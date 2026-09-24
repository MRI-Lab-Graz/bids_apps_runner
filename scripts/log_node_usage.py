#!/usr/bin/env python3
"""Record this account's login-node usage over time, with rank.

Why
---
The HPC admin warned this account twice (2026-07 and 2026-09-22) about
login-node usage. When the second warning was investigated, a live
snapshot put this user 35th of 59 by CPU time -- but there was no way to
show what usage had been over the preceding weeks. `ps` only sees
processes that are alive at this instant; anything that already exited
leaves no trace. This node has no sysstat/sar, no process accounting, and
its wtmp does not record the `sshd@notty` sessions VS Code Remote opens.

So this samples the node on a timer and appends the result, building the
history that did not exist. Rank is recorded alongside the raw numbers
deliberately: "2.5 CPU-minutes" is not defensible on its own, "35th of
59, while the busiest user burned 2853" is.

Reads /proc directly rather than shelling out to `ps` -- one pass, no
subprocess, negligible cost. Adding measurable load to the node this is
meant to prove innocence on would be self-defeating.

Usage:
    log_node_usage.py                 # record one sample
    log_node_usage.py --report        # summarise the samples so far
"""

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from login_node_hygiene import (  # noqa: E402  (path set above)
    iter_proc_pids,
    process_owner_uid,
    process_rss_bytes,
    process_start_epoch,
    _stat_fields,
)

DEFAULT_LOG = Path(__file__).resolve().parents[1] / "logs" / "node_usage.jsonl"


def process_cpu_seconds(pid):
    """Total CPU (user + system) a process has consumed, in seconds."""
    try:
        fields = _stat_fields(pid)
        # After comm, 0-indexed: utime=11, stime=12.
        ticks = float(fields[11]) + float(fields[12])
    except Exception:
        return 0.0
    try:
        clk_tck = os.sysconf("SC_CLK_TCK")
    except (AttributeError, ValueError):
        clk_tck = 100
    return ticks / clk_tck


def collect_usage_by_uid():
    """Aggregate every live process on this host, grouped by owning UID."""
    now = time.time()
    by_uid = {}
    for pid in iter_proc_pids():
        uid = process_owner_uid(pid)
        if uid is None:
            continue
        entry = by_uid.setdefault(
            uid,
            {"cpu_seconds": 0.0, "rss_bytes": 0, "procs": 0, "oldest_age_seconds": 0},
        )
        entry["cpu_seconds"] += process_cpu_seconds(pid)
        entry["rss_bytes"] += process_rss_bytes(pid)
        entry["procs"] += 1
        started = process_start_epoch(pid)
        if started is not None:
            age = now - started
            if age > entry["oldest_age_seconds"]:
                entry["oldest_age_seconds"] = age
    return by_uid


def snapshot():
    """One sample: this user's usage plus where it sits among all users."""
    by_uid = collect_usage_by_uid()
    my_uid = os.getuid()
    mine = by_uid.get(my_uid)

    ordered = sorted(
        by_uid.items(), key=lambda kv: kv[1]["cpu_seconds"], reverse=True
    )
    rank = None
    for position, (uid, _) in enumerate(ordered, start=1):
        if uid == my_uid:
            rank = position
            break

    try:
        load1 = os.getloadavg()[0]
    except OSError:
        load1 = None

    return {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "host": platform.node(),
        "cpu_seconds": round(mine["cpu_seconds"], 2) if mine else 0.0,
        "rss_bytes": mine["rss_bytes"] if mine else 0,
        "procs": mine["procs"] if mine else 0,
        "oldest_age_seconds": round(mine["oldest_age_seconds"]) if mine else 0,
        "rank_by_cpu": rank,
        "total_users": len(by_uid),
        "busiest_user_cpu_seconds": round(ordered[0][1]["cpu_seconds"], 2)
        if ordered
        else 0.0,
        "load1": load1,
    }


def append_snapshot(path, snap):
    """Append one JSON line. Creates the directory if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(snap) + "\n")


def _load_samples(path):
    """Read samples, skipping anything unparseable.

    A sample truncated by a killed write must not make weeks of history
    unreadable -- this log exists to be read long after it was written.
    """
    path = Path(path)
    if not path.exists():
        return []
    samples = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            samples.append(json.loads(line))
        except ValueError:
            continue
    return samples


def _median(values):
    if not values:
        return 0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def summarize(path):
    """Human-readable summary, suitable for pasting to an admin."""
    samples = _load_samples(path)
    if not samples:
        return f"No samples recorded yet in {path}."

    cpu = [s.get("cpu_seconds", 0) or 0 for s in samples]
    rss = [s.get("rss_bytes", 0) or 0 for s in samples]
    procs = [s.get("procs", 0) or 0 for s in samples]
    ranks = [s["rank_by_cpu"] for s in samples if s.get("rank_by_cpu")]
    totals = [s.get("total_users", 0) or 0 for s in samples]

    first = samples[0].get("iso", "?")
    last = samples[-1].get("iso", "?")

    lines = [
        f"Login-node usage for {os.environ.get('USER', 'this account')} "
        f"on {samples[-1].get('host', platform.node())}",
        f"{len(samples)} samples, {first} .. {last}",
        "",
        f"  CPU minutes   peak {max(cpu)/60:8.1f}   median {_median(cpu)/60:8.1f}",
        f"  Memory MB     peak {max(rss)/1048576:8.0f}   median {_median(rss)/1048576:8.0f}",
        f"  Processes     peak {max(procs):8d}   median {_median(procs):8.0f}",
    ]
    if ranks:
        # Ranks are assigned over a reverse=True (descending CPU) sort, so
        # rank 1 is the BUSIEST user. min(ranks) is therefore this account's
        # heaviest moment, not its lightest -- labelling it "best" inverted the
        # meaning of the one number this report exists to make defensible.
        lines.append(
            f"  Rank by CPU   busiest {min(ranks):5d}   median {_median(ranks):5.0f}"
            f"   (of ~{max(totals)} users; rank 1 = heaviest user on the node)"
        )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Record or summarise this account's login-node usage."
    )
    parser.add_argument("--out", default=str(DEFAULT_LOG), help="JSONL log path")
    parser.add_argument(
        "--report",
        action="store_true",
        help="print a summary of samples so far instead of recording one",
    )
    args = parser.parse_args(argv)

    if args.report:
        print(summarize(args.out))
        return 0

    append_snapshot(args.out, snapshot())
    return 0


if __name__ == "__main__":
    sys.exit(main())
