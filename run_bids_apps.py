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
        description="Run a BIDS App using a JSON config file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -x config.json
  %(prog)s -x config.json --dry-run
  %(prog)s -x config.json --log-level DEBUG
  %(prog)s -x config.json --subjects sub-001 sub-002
  
For more information, see README.md
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

def subject_processed(subject, common, app, force=False):
    """Check if a subject has already been processed."""
    if force:
        return False
        
    pattern = app.get("output_check", {}).get("pattern", "")
    if not pattern:
        logging.debug(f"No output check pattern defined for {subject}")
        return False
        
    check_dir = os.path.join(common["output_folder"], app["output_check"].get("directory", ""))
    full_pattern = os.path.join(check_dir, pattern.replace("{subject}", subject))
    
    matches = glob.glob(full_pattern)
    if matches:
        logging.debug(f"Found existing output for {subject}: {matches}")
        return True
    
    return False

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

def run_container(cmd, env=None, dry_run=False):
    """Execute container command with optional dry run mode."""
    cmd_str = " ".join(cmd)
    
    if dry_run:
        logging.info(f"DRY RUN - Would execute: {cmd_str}")
        return None
    
    logging.info(f"Running command: {cmd_str}")
    
    try:
        # Create environment with fallback
        run_env = env or os.environ.copy()
        
        # Run the command
        result = subprocess.run(
            cmd, 
            check=True, 
            env=run_env,
            capture_output=True, 
            text=True,
            timeout=None  # No timeout for long-running processes
        )
        
        if result.stdout:
            logging.debug(f"Command stdout: {result.stdout}")
        if result.stderr:
            logging.debug(f"Command stderr: {result.stderr}")
        
        logging.info("Command completed successfully")
        return result
        
    except subprocess.CalledProcessError as e:
        logging.error(f"Container execution failed with exit code {e.returncode}")
        if e.stdout:
            logging.error(f"stdout: {e.stdout}")
        if e.stderr:
            logging.error(f"stderr: {e.stderr}")
        raise
    except subprocess.TimeoutExpired as e:
        logging.error(f"Command timed out after {e.timeout} seconds")
        raise
    except Exception as e:
        logging.error(f"Unexpected error during command execution: {e}")
        raise

def process_subject(subject, common, app, dry_run=False, force=False):
    """Process a single subject with comprehensive error handling."""
    logging.info(f"Starting processing for subject: {subject}")
    
    # Create temporary directory
    tmp_dir = os.path.join(common["tmp_folder"], subject)
    
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
        
        # Execute the command
        result = run_container(cmd, dry_run=dry_run)
        
        if not dry_run:
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
                logging.error(f"Processing failed for {subject} - no expected output found")
                logging.warning(f"Temp directory preserved for debugging: {tmp_dir}")
                return False
        
        return True
        
    except Exception as e:
        logging.error(f"Error processing subject {subject}: {e}")
        logging.warning(f"Temp directory preserved for debugging: {tmp_dir}")
        return False

def process_group(common, app, dry_run=False):
    """Process group-level analysis."""
    logging.info("Starting group-level processing")
    
    tmp_dir = os.path.join(common["tmp_folder"], "group")
    
    try:
        os.makedirs(tmp_dir, exist_ok=True)
        logging.debug(f"Created temp directory: {tmp_dir}")
        
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
        result = run_container(cmd, dry_run=dry_run)
        
        if not dry_run:
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
                
                success = process_subject(subject, common, app, args.dry_run, args.force)
                processed_subjects.append(subject)
                if not success:
                    failed_subjects.append(subject)
                    
            else:
                jobs = common.get("jobs", multiprocessing.cpu_count())
                logging.info(f"Processing {len(subjects)} subjects with {jobs} parallel jobs")
                
                if args.dry_run:
                    logging.info("DRY RUN MODE - No actual processing will occur")
                    for subject in subjects:
                        process_subject(subject, common, app, dry_run=True, force=args.force)
                        processed_subjects.append(subject)
                else:
                    # Use ProcessPoolExecutor for parallel processing
                    with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as executor:
                        # Submit all jobs
                        future_to_subject = {
                            executor.submit(process_subject, subject, common, app, False, args.force): subject
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
            success = process_group(common, app, args.dry_run)
            
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
