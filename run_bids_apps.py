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
from datetime import datetime
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

def create_analysis_branch(dataset_dir, pipeline_name, dry_run=False):
    """Create a dedicated analysis branch for the pipeline."""
    if not is_datalad_dataset(dataset_dir):
        return True
    
    branch_name = f"analysis/{pipeline_name}"
    logging.info(f"Creating/switching to analysis branch: {branch_name}")
    
    # Check if branch exists
    cmd = ["git", "rev-parse", "--verify", f"origin/{branch_name}"]
    branch_exists = run_datalad_command(cmd, cwd=dataset_dir, dry_run=False)
    
    if branch_exists:
        # Switch to existing branch
        cmd = ["git", "checkout", branch_name]
        run_datalad_command(cmd, cwd=dataset_dir, dry_run=dry_run)
        
        # Pull latest changes
        cmd = ["git", "pull", "origin", branch_name]
        run_datalad_command(cmd, cwd=dataset_dir, dry_run=dry_run)
    else:
        # Create new branch from main
        cmd = ["git", "checkout", "-b", branch_name, "main"]
        run_datalad_command(cmd, cwd=dataset_dir, dry_run=dry_run)
        
        # Create derivatives directory structure if needed
        derivatives_dir = os.path.join(dataset_dir, "derivatives", pipeline_name)
        if not dry_run:
            os.makedirs(derivatives_dir, exist_ok=True)
        
        # Initial commit for new branch
        cmd = ["datalad", "save", "-m", f"Initialize {pipeline_name} analysis branch"]
        run_datalad_command(cmd, cwd=dataset_dir, dry_run=dry_run)
    
    return True

def save_results_datalad(output_dir, subject, dry_run=False, pipeline_name=None):
    """Save processing results using DataLad with enhanced branch strategy."""
    if not is_datalad_dataset(output_dir):
        return True  # Not a DataLad dataset, no action needed
    
    if not check_datalad_available():
        logging.warning("DataLad not available, skipping result saving")
        return True
    
    # Determine if this is the dataset root (for branch switching)
    dataset_root = output_dir
    if output_dir.endswith('derivatives') or '/derivatives/' in output_dir:
        # Navigate to dataset root
        parts = output_dir.split(os.sep)
        if 'derivatives' in parts:
            root_parts = parts[:parts.index('derivatives')]
            dataset_root = os.sep.join(root_parts)
    
    # Create/switch to analysis branch if pipeline specified
    if pipeline_name:
        create_analysis_branch(dataset_root, pipeline_name, dry_run)
    
    logging.info(f"Saving DataLad results for subject: {subject}")
    
    # Save results with descriptive commit message
    if pipeline_name:
        commit_msg = f"Add {pipeline_name} results for {subject}"
    else:
        commit_msg = f"Add results for {subject}"
    
    cmd = ["datalad", "save", "-m", commit_msg]
    
    if not run_datalad_command(cmd, cwd=dataset_root, dry_run=dry_run):
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

def validate_datalad_config(datalad_config):
    """Validate DataLad-specific configuration."""
    if not datalad_config:
        return None
    
    logging.info("Validating DataLad configuration...")
    
    # Set defaults
    defaults = {
        "analysis_branch_strategy": "analysis/{pipeline}",
        "pipeline_name": "unknown",
        "auto_push": False,
        "create_analysis_branch": True
    }
    
    for key, default_value in defaults.items():
        if key not in datalad_config:
            datalad_config[key] = default_value
            logging.debug(f"Set DataLad default: {key} = {default_value}")
    
    # Validate pipeline name
    pipeline_name = datalad_config["pipeline_name"]
    if not pipeline_name or pipeline_name == "unknown":
        logging.warning("No pipeline_name specified in DataLad config - using 'analysis'")
        datalad_config["pipeline_name"] = "analysis"
    
    logging.info("DataLad configuration validation completed")
    return datalad_config

def validate_common_config(cfg):
    """Validate common configuration section."""
    logging.info("Validating common configuration...")
    
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
    """Check if a subject has already been processed.
    
    For longitudinal datasets, checks that ALL sessions are processed.
    For single-session datasets, uses the original logic.
    """
    if force:
        return False
    
    # Get all sessions for this subject
    sessions = get_subject_sessions(subject, common["bids_folder"])
    
    pattern = app.get("output_check", {}).get("pattern", "")
    if not pattern:
        logging.debug(f"No output check pattern defined for {subject}")
        return False
    
    check_dir = os.path.join(common["output_folder"], app["output_check"].get("directory", ""))
    
    # For single-session datasets (sessions = [None])
    if len(sessions) == 1 and sessions[0] is None:
        full_pattern = os.path.join(check_dir, pattern.replace("{subject}", subject))
        matches = glob.glob(full_pattern)
        if matches:
            logging.debug(f"Found existing output for {subject}: {matches}")
            return True
        return False
    
    # For multi-session datasets, check ALL sessions
    processed_sessions = []
    missing_sessions = []
    
    for session in sessions:
        # Create session-aware pattern
        # Replace {subject} and add session info if pattern supports it
        if "{session}" in pattern:
            session_pattern = pattern.replace("{subject}", subject).replace("{session}", session)
        else:
            # If pattern doesn't have {session}, try to add session to subject part
            session_pattern = pattern.replace("{subject}", f"{subject}/{session}")
        
        full_pattern = os.path.join(check_dir, session_pattern)
        matches = glob.glob(full_pattern)
        
        if matches:
            processed_sessions.append(session)
            logging.debug(f"Found output for {subject} {session}: {matches}")
        else:
            missing_sessions.append(session)
            logging.debug(f"Missing output for {subject} {session}")
    
    # Subject is only considered processed if ALL sessions are processed
    if missing_sessions:
        logging.info(f"Subject '{subject}' partially processed: "
                    f"{len(processed_sessions)}/{len(sessions)} sessions complete. "
                    f"Missing sessions: {missing_sessions}")
        return False
    else:
        logging.debug(f"Subject '{subject}' fully processed: "
                     f"all {len(sessions)} sessions complete")
        return True

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

def process_subject(subject, common, app, dry_run=False, force=False, debug=False, datalad_config=None):
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
            # Save results if DataLad output dataset
            if is_output_datalad:
                pipeline_name = datalad_config.get("pipeline_name") if datalad_config else None
                save_results_datalad(common["output_folder"], subject, dry_run, pipeline_name)
            
            # Check if processing was successful
            if subject_processed(subject, common, app, force=False):
                logging.info(f"Subject {subject} processing completed successfully")
                try:
                    shutil.rmtree(tmp_dir)
                    logging.debug(f"Cleaned up temp directory: {tmp_dir}")
                except Exception as e:
                    logging.warning(f"Could not clean up temp directory {tmp_dir}: {e}")
                return True
            else:
                error_msg = f"Processing failed for {subject} - no expected output found"
                logging.error(error_msg)
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

def process_group(common, app, dry_run=False, debug=False, datalad_config=None):
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
                pipeline_name = datalad_config.get("pipeline_name") if datalad_config else None
                save_results_datalad(common["output_folder"], "group", dry_run, pipeline_name)
            
            try:
                shutil.rmtree(tmp_dir)
                logging.debug(f"Cleaned up temp directory: {tmp_dir}")
            except Exception as e:
                logging.warning(f"Could not clean up temp directory {tmp_dir}: {e}")
        
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
        log_file = setup_logging(args.log_level)
        
        logging.info("BIDS App Runner 2.0.0 starting...")
        logging.info(f"Command line: {' '.join(sys.argv)}")
        
        # Read and validate configuration
        config = read_config(args.config)
        
        if "common" not in config or "app" not in config:
            logging.error("Config must contain 'common' and 'app' sections")
            sys.exit("ERROR: Config must contain 'common' and 'app' sections.")
        
        common, app = config["common"], config["app"]
        validate_common_config(common)
        validate_app_config(app)
        
        # Validate DataLad configuration if present
        datalad_config = validate_datalad_config(config.get("datalad"))
        
        # Get subjects list
        subjects = []
        level = app.get("analysis_level", "participant")
        
        if level == "participant":
            # Use command-line subjects if provided
            if args.subjects:
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
            
            if common.get("pilottest", False):
                subject = random.choice(subjects)
                logging.info(f"Pilot mode: processing single subject ({subject})")
                
                success = process_subject(subject, common, app, args.dry_run, args.force, args.debug, datalad_config)
                processed_subjects.append(subject)
                if not success:
                    failed_subjects.append(subject)
                    
            else:
                jobs = common.get("jobs", multiprocessing.cpu_count())
                logging.info(f"Processing {len(subjects)} subjects with {jobs} parallel jobs")
                
                if args.debug:
                    logging.info("Debug mode enabled - Container execution will be logged in detail")
                
                if args.dry_run:
                    logging.info("DRY RUN MODE - No actual processing will occur")
                    for subject in subjects:
                        process_subject(subject, common, app, dry_run=True, force=args.force, debug=args.debug, datalad_config=datalad_config)
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
                            success = process_subject(subject, common, app, False, args.force, args.debug, datalad_config)
                            processed_subjects.append(subject)
                            if not success:
                                failed_subjects.append(subject)
                    else:
                        # Parallel processing (debug mode disabled)
                        with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as executor:
                            # Submit all jobs
                            future_to_subject = {
                                executor.submit(process_subject, subject, common, app, False, args.force, False, datalad_config): subject
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
                sys.exit(1)
            else:
                logging.info("All subjects processed successfully")
                
        else:
            logging.info(f"Running {level} level analysis")
            success = process_group(common, app, args.dry_run, args.debug, datalad_config)
            
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
