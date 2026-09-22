"""Tests for scripts/log_node_usage.py -- the login-node usage recorder.

Context (2026-09-22): the HPC admin warned this account twice about login
node usage. A live snapshot put this user 35th of 59 by CPU time, but
`ps` only sees processes that are alive right now -- this node has no
sar, no process accounting, and a wtmp that does not record the
`sshd@notty` sessions VS Code Remote uses. There was no way to show what
usage had actually been over time.

This module records that evidence continuously so the next conversation
with the admin can be had with numbers. Rank matters as much as the raw
figures: "2.5 CPU-minutes" means nothing on its own, "35th of 59" does.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import log_node_usage as usage


MY_UID = 4242
NOW = 1_000_000.0


def _install_fake_proc(monkeypatch, table):
    """table: {pid: {"uid", "cpu", "rss", "start"}}"""
    monkeypatch.setattr(usage, "iter_proc_pids", lambda: sorted(table))
    monkeypatch.setattr(usage, "process_owner_uid", lambda p: table[p]["uid"])
    monkeypatch.setattr(usage, "process_cpu_seconds", lambda p: table[p]["cpu"])
    monkeypatch.setattr(usage, "process_rss_bytes", lambda p: table[p]["rss"])
    monkeypatch.setattr(usage, "process_start_epoch", lambda p: table[p]["start"])
    monkeypatch.setattr(usage.time, "time", lambda: NOW)


# ── aggregation ─────────────────────────────────────────────────────────────


def test_usage_is_grouped_per_uid(monkeypatch):
    _install_fake_proc(
        monkeypatch,
        {
            1: {"uid": MY_UID, "cpu": 60.0, "rss": 1024, "start": NOW - 100},
            2: {"uid": MY_UID, "cpu": 30.0, "rss": 2048, "start": NOW - 500},
            3: {"uid": 7777, "cpu": 600.0, "rss": 4096, "start": NOW - 50},
        },
    )

    by_uid = usage.collect_usage_by_uid()

    assert by_uid[MY_UID]["cpu_seconds"] == 90.0
    assert by_uid[MY_UID]["rss_bytes"] == 3072
    assert by_uid[MY_UID]["procs"] == 2
    assert by_uid[MY_UID]["oldest_age_seconds"] == 500
    assert by_uid[7777]["cpu_seconds"] == 600.0


def test_snapshot_ranks_this_user_against_the_others(monkeypatch):
    # The whole point: a bare number is not defensible, a rank is.
    _install_fake_proc(
        monkeypatch,
        {
            1: {"uid": MY_UID, "cpu": 10.0, "rss": 1024, "start": NOW - 100},
            2: {"uid": 7777, "cpu": 900.0, "rss": 1024, "start": NOW - 100},
            3: {"uid": 8888, "cpu": 500.0, "rss": 1024, "start": NOW - 100},
        },
    )
    monkeypatch.setattr(usage.os, "getuid", lambda: MY_UID)

    snap = usage.snapshot()

    assert snap["rank_by_cpu"] == 3
    assert snap["total_users"] == 3
    assert snap["cpu_seconds"] == 10.0


def test_snapshot_records_the_busiest_user_for_context(monkeypatch):
    _install_fake_proc(
        monkeypatch,
        {
            1: {"uid": MY_UID, "cpu": 10.0, "rss": 1024, "start": NOW - 100},
            2: {"uid": 7777, "cpu": 900.0, "rss": 1024, "start": NOW - 100},
        },
    )
    monkeypatch.setattr(usage.os, "getuid", lambda: MY_UID)

    snap = usage.snapshot()

    # Without a busiest-user figure, a reviewer cannot tell whether
    # "10 CPU-seconds" is a lot on this machine or nothing at all.
    assert snap["busiest_user_cpu_seconds"] == 900.0


def test_snapshot_handles_this_user_having_no_processes(monkeypatch):
    _install_fake_proc(
        monkeypatch,
        {1: {"uid": 7777, "cpu": 900.0, "rss": 1024, "start": NOW - 100}},
    )
    monkeypatch.setattr(usage.os, "getuid", lambda: MY_UID)

    snap = usage.snapshot()

    assert snap["cpu_seconds"] == 0.0
    assert snap["procs"] == 0
    assert snap["rank_by_cpu"] is None


# ── persistence ─────────────────────────────────────────────────────────────


def test_append_writes_one_json_line_per_call(tmp_path):
    log = tmp_path / "usage.jsonl"

    usage.append_snapshot(log, {"ts": 1, "cpu_seconds": 1.0})
    usage.append_snapshot(log, {"ts": 2, "cpu_seconds": 2.0})

    lines = log.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["ts"] == 2


def test_append_creates_the_log_directory(tmp_path):
    log = tmp_path / "nested" / "dir" / "usage.jsonl"

    usage.append_snapshot(log, {"ts": 1})

    assert log.exists()


# ── reporting ───────────────────────────────────────────────────────────────


def test_summary_reports_peak_and_median_rank(tmp_path):
    log = tmp_path / "usage.jsonl"
    # Whole minutes so the rendered figures are unambiguous:
    # 600s=10min, 3000s=50min, 1200s=20min -> peak 50.0, median 20.0
    for cpu, rank in ((600.0, 40), (3000.0, 30), (1200.0, 35)):
        usage.append_snapshot(
            log,
            {
                "ts": 1,
                "iso": "2026-09-22T10:00:00",
                "cpu_seconds": cpu,
                "rss_bytes": 1024,
                "procs": 3,
                "rank_by_cpu": rank,
                "total_users": 59,
            },
        )

    text = usage.summarize(log)

    assert "3 samples" in text
    assert "50.0" in text  # peak CPU minutes
    assert "20.0" in text  # median CPU minutes
    assert "35" in text  # median rank


def test_summary_of_a_missing_log_is_not_an_error(tmp_path):
    assert "no samples" in usage.summarize(tmp_path / "absent.jsonl").lower()


def test_summary_ignores_a_corrupt_line(tmp_path):
    # A truncated write (killed mid-append) must not break reporting --
    # this log exists precisely to be readable weeks later.
    log = tmp_path / "usage.jsonl"
    usage.append_snapshot(log, {"ts": 1, "cpu_seconds": 5.0, "rank_by_cpu": 3, "total_users": 9, "rss_bytes": 1, "procs": 1})
    with open(log, "a") as fh:
        fh.write("{not json\n")

    text = usage.summarize(log)

    assert "1 samples" in text


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_main_records_a_sample(tmp_path, monkeypatch):
    log = tmp_path / "usage.jsonl"
    monkeypatch.setattr(usage, "snapshot", lambda: {"ts": 1, "cpu_seconds": 1.0})

    assert usage.main(["--out", str(log)]) == 0
    assert log.exists()


def test_main_report_mode_does_not_record(tmp_path, monkeypatch):
    log = tmp_path / "usage.jsonl"
    monkeypatch.setattr(usage, "snapshot", lambda: {"ts": 1})

    assert usage.main(["--out", str(log), "--report"]) == 0
    assert not log.exists()


# ── the real /proc accessor ─────────────────────────────────────────────────


def test_process_cpu_seconds_is_nonnegative_for_this_process():
    assert usage.process_cpu_seconds(os.getpid()) >= 0.0


def test_process_cpu_seconds_of_a_dead_pid_is_zero():
    assert usage.process_cpu_seconds(0) == 0.0
