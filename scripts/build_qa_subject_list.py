#!/usr/bin/env python3
"""Build a QA-valid subject list from MRIQC output, for the fmriprep cohort.

Advisory + generating tool for the mega-study cohort workflow (see
configs/megastudy_openneuro_mriqc.json / megastudy_openneuro_fmriprep.json):
run once per dataset, after that dataset's MRIQC run is complete, to decide
which subjects' resting-state BOLD data is QA-valid and write the subject
list fmriprep's cohort submit should use.

QA rule (2026-09-15 decision): a subject is QA-valid if it has at least one
resting-state BOLD run with an MRIQC IQM file, AND every such run has
fd_mean <= --fd-threshold (default 0.5mm, mean framewise displacement).
A subject with zero matching resting-state runs, or where MRIQC itself
failed to produce IQMs for a run, is excluded -- so is a subject where any
matching run exceeds the threshold (conservative: one noisy run is enough
to keep the whole subject out of fmriprep for this study).

"Resting-state" is matched by BIDS task label, not simply presence of any
func/ file -- these datasets carry other tasks too. The per-dataset task
label map below mirrors the "keep" decisions already made in
scripts/remove_non_rest_data.sh (2026-09-10) so the same resting-state runs
are the ones both scripts agree on; datasets not listed there default to
["rest"].
"""
import argparse
import json
from pathlib import Path
from typing import Dict, List

# Mirrors scripts/remove_non_rest_data.sh's per-dataset task decisions.
TASK_LABELS_BY_DATASET: Dict[str, List[str]] = {
    "ds003171": ["restawake"],
    "ds000256": ["restbaseline"],
    "ds005127": ["rest"],
    "ds004592": ["rest1", "rest2"],
    "ds000031": ["restME", "restEyesOpen"],
}
DEFAULT_TASK_LABELS = ["rest"]


def task_labels_for(dataset_id: str) -> List[str]:
    return TASK_LABELS_BY_DATASET.get(dataset_id, DEFAULT_TASK_LABELS)


def build_qa_report(
    mriqc_dir: str, dataset_id: str, fd_threshold: float
) -> Dict[str, Dict]:
    """Return per-subject QA results for one dataset's MRIQC output."""
    root = Path(mriqc_dir)
    task_labels = task_labels_for(dataset_id)
    results: Dict[str, Dict] = {}

    for subject_dir in sorted(root.glob("sub-*")):
        if not subject_dir.is_dir():
            continue
        runs = []
        for task in task_labels:
            for iqm_file in sorted(
                subject_dir.glob(f"**/func/*task-{task}*_bold.json")
            ):
                try:
                    iqm = json.loads(iqm_file.read_text())
                except (OSError, json.JSONDecodeError) as exc:
                    runs.append(
                        {
                            "file": str(iqm_file.relative_to(root)),
                            "fd_mean": None,
                            "passed": False,
                            "reason": f"unreadable IQM file: {exc}",
                        }
                    )
                    continue
                fd_mean = iqm.get("fd_mean")
                passed = fd_mean is not None and fd_mean <= fd_threshold
                runs.append(
                    {
                        "file": str(iqm_file.relative_to(root)),
                        "fd_mean": fd_mean,
                        "passed": passed,
                        "reason": None
                        if passed
                        else (
                            "fd_mean missing from IQM file"
                            if fd_mean is None
                            else f"fd_mean {fd_mean:.3f} > {fd_threshold}"
                        ),
                    }
                )

        qa_valid = bool(runs) and all(r["passed"] for r in runs)
        results[subject_dir.name] = {
            "qa_valid": qa_valid,
            "runs": runs,
            "reason": None
            if qa_valid
            else (
                f"no resting-state IQM found for task(s) {task_labels}"
                if not runs
                else "at least one resting-state run failed QA"
            ),
        }

    return results


def summarize(results: Dict[str, Dict]) -> str:
    total = len(results)
    valid = sorted(s for s, v in results.items() if v["qa_valid"])
    invalid = sorted(s for s, v in results.items() if not v["qa_valid"])

    lines = [f"Subjects: {total}"]
    lines.append(f"  QA-valid:   {len(valid)}")
    lines.append(f"  excluded:   {len(invalid)}")
    if invalid:
        lines.append("")
        lines.append("Excluded:")
        for s in invalid:
            lines.append(f"  {s}: {results[s]['reason']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a QA-valid subject list from MRIQC output for one dataset."
    )
    parser.add_argument("dataset_id", help="OpenNeuro dataset ID, e.g. ds004182")
    parser.add_argument("mriqc_dir", help="Path to that dataset's MRIQC output directory")
    parser.add_argument(
        "--fd-threshold",
        type=float,
        default=0.5,
        help="Max mean framewise displacement (mm) to pass QA (default: 0.5)",
    )
    parser.add_argument(
        "--out",
        help="Write the QA-valid subject list here (one sub-ID per line). "
        "If omitted, only prints the summary.",
    )
    parser.add_argument(
        "--report-json",
        help="Write the full per-subject/per-run QA report to this path.",
    )
    args = parser.parse_args()

    results = build_qa_report(args.mriqc_dir, args.dataset_id, args.fd_threshold)
    print(summarize(results))

    if args.report_json:
        Path(args.report_json).write_text(json.dumps(results, indent=2))

    if args.out:
        valid = sorted(s for s, v in results.items() if v["qa_valid"])
        Path(args.out).write_text("\n".join(valid) + ("\n" if valid else ""))
        print(f"\nWrote {len(valid)} QA-valid subject(s) to {args.out}")


if __name__ == "__main__":
    main()
