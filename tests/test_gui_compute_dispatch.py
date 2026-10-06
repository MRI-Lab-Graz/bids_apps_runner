"""GUI routes that do real work must not do it on the login node.

execute_local()'s guard only covered /run_app and the pilot estimator. The
routes below still ran `git status` (proven to hang on 134), datalad
slurm-finish/save/drop, check_app_output.py walks and `apptainer run`
straight in the GUI process on IT010128. On a bare login node they now go
through `srun` (stdout/stderr/exit code pass through unchanged, so the
routes keep working); off a login node nothing changes.
"""

import subprocess
from pathlib import Path

import pytest

import gui.gui_cohort_routes as gui_cohort_routes
import gui.gui_misc_routes as gui_misc_routes
import login_node_hygiene
import prism_app_runner
from test_cohort_storage_sync import (  # noqa: F401  (fixtures)
    _FakeCompletedProcess,
    _completeness_stdout,
    _make_synced_repo,
    _save_runnable_project,
    client,
    disposable_project,
)


# ── the helper ──────────────────────────────────────────────────────────────


def test_compute_node_cmd_is_identity_off_login_node(monkeypatch):
    monkeypatch.setattr(login_node_hygiene, "on_bare_slurm_login_node", lambda: False)
    cmd = ["git", "-C", "/x", "ls-files"]
    assert login_node_hygiene.compute_node_cmd(cmd, time="00:10:00", mem="2G") == cmd


def test_compute_node_cmd_prefixes_srun_on_login_node(monkeypatch):
    monkeypatch.setattr(login_node_hygiene, "on_bare_slurm_login_node", lambda: True)
    monkeypatch.delenv("PRISM_COMPUTE_PARTITION", raising=False)
    cmd = login_node_hygiene.compute_node_cmd(
        ["datalad", "drop", "."], time="00:30:00", mem="4G", cpus=2
    )
    assert cmd[:5] == [
        "srun", "--quiet", "--time=00:30:00", "--mem=4G", "--cpus-per-task=2",
    ]
    assert cmd[-3:] == ["datalad", "drop", "."]


def test_compute_node_cmd_puts_the_portable_venv_first_inside_the_job(monkeypatch):
    """2026-10-06: cohort setup under srun died with "No module named
    'datalad'". The GUI's PATH starts with .appsrunner/bin, whose python is
    a symlink to the login node's /usr/bin/python3 (3.10); compute nodes
    run 3.12. .datalad-slurm-venv uses a uv-managed python in $HOME and
    works on both -- it must win inside the job, ahead of the inherited
    PATH, which must otherwise survive."""
    monkeypatch.setattr(login_node_hygiene, "on_bare_slurm_login_node", lambda: True)
    monkeypatch.delenv("PRISM_COMPUTE_PARTITION", raising=False)
    cmd = login_node_hygiene.compute_node_cmd(
        ["sh", "-c", 'printf %s "$PATH"'], time="00:01:00", mem="1G"
    )
    # Run exactly what srun would run on the node, minus srun's own options.
    in_job = cmd[5:]
    out = subprocess.run(
        in_job, capture_output=True, text=True, check=True,
        env={"PATH": "/inherited/bin:/usr/bin:/bin"},
    ).stdout
    venv_bin = str(Path(login_node_hygiene.__file__).resolve().parents[1] / ".datalad-slurm-venv" / "bin")
    assert out.split(":") == [venv_bin, "/inherited/bin", "/usr/bin", "/bin"]


def test_compute_node_cmd_honors_partition_env(monkeypatch):
    monkeypatch.setattr(login_node_hygiene, "on_bare_slurm_login_node", lambda: True)
    monkeypatch.setenv("PRISM_COMPUTE_PARTITION", "short")
    cmd = login_node_hygiene.compute_node_cmd(["true"], time="00:01:00", mem="1G")
    assert "--partition=short" in cmd


# ── git sync check: known-state commands only ───────────────────────────────


def _recording_run(calls, module):
    real_run = subprocess.run

    def run(cmd, **kwargs):
        calls.append(list(cmd))
        return real_run(cmd, **kwargs)

    return run


def test_git_sync_status_never_runs_git_status(tmp_path, monkeypatch):
    # `git status` compares index vs worktree: it hangs on 134 (CLAUDE.md).
    work = _make_synced_repo(tmp_path)
    (work / "stray.txt").write_text("x\n")
    calls = []
    monkeypatch.setattr(gui_cohort_routes.subprocess, "run", _recording_run(calls, gui_cohort_routes))

    result = gui_cohort_routes._git_sync_status(str(work))

    assert result["uncommitted"] == 1
    flat = [" ".join(c) for c in calls]
    assert not any(" status" in c for c in flat), flat


def test_git_sync_status_runs_through_srun_on_login_node(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return _FakeCompletedProcess(stdout="main\t0\t0\n")

    work = _make_synced_repo(tmp_path)
    monkeypatch.setattr(login_node_hygiene, "on_bare_slurm_login_node", lambda: True)
    monkeypatch.setattr(gui_cohort_routes.subprocess, "run", fake_run)

    result = gui_cohort_routes._git_sync_status(str(work))

    assert result["ok"] is True
    assert calls and all(c[0] == "srun" for c in calls)


# ── routes ──────────────────────────────────────────────────────────────────


@pytest.fixture
def on_login_node(monkeypatch):
    monkeypatch.setattr(login_node_hygiene, "on_bare_slurm_login_node", lambda: True)


def test_storage_sync_checker_runs_through_srun(
    client, disposable_project, tmp_path, monkeypatch, on_login_node
):
    output = _make_synced_repo(tmp_path, name="derivatives")
    _save_runnable_project(disposable_project, tmp_path, output, pipeline_app_name="mriqc")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if any("check_app_output.py" in str(part) for part in cmd):
            return _FakeCompletedProcess(stdout=_completeness_stdout("mriqc", []))
        return _FakeCompletedProcess(stdout="main\t0\t0\n")

    monkeypatch.setattr(gui_cohort_routes.subprocess, "run", fake_run)

    resp = client.get(
        f"/cohort/check_storage_sync?project_id={disposable_project}&pipeline_id=default"
    )

    assert resp.status_code == 200, resp.get_json()
    assert len(calls) >= 2
    assert all(c[0] == "srun" for c in calls), calls


def test_cleanup_datalad_drop_runs_through_srun(
    client, disposable_project, tmp_path, monkeypatch, on_login_node
):
    output = _make_synced_repo(tmp_path, name="derivatives")
    _save_runnable_project(disposable_project, tmp_path, output, pipeline_app_name="mriqc")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if any("check_app_output.py" in str(part) for part in cmd):
            return _FakeCompletedProcess(stdout=_completeness_stdout("mriqc", []))
        return _FakeCompletedProcess(stdout="main\t0\t0\n")

    monkeypatch.setattr(gui_cohort_routes.subprocess, "run", fake_run)

    resp = client.post(
        "/cohort/cleanup_local_storage",
        json={"project_id": disposable_project, "pipeline_id": "default"},
    )

    assert resp.status_code == 200, resp.get_json()
    drops = [c for c in calls if "drop" in c]
    assert drops and all(c[0] == "srun" for c in drops)


@pytest.mark.parametrize(
    "command,dry_run,wrapped",
    [
        ("setup", False, True),
        ("submit", False, True),
        ("submit-subregions", False, True),
        ("submit", True, False),  # dry-run only prints
        ("status", False, False),  # sacct only
    ],
)
def test_cohort_run_dispatch(
    client, disposable_project, tmp_path, monkeypatch, on_login_node, command, dry_run, wrapped
):
    output = tmp_path / "derivatives"
    _save_runnable_project(disposable_project, tmp_path, output)
    started = []

    class _NoThread:
        def __init__(self, target, args, daemon):
            started.append(args[1])

        def start(self):
            pass

    monkeypatch.setattr(gui_cohort_routes.threading, "Thread", _NoThread)
    import app_profiles

    monkeypatch.setattr(app_profiles, "check_gpu_request_feasible", lambda hpc: None)

    resp = client.post(
        "/cohort/run",
        json={
            "command": command,
            "project_id": disposable_project,
            "pipeline_id": "default",
            "dry_run": dry_run,
        },
    )

    assert resp.status_code == 200, resp.get_json()
    assert (started[0][0] == "srun") is wrapped, started[0]


def test_run_output_check_runs_through_srun(client, tmp_path, monkeypatch, on_login_node):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return _FakeCompletedProcess(stdout="{}")

    monkeypatch.setattr(gui_misc_routes.subprocess, "run", fake_run)
    (tmp_path / "bids").mkdir()
    (tmp_path / "deriv").mkdir()

    client.post(
        "/run_output_check",
        json={"bids_dir": str(tmp_path / "bids"), "derivatives_dir": str(tmp_path / "deriv")},
    )

    assert calls and calls[0][0] == "srun"


def test_get_app_help_apptainer_runs_through_srun(client, tmp_path, monkeypatch, on_login_node):
    sif = tmp_path / "mriqc_24.0.2.sif"
    sif.write_text("fake")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return _FakeCompletedProcess(stdout="usage: mriqc\n")

    monkeypatch.setattr(gui_misc_routes.subprocess, "run", fake_run)
    monkeypatch.setattr(gui_misc_routes.shutil, "which", lambda name: f"/usr/bin/{name}")

    client.post("/get_app_help", json={"container": str(sif), "container_engine": "apptainer"})

    runs = [c for c in calls if "--help" in c or "run" in c or "exec" in c]
    assert runs and all(c[0] == "srun" for c in runs), calls


def test_the_annex_slurm_marker_is_not_counted_as_uncommitted_output(tmp_path):
    # 134 carries an untracked .annex-slurm-managed marker by design; counting
    # it would make "can reclaim local storage" permanently false there.
    work = _make_synced_repo(tmp_path)
    (work / ".annex-slurm-managed").write_text("marker\n")

    result = gui_cohort_routes._git_sync_status(str(work))

    assert result["uncommitted"] == 0
    assert result["ok"] is True
