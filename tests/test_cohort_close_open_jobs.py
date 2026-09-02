import shutil
import subprocess

import pytest

import prism_app_runner
import gui.gui_cohort_routes as gui_cohort_routes


@pytest.fixture
def client():
    prism_app_runner.app.config["TESTING"] = True
    with prism_app_runner.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def disposable_project():
    project_id, _ = prism_app_runner.ProjectManager.create_project(
        "pytest_cohort_close_open_jobs_tmp"
    )
    yield project_id
    project_dir = prism_app_runner._resolve_project_dir(
        prism_app_runner.PROJECTS_DIR, project_id
    )
    shutil.rmtree(project_dir, ignore_errors=True)


def _save_runnable_project(project_id, tmp_path, output_dir):
    bids_dir = tmp_path / "bids"
    bids_dir.mkdir(exist_ok=True)
    container = tmp_path / "container.sif"
    container.write_text("fake")
    prism_app_runner.ProjectManager.save_project(
        project_id,
        {
            "common": {
                "bids_folder": str(bids_dir),
                "output_folder": str(output_dir),
                "container": str(container),
                "container_engine": "apptainer",
            },
            "app": {"analysis_level": "participant", "options": [], "mounts": []},
            "pipelines": {
                "default": {
                    "name": "default",
                    "common": {
                        "bids_folder": str(bids_dir),
                        "output_folder": str(output_dir),
                        "container": str(container),
                        "container_engine": "apptainer",
                    },
                    "app": {"analysis_level": "participant", "options": [], "mounts": []},
                }
            },
            "active_pipeline": "default",
            "hpc": {"partition": "hpc", "time": "06:00:00", "mem": "8G", "cpus": 2},
        },
    )


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestCloseOpenJobs:
    def test_scopes_each_close_to_its_own_job_id(self, client, disposable_project, tmp_path, monkeypatch):
        """Regression test for the stale-sweep bug (134_subregions incident):
        closing must never call `slurm-finish --commit-failed-jobs` without
        --slurm-job-id, since that processes every open job in the DB, not
        just the ones the operator asked to close."""
        output_dir = tmp_path / "output"
        (output_dir / ".datalad").mkdir(parents=True)
        _save_runnable_project(disposable_project, tmp_path, output_dir)

        list_open_jobs_stdout = (
            "The following jobs are open:\n\n"
            "slurm-job-id   slurm-job-status\n"
            "111            FAILED\n"
            "222            CANCELLED\n"
            "333            RUNNING\n"
        )
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "--list-open-jobs" in cmd:
                return _FakeCompletedProcess(returncode=0, stdout=list_open_jobs_stdout)
            return _FakeCompletedProcess(returncode=0, stdout="ok")

        monkeypatch.setattr(gui_cohort_routes.subprocess, "run", fake_run)

        resp = client.post(
            "/cohort/close_open_jobs",
            json={"project_id": disposable_project, "pipeline_id": "default"},
        )
        data = resp.get_json()

        assert data["ok"] is True
        close_calls = [c for c in calls if "--commit-failed-jobs" in c]
        assert len(close_calls) == 2
        for c in close_calls:
            assert "--slurm-job-id" in c
            # --close-failed-jobs must not also be passed: --commit-failed-jobs
            # already implies it (datalad_slurm/finish.py), and passing both
            # is redundant, not a correctness issue -- just keep the call
            # surface exactly what's documented.
        closed_ids = {c[c.index("--slurm-job-id") + 1] for c in close_calls}
        assert closed_ids == {"111", "222"}
        assert not any("333" in c for c in close_calls)

    def test_no_closeable_jobs_short_circuits(self, client, disposable_project, tmp_path, monkeypatch):
        output_dir = tmp_path / "output"
        (output_dir / ".datalad").mkdir(parents=True)
        _save_runnable_project(disposable_project, tmp_path, output_dir)

        def fake_run(cmd, **kwargs):
            assert "--list-open-jobs" in cmd
            return _FakeCompletedProcess(returncode=0, stdout="No open jobs.\n")

        monkeypatch.setattr(gui_cohort_routes.subprocess, "run", fake_run)

        resp = client.post(
            "/cohort/close_open_jobs",
            json={"project_id": disposable_project, "pipeline_id": "default"},
        )
        data = resp.get_json()
        assert data["ok"] is True
        assert "No closeable" in data["output"]

    def test_stops_after_time_budget_and_reports_remaining(
        self, client, disposable_project, tmp_path, monkeypatch
    ):
        """Regression test: this loop calls --commit-failed-jobs once per
        stale job with a 60s subprocess timeout each, serially, inside a
        synchronous Flask request handler. With enough stale jobs that adds
        up to minutes inside one HTTP request -- ties up one of only a
        handful of GUI worker threads, and risks a reverse-proxy or browser
        timing the request out mid-slurm-finish, which is exactly the
        interrupted-mid-finish failure mode this whole fix exists to avoid,
        just reintroduced via the HTTP layer instead of SLURM wallclock.
        A wall-clock budget must cap the loop and report what's left rather
        than attempting every stale job no matter how many there are."""
        output_dir = tmp_path / "output"
        (output_dir / ".datalad").mkdir(parents=True)
        _save_runnable_project(disposable_project, tmp_path, output_dir)

        list_open_jobs_stdout = (
            "slurm-job-id   slurm-job-status\n"
            "111            FAILED\n"
            "222            FAILED\n"
            "333            FAILED\n"
        )
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if "--list-open-jobs" in cmd:
                return _FakeCompletedProcess(returncode=0, stdout=list_open_jobs_stdout)
            return _FakeCompletedProcess(returncode=0, stdout="ok")

        monkeypatch.setattr(gui_cohort_routes.subprocess, "run", fake_run)

        # First value is the loop's start timestamp; the jump to 500 on the
        # very next read simulates the budget having elapsed before a
        # second job could be attempted.
        monotonic_values = iter([0.0, 0.0, 500.0, 500.0, 500.0, 500.0])
        monkeypatch.setattr(
            gui_cohort_routes.time, "monotonic", lambda: next(monotonic_values, 500.0)
        )

        resp = client.post(
            "/cohort/close_open_jobs",
            json={"project_id": disposable_project, "pipeline_id": "default"},
        )
        data = resp.get_json()

        close_calls = [c for c in calls if "--commit-failed-jobs" in c]
        assert len(close_calls) == 1, "must stop after the budget instead of attempting every job"
        assert data["ok"] is False
        assert "2" in data["output"] and "re-run" in data["output"].lower()

    def test_one_bad_job_does_not_block_the_others(self, client, disposable_project, tmp_path, monkeypatch):
        """A legacy stale entry that still fails to close on its own scoped
        call must not prevent other, unrelated jobs from being closed."""
        output_dir = tmp_path / "output"
        (output_dir / ".datalad").mkdir(parents=True)
        _save_runnable_project(disposable_project, tmp_path, output_dir)

        list_open_jobs_stdout = (
            "slurm-job-id   slurm-job-status\n"
            "111            FAILED\n"
            "222            FAILED\n"
        )

        def fake_run(cmd, **kwargs):
            if "--list-open-jobs" in cmd:
                return _FakeCompletedProcess(returncode=0, stdout=list_open_jobs_stdout)
            if "111" in cmd:
                return _FakeCompletedProcess(returncode=1, stdout="", stderr="boom")
            return _FakeCompletedProcess(returncode=0, stdout="ok")

        monkeypatch.setattr(gui_cohort_routes.subprocess, "run", fake_run)

        resp = client.post(
            "/cohort/close_open_jobs",
            json={"project_id": disposable_project, "pipeline_id": "default"},
        )
        data = resp.get_json()
        assert data["ok"] is False
        assert "job 111 (FAILED)" in data["output"]
        assert "job 222 (ok)" in data["output"]
