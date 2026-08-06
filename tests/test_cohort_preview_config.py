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
    """See tests/test_cohort_readiness.py's fixture of the same name for why
    this exercises the real ProjectManager instead of a mock."""
    project_id, _ = prism_app_runner.ProjectManager.create_project(
        "pytest_cohort_preview_config_tmp"
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


def _save_runnable_project(project_id, tmp_path):
    bids_dir = tmp_path / "bids"
    bids_dir.mkdir()
    container = tmp_path / "container.sif"
    container.write_text("fake")
    _save(
        project_id,
        {
            "bids_folder": str(bids_dir),
            "output_folder": str(tmp_path / "derivatives"),
            "container": str(container),
            "container_engine": "apptainer",
        },
        {"analysis_level": "participant", "options": [], "mounts": []},
        hpc={"partition": "hpc", "time": "06:00:00", "mem": "16G", "cpus": 2},
    )


def test_preview_config_defaults_batch_size_to_10(client, disposable_project, tmp_path):
    _save_runnable_project(disposable_project, tmp_path)

    resp = client.get(f"/cohort/preview_config?project_id={disposable_project}")
    data = resp.get_json()

    assert resp.status_code == 200
    assert data["config"]["hpc"]["batch_size"] == 10


def test_preview_config_honors_explicit_batch_size(client, disposable_project, tmp_path):
    _save_runnable_project(disposable_project, tmp_path)

    resp = client.get(
        f"/cohort/preview_config?project_id={disposable_project}&batch_size=15"
    )
    data = resp.get_json()

    assert data["config"]["hpc"]["batch_size"] == 15


def test_preview_config_batch_size_zero_disables_batching(client, disposable_project, tmp_path):
    # 0 is a meaningful, explicit "no batching" value -- must survive, not
    # get treated as falsy-and-defaulted like an absent value would.
    _save_runnable_project(disposable_project, tmp_path)

    resp = client.get(
        f"/cohort/preview_config?project_id={disposable_project}&batch_size=0"
    )
    data = resp.get_json()

    assert data["config"]["hpc"]["batch_size"] == 0
