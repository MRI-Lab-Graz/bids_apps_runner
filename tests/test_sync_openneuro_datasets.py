import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sync_openneuro_datasets as sync_mod


def test_merge_dataset_ids_adds_new_plain_ids():
    datasets = ["ds000031"]
    remote_ids = ["ds000031", "ds000256", "ds002156"]

    merged = sync_mod.merge_dataset_ids(datasets, remote_ids)

    assert merged == ["ds000031", "ds000256", "ds002156"]


def test_merge_dataset_ids_preserves_existing_override_objects():
    datasets = [
        "ds000031",
        {"id": "ds000256", "options_extra": ["--use-syn-sdc"]},
    ]
    remote_ids = ["ds000031", "ds000256", "ds002156"]

    merged = sync_mod.merge_dataset_ids(datasets, remote_ids)

    assert {"id": "ds000256", "options_extra": ["--use-syn-sdc"]} in merged
    assert "ds002156" in merged
    assert merged.count("ds000256") == 0  # not duplicated as a plain string


def test_merge_dataset_ids_prune_removes_missing():
    datasets = ["ds000031", "ds999999"]
    remote_ids = ["ds000031"]

    merged = sync_mod.merge_dataset_ids(datasets, remote_ids, prune=True)

    assert merged == ["ds000031"]


def test_merge_dataset_ids_without_prune_keeps_missing():
    datasets = ["ds000031", "ds999999"]
    remote_ids = ["ds000031"]

    merged = sync_mod.merge_dataset_ids(datasets, remote_ids, prune=False)

    assert "ds999999" in merged


def test_list_remote_dataset_ids_filters_to_accession_pattern(monkeypatch):
    def fake_list_remote_directory_names(ssh_host, remote_path):
        assert ssh_host == "datalad-server"
        assert remote_path == "/datalad/mri/openneuro"
        return ["ds000031", "ds002372", "lost+found", "openneuro-crawler", ".git"]

    monkeypatch.setattr(
        sync_mod.prism_datalad,
        "list_remote_directory_names",
        fake_list_remote_directory_names,
    )

    ids = sync_mod.list_remote_dataset_ids("datalad-server", "/datalad/mri/openneuro")

    assert ids == ["ds000031", "ds002372"]


def test_sync_config_writes_merged_datasets(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"datasets": ["ds000031"], "other": "kept"}))

    monkeypatch.setattr(
        sync_mod,
        "list_remote_dataset_ids",
        lambda ssh_host, remote_path: ["ds000031", "ds000256"],
    )

    sync_mod.sync_config(
        str(config_path), "datalad-server", "/datalad/mri/openneuro", prune=False, dry_run=False
    )

    updated = json.loads(config_path.read_text())
    assert updated["datasets"] == ["ds000031", "ds000256"]
    assert updated["other"] == "kept"


def test_sync_config_dry_run_does_not_write_file(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    original_text = json.dumps({"datasets": ["ds000031"]})
    config_path.write_text(original_text)

    monkeypatch.setattr(
        sync_mod,
        "list_remote_dataset_ids",
        lambda ssh_host, remote_path: ["ds000031", "ds000256"],
    )

    sync_mod.sync_config(
        str(config_path), "datalad-server", "/datalad/mri/openneuro", prune=False, dry_run=True
    )

    assert config_path.read_text() == original_text
