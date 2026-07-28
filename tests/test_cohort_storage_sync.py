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
    """A real project under the actual projects/ dir -- gui_cohort_routes.py
    binds load_project=ProjectManager.load_project at import time (not a
    lazy getter like gui_run_routes.py's project_manager_getter), so it
    can't be redirected to an isolated store via monkeypatch; exercise it
    against the real ProjectManager instead and clean up afterward."""
    project_id, _ = prism_app_runner.ProjectManager.create_project(
        "pytest_cohort_storage_sync_tmp"
    )
    yield project_id
    project_dir = prism_app_runner._resolve_project_dir(
        prism_app_runner.PROJECTS_DIR, project_id
    )
    shutil.rmtree(project_dir, ignore_errors=True)


def _save(project_id, common, app, hpc=None, pipeline_id="default"):
    prism_app_runner.ProjectManager.save_project(
        project_id,
        {
            "common": common,
            "app": app,
            "pipelines": {pipeline_id: {"name": pipeline_id, "common": common, "app": app}},
            "active_pipeline": pipeline_id,
            "hpc": hpc or {},
        },
    )


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo)] + list(args), check=True, capture_output=True)


def _make_synced_repo(tmp_path, name="output"):
    """A working repo on branch 'main', with a bare 'origin' remote, one
    commit pushed -- i.e. fully synced."""
    origin = tmp_path / f"{name}_origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "-b", "main")

    work = tmp_path / name
    work.mkdir()
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    (work / "README").write_text("hello\n")
    _git(work, "add", "README")
    _git(work, "commit", "-m", "initial")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-u", "origin", "main")
    return work


def _dirty(work):
    (work / "untracked.txt").write_text("stray\n")


def _unpushed_commit(work):
    (work / "more.txt").write_text("more\n")
    _git(work, "add", "more.txt")
    _git(work, "commit", "-m", "local only")


class TestGitSyncStatus:
    def test_not_a_git_repo(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        result = gui_cohort_routes._git_sync_status(str(plain))
        assert result["ok"] is False
        assert "Not a git repository" in result["error"]

    def test_clean_and_pushed(self, tmp_path):
        work = _make_synced_repo(tmp_path)
        result = gui_cohort_routes._git_sync_status(str(work))
        assert result == {
            "ok": True,
            "branch": "main",
            "uncommitted": 0,
            "unpushed": 0,
            "error": None,
        }

    def test_uncommitted_changes(self, tmp_path):
        work = _make_synced_repo(tmp_path)
        _dirty(work)
        result = gui_cohort_routes._git_sync_status(str(work))
        assert result["ok"] is False
        assert result["uncommitted"] == 1
        assert result["unpushed"] == 0

    def test_unpushed_commit(self, tmp_path):
        work = _make_synced_repo(tmp_path)
        _unpushed_commit(work)
        result = gui_cohort_routes._git_sync_status(str(work))
        assert result["ok"] is False
        assert result["uncommitted"] == 0
        assert result["unpushed"] == 1


def _save_runnable_project(project_id, tmp_path, output_dir):
    bids_dir = tmp_path / "bids"
    bids_dir.mkdir(exist_ok=True)
    container = tmp_path / "container.sif"
    container.write_text("fake")
    _save(
        project_id,
        {
            "bids_folder": str(bids_dir),
            "output_folder": str(output_dir),
            "container": str(container),
            "container_engine": "apptainer",
        },
        {"analysis_level": "participant", "options": [], "mounts": []},
        hpc={"partition": "hpc", "time": "06:00:00", "mem": "8G", "cpus": 2},
    )


class TestCheckStorageSync:
    def test_missing_project_id(self, client):
        resp = client.get("/cohort/check_storage_sync")
        assert resp.status_code == 400

    def test_output_not_cloned(self, client, disposable_project, tmp_path):
        output_dir = tmp_path / "derivatives"
        _save_runnable_project(disposable_project, tmp_path, output_dir)

        resp = client.get(f"/cohort/check_storage_sync?project_id={disposable_project}")
        data = resp.get_json()
        assert data["output_cloned"] is False
        assert data["can_reclaim"] is False

    def test_reports_synced(self, client, disposable_project, tmp_path):
        output_dir = _make_synced_repo(tmp_path)
        _save_runnable_project(disposable_project, tmp_path, output_dir)

        resp = client.get(f"/cohort/check_storage_sync?project_id={disposable_project}")
        data = resp.get_json()
        assert data["output_cloned"] is True
        assert data["can_reclaim"] is True
        assert data["output_sync"]["ok"] is True

    def test_reports_dirty(self, client, disposable_project, tmp_path):
        output_dir = _make_synced_repo(tmp_path)
        _dirty(output_dir)
        _save_runnable_project(disposable_project, tmp_path, output_dir)

        resp = client.get(f"/cohort/check_storage_sync?project_id={disposable_project}")
        data = resp.get_json()
        assert data["can_reclaim"] is False
        assert data["output_sync"]["uncommitted"] == 1


class TestCleanupLocalStorage:
    def test_refuses_when_not_synced(self, client, disposable_project, tmp_path):
        output_dir = _make_synced_repo(tmp_path)
        _dirty(output_dir)
        _save_runnable_project(disposable_project, tmp_path, output_dir)

        resp = client.post(
            "/cohort/cleanup_local_storage",
            json={"project_id": disposable_project},
        )
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["ok"] is False

    def test_calls_datalad_drop_for_both_clones(self, client, disposable_project, tmp_path, monkeypatch):
        output_dir = _make_synced_repo(tmp_path, name="output")
        input_dir = _make_synced_repo(tmp_path, name="input")
        _save(
            disposable_project,
            {
                "bids_folder": str(input_dir),
                "output_folder": str(output_dir),
                "container": str(tmp_path / "container.sif"),
                "container_engine": "apptainer",
            },
            {"analysis_level": "participant", "options": [], "mounts": []},
            hpc={"partition": "hpc", "time": "06:00:00", "mem": "8G", "cpus": 2},
        )
        (tmp_path / "container.sif").write_text("fake")

        calls = []
        real_run = subprocess.run

        def fake_run(cmd, **kwargs):
            if cmd and cmd[0] == "datalad":
                calls.append({"cmd": cmd, "cwd": kwargs.get("cwd")})

                class FakeProc:
                    returncode = 0
                    stdout = "dropped"
                    stderr = ""

                return FakeProc()
            # Pass real git calls (the internal sync-status re-check) through
            # unmodified -- only datalad invocations are faked here.
            return real_run(cmd, **kwargs)

        monkeypatch.setattr(gui_cohort_routes.subprocess, "run", fake_run)

        resp = client.post(
            "/cohort/cleanup_local_storage",
            json={"project_id": disposable_project},
        )
        data = resp.get_json()
        assert data["ok"] is True
        assert len(calls) == 2
        paths_called = {c["cwd"] for c in calls}
        assert paths_called == {str(input_dir), str(output_dir)}
        for c in calls:
            assert c["cmd"] == ["datalad", "drop", "-d", c["cwd"], "-r", "."]
