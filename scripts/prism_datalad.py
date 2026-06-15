#!/usr/bin/env python3
"""
PRISM DataLad - DataLad operations for BIDS datasets

Handles DataLad dataset detection, data retrieval, and result saving.
Shared between local and HPC execution modes.

Author: BIDS Apps Runner Team (PRISM Edition)
Version: 3.1.0
"""

import os
import re
import glob
import logging
import subprocess
from typing import Optional

# Root-level BIDS files that most pipelines need (always fetched before per-subject data).
_BIDS_ROOT_FILES = [
    "dataset_description.json",
    "participants.tsv",
    "participants.json",
    ".bidsignore",
    "README",
    "CHANGES",
    "task-*_bold.json",
    "task-*_events.json",
    "*_T1w.json",
    "*_T2w.json",
]

# Canonical OpenNeuro GitHub organisation prefix
_OPENNEURO_GITHUB_PREFIX = "https://github.com/OpenNeuroDatasets/"


def is_datalad_dataset(path: str) -> bool:
    """Check if a path is a DataLad dataset.

    Args:
        path: Path to check

    Returns:
        True if path is a DataLad dataset, False otherwise
    """
    if not os.path.isdir(path):
        return False

    # Check for .datalad directory
    datalad_dir = os.path.join(path, ".datalad")
    if os.path.isdir(datalad_dir):
        config_file = os.path.join(datalad_dir, "config")
        if os.path.isfile(config_file):
            return True

    return False


def check_datalad_available() -> bool:
    """Check if DataLad is available in the system.

    Returns:
        True if DataLad is available, False otherwise
    """
    try:
        result = subprocess.run(
            ["datalad", "--version"], capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
        return False


def run_datalad_command(
    cmd: list, cwd: Optional[str] = None, dry_run: bool = False
) -> bool:
    """Execute a DataLad command with error handling.

    Args:
        cmd: Command to execute as list
        cwd: Working directory for command execution
        dry_run: If True, only log the command without executing

    Returns:
        True if successful, False otherwise
    """
    if dry_run:
        logging.info(f"DRY RUN - Would execute DataLad command: {' '.join(cmd)}")
        return True

    try:
        logging.debug(f"Running DataLad command: {' '.join(cmd)}")
        result = subprocess.run(
            cmd, check=True, capture_output=True, text=True, cwd=cwd, timeout=300
        )

        if result.stdout:
            logging.debug(f"DataLad stdout: {result.stdout}")
        if result.stderr:
            logging.debug(f"DataLad stderr: {result.stderr}")

        return True

    except subprocess.CalledProcessError as e:
        logging.warning(f"DataLad command failed: {' '.join(cmd)}")
        logging.warning(f"Error: {e.stderr if e.stderr else str(e)}")
        return False
    except subprocess.TimeoutExpired:
        logging.warning(f"DataLad command timed out: {' '.join(cmd)}")
        return False
    except Exception as e:
        logging.warning(f"Unexpected error running DataLad command: {e}")
        return False


def get_bids_root_files(bids_dir: str, dry_run: bool = False) -> bool:
    """Fetch root-level BIDS metadata files from a DataLad dataset.

    These files (dataset_description.json, participants.tsv, etc.) are small
    but required by all BIDS apps.  They must be downloaded before any
    per-subject processing begins.

    Args:
        bids_dir: BIDS dataset root (must be a DataLad dataset)
        dry_run:  If True only log, do not execute

    Returns:
        True (errors are warned, not fatal)
    """
    if not is_datalad_dataset(bids_dir):
        return True

    if not check_datalad_available():
        return True

    targets = []
    for pattern in _BIDS_ROOT_FILES:
        matches = glob.glob(os.path.join(bids_dir, pattern))
        targets.extend(matches)

    if not targets:
        logging.debug("No root-level BIDS files to retrieve from DataLad")
        return True

    logging.info(f"Fetching {len(targets)} root-level BIDS file(s) from DataLad")
    cmd = ["datalad", "get"] + targets
    if not run_datalad_command(cmd, cwd=bids_dir, dry_run=dry_run):
        logging.warning("Could not fetch some root-level BIDS files; continuing anyway")

    return True


def get_subject_data(bids_dir: str, subject: str, dry_run: bool = False) -> bool:
    """Get subject data using DataLad if dataset is detected.

    Fetches root-level BIDS metadata first (dataset_description.json, etc.),
    then downloads only the subject's directory.  This means the rest of the
    dataset remains as lightweight git-annex pointers until needed.

    Args:
        bids_dir: BIDS dataset directory
        subject: Subject ID (with or without 'sub-' prefix)
        dry_run: If True, only log without executing

    Returns:
        True if successful or not needed, False on error
    """
    if not is_datalad_dataset(bids_dir):
        return True  # Not a DataLad dataset, no action needed

    if not check_datalad_available():
        logging.warning("DataLad not available, skipping data retrieval")
        return True

    # Ensure subject has 'sub-' prefix
    if not subject.startswith("sub-"):
        subject = f"sub-{subject}"

    # Always make sure root-level BIDS files are present first.
    get_bids_root_files(bids_dir, dry_run=dry_run)

    logging.info(f"Getting DataLad data for subject: {subject}")

    # -r recurses into sub-datasets (e.g. when the dataset uses nested DataLad datasets)
    subject_dir = os.path.join(bids_dir, subject)
    cmd = ["datalad", "get", "-r", subject_dir]

    if not run_datalad_command(cmd, cwd=bids_dir, dry_run=dry_run):
        logging.warning(f"Could not get data for {subject}, continuing anyway")

    return True


def save_results(output_dir: str, subject: str, dry_run: bool = False) -> bool:
    """Save processing results using DataLad if dataset is detected.

    Args:
        output_dir: Output directory (derivatives folder)
        subject: Subject ID (with or without 'sub-' prefix)
        dry_run: If True, only log without executing

    Returns:
        True if successful or not needed, False on error
    """
    if not is_datalad_dataset(output_dir):
        return True  # Not a DataLad dataset, no action needed

    if not check_datalad_available():
        logging.warning("DataLad not available, skipping result saving")
        return True

    # Ensure subject has 'sub-' prefix
    if not subject.startswith("sub-"):
        subject = f"sub-{subject}"

    logging.info(f"Saving DataLad results for subject: {subject}")

    # Save results
    cmd = ["datalad", "save", "-m", f"Add results for {subject}"]

    if not run_datalad_command(cmd, cwd=output_dir, dry_run=dry_run):
        logging.warning(f"Could not save results for {subject}")
        return False

    return True


def unlock_dataset(dataset_dir: str, dry_run: bool = False) -> bool:
    """Unlock a DataLad dataset for writing.

    Args:
        dataset_dir: Dataset directory to unlock
        dry_run: If True, only log without executing

    Returns:
        True if successful or not needed, False on error
    """
    if not is_datalad_dataset(dataset_dir):
        return True  # Not a DataLad dataset, no action needed

    if not check_datalad_available():
        logging.warning("DataLad not available, skipping unlock")
        return True

    logging.info(f"Unlocking DataLad dataset: {dataset_dir}")

    cmd = ["datalad", "unlock", dataset_dir]
    return run_datalad_command(cmd, cwd=dataset_dir, dry_run=dry_run)


def clone_dataset(source_url: str, target_dir: str, dry_run: bool = False) -> bool:
    """Clone a DataLad dataset from a URL.

    Args:
        source_url: Source URL to clone from
        target_dir: Target directory for clone
        dry_run: If True, only log without executing

    Returns:
        True if successful, False on error
    """
    if not check_datalad_available():
        logging.error("DataLad not available, cannot clone")
        return False

    logging.info(f"Cloning DataLad dataset from {source_url} to {target_dir}")

    cmd = ["datalad", "clone", source_url, target_dir]
    return run_datalad_command(cmd, dry_run=dry_run)


def resolve_openneuro_url(dataset_id_or_url: str) -> str:
    """Resolve an OpenNeuro dataset ID or URL to a clonable GitHub URL.

    Accepted forms:
      - "ds005239"                                     → full GitHub URL
      - "https://openneuro.org/datasets/ds005239"      → GitHub URL
      - "https://github.com/OpenNeuroDatasets/ds005239" → returned as-is

    Args:
        dataset_id_or_url: Dataset identifier or URL

    Returns:
        Clonable GitHub URL

    Raises:
        ValueError: If the input cannot be resolved to a valid URL
    """
    value = dataset_id_or_url.strip()

    # Already a GitHub OpenNeuroDatasets URL
    if value.startswith("https://github.com/OpenNeuroDatasets/"):
        return value

    # openneuro.org dataset URL  →  extract accession number
    m = re.search(r"openneuro\.org/datasets/(ds\d+)", value, re.IGNORECASE)
    if m:
        return f"{_OPENNEURO_GITHUB_PREFIX}{m.group(1)}"

    # Bare accession number like "ds005239"
    m = re.fullmatch(r"(ds\d{6})", value, re.IGNORECASE)
    if m:
        return f"{_OPENNEURO_GITHUB_PREFIX}{m.group(1)}"

    # Generic git URL — return as-is and let DataLad handle errors
    if value.startswith(("https://", "http://", "git@", "ssh://")):
        return value

    raise ValueError(
        f"Cannot resolve '{value}' to an OpenNeuro dataset URL. "
        "Provide an accession number like 'ds005239', an openneuro.org URL, "
        "or a direct GitHub URL."
    )


def clone_openneuro_dataset(
    dataset_id_or_url: str,
    target_dir: str,
    dry_run: bool = False,
) -> bool:
    """Clone an OpenNeuro dataset (via DataLad) without downloading imaging data.

    Only the git history and git-annex key database are cloned; actual files
    remain as lightweight pointers until ``get_subject_data()`` is called.

    Args:
        dataset_id_or_url: OpenNeuro accession number (e.g. "ds005239"),
            openneuro.org URL, or direct GitHub URL.
        target_dir: Local directory where the dataset will be cloned.
            Must not exist yet (DataLad will create it).
        dry_run: If True only log, do not execute.

    Returns:
        True on success, False on error.
    """
    if not check_datalad_available():
        logging.error("DataLad not available — cannot clone OpenNeuro dataset")
        return False

    try:
        source_url = resolve_openneuro_url(dataset_id_or_url)
    except ValueError as exc:
        logging.error(str(exc))
        return False

    logging.info(f"Cloning OpenNeuro dataset: {source_url} → {target_dir}")

    # datalad clone fetches only metadata; imaging data stays remote.
    cmd = ["datalad", "clone", source_url, target_dir]
    if not run_datalad_command(cmd, dry_run=dry_run):
        logging.error(f"Failed to clone {source_url}")
        return False

    if not dry_run and not is_datalad_dataset(target_dir):
        logging.error(f"Clone succeeded but {target_dir} is not a DataLad dataset")
        return False

    logging.info(
        f"Dataset cloned to {target_dir}. "
        "Imaging data will be downloaded on demand per subject."
    )
    return True


def push_dataset(
    dataset_dir: str, remote: str = "origin", dry_run: bool = False
) -> bool:
    """Push DataLad dataset changes to remote.

    Args:
        dataset_dir: Dataset directory to push
        remote: Remote name (default: origin)
        dry_run: If True, only log without executing

    Returns:
        True if successful or not needed, False on error
    """
    if not is_datalad_dataset(dataset_dir):
        return True  # Not a DataLad dataset, no action needed

    if not check_datalad_available():
        logging.warning("DataLad not available, skipping push")
        return True

    logging.info(f"Pushing DataLad dataset to {remote}")

    cmd = ["datalad", "push", "--to", remote]
    return run_datalad_command(cmd, cwd=dataset_dir, dry_run=dry_run)
