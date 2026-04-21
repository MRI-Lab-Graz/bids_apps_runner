import json
import types
from pathlib import Path

import pytest


ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(SCRIPTS_DIR))

import pilot_resource_estimator as pre


def test_load_config_supports_project_wrapper(tmp_path):
    config_path = tmp_path / "project.json"
    payload = {"config": {"common": {"jobs": 1}, "app": {"analysis_level": "participant"}}}
    config_path.write_text(json.dumps(payload), encoding="utf-8")

    data, resolved = pre.load_config(str(config_path))

    assert data["common"]["jobs"] == 1
    assert data["app"]["analysis_level"] == "participant"
    assert resolved == config_path.resolve()


def test_load_config_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        pre.load_config(str(tmp_path / "missing.json"))


def test_normalize_subject_behavior():
    assert pre.normalize_subject(None) is None
    assert pre.normalize_subject("sub-001") == "sub-001"
    assert pre.normalize_subject("001") == "sub-001"


def test_discover_subject_from_bids_folder(tmp_path):
    bids_dir = tmp_path / "bids"
    (bids_dir / "sub-003").mkdir(parents=True)
    (bids_dir / "sub-001").mkdir(parents=True)
    (bids_dir / "sub-002").mkdir(parents=True)

    config = {"common": {"bids_folder": str(bids_dir)}}
    assert pre.discover_subject(config) == "sub-001"


def test_discover_subject_errors(tmp_path):
    with pytest.raises(ValueError, match="common.bids_folder"):
        pre.discover_subject({"common": {}})

    with pytest.raises(FileNotFoundError):
        pre.discover_subject({"common": {"bids_folder": str(tmp_path / "missing")}})

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="No subjects found"):
        pre.discover_subject({"common": {"bids_folder": str(empty)}})


def test_parse_nprocs_list_valid_and_invalid():
    assert pre.parse_nprocs_list("8, 4,8,2") == [2, 4, 8]

    with pytest.raises(ValueError, match="must be > 0"):
        pre.parse_nprocs_list("0,2")

    with pytest.raises(ValueError, match="No valid nprocs"):
        pre.parse_nprocs_list(" , , ")


def test_build_auto_nprocs_list(monkeypatch):
    monkeypatch.setattr(pre.os, "cpu_count", lambda: 10)

    values, detected = pre.build_auto_nprocs_list(nprocs_min=2, nprocs_max=9, nprocs_step=3)
    assert values == [2, 5, 8, 9]
    assert detected == 10

    with pytest.raises(ValueError, match="nprocs-step"):
        pre.build_auto_nprocs_list(nprocs_min=1, nprocs_max=2, nprocs_step=0)

    with pytest.raises(ValueError, match="Invalid nprocs bounds"):
        pre.build_auto_nprocs_list(nprocs_min=12, nprocs_max=6, nprocs_step=1)


def test_drop_flag_and_set_app_resource_options():
    options = ["--nprocs", "8", "--x", "1", "--nthreads=4", "--keep", "y"]
    assert pre.drop_flag(options, "--nprocs") == ["--x", "1", "--nthreads=4", "--keep", "y"]

    cfg = {"app": {"options": ["--nthreads", "16", "--foo", "bar", "--n-cpus=2"]}}
    pre.set_app_resource_options(cfg, nprocs=6, omp_nthreads=2)
    assert cfg["app"]["options"][-4:] == ["--nprocs", "6", "--omp-nthreads", "2"]
    assert "--nthreads" not in cfg["app"]["options"]


def test_start_gpu_monitor_returns_none_when_nvidia_smi_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(pre.shutil, "which", lambda _: None)
    assert pre.start_gpu_monitor(tmp_path / "gpu.csv", sample_seconds=2) is None


def test_stop_gpu_monitor_none_is_safe():
    pre.stop_gpu_monitor(None)


def test_stop_gpu_monitor_terminates_process(monkeypatch, tmp_path):
    events = []

    class DummyProc:
        pid = 123

        @staticmethod
        def poll():
            return None

        @staticmethod
        def wait(timeout=None):
            events.append(("wait", timeout))

    class DummyHandle:
        closed = False

        def close(self):
            self.closed = True

    monkeypatch.setattr(pre.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(pre.os, "killpg", lambda pgid, sig: events.append(("kill", pgid, sig)))

    handle = DummyHandle()
    pre.stop_gpu_monitor((DummyProc(), handle))

    assert any(e[0] == "kill" for e in events)
    assert any(e[0] == "wait" for e in events)
    assert handle.closed is True


def test_parse_elapsed_to_seconds_formats():
    assert pre.parse_elapsed_to_seconds("1:02:03") == 3723.0
    assert pre.parse_elapsed_to_seconds("02:30") == 150.0
    assert pre.parse_elapsed_to_seconds("12.5") == 12.5


def test_parse_time_metrics_reads_rss_and_elapsed(tmp_path):
    stderr_file = tmp_path / "stderr.log"
    stderr_file.write_text(
        "Maximum resident set size (kbytes): 204800\n"
        "Elapsed (wall clock) time (h:mm:ss or m:ss): 01:02:03\n",
        encoding="utf-8",
    )

    rss_kb, elapsed = pre.parse_time_metrics(stderr_file)
    assert rss_kb == 204800
    assert elapsed == 3723.0


def test_parse_gpu_metrics_cases(tmp_path):
    missing = tmp_path / "missing.csv"
    empty_stats = pre.parse_gpu_metrics(missing)
    assert empty_stats["samples"] == 0

    gpu_csv = tmp_path / "gpu.csv"
    gpu_csv.write_text(
        "2026-01-01,0,gpu0,30,10,1000,8000\n"
        "invalid,row\n"
        "2026-01-01,0,gpu0,50,20,1200,8000\n",
        encoding="utf-8",
    )
    stats = pre.parse_gpu_metrics(gpu_csv)
    assert stats["samples"] == 2
    assert stats["gpu_util_max"] == 50.0
    assert stats["gpu_mem_used_max_mb"] == 1200.0


def test_format_hms_and_pick_recommended_cpu():
    assert pre.format_hms(3661.1) == "01:01:02"

    results = [
        {"nprocs": 2, "wall_seconds": 100.0},
        {"nprocs": 4, "wall_seconds": 80.0},
        {"nprocs": 8, "wall_seconds": 76.0},
    ]
    rec = pre.pick_recommended_cpu(results, min_gain=0.10)
    assert rec["nprocs"] == 4

    rec2 = pre.pick_recommended_cpu(results, min_gain=0.01)
    assert rec2["nprocs"] == 8


def test_write_report_generates_expected_sections(tmp_path):
    report_path = tmp_path / "report.md"
    args = types.SimpleNamespace()
    results = [
        {
            "nprocs": 4,
            "returncode": 0,
            "wall_seconds": 120.5,
            "max_rss_kb": 1024 * 1024,
            "gpu_util_max": 40.0,
            "gpu_mem_used_max_mb": 500.0,
        }
    ]
    recommendation = {
        "cpus": 4,
        "mem": "8G",
        "time": "00:05:00",
        "use_gpu": True,
        "source_nprocs": 4,
        "source_wall_seconds": 120.5,
        "source_max_rss_gib": 1.0,
    }

    pre.write_report(
        report_path=report_path,
        args=args,
        subject="sub-001",
        config_path=Path("config.json"),
        results=results,
        recommendation=recommendation,
    )

    text = report_path.read_text(encoding="utf-8")
    assert "# Pilot Resource Estimation Report" in text
    assert "Recommended cpus: 4" in text
    assert '"sbatch_gres": "gpu:1"' in text


def test_main_success_single_run(monkeypatch, tmp_path):
    config = {
        "common": {"jobs": 2, "bids_folder": str(tmp_path / "bids")},
        "app": {"options": ["--nthreads", "8"]},
    }
    config_file = tmp_path / "config.json"
    config_file.write_text("{}", encoding="utf-8")

    args = types.SimpleNamespace(
        config=str(config_file),
        subject="001",
        output_dir=str(tmp_path / "out"),
        nprocs="4",
        nprocs_min=2,
        nprocs_max=None,
        nprocs_step=1,
        omp_nthreads=None,
        gpu_sample_sec=1,
        min_speedup_gain=0.1,
        mem_safety_factor=1.3,
        time_safety_factor=1.7,
        gpu_util_threshold=20.0,
        force=True,
        keep_temp_configs=False,
    )

    monkeypatch.setattr(pre, "parse_args", lambda: args)
    monkeypatch.setattr(pre, "load_config", lambda _: (json.loads(json.dumps(config)), config_file))
    monkeypatch.setattr(pre, "parse_nprocs_list", lambda raw: [4])
    monkeypatch.setattr(pre, "normalize_subject", lambda subj: "sub-001")
    monkeypatch.setattr(pre, "discover_subject", lambda cfg: "sub-999")
    monkeypatch.setattr(pre, "start_gpu_monitor", lambda *_: None)
    monkeypatch.setattr(pre, "stop_gpu_monitor", lambda *_: None)
    monkeypatch.setattr(pre, "parse_time_metrics", lambda _: (1024 * 1024, 30.0))
    monkeypatch.setattr(
        pre,
        "parse_gpu_metrics",
        lambda _: {
            "samples": 2,
            "gpu_util_max": 35.0,
            "gpu_util_avg": 20.0,
            "gpu_mem_used_max_mb": 700.0,
        },
    )

    class DummyRunResult:
        returncode = 0

    monkeypatch.setattr(pre.subprocess, "run", lambda *a, **k: DummyRunResult())

    reports = {}

    def _capture_report(report_path, args, subject, config_path, results, recommendation):
        reports["report_path"] = report_path
        reports["subject"] = subject
        reports["results"] = results
        reports["recommendation"] = recommendation

    monkeypatch.setattr(pre, "write_report", _capture_report)

    pre.main()

    out_dir = Path(args.output_dir)
    assert (out_dir / "pilot_results.json").exists()
    assert reports["subject"] == "sub-001"
    assert reports["results"][0]["nprocs"] == 4
    assert reports["recommendation"]["cpus"] == 4


def test_main_raises_when_all_runs_fail(monkeypatch, tmp_path):
    config = {
        "common": {"jobs": 2, "bids_folder": str(tmp_path / "bids")},
        "app": {"options": []},
    }
    config_file = tmp_path / "config.json"
    config_file.write_text("{}", encoding="utf-8")

    args = types.SimpleNamespace(
        config=str(config_file),
        subject="sub-001",
        output_dir=str(tmp_path / "out_fail"),
        nprocs="2",
        nprocs_min=2,
        nprocs_max=None,
        nprocs_step=1,
        omp_nthreads=None,
        gpu_sample_sec=1,
        min_speedup_gain=0.1,
        mem_safety_factor=1.3,
        time_safety_factor=1.7,
        gpu_util_threshold=20.0,
        force=False,
        keep_temp_configs=True,
    )

    monkeypatch.setattr(pre, "parse_args", lambda: args)
    monkeypatch.setattr(pre, "load_config", lambda _: (json.loads(json.dumps(config)), config_file))
    monkeypatch.setattr(pre, "parse_nprocs_list", lambda raw: [2])
    monkeypatch.setattr(pre, "normalize_subject", lambda subj: "sub-001")
    monkeypatch.setattr(pre, "start_gpu_monitor", lambda *_: None)
    monkeypatch.setattr(pre, "stop_gpu_monitor", lambda *_: None)
    monkeypatch.setattr(pre, "parse_time_metrics", lambda _: (None, 10.0))
    monkeypatch.setattr(
        pre,
        "parse_gpu_metrics",
        lambda _: {
            "samples": 0,
            "gpu_util_max": 0.0,
            "gpu_util_avg": 0.0,
            "gpu_mem_used_max_mb": 0.0,
        },
    )

    class DummyFailResult:
        returncode = 1

    monkeypatch.setattr(pre.subprocess, "run", lambda *a, **k: DummyFailResult())

    with pytest.raises(RuntimeError, match="All pilot runs failed"):
        pre.main()

    assert (Path(args.output_dir) / "pilot_results.json").exists()
