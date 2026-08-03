#!/usr/bin/env python3
"""Report per-subject fieldmap presence for a BIDS dataset.

Advisory tool for the mega-study cohort workflow (see
docs/hpc_fmriprep_workflow_hpc.md / configs/megastudy_openneuro_*.json):
run once per dataset to decide whether that dataset needs a
per-dataset ``options_extra`` override (e.g. ``--use-syn-sdc`` or
``--ignore fieldmaps``) in the fmriprep cohort config. Does not modify
anything and is not wired into the submit path -- the actual per-dataset
override stays a manual, human decision.
"""
import argparse
import json
from pathlib import Path
from typing import Dict


def check_fieldmaps(bids_root: str) -> Dict[str, Dict]:
    """Return per-subject fieldmap presence for all sub-* directories.

    Mirrors the glob idiom used in utils/fix_bids_intendedfor.py and
    scripts/prism_local.py's IntendedFor handling (``**/fmap/*``), just
    checking existence instead of rewriting file contents.
    """
    root = Path(bids_root)
    results: Dict[str, Dict] = {}
    for subject_dir in sorted(root.glob("sub-*")):
        if not subject_dir.is_dir():
            continue
        fmap_files = sorted(
            p for p in subject_dir.glob("**/fmap/*") if p.is_file()
        )
        results[subject_dir.name] = {
            "has_fieldmaps": bool(fmap_files),
            "fmap_files": [str(p.relative_to(root)) for p in fmap_files],
        }
    return results


def summarize(results: Dict[str, Dict]) -> str:
    total = len(results)
    with_fmaps = sorted(s for s, v in results.items() if v["has_fieldmaps"])
    without_fmaps = sorted(s for s, v in results.items() if not v["has_fieldmaps"])

    lines = [f"Subjects: {total}"]
    lines.append(f"  with fieldmaps:    {len(with_fmaps)}")
    lines.append(f"  without fieldmaps: {len(without_fmaps)}")

    if without_fmaps:
        lines.append("")
        lines.append("Missing fieldmaps for:")
        lines.extend(f"  {s}" for s in without_fmaps)

    if with_fmaps and without_fmaps:
        lines.append("")
        lines.append(
            "Dataset has MIXED fieldmap coverage -- a single dataset-wide "
            "options_extra override may not be correct for every subject; "
            "consider per-subject handling instead."
        )
    elif not with_fmaps and total:
        lines.append("")
        lines.append(
            "No subject has fieldmaps -- this dataset likely needs an "
            "options_extra override in the fmriprep cohort config (e.g. "
            '["--use-syn-sdc"] or ["--ignore", "fieldmaps"]).'
        )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Report per-subject fieldmap presence for a BIDS dataset."
    )
    parser.add_argument("bids_root", help="Root of the BIDS dataset")
    parser.add_argument(
        "--json", action="store_true", help="Print raw per-subject results as JSON"
    )
    args = parser.parse_args()

    results = check_fieldmaps(args.bids_root)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(summarize(results))


if __name__ == "__main__":
    main()
