#!/usr/bin/env python3
"""
BIDS App Runner - Production Version

A robust and user-friendly tool for running BIDS Apps with comprehensive
error handling, logging, and configuration validation.

Author: BIDS Apps Runner Team
Version: 2.0.0
"""

import os
import sys
import json
import glob
import shutil
import random
import argparse
import subprocess
import multiprocessing
import concurrent.futures
import logging
import signal
import time
import re
from datetime import datetime
from pathlib import Path
from typing import Set, Optional

# Import validation integration
try:
    from bids_validation_integration import (
        BIDSAppIntegratedValidator, 
        validate_and_generate_reprocess_config,
        print_validation_summary
    )
    VALIDATION_AVAILABLE = True
except ImportError:
    logging.warning("Validation integration not available - validation features disabled")
    VALIDATION_AVAILABLE = False
from pathlib import Path

def is_datalad_dataset(path):
    """Check if a path is a DataLad dataset."""
    if not os.path.isdir(path):
        return False
    
    # Check for .datalad directory
    datalad_dir = os.path.join(path, '.datalad')
    if os.path.isdir(datalad_dir):
        config_file = os.path.join(datalad_dir, 'config')
        if os.path.isfile(config_file):
            return True
    
    return False

def check_datalad_available():
    """Check if DataLad is available in the system."""
    try:
        result = subprocess.run(['datalad', '--version'], 
                              capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
        return False

def run_datalad_command(cmd, cwd=None, dry_run=False):
    """Execute a DataLad command with error handling."""
    if dry_run:
        logging.info(f"DRY RUN - Would execute DataLad command: {' '.join(cmd)}")
        return True
    
    try:
        logging.debug(f"Running DataLad command: {' '.join(cmd)}")
        result = subprocess.run(cmd, check=True, capture_output=True, 
                              text=True, cwd=cwd, timeout=300)
        
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

def get_subject_data_datalad(bids_dir, subject, dry_run=False):
    """Get subject data using DataLad if dataset is detected."""
    if not is_datalad_dataset(bids_dir):
        return True  # Not a DataLad dataset, no action needed
    
    if not check_datalad_available():
        logging.warning("DataLad not available, skipping data retrieval")
        return True
    
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

def save_results_datalad(output_dir, subject, dry_run=False):
    """Save processing results using DataLad if dataset is detected."""
    if not is_datalad_dataset(output_dir):
        return True  # Not a DataLad dataset, no action needed
    
    if not check_datalad_available():
        logging.warning("DataLad not available, skipping result saving")
        return True
    
    logging.info(f"Saving DataLad results for subject: {subject}")
    
    # Save results
    cmd = ["datalad", "save", "-m", f"Add results for {subject}"]
    
    if not run_datalad_command(cmd, cwd=output_dir, dry_run=dry_run):
        logging.warning(f"Could not save results for {subject}")
        return False
    
    return True

def setup_logging(log_level="INFO"):
    """Setup logging configuration."""
    # Create logs directory if it doesn't exist
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    log_file = log_dir / f'bids_app_runner_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
    
    # Setup logging with both file and console output
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file)
        ]
    )
    
    # Log the log file location
    logging.info(f"Logging to file: {log_file}")
    
    return log_file


def parse_missing_subjects_from_json(json_file: Path, pipeline_filter: Optional[str] = None) -> Set[str]:
    """Parse missing subjects from external JSON report file.
    
    Supports JSON format from external BIDS app checking tools with structure:
    {
        "pipelines": {
            "pipeline_name": {
                "subjects": ["sub-001", "sub-002", ...]
            }
        }
    }
    """
    try:
        with open(json_file, 'r') as f:
            data = json.load(f)
    except Exception as e:
        raise ValueError(f"Could not read JSON file {json_file}: {e}")
    
    missing_subjects = set()
    
    # Handle the format from external BIDS app checking tools
    if 'pipelines' in data:
        pipelines = data['pipelines']
        
        # If pipeline filter specified, only use that pipeline
        if pipeline_filter:
            if pipeline_filter in pipelines:
                pipeline_data = pipelines[pipeline_filter]
                if 'subjects' in pipeline_data:
                    missing_subjects.update(pipeline_data['subjects'])
                else:
                    logging.warning(f"No 'subjects' field found in pipeline '{pipeline_filter}'")
            else:
                available_pipelines = list(pipelines.keys())
                raise ValueError(f"Pipeline '{pipeline_filter}' not found in JSON. Available: {available_pipelines}")
        else:
            # No filter - collect subjects from all pipelines
            for pipeline_name, pipeline_data in pipelines.items():
                if 'subjects' in pipeline_data:
                    missing_subjects.update(pipeline_data['subjects'])
                    logging.info(f"Found {len(pipeline_data['subjects'])} missing subjects in {pipeline_name}")
    
    # Also handle the format from check_app_output.py --output-json
    elif 'missing_data_by_pipeline' in data:
        pipelines = data['missing_data_by_pipeline']
        
        if pipeline_filter:
            if pipeline_filter in pipelines:
                pipeline_data = pipelines[pipeline_filter]
                if 'subjects_with_missing_data' in pipeline_data:
                    missing_subjects.update(pipeline_data['subjects_with_missing_data'])
            else:
                available_pipelines = list(pipelines.keys())
                raise ValueError(f"Pipeline '{pipeline_filter}' not found in JSON. Available: {available_pipelines}")
        else:
            for pipeline_name, pipeline_data in pipelines.items():
                if 'subjects_with_missing_data' in pipeline_data:
                    missing_subjects.update(pipeline_data['subjects_with_missing_data'])
                    logging.info(f"Found {len(pipeline_data['subjects_with_missing_data'])} missing subjects in {pipeline_name}")
    
    # Handle direct subject list format
    elif 'all_missing_subjects' in data:
        missing_subjects.update(data['all_missing_subjects'])
    
    else:
        raise ValueError("Unsupported JSON format. Expected 'pipelines', 'missing_data_by_pipeline', or 'all_missing_subjects' fields.")
    
    if not missing_subjects:
        logging.info("No missing subjects found in JSON file")
    else:
        logging.info(f"Parsed {len(missing_subjects)} missing subjects from {json_file}")
    
    return missing_subjects


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run a BIDS App using a JSON config file (with DataLad auto-detection)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -x config.json                                    # Standard BIDS folder
  %(prog)s -x config.json                                    # DataLad dataset (auto-detected)
  %(prog)s -x config.json --dry-run                          # Test configuration
  %(prog)s -x config.json --log-level DEBUG                  # Verbose logging
  %(prog)s -x config.json --subjects sub-001 sub-002         # Specific subjects
  %(prog)s -x config.json --debug                            # Enable detailed container logs
  %(prog)s -x config.json --debug --subjects sub-001         # Debug single subject
  %(prog)s -x config.json --force                            # Force reprocessing
  %(prog)s -x config.json --pilot                            # Pilot mode: process one random subject
  %(prog)s -x config.json --from-json missing.json           # Reprocess subjects from JSON report (--force auto-enabled)
  %(prog)s -x config.json --from-json missing.json --pipeline qsiprep  # Specific pipeline from JSON
  
Validation Examples:
  %(prog)s -x config.json --validate                         # Validate outputs after processing
  %(prog)s -x config.json --validate-only                    # Only validate, don't process
  %(prog)s -x config.json --reprocess-missing                # Auto-reprocess missing subjects
  %(prog)s -x config.json --validate --validation-output-dir reports  # Custom reports directory
  
DataLad Integration:
  - Automatically detects DataLad datasets in input/output folders
  - Performs 'datalad get' for required data before processing
  - Saves results with 'datalad save' after successful processing
  - Works seamlessly with standard BIDS folders when DataLad not detected
  
For more information, see README_STANDARD.md
        """
    )
    
    parser.add_argument(
        "-x", "--config", 
        required=True, 
        help="Path to JSON config file"
    )
    
    parser.add_argument(
        "--log-level", 
        default="INFO", 
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level (default: INFO)"
    )
    
    parser.add_argument(
        "--dry-run", 
        action="store_true", 
        help="Show commands that would be run without executing them"
    )
    
    parser.add_argument(
        "--subjects", 
        nargs="+", 
        help="Process only specified subjects (e.g., sub-001 sub-002)"
    )
    
    parser.add_argument(
        "--force", 
        action="store_true", 
        help="Force reprocessing of subjects even if output exists"
    )
    
    parser.add_argument(
        "--debug", 
        action="store_true", 
        help="Enable debug mode with detailed container execution logs"
    )
    
    parser.add_argument(
        "--version", 
        action="version", 
        version="BIDS App Runner 2.0.0"
    )
    
    parser.add_argument(
        "--clean-success-markers", 
        action="store_true", 
        help="Remove all success markers (forces fresh detection of already processed subjects)"
    )
    
    parser.add_argument(
        "--pilot", 
        action="store_true", 
        help="Run in pilot mode (process only one randomly selected subject, forces jobs=1)"
    )
    
    parser.add_argument(
        "--validate", 
        action="store_true", 
        help="Validate outputs after processing and generate reports"
    )
    
    parser.add_argument(
        "--validate-only", 
        action="store_true", 
        help="Only validate outputs, skip processing"
    )
    
    parser.add_argument(
        "--reprocess-missing", 
        action="store_true", 
        help="Automatically reprocess subjects with missing outputs (implies --validate)"
    )
    
    parser.add_argument(
        "--validation-output-dir", 
        default="validation_reports", 
        help="Directory for validation reports and reprocess configs (default: validation_reports)"
    )
    
    parser.add_argument(
        "--from-json", 
        type=Path,
        help="Parse missing subjects from external JSON report file and process them (automatically enables --force)"
    )
    
    parser.add_argument(
        "--pipeline", 
        help="When using --from-json, specify which pipeline to extract subjects from (if not specified, uses all pipelines)"
    )
    
    return parser.parse_args()

def read_config(path):
    """Read and validate JSON configuration file."""
    try:
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")
        
        with open(config_path, "r") as f:
            config = json.load(f)
        
        logging.info(f"Successfully loaded config from: {config_path}")
        return config
        
    except json.JSONDecodeError as e:
        logging.error(f"Invalid JSON in config file: {e}")
        sys.exit(f"Error parsing config file: {e}")
    except Exception as e:
        logging.error(f"Error reading config file: {e}")
        sys.exit(f"Error reading config: {e}")

def validate_app_config(app):
    """Validate app-specific configuration."""
    logging.info("Validating app configuration...")
    
    # Check if apptainer_args is a list if provided
    if "apptainer_args" in app and not isinstance(app["apptainer_args"], list):
        sys.exit("ERROR: 'apptainer_args' must be a list")
    
    # Check if options is a list if provided
    if "options" in app and not isinstance(app["options"], list):
        sys.exit("ERROR: 'options' must be a list")
    
    # Check if mounts is a list of dictionaries if provided
    if "mounts" in app:
        if not isinstance(app["mounts"], list):
            sys.exit("ERROR: 'mounts' must be a list")
        for i, mount in enumerate(app["mounts"]):
            if not isinstance(mount, dict):
                sys.exit(f"ERROR: Mount {i} must be a dictionary")
            if "source" not in mount or "target" not in mount:
                sys.exit(f"ERROR: Mount {i} must have 'source' and 'target' keys")
            if not os.path.exists(mount["source"]):
                logging.warning(f"Mount source does not exist: {mount['source']}")
    
    # Validate output_check structure if provided
    if "output_check" in app:
        if not isinstance(app["output_check"], dict):
            sys.exit("ERROR: 'output_check' must be a dictionary")
        if "pattern" not in app["output_check"]:
            logging.warning("'output_check' defined but no 'pattern' specified - output checking disabled")
    
    # Validate analysis_level
    valid_levels = ["participant", "group", "session"]
    level = app.get("analysis_level", "participant")
    if level not in valid_levels:
        logging.warning(f"Unknown analysis level '{level}', using 'participant'")
        app["analysis_level"] = "participant"
    
    logging.info("App configuration validation completed")

def validate_common_config(cfg):
    """Validate common configuration section."""
    logging.info("Validating common configuration...")
    
    # Check for Apptainer or Singularity
    if not (shutil.which("apptainer") or shutil.which("singularity")):
        logging.error("Neither 'apptainer' nor 'singularity' found in PATH")
        sys.exit("ERROR: Apptainer/Singularity is required but not found")
    
    required = ["bids_folder", "output_folder", "tmp_folder", "container", "templateflow_dir"]
    missing = []
    
    for key in required:
        if key not in cfg:
            missing.append(key)
    
    if missing:
        logging.error(f"Missing required 'common' config keys: {', '.join(missing)}")
        sys.exit(f"ERROR: Missing required 'common' config: {', '.join(missing)}")
    
    # Validate input directories exist
    for key in ["bids_folder", "templateflow_dir"]:
        path = cfg[key]
        if not os.path.isdir(path):
            logging.error(f"Directory not found: {path}")
            sys.exit(f"ERROR: Missing directory '{key}': {path}")
    
    # Create output directories if they don't exist
    for key in ["output_folder", "tmp_folder"]:
        path = cfg[key]
        try:
            os.makedirs(path, exist_ok=True)
            logging.info(f"Created directory: {path}")
        except Exception as e:
            logging.error(f"Cannot create directory '{key}': {path}")
            sys.exit(f"ERROR: Cannot create directory '{key}': {e}")
    
    # Validate container file exists
    container_path = cfg["container"]
    if not os.path.isfile(container_path):
        logging.error(f"Container file not found: {container_path}")
        sys.exit(f"ERROR: Missing container file: {container_path}")
    
    # Validate optional folder if specified
    if "optional_folder" in cfg and cfg["optional_folder"]:
        if not os.path.isdir(cfg["optional_folder"]):
            logging.error(f"Optional folder not found: {cfg['optional_folder']}")
            sys.exit(f"ERROR: Missing optional_folder: {cfg['optional_folder']}")
    
    # Validate jobs parameter
    jobs = cfg.get("jobs", multiprocessing.cpu_count())
    if not isinstance(jobs, int) or jobs < 1:
        logging.warning(f"Invalid jobs value: {jobs}, using default")
        cfg["jobs"] = multiprocessing.cpu_count()
    
    logging.info("Common configuration validation completed")

def get_subject_sessions(subject, bids_folder):
    """Get list of sessions for a subject in BIDS dataset."""
    subject_dir = os.path.join(bids_folder, subject)
    if not os.path.isdir(subject_dir):
        return []
    
    sessions = []
    # Check for session directories
    for item in os.listdir(subject_dir):
        if item.startswith("ses-") and os.path.isdir(os.path.join(subject_dir, item)):
            sessions.append(item)
    
    # If no sessions found, this is a single-session dataset
    if not sessions:
        return [None]  # None represents no session structure
    
    return sorted(sessions)

def subject_processed(subject, common, app, force=False):
    """Check if a subject has already been processed."""
    if force:
        logging.info(f"Force flag enabled - will reprocess {subject} regardless of existing outputs")
        return False
    
    # Strategy 1: Check success marker file (most reliable)
    success_marker = os.path.join(common["output_folder"], ".bids_app_runner", f"{subject}_success.txt")
    if os.path.exists(success_marker):
        logging.info(f"Subject '{subject}' already processed (success marker found)")
        return True
    
    # Strategy 2: Check configured output pattern if available
    pattern = app.get("output_check", {}).get("pattern", "")
    if pattern:
        check_dir = os.path.join(common["output_folder"], app["output_check"].get("directory", ""))
        full_pattern = os.path.join(check_dir, pattern.replace("{subject}", subject))
        matches = glob.glob(full_pattern)
        if matches:
            logging.info(f"Subject '{subject}' already processed (output pattern matched)")
            return True
    
    # Strategy 3: Generic output detection (fallback) - but be conservative
    # Only consider subject processed if we have substantial evidence
    if check_generic_output_exists(subject, common):
        # For critical pipelines, be more strict about what counts as "processed"
        app_name = app.get("image", "").lower()
        if any(critical_app in app_name for critical_app in ["fmriprep", "qsiprep", "freesurfer"]):
            # For critical apps, also check for completion indicators
            if has_completion_indicators(subject, common, app_name):
                logging.info(f"Subject '{subject}' already processed (generic output detection + completion indicators)")
                return True
            else:
                logging.info(f"Subject '{subject}' has partial output but no completion indicators - will reprocess")
                return False
        else:
            logging.info(f"Subject '{subject}' already processed (generic output detection)")
            return True
    
    return False

def check_generic_output_exists(subject, common):
    """Check for generic output patterns that most BIDS apps produce."""
    output_dir = common["output_folder"]
    
    # Common patterns that BIDS apps typically create
    patterns_to_check = [
        # Subject-specific directories
        os.path.join(output_dir, subject),
        os.path.join(output_dir, "derivatives", "*", subject),
        
        # fMRIPrep-style patterns
        os.path.join(output_dir, subject, "func", f"{subject}_*"),
        os.path.join(output_dir, subject, "anat", f"{subject}_*"),
        
        # FreeSurfer-style patterns
        os.path.join(output_dir, subject, "scripts", "*"),
        os.path.join(output_dir, subject, "surf", "*"),
        
        # QSIPrep-style patterns  
        os.path.join(output_dir, subject, "dwi", f"{subject}_*"),
        
        # HTML reports (common across many apps)
        os.path.join(output_dir, f"{subject}.html"),
        os.path.join(output_dir, f"{subject}_report.html"),
        
        # Log files indicating completion
        os.path.join(output_dir, "logs", f"{subject}_*"),
        os.path.join(output_dir, f"{subject}_log.txt"),
    ]
    
    for pattern in patterns_to_check:
        matches = glob.glob(pattern)
        if matches:
            logging.debug(f"Found generic output for {subject}: {matches[0]} (and {len(matches)-1} others)" if len(matches) > 1 else f"Found generic output for {subject}: {matches[0]}")
            return True
    
    # Strategy 4: Check if subject directory exists and is non-empty
    subject_dir = os.path.join(output_dir, subject)
    if os.path.isdir(subject_dir):
        try:
            # Check if directory has any files (recursively)
            for root, dirs, files in os.walk(subject_dir):
                if files:  # Found at least one file
                    logging.debug(f"Found non-empty subject directory for {subject}: {subject_dir}")
                    return True
        except Exception as e:
            logging.debug(f"Error checking subject directory {subject_dir}: {e}")
    
    logging.debug(f"No output found for {subject}")
    return False

def has_completion_indicators(subject, common, app_name):
    """Check for specific completion indicators for critical BIDS apps."""
    output_dir = common["output_folder"]
    
    if "fmriprep" in app_name:
        # fMRIPrep completion indicators
        indicators = [
            os.path.join(output_dir, f"{subject}.html"),  # HTML report
            os.path.join(output_dir, subject, "func", f"{subject}_*desc-preproc_bold.nii*"),  # Preprocessed BOLD
            os.path.join(output_dir, subject, "anat", f"{subject}_*desc-preproc_T1w.nii*"),   # Preprocessed T1w
        ]
    elif "qsiprep" in app_name:
        # QSIPrep completion indicators
        indicators = [
            os.path.join(output_dir, f"{subject}.html"),  # HTML report
            os.path.join(output_dir, subject, "dwi", f"{subject}_*desc-preproc_dwi.nii*"),    # Preprocessed DWI
        ]
    elif "freesurfer" in app_name:
        # FreeSurfer completion indicators
        indicators = [
            os.path.join(output_dir, subject, "scripts", "recon-all.done"),  # Completion marker
            os.path.join(output_dir, subject, "surf", "lh.pial"),            # Surface files
            os.path.join(output_dir, subject, "surf", "rh.pial"),
        ]
    else:
        # For other apps, just check for any substantial output
        indicators = [
            os.path.join(output_dir, f"{subject}.html"),
            os.path.join(output_dir, subject, "*", f"{subject}_*"),
        ]
    
    # Check if at least some key indicators exist
    found_indicators = 0
    for indicator in indicators:
        if glob.glob(indicator):
            found_indicators += 1
    
    # For fMRIPrep/QSIPrep: need HTML report + at least one preprocessed file
    # For FreeSurfer: need recon-all.done
    # For others: need at least one indicator
    if "fmriprep" in app_name or "qsiprep" in app_name:
        return found_indicators >= 2  # HTML + preprocessed data
    elif "freesurfer" in app_name:
        return any(glob.glob(os.path.join(output_dir, subject, "scripts", "recon-all.done")))
    else:
        return found_indicators >= 1

def create_success_marker(subject, common):
    """Create a success marker file for a subject."""
    marker_dir = os.path.join(common["output_folder"], ".bids_app_runner")
    os.makedirs(marker_dir, exist_ok=True)
    
    marker_file = os.path.join(marker_dir, f"{subject}_success.txt")
    try:
        with open(marker_file, 'w') as f:
            f.write(f"Subject {subject} processed successfully\n")
            f.write(f"Timestamp: {datetime.now().isoformat()}\n")
            f.write(f"Runner version: 2.0.0\n")
        logging.debug(f"Created success marker: {marker_file}")
        return True
    except Exception as e:
        logging.warning(f"Could not create success marker for {subject}: {e}")
        return False

def remove_success_marker(subject, common):
    """Remove success marker file for a subject (used when forcing reprocessing)."""
    marker_file = os.path.join(common["output_folder"], ".bids_app_runner", f"{subject}_success.txt")
    try:
        if os.path.exists(marker_file):
            os.remove(marker_file)
            logging.debug(f"Removed success marker: {marker_file}")
    except Exception as e:
        logging.warning(f"Could not remove success marker for {subject}: {e}")

def clean_all_success_markers(common):
    """Remove all success marker files."""
    marker_dir = os.path.join(common["output_folder"], ".bids_app_runner")
    if not os.path.exists(marker_dir):
        logging.info("No success markers directory found")
        return
    
    try:
        marker_files = [f for f in os.listdir(marker_dir) if f.endswith("_success.txt")]
        if not marker_files:
            logging.info("No success marker files found")
            return
        
        for marker_file in marker_files:
            marker_path = os.path.join(marker_dir, marker_file)
            os.remove(marker_path)
            logging.debug(f"Removed success marker: {marker_path}")
        
        logging.info(f"Removed {len(marker_files)} success marker files")
        
        # Try to remove the directory if it's empty
        try:
            os.rmdir(marker_dir)
            logging.debug(f"Removed empty success markers directory: {marker_dir}")
        except OSError:
            pass  # Directory not empty, that's fine
            
    except Exception as e:
        logging.error(f"Error cleaning success markers: {e}")

def build_common_mounts(common, tmp_dir):
    """Build common mount points for the container."""
    mounts = [
        f"{tmp_dir}:/tmp",
        f"{common['templateflow_dir']}:/templateflow",
        f"{common['output_folder']}:/output",
        f"{common['bids_folder']}:/bids"
    ]
    
    if common.get("optional_folder"):
        mounts.append(f"{common['optional_folder']}:/base")
    
    logging.debug(f"Common mounts: {mounts}")
    return mounts

def run_container(cmd, env=None, dry_run=False, debug=False, subject=None, log_dir=None):
    """Execute container command with optional dry run mode and detailed logging."""
    cmd_str = " ".join(cmd)
    
    if dry_run:
        logging.info(f"DRY RUN - Would execute: {cmd_str}")
        return None
    
    logging.info(f"Running command: {cmd_str}")
    
    # Create container log files if debug mode is enabled
    container_log_file = None
    container_error_file = None
    
    if debug and subject and log_dir:
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        container_log_file = os.path.join(log_dir, f"container_{subject}_{timestamp}.log")
        container_error_file = os.path.join(log_dir, f"container_{subject}_{timestamp}.err")
        
        logging.info(f"Debug mode: Container logs will be saved to:")
        logging.info(f"  - stdout: {container_log_file}")
        logging.info(f"  - stderr: {container_error_file}")
    
    try:
        # Create environment with fallback
        run_env = env or os.environ.copy()
        
        if debug:
            # In debug mode, stream output in real-time and save to files
            logging.info("Debug mode: Starting container execution with real-time logging...")
            
            with open(container_log_file, 'w') if container_log_file else open(os.devnull, 'w') as stdout_file, \
                 open(container_error_file, 'w') if container_error_file else open(os.devnull, 'w') as stderr_file:
                
                process = subprocess.Popen(
                    cmd,
                    env=run_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    universal_newlines=True
                )
                
                # Real-time output processing
                stdout_lines = []
                stderr_lines = []
                
                # Read output in real-time
                import select
                import sys
                
                if hasattr(select, 'select'):  # Unix-like systems
                    while process.poll() is None:
                        ready, _, _ = select.select([process.stdout, process.stderr], [], [], 0.1)
                        
                        for stream in ready:
                            if stream == process.stdout:
                                line = stream.readline()
                                if line:
                                    stdout_lines.append(line)
                                    if container_log_file:
                                        stdout_file.write(line)
                                        stdout_file.flush()
                                    logging.debug(f"CONTAINER STDOUT: {line.rstrip()}")
                            
                            elif stream == process.stderr:
                                line = stream.readline()
                                if line:
                                    stderr_lines.append(line)
                                    if container_error_file:
                                        stderr_file.write(line)
                                        stderr_file.flush()
                                    logging.debug(f"CONTAINER STDERR: {line.rstrip()}")
                else:
                    # Fallback for systems without select (Windows)
                    stdout, stderr = process.communicate()
                    stdout_lines = stdout.splitlines(keepends=True) if stdout else []
                    stderr_lines = stderr.splitlines(keepends=True) if stderr else []
                    
                    if container_log_file and stdout:
                        stdout_file.write(stdout)
                    if container_error_file and stderr:
                        stderr_file.write(stderr)
                
                # Wait for process to complete
                return_code = process.wait()
                
                # Create result object
                class DebugResult:
                    def __init__(self, returncode, stdout_lines, stderr_lines):
                        self.returncode = returncode
                        self.stdout = ''.join(stdout_lines)
                        self.stderr = ''.join(stderr_lines)
                
                result = DebugResult(return_code, stdout_lines, stderr_lines)
                
                if return_code != 0:
                    raise subprocess.CalledProcessError(return_code, cmd, result.stdout, result.stderr)
        
        else:
            # Standard mode - capture output but don't stream
            result = subprocess.run(
                cmd, 
                check=True, 
                env=run_env,
                capture_output=True, 
                text=True,
                timeout=None  # No timeout for long-running processes
            )
        
        # Log output based on mode
        if debug:
            logging.info(f"Container execution completed successfully (exit code: {result.returncode})")
            if result.stdout:
                logging.info(f"Container produced {len(result.stdout.splitlines())} lines of stdout")
            if result.stderr:
                logging.info(f"Container produced {len(result.stderr.splitlines())} lines of stderr")
                # In debug mode, always show stderr even if successful
                for line in result.stderr.splitlines():
                    if line.strip():
                        logging.warning(f"CONTAINER STDERR: {line}")
        else:
            # Standard mode - only debug level output
            if result.stdout:
                logging.debug(f"Command stdout: {result.stdout}")
            if result.stderr:
                logging.debug(f"Command stderr: {result.stderr}")
        
        logging.info("Command completed successfully")
        return result
        
    except subprocess.CalledProcessError as e:
        logging.error(f"Container execution failed with exit code {e.returncode}")
        
        if debug:
            logging.error(f"Debug mode: Detailed error information:")
            if container_log_file and os.path.exists(container_log_file):
                logging.error(f"Full stdout log saved to: {container_log_file}")
            if container_error_file and os.path.exists(container_error_file):
                logging.error(f"Full stderr log saved to: {container_error_file}")
                
                # Show last 20 lines of stderr for immediate debugging
                try:
                    with open(container_error_file, 'r') as f:
                        lines = f.readlines()
                        if lines:
                            logging.error("Last 20 lines of container stderr:")
                            for line in lines[-20:]:
                                logging.error(f"  {line.rstrip()}")
                except Exception:
                    pass
        
        # Always show captured output in error case
        if e.stdout:
            logging.error(f"Container stdout: {e.stdout}")
        if e.stderr:
            logging.error(f"Container stderr: {e.stderr}")
        raise
        
    except subprocess.TimeoutExpired as e:
        logging.error(f"Command timed out after {e.timeout} seconds")
        if debug and container_log_file:
            logging.error(f"Partial container logs may be available at: {container_log_file}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error during command execution: {e}")
        raise

def process_subject(subject, common, app, dry_run=False, force=False, debug=False):
    """Process a single subject with comprehensive error handling."""
    logging.info(f"Starting processing for subject: {subject}")
    
    # Check if input is a DataLad dataset
    is_input_datalad = is_datalad_dataset(common["bids_folder"])
    is_output_datalad = is_datalad_dataset(common["output_folder"])
    
    if is_input_datalad or is_output_datalad:
        logging.info("DataLad dataset detected, enabling enhanced features")
        if not check_datalad_available():
            logging.warning("DataLad features requested but DataLad not available")
    
    # Create temporary directory
    tmp_dir = os.path.join(common["tmp_folder"], subject)
    
    # Create debug log directory if in debug mode
    debug_log_dir = None
    if debug:
        debug_log_dir = os.path.join(common.get("log_dir", "logs"), "container_logs")
        os.makedirs(debug_log_dir, exist_ok=True)
        logging.info(f"Debug mode enabled: Container logs will be saved to {debug_log_dir}")
    
    try:
        os.makedirs(tmp_dir, exist_ok=True)
        logging.debug(f"Created temp directory: {tmp_dir}")
        
        # Check if already processed
        if subject_processed(subject, common, app, force):
            logging.info(f"Subject '{subject}' already processed, skipping")
            try:
                shutil.rmtree(tmp_dir)
                logging.debug(f"Cleaned up temp directory: {tmp_dir}")
            except:
                pass
            return True
        
        # Remove existing success marker if forcing reprocessing
        if force:
            remove_success_marker(subject, common)
        
        # Get subject data if DataLad dataset
        if is_input_datalad:
            get_subject_data_datalad(common["bids_folder"], subject, dry_run)
        
        # Build container command
        cmd = ["apptainer", "run"]
        
        # Add app-specific apptainer arguments
        if app.get("apptainer_args"):
            cmd.extend(app["apptainer_args"])
            logging.debug(f"Added apptainer args: {app['apptainer_args']}")
        else:
            cmd.append("--containall")
        
        # Add bind mounts
        for mnt in build_common_mounts(common, tmp_dir):
            cmd.extend(["-B", mnt])
        
        # Add custom mounts
        for mount in app.get("mounts", []):
            if mount.get("source") and mount.get("target"):
                cmd.extend(["-B", f"{mount['source']}:{mount['target']}"])
                logging.debug(f"Added custom mount: {mount['source']}:{mount['target']}")
        
        # Add environment variables
        cmd.extend([
            "--env", f"TEMPLATEFLOW_HOME=/templateflow",
            common["container"],
            "/bids", "/output", app.get("analysis_level", "participant")
        ])
        
        # Add app-specific options
        if app.get("options"):
            cmd.extend(app["options"])
            logging.debug(f"Added app options: {app['options']}")
        
        # Add subject-specific parameters
        cmd.extend(["--participant-label", subject.replace("sub-", ""), "-w", "/tmp"])
        
        if debug:
            logging.info(f"Debug mode: About to execute container for {subject}")
            logging.info(f"Full command: {' '.join(cmd)}")
        
        # Execute the command with debug information
        result = run_container(
            cmd, 
            dry_run=dry_run, 
            debug=debug, 
            subject=subject, 
            log_dir=debug_log_dir
        )
        
        if not dry_run:
            # Check if container execution was successful (exit code 0)
            container_success = result is not None and (not hasattr(result, 'returncode') or result.returncode == 0)
            
            if container_success:
                logging.info(f"Container execution successful for {subject}")
                
                # Save results if DataLad output dataset
                if is_output_datalad:
                    save_results_datalad(common["output_folder"], subject, dry_run)
                
                # Wait a moment for filesystem to sync
                time.sleep(1)
                
                # Check if processing produced output using multiple strategies
                output_exists = check_generic_output_exists(subject, common)
                
                # Also check configured pattern if available
                if not output_exists:
                    pattern = app.get("output_check", {}).get("pattern", "")
                    if pattern:
                        check_dir = os.path.join(common["output_folder"], app["output_check"].get("directory", ""))
                        full_pattern = os.path.join(check_dir, pattern.replace("{subject}", subject))
                        matches = glob.glob(full_pattern)
                        output_exists = len(matches) > 0
                        if output_exists:
                            logging.info(f"Found expected output via pattern for {subject}: {matches}")
                
                if output_exists:
                    # Create success marker
                    create_success_marker(subject, common)
                    logging.info(f"Subject {subject} processing completed successfully")
                    
                    try:
                        shutil.rmtree(tmp_dir)
                        logging.debug(f"Cleaned up temp directory: {tmp_dir}")
                    except Exception as e:
                        logging.warning(f"Could not clean up temp directory {tmp_dir}: {e}")
                    return True
                else:
                    # Container ran successfully but no expected output found
                    logging.warning(f"Container completed for {subject} but no output detected")
                    logging.warning(f"This might indicate a configuration issue or the app produced output in an unexpected location")
                    
                    # Show output directory structure for debugging
                    list_output_structure(subject, common)
                    
                    logging.warning(f"Temp directory preserved for debugging: {tmp_dir}")
                    
                    if debug and debug_log_dir:
                        logging.warning(f"Debug mode: Check container logs in {debug_log_dir}")
                        # List available log files for this subject
                        try:
                            log_files = [f for f in os.listdir(debug_log_dir) if subject in f]
                            if log_files:
                                logging.warning(f"Available container log files for {subject}:")
                                for log_file in log_files:
                                    full_path = os.path.join(debug_log_dir, log_file)
                                    logging.warning(f"  - {full_path}")
                        except Exception as e:
                            logging.warning(f"Could not list debug log files: {e}")
                    
                    # List output structure for debugging
                    list_output_structure(subject, common)
                    
                    return False
            else:
                # Container execution failed
                logging.error(f"Container execution failed for {subject}")
                logging.warning(f"Temp directory preserved for debugging: {tmp_dir}")
                
                if debug and debug_log_dir:
                    logging.error(f"Debug mode: Check container logs in {debug_log_dir}")
                    # List available log files for this subject
                    try:
                        log_files = [f for f in os.listdir(debug_log_dir) if subject in f]
                        if log_files:
                            logging.error(f"Available container log files for {subject}:")
                            for log_file in log_files:
                                full_path = os.path.join(debug_log_dir, log_file)
                                logging.error(f"  - {full_path}")
                        else:
                            logging.error(f"No container log files found for {subject}")
                    except Exception as e:
                        logging.warning(f"Could not list debug log files: {e}")
                
                return False
        
        return True
        
    except Exception as e:
        error_msg = f"Error processing subject {subject}: {e}"
        logging.error(error_msg)
        logging.warning(f"Temp directory preserved for debugging: {tmp_dir}")
        
        if debug and debug_log_dir:
            logging.error(f"Debug mode: Check container logs in {debug_log_dir} for error details")
        
        return False

def list_output_structure(subject, common, max_depth=3):
    """List the output directory structure to help debug where files are located."""
    output_dir = common["output_folder"]
    logging.info(f"Output directory structure for debugging {subject}:")
    logging.info(f"Base output directory: {output_dir}")
    
    if not os.path.exists(output_dir):
        logging.info("  Output directory does not exist")
        return
    
    def list_dir_recursive(path, prefix="", current_depth=0):
        if current_depth >= max_depth:
            return
        
        try:
            items = sorted(os.listdir(path))
            for item in items[:20]:  # Limit to first 20 items to avoid spam
                item_path = os.path.join(path, item)
                if os.path.isdir(item_path):
                    logging.info(f"  {prefix}📁 {item}/")
                    if current_depth < max_depth - 1:
                        list_dir_recursive(item_path, prefix + "  ", current_depth + 1)
                else:
                    # Show file size for context
                    try:
                        size = os.path.getsize(item_path)
                        size_str = f" ({size} bytes)" if size < 1024 else f" ({size//1024} KB)"
                    except:
                        size_str = ""
                    logging.info(f"  {prefix}📄 {item}{size_str}")
            
            if len(items) > 20:
                logging.info(f"  {prefix}... and {len(items) - 20} more items")
                
        except PermissionError:
            logging.info(f"  {prefix}❌ Permission denied")
        except Exception as e:
            logging.info(f"  {prefix}❌ Error: {e}")
    
    list_dir_recursive(output_dir)

def process_group(common, app, dry_run=False, debug=False):
    """Process group-level analysis."""
    logging.info("Starting group-level processing")
    
    # Check if input/output are DataLad datasets
    is_input_datalad = is_datalad_dataset(common["bids_folder"])
    is_output_datalad = is_datalad_dataset(common["output_folder"])
    
    if is_input_datalad or is_output_datalad:
        logging.info("DataLad dataset detected for group analysis")
    
    tmp_dir = os.path.join(common["tmp_folder"], "group")
    
    # Create debug log directory if in debug mode
    debug_log_dir = None
    if debug:
        debug_log_dir = os.path.join(common.get("log_dir", "logs"), "container_logs")
        os.makedirs(debug_log_dir, exist_ok=True)
        logging.info(f"Debug mode enabled for group analysis: Container logs will be saved to {debug_log_dir}")
    
    try:
        os.makedirs(tmp_dir, exist_ok=True)
        logging.debug(f"Created temp directory: {tmp_dir}")
        
        # Get all data if DataLad input dataset
        if is_input_datalad:
            logging.info("Getting all data for group analysis from DataLad dataset")
            # Get all subject data for group analysis
            subjects_pattern = os.path.join(common["bids_folder"], "sub-*")
            cmd = ["datalad", "get", subjects_pattern]
            run_datalad_command(cmd, cwd=common["bids_folder"], dry_run=dry_run)
            
            # Also get derivatives
            derivatives_pattern = os.path.join(common["bids_folder"], "derivatives")
            if os.path.exists(derivatives_pattern):
                cmd = ["datalad", "get", derivatives_pattern]
                run_datalad_command(cmd, cwd=common["bids_folder"], dry_run=dry_run)
        
        # Build container command
        cmd = ["apptainer", "run"]
        
        # Add app-specific apptainer arguments
        if app.get("apptainer_args"):
            cmd.extend(app["apptainer_args"])
        else:
            cmd.append("--containall")
        
        # Add bind mounts
        for mnt in build_common_mounts(common, tmp_dir):
            cmd.extend(["-B", mnt])
        
        # Add custom mounts
        for mount in app.get("mounts", []):
            if mount.get("source") and mount.get("target"):
                cmd.extend(["-B", f"{mount['source']}:{mount['target']}"])
        
        # Add environment and container
        cmd.extend([
            "--env", f"TEMPLATEFLOW_HOME=/templateflow",
            common["container"],
            "/bids", "/output", app.get("analysis_level", "group"),
            "-w", "/tmp"
        ])
        
        # Add app-specific options
        if app.get("options"):
            cmd.extend(app["options"])
        
        # Execute the command
        result = run_container(
            cmd, 
            dry_run=dry_run, 
            debug=debug, 
            subject="group", 
            log_dir=debug_log_dir
        )
        
        if not dry_run:
            # Save results if DataLad output dataset
            if is_output_datalad:
                save_results_datalad(common["output_folder"], "group", dry_run)
            
            try:
                shutil.rmtree(tmp_dir)
                logging.debug(f"Cleaned up temp directory: {tmp_dir}")
            except Exception as e:
                logging.warning(f"Could not clean up tmp directory {tmp_dir}: {e}")
        
        logging.info("Group-level processing completed successfully")
        return True
        
    except Exception as e:
        logging.error(f"Error in group-level processing: {e}")
        logging.warning(f"Temp directory preserved for debugging: {tmp_dir}")
        return False

def signal_handler(signum, frame):
    """Handle interrupt signals gracefully."""
    logging.warning(f"Received signal {signum}, attempting graceful shutdown...")
    sys.exit(1)

def print_summary(processed_subjects, failed_subjects, total_time):
    """Print a summary of the processing results."""
    logging.info("=" * 60)
    logging.info("PROCESSING SUMMARY")
    logging.info("=" * 60)
    logging.info(f"Total subjects processed: {len(processed_subjects)}")
    logging.info(f"Successfully completed: {len(processed_subjects) - len(failed_subjects)}")
    logging.info(f"Failed: {len(failed_subjects)}")
    logging.info(f"Total processing time: {total_time:.2f} seconds")
    
    if failed_subjects:
        logging.warning("Failed subjects:")
        for subject in failed_subjects:
            logging.warning(f"  - {subject}")
    
    logging.info("=" * 60)

def main():
    """Main function with comprehensive error handling."""
    start_time = time.time()
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Parse arguments and setup logging
        args = parse_args()
        
        # Auto-enable --force when using --from-json
        force_auto_enabled = False
        if args.from_json and not args.force:
            args.force = True
            force_auto_enabled = True
        
        log_file = setup_logging(args.log_level)
        
        logging.info("BIDS App Runner 2.0.0 starting...")
        logging.info(f"Command line: {' '.join(sys.argv)}")
        
        # Log force auto-activation after logging is set up
        if force_auto_enabled:
            logging.info("Auto-enabling --force flag when using --from-json")
        
        # Read and validate configuration
        config = read_config(args.config)
        
        if "common" not in config or "app" not in config:
            logging.error("Config must contain 'common' and 'app' sections")
            sys.exit("ERROR: Config must contain 'common' and 'app' sections.")
        
        common, app = config["common"], config["app"]
        validate_common_config(common)
        validate_app_config(app)
        
        # Handle validation-only mode
        if args.validate_only:
            if not VALIDATION_AVAILABLE:
                logging.error("Validation not available - missing bids_validation_integration.py")
                sys.exit("ERROR: Validation features not available")
            
            logging.info("Running validation-only mode...")
            os.makedirs(args.validation_output_dir, exist_ok=True)
            
            results = validate_and_generate_reprocess_config(
                common, app, args.validation_output_dir
            )
            
            print_validation_summary(results)
            
            if results.get("total_missing", 0) > 0:
                sys.exit(1)
            else:
                sys.exit(0)
        
        # Setup validation if requested
        run_validation = args.validate or args.reprocess_missing
        if run_validation and not VALIDATION_AVAILABLE:
            logging.warning("Validation requested but not available - continuing without validation")
            run_validation = False
        
        # Clean success markers if requested
        if args.clean_success_markers:
            logging.info("Cleaning all success markers...")
            clean_all_success_markers(common)
        
        # Get subjects list
        subjects = []
        level = app.get("analysis_level", "participant")
        
        if level == "participant":
            # Handle --from-json option first
            if args.from_json:
                try:
                    missing_subjects = parse_missing_subjects_from_json(args.from_json, args.pipeline)
                    subjects = sorted(list(missing_subjects))
                    if args.pipeline:
                        logging.info(f"Using subjects from JSON file {args.from_json} (pipeline: {args.pipeline}): {len(subjects)} subjects")
                    else:
                        logging.info(f"Using subjects from JSON file {args.from_json} (all pipelines): {len(subjects)} subjects")
                    if subjects:
                        logging.info(f"Subjects to reprocess: {subjects[:5]}" + ("..." if len(subjects) > 5 else ""))
                except Exception as e:
                    logging.error(f"Error parsing JSON file: {e}")
                    sys.exit(f"ERROR: Could not parse JSON file {args.from_json}: {e}")
            # Use command-line subjects if provided
            elif args.subjects:
                subjects = [s if s.startswith("sub-") else f"sub-{s}" for s in args.subjects]
                logging.info(f"Using subjects from command line: {subjects}")
            # Use subjects from config if provided
            elif "participant_labels" in app and app["participant_labels"]:
                subjects = [s if s.startswith("sub-") else f"sub-{s}" for s in app["participant_labels"]]
                logging.info(f"Using subjects from config: {subjects}")
            # Auto-discover subjects
            else:
                try:
                    subjects = [d for d in os.listdir(common["bids_folder"])
                               if d.startswith("sub-") and os.path.isdir(os.path.join(common["bids_folder"], d))]
                    logging.info(f"Auto-discovered subjects: {subjects}")
                except Exception as e:
                    logging.error(f"Error discovering subjects: {e}")
                    sys.exit("ERROR: Could not discover subjects in BIDS folder")
            
            if not subjects:
                logging.error("No subjects found to process")
                sys.exit("ERROR: No subjects found.")
            
            logging.info(f"Found {len(subjects)} subjects to process")
            
            # Process subjects
            processed_subjects = []
            failed_subjects = []
            
            # Determine number of jobs (force to 1 if pilot mode)
            if args.pilot:
                jobs = 1
                logging.info("Pilot mode enabled: Setting jobs to 1")
            else:
                jobs = common.get("jobs", multiprocessing.cpu_count())
            
            if args.pilot:
                subject = random.choice(subjects)
                logging.info(f"Pilot mode: processing single subject ({subject})")
                
                success = process_subject(subject, common, app, args.dry_run, args.force, args.debug)
                processed_subjects.append(subject)
                if not success:
                    failed_subjects.append(subject)
                    
            else:
                logging.info(f"Processing {len(subjects)} subjects with {jobs} parallel jobs")
                
                if args.debug:
                    logging.info("Debug mode enabled - Container execution will be logged in detail")
                
                if args.dry_run:
                    logging.info("DRY RUN MODE - No actual processing will occur")
                    for subject in subjects:
                        process_subject(subject, common, app, dry_run=True, force=args.force, debug=args.debug)
                        processed_subjects.append(subject)
                else:
                    # Use ProcessPoolExecutor for parallel processing
                    # Note: Debug mode disabled in parallel processing due to complexity
                    if args.debug and jobs > 1:
                        logging.warning("Debug mode with parallel processing (jobs > 1) not supported")
                        logging.warning("Running in serial mode for debug output")
                        jobs = 1
                    
                    if jobs == 1:
                        # Serial processing (supports debug mode)
                        for subject in subjects:
                            success = process_subject(subject, common, app, False, args.force, args.debug)
                            processed_subjects.append(subject)
                            if not success:
                                failed_subjects.append(subject)
                    else:
                        # Parallel processing (debug mode disabled)
                        with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as executor:
                            # Submit all jobs
                            future_to_subject = {
                                executor.submit(process_subject, subject, common, app, False, args.force, False): subject
                                for subject in subjects
                            }
                        
                        # Collect results
                        for future in concurrent.futures.as_completed(future_to_subject):
                            subject = future_to_subject[future]
                            processed_subjects.append(subject)
                            
                            try:
                                success = future.result()
                                if not success:
                                    failed_subjects.append(subject)
                            except Exception as e:
                                logging.error(f"Exception processing subject {subject}: {e}")
                                failed_subjects.append(subject)
            
            # Print summary
            end_time = time.time()
            print_summary(processed_subjects, failed_subjects, end_time - start_time)
            
            # Exit with appropriate code
            if failed_subjects:
                logging.error(f"Processing completed with {len(failed_subjects)} failures")
                # Don't run validation if processing failed
                sys.exit(1)
            else:
                logging.info("All subjects processed successfully")
                
                # Run validation if requested
                if run_validation and not args.dry_run:
                    logging.info("Running post-processing validation...")
                    os.makedirs(args.validation_output_dir, exist_ok=True)
                    
                    try:
                        results = validate_and_generate_reprocess_config(
                            common, app, args.validation_output_dir
                        )
                        
                        print_validation_summary(results)
                        
                        # Handle reprocess-missing mode
                        if args.reprocess_missing and results.get("total_missing", 0) > 0:
                            reprocess_config = results.get("reprocess_config")
                            if reprocess_config and os.path.exists(reprocess_config):
                                logging.info(f"Automatically reprocessing {results['total_missing']} missing subjects...")
                                
                                # Recursively call run_bids_apps with the reprocess config
                                import subprocess
                                reprocess_cmd = [
                                    sys.executable, 
                                    os.path.abspath(__file__), 
                                    "-x", reprocess_config
                                ]
                                
                                # Add other relevant flags
                                if args.debug:
                                    reprocess_cmd.append("--debug")
                                if args.force:
                                    reprocess_cmd.append("--force")
                                    
                                logging.info(f"Reprocessing command: {' '.join(reprocess_cmd)}")
                                result = subprocess.run(reprocess_cmd)
                                
                                if result.returncode != 0:
                                    logging.error("Reprocessing failed")
                                    sys.exit(1)
                                else:
                                    logging.info("Reprocessing completed successfully")
                        
                        # Exit with error if validation found missing items but no reprocessing
                        elif not args.reprocess_missing and results.get("total_missing", 0) > 0:
                            logging.warning(f"Validation found {results['total_missing']} subjects with missing outputs")
                            logging.warning("Use --reprocess-missing to automatically reprocess them")
                            sys.exit(1)
                            
                    except Exception as e:
                        logging.error(f"Validation failed: {e}")
                        sys.exit(1)
                
        else:
            logging.info(f"Running {level} level analysis")
            success = process_group(common, app, args.dry_run, args.debug)
            
            if success:
                logging.info("Group analysis completed successfully")
            else:
                logging.error("Group analysis failed")
                sys.exit(1)
        
        logging.info("BIDS App Runner completed successfully")
        logging.info(f"Log file: {log_file}")
        
    except KeyboardInterrupt:
        logging.warning("Process interrupted by user")
        sys.exit(1)
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        logging.error("See log file for details")
        sys.exit(1)

if __name__ == "__main__":
    main()
