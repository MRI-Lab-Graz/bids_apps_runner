#!/usr/bin/env python3
"""
PRISM Core - Shared utilities for BIDS app execution

Contains configuration validation, logging setup, and common functions
used by both local and HPC execution modes.

Author: BIDS Apps Runner Team (PRISM Edition)
Version: 3.0.0
"""

import os
import sys
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional


def setup_logging(log_level: str = "INFO", log_dir: Optional[Path] = None) -> Path:
    """Setup logging configuration with optional custom log directory.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_dir: Optional custom log directory (default: ./logs)

    Returns:
        Path to the created log file
    """
    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
    else:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)

    log_file = log_dir / f'prism_runner_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'

    # Setup logging with both file and console output
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(log_file)],
    )

    logging.info(f"Logging to file: {log_file}")
    return log_file


def read_config(config_path: str) -> Dict[str, Any]:
    """Read and parse JSON configuration file.

    Args:
        config_path: Path to JSON config file

    Returns:
        Dictionary containing configuration

    Raises:
        FileNotFoundError: If config file doesn't exist
        json.JSONDecodeError: If config file is invalid JSON
    """
    config_file = Path(config_path)

    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_file, "r") as f:
        config = json.load(f)

    # Handle project.json format (GUI creates configs nested under "config" key)
    if "config" in config and isinstance(config["config"], dict):
        if "common" in config["config"]:
            logging.debug("Detected project.json format, extracting nested config")
            config = config["config"]

    logging.info(f"Loaded configuration from: {config_path}")
    return config


def detect_execution_mode(
    config: Dict[str, Any], force_mode: Optional[str] = None
) -> str:
    """Auto-detect execution mode from configuration.

    Args:
        config: Configuration dictionary
        force_mode: Optional forced mode ('local' or 'hpc')

    Returns:
        Execution mode: 'local' or 'hpc'
    """
    if force_mode:
        logging.info(f"Execution mode forced to: {force_mode}")
        return force_mode

    # Auto-detect based on config structure
    if "hpc" in config and config["hpc"]:
        mode = "hpc"
        logging.info("Auto-detected HPC mode (found 'hpc' section in config)")
    else:
        mode = "local"
        logging.info("Auto-detected local mode (no 'hpc' section in config)")

    return mode


def validate_common_config(config: Dict[str, Any]) -> None:
    """Validate common configuration sections.

    Args:
        config: Configuration dictionary

    Raises:
        ValueError: If configuration is invalid
    """
    if "common" not in config:
        raise ValueError("Config missing 'common' section")

    common = config["common"]

    # Container validation (if specified and not empty)
    if "container" in common and common["container"]:
        container = Path(common["container"])
        if container.exists() and not container.is_file():
            raise ValueError(f"Container is not a file: {container}")

    logging.debug("Common configuration validated successfully")


def validate_app_config(config: Dict[str, Any]) -> None:
    """Validate app configuration section.

    Args:
        config: Configuration dictionary

    Raises:
        ValueError: If configuration is invalid
    """
    if "app" not in config:
        raise ValueError("Config missing 'app' section")

    app = config["app"]

    # Analysis level validation
    if "analysis_level" not in app:
        raise ValueError("App config missing 'analysis_level'")

    if app["analysis_level"] not in ["participant", "group"]:
        raise ValueError(f"Invalid analysis_level: {app['analysis_level']}")

    logging.debug("App configuration validated successfully")


def validate_hpc_config(config: Dict[str, Any]) -> None:
    """Validate HPC-specific configuration.

    Args:
        config: Configuration dictionary

    Raises:
        ValueError: If HPC configuration is invalid
    """
    if "hpc" not in config:
        raise ValueError("HPC mode requires 'hpc' section in config")

    hpc = config["hpc"]
    required_fields = ["partition", "time", "mem", "cpus"]

    for field in required_fields:
        if field not in hpc:
            raise ValueError(f"HPC config missing required field: {field}")

    logging.debug("HPC configuration validated successfully")


def fix_system_path() -> None:
    """Ensure common paths are in PATH environment variable."""
    extra_paths = [
        "/usr/local/bin",
        "/opt/homebrew/bin",
        "/opt/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]

    current_path = os.environ.get("PATH", "").split(os.pathsep)
    path_changed = False

    for p in extra_paths:
        if p not in current_path and os.path.exists(p):
            current_path.append(p)
            path_changed = True

    if path_changed:
        os.environ["PATH"] = os.pathsep.join(current_path)
        logging.debug(f"Updated PATH: {os.environ['PATH']}")


def get_subjects_from_bids(bids_folder: str, dry_run: bool = False) -> list:
    """Get list of subjects from BIDS folder.

    Args:
        bids_folder: Path to BIDS dataset
        dry_run: If True, don't fail on missing folder

    Returns:
        List of subject IDs (without 'sub-' prefix)
    """
    bids_path = Path(bids_folder)

    if not bids_path.exists():
        if dry_run:
            logging.warning(f"BIDS folder not found: {bids_folder} (dry-run mode)")
            return []
        raise FileNotFoundError(f"BIDS folder not found: {bids_folder}")

    subjects = []
    for sub_dir in sorted(bids_path.glob("sub-*")):
        if sub_dir.is_dir():
            subjects.append(sub_dir.name[4:])  # Remove 'sub-' prefix

    logging.info(f"Found {len(subjects)} subjects in BIDS folder")
    return subjects


def print_summary(processed: list, failed: list, total_time: float) -> None:
    """Print execution summary.

    Args:
        processed: List of successfully processed subjects
        failed: List of failed subjects
        total_time: Total execution time in seconds
    """
    print("\n" + "=" * 60)
    print("EXECUTION SUMMARY")
    print("=" * 60)
    print(f"Successfully processed: {len(processed)} subjects")
    if processed:
        print(f"  {', '.join(processed)}")

    if failed:
        print(f"\nFailed: {len(failed)} subjects")
        print(f"  {', '.join(failed)}")

    print(f"\nTotal time: {total_time:.2f} seconds ({total_time/60:.1f} minutes)")
    print("=" * 60)


def run_command(
    cmd: list,
    capture_output: bool = True,
    check: bool = True,
    dry_run: bool = False,
    cwd: Optional[str] = None,
) -> Any:
    """Run a shell command with error handling.

    Args:
        cmd: Command as list of strings
        capture_output: Whether to capture stdout/stderr
        check: Whether to raise exception on non-zero exit
        dry_run: If True, only log the command without executing
        cwd: Working directory for command execution

    Returns:
        CompletedProcess object or None if dry_run
    """
    import subprocess

    if dry_run:
        logging.info(f"DRY RUN - Would execute: {' '.join(cmd)}")
        return None

    try:
        result = subprocess.run(
            cmd, capture_output=capture_output, text=True, check=check, cwd=cwd
        )
        return result
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed: {' '.join(cmd)}")
        logging.error(f"Exit code: {e.returncode}")
        if e.stdout:
            logging.error(f"Stdout: {e.stdout}")
        if e.stderr:
            logging.error(f"Stderr: {e.stderr}")
        raise
    except Exception as e:
        logging.error(f"Error running command: {e}")
        raise
