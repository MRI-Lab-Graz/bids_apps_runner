#!/usr/bin/env python3
"""Utility for renaming old nf_* logs into BIDS event files."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Sequence, Tuple


def parse_session(name: str, subject_start: int) -> int:
    cb_idx = name.find("CB01")
    if cb_idx == -1:
        return 1
    within = name[cb_idx + 4 : subject_start]
    if not within:
        return 1
    if "_" in within:
        tokens = [tok for tok in within.split("_") if tok]
        if tokens:
            return int(tokens[-1])
        digits = "".join(ch for ch in within if ch.isdigit())
        if digits:
            return int(digits[-1])
    digits = "".join(ch for ch in within if ch.isdigit())
    if digits:
        return int(digits[-1])
    return 1


def build_target_name(
    subject_prefix: str,
    subject: str,
    session: int,
    bids_task: str,
    run: int | None,
) -> str:
    subject_code = subject.zfill(3)
    parts = [
        f"sub-{subject_prefix}{subject_code}",
        f"ses-{session}",
        f"task-{bids_task}",
    ]
    if run is not None:
        parts.append(f"run-{run}")
    return "_".join(parts) + "_events.tsv"


def rename_event_files(
    directory: Path,
    legacy_task: str,
    bids_task: str,
    subject_prefix: str,
    include_run: bool,
    session_override: int | None,
) -> Tuple[Sequence[Tuple[str, str]], Sequence[str], Sequence[Tuple[str, str]]]:
    legacy_upper = legacy_task.upper()
    subject_re = re.compile(rf"(\d{{2,3}})(?=_{legacy_upper})")
    run_re = re.compile(rf"_{legacy_upper}(\d+)")
    renamed: list[Tuple[str, str]] = []
    skipped: list[str] = []
    conflicts: list[Tuple[str, str]] = []

    for path in sorted(directory.glob("*.tsv")):
        name = path.name
        subject_match = subject_re.search(name)
        if not subject_match:
            skipped.append(name)
            continue
        subject = subject_match.group(1)
        session = parse_session(name, subject_match.start())
        run = None
        if include_run:
            run_match = run_re.search(name)
            run = int(run_match.group(1)) if run_match else 1

        effective_session = (
            session_override if session_override is not None else session
        )
        target_name = build_target_name(
            subject_prefix, subject, effective_session, bids_task, run
        )
        target_path = path.with_name(target_name)
        if target_path.exists():
            conflicts.append((name, target_name))
            continue
        if target_path == path:
            skipped.append(name)
            continue
        path.rename(target_path)
        renamed.append((name, target_name))

    return renamed, skipped, conflicts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rename old nf_* logs into BIDS event names."
    )
    parser.add_argument(
        "--directory",
        "-d",
        type=Path,
        default=Path("/Users/karl/Downloads/download-2/AL_restructured"),
        help="Directory that contains the source TSV logs.",
    )
    parser.add_argument(
        "--legacy-task",
        default="AL",
        help="Legacy code used for detection (e.g., AL, OL, AR).",
    )
    parser.add_argument(
        "--bids-task",
        help="Target BIDS task label (defaults to lowercase legacy task).",
    )
    parser.add_argument(
        "--subject-prefix",
        default="13210",
        help="Static prefix used in the BIDS subject code (e.g., 13210).",
    )
    parser.add_argument(
        "--no-run",
        dest="include_run",
        action="store_false",
        help="Skip appending run-<n> to the filename.",
    )
    parser.add_argument(
        "--session",
        type=int,
        help="Override the parsed session number.",
    )
    parser.set_defaults(include_run=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    directory = args.directory.expanduser()
    if not directory.is_dir():
        raise SystemExit(f"{directory} is not a directory")

    legacy_task = args.legacy_task
    bids_task = args.bids_task or legacy_task.lower()

    renamed, skipped, conflicts = rename_event_files(
        directory,
        legacy_task,
        bids_task,
        args.subject_prefix,
        args.include_run,
        args.session,
    )

    if renamed:
        print(f"renamed {len(renamed)} files:")
        for old, new in renamed[:10]:
            print("  ", old, "->", new)
        if len(renamed) > 10:
            print(f"  ...plus {len(renamed) - 10} more")
    else:
        print("renamed 0 files")

    if skipped:
        print(f"skipped {len(skipped)} files (first 5): {skipped[:5]}")
    if conflicts:
        print(f"conflicts {len(conflicts)}:")
        for old, new in conflicts:
            print("  ", old, "->", new)

    print(f"completed {len(renamed)} renames in {directory}")


if __name__ == "__main__":
    main()
