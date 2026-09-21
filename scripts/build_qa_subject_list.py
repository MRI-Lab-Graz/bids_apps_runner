#!/usr/bin/env python3
"""Build a QA-valid subject list from MRIQC output, for the fmriprep cohort.

Advisory + generating tool for the mega-study cohort workflow (see
configs/megastudy_openneuro_mriqc.json / megastudy_openneuro_fmriprep.json):
run once per dataset, after that dataset's MRIQC run is complete, to decide
which subjects' resting-state BOLD data is QA-valid and write the subject
list fmriprep's cohort submit should use.

QA rule (2026-09-21 decision, supersedes the 2026-09-15 "all runs must
pass" rule): this study looks at CROSS-SECTIONAL correlations between
resting-state connectivity and weather metrics, so each subject
contributes exactly ONE scan, not every session/run they happen to have.
A subject is QA-valid if it has at least one resting-state BOLD run with
an MRIQC IQM file, and the SINGLE BEST one (lowest fd_mean) has
fd_mean <= --fd-threshold (default 0.5mm). That best run is recorded as
the subject's `selected` run -- downstream (hpc_datalad_runner.py) uses
it to write a per-subject BIDS filter file so fmriprep only ever processes
that one session/run, not the whole multi-session subject directory.

"Resting-state" is matched by BIDS task label, not simply presence of any
func/ file -- these datasets carry other tasks too. The per-dataset task
label map below mirrors the "keep" decisions already made in
scripts/remove_non_rest_data.sh (2026-09-10) so the same resting-state runs
are the ones both scripts agree on; datasets not listed there default to
["rest"].
"""
import argparse
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

# Mirrors scripts/remove_non_rest_data.sh's per-dataset task decisions.
TASK_LABELS_BY_DATASET: Dict[str, List[str]] = {
    "ds003171": ["restawake"],
    "ds000256": ["restbaseline"],
    "ds005127": ["rest"],
    "ds004592": ["rest1", "rest2"],
    "ds000031": ["restME", "restEyesOpen"],
}
DEFAULT_TASK_LABELS = ["rest"]

_ENTITY_RE = {
    "session": re.compile(r"_ses-([a-zA-Z0-9]+)"),
    "run": re.compile(r"_run-([a-zA-Z0-9]+)"),
    "task": re.compile(r"_task-([a-zA-Z0-9]+)"),
}


def task_labels_for(dataset_id: str) -> List[str]:
    return TASK_LABELS_BY_DATASET.get(dataset_id, DEFAULT_TASK_LABELS)


def _parse_entities(filename: str) -> Dict[str, Optional[str]]:
    return {key: (m.group(1) if (m := rx.search(filename)) else None) for key, rx in _ENTITY_RE.items()}


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
        candidates = []
        for task in task_labels:
            for iqm_file in sorted(
                subject_dir.glob(f"**/func/*task-{task}*_bold.json")
            ):
                try:
                    iqm = json.loads(iqm_file.read_text())
                    fd_mean = iqm.get("fd_mean")
                except (OSError, json.JSONDecodeError):
                    fd_mean = None
                candidates.append(
                    {
                        "file": str(iqm_file.relative_to(root)),
                        "fd_mean": fd_mean,
                        **_parse_entities(iqm_file.name),
                    }
                )

        scored = [c for c in candidates if c["fd_mean"] is not None]
        selected = min(scored, key=lambda c: c["fd_mean"]) if scored else None
        qa_valid = selected is not None and selected["fd_mean"] <= fd_threshold

        if qa_valid:
            reason = None
        elif not candidates:
            reason = f"no resting-state IQM found for task(s) {task_labels}"
        elif selected is None:
            reason = "no candidate run had a readable fd_mean"
        else:
            reason = f"best run's fd_mean {selected['fd_mean']:.3f} > {fd_threshold} ({len(candidates)} candidate(s))"

        results[subject_dir.name] = {
            "qa_valid": qa_valid,
            "selected": selected,
            "n_candidates": len(candidates),
            "reason": reason,
        }

    return results


def summarize(results: Dict[str, Dict]) -> str:
    total = len(results)
    valid = sorted(s for s, v in results.items() if v["qa_valid"])
    invalid = sorted(s for s, v in results.items() if not v["qa_valid"])
    multi = sorted(s for s, v in results.items() if v["qa_valid"] and v["n_candidates"] > 1)

    lines = [f"Subjects: {total}"]
    lines.append(f"  QA-valid:   {len(valid)}")
    lines.append(f"  excluded:   {len(invalid)}")
    if multi:
        lines.append(f"  QA-valid with >1 candidate run (best one selected): {len(multi)}")
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
        help="Write the full per-subject QA report (including each subject's "
        "selected run) to this path.",
    )
    parser.add_argument(
        "--bids-filters-out",
        help="Write per-subject BIDS filter JSON files (fmriprep --bids-filter-file "
        "format) into this directory, one per subject that has a session or run "
        "entity to pin -- so fmriprep only ever processes the single selected "
        "scan, not every session/run a multi-session subject happens to have.",
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

    if args.bids_filters_out:
        out_dir = Path(args.bids_filters_out)
        out_dir.mkdir(parents=True, exist_ok=True)
        written = 0
        for subject, v in results.items():
            if not v["qa_valid"]:
                continue
            if v["n_candidates"] <= 1:
                continue  # only one candidate scan -- nothing to disambiguate, fmriprep's default is already correct
            sel = v["selected"]
            bold_filter = {"task": sel["task"]}
            if sel["session"] is not None:
                bold_filter["session"] = sel["session"]
            if sel["run"] is not None:
                bold_filter["run"] = sel["run"]
            (out_dir / f"{subject}.json").write_text(json.dumps({"bold": bold_filter}, indent=2))
            written += 1
        print(f"Wrote {written} per-subject BIDS filter file(s) to {out_dir}")


if __name__ == "__main__":
    main()
