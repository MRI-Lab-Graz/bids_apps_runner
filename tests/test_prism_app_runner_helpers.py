import requests

import prism_app_runner


def test_default_machine_settings_prefers_docker_on_darwin(monkeypatch):
    monkeypatch.setattr(prism_app_runner.platform, "system", lambda: "Darwin")

    settings = prism_app_runner._default_machine_settings()

    assert settings["preferred_container_engine"] == "docker"
    assert settings["default_jobs"] == 1


def test_sanitize_machine_settings_filters_and_bounds_values():
    raw = {
        "preferred_container_engine": "  DOCKER  ",
        "allow_apptainer": 0,
        "allow_docker": "yes",
        "default_tmp_folder": "  /tmp/work  ",
        "default_jobs": "999",
        "unknown": "ignored",
    }

    cleaned = prism_app_runner._sanitize_machine_settings(raw)

    assert cleaned["preferred_container_engine"] == "docker"
    assert cleaned["allow_apptainer"] is False
    assert cleaned["allow_docker"] is True
    assert cleaned["default_tmp_folder"] == "/tmp/work"
    assert cleaned["default_jobs"] == 256
    assert "unknown" not in cleaned


def test_resolve_container_engine_fallback_paths():
    assert (
        prism_app_runner._resolve_container_engine(
            "docker",
            dependencies={"docker": True, "apptainer": False, "singularity": False},
        )
        == "docker"
    )

    assert (
        prism_app_runner._resolve_container_engine(
            "docker",
            dependencies={"docker": False, "apptainer": True, "singularity": False},
        )
        == "apptainer"
    )

    assert (
        prism_app_runner._resolve_container_engine(
            "auto",
            allow_apptainer=False,
            allow_docker=True,
            dependencies={"docker": False, "apptainer": False, "singularity": False},
        )
        == "docker"
    )


def test_version_and_extension_helpers():
    assert prism_app_runner._strip_container_extension("fmriprep_24.1.0.sif") == "fmriprep_24.1.0"
    assert prism_app_runner._strip_container_extension("mriqc-v23.0.0.IMG") == "mriqc-v23.0.0"
    assert prism_app_runner._numeric_version_key("24.1.0rc1") == (24, 1, 0)
    assert prism_app_runner._numeric_version_key("latest") is None


def test_drop_flag_with_value_removes_both_forms():
    options = [
        "--nprocs",
        "8",
        "--x",
        "1",
        "--nprocs=4",
        "--keep",
        "yes",
    ]

    cleaned = prism_app_runner._drop_flag_with_value(options, "--nprocs")

    assert cleaned == ["--x", "1", "--keep", "yes"]


def test_apply_max_usage_cap_rewrites_cpu_flags(monkeypatch):
    monkeypatch.setattr(prism_app_runner.os, "cpu_count", lambda: 16)
    runtime_cfg = {
        "common": {"jobs": 20},
        "app": {"options": ["--nthreads", "32", "--flag", "x"]},
    }

    capped, allowed, detected = prism_app_runner._apply_max_usage_cap(runtime_cfg, 50)

    assert detected == 16
    assert allowed == 8
    assert capped["common"]["jobs"] == 8
    assert "--nthreads" not in capped["app"]["options"]
    assert capped["app"]["options"][-2:] == ["--nprocs", "8"]
    assert runtime_cfg["common"]["jobs"] == 20


def test_compute_auto_nprocs_values_handles_steps_and_bounds(monkeypatch):
    monkeypatch.setattr(prism_app_runner.os, "cpu_count", lambda: 10)

    assert prism_app_runner._compute_auto_nprocs_values(2, None, 3) == [2, 5, 8, 10]
    assert prism_app_runner._compute_auto_nprocs_values(12, 6, 1) == [6]


def test_get_latest_version_from_dockerhub_selects_semver(monkeypatch):
    class DummyResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "results": [
                    {"name": "latest"},
                    {"name": "stable"},
                    {"name": "24.1.0"},
                    {"name": "23.2.0"},
                ]
            }

    monkeypatch.setattr(prism_app_runner.requests, "get", lambda *args, **kwargs: DummyResponse())

    assert prism_app_runner.get_latest_version_from_dockerhub("nipreps/fmriprep") == "24.1.0"


def test_get_latest_version_from_dockerhub_handles_errors(monkeypatch):
    class ErrorResponse:
        status_code = 503

        @staticmethod
        def json():
            return {"results": []}

    monkeypatch.setattr(prism_app_runner.requests, "get", lambda *args, **kwargs: ErrorResponse())
    assert prism_app_runner.get_latest_version_from_dockerhub("nipreps/fmriprep") is None

    def _raise(*args, **kwargs):
        raise requests.RequestException("offline")

    monkeypatch.setattr(prism_app_runner.requests, "get", _raise)
    assert prism_app_runner.get_latest_version_from_dockerhub("nipreps/fmriprep") is None
