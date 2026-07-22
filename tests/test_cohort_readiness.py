import shutil

import pytest

import prism_app_runner


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
        "pytest_cohort_readiness_tmp"
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


def test_cohort_readiness_reports_missing_project_id(client):
    resp = client.get("/cohort/readiness")
    data = resp.get_json()
    assert data["ready"] is False
    assert any(c["id"] == "project" and not c["ok"] for c in data["checks"])


def test_cohort_readiness_flags_missing_hpc_and_paths(client, disposable_project):
    _save(disposable_project, {"container_engine": "apptainer"}, {"analysis_level": "participant", "options": [], "mounts": []})

    resp = client.get(f"/cohort/readiness?project_id={disposable_project}")
    data = resp.get_json()

    assert data["ready"] is False
    by_id = {c["id"]: c for c in data["checks"]}
    assert by_id["bids_folder"]["ok"] is False
    assert by_id["output_folder"]["ok"] is False
    assert by_id["hpc_partition"]["ok"] is False
    assert by_id["hpc_time"]["ok"] is False
    assert by_id["hpc_mem"]["ok"] is False
    assert by_id["hpc_cpus"]["ok"] is False
    # Config-derivation-dependent checks (container existence, GPU
    # feasibility) shouldn't even attempt to run when the basics are missing.
    assert "container" not in by_id


def test_cohort_readiness_rejects_docker_engine(client, disposable_project):
    _save(
        disposable_project,
        {"bids_folder": "/tmp/bids", "output_folder": "/tmp/out", "container_engine": "docker"},
        {"analysis_level": "participant", "options": [], "mounts": []},
        hpc={"partition": "gpu", "time": "06:00:00", "mem": "24G", "cpus": 8},
    )

    resp = client.get(f"/cohort/readiness?project_id={disposable_project}")
    data = resp.get_json()

    by_id = {c["id"]: c for c in data["checks"]}
    assert by_id["container_engine"]["ok"] is False


def test_cohort_readiness_passes_and_surfaces_execution_adapter(client, disposable_project, tmp_path):
    bids_dir = tmp_path / "bids"
    bids_dir.mkdir()
    container = tmp_path / "fastsurfer_bids_cuda-v2.5.4.sif"
    container.write_text("fake")

    _save(
        disposable_project,
        {
            "bids_folder": str(bids_dir),
            "output_folder": str(tmp_path / "derivatives"),
            "container": str(container),
            "container_engine": "apptainer",
        },
        {
            "analysis_level": "participant",
            "options": [],
            "mounts": [],
            "execution_adapter": "fastsurfer-bids",
        },
        hpc={"partition": "gpu", "time": "06:00:00", "mem": "24G", "cpus": 8},
    )

    resp = client.get(f"/cohort/readiness?project_id={disposable_project}")
    data = resp.get_json()

    by_id = {c["id"]: c for c in data["checks"]}
    assert by_id["container"]["ok"] is True
    assert "execution_adapter" in by_id
    assert "fastsurfer-bids" in by_id["execution_adapter"]["detail"]
    assert data["ready"] is True


def test_cohort_readiness_passes_and_surfaces_freesurfer_bids_adapter(client, disposable_project, tmp_path):
    bids_dir = tmp_path / "bids"
    bids_dir.mkdir()
    container = tmp_path / "freesurfer_bids_8.2.0.sif"
    container.write_text("fake")

    _save(
        disposable_project,
        {
            "bids_folder": str(bids_dir),
            "output_folder": str(tmp_path / "derivatives"),
            "container": str(container),
            "container_engine": "apptainer",
        },
        {
            "analysis_level": "participant",
            "options": [],
            "mounts": [],
            "execution_adapter": "freesurfer-bids",
        },
        hpc={"partition": "hpc", "time": "20:00:00", "mem": "32G", "cpus": 1},
    )

    resp = client.get(f"/cohort/readiness?project_id={disposable_project}")
    data = resp.get_json()

    by_id = {c["id"]: c for c in data["checks"]}
    assert by_id["container"]["ok"] is True
    assert "execution_adapter" in by_id
    assert "freesurfer-bids" in by_id["execution_adapter"]["detail"]
    assert data["ready"] is True
