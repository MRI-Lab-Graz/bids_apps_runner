import pytest

import prism_app_runner
from gui.gui_projects import ProjectStore


@pytest.fixture
def isolated_project_store(tmp_path):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    return ProjectStore(
        projects_dir,
        machine_settings_provider=prism_app_runner._get_effective_machine_settings,
        config_normalizer=prism_app_runner._coerce_project_config_shape,
        project_dir_resolver=lambda project_id: prism_app_runner._resolve_project_dir(
            projects_dir, project_id
        ),
        timestamp_factory=lambda: "2026-07-16T00:00:00",
    )


@pytest.fixture
def client(monkeypatch, isolated_project_store):
    monkeypatch.setattr(prism_app_runner, "GUI_LOGIN_ENABLED", False)
    monkeypatch.setattr(prism_app_runner, "ProjectManager", isolated_project_store)
    prism_app_runner.app.config["TESTING"] = True
    with prism_app_runner.app.test_client() as test_client:
        yield test_client


def _save(store, project_id, common, app, pipeline_id="default"):
    store.save_project(
        project_id,
        {
            "common": common,
            "app": app,
            "pipelines": {pipeline_id: {"name": pipeline_id, "common": common, "app": app}},
            "active_pipeline": pipeline_id,
        },
    )


def test_readiness_reports_missing_project_id(client):
    resp = client.get("/local_run_readiness")
    data = resp.get_json()
    assert data["ready"] is False
    assert any(c["id"] == "project" and not c["ok"] for c in data["checks"])


def test_readiness_flags_missing_bids_folder_and_container(client, isolated_project_store):
    project_id, _ = isolated_project_store.create_project("readiness_test")
    _save(
        isolated_project_store,
        project_id,
        {"bids_folder": "", "container": "", "container_engine": "apptainer"},
        {"analysis_level": "participant", "options": [], "mounts": []},
    )

    resp = client.get(f"/local_run_readiness?project_id={project_id}")
    data = resp.get_json()

    assert data["ready"] is False
    by_id = {c["id"]: c for c in data["checks"]}
    assert by_id["bids_folder"]["ok"] is False
    assert by_id["container"]["ok"] is False


def test_readiness_passes_with_real_paths(client, isolated_project_store, tmp_path):
    bids_dir = tmp_path / "bids"
    bids_dir.mkdir()
    container = tmp_path / "fake.sif"
    container.write_text("fake")

    project_id, _ = isolated_project_store.create_project("readiness_ok")
    _save(
        isolated_project_store,
        project_id,
        {
            "bids_folder": str(bids_dir),
            "output_folder": str(tmp_path / "derivatives"),
            "container": str(container),
            "container_engine": "apptainer",
        },
        {"analysis_level": "participant", "options": [], "mounts": []},
    )

    resp = client.get(f"/local_run_readiness?project_id={project_id}")
    data = resp.get_json()

    assert data["ready"] is True
    by_id = {c["id"]: c for c in data["checks"]}
    assert by_id["bids_folder"]["ok"] is True
    assert by_id["container"]["ok"] is True
    assert by_id["output_folder"]["ok"] is True


def test_readiness_surfaces_execution_adapter_for_fastsurfer_bids(client, isolated_project_store, tmp_path):
    bids_dir = tmp_path / "bids"
    bids_dir.mkdir()
    container = tmp_path / "fastsurfer_bids_cuda-v2.5.4.sif"
    container.write_text("fake")

    project_id, _ = isolated_project_store.create_project("readiness_fastsurfer")
    _save(
        isolated_project_store,
        project_id,
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
    )

    resp = client.get(f"/local_run_readiness?project_id={project_id}")
    data = resp.get_json()

    by_id = {c["id"]: c for c in data["checks"]}
    assert "execution_adapter" in by_id
    assert "fastsurfer-bids" in by_id["execution_adapter"]["detail"]
    assert by_id["execution_adapter"]["blocking"] is False


def test_readiness_surfaces_execution_adapter_for_freesurfer_bids(client, isolated_project_store, tmp_path):
    bids_dir = tmp_path / "bids"
    bids_dir.mkdir()
    container = tmp_path / "freesurfer_bids_8.2.0.sif"
    container.write_text("fake")

    project_id, _ = isolated_project_store.create_project("readiness_freesurfer")
    _save(
        isolated_project_store,
        project_id,
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
    )

    resp = client.get(f"/local_run_readiness?project_id={project_id}")
    data = resp.get_json()

    by_id = {c["id"]: c for c in data["checks"]}
    assert "execution_adapter" in by_id
    assert "freesurfer-bids" in by_id["execution_adapter"]["detail"]
    assert by_id["execution_adapter"]["blocking"] is False


def test_readiness_surfaces_auto_detected_execution_adapter_without_explicit_choice(
    client, isolated_project_store, tmp_path
):
    # Regression coverage for a fastsurfer_bids container selected with no
    # explicit execution_adapter set -- this must surface the auto-detected
    # adapter so it's visible before running, instead of only discoverable
    # via a runtime failure. Prior to app_profiles.resolve_app_name()
    # preferring the longest matching app_key across catalog entries, this
    # sniffed as the wrong "fastsurfer" (cross-sectional) entry and
    # auto-detected "fastsurfer-cross" -- the actually-correct-for-this-
    # filename "fastsurfer-bids" is what should surface now.
    bids_dir = tmp_path / "bids"
    bids_dir.mkdir()
    container = tmp_path / "fastsurfer_bids_cuda-v2.5.4.sif"
    container.write_text("fake")

    project_id, _ = isolated_project_store.create_project("readiness_fastsurfer_default")
    _save(
        isolated_project_store,
        project_id,
        {
            "bids_folder": str(bids_dir),
            "output_folder": str(tmp_path / "derivatives"),
            "container": str(container),
            "container_engine": "apptainer",
        },
        {"analysis_level": "participant", "options": [], "mounts": []},
    )

    resp = client.get(f"/local_run_readiness?project_id={project_id}")
    data = resp.get_json()

    by_id = {c["id"]: c for c in data["checks"]}
    assert by_id["execution_adapter"]["detail"].startswith("Will use 'fastsurfer-bids'")
    assert "auto-detected" in by_id["execution_adapter"]["detail"]
