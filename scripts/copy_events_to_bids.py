#!/usr/bin/env python3
"""Copy BIDS event TSVs into matching subjects/sessions in a BIDS tree."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
import re
import sys

EVENT_RE = re.compile(
    r"sub-(?P<subject>\d+)_ses-(?P<session>\d+)_task-(?P<task>[^_]+)(?:_run-(?P<run>\d+))?_events\.tsv$"
)


def find_func_match(func_dir: Path, subject: str, session: str, task: str, run: str | None) -> bool:
    pattern = f"sub-{subject}_ses-{session}_task-{task}"
    if run is not None:
        pattern += f"_run-{run}"
    pattern += "*_bold.*"
    return any(func_dir.glob(pattern))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy event TSVs into a BIDS dataset after validating existing subject/session/task/run."
    )
    parser.add_argument(
        "--events-dir",
        "-e",
        type=Path,
        required=True,
        help="Flat directory that contains the renamed events TSV files.",
    )
    parser.add_argument(
        "--bids-root",
        "-b",
        type=Path,
        required=True,
        help="Root of the BIDS dataset where sub-<xx>/ses-<yy>/func/ live.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report what would be copied.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    events_dir = args.events_dir.expanduser()
    bids_root = args.bids_root.expanduser()

    if not events_dir.is_dir():
        raise SystemExit(f"{events_dir} is not a directory")
    if not bids_root.is_dir():
        raise SystemExit(f"{bids_root} is not a directory")

    copied = 0
    issues: list[str] = []
    skipped: list[str] = []

    for event_file in sorted(events_dir.glob("*.tsv")):
        match = EVENT_RE.match(event_file.name)
        if not match:
            skipped.append(f"{event_file.name}: does not match expected pattern")
            continue

        subject = match.group("subject")
        session = match.group("session")
        task = match.group("task")
        run = match.group("run")

        subject_dir = bids_root / f"sub-{subject}"
        if not subject_dir.is_dir():
            issues.append(f"{event_file.name}: missing subject directory {subject_dir}")
            continue

        session_dir = subject_dir / f"ses-{session}"
        if not session_dir.is_dir():
            issues.append(f"{event_file.name}: missing session directory {session_dir}")
            continue

        func_dir = session_dir / "func"
        if not func_dir.is_dir():
            issues.append(f"{event_file.name}: missing func/ directory in {session_dir}")
            continue

        if not find_func_match(func_dir, subject, session, task, run):
            issues.append(
                f"{event_file.name}: no matching _bold file for task={task} run={run or 'n/a'} in {func_dir}"
            )
            continue

        target = func_dir / event_file.name
        if target.exists():
            issues.append(f"{event_file.name}: target already exists at {target}")
            continue

        if args.dry_run:
            print(f"[dry-run] copy {event_file} -> {target}")
        else:
            shutil.copy2(event_file, target)
        copied += 1

    print(f"copied {copied} files into {bids_root}")
    if skipped:
        print("skipped files:")
        for msg in skipped:
            print("  ", msg)
    if issues:
        print("issues encountered:")
        for msg in issues:
            print("  ", msg)
        sys.exit(1 if copied == 0 else 0)


if __name__ == "__main__":
    main()
