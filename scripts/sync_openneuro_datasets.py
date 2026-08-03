#!/usr/bin/env python3
"""Sync a mega-study cohort config's `datasets[]` from the actual OpenNeuro
folder structure on the DataLad server, instead of hand-maintaining the list.

Usage:
    scripts/sync_openneuro_datasets.py -c configs/megastudy_openneuro_mriqc.json
    scripts/sync_openneuro_datasets.py \\
        -c configs/megastudy_openneuro_mriqc.json \\
        -c configs/megastudy_openneuro_fmriprep.json --dry-run

Only newly-seen dataset IDs are appended, as plain strings. Existing entries
-- including the ``{"id": ..., "options_extra": [...]}`` override form used
for datasets needing non-default fmriprep flags -- are left untouched.
Dataset IDs no longer present on the server are kept unless ``--prune`` is
given, since a config entry (especially an override) reflects a deliberate
decision that shouldn't be silently dropped just because a listing failed
or a dataset is temporarily offline.
"""
import argparse
import json
import re
from pathlib import Path
from typing import List

import prism_datalad

_DATASET_ID_RE = re.compile(r"^ds\d{6}$")


def list_remote_dataset_ids(ssh_host: str, remote_path: str) -> List[str]:
    """List OpenNeuro dataset IDs (``ds######`` subfolders) on the server.

    Goes through the same ``prism_datalad.list_remote_directory_names``
    connection the GUI's remote-dataset browsing uses
    (``gui/gui_utility_routes.py``'s ``/list_remote_studies``), rather than a
    separate ad-hoc SSH call, and filters to the strict OpenNeuro accession
    pattern so any stray non-dataset entries under the remote path aren't
    picked up.
    """
    names = prism_datalad.list_remote_directory_names(ssh_host, remote_path)
    return sorted(name for name in names if _DATASET_ID_RE.match(name))


def existing_dataset_ids(datasets: list) -> set:
    return {
        entry.get("id") if isinstance(entry, dict) else entry for entry in datasets
    }


def merge_dataset_ids(
    datasets: list, remote_ids: List[str], prune: bool = False
) -> list:
    """Add newly-seen dataset IDs and optionally drop ones no longer remote.

    Existing entries (plain strings or override objects) are otherwise left
    untouched and in their original order; new IDs are appended as plain
    strings in the order they were listed remotely.
    """
    current_ids = existing_dataset_ids(datasets)
    remote_id_set = set(remote_ids)

    merged = list(datasets)
    if prune:
        merged = [
            entry
            for entry in merged
            if (entry.get("id") if isinstance(entry, dict) else entry)
            in remote_id_set
        ]

    for ds_id in remote_ids:
        if ds_id not in current_ids:
            merged.append(ds_id)

    return merged


def sync_config(
    config_path: str, ssh_host: str, remote_path: str, prune: bool, dry_run: bool
) -> None:
    path = Path(config_path)
    config = json.loads(path.read_text())
    original = config.get("datasets", [])

    remote_ids = list_remote_dataset_ids(ssh_host, remote_path)
    updated = merge_dataset_ids(original, remote_ids, prune=prune)

    added = [entry for entry in updated if entry not in original]
    removed = [entry for entry in original if entry not in updated]

    action = "Would update" if dry_run else "Updated"
    print(
        f"{action} {config_path}: +{len(added)} added, -{len(removed)} removed, "
        f"{len(updated)} total"
    )
    if added:
        print(f"  added:   {added}")
    if removed:
        print(f"  removed: {removed}")

    if not dry_run:
        config["datasets"] = updated
        path.write_text(json.dumps(config, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync a mega-study cohort config's datasets[] from the "
        "OpenNeuro folder structure on the DataLad server."
    )
    parser.add_argument(
        "-c",
        "--config",
        action="append",
        required=True,
        help="Path to a cohort config JSON file (repeatable)",
    )
    parser.add_argument(
        "--ssh-host",
        default="datalad-server",
        help="SSH alias for the DataLad server (see gui/gui_utility_routes.py)",
    )
    parser.add_argument(
        "--remote-path",
        default="/datalad/mri/openneuro",
        help="Remote directory whose subfolders are OpenNeuro dataset IDs",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Remove dataset IDs no longer present on the server (off by default)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    for config_path in args.config:
        sync_config(config_path, args.ssh_host, args.remote_path, args.prune, args.dry_run)


if __name__ == "__main__":
    main()
