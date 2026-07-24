#!/usr/bin/env python3
"""Concatenate FreeSurfer segment_subregions volume outputs across a cohort.

Produces one wide CSV per structure/hemisphere file (row per subject
timepoint), the same end result as FreeSurfer's own
ConcatenateSubregionsResults.sh (https://surfer.nmr.mgh.harvard.edu/fswiki/
ConcatenateSubregionsResults) -- but reimplemented rather than calling that
script directly: it expects output under <subject>/stats/<file>.stats with
a "#"-comment header line, while segment_subregions (the tool this pipeline
actually runs, FreeSurfer 7+'s samseg.cli.segment_subregions) writes plain
"label value" lines with no header straight into <subject>/mri/ (confirmed
against FreeSurfer 8.2's own samseg/subregions/{core,thalamus,hippocampus,
brainstem}.py -- write_volumes() and each model's outDir default). Run
against a real FS 8.2 image, ConcatenateSubregionsResults.sh silently finds
nothing for every subject. This reads the format segment_subregions
actually writes.

Called as a plain SLURM job by submit_bids_cohort.sh's
submit_subregion_segmentation(), between the subregion array job and its
finish job, so the new CSVs are covered by the same "-o ." datalad-slurm
declaration and get committed+pushed together with the raw per-subject
output.
"""

import argparse
import csv
import sys
from pathlib import Path

# {suffix} is "" for cross-sectional, ".long" for longitudinal (segment_subregions
# prepends ".long" to fileSuffix for both the base and every timepoint model in
# longitudinal mode -- see samseg/subregions/process.py).
_STRUCTURE_FILES = {
    "thalamus": [
        ("ThalamicNuclei{suffix}.volumes.txt", "ThalamicNuclei"),
    ],
    "hippo-amygdala": [
        ("lh.hippoSfVolumes{suffix}.txt", "lh.hippoSfVolumes"),
        ("rh.hippoSfVolumes{suffix}.txt", "rh.hippoSfVolumes"),
        ("lh.amygNucVolumes{suffix}.txt", "lh.amygNucVolumes"),
        ("rh.amygNucVolumes{suffix}.txt", "rh.amygNucVolumes"),
    ],
    "brainstem": [
        ("brainstemSsLabels{suffix}.volumes.txt", "brainstemSsLabels"),
    ],
}


def _read_volumes(path: Path) -> dict:
    """Parse a segment_subregions "label value" text file (no header)."""
    volumes = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            label, _, value = line.rpartition(" ")
            if not label:
                continue
            volumes[label] = value
    return volumes


def concat_structure_file(
    subjects_dir: Path, timepoint_dirs: list, filename: str, out_path: Path
) -> int:
    """Write one wide CSV (row per timepoint) for a single output filename.
    Returns the number of timepoints actually found (rows written)."""
    rows = {}
    columns: list = []
    for tp in timepoint_dirs:
        vol_file = subjects_dir / tp / "mri" / filename
        if not vol_file.is_file():
            continue
        volumes = _read_volumes(vol_file)
        for label in volumes:
            if label not in columns:
                columns.append(label)
        rows[tp] = volumes

    if not rows:
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timepoint"] + columns)
        for tp in sorted(rows):
            writer.writerow([tp] + [rows[tp].get(c, "") for c in columns])
    return len(rows)


def main_from_args(
    subjects_dir: str, mode: str, structures: list, timepoint_list: str, results_dir: str
) -> int:
    subjects_dir = Path(subjects_dir)
    results_dir = Path(results_dir)
    suffix = ".long" if mode == "longitudinal" else ""

    with open(timepoint_list) as f:
        timepoint_dirs = [line.strip() for line in f if line.strip()]
    if not timepoint_dirs:
        print("No timepoints in timepoint list -- nothing to concatenate", file=sys.stderr)
        return 1

    # Longitudinal mode: the timepoint list holds BASE dir names (sub-XXX);
    # segment_subregions --long-base writes each timepoint's own results into
    # that base's ".long." directories, not the base dir itself.
    if mode == "longitudinal":
        expanded = []
        for base in timepoint_dirs:
            expanded.extend(
                d.name for d in subjects_dir.glob(f"*.long.{base}") if d.is_dir()
            )
        timepoint_dirs = sorted(expanded)
        if not timepoint_dirs:
            print(
                "No .long. timepoint directories found for the given bases -- nothing to concatenate",
                file=sys.stderr,
            )
            return 1

    total_written = 0
    for structure in structures:
        for filename_template, canonical_name in _STRUCTURE_FILES[structure]:
            filename = filename_template.format(suffix=suffix)
            out_path = results_dir / f"{canonical_name}_{mode}_concat.csv"
            n = concat_structure_file(subjects_dir, timepoint_dirs, filename, out_path)
            print(f"{structure}: {filename} -> {out_path} ({n} timepoint(s))")
            total_written += n

    if total_written == 0:
        print(
            "WARNING: no volume files found for any structure -- check that "
            "the segmentation array job actually completed",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Concatenate segment_subregions volume outputs across a "
        "cohort into one CSV per structure/file."
    )
    parser.add_argument(
        "--subjects-dir", required=True, help="FreeSurfer SUBJECTS_DIR (the output dataset root)"
    )
    parser.add_argument("--mode", required=True, choices=["cross", "longitudinal"])
    parser.add_argument(
        "--structures", required=True, nargs="+", choices=list(_STRUCTURE_FILES)
    )
    parser.add_argument(
        "--timepoint-list",
        required=True,
        help="Path to timepoint list file, one dir name per line (the same "
        "list the segmentation array job used)",
    )
    parser.add_argument(
        "--results-dir", required=True, help="Directory to write concatenated CSVs into"
    )
    args = parser.parse_args()

    return main_from_args(
        subjects_dir=args.subjects_dir,
        mode=args.mode,
        structures=args.structures,
        timepoint_list=args.timepoint_list,
        results_dir=args.results_dir,
    )


if __name__ == "__main__":
    sys.exit(main())
