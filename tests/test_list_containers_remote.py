"""Tests for /list_containers' remote-catalog merge and /fetch_container.

Covers the GUI's "show containers available on the DataLad server but not
yet downloaded here, and let the user fetch one on demand" feature.
"""
import sys
import time
from pathlib import Path

import pytest
from flask import Flask

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import prism_datalad  # noqa: E402
from gui.gui_misc_routes import register_misc_routes  # noqa: E402


def _make_app(machine_settings=None):
    app = Flask(__name__)
    register_misc_routes(
        app,
        bids_output_validator_cls=object,
        ensure_logs_dir=lambda: None,
        log_dir=Path("/tmp"),
        base_dir=ROOT,
        machine_settings_provider=lambda: dict(machine_settings or {}),
    )
    app.config["TESTING"] = True
    return app


def _write_sif(root: Path, relpath: str) -> Path:
    p = root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"")
    return p


def test_list_containers_scans_subfolders_recursively(tmp_path):
    _write_sif(tmp_path, "mriqc/mriqc_24.0.2.sif")
    app = _make_app()

    with app.test_client() as c:
        resp = c.post("/list_containers", json={"folder": str(tmp_path)})

    assert resp.get_json()["containers"] == [
        {"name": "mriqc/mriqc_24.0.2.sif", "local": True}
    ]


def test_list_containers_merges_remote_catalog_at_configured_root(tmp_path, monkeypatch):
    _write_sif(tmp_path, "mriqc/mriqc_24.0.2.sif")
    monkeypatch.setattr(
        prism_datalad,
        "run_remote_script",
        lambda ssh_host, script, timeout=30: (
            "mriqc/mriqc_24.0.2.sif\nfmriprep/fmriprep_24.1.1.sif\n"
        ),
    )
    app = _make_app(
        {
            "default_apptainer_folder": str(tmp_path),
            "remote_container_path": "datalad-server:/datalad/mri/container/",
        }
    )

    with app.test_client() as c:
        resp = c.post("/list_containers", json={"folder": str(tmp_path)})

    containers = resp.get_json()["containers"]
    assert {"name": "mriqc/mriqc_24.0.2.sif", "local": True} in containers
    assert {"name": "fmriprep/fmriprep_24.1.1.sif", "local": False} in containers


def test_list_containers_skips_remote_merge_outside_configured_root(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        prism_datalad,
        "run_remote_script",
        lambda *a, **kw: calls.append(1) or "",
    )
    app = _make_app(
        {
            "default_apptainer_folder": "/some/other/root",
            "remote_container_path": "datalad-server:/datalad/mri/container/",
        }
    )

    with app.test_client() as c:
        resp = c.post("/list_containers", json={"folder": str(tmp_path)})

    assert calls == []
    assert resp.get_json()["containers"] == []


def test_list_containers_reports_remote_error_without_failing(tmp_path, monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("Timed out contacting datalad-server")

    monkeypatch.setattr(prism_datalad, "run_remote_script", _boom)
    app = _make_app(
        {
            "default_apptainer_folder": str(tmp_path),
            "remote_container_path": "datalad-server:/datalad/mri/container/",
        }
    )

    with app.test_client() as c:
        resp = c.post("/list_containers", json={"folder": str(tmp_path)})

    data = resp.get_json()
    assert data["containers"] == []
    assert "Timed out" in data["remote_error"]


def test_fetch_container_rejects_path_traversal(tmp_path):
    app = _make_app({"remote_container_path": "datalad-server:/datalad/mri/container/"})

    with app.test_client() as c:
        resp = c.post(
            "/fetch_container",
            json={"folder": str(tmp_path), "name": "../../etc/passwd"},
        )

    assert resp.status_code == 400


def test_fetch_container_requires_remote_container_path_configured(tmp_path):
    app = _make_app({})

    with app.test_client() as c:
        resp = c.post(
            "/fetch_container",
            json={"folder": str(tmp_path), "name": "mriqc/mriqc_24.0.2.sif"},
        )

    assert resp.status_code == 400


def test_fetch_container_runs_rsync_and_reports_completion(tmp_path, monkeypatch):
    calls = []

    class FakeProc:
        stdout = ["receiving file list...\n", "mriqc/mriqc_24.0.2.sif\n"]
        returncode = 0

        def wait(self):
            return None

    def fake_popen(cmd, **kwargs):
        calls.append(cmd)
        return FakeProc()

    monkeypatch.setattr("gui.gui_misc_routes.subprocess.Popen", fake_popen)

    app = _make_app({"remote_container_path": "datalad-server:/datalad/mri/container/"})

    with app.test_client() as c:
        resp = c.post(
            "/fetch_container",
            json={"folder": str(tmp_path), "name": "mriqc/mriqc_24.0.2.sif"},
        )
        assert resp.status_code == 200
        job_id = resp.get_json()["job_id"]

        status_data = None
        for _ in range(50):
            status_resp = c.get(f"/fetch_container_status?job_id={job_id}")
            status_data = status_resp.get_json()
            if status_data["status"] != "running":
                break
            time.sleep(0.05)

    assert status_data["status"] == "completed"
    assert calls[0][0] == "rsync"
    assert calls[0][-2] == "datalad-server:/datalad/mri/container/mriqc/mriqc_24.0.2.sif"
    assert calls[0][-1] == str(tmp_path / "mriqc" / "mriqc_24.0.2.sif")
