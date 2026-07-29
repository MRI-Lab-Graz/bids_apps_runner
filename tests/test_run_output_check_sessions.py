import subprocess

import pytest

import prism_app_runner
import gui.gui_misc_routes as gui_misc_routes


@pytest.fixture
def client():
    prism_app_runner.app.config["TESTING"] = True
    with prism_app_runner.app.test_client() as test_client:
        yield test_client


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="{}", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_forwards_sessions_flag_to_checker(client, tmp_path, monkeypatch):
    """The 'Verify Output' panel's optional Expected Sessions field must
    reach check_app_output.py as --sessions, the same as the cohort
    housekeeping completeness gate does -- otherwise a pipeline
    deliberately scoped to a subset of sessions (e.g. FreeSurfer on
    ses-1/ses-2 of a 3-session dataset) would report false positives here
    even after being fixed for housekeeping."""
    bids_dir = tmp_path / "bids"
    bids_dir.mkdir()
    derivatives_dir = tmp_path / "derivatives"
    derivatives_dir.mkdir()

    calls = []
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompletedProcess()

    monkeypatch.setattr(gui_misc_routes.subprocess, "run", fake_run)

    resp = client.post(
        "/run_output_check",
        json={
            "bids_dir": str(bids_dir),
            "derivatives_dir": str(derivatives_dir),
            "pipeline": "freesurfer",
            "sessions": "ses-1,ses-2",
        },
    )

    assert resp.status_code == 200
    assert len(calls) == 1
    assert "--sessions" in calls[0]
    assert calls[0][calls[0].index("--sessions") + 1] == "ses-1,ses-2"


def test_omits_sessions_flag_when_not_provided(client, tmp_path, monkeypatch):
    bids_dir = tmp_path / "bids"
    bids_dir.mkdir()
    derivatives_dir = tmp_path / "derivatives"
    derivatives_dir.mkdir()

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompletedProcess()

    monkeypatch.setattr(gui_misc_routes.subprocess, "run", fake_run)

    resp = client.post(
        "/run_output_check",
        json={"bids_dir": str(bids_dir), "derivatives_dir": str(derivatives_dir)},
    )

    assert resp.status_code == 200
    assert "--sessions" not in calls[0]
