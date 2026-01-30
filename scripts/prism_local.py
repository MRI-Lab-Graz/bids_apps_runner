#!/usr/bin/env python3
"""
PRISM Local - Local/cluster execution mode

Handles BIDS app execution on local machines or traditional compute clusters
using Python multiprocessing for parallel subject processing.

Extracted from: run_bids_apps.py
Author: BIDS Apps Runner Team (PRISM Edition)
Version: 3.0.0
"""

import os
import logging
import platform
import subprocess
import shutil
import time
import glob
import multiprocessing
import concurrent.futures
import random
from typing import Dict, Any, List
from argparse import Namespace
from datetime import datetime

# Import from PRISM modules
from prism_core import get_subjects_from_bids, print_summary
import prism_datalad


# ============================================================================
# Helper Functions for Container Execution
# ============================================================================

def _sanitize_apptainer_args(apptainer_args):
    """Sanitize apptainer args to avoid invalid invocations."""
    if not apptainer_args:
        return []
    
    sanitized = []
    i = 0
    while i < len(apptainer_args):
        token = str(apptainer_args[i])
        
        if token.startswith("--env="):
            if "=" not in token[len("--env="):]:
                logging.warning(f"Ignoring invalid apptainer arg '{token}'")
                i += 1
                continue
            sanitized.append(token)
            i += 1
            continue
        
        if token == "--env":
            if i + 1 >= len(apptainer_args):
                logging.warning("Ignoring invalid apptainer arg '--env' (missing KEY=VALUE)")
                i += 1
                continue
            
            value = str(apptainer_args[i + 1])
            if value.startswith("-") or "=" not in value:
                logging.warning(f"Ignoring invalid apptainer args '--env {value}'")
                i += 2 if not value.startswith("-") else 1
                continue
            
            sanitized.extend([token, value])
            i += 2
            continue
        
        sanitized.append(token)
        i += 1
    
    return sanitized


def _build_common_mounts(common, tmp_dir):
    """Build common mount points for the container."""
    mounts = [
        f"{tmp_dir}:/tmp",
        f"{common['output_folder']}:/output",
        f"{common['bids_folder']}:/bids"
    ]
    
    # Only add templateflow if it's specified and exists
    if common.get('templateflow_dir') and os.path.exists(common['templateflow_dir']):
        mounts.append(f"{common['templateflow_dir']}:/templateflow")
    
    if common.get("optional_folder"):
        mounts.append(f"{common['optional_folder']}:/base")
    
    return mounts


def _run_container(cmd, env=None, dry_run=False, debug=False, subject=None, log_dir=None):
    """Execute container command with optional dry run mode and detailed logging."""
    cmd_str = " ".join(cmd)
    
    if dry_run:
        logging.info(f"DRY RUN - Would execute: {cmd_str}")
        logging.info("✅ Command syntax validated successfully")
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
        logging.info(f"Debug mode: Container logs saved to {container_log_file}")
    
    try:
        run_env = env or os.environ.copy()
        
        if debug:
            logging.info("Debug mode: Starting container with real-time logging...")
            
            with open(container_log_file, 'w') if container_log_file else open(os.devnull, 'w') as stdout_file, \
                 open(container_error_file, 'w') if container_error_file else open(os.devnull, 'w') as stderr_file:
                
                process = subprocess.Popen(
                    cmd,
                    env=run_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1
                )
                
                stdout_data, stderr_data = process.communicate()
                return_code = process.wait()
                
                if container_log_file and stdout_data:
                    stdout_file.write(stdout_data)
                if container_error_file and stderr_data:
                    stderr_file.write(stderr_data)
                
                class DebugResult:
                    def __init__(self, returncode, stdout, stderr):
                        self.returncode = returncode
                        self.stdout = stdout
                        self.stderr = stderr
                
                result = DebugResult(return_code, stdout_data, stderr_data)
                
                if return_code != 0:
                    raise subprocess.CalledProcessError(return_code, cmd, result.stdout, result.stderr)
        else:
            result = subprocess.run(
                cmd, 
                check=True, 
                env=run_env,
                capture_output=True, 
                text=True
            )
        
        logging.info("Container execution completed successfully")
        return result
        
    except subprocess.CalledProcessError as e:
        logging.error(f"Container execution failed with exit code {e.returncode}")
        if e.stdout:
            logging.error(f"stdout: {e.stdout[:500]}")
        if e.stderr:
            logging.error(f"stderr: {e.stderr[:500]}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error during execution: {e}")
        raise


# ============================================================================
# Subject Processing Functions
# ============================================================================

def _check_generic_output_exists(subject, common):
    """Check for generic output patterns that most BIDS apps produce."""
    output_dir = common["output_folder"]
    
    patterns_to_check = [
        os.path.join(output_dir, subject),
        os.path.join(output_dir, "derivatives", "*", subject),
        os.path.join(output_dir, subject, "func", f"{subject}_*"),
        os.path.join(output_dir, subject, "anat", f"{subject}_*"),
        os.path.join(output_dir, subject, "dwi", f"{subject}_*"),
        os.path.join(output_dir, f"{subject}.html"),
    ]
    
    for pattern in patterns_to_check:
        matches = glob.glob(pattern)
        if matches:
            logging.debug(f"Found output for {subject}: {matches[0]}")
            return True
    
    # Check if subject directory exists and is non-empty
    subject_dir = os.path.join(output_dir, subject)
    if os.path.isdir(subject_dir):
        try:
            for root, dirs, files in os.walk(subject_dir):
                if files:
                    return True
        except Exception:
            pass
    
    return False


def _subject_processed(subject, common, app, force=False):
    """Check if a subject has already been processed."""
    if force:
        logging.info(f"Force flag - will reprocess {subject}")
        return False
    
    # Check success marker file
    success_marker = os.path.join(common["output_folder"], ".bids_app_runner", f"{subject}_success.txt")
    if os.path.exists(success_marker):
        logging.info(f"Subject '{subject}' already processed (success marker found)")
        return True
    
    # Check configured output pattern
    pattern = app.get("output_check", {}).get("pattern", "")
    if pattern:
        check_dir = os.path.join(common["output_folder"], app["output_check"].get("directory", ""))
        full_pattern = os.path.join(check_dir, pattern.replace("{subject}", subject))
        matches = glob.glob(full_pattern)
        if matches:
            logging.info(f"Subject '{subject}' already processed (output pattern matched)")
            return True
    
    # Generic output detection
    if _check_generic_output_exists(subject, common):
        logging.info(f"Subject '{subject}' already processed (output found)")
        return True
    
    return False


def _create_success_marker(subject, common):
    """Create a success marker file for a subject."""
    marker_dir = os.path.join(common["output_folder"], ".bids_app_runner")
    os.makedirs(marker_dir, exist_ok=True)
    
    marker_file = os.path.join(marker_dir, f"{subject}_success.txt")
    try:
        with open(marker_file, 'w') as f:
            f.write(f"Subject {subject} processed successfully\n")
            f.write(f"Timestamp: {datetime.now().isoformat()}\n")
            f.write(f"Runner version: PRISM 3.0.0\n")
        return True
    except Exception as e:
        logging.warning(f"Could not create success marker for {subject}: {e}")
        return False


def _process_subject(subject, common, app, dry_run=False, force=False, debug=False):
    """Process a single subject with comprehensive error handling."""
    logging.info(f"Starting processing for subject: {subject}")
    
    # Check if input/output is a DataLad dataset
    is_input_datalad = prism_datalad.is_datalad_dataset(common["bids_folder"])
    is_output_datalad = prism_datalad.is_datalad_dataset(common["output_folder"])
    
    # Create temporary directory
    tmp_dir = os.path.join(common["tmp_folder"], subject)
    
    # Create debug log directory if in debug mode
    debug_log_dir = None
    if debug:
        debug_log_dir = os.path.join(common.get("log_dir", "logs"), "container_logs")
        os.makedirs(debug_log_dir, exist_ok=True)
    
    try:
        os.makedirs(tmp_dir, exist_ok=True)
        
        # Check if already processed
        if _subject_processed(subject, common, app, force):
            try:
                shutil.rmtree(tmp_dir)
            except:
                pass
            return True
        
        # Get subject data if DataLad dataset
        if is_input_datalad:
            prism_datalad.get_subject_data(common["bids_folder"], subject, dry_run)
        
        # Build container command
        engine = common.get("container_engine", "apptainer")
        
        if engine == "docker":
            cmd = ["docker", "run", "--rm"]
            
            # Apple Silicon support
            if platform.system() == "Darwin" and platform.machine() == "arm64":
                logging.info("Apple Silicon detected - adding platform flag")
                cmd.extend(["--platform", "linux/amd64"])
            
            cmd.extend(["-e", "TEMPLATEFLOW_HOME=/templateflow"])
            
            for mnt in _build_common_mounts(common, tmp_dir):
                cmd.extend(["-v", mnt])
            
            for mount in app.get("mounts", []):
                if mount.get("source") and mount.get("target"):
                    cmd.extend(["-v", f"{mount['source']}:{mount['target']}"])
            
            cmd.append(common["container"])
        else:
            # Apptainer/Singularity
            cmd = ["apptainer", "run"]
            
            if app.get("apptainer_args"):
                safe_args = _sanitize_apptainer_args(app["apptainer_args"])
                cmd.extend(safe_args)
            else:
                cmd.append("--containall")
            
            for mnt in _build_common_mounts(common, tmp_dir):
                cmd.extend(["-B", mnt])
            
            for mount in app.get("mounts", []):
                if mount.get("source") and mount.get("target"):
                    cmd.extend(["-B", f"{mount['source']}:{mount['target']}"])
            
            cmd.extend(["--env", "TEMPLATEFLOW_HOME=/templateflow"])
            cmd.append(common["container"])
        
        # Add common BIDS app arguments
        cmd.extend(["/bids", "/output", app.get("analysis_level", "participant")])
        
        if app.get("options"):
            cmd.extend(app["options"])
        
        cmd.extend(["--participant-label", subject.replace("sub-", ""), "-w", "/tmp"])
        
        # Execute the command
        result = _run_container(
            cmd, 
            dry_run=dry_run, 
            debug=debug, 
            subject=subject, 
            log_dir=debug_log_dir
        )
        
        if not dry_run:
            container_success = result is not None and (not hasattr(result, 'returncode') or result.returncode == 0)
            
            if container_success:
                logging.info(f"Container execution successful for {subject}")
                
                # Save results if DataLad output dataset
                if is_output_datalad:
                    prism_datalad.save_results(common["output_folder"], subject, dry_run)
                
                time.sleep(1)  # Wait for filesystem sync
                
                # Check if output exists
                output_exists = _check_generic_output_exists(subject, common)
                
                if not output_exists:
                    pattern = app.get("output_check", {}).get("pattern", "")
                    if pattern:
                        check_dir = os.path.join(common["output_folder"], app["output_check"].get("directory", ""))
                        full_pattern = os.path.join(check_dir, pattern.replace("{subject}", subject))
                        matches = glob.glob(full_pattern)
                        output_exists = len(matches) > 0
                
                if output_exists:
                    _create_success_marker(subject, common)
                    logging.info(f"Subject {subject} completed successfully")
                    
                    try:
                        shutil.rmtree(tmp_dir)
                    except:
                        pass
                    return True
                else:
                    logging.warning(f"Container completed for {subject} but no output detected")
                    return False
            else:
                logging.error(f"Container execution failed for {subject}")
                return False
        
        return True
        
    except Exception as e:
        logging.error(f"Error processing subject {subject}: {e}")
        return False


# ============================================================================
# Main Execution Function
# ============================================================================

def execute_local(config: Dict[str, Any], args: Namespace) -> bool:
    """Execute BIDS app in local/cluster mode.
    
    Args:
        config: Configuration dictionary
        args: Parsed command-line arguments
        
    Returns:
        True if execution successful, False otherwise
    """
    logging.info("=" * 60)
    logging.info("LOCAL/CLUSTER EXECUTION MODE")
    logging.info("=" * 60)
    
    common = config.get('common', {})
    app = config.get('app', {})
    
    # Create output directory if it doesn't exist
    output_folder = common.get('output_folder')
    if output_folder:
        os.makedirs(output_folder, exist_ok=True)
        logging.info(f"Ensured output directory exists: {output_folder}")
    
    start_time = time.time()
    
    # Get subjects
    if args.subjects:
        subjects = [s if s.startswith("sub-") else f"sub-{s}" for s in args.subjects]
        logging.info(f"Processing specified subjects: {subjects}")
    else:
        bids_folder = common.get('bids_folder')
        subjects = get_subjects_from_bids(bids_folder, args.dry_run)
        
        if not subjects and not args.dry_run:
            logging.error(f"No subjects found in BIDS folder: {bids_folder}")
            return False
        elif not subjects and args.dry_run:
            logging.info("Dry-run mode: using placeholder subject")
            subjects = ['sub-example']
        else:
            logging.info(f"Auto-discovered {len(subjects)} subjects")
    
    # Handle pilot mode
    pilot = args.pilot if hasattr(args, 'pilot') else False
    if pilot:
        subject = random.choice(subjects)
        subjects = [subject]
        logging.info(f"Pilot mode: processing only {subject}")
    
    # Determine number of parallel jobs
    jobs = common.get("jobs", multiprocessing.cpu_count())
    if pilot:
        jobs = 1
        logging.info("Pilot mode: forcing jobs=1")
    
    # Handle debug mode
    debug = args.debug if hasattr(args, 'debug') else False
    force = args.force if hasattr(args, 'force') else False
    dry_run = args.dry_run if hasattr(args, 'dry_run') else False
    
    if debug and jobs > 1:
        logging.warning("Debug mode not supported with parallel processing")
        logging.warning("Running in serial mode (jobs=1)")
        jobs = 1
    
    logging.info(f"Processing {len(subjects)} subjects with {jobs} parallel jobs")
    
    if debug:
        logging.info("Debug mode enabled - detailed container logs will be saved")
    
    # Process subjects
    processed_subjects = []
    failed_subjects = []
    
    if dry_run:
        logging.info("DRY RUN MODE - No actual processing will occur")
        for subject in subjects:
            _process_subject(subject, common, app, dry_run=True, force=force, debug=debug)
            processed_subjects.append(subject)
    elif jobs == 1:
        # Serial processing (supports debug mode)
        for subject in subjects:
            success = _process_subject(subject, common, app, False, force, debug)
            processed_subjects.append(subject)
            if not success:
                failed_subjects.append(subject)
    else:
        # Parallel processing
        with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as executor:
            future_to_subject = {
                executor.submit(_process_subject, subject, common, app, False, force, False): subject
                for subject in subjects
            }
            
            for future in concurrent.futures.as_completed(future_to_subject):
                subject = future_to_subject[future]
                processed_subjects.append(subject)
                
                try:
                    success = future.result()
                    if not success:
                        failed_subjects.append(subject)
                except Exception as e:
                    logging.error(f"Exception processing {subject}: {e}")
                    failed_subjects.append(subject)
    
    # Print summary
    end_time = time.time()
    print_summary(processed_subjects, failed_subjects, end_time - start_time)
    
    return len(failed_subjects) == 0
