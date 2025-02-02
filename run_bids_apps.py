#!/usr/bin/env python3
"""
run_bidsapp.py

This script runs a BIDS App container (such as fmriprep, mriqc, etc.) based entirely
on a JSON configuration file that defines both the common parameters (paths, parallel
processing options, etc.) and app-specific settings (analysis level, container options,
additional mounts, and output-checking).

Usage:
    run_bidsapp.py -x config.json

The JSON file should have two sections: "common" and "app". See the example above.
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

def parse_args():
    parser = argparse.ArgumentParser(
        description="Run a BIDS App container with all parameters defined in a JSON config file.")
    parser.add_argument("-x", "--config", required=True,
                        help="Path to the configuration JSON file")
    return parser.parse_args()

def read_config(config_file):
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
    except Exception as e:
        sys.exit(f"Error reading config file: {e}")
    return config

def validate_common_config(common):
    required_keys = ["bids_folder", "output_folder", "tmp_folder", "container"]
    for key in required_keys:
        if key not in common:
            sys.exit(f"Error: '{key}' is required in the 'common' section of the config file.")
    # Check that the BIDS folder exists.
    if not os.path.isdir(common["bids_folder"]):
        sys.exit(f"Error: BIDS folder '{common['bids_folder']}' does not exist.")
    # Create output and tmp directories if they don't exist.
    os.makedirs(common["output_folder"], exist_ok=True)
    os.makedirs(common["tmp_folder"], exist_ok=True)
    # Check that the container file exists.
    if not os.path.isfile(common["container"]):
        sys.exit(f"Error: Container file '{common['container']}' does not exist.")
    # If optional_folder is provided, verify that it exists.
    if "optional_folder" in common and common["optional_folder"]:
        if not os.path.isdir(common["optional_folder"]):
            sys.exit(f"Error: Optional folder '{common['optional_folder']}' does not exist.")

def validate_app_config(app):
    # Provide defaults for optional app-specific settings.
    app.setdefault("analysis_level", "participant")
    app.setdefault("options", [])
    app.setdefault("mounts", [])
    app.setdefault("output_check", {})

def subject_processed(subject, common, app):
    """Determine if a subject has already been processed using the output_check settings.
    
    If output_check defines a directory and a filename pattern (with a {subject} placeholder),
    then this function returns True if a matching file is found.
    """
    output_check = app.get("output_check", {})
    pattern = output_check.get("pattern", "")
    if not pattern:
        return False
    check_dir = os.path.join(common["output_folder"], output_check.get("directory", ""))
    file_pattern = pattern.replace("{subject}", subject)
    full_pattern = os.path.join(check_dir, file_pattern)
    matches = glob.glob(full_pattern)
    return len(matches) > 0

def run_container(cmd):
    """Run the container command and print it."""
    try:
        print("Running command:", " ".join(cmd))
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running container command: {e}")

def process_subject(subject, common, app):
    # Ensure the parent temporary folder exists.
    os.makedirs(common["tmp_folder"], exist_ok=True)

    """Process a single subject: build and run the container command for that subject."""
    tmp_subject = os.path.join(common["tmp_folder"], subject)
    os.makedirs(tmp_subject, exist_ok=True)
    if subject_processed(subject, common, app):
        print(f"Skipping subject '{subject}': output already exists.")
        shutil.rmtree(tmp_subject)
        return
    print(f"Processing subject: {subject}")
    cmd = ["apptainer", "run", "--containall", "--writable-tmpfs"]
    cmd.extend(["-B", f"{tmp_subject}:/tmp"])
    cmd.extend(["-B", f"{common['output_folder']}:/output"])
    cmd.extend(["-B", f"{common['bids_folder']}:/bids"])
    if "optional_folder" in common and common["optional_folder"]:
        cmd.extend(["-B", f"{common['optional_folder']}:/base"])
    # Process extra mounts from the app configuration.
    for mount in app.get("mounts", []):
        src = mount.get("source")
        tgt = mount.get("target")
        if src and tgt:
            cmd.extend(["-B", f"{src}:{tgt}"])
    cmd.append(common["container"])
    container_options = app.get("options", [])
    analysis_level = app.get("analysis_level", "participant")
    cmd.extend(["/bids", "/output", analysis_level])
    cmd.extend(container_options)
    if analysis_level == "participant":
        cmd.extend(["--participant-label", subject])
    cmd.extend(["-w", "/tmp"])
    run_container(cmd)
# After processing, check for the expected output file.
    if subject_processed(subject, common, app):
        print(f"Subject {subject} processed successfully. Removing temporary folder.")
        shutil.rmtree(tmp_subject)
    else:
        print(f"Subject {subject} did not produce the expected output. Temporary folder '{tmp_subject}' is preserved for inspection.")

def process_group(common, app):
    """Process group-level analysis (non-participant): run the container once."""
    tmp_group = os.path.join(common["tmp_folder"], "group_analysis")
    os.makedirs(tmp_group, exist_ok=True)
    cmd = ["apptainer", "run", "--containall", "--writable-tmpfs"]
    cmd.extend(["-B", f"{tmp_group}:/tmp"])
    cmd.extend(["-B", f"{common['output_folder']}:/output"])
    cmd.extend(["-B", f"{common['bids_folder']}:/bids"])
    if "optional_folder" in common and common["optional_folder"]:
        cmd.extend(["-B", f"{common['optional_folder']}:/base"])
    for mount in app.get("mounts", []):
        src = mount.get("source")
        tgt = mount.get("target")
        if src and tgt:
            cmd.extend(["-B", f"{src}:{tgt}"])
    cmd.append(common["container"])
    container_options = app.get("options", [])
    analysis_level = app.get("analysis_level", "participant")
    cmd.extend(["/bids", "/output", analysis_level])
    cmd.extend(container_options)
    cmd.extend(["-w", "/tmp"])
    run_container(cmd)
    shutil.rmtree(tmp_group)

def main():
    args = parse_args()
    config = read_config(args.config)
    # The config must have both "common" and "app" sections.
    if "common" not in config or "app" not in config:
        sys.exit("Error: Config file must contain both 'common' and 'app' sections.")
    common = config["common"]
    app = config["app"]
    validate_common_config(common)
    validate_app_config(app)
    
    analysis_level = app.get("analysis_level", "participant")
    if analysis_level == "participant":
        # Find subject directories (those starting with "sub-") in the BIDS folder.
        subjects = [d for d in os.listdir(common["bids_folder"])
                    if d.startswith("sub-") and os.path.isdir(os.path.join(common["bids_folder"], d))]
        if not subjects:
            sys.exit("Error: No subjects found in the BIDS folder.")
        print(f"Found {len(subjects)} subjects.")
        jobs = common.get("jobs", multiprocessing.cpu_count())
        pilottest = common.get("pilottest", False)
        if pilottest:
            subject = random.choice(subjects)
            print(f"Pilot test mode: processing subject '{subject}' only.")
            process_subject(subject, common, app)
        else:
            print(f"Processing subjects in parallel using {jobs} jobs.")
            with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as executor:
                futures = [executor.submit(process_subject, subject, common, app) for subject in subjects]
                for future in concurrent.futures.as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        print(f"Error processing subject: {e}")
    else:
        # Group-level analysis: run the container once.
        print(f"Running group-level analysis (analysis_level: {analysis_level})")
        process_group(common, app)

if __name__ == '__main__':
    main()