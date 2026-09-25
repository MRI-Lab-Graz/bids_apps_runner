#!/usr/bin/env python3
"""Check which subjects' fMRIPrep resting-state output is actually complete.

An `.html` report existing is NOT a reliable signal that a subject's
functional preprocessing finished -- fMRIPrep writes that report once the
workflow reaches a certain point even when a later stage (e.g. the
functional pipeline for one run) errors out. Confirmed real incident
(2026-09-17, connectoflow demo run): ds003849's sub-BPD0113B and
sub-BPD0119A both had an `.html` report on disk, but their fMRIPrep run
never produced `desc-preproc_bold.nii.gz` at all -- fmridenoiser correctly
failed on both with "No functional images found".

This checks for the real thing instead: the actual
`desc-preproc_bold.nii.gz` output file for each matching resting-state raw
BOLD run, opened with nibabel to confirm it has a sane volume count
(compared against that run's raw BOLD file -- fMRIPrep doesn't drop volumes
from desc-preproc_bold by default, so a shorter processed run than its raw
input means a truncated/corrupted output, not just "still running").

Needs nibabel, which isn't a bids_apps_runner dependency -- run via:
    uv run --with nibabel scripts/check_fmriprep_complete.py ...
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))
from build_qa_subject_list import task_labels_for  # noqa: E402

try:
    import nibabel as nib
except ImportError:
    nib = None

# fMRIPrep doesn't trim volumes from desc-preproc_bold by default; allow a
# small tolerance for the rare dataset where it legitimately does (e.g.
# --dummy-scans configured), rather than flagging every off-by-one as a
# truncation.
VOLUME_COUNT_TOLERANCE = 2


def _nvols(path: Path) -> Optional[int]:
    if nib is None:
        raise RuntimeError(
            "nibabel not installed -- run via: uv run --with nibabel "
            f"{Path(__file__).name} ..."
        )
    try:
        shape = nib.load(str(path)).shape
    except Exception:
        return None
    return shape[3] if len(shape) == 4 else None


def _preproc_path_for(raw_bold: Path, fmriprep_dir: Path, bids_dir: Path, space: str) -> Path:
    rel_dir = raw_bold.parent.relative_to(bids_dir)  # sub-X[/ses-Y]/func
    stem = raw_bold.name.removesuffix(".nii.gz").removesuffix("_bold")
    # fMRIPrep COMBINES multi-echo runs, so its output carries no `echo-`
    # entity even though each raw echo does. Keeping it made every multi-echo
    # subject look missing -- ds006707 had all 184 runs flagged while the data
    # was on disk. Other entities (part-mag, dir-AP, run-N) are preserved.
    stem = re.sub(r"_echo-[0-9]+", "", stem)
    return fmriprep_dir / rel_dir / f"{stem}_space-{space}_desc-preproc_bold.nii.gz"


def fmriprep_complete_report(
    fmriprep_dir: str, bids_dir: str, dataset_id: str, space: str = "MNI152NLin2009cAsym"
) -> Dict[str, Dict]:
    """Return per-subject fMRIPrep-completeness results for one dataset."""
    fp_root = Path(fmriprep_dir)
    bids_root = Path(bids_dir)
    task_labels = task_labels_for(dataset_id)
    results: Dict[str, Dict] = {}

    for subject_dir in sorted(bids_root.glob("sub-*")):
        if not subject_dir.is_dir():
            continue
        subject = subject_dir.name
        raw_files: List[Path] = []
        for task in task_labels:
            raw_files.extend(sorted(subject_dir.glob(f"**/func/*task-{task}*_bold.nii.gz")))

        runs = []
        seen_preproc = set()
        for raw_bold in raw_files:
            preproc = _preproc_path_for(raw_bold, fp_root, bids_root, space)
            # Several raw echoes collapse onto one combined output; report that
            # output once rather than once per echo.
            if str(preproc) in seen_preproc:
                continue
            seen_preproc.add(str(preproc))
            if not preproc.exists():
                runs.append({"raw": str(raw_bold), "preproc": str(preproc), "ok": False, "reason": "missing"})
                continue
            raw_nvols = _nvols(raw_bold)
            proc_nvols = _nvols(preproc)
            if proc_nvols is None:
                runs.append({"raw": str(raw_bold), "preproc": str(preproc), "ok": False, "reason": "unreadable output nifti"})
            elif raw_nvols is not None and proc_nvols < raw_nvols - VOLUME_COUNT_TOLERANCE:
                runs.append({
                    "raw": str(raw_bold), "preproc": str(preproc), "ok": False,
                    "reason": f"truncated: {proc_nvols} volumes vs {raw_nvols} raw",
                })
            else:
                runs.append({"raw": str(raw_bold), "preproc": str(preproc), "ok": True, "nvols": proc_nvols})

        # Completeness is "at least one verified scan", NOT "every raw run
        # processed". Since 3c7d25a (2026-09-21) this megastudy is
        # cross-sectional -- each subject contributes exactly one scan, so an
        # unprocessed sibling session is a deliberate choice. Requiring all of
        # them reported by-design behaviour as data loss (ds004592: "ok=1/2"
        # for all 27 subjects; ds005339 and ds004466 likewise).
        #
        # A run whose output is ABSENT is therefore not an error. A run whose
        # output EXISTS but is truncated or unreadable still is -- catching
        # exactly that is why this tool exists, so the relaxation must not
        # excuse it.
        verified = [r for r in runs if r["ok"]]
        corrupt = [r for r in runs if not r["ok"] and r.get("reason") != "missing"]
        complete = bool(verified) and not corrupt

        if complete:
            reason = None
        elif not runs:
            reason = f"no resting-state raw BOLD found for task(s) {task_labels}"
        elif corrupt:
            reason = "; ".join(sorted({r["reason"] for r in corrupt}))
        else:
            reason = "no resting-state run has a verified fMRIPrep output"

        results[subject] = {
            "complete": complete,
            "verified_runs": len(verified),
            "runs": runs,
            "reason": reason,
        }

    return results


def summarize(results: Dict[str, Dict]) -> str:
    total = len(results)
    complete = sorted(s for s, v in results.items() if v["complete"])
    incomplete = sorted(s for s, v in results.items() if not v["complete"])

    lines = [f"Subjects: {total}", f"  fMRIPrep-complete: {len(complete)}", f"  incomplete:        {len(incomplete)}"]
    if incomplete:
        lines.append("")
        lines.append("Incomplete:")
        for s in incomplete:
            lines.append(f"  {s}: {results[s]['reason']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dataset_id", help="OpenNeuro dataset ID, e.g. ds004182")
    parser.add_argument("fmriprep_dir", help="Path to that dataset's fMRIPrep output directory")
    parser.add_argument("bids_dir", help="Path to that dataset's raw BIDS directory")
    parser.add_argument("--space", default="MNI152NLin2009cAsym", help="fMRIPrep output space (default: MNI152NLin2009cAsym)")
    parser.add_argument("--out", help="Write the fMRIPrep-complete subject list here (one sub-ID per line).")
    parser.add_argument("--report-json", help="Write the full per-subject/per-run report to this path.")
    args = parser.parse_args()

    results = fmriprep_complete_report(args.fmriprep_dir, args.bids_dir, args.dataset_id, args.space)
    print(summarize(results))

    if args.report_json:
        Path(args.report_json).write_text(json.dumps(results, indent=2))

    if args.out:
        complete = sorted(s for s, v in results.items() if v["complete"])
        Path(args.out).write_text("\n".join(complete) + ("\n" if complete else ""))
        print(f"\nWrote {len(complete)} fMRIPrep-complete subject(s) to {args.out}")


if __name__ == "__main__":
    main()
