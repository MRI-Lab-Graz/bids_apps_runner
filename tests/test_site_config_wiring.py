"""The routes, cohort config and page honour the site config instead of the
old hardwired lab values -- and say so clearly when no remote is configured."""

import json
import re

import pytest

import gui.gui_utility_routes as utility
import prism_app_runner
from gui.site_config import datalad_url_templates

SITE = {"remote_ssh_host": "data-server", "remote_base_path": "/srv/datasets"}


@pytest.fixture
def client():
    prism_app_runner.app.config["TESTING"] = True
    with prism_app_runner.app.test_client() as c:
        yield c


@pytest.fixture
def no_remote(monkeypatch):
    monkeypatch.setattr(utility, "REMOTE_DATASET_SSH_HOST", "")
    monkeypatch.setattr(utility, "REMOTE_DATASET_BASE_PATH", "")

    def boom(*a, **k):  # nothing may reach ssh/datalad when unconfigured
        raise AssertionError("tried to run a command with no remote configured")

    monkeypatch.setattr(utility.subprocess, "run", boom)
    monkeypatch.setattr(utility.subprocess, "Popen", boom)


@pytest.fixture
def with_remote(monkeypatch):
    monkeypatch.setattr(utility, "REMOTE_DATASET_SSH_HOST", SITE["remote_ssh_host"])
    monkeypatch.setattr(utility, "REMOTE_DATASET_BASE_PATH", SITE["remote_base_path"])


# ── remote routes ───────────────────────────────────────────────────────────


def test_list_remote_studies_says_how_to_configure_when_unset(client, no_remote):
    resp = client.get("/list_remote_studies")
    assert resp.status_code == 400
    assert "PRISM_REMOTE_SSH_HOST" in resp.get_json()["error"]


def test_ssh_probe_reports_unconfigured_instead_of_running_ssh(client, no_remote):
    resp = client.get("/check_datalad_ssh")
    assert resp.status_code == 200  # always 200: the client tells failure kinds apart
    body = resp.get_json()
    assert body["ok"] is False and "PRISM_REMOTE_SSH_HOST" in body["error"]


def test_connect_remote_dataset_refuses_when_unset(client, no_remote):
    resp = client.post("/connect_remote_dataset", json={"study": "ds001"})
    assert resp.status_code == 400
    assert "PRISM_REMOTE_SSH_HOST" in resp.get_json()["error"]


def test_list_remote_studies_uses_the_configured_server(client, with_remote, monkeypatch):
    import prism_datalad

    seen = {}

    def fake(host, path, timeout):
        seen.update(host=host, path=path)
        return ["ds001"]

    monkeypatch.setattr(prism_datalad, "list_remote_directory_names", fake)
    resp = client.get("/list_remote_studies")
    assert resp.get_json() == {"studies": ["ds001"]}
    assert seen == {"host": "data-server", "path": "/srv/datasets"}


# ── cohort DataLad URLs ─────────────────────────────────────────────────────


def test_url_templates_follow_the_configured_server():
    t = datalad_url_templates(SITE, "mriqc")
    assert t["input_url_template"] == "data-server:/srv/datasets/{dataset_id}"
    assert t["output_url_template"] == "ssh://data-server/srv/datasets/{dataset_id}/derivatives/mriqc"


def test_url_templates_refuse_an_unconfigured_server_rather_than_emit_a_broken_url():
    with pytest.raises(ValueError, match="PRISM_REMOTE_SSH_HOST"):
        datalad_url_templates({"remote_ssh_host": "", "remote_base_path": ""}, "mriqc")


# ── the page hands the same values to the JS ────────────────────────────────


def test_index_exposes_site_values_to_the_frontend(client, monkeypatch):
    monkeypatch.setattr(prism_app_runner, "SITE", {**prism_app_runner.SITE, "local_dataset_base_dir": "/scratch/alice/datasets"})
    html = client.get("/").get_data(as_text=True)
    match = re.search(r"window\.PRISM_SITE\s*=\s*(\{.*?\});", html, re.S)
    assert match, "window.PRISM_SITE missing from the page"
    assert json.loads(match.group(1))["localDatasetBase"] == "/scratch/alice/datasets"


def test_the_page_no_longer_hardcodes_the_lab_clone_root(client, monkeypatch):
    # Neutral user: the default scratch is /cl_tmp/<current user>, which for the
    # lab's own account legitimately starts with "mrilab".
    monkeypatch.setattr(prism_app_runner, "SITE", {**prism_app_runner.SITE, "local_dataset_base_dir": "/cl_tmp/alice/datasets"})
    html = client.get("/").get_data(as_text=True)
    assert "/cl_tmp/mrilab" not in html


def test_the_frontend_module_no_longer_hardcodes_the_lab_clone_root():
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "static" / "js" / "bids_source_mode.js").read_text()
    assert "/cl_tmp/mrilab" not in js


def test_the_app_keeps_its_data_where_pick_data_dir_says(monkeypatch, tmp_path):
    target = tmp_path / "mine"
    monkeypatch.setenv("PRISM_DATA_DIR", str(target))
    assert prism_app_runner._get_data_dir() == target
    assert target.is_dir()
