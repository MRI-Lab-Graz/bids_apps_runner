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

def parse_args():
    parser = argparse.ArgumentParser(description="Run a BIDS App using a JSON config.")
    parser.add_argument("-x", "--config", required=True, help="Path to JSON config file.")
    return parser.parse_args()

def read_config(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        sys.exit(f"Error reading config: {e}")

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

def run_container(cmd, env=None):
    try:
        print("Running command:", " ".join(cmd))
        subprocess.run(cmd, check=True, env=env or os.environ.copy())
    except subprocess.CalledProcessError as e:
        print(f"Container execution failed: {e}")

def process_subject(subject, common, app):
    tmp_dir = os.path.join(common["tmp_folder"], subject)
    os.makedirs(tmp_dir, exist_ok=True)

    if subject_processed(subject, common, app):
        print(f"Skipping subject '{subject}', already processed.")
        shutil.rmtree(tmp_dir)
        return

    print(f"Processing subject: {subject}")
    cmd = ["apptainer", "run", "--containall"]
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

    run_container(cmd)

    if subject_processed(subject, common, app):
        print(f"Subject {subject} completed. Cleaning up.")
        shutil.rmtree(tmp_dir)
    else:
        print(f"Output missing for {subject}, temp dir preserved at {tmp_dir}")

def process_group(common, app):
    tmp_dir = os.path.join(common["tmp_folder"], "group")
    os.makedirs(tmp_dir, exist_ok=True)
    cmd = ["apptainer", "run", "--containall"]
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
    run_container(cmd)
    shutil.rmtree(tmp_dir)

def main():
    args = parse_args()
    config = read_config(args.config)

    if "common" not in config or "app" not in config:
        sys.exit("Config must contain 'common' and 'app' sections.")

    common, app = config["common"], config["app"]
    validate_common_config(common)

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
            print(f"Pilot mode: running one subject ({subject})")
            process_subject(subject, common, app)
        else:
            jobs = common.get("jobs", multiprocessing.cpu_count())
            print(f"Processing {len(subjects)} subjects with {jobs} parallel jobs.")
            with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as executor:
                futures = [executor.submit(process_subject, sub, common, app) for sub in subjects]
                for f in concurrent.futures.as_completed(futures):
                    try:
                        f.result()
                    except Exception as e:
                        print(f"Error: {e}")
    else:
        print(f"Running {level} level analysis.")
        process_group(common, app)

if __name__ == "__main__":
    main()
