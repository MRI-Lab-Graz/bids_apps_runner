#!/usr/bin/env python3
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
from datetime import datetime

def setup_logging(log_level="INFO"):
    """Setup logging configuration."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(f'bids_app_runner_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
        ]
    )

def parse_args():
    parser = argparse.ArgumentParser(description="Run a BIDS App using a JSON config.")
    parser.add_argument("-x", "--config", required=True, help="Path to JSON config file.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                       help="Set logging level (default: INFO)")
    parser.add_argument("--dry-run", action="store_true", 
                       help="Show commands that would be run without executing them")
    return parser.parse_args()

def read_config(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        sys.exit(f"Error reading config: {e}")

def validate_app_config(app):
    """Validate app-specific configuration."""
    # Check if apptainer_args is a list if provided
    if "apptainer_args" in app and not isinstance(app["apptainer_args"], list):
        sys.exit("'apptainer_args' must be a list")
    
    # Check if options is a list if provided
    if "options" in app and not isinstance(app["options"], list):
        sys.exit("'options' must be a list")
    
    # Check if mounts is a list of dictionaries if provided
    if "mounts" in app:
        if not isinstance(app["mounts"], list):
            sys.exit("'mounts' must be a list")
        for mount in app["mounts"]:
            if not isinstance(mount, dict) or "source" not in mount or "target" not in mount:
                sys.exit("Each mount must be a dictionary with 'source' and 'target' keys")
    
    # Validate output_check structure if provided
    if "output_check" in app:
        if not isinstance(app["output_check"], dict):
            sys.exit("'output_check' must be a dictionary")
        if "pattern" not in app["output_check"]:
            logging.warning("'output_check' defined but no 'pattern' specified - output checking disabled")

def validate_common_config(cfg):
    required = ["bids_folder", "output_folder", "tmp_folder", "container", "templateflow_dir"]
    for key in required:
        if key not in cfg:
            sys.exit(f"Missing required 'common' config: {key}")
    for path in [cfg["bids_folder"], cfg["templateflow_dir"]]:
        if not os.path.isdir(path):
            sys.exit(f"Missing directory: {path}")
    for path in [cfg["output_folder"], cfg["tmp_folder"]]:
        os.makedirs(path, exist_ok=True)
    if not os.path.isfile(cfg["container"]):
        sys.exit(f"Missing container: {cfg['container']}")
    if "optional_folder" in cfg and cfg["optional_folder"]:
        if not os.path.isdir(cfg["optional_folder"]):
            sys.exit(f"Missing optional_folder: {cfg['optional_folder']}")

def subject_processed(subject, common, app):
    pattern = app.get("output_check", {}).get("pattern", "")
    if not pattern:
        return False
    check_dir = os.path.join(common["output_folder"], app["output_check"].get("directory", ""))
    full_pattern = os.path.join(check_dir, pattern.replace("{subject}", subject))
    return bool(glob.glob(full_pattern))

def build_common_mounts(common, tmp_dir):
    mounts = [
        f"{tmp_dir}:/tmp",
        f"{common['templateflow_dir']}:/templateflow",
        f"{common['output_folder']}:/output",
        f"{common['bids_folder']}:/bids"
    ]
    if common.get("optional_folder"):
        mounts.append(f"{common['optional_folder']}:/base")
    return mounts

def run_container(cmd, env=None, dry_run=False):
    """Execute container command with optional dry run mode."""
    try:
        cmd_str = " ".join(cmd)
        if dry_run:
            logging.info(f"DRY RUN - Would execute: {cmd_str}")
            return
        
        logging.info(f"Running command: {cmd_str}")
        result = subprocess.run(cmd, check=True, env=env or os.environ.copy(), 
                              capture_output=True, text=True)
        logging.debug(f"Command output: {result.stdout}")
        
    except subprocess.CalledProcessError as e:
        logging.error(f"Container execution failed: {e}")
        if e.stderr:
            logging.error(f"Error output: {e.stderr}")
        raise

def process_subject(subject, common, app, dry_run=False):
    tmp_dir = os.path.join(common["tmp_folder"], subject)
    os.makedirs(tmp_dir, exist_ok=True)

    if subject_processed(subject, common, app):
        logging.info(f"Skipping subject '{subject}', already processed.")
        shutil.rmtree(tmp_dir)
        return

    logging.info(f"Processing subject: {subject}")
    cmd = ["apptainer", "run"]
    
    # Add app-specific apptainer arguments if configured
    if app.get("apptainer_args"):
        cmd.extend(app["apptainer_args"])
    else:
        cmd.append("--containall")
    
    for mnt in build_common_mounts(common, tmp_dir):
        cmd.extend(["-B", mnt])
    for mount in app.get("mounts", []):
        if mount.get("source") and mount.get("target"):
            cmd.extend(["-B", f"{mount['source']}:{mount['target']}"])
    cmd.extend([
        "--env", f"TEMPLATEFLOW_HOME=/templateflow",
        common["container"],
        "/bids", "/output", app.get("analysis_level", "participant")
    ])
    cmd.extend(app.get("options", []))
    cmd.extend(["--participant-label", subject, "-w", "/tmp"])

    run_container(cmd, dry_run=dry_run)

    if not dry_run:
        if subject_processed(subject, common, app):
            logging.info(f"Subject {subject} completed. Cleaning up.")
            shutil.rmtree(tmp_dir)
        else:
            logging.warning(f"Output missing for {subject}, temp dir preserved at {tmp_dir}")

def process_group(common, app, dry_run=False):
    tmp_dir = os.path.join(common["tmp_folder"], "group")
    os.makedirs(tmp_dir, exist_ok=True)
    cmd = ["apptainer", "run"]
    
    # Add app-specific apptainer arguments if configured
    if app.get("apptainer_args"):
        cmd.extend(app["apptainer_args"])
    else:
        cmd.append("--containall")
        
    for mnt in build_common_mounts(common, tmp_dir):
        cmd.extend(["-B", mnt])
    for mount in app.get("mounts", []):
        if mount.get("source") and mount.get("target"):
            cmd.extend(["-B", f"{mount['source']}:{mount['target']}"])
    cmd.extend([
        "--env", f"TEMPLATEFLOW_HOME=/templateflow",
        common["container"],
        "/bids", "/output", app.get("analysis_level", "group"),
        "-w", "/tmp"
    ])
    cmd.extend(app.get("options", []))
    run_container(cmd, dry_run=dry_run)
    
    if not dry_run:
        shutil.rmtree(tmp_dir)

def main():
    args = parse_args()
    setup_logging(args.log_level)
    
    config = read_config(args.config)

    if "common" not in config or "app" not in config:
        sys.exit("Config must contain 'common' and 'app' sections.")

    common, app = config["common"], config["app"]
    validate_common_config(common)
    validate_app_config(app)
    validate_app_config(app)

    subjects = []
    level = app.get("analysis_level", "participant")

    if level == "participant":
        if "participant_labels" in app and app["participant_labels"]:
            subjects = [s if s.startswith("sub-") else f"sub-{s}" for s in app["participant_labels"]]
        else:
            subjects = [d for d in os.listdir(common["bids_folder"])
                        if d.startswith("sub-") and os.path.isdir(os.path.join(common["bids_folder"], d))]

        if not subjects:
            sys.exit("No subjects found.")

        if common.get("pilottest", False):
            subject = random.choice(subjects)
            logging.info(f"Pilot mode: running one subject ({subject})")
            process_subject(subject, common, app, args.dry_run)
        else:
            jobs = common.get("jobs", multiprocessing.cpu_count())
            logging.info(f"Processing {len(subjects)} subjects with {jobs} parallel jobs.")
            
            if args.dry_run:
                logging.info("DRY RUN MODE - No actual processing will occur")
                for subject in subjects:
                    process_subject(subject, common, app, dry_run=True)
            else:
                with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as executor:
                    futures = [executor.submit(process_subject, sub, common, app, False) for sub in subjects]
                    for f in concurrent.futures.as_completed(futures):
                        try:
                            f.result()
                        except Exception as e:
                            logging.error(f"Error processing subject: {e}")
    else:
        logging.info(f"Running {level} level analysis.")
        process_group(common, app, args.dry_run)

if __name__ == "__main__":
    main()
