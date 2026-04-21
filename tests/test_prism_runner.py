import argparse
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import prism_runner


def _args(**overrides):
    defaults = {
        "config": "config.json",
        "local": False,
        "hpc": False,
        "log_level": "INFO",
        "dry_run": False,
        "subjects": None,
        "force": False,
        "start_delay_sec": 0.0,
        "debug": False,
        "jobs": None,
        "pilot": False,
        "validate": False,
        "validate_only": False,
        "reprocess_missing": False,
        "reprocess_from_json": None,
        "clean_success_markers": False,
        "pipeline": None,
        "slurm_only": False,
        "monitor": False,
        "no_datalad": False,
        "nohup": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_parse_arguments_reads_common_flags(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prism_runner.py",
            "-c",
            "config.json",
            "--hpc",
            "--log-level",
            "DEBUG",
            "--subjects",
            "sub-001",
            "sub-002",
        ],
    )

    parsed = prism_runner.parse_arguments()

    assert parsed.config == "config.json"
    assert parsed.hpc is True
    assert parsed.local is False
    assert parsed.log_level == "DEBUG"
    assert parsed.subjects == ["sub-001", "sub-002"]


def test_parse_arguments_without_args_exits_cleanly(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["prism_runner.py"])

    with pytest.raises(SystemExit) as exc:
        prism_runner.parse_arguments()

    assert exc.value.code == 0


def test_main_local_success(monkeypatch):
    calls = []

    monkeypatch.setattr(prism_runner, "fix_system_path", lambda: calls.append("fix"))
    monkeypatch.setattr(prism_runner, "parse_arguments", lambda: _args(local=True))
    monkeypatch.setattr(
        prism_runner,
        "setup_logging",
        lambda level: calls.append(("setup_logging", level)),
    )
    monkeypatch.setattr(
        prism_runner,
        "read_config",
        lambda path: {"common": {}, "app": {"analysis_level": "participant"}},
    )
    monkeypatch.setattr(prism_runner, "detect_execution_mode", lambda cfg, force: "local")
    monkeypatch.setattr(
        prism_runner, "validate_common_config", lambda cfg: calls.append("valid_common")
    )
    monkeypatch.setattr(
        prism_runner, "validate_app_config", lambda cfg: calls.append("valid_app")
    )
    monkeypatch.setattr(
        prism_runner, "validate_hpc_config", lambda cfg: calls.append("valid_hpc")
    )

    fake_local = types.ModuleType("prism_local")
    fake_local.execute_local = lambda cfg, args: True
    monkeypatch.setitem(sys.modules, "prism_local", fake_local)

    assert prism_runner.main() == 0
    assert "valid_hpc" not in calls


def test_main_hpc_failure(monkeypatch):
    calls = []

    monkeypatch.setattr(prism_runner, "fix_system_path", lambda: None)
    monkeypatch.setattr(prism_runner, "parse_arguments", lambda: _args(hpc=True))
    monkeypatch.setattr(prism_runner, "setup_logging", lambda level: None)
    monkeypatch.setattr(
        prism_runner,
        "read_config",
        lambda path: {
            "common": {},
            "app": {"analysis_level": "participant"},
            "hpc": {"partition": "cpu", "time": "01:00:00", "mem": "8G", "cpus": 4},
        },
    )
    monkeypatch.setattr(prism_runner, "detect_execution_mode", lambda cfg, force: "hpc")
    monkeypatch.setattr(prism_runner, "validate_common_config", lambda cfg: None)
    monkeypatch.setattr(prism_runner, "validate_app_config", lambda cfg: None)
    monkeypatch.setattr(
        prism_runner,
        "validate_hpc_config",
        lambda cfg: calls.append("valid_hpc"),
    )

    fake_hpc = types.ModuleType("prism_hpc")
    fake_hpc.execute_hpc = lambda cfg, args: False
    monkeypatch.setitem(sys.modules, "prism_hpc", fake_hpc)

    assert prism_runner.main() == 1
    assert calls == ["valid_hpc"]


def test_main_keyboard_interrupt(monkeypatch):
    monkeypatch.setattr(prism_runner, "fix_system_path", lambda: None)
    monkeypatch.setattr(prism_runner, "parse_arguments", lambda: _args())
    monkeypatch.setattr(prism_runner, "setup_logging", lambda level: None)
    monkeypatch.setattr(
        prism_runner,
        "read_config",
        lambda path: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    assert prism_runner.main() == 130


def test_main_unhandled_exception_returns_one(monkeypatch):
    monkeypatch.setattr(prism_runner, "fix_system_path", lambda: None)
    monkeypatch.setattr(prism_runner, "parse_arguments", lambda: _args())
    monkeypatch.setattr(prism_runner, "setup_logging", lambda level: None)
    monkeypatch.setattr(
        prism_runner,
        "read_config",
        lambda path: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert prism_runner.main() == 1


def test_main_nohup_branch_relaunches_and_exits(monkeypatch, tmp_path):
    launched = {}

    def _fake_popen(cmd, stdout=None, stderr=None, preexec_fn=None):
        launched["cmd"] = cmd
        launched["stdout"] = stdout
        launched["stderr"] = stderr
        launched["preexec_fn"] = preexec_fn
        return object()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(prism_runner, "fix_system_path", lambda: None)
    monkeypatch.setattr(prism_runner, "parse_arguments", lambda: _args(nohup=True))
    monkeypatch.setattr(
        sys,
        "argv",
        ["prism_runner.py", "-c", "config.json", "--nohup", "--dry-run"],
    )
    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    with pytest.raises(SystemExit) as exc:
        prism_runner.main()

    assert exc.value.code == 0
    assert launched["cmd"][0] == sys.executable
    assert "--nohup" not in launched["cmd"]
    assert launched["stderr"] == subprocess.STDOUT
    assert launched["preexec_fn"] == os.setpgrp
