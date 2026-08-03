import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import prism_datalad


def _fake_run(returncode=0, stdout="", stderr=""):
    def run(cmd, capture_output, text, timeout):
        assert cmd[0] == "ssh"
        assert "-o" in cmd and "BatchMode=yes" in cmd

        class Result:
            pass

        result = Result()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = stderr
        return result

    return run


def test_list_remote_directory_names_returns_sorted_lines(monkeypatch):
    monkeypatch.setattr(
        prism_datalad.subprocess, "run", _fake_run(stdout="ds000031\nds000256\n")
    )

    names = prism_datalad.list_remote_directory_names("datalad-server", "/some/path")

    assert names == ["ds000031", "ds000256"]


def test_list_remote_directory_names_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        prism_datalad.subprocess, "run", _fake_run(returncode=1, stderr="no such file")
    )

    with pytest.raises(RuntimeError, match="no such file"):
        prism_datalad.list_remote_directory_names("datalad-server", "/some/path")


def test_list_remote_directory_names_raises_on_timeout(monkeypatch):
    def raise_timeout(cmd, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(prism_datalad.subprocess, "run", raise_timeout)

    with pytest.raises(RuntimeError, match="Timed out"):
        prism_datalad.list_remote_directory_names("datalad-server", "/some/path")


def test_list_remote_directory_names_raises_when_ssh_missing(monkeypatch):
    def raise_not_found(cmd, capture_output, text, timeout):
        raise FileNotFoundError("ssh")

    monkeypatch.setattr(prism_datalad.subprocess, "run", raise_not_found)

    with pytest.raises(RuntimeError, match="ssh not found"):
        prism_datalad.list_remote_directory_names("datalad-server", "/some/path")


def _fake_run_with_input(returncode=0, stdout="", stderr=""):
    def run(cmd, input, capture_output, text, timeout):
        assert cmd[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        assert cmd[-3:] == ["datalad-server", "bash", "-s"]

        class Result:
            pass

        result = Result()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = stderr
        return result

    return run


def test_run_remote_script_returns_stdout(monkeypatch):
    monkeypatch.setattr(
        prism_datalad.subprocess, "run", _fake_run_with_input(stdout="line1\nline2\n")
    )

    output = prism_datalad.run_remote_script("datalad-server", "echo hi")

    assert output == "line1\nline2\n"


def test_run_remote_script_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        prism_datalad.subprocess,
        "run",
        _fake_run_with_input(returncode=1, stderr="boom"),
    )

    with pytest.raises(RuntimeError, match="boom"):
        prism_datalad.run_remote_script("datalad-server", "echo hi")


def test_run_remote_script_raises_on_timeout(monkeypatch):
    def raise_timeout(cmd, input, capture_output, text, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(prism_datalad.subprocess, "run", raise_timeout)

    with pytest.raises(RuntimeError, match="Timed out"):
        prism_datalad.run_remote_script("datalad-server", "echo hi")
