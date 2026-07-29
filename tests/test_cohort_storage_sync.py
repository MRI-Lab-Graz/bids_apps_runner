import json
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


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _completeness_stdout(app_name, missing_items):
    return json.dumps(
        {
            "pipelines": {
                app_name: {
                    "pipeline": app_name,
                    "status": "failed" if missing_items else "passed",
                    "missing_items": missing_items,
                    "total_missing": len(missing_items),
                    "stats": {},
                }
            },
            "summary": {
                "total_pipelines": 1,
                "passed": 0 if missing_items else 1,
                "failed": 1 if missing_items else 0,
                "total_missing_items": len(missing_items),
            },
        }
    )


def _make_completeness_fake_run(app_name, missing_items=None, unsupported=False, calls=None):
    """Builds a fake for gui_cohort_routes.subprocess.run that intercepts
    only the check_app_output.py invocation (canned JSON stdout, matching
    -p <app_name>) and passes every other call (git, datalad) straight to
    the real subprocess.run -- same passthrough pattern already used for
    faking `datalad drop` in TestCleanupLocalStorage."""
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if calls is not None:
            calls.append(cmd)
        if cmd and len(cmd) > 1 and "check_app_output.py" in str(cmd[1]):
            if unsupported:
                return _FakeCompletedProcess(
                    returncode=1, stdout="", stderr=f"Error: Unknown pipeline: {app_name}\n"
                )
            return _FakeCompletedProcess(
                returncode=0 if not missing_items else 1,
                stdout=_completeness_stdout(app_name, missing_items or []),
            )
        return real_run(cmd, **kwargs)

    return fake_run


def _save_runnable_project(project_id, tmp_path, output_dir, pipeline_app_name=None):
    bids_dir = tmp_path / "bids"
    bids_dir.mkdir(exist_ok=True)
    container = tmp_path / "container.sif"
    container.write_text("fake")
    common = {
        "bids_folder": str(bids_dir),
        "output_folder": str(output_dir),
        "container": str(container),
        "container_engine": "apptainer",
    }
    if pipeline_app_name:
        common["pipeline_app_name"] = pipeline_app_name
    _save(
        project_id,
        common,
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

    def test_reports_synced(self, client, disposable_project, tmp_path, monkeypatch):
        output_dir = _make_synced_repo(tmp_path)
        _save_runnable_project(disposable_project, tmp_path, output_dir, pipeline_app_name="qsiprep")
        monkeypatch.setattr(
            gui_cohort_routes.subprocess,
            "run",
            _make_completeness_fake_run("qsiprep", missing_items=[]),
        )

        resp = client.get(f"/cohort/check_storage_sync?project_id={disposable_project}")
        data = resp.get_json()
        assert data["output_cloned"] is True
        assert data["can_reclaim"] is True
        assert data["output_sync"]["ok"] is True
        assert data["completeness"]["ok"] is True
        assert data["completeness"]["supported"] is True

    def test_reports_dirty(self, client, disposable_project, tmp_path):
        output_dir = _make_synced_repo(tmp_path)
        _dirty(output_dir)
        _save_runnable_project(disposable_project, tmp_path, output_dir)

        resp = client.get(f"/cohort/check_storage_sync?project_id={disposable_project}")
        data = resp.get_json()
        assert data["can_reclaim"] is False
        assert data["output_sync"]["uncommitted"] == 1
        # Completeness check is skipped entirely when git sync already fails
        # -- no point deep-scanning a clone that isn't even pushed yet.
        assert data["completeness"] is None

    def test_reports_incomplete_pipeline_output(self, client, disposable_project, tmp_path, monkeypatch):
        output_dir = _make_synced_repo(tmp_path)
        _save_runnable_project(disposable_project, tmp_path, output_dir, pipeline_app_name="qsiprep")
        missing = ["[ERROR] DWI directory missing for session with DWI data in other sessions:\n    Subject:  sub-01\n    Session:  ses-2"]
        monkeypatch.setattr(
            gui_cohort_routes.subprocess,
            "run",
            _make_completeness_fake_run("qsiprep", missing_items=missing),
        )

        resp = client.get(f"/cohort/check_storage_sync?project_id={disposable_project}")
        data = resp.get_json()
        assert data["output_sync"]["ok"] is True
        assert data["completeness"]["ok"] is False
        assert data["completeness"]["missing_items"] == missing
        assert data["can_reclaim"] is False

    def test_reports_unsupported_pipeline(self, client, disposable_project, tmp_path, monkeypatch):
        output_dir = _make_synced_repo(tmp_path)
        _save_runnable_project(disposable_project, tmp_path, output_dir, pipeline_app_name="custom_app")
        monkeypatch.setattr(
            gui_cohort_routes.subprocess,
            "run",
            _make_completeness_fake_run("custom_app", unsupported=True),
        )

        resp = client.get(f"/cohort/check_storage_sync?project_id={disposable_project}")
        data = resp.get_json()
        assert data["completeness"]["ok"] is False
        assert data["completeness"]["supported"] is False
        assert data["can_reclaim"] is False


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

        # This project never sets pipeline_app_name, so it defaults to
        # "bids_app" -- not a real checker (see PIPELINE_CHECKERS), i.e. the
        # completeness gate can't verify it and requires an explicit
        # override, same as any other unsupported/custom pipeline.
        resp = client.post(
            "/cohort/cleanup_local_storage",
            json={"project_id": disposable_project, "force_unverified": True},
        )
        data = resp.get_json()
        assert data["ok"] is True
        assert len(calls) == 2
        paths_called = {c["cwd"] for c in calls}
        assert paths_called == {str(input_dir), str(output_dir)}
        for c in calls:
            assert c["cmd"] == ["datalad", "drop", "-d", c["cwd"], "-r", "."]

    def test_refuses_when_pipeline_output_incomplete(self, client, disposable_project, tmp_path, monkeypatch):
        output_dir = _make_synced_repo(tmp_path)
        _save_runnable_project(disposable_project, tmp_path, output_dir, pipeline_app_name="qsiprep")

        datalad_calls = []
        fake_completeness = _make_completeness_fake_run(
            "qsiprep", missing_items=["[ERROR] something missing"], calls=datalad_calls
        )
        monkeypatch.setattr(gui_cohort_routes.subprocess, "run", fake_completeness)

        resp = client.post(
            "/cohort/cleanup_local_storage",
            json={"project_id": disposable_project},
        )
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["ok"] is False
        assert data["completeness"]["supported"] is True
        assert not any(c[0] == "datalad" for c in datalad_calls if c)

    def test_allows_pipeline_output_incomplete_with_force(self, client, disposable_project, tmp_path, monkeypatch):
        """An operator who knows *why* the pipeline is incomplete (e.g.
        known-bad/excluded subjects) can override the completeness gate
        with force_incomplete_output -- distinct from force_unverified,
        which is for pipelines with no checker at all."""
        output_dir = _make_synced_repo(tmp_path, name="output")
        input_dir = _make_synced_repo(tmp_path, name="input")
        _save(
            disposable_project,
            {
                "bids_folder": str(input_dir),
                "output_folder": str(output_dir),
                "container": str(tmp_path / "container.sif"),
                "container_engine": "apptainer",
                "pipeline_app_name": "qsiprep",
            },
            {"analysis_level": "participant", "options": [], "mounts": []},
            hpc={"partition": "hpc", "time": "06:00:00", "mem": "8G", "cpus": 2},
        )
        (tmp_path / "container.sif").write_text("fake")

        datalad_calls = []
        real_run = subprocess.run

        def fake_run(cmd, **kwargs):
            if cmd and cmd[0] == "datalad":
                datalad_calls.append(cmd)
                return _FakeCompletedProcess(returncode=0, stdout="dropped")
            if cmd and len(cmd) > 1 and "check_app_output.py" in str(cmd[1]):
                return _FakeCompletedProcess(
                    returncode=1,
                    stdout=_completeness_stdout("qsiprep", ["[ERROR] sub-99 excluded (motion artefact)"]),
                )
            return real_run(cmd, **kwargs)

        monkeypatch.setattr(gui_cohort_routes.subprocess, "run", fake_run)

        resp = client.post(
            "/cohort/cleanup_local_storage",
            json={"project_id": disposable_project, "force_incomplete_output": True},
        )
        data = resp.get_json()
        assert data["ok"] is True
        assert len(datalad_calls) == 2

    def test_refuses_unsupported_pipeline_without_force(self, client, disposable_project, tmp_path, monkeypatch):
        output_dir = _make_synced_repo(tmp_path)
        _save_runnable_project(disposable_project, tmp_path, output_dir, pipeline_app_name="custom_app")
        monkeypatch.setattr(
            gui_cohort_routes.subprocess,
            "run",
            _make_completeness_fake_run("custom_app", unsupported=True),
        )

        resp = client.post(
            "/cohort/cleanup_local_storage",
            json={"project_id": disposable_project},
        )
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["ok"] is False
        assert data["completeness"]["supported"] is False

    def test_allows_unsupported_pipeline_with_force_unverified(self, client, disposable_project, tmp_path, monkeypatch):
        output_dir = _make_synced_repo(tmp_path, name="output")
        input_dir = _make_synced_repo(tmp_path, name="input")
        _save(
            disposable_project,
            {
                "bids_folder": str(input_dir),
                "output_folder": str(output_dir),
                "container": str(tmp_path / "container.sif"),
                "container_engine": "apptainer",
                "pipeline_app_name": "custom_app",
            },
            {"analysis_level": "participant", "options": [], "mounts": []},
            hpc={"partition": "hpc", "time": "06:00:00", "mem": "8G", "cpus": 2},
        )
        (tmp_path / "container.sif").write_text("fake")

        real_run = subprocess.run

        def fake_run(cmd, **kwargs):
            if cmd and cmd[0] == "datalad":
                return _FakeCompletedProcess(returncode=0, stdout="dropped")
            if cmd and len(cmd) > 1 and "check_app_output.py" in str(cmd[1]):
                return _FakeCompletedProcess(returncode=1, stderr="Error: Unknown pipeline: custom_app\n")
            return real_run(cmd, **kwargs)

        monkeypatch.setattr(gui_cohort_routes.subprocess, "run", fake_run)

        resp = client.post(
            "/cohort/cleanup_local_storage",
            json={"project_id": disposable_project, "force_unverified": True},
        )
        data = resp.get_json()
        assert data["ok"] is True

    def test_force_flags_do_not_bypass_unsynced_output(self, client, disposable_project, tmp_path):
        """Neither override flag touches the git-sync precondition -- there's
        no "this is expected" story for local-only content that never
        reached the datalad server, unlike pipeline completeness."""
        output_dir = _make_synced_repo(tmp_path)
        _dirty(output_dir)
        _save_runnable_project(disposable_project, tmp_path, output_dir, pipeline_app_name="qsiprep")

        resp = client.post(
            "/cohort/cleanup_local_storage",
            json={
                "project_id": disposable_project,
                "force_unverified": True,
                "force_incomplete_output": True,
            },
        )
        assert resp.status_code == 409
        data = resp.get_json()
        assert data["ok"] is False
        assert "not fully synced" in data["error"]
