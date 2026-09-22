#!/usr/bin/env python3
"""Login-node hygiene -- find and reap this user's own stale processes.

Why this exists
---------------
`prism_local.execute_local()` already refuses to run containers or move
data on a bare SLURM login node (see CLAUDE.md). That guard answers "is
this host a login node?". It never answers "has this process outlived
its purpose?", and on 2026-09-22 the second question was the one that
got this account flagged by the HPC admin. What had accumulated on
IT010128 was residue, not compute:

  * a `prism_app_runner.py` GUI daemon running for 62 days,
  * `tail -f`/`grep --line-buffered` watchers from Claude Code sessions
    that had exited 48 days earlier, reparented to init,
  * VS Code Remote-SSH server stacks 41 and 91 days old.

Worse, that GUI had started on 2026-07-22 and the login-node guard only
landed on 2026-07-29 (c5edee6). A running process keeps the code it
loaded at startup, so the daemon enforcing the policy predated the
policy -- for 62 days its "Run" button would have launched containers
straight onto the login node. A long-lived process can outlive the
safety code meant to constrain it, which is why capping process
lifetime is a safety control here and not just tidiness.

Safety posture
--------------
This module kills things, so it is deliberately timid:

  * every candidate is filtered to this OS user's own UID first -- the
    login node carries ~74 users and touching another's process would be
    far worse than the residue being cleaned up;
  * `is_protected()` vetoes anything with living children, anything
    holding a SLURM_JOB_ID, and the reaper's own lineage;
  * only known-disposable process shapes are eligible at all. Being old
    and orphaned is not by itself a reason to die.

Usage:
    login_node_hygiene.py              # report only (default, safe)
    login_node_hygiene.py --reap       # actually terminate
"""

import argparse
import os
import re
import signal
import sys
import time
from pathlib import Path

# ── tunables ────────────────────────────────────────────────────────────────

# Orphaned log watchers younger than this are left alone: a session that
# is still starting up can briefly look parentless.
ORPHAN_WATCHER_MIN_AGE_SECONDS = 24 * 60 * 60

# Matches the VS Code Remote-SSH server stack layout; the captured group
# is the per-server "Stable-<hash>" directory, roughly one per connected
# window.
VSCODE_SERVER_STACK_RE = re.compile(r"\.vscode-server/cli/servers/stable-([a-f0-9]+)/")
VSCODE_STALE_THRESHOLD_SECONDS = 12 * 60 * 60

# Claude Code parks per-session scratch under /tmp/claude-<uid>/...; a
# `tail -f` on such a path whose parent is gone can never be read again.
CLAUDE_SCRATCH_RE = re.compile(r"/tmp/claude-\d+/")
WATCHER_PREFIXES = ("tail -f", "tail -n", "tail ")

# How long the GUI may sit idle on a login node before shutting itself
# down. Jobs are dispatched to SLURM at submission time and notified via
# notify_ntfy.sh from inside the generated sbatch scripts, so nothing in
# the GUI process needs to survive a submission.
GUI_IDLE_EXIT_SECONDS = 30 * 60


# ── /proc accessors (the seams tests monkeypatch) ───────────────────────────


def iter_proc_pids():
    """Yield numeric process IDs from /proc."""
    try:
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit():
                yield int(entry.name)
    except Exception:
        return


def read_proc_cmdline(pid):
    """Process command line as one lowercased string, or "" if unreadable."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        if not raw:
            return ""
        return (
            raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip().lower()
        )
    except Exception:
        return ""


def process_owner_uid(pid):
    """Owning UID of a process, or None if it vanished."""
    try:
        return os.stat(f"/proc/{pid}").st_uid
    except OSError:
        return None


def _stat_fields(pid):
    """Numeric fields of /proc/<pid>/stat after the comm field.

    comm (field 2) is parenthesized and may itself contain ")", so split on
    the LAST ")" to find where the numeric fields reliably begin.
    """
    raw = Path(f"/proc/{pid}/stat").read_text(errors="ignore")
    return raw.rsplit(")", 1)[1].split()


def process_ppid(pid):
    """Parent PID, or None if unreadable. PPID 1 means orphaned."""
    try:
        # After comm, 0-indexed: state=0, ppid=1.
        return int(_stat_fields(pid)[1])
    except Exception:
        return None


_boot_time_cache = None


def boot_time():
    """System boot time as a Unix epoch timestamp, or None if unreadable."""
    global _boot_time_cache
    if _boot_time_cache is not None:
        return _boot_time_cache
    try:
        with open("/proc/stat", "r") as f:
            for line in f:
                if line.startswith("btime"):
                    _boot_time_cache = float(line.split()[1])
                    return _boot_time_cache
    except Exception:
        pass
    return None


def process_start_epoch(pid):
    """Wall-clock epoch a process started, or None if unavailable."""
    boot = boot_time()
    if boot is None:
        return None
    try:
        # starttime is overall field 22, i.e. index 19 after comm.
        starttime_ticks = float(_stat_fields(pid)[19])
    except Exception:
        return None
    try:
        clk_tck = os.sysconf("SC_CLK_TCK")
    except (AttributeError, ValueError):
        clk_tck = 100
    return boot + starttime_ticks / clk_tck


def process_rss_bytes(pid):
    """Resident memory in bytes, or 0 if unavailable."""
    try:
        with open(f"/proc/{pid}/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        pass
    return 0


def process_slurm_job_id(pid):
    """SLURM_JOB_ID from a process's environment, or None.

    Readable only for this user's own processes, which is exactly the set
    being considered for reaping.
    """
    for key in ("SLURM_JOB_ID", "SLURM_JOBID"):
        try:
            raw = Path(f"/proc/{pid}/environ").read_bytes()
        except Exception:
            return None
        for item in raw.split(b"\x00"):
            name, _, value = item.partition(b"=")
            if name.decode("utf-8", "ignore") == key and value:
                return value.decode("utf-8", "ignore")
    return None


def own_pid_lineage():
    """PIDs of this process and every ancestor, so the reaper can't
    terminate the cron job or shell that invoked it."""
    lineage = set()
    pid = os.getpid()
    while pid and pid not in lineage:
        lineage.add(pid)
        parent = process_ppid(pid)
        if parent is None or parent == pid:
            break
        pid = parent
    return lineage


# ── termination ─────────────────────────────────────────────────────────────


def _terminate_pid_group(pid):
    # PID 0 and negatives are not "some process that no longer exists":
    # os.getpgid(0)/os.kill(0, ...) mean "the CALLING process's own group",
    # and a negative pid is itself a process-group selector. Passing either
    # through would make this reaper SIGTERM itself and everything sharing
    # its process group -- including, from cron, the shell running it.
    if pid is None or pid <= 0:
        return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
        return True
    except OSError:
        pass
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def terminate_pid_groups(pids):
    """SIGTERM each distinct process group among the given PIDs.

    Group-wise so a `tail -f ... | grep --line-buffered ...` pipeline dies
    whole -- the grep half carries no identifying path of its own.
    """
    terminated = 0
    signaled = set()
    for pid in sorted(set(pids)):
        if pid is None or pid <= 0:
            continue  # see _terminate_pid_group: 0/negative are group selectors
        try:
            pgid = os.getpgid(pid)
        except OSError:
            continue
        if pgid in signaled:
            continue
        if _terminate_pid_group(pid):
            signaled.add(pgid)
            terminated += 1
    return terminated


# ── detectors ───────────────────────────────────────────────────────────────


def _own_pids_with_cmdlines():
    """(pid, cmdline) for every live process owned by this user."""
    my_uid = os.getuid()
    for pid in iter_proc_pids():
        cmdline = read_proc_cmdline(pid)
        if not cmdline:
            continue
        if process_owner_uid(pid) != my_uid:
            continue
        yield pid, cmdline


def has_living_children(pid):
    """True if any live process reports this PID as its parent.

    PID 0 is not a process: kernel threads report a parent of 0, so asking
    this about 0 would always answer True and make the emptiness of the
    check meaningless. Non-positive PIDs are rejected outright.
    """
    if pid is None or pid <= 0:
        return False
    for child in iter_proc_pids():
        if child != pid and process_ppid(child) == pid:
            return True
    return False


def _age_of(pid, now):
    started = process_start_epoch(pid)
    return None if started is None else now - started


def find_orphaned_watchers():
    """Log watchers left behind by Claude Code sessions that have exited.

    Eligible only when all three hold: the command is a `tail` on a
    /tmp/claude-<uid>/ scratch path, the parent is gone (PPID 1), and it
    is older than ORPHAN_WATCHER_MIN_AGE_SECONDS. A live session's tail
    still has its parent, so this cannot touch one.
    """
    now = time.time()
    found = []
    for pid, cmdline in _own_pids_with_cmdlines():
        if not cmdline.startswith(WATCHER_PREFIXES):
            continue
        if not CLAUDE_SCRATCH_RE.search(cmdline):
            continue
        if process_ppid(pid) != 1:
            continue
        age = _age_of(pid, now)
        if age is None or age < ORPHAN_WATCHER_MIN_AGE_SECONDS:
            continue
        found.append(
            {
                "pid": pid,
                "kind": "orphaned-watcher",
                "age_seconds": age,
                "cmdline": cmdline,
            }
        )
    return sorted(found, key=lambda w: w["pid"])


def find_vscode_remote_ssh_stacks():
    """Group this user's VS Code Remote-SSH server processes by version
    stack (roughly one per connected window).

    Returns dicts, oldest first:
        {"hash", "pids", "started_at", "age_seconds", "rss_bytes",
         "stale", "has_children"}
    """
    stacks = {}
    for pid, cmdline in _own_pids_with_cmdlines():
        if ".vscode-server/cli/servers/stable-" not in cmdline:
            continue
        match = VSCODE_SERVER_STACK_RE.search(cmdline)
        if not match:
            continue
        stacks.setdefault(match.group(1), []).append(pid)

    now = time.time()
    results = []
    for stack_hash, pids in stacks.items():
        starts = [t for t in (process_start_epoch(p) for p in pids) if t is not None]
        started_at = min(starts) if starts else None
        age_seconds = (now - started_at) if started_at is not None else None
        results.append(
            {
                "hash": stack_hash,
                "pids": sorted(pids),
                "started_at": started_at,
                "age_seconds": age_seconds,
                "rss_bytes": sum(process_rss_bytes(p) for p in pids),
                "stale": bool(
                    age_seconds is not None
                    and age_seconds >= VSCODE_STALE_THRESHOLD_SECONDS
                ),
                "has_children": any(has_living_children(p) for p in pids),
            }
        )
    results.sort(key=lambda s: (s["age_seconds"] is None, -(s["age_seconds"] or 0)))
    return results


def find_stale_vscode_stacks():
    """Stale VS Code stacks that are safe to reap.

    A stack with living children is an attached session someone is using;
    in the real incident one 20-day-old stack was the live one and two
    older ones were genuinely abandoned. Age alone would have cut the
    user off.
    """
    return [
        stack
        for stack in find_vscode_remote_ssh_stacks()
        if stack["stale"] and not stack["has_children"]
    ]


# ── safety gate ─────────────────────────────────────────────────────────────


def is_protected(pid):
    """True when a PID must never be terminated, whatever its age."""
    if pid in own_pid_lineage():
        return True
    if process_slurm_job_id(pid):
        return True
    if has_living_children(pid):
        return True
    return False


# ── the GUI's self-imposed lifetime cap ─────────────────────────────────────


def on_bare_slurm_login_node():
    """True on a SLURM login/edge node with no allocation of its own.

    Delegates to prism_local's predicate rather than re-deriving it: that
    one is the guard CLAUDE.md's login-node policy is written around, and
    two copies of a safety check are exactly how they drift apart. Imported
    lazily so this module stays usable from cron without dragging in the
    runner's dependencies.
    """
    from prism_local import _on_bare_slurm_login_node

    return _on_bare_slurm_login_node()


# Statuses that mean a background job has stopped for good. Anything
# else -- including a job dict with no status at all -- counts as still
# running, so an unrecognised job shape can never license a shutdown.
FINISHED_JOB_STATUSES = frozenset(
    {"completed", "complete", "failed", "error", "cancelled", "canceled", "stopped", "done"}
)


def any_jobs_active(job_registries):
    """True if any job across the given registries is still in flight.

    Takes the registry dicts rather than importing them so this stays
    free of Flask and testable on its own.
    """
    for registry in job_registries:
        for job in (registry or {}).values():
            status = (job or {}).get("status")
            if status is None or str(status).lower() not in FINISHED_JOB_STATUSES:
                return True
    return False


def should_exit_idle(
    idle_seconds, jobs_active, on_login_node, threshold=GUI_IDLE_EXIT_SECONDS
):
    """Whether the GUI should shut itself down now.

    All three must hold. `jobs_active` covers in-flight `datalad clone`
    and container pulls -- long-running background work the GUI owns and
    must not abandon midway.
    """
    if not on_login_node:
        return False
    if jobs_active:
        return False
    return idle_seconds >= threshold


# ── reaping ─────────────────────────────────────────────────────────────────


def collect_residue():
    """Everything eligible for reaping, before the protection gate."""
    residue = list(find_orphaned_watchers())
    for stack in find_stale_vscode_stacks():
        for pid in stack["pids"]:
            residue.append(
                {
                    "pid": pid,
                    "kind": "stale-vscode-stack",
                    "age_seconds": stack["age_seconds"],
                    "cmdline": f"vscode server stack {stack['hash']}",
                }
            )
    return residue


def reap(dry_run=True):
    """Terminate stale residue owned by this user. Returns what was (or
    would be) reaped; protected PIDs are silently excluded."""
    targets = [item for item in collect_residue() if not is_protected(item["pid"])]
    if targets and not dry_run:
        terminate_pid_groups([item["pid"] for item in targets])
    return targets


def _format(item):
    age = item.get("age_seconds")
    age_text = "unknown age" if age is None else f"{age / 86400:.1f}d old"
    return f"  pid {item['pid']:>8}  {item['kind']:<20} {age_text:>14}  {item['cmdline'][:70]}"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Report or reap this user's stale login-node processes."
    )
    parser.add_argument(
        "--reap",
        action="store_true",
        help="actually terminate stale processes (default: report only)",
    )
    args = parser.parse_args(argv)

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    targets = reap(dry_run=not args.reap)

    if not targets:
        print(f"[{stamp}] login-node hygiene: nothing stale found.")
        return 0

    verb = "Terminated" if args.reap else "Would terminate"
    print(f"[{stamp}] login-node hygiene: {verb} {len(targets)} process(es):")
    for item in targets:
        print(_format(item))
    return 0


if __name__ == "__main__":
    sys.exit(main())
