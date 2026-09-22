"""Tests for scripts/prism_notify.py -- the GUI's push notifications.

This project moved off SMTP to ntfy: cluster jobs already notify via
scripts/notify_ntfy.sh from inside the sbatch scripts that
hpc_datalad_runner.py generates, but the GUI still carried a parallel
email path that nobody read. This module gives the GUI the same
transport the cluster side uses.

Notification is best-effort by design. A push that fails must never take
down or stall the run it was reporting on, so every failure mode here
must come back as a quiet False rather than an exception.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import prism_notify


class _Result:
    def __init__(self, returncode):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def test_notify_invokes_the_shared_ntfy_script(monkeypatch):
    calls = []
    monkeypatch.setattr(
        prism_notify.subprocess,
        "run",
        lambda cmd, **kw: (calls.append(cmd), _Result(0))[1],
    )

    assert prism_notify.notify("Run finished", "ds004592 completed") is True

    (cmd,) = calls
    assert cmd[0].endswith("notify_ntfy.sh")
    assert cmd[1] == "Run finished"
    assert cmd[2] == "ds004592 completed"


def test_notify_forwards_priority_and_tags(monkeypatch):
    calls = []
    monkeypatch.setattr(
        prism_notify.subprocess,
        "run",
        lambda cmd, **kw: (calls.append(cmd), _Result(0))[1],
    )

    prism_notify.notify("Run failed", "ds005339", priority="high", tags="rotating_light")

    (cmd,) = calls
    assert cmd[3] == "high"
    assert cmd[4] == "rotating_light"


def test_notify_returns_false_when_the_script_fails(monkeypatch):
    monkeypatch.setattr(prism_notify.subprocess, "run", lambda cmd, **kw: _Result(1))

    assert prism_notify.notify("t", "m") is False


def test_notify_never_raises_when_the_script_is_missing(monkeypatch):
    def _boom(cmd, **kw):
        raise FileNotFoundError("notify_ntfy.sh")

    monkeypatch.setattr(prism_notify.subprocess, "run", _boom)

    assert prism_notify.notify("t", "m") is False


def test_notify_never_raises_on_timeout(monkeypatch):
    def _slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd="notify_ntfy.sh", timeout=10)

    monkeypatch.setattr(prism_notify.subprocess, "run", _slow)

    assert prism_notify.notify("t", "m") is False


def test_notify_uses_a_timeout_so_a_hung_push_cannot_stall_a_run(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        prism_notify.subprocess,
        "run",
        lambda cmd, **kw: (seen.update(kw), _Result(0))[1],
    )

    prism_notify.notify("t", "m")

    assert seen.get("timeout")


def test_run_completion_message_names_the_outcome():
    ok = prism_notify.run_completion_message("ds004592", success=True, detail="")
    bad = prism_notify.run_completion_message("ds004592", success=False, detail="oom")

    assert "ds004592" in ok["title"] and "ds004592" in bad["title"]
    assert ok["priority"] != bad["priority"]
    assert "oom" in bad["message"]
