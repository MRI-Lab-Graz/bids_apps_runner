#!/usr/bin/env python3
"""Backup tool: harmonize sub-voxel FOV/affine drift across DWI runs.

Not part of the normal submission pipeline -- run manually when a qsiprep
(or similar) job fails with nilearn's:

    ValueError: Field of view of image #1 is different from reference FOV.

That error fires when nilearn.concat_imgs is asked to combine DWI runs
within the same session and their affines aren't bit-identical. In
practice this is usually sub-voxel scanner header rounding between
back-to-back runs (same FOV, same matrix, same voxel size -- just a
fraction of a millimeter of translation/rotation noise in the sform),
not a real geometric or subject-identity mismatch. This tool detects
that specific, narrow case and can optionally snap the affine header
of the drifting run(s) onto the session's reference run.

It refuses to touch anything where shape or voxel size differ, or where
the affine difference exceeds --tolerance-mm -- those are left for a
human to look at, since they may be a genuine acquisition difference
rather than header noise.

Must be run with a Python that has nibabel/numpy -- this repo's own
venv intentionally doesn't carry that dependency (see requirements.txt),
so use one of the BIDS app containers already on this machine, e.g.:

    singularity exec /usr/people/mrilabgraz/container/qsiprep/qsiprep_26.0.0.sif \\
        python3 scripts/fix_dwi_fov_headers.py --bids-dir /cl_tmp/mrilab/129 --check

Default mode is --check (read-only, reports what it finds/would do).
Pass --apply to actually rewrite headers; originals are preserved next
to each rewritten file with a .orig backup suffix.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

RUN_RE = re.compile(r"_run-(\d+)")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--bids-dir", required=True, help="Path to the BIDS dataset root"
    )
    parser.add_argument(
        "--subjects",
        nargs="*",
        default=None,
        help="Restrict to these subject labels (e.g. sub-006 006). "
        "Default: scan every subject in --bids-dir.",
    )
    parser.add_argument(
        "--tolerance-mm",
        type=float,
        default=0.05,
        help="Max affine element difference (mm) to treat as header "
        "rounding noise and consider fixable. Larger differences are "
        "flagged for manual review, never auto-fixed. Default: 0.05mm.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually rewrite headers. Without this flag, only reports "
        "findings (dry-run).",
    )
    parser.add_argument(
        "--backup-suffix",
        default=".orig",
        help="Suffix appended to the original file before it's overwritten "
        "(default: .orig). Existing backups are never clobbered.",
    )
    return parser.parse_args(argv)


def normalize_subject_label(label):
    return label if label.startswith("sub-") else f"sub-{label}"


def find_dwi_run_groups(bids_dir, subjects=None):
    """Group DWI niftis by (subject, session, everything-but-run-N).

    Returns a dict: group_key (str) -> list of (run_number, Path) sorted
    by run number. Only groups with 2+ runs are included -- a lone run
    has nothing to be inconsistent with.
    """
    bids_dir = Path(bids_dir)
    if subjects:
        subject_dirs = [
            bids_dir / normalize_subject_label(s)
            for s in subjects
        ]
        subject_dirs = [d for d in subject_dirs if d.is_dir()]
    else:
        subject_dirs = sorted(d for d in bids_dir.glob("sub-*") if d.is_dir())

    groups = {}
    for subj_dir in subject_dirs:
        session_dirs = sorted(subj_dir.glob("ses-*"))
        dwi_dirs = (
            [d / "dwi" for d in session_dirs] if session_dirs else [subj_dir / "dwi"]
        )
        for dwi_dir in dwi_dirs:
            if not dwi_dir.is_dir():
                continue
            for nii in sorted(dwi_dir.glob("*_dwi.nii*")):
                m = RUN_RE.search(nii.name)
                if not m:
                    continue
                run_number = int(m.group(1))
                group_key = str(dwi_dir / RUN_RE.sub("", nii.name))
                groups.setdefault(group_key, []).append((run_number, nii))

    return {k: sorted(v) for k, v in groups.items() if len(v) > 1}


def _load_affine_info(path):
    import nibabel as nib

    img = nib.load(str(path))
    return img, img.shape[:3], img.header.get_zooms()[:3]


def check_group(group_key, runs, tolerance_mm):
    """Compare every run in a group against the first (reference) run.

    Returns a list of dicts, one per non-reference run, each describing
    whether it's already consistent, fixable (sub-tolerance drift), or
    needs manual review (shape/zoom mismatch or over-tolerance drift).
    """
    import numpy as np

    ref_run_number, ref_path = runs[0]
    ref_img, ref_shape, ref_zooms = _load_affine_info(ref_path)
    ref_affine = ref_img.affine

    results = []
    for run_number, path in runs[1:]:
        img, shape, zooms = _load_affine_info(path)
        if shape != ref_shape or zooms != ref_zooms:
            results.append(
                {
                    "path": path,
                    "run": run_number,
                    "status": "needs_review",
                    "reason": (
                        f"shape/zoom differ from run-{ref_run_number} "
                        f"({shape}/{zooms} vs {ref_shape}/{ref_zooms}) -- "
                        "not a header-rounding case, leaving untouched"
                    ),
                }
            )
            continue

        diff = float(np.max(np.abs(img.affine - ref_affine)))
        if diff == 0.0:
            results.append(
                {"path": path, "run": run_number, "status": "consistent", "diff": 0.0}
            )
        elif diff <= tolerance_mm:
            results.append(
                {
                    "path": path,
                    "run": run_number,
                    "status": "fixable",
                    "diff": diff,
                    "ref_affine": ref_affine,
                }
            )
        else:
            results.append(
                {
                    "path": path,
                    "run": run_number,
                    "status": "needs_review",
                    "reason": (
                        f"affine differs from run-{ref_run_number} by "
                        f"{diff:.4f}mm, over the {tolerance_mm}mm tolerance -- "
                        "may be a real geometric difference, not header noise"
                    ),
                    "diff": diff,
                }
            )
    return results


def apply_fix(entry, backup_suffix):
    import nibabel as nib

    path = entry["path"]
    backup_path = path.with_name(path.name + backup_suffix)
    if not backup_path.exists():
        shutil.copy2(path, backup_path)

    img = nib.load(str(path))
    fixed = nib.Nifti1Image(img.dataobj, entry["ref_affine"], img.header)
    fixed.set_qform(entry["ref_affine"])
    fixed.set_sform(entry["ref_affine"])
    nib.save(fixed, str(path))


def main(argv=None):
    args = parse_args(argv)

    try:
        import nibabel  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        print(
            "ERROR: nibabel/numpy not available in this Python. This tool "
            "must be run with a Python that has them -- e.g. via one of "
            "the BIDS app containers on this machine:\n\n"
            "  singularity exec <container>.sif python3 "
            f"{Path(__file__).relative_to(Path.cwd()) if Path(__file__).is_relative_to(Path.cwd()) else __file__} "
            "--bids-dir ... --check\n",
            file=sys.stderr,
        )
        return 1

    groups = find_dwi_run_groups(args.bids_dir, args.subjects)
    if not groups:
        print("No multi-run DWI groups found.")
        return 0

    n_consistent = n_fixable = n_review = n_fixed = 0

    for group_key, runs in sorted(groups.items()):
        results = check_group(group_key, runs, args.tolerance_mm)
        for entry in results:
            if entry["status"] == "consistent":
                n_consistent += 1
                continue
            if entry["status"] == "fixable":
                n_fixable += 1
                verb = "Fixing" if args.apply else "Would fix"
                print(
                    f"{verb}: {entry['path']} "
                    f"(affine drift {entry['diff']:.4f}mm vs run-{runs[0][0]})"
                )
                if args.apply:
                    apply_fix(entry, args.backup_suffix)
                    n_fixed += 1
            else:
                n_review += 1
                print(f"NEEDS REVIEW: {entry['path']} -- {entry['reason']}")

    print(
        f"\nSummary: {n_consistent} already consistent, "
        f"{n_fixable} fixable ({n_fixed} fixed), "
        f"{n_review} need manual review."
    )
    if not args.apply and n_fixable:
        print("Re-run with --apply to write the harmonized headers.")

    return 1 if n_review else 0


if __name__ == "__main__":
    sys.exit(main())
