import json
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import prism_core


def test_sanitize_pipeline_id_normalizes_text():
    assert prism_core._sanitize_pipeline_id("  My Pipeline v1.0  ") == "my_pipeline_v1_0"
    assert prism_core._sanitize_pipeline_id(None) == "default"


def test_materialize_runtime_config_merges_active_pipeline():
    config = {
        "common": {"threads": 1, "container": "base.sif"},
        "app": {"analysis_level": "participant", "debug": False},
        "active_pipeline": "QSIPrep Main",
        "pipelines": {
            "QSIPrep Main": {
                "common": {"threads": 8},
                "app": {"analysis_level": "group", "debug": True},
            }
        },
        "keep": "value",
    }

    runtime = prism_core._materialize_runtime_config(config)

    assert runtime["active_pipeline"] == "qsiprep_main"
    assert runtime["common"] == {"threads": 8, "container": "base.sif"}
    assert runtime["app"] == {"analysis_level": "group", "debug": True}
    assert runtime["keep"] == "value"


def test_materialize_runtime_config_falls_back_to_first_pipeline():
    config = {
        "active_pipeline": "does-not-exist",
        "pipelines": {
            "A": {"common": {"x": 1}, "app": {"analysis_level": "participant"}},
            "B": {"common": {"x": 2}, "app": {"analysis_level": "group"}},
        },
    }

    runtime = prism_core._materialize_runtime_config(config)

    assert runtime["active_pipeline"] == "a"
    assert runtime["common"]["x"] == 1


def test_read_config_reads_nested_project_wrapper(tmp_path):
    config_payload = {
        "config": {
            "common": {"threads": 1},
            "app": {"analysis_level": "participant"},
            "active_pipeline": "MyPipe",
            "pipelines": {
                "MyPipe": {
                    "common": {"threads": 4},
                    "app": {"analysis_level": "group", "extra": "yes"},
                }
            },
            "other": 7,
        }
    }
    config_file = tmp_path / "project.json"
    config_file.write_text(json.dumps(config_payload), encoding="utf-8")

    config = prism_core.read_config(str(config_file))

    assert config["active_pipeline"] == "mypipe"
    assert config["common"]["threads"] == 4
    assert config["app"]["analysis_level"] == "group"
    assert config["app"]["extra"] == "yes"
    assert config["other"] == 7


def test_read_config_relative_path_from_cwd(tmp_path, monkeypatch):
    config_payload = {"common": {}, "app": {"analysis_level": "participant"}}
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config_payload), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config = prism_core.read_config("config.json")
    assert config["app"]["analysis_level"] == "participant"


def test_read_config_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        prism_core.read_config("/definitely/missing/config.json")


def test_detect_execution_mode_variants():
    assert prism_core.detect_execution_mode({"hpc": {"partition": "cpu"}}) == "hpc"
    assert prism_core.detect_execution_mode({"hpc": {}}, force_mode="local") == "local"
    assert prism_core.detect_execution_mode({}) == "local"


def test_validate_common_config_missing_section_raises():
    with pytest.raises(ValueError, match="missing 'common'"):
        prism_core.validate_common_config({})


def test_validate_common_config_rejects_container_directory(tmp_path):
    with pytest.raises(ValueError, match="Container is not a file"):
        prism_core.validate_common_config({"common": {"container": str(tmp_path)}})


def test_validate_common_config_accepts_file_container(tmp_path):
    container_file = tmp_path / "container.sif"
    container_file.write_text("x", encoding="utf-8")
    prism_core.validate_common_config({"common": {"container": str(container_file)}})


def test_validate_app_config_errors():
    with pytest.raises(ValueError, match="missing 'app'"):
        prism_core.validate_app_config({})

    with pytest.raises(ValueError, match="missing 'analysis_level'"):
        prism_core.validate_app_config({"app": {}})

    with pytest.raises(ValueError, match="Invalid analysis_level"):
        prism_core.validate_app_config({"app": {"analysis_level": "session"}})


def test_validate_app_config_accepts_valid_level():
    prism_core.validate_app_config({"app": {"analysis_level": "participant"}})
    prism_core.validate_app_config({"app": {"analysis_level": "group"}})


def test_validate_hpc_config_errors_and_success():
    with pytest.raises(ValueError, match="requires 'hpc'"):
        prism_core.validate_hpc_config({})

    with pytest.raises(ValueError, match="missing required field: cpus"):
        prism_core.validate_hpc_config(
            {"hpc": {"partition": "cpu", "time": "01:00:00", "mem": "8G"}}
        )

    prism_core.validate_hpc_config(
        {
            "hpc": {
                "partition": "cpu",
                "time": "01:00:00",
                "mem": "8G",
                "cpus": 4,
            }
        }
    )


def test_fix_system_path_adds_known_existing_paths(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setattr(
        prism_core.os.path,
        "exists",
        lambda p: p in {"/usr/bin", "/usr/local/bin", "/bin"},
    )

    prism_core.fix_system_path()
    updated = prism_core.os.environ["PATH"].split(prism_core.os.pathsep)

    assert "/usr/local/bin" in updated
    assert "/bin" in updated


def test_get_subjects_from_bids_returns_sorted_ids(tmp_path):
    (tmp_path / "sub-010").mkdir()
    (tmp_path / "sub-002").mkdir()
    (tmp_path / "sub-001").mkdir()
    (tmp_path / "sub-abc").mkdir()
    (tmp_path / "sub-999.txt").write_text("ignore", encoding="utf-8")

    subjects = prism_core.get_subjects_from_bids(str(tmp_path))

    assert subjects == ["001", "002", "010", "abc"]


def test_get_subjects_from_bids_missing_folder_behaviors(tmp_path):
    missing = tmp_path / "does-not-exist"

    assert prism_core.get_subjects_from_bids(str(missing), dry_run=True) == []
    with pytest.raises(FileNotFoundError):
        prism_core.get_subjects_from_bids(str(missing), dry_run=False)


def test_print_summary_formats_output(capsys):
    prism_core.print_summary(["001", "002"], ["003"], 90.0)
    output = capsys.readouterr().out

    assert "Successfully processed: 2 subjects" in output
    assert "Failed: 1 subjects" in output
    assert "Total time: 90.00 seconds (1.5 minutes)" in output


def test_run_command_dry_run_returns_none():
    assert prism_core.run_command([sys.executable, "-c", "print('x')"], dry_run=True) is None


def test_run_command_success_returns_completed_process():
    result = prism_core.run_command(
        [sys.executable, "-c", "print('hello')"], capture_output=True, check=True
    )
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_run_command_failure_modes():
    with pytest.raises(Exception):
        prism_core.run_command(
            [sys.executable, "-c", "import sys; sys.exit(3)"],
            capture_output=True,
            check=True,
        )

    result = prism_core.run_command(
        [sys.executable, "-c", "import sys; sys.exit(4)"],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 4


def test_setup_logging_creates_log_file(tmp_path):
    log_file = prism_core.setup_logging(log_level="INFO", log_dir=tmp_path)

    assert log_file.parent == tmp_path
    assert log_file.name.startswith("prism_runner_")
    assert log_file.suffix == ".log"
    assert log_file.exists()
