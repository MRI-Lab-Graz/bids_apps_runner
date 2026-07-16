#!/usr/bin/env python3
"""
Project HPC Settings Audit

Read-only report of which projects/*/project.json files are missing an
"hpc" section, or have one with empty partition/time/mem/cpus fields.

Written as a one-time check after fixing ProjectStore.save_project() to
merge rather than replace on save (see gui/gui_projects.py): before that
fix, saving from the Projects tab or the Run App tab's "Save & Start
Runner" button silently wiped any previously-saved HPC settings, since
that payload never included an "hpc" key at all. This script never
modifies anything -- it only reports which projects may need their HPC
settings re-entered.

Usage:
    python scripts/audit_project_hpc_settings.py
    python scripts/audit_project_hpc_settings.py --projects-dir /path/to/projects
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


def _load_config(project_json_path: Path) -> Dict[str, Any]:
    with open(project_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    config = data.get("config")
    return config if isinstance(config, dict) else {}


def _missing_hpc_fields(hpc: Dict[str, Any]) -> list:
    required = ("partition", "time", "mem", "cpus")
    return [field for field in required if not hpc.get(field)]


def audit(projects_dir: Path) -> int:
    if not projects_dir.is_dir():
        print(f"ERROR: projects directory not found: {projects_dir}", file=sys.stderr)
        return 1

    flagged = []
    checked = 0

    for project_dir in sorted(projects_dir.iterdir()):
        project_json_path = project_dir / "project.json"
        if not project_json_path.is_file():
            continue
        checked += 1

        try:
            config = _load_config(project_json_path)
        except (json.JSONDecodeError, OSError) as exc:
            flagged.append((project_dir.name, f"could not read project.json: {exc}"))
            continue

        hpc = config.get("hpc")
        if not isinstance(hpc, dict) or not hpc:
            flagged.append((project_dir.name, "no 'hpc' section at all"))
            continue

        missing = _missing_hpc_fields(hpc)
        if missing:
            flagged.append(
                (project_dir.name, f"hpc present but missing/empty: {', '.join(missing)}")
            )

    print(f"Checked {checked} project(s) under {projects_dir}\n")
    if not flagged:
        print("No projects flagged -- every project has a populated hpc section.")
        return 0

    print(f"{len(flagged)} project(s) may need HPC settings re-entered:\n")
    for name, reason in flagged:
        print(f"  - {name}: {reason}")
    print(
        "\nThis is a read-only report. An empty/missing 'hpc' section may be "
        "intentional for a project only ever used for local (non-HPC) runs -- "
        "review each one before re-entering settings."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--projects-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "projects",
        help="Path to the projects/ directory (default: <repo>/projects)",
    )
    args = parser.parse_args()
    return audit(args.projects_dir)


if __name__ == "__main__":
    sys.exit(main())
