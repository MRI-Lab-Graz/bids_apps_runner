import json

import pytest

import prism_app_runner
from gui.gui_projects import ProjectStore


@pytest.fixture
def store(tmp_path):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    return ProjectStore(
        projects_dir,
        machine_settings_provider=prism_app_runner._get_effective_machine_settings,
        config_normalizer=prism_app_runner._coerce_project_config_shape,
        project_dir_resolver=lambda project_id: prism_app_runner._resolve_project_dir(
            projects_dir, project_id
        ),
        timestamp_factory=lambda: "2026-05-28T00:00:00",
    )


def _read_config(store, project_id):
    project_json_path = store.project_dir_resolver(project_id) / "project.json"
    with open(project_json_path, "r", encoding="utf-8") as f:
        return json.load(f)["config"]


def test_save_without_hpc_key_preserves_existing_hpc(store):
    # Mirrors handleSaveAndRun()'s real payload shape (templates/index.html:
    # 4777-4791): common/app/pipelines/active_pipeline always travel together
    # in sync (pipelines[active].common/app is authoritative in
    # _coerce_project_config_shape), but "hpc" is never included at all.
    project_id, _ = store.create_project("proj")

    def _payload(bids_folder):
        common = {"bids_folder": bids_folder}
        app = {"analysis_level": "participant", "options": [], "mounts": []}
        return {
            "common": common,
            "app": app,
            "pipelines": {"default": {"name": "Default Pipeline", "common": common, "app": app}},
            "active_pipeline": "default",
        }

    ok = store.save_project(
        project_id,
        {**_payload("/data/bids"), "hpc": {"partition": "gpu", "time": "06:00:00", "mem": "24G", "cpus": 8}},
    )
    assert ok

    # Later save omits "hpc" entirely -- exactly what handleSaveAndRun() sends.
    ok = store.save_project(project_id, _payload("/data/bids2"))
    assert ok

    config = _read_config(store, project_id)
    assert config["common"]["bids_folder"] == "/data/bids2"
    assert config["hpc"] == {
        "partition": "gpu",
        "time": "06:00:00",
        "mem": "24G",
        "cpus": 8,
    }


def test_save_hpc_only_preserves_common_app_and_pipelines(store):
    project_id, seeded = store.create_project("proj2")
    seeded_common = seeded["config"]["common"]
    seeded_app = seeded["config"]["app"]
    ok = store.save_project(
        project_id,
        {
            "common": seeded_common,
            "app": seeded_app,
            "pipelines": seeded["config"]["pipelines"],
            "active_pipeline": seeded["config"]["active_pipeline"],
        },
    )
    assert ok

    # HPC tab's own save -- only sends {hpc: ...}, like saveHPCSettings() did
    # before it started defensively cloning currentProjectConfig client-side.
    ok = store.save_project(project_id, {"hpc": {"partition": "hpc", "time": "01:00:00"}})
    assert ok

    config = _read_config(store, project_id)
    assert config["hpc"] == {"partition": "hpc", "time": "01:00:00"}
    assert config["common"] == seeded_common
    assert config["app"] == seeded_app
    assert config["pipelines"]


def test_explicit_empty_hpc_still_overwrites(store):
    project_id, _ = store.create_project("proj3")
    store.save_project(project_id, {"hpc": {"partition": "gpu", "time": "06:00:00"}})

    # An explicit {} means "caller wants this cleared", not "leave it alone"
    # -- key-presence-means-replace, not a deep-recursive merge.
    ok = store.save_project(project_id, {"hpc": {}})
    assert ok

    config = _read_config(store, project_id)
    assert config["hpc"] == {}


def test_save_project_unknown_id_returns_false(store):
    assert store.save_project("does-not-exist", {"hpc": {}}) is False


def test_save_project_writes_backup_of_previous_state(store):
    project_id, _ = store.create_project("proj4")
    store.save_project(project_id, {"hpc": {"partition": "gpu"}})
    store.save_project(project_id, {"hpc": {"partition": "hpc"}})

    backup_path = store.project_dir_resolver(project_id) / "project.json.bak"
    assert backup_path.exists()
    with open(backup_path, "r", encoding="utf-8") as f:
        backup = json.load(f)
    # Backup captures state *before* the most recent save.
    assert backup["config"]["hpc"]["partition"] == "gpu"
