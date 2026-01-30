#!/usr/bin/env python3
"""
PRISM DataLad - DataLad operations for BIDS datasets

Handles DataLad dataset detection, data retrieval, and result saving.
Shared between local and HPC execution modes.

Author: BIDS Apps Runner Team (PRISM Edition)
Version: 3.0.0
"""

import os
import glob
import logging
import subprocess
from pathlib import Path
from typing import Optional


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
    datalad_dir = os.path.join(path, '.datalad')
    if os.path.isdir(datalad_dir):
        config_file = os.path.join(datalad_dir, 'config')
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
            ['datalad', '--version'],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
        return False


def run_datalad_command(cmd: list, cwd: Optional[str] = None, dry_run: bool = False) -> bool:
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
            cmd,
            check=True,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=300
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


def get_subject_data(bids_dir: str, subject: str, dry_run: bool = False) -> bool:
    """Get subject data using DataLad if dataset is detected.
    
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
    if not subject.startswith('sub-'):
        subject = f'sub-{subject}'
    
    logging.info(f"Getting DataLad data for subject: {subject}")
    
    # Get subject data
    subject_pattern = os.path.join(bids_dir, subject)
    cmd = ["datalad", "get", subject_pattern]
    
    if not run_datalad_command(cmd, cwd=bids_dir, dry_run=dry_run):
        logging.warning(f"Could not get data for {subject}, continuing anyway")
    
    # Also try to get derivatives if they exist
    derivatives_pattern = os.path.join(bids_dir, "derivatives", "*", subject)
    if glob.glob(derivatives_pattern):
        cmd = ["datalad", "get", derivatives_pattern]
        run_datalad_command(cmd, cwd=bids_dir, dry_run=dry_run)
    
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
    if not subject.startswith('sub-'):
        subject = f'sub-{subject}'
    
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


def push_dataset(dataset_dir: str, remote: str = "origin", dry_run: bool = False) -> bool:
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
