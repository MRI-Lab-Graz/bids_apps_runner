"""Tests for scripts/login_node_hygiene.py -- the login-node process reaper.

Context: on 2026-09-22 the HPC admin flagged this account for login-node
usage. The residue found was a 62-day-old GUI daemon, 48-day-old orphaned
`tail -f`/`grep` watchers from dead Claude Code sessions, and VS Code
server stacks 41 and 91 days old. None of it was caught by the existing
container-execution guard in prism_local.py, which asks "is this a login
node?" but never "has this process outlived its purpose?".

Everything here drives /proc through monkeypatched seams so the tests are
hermetic -- they must never depend on what happens to be running on the
machine executing them.
"""

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import login_node_hygiene as hygiene


MY_UID = 4242
OTHER_UID = 9999
NOW = 1_000_000.0
HOUR = 3600.0


def _install_fake_proc(monkeypatch, *, cmdlines, ppids=None, starts=None, uids=None):
    """Point the module's /proc accessors at an in-memory process table."""
    ppids = ppids or {}
    starts = starts or {}
    uids = uids or {}

    monkeypatch.setattr(hygiene, "iter_proc_pids", lambda: sorted(cmdlines))
    monkeypatch.setattr(hygiene, "read_proc_cmdline", lambda pid: cmdlines.get(pid, ""))
    monkeypatch.setattr(hygiene, "process_ppid", lambda pid: ppids.get(pid))
    monkeypatch.setattr(hygiene, "process_start_epoch", lambda pid: starts.get(pid))
    monkeypatch.setattr(hygiene, "process_rss_bytes", lambda pid: 1024)
    monkeypatch.setattr(
        hygiene, "process_owner_uid", lambda pid: uids.get(pid, MY_UID)
    )
    monkeypatch.setattr(hygiene.os, "getuid", lambda: MY_UID)
    monkeypatch.setattr(hygiene.time, "time", lambda: NOW)


# ── find_orphaned_watchers() ────────────────────────────────────────────────

CLAUDE_TAIL = (
    "tail -f -n0 /tmp/claude-2002/-usr-people-mrilabgraz-github-bids-apps-runner/"
    "89688979-8977-4b62-909b-e61ac29b844e/scratchpad/full_run_batch1.log"
)


def test_orphaned_claude_watcher_older_than_threshold_is_reapable(monkeypatch):
    # The exact shape of the real 48-day-old leftovers: reparented to init
    # because the Claude session that spawned them is long gone.
    _install_fake_proc(
        monkeypatch,
        cmdlines={101: CLAUDE_TAIL},
        ppids={101: 1},
        starts={101: NOW - 48 * 24 * HOUR},
    )

    found = hygiene.find_orphaned_watchers()

    assert [w["pid"] for w in found] == [101]


def test_young_orphaned_watcher_is_left_alone(monkeypatch):
    # A watcher orphaned minutes ago may belong to a session still starting
    # up; the age floor keeps the reaper off anything recent.
    _install_fake_proc(
        monkeypatch,
        cmdlines={101: CLAUDE_TAIL},
        ppids={101: 1},
        starts={101: NOW - 0.5 * HOUR},
    )

    assert hygiene.find_orphaned_watchers() == []


def test_watcher_with_a_live_parent_is_left_alone(monkeypatch):
    # PPID != 1 means the owning session is still alive -- this is an
    # active Claude Code session's log tail, not residue.
    _install_fake_proc(
        monkeypatch,
        cmdlines={101: CLAUDE_TAIL},
        ppids={101: 5000},
        starts={101: NOW - 48 * 24 * HOUR},
    )

    assert hygiene.find_orphaned_watchers() == []


def test_unrelated_orphaned_process_is_not_reaped(monkeypatch):
    # Being old and orphaned is not on its own a reason to die. Only the
    # known-disposable watcher shape qualifies.
    _install_fake_proc(
        monkeypatch,
        cmdlines={101: "/usr/bin/gpg-agent --daemon"},
        ppids={101: 1},
        starts={101: NOW - 90 * 24 * HOUR},
    )

    assert hygiene.find_orphaned_watchers() == []


def test_another_users_watcher_is_never_reaped(monkeypatch):
    # This node has ~74 users on it. Touching someone else's process would
    # be far worse than the problem being solved.
    _install_fake_proc(
        monkeypatch,
        cmdlines={101: CLAUDE_TAIL},
        ppids={101: 1},
        starts={101: NOW - 48 * 24 * HOUR},
        uids={101: OTHER_UID},
    )

    assert hygiene.find_orphaned_watchers() == []


# ── is_protected() ──────────────────────────────────────────────────────────


def test_process_with_living_children_is_protected(monkeypatch):
    # PID 2089861 in the real incident looked stale by age but was the
    # parent of the live session -- killing it would have cut the user off.
    _install_fake_proc(
        monkeypatch,
        cmdlines={200: "node agent host", 201: "node child"},
        ppids={200: 1, 201: 200},
        starts={200: NOW - 90 * 24 * HOUR, 201: NOW - 60},
    )
    monkeypatch.setattr(hygiene, "process_slurm_job_id", lambda pid: None)
    monkeypatch.setattr(hygiene, "own_pid_lineage", lambda: {1, 999})

    assert hygiene.is_protected(200) is True


def test_process_inside_a_slurm_allocation_is_protected(monkeypatch):
    # A process holding SLURM_JOB_ID is real allocated work, not residue.
    _install_fake_proc(
        monkeypatch,
        cmdlines={300: "python heavy_thing.py"},
        ppids={300: 1},
        starts={300: NOW - 90 * 24 * HOUR},
    )
    monkeypatch.setattr(hygiene, "process_slurm_job_id", lambda pid: "5697232")
    monkeypatch.setattr(hygiene, "own_pid_lineage", lambda: {1, 999})

    assert hygiene.is_protected(300) is True


def test_reapers_own_lineage_is_protected(monkeypatch):
    # The reaper must never kill itself or the shell/cron job running it.
    _install_fake_proc(
        monkeypatch,
        cmdlines={400: "python login_node_hygiene.py --reap"},
        ppids={400: 1},
        starts={400: NOW - 90 * 24 * HOUR},
    )
    monkeypatch.setattr(hygiene, "process_slurm_job_id", lambda pid: None)
    monkeypatch.setattr(hygiene, "own_pid_lineage", lambda: {400, 1})

    assert hygiene.is_protected(400) is True


def test_plain_orphaned_process_is_not_protected(monkeypatch):
    _install_fake_proc(
        monkeypatch,
        cmdlines={500: CLAUDE_TAIL},
        ppids={500: 1},
        starts={500: NOW - 90 * 24 * HOUR},
    )
    monkeypatch.setattr(hygiene, "process_slurm_job_id", lambda pid: None)
    monkeypatch.setattr(hygiene, "own_pid_lineage", lambda: {1, 999})

    assert hygiene.is_protected(500) is False


# ── reap() ──────────────────────────────────────────────────────────────────


def test_reap_never_terminates_a_protected_process(monkeypatch):
    """The single most important guarantee in this module."""
    _install_fake_proc(
        monkeypatch,
        cmdlines={600: CLAUDE_TAIL},
        ppids={600: 1},
        starts={600: NOW - 48 * 24 * HOUR},
    )
    monkeypatch.setattr(hygiene, "is_protected", lambda pid: True)

    killed = []
    monkeypatch.setattr(hygiene, "terminate_pid_groups", lambda pids: killed.extend(pids))

    reaped = hygiene.reap(dry_run=False)

    assert killed == []
    assert reaped == []


def test_reap_dry_run_reports_without_killing(monkeypatch):
    _install_fake_proc(
        monkeypatch,
        cmdlines={700: CLAUDE_TAIL},
        ppids={700: 1},
        starts={700: NOW - 48 * 24 * HOUR},
    )
    monkeypatch.setattr(hygiene, "is_protected", lambda pid: False)

    killed = []
    monkeypatch.setattr(hygiene, "terminate_pid_groups", lambda pids: killed.extend(pids))

    reaped = hygiene.reap(dry_run=True)

    assert killed == []
    assert [item["pid"] for item in reaped] == [700]


def test_reap_terminates_unprotected_residue(monkeypatch):
    _install_fake_proc(
        monkeypatch,
        cmdlines={800: CLAUDE_TAIL},
        ppids={800: 1},
        starts={800: NOW - 48 * 24 * HOUR},
    )
    monkeypatch.setattr(hygiene, "is_protected", lambda pid: False)

    killed = []
    monkeypatch.setattr(hygiene, "terminate_pid_groups", lambda pids: killed.extend(pids))

    reaped = hygiene.reap(dry_run=False)

    assert killed == [800]
    assert [item["pid"] for item in reaped] == [800]


# ── should_exit_idle() -- the GUI's self-imposed lifetime cap ───────────────
#
# The 62-day GUI daemon started 2026-07-22; the login-node guard landed in
# c5edee6 on 2026-07-29. A running process keeps the code it loaded at
# startup, so that daemon never had the guard at all. Capping the GUI's
# lifetime is what stops a long-lived process from outliving the safety
# code meant to constrain it.


def test_gui_does_not_exit_when_not_on_a_login_node():
    # A collaborator's Mac, or a real SLURM allocation. The cap must not
    # apply there or it would kill sessions people are relying on.
    assert (
        hygiene.should_exit_idle(
            idle_seconds=99 * HOUR,
            jobs_active=False,
            on_login_node=False,
        )
        is False
    )


def test_gui_does_not_exit_while_a_background_job_is_in_flight():
    # datalad clone / container pull can legitimately run long; killing
    # the GUI mid-download would corrupt real work.
    assert (
        hygiene.should_exit_idle(
            idle_seconds=99 * HOUR,
            jobs_active=True,
            on_login_node=True,
        )
        is False
    )


def test_gui_does_not_exit_before_the_idle_threshold():
    assert (
        hygiene.should_exit_idle(
            idle_seconds=5 * 60,
            jobs_active=False,
            on_login_node=True,
        )
        is False
    )


def test_gui_exits_when_idle_on_a_login_node_with_no_jobs():
    assert (
        hygiene.should_exit_idle(
            idle_seconds=31 * 60,
            jobs_active=False,
            on_login_node=True,
        )
        is True
    )


# ── any_jobs_active() -- the veto that protects in-flight work ──────────────


def test_no_jobs_active_when_all_registries_empty():
    assert hygiene.any_jobs_active([{}, {}]) is False


def test_no_jobs_active_when_every_job_has_finished():
    registries = [
        {"a": {"status": "completed"}},
        {"b": {"status": "failed"}, "c": {"status": "cancelled"}},
    ]
    assert hygiene.any_jobs_active(registries) is False


def test_running_clone_keeps_the_gui_alive():
    # A `datalad clone` of a large dataset is exactly the case that must
    # never be interrupted by the idle timer.
    registries = [{"a": {"status": "completed"}}, {"b": {"status": "running"}}]
    assert hygiene.any_jobs_active(registries) is True


def test_queued_job_keeps_the_gui_alive():
    assert hygiene.any_jobs_active([{"a": {"status": "queued"}}]) is True


def test_job_without_a_status_is_treated_as_active():
    # Fail safe: an unrecognised job shape must not license a shutdown.
    assert hygiene.any_jobs_active([{"a": {}}]) is True


# ── the real /proc accessors ────────────────────────────────────────────────
#
# Everything above drives monkeypatched seams, which proves the decision
# logic but never the parsing underneath it. These run against this actual
# test process: if the /proc/<pid>/stat field offsets or the boot-time
# arithmetic are wrong, only these catch it.


def test_iter_proc_pids_includes_this_process():
    assert os.getpid() in set(hygiene.iter_proc_pids())


def test_read_proc_cmdline_returns_this_processes_command():
    cmdline = hygiene.read_proc_cmdline(os.getpid())
    assert cmdline
    assert cmdline == cmdline.lower()


def test_read_proc_cmdline_of_a_dead_pid_is_empty():
    assert hygiene.read_proc_cmdline(0) == ""


def test_process_owner_uid_matches_this_user():
    assert hygiene.process_owner_uid(os.getpid()) == os.getuid()


def test_process_owner_uid_of_a_dead_pid_is_none():
    assert hygiene.process_owner_uid(0) is None


def test_process_ppid_matches_the_real_parent():
    assert hygiene.process_ppid(os.getpid()) == os.getppid()


def test_process_ppid_of_a_dead_pid_is_none():
    assert hygiene.process_ppid(0) is None


def test_boot_time_is_in_the_past_and_plausible():
    boot = hygiene.boot_time()
    assert boot is not None
    assert 0 < boot < time.time()


def test_process_start_epoch_matches_this_process():
    # Catches a wrong field index or a bad SC_CLK_TCK conversion, which
    # would silently make every process look absurdly old or young --
    # and this module kills things based on age.
    started = hygiene.process_start_epoch(os.getpid())
    assert started is not None
    assert started <= time.time()
    assert time.time() - started < 3600


def test_process_start_epoch_of_a_dead_pid_is_none():
    assert hygiene.process_start_epoch(0) is None


def test_process_rss_bytes_is_positive_for_a_live_process():
    assert hygiene.process_rss_bytes(os.getpid()) > 0


def test_process_rss_bytes_of_a_dead_pid_is_zero():
    assert hygiene.process_rss_bytes(0) == 0


def test_own_pid_lineage_contains_self_and_parent():
    lineage = hygiene.own_pid_lineage()
    assert os.getpid() in lineage
    assert os.getppid() in lineage


def test_process_slurm_job_id_reflects_this_environment():
    # pytest normally runs outside an allocation; inside one (salloc/srun)
    # the value must match what SLURM actually set.
    expected = os.environ.get("SLURM_JOB_ID") or os.environ.get("SLURM_JOBID")
    assert hygiene.process_slurm_job_id(os.getpid()) == (expected or None)


def test_has_living_children_is_false_for_a_dead_pid():
    assert hygiene.has_living_children(0) is False


def test_terminate_pid_groups_on_nothing_kills_nothing():
    assert hygiene.terminate_pid_groups([]) == 0


def test_terminate_pid_groups_skips_a_dead_pid():
    # PID 0 can't be signalled; it must be skipped, not raised on.
    assert hygiene.terminate_pid_groups([0]) == 0


def test_this_live_session_is_protected():
    """Guards the guard: the process running these tests must be immune."""
    assert hygiene.is_protected(os.getpid()) is True


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_main_reports_when_nothing_is_stale(monkeypatch, capsys):
    monkeypatch.setattr(hygiene, "reap", lambda dry_run=True: [])

    assert hygiene.main([]) == 0
    assert "nothing stale" in capsys.readouterr().out


def test_main_default_is_report_only(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(
        hygiene,
        "reap",
        lambda dry_run=True: seen.update(dry_run=dry_run)
        or [{"pid": 7, "kind": "orphaned-watcher", "age_seconds": 86400.0, "cmdline": "tail -f x"}],
    )

    assert hygiene.main([]) == 0
    assert seen["dry_run"] is True
    assert "Would terminate" in capsys.readouterr().out


def test_main_with_reap_flag_actually_reaps(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(
        hygiene,
        "reap",
        lambda dry_run=True: seen.update(dry_run=dry_run)
        or [{"pid": 7, "kind": "orphaned-watcher", "age_seconds": 86400.0, "cmdline": "tail -f x"}],
    )

    assert hygiene.main(["--reap"]) == 0
    assert seen["dry_run"] is False
    assert "Terminated" in capsys.readouterr().out


def test_report_line_handles_unknown_age():
    line = hygiene._format(
        {"pid": 1, "kind": "orphaned-watcher", "age_seconds": None, "cmdline": "x"}
    )
    assert "unknown age" in line


# ── collect_residue() spans both detectors ──────────────────────────────────


def test_collect_residue_includes_abandoned_vscode_stacks(monkeypatch):
    monkeypatch.setattr(hygiene, "find_orphaned_watchers", lambda: [])
    monkeypatch.setattr(
        hygiene,
        "find_stale_vscode_stacks",
        lambda: [{"hash": "abc123", "pids": [11, 12], "age_seconds": 91 * 24 * HOUR}],
    )

    residue = hygiene.collect_residue()

    assert [item["pid"] for item in residue] == [11, 12]
    assert all(item["kind"] == "stale-vscode-stack" for item in residue)


def test_find_stale_vscode_stacks_skips_a_stack_with_a_live_child(monkeypatch):
    # The real 20-day-old stack that was still attached; killing it would
    # have disconnected the user mid-session.
    monkeypatch.setattr(
        hygiene,
        "find_vscode_remote_ssh_stacks",
        lambda: [
            {"hash": "live", "pids": [1], "age_seconds": 20 * 24 * HOUR, "stale": True, "has_children": True},
            {"hash": "dead", "pids": [2], "age_seconds": 91 * 24 * HOUR, "stale": True, "has_children": False},
        ],
    )

    assert [s["hash"] for s in hygiene.find_stale_vscode_stacks()] == ["dead"]
