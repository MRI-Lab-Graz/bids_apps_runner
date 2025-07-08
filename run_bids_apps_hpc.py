#!/usr/bin/env python3
"""
BIDS App Runner for HPC with SLURM and DataLad integration

This script is designed for High Performance Computing environments with:
- SLURM job scheduling instead of multiprocessing
- DataLad for BIDS dataset management 
- Git/Git-annex for data versioning
- Separate output repositories for results

Author: BIDS Apps Runner Team
"""

import os
import sys
import json
import glob
import shutil
import random
import argparse
import subprocess
import logging
import tempfile
from datetime import datetime
from pathlib import Path

def setup_logging(log_level="INFO", log_dir=None):
    """Setup logging configuration with optional log directory."""
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f'bids_app_runner_hpc_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
    else:
        log_file = f'bids_app_runner_hpc_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
    
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file)
        ]
    )

def parse_args():
    parser = argparse.ArgumentParser(description="Run a BIDS App on HPC using SLURM and DataLad.")
    parser.add_argument("-x", "--config", required=True, help="Path to JSON config file.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                       help="Set logging level (default: INFO)")
    parser.add_argument("--dry-run", action="store_true", 
                       help="Show commands that would be run without executing them")
    parser.add_argument("--slurm-only", action="store_true",
                       help="Only generate SLURM job scripts without submitting")
    parser.add_argument("--subjects", nargs="+", 
                       help="Specific subjects to process (overrides config)")
    parser.add_argument("--job-template", 
                       help="Custom SLURM job template file")
    return parser.parse_args()

def read_config(path):
    """Read and validate JSON configuration file."""
    try:
        with open(path, "r") as f:
            config = json.load(f)
        
        # Add HPC-specific defaults if not present
        if "hpc" not in config:
            config["hpc"] = {}
        
        hpc_defaults = {
            "partition": "standard",
            "time": "24:00:00",
            "mem": "32G",
            "cpus": 8,
            "job_name": "bids_app",
            "output_pattern": "slurm-%j.out",
            "error_pattern": "slurm-%j.err",
            "modules": [],
            "environment": {}
        }
        
        for key, value in hpc_defaults.items():
            if key not in config["hpc"]:
                config["hpc"][key] = value
                
        return config
    except Exception as e:
        sys.exit(f"Error reading config: {e}")

def validate_hpc_config(hpc_config):
    """Validate HPC-specific configuration."""
    required_hpc = ["partition", "time", "mem", "cpus"]
    for key in required_hpc:
        if key not in hpc_config:
            logging.warning(f"Missing HPC config '{key}', using default")

def validate_datalad_config(datalad_config):
    """Validate DataLad-specific configuration."""
    required_datalad = ["input_repo", "output_repo"]
    for key in required_datalad:
        if key not in datalad_config:
            sys.exit(f"Missing required DataLad config: {key}")
    
    # Optional configurations with defaults
    defaults = {
        "clone_method": "clone",  # or "install"
        "get_data": True,
        "branch_per_subject": True,
        "output_branch": "results",
        "merge_strategy": "merge"  # or "rebase"
    }
    
    for key, value in defaults.items():
        if key not in datalad_config:
            datalad_config[key] = value

def run_command(cmd, dry_run=False, check=True, capture_output=False):
    """Execute command with optional dry run mode."""
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
    
    if dry_run:
        logging.info(f"DRY RUN - Would execute: {cmd_str}")
        return None
    
    logging.debug(f"Executing: {cmd_str}")
    
    try:
        result = subprocess.run(
            cmd, 
            check=check, 
            capture_output=capture_output, 
            text=True,
            shell=isinstance(cmd, str)
        )
        
        if capture_output:
            logging.debug(f"Command output: {result.stdout}")
            if result.stderr:
                logging.debug(f"Command stderr: {result.stderr}")
        
        return result
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed: {cmd_str}")
        if e.stderr:
            logging.error(f"Error output: {e.stderr}")
        raise

def setup_datalad_environment(datalad_config, work_dir, dry_run=False):
    """Setup DataLad repositories and environment."""
    input_repo = datalad_config["input_repo"]
    output_repo = datalad_config["output_repo"]
    
    # Create working directory structure
    bids_dir = os.path.join(work_dir, "input_data")
    output_dir = os.path.join(work_dir, "output_data")
    
    os.makedirs(work_dir, exist_ok=True)
    
    # Clone/install input repository
    logging.info(f"Setting up input data from {input_repo}")
    if datalad_config["clone_method"] == "install":
        cmd = ["datalad", "install", "-s", input_repo, bids_dir]
    else:
        cmd = ["datalad", "clone", input_repo, bids_dir]
    
    run_command(cmd, dry_run=dry_run)
    
    # Clone/install output repository
    logging.info(f"Setting up output repository from {output_repo}")
    if datalad_config["clone_method"] == "install":
        cmd = ["datalad", "install", "-s", output_repo, output_dir]
    else:
        cmd = ["datalad", "clone", output_repo, output_dir]
    
    run_command(cmd, dry_run=dry_run)
    
    return bids_dir, output_dir

def get_data_for_subject(subject, bids_dir, datalad_config, dry_run=False):
    """Get data for a specific subject using DataLad."""
    if not datalad_config.get("get_data", True):
        return
    
    logging.info(f"Getting data for subject {subject}")
    
    # Create subject-specific branch if configured
    if datalad_config.get("branch_per_subject", True):
        branch_name = f"processing-{subject}"
        cmd = f"cd {bids_dir} && git checkout -b {branch_name}"
        run_command(cmd, dry_run=dry_run)
    
    # Get subject data
    subject_pattern = os.path.join(bids_dir, subject)
    cmd = ["datalad", "get", subject_pattern]
    run_command(cmd, dry_run=dry_run)
    
    # Also get derivatives if they exist
    derivatives_pattern = os.path.join(bids_dir, "derivatives", "*", subject)
    if glob.glob(derivatives_pattern):
        cmd = ["datalad", "get", derivatives_pattern]
        run_command(cmd, dry_run=dry_run)

def create_slurm_job(subject, config, work_dir, script_path, dry_run=False):
    """Create SLURM job script for a single subject."""
    common = config["common"]
    app = config["app"]
    hpc = config["hpc"]
    datalad = config.get("datalad", {})
    
    job_name = f"{hpc['job_name']}_{subject}"
    job_script = os.path.join(work_dir, f"job_{subject}.sh")
    
    # Prepare paths for the job
    bids_dir = os.path.join(work_dir, "input_data")
    output_dir = os.path.join(work_dir, "output_data")
    tmp_dir = os.path.join(work_dir, "tmp", subject)
    
    script_content = f"""#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --partition={hpc['partition']}
#SBATCH --time={hpc['time']}
#SBATCH --mem={hpc['mem']}
#SBATCH --cpus-per-task={hpc['cpus']}
#SBATCH --output={hpc['output_pattern']}
#SBATCH --error={hpc['error_pattern']}

# Set up environment
set -e
set -u

echo "Starting job for subject: {subject}"
echo "Job ID: $SLURM_JOB_ID"
echo "Node: $SLURM_JOB_NODELIST"
echo "Start time: $(date)"

# Load modules
"""
    
    for module in hpc.get("modules", []):
        script_content += f"module load {module}\n"
    
    # Add environment variables
    for key, value in hpc.get("environment", {}).items():
        script_content += f"export {key}={value}\n"
    
    script_content += f"""
# Create temporary directory
mkdir -p {tmp_dir}

# Setup DataLad environment for this subject
cd {bids_dir}
"""
    
    if datalad.get("branch_per_subject", True):
        script_content += f"""
# Create and checkout subject branch
git checkout -b processing-{subject} || git checkout processing-{subject}
"""
    
    script_content += f"""
# Get subject data
datalad get {subject}

# Run the BIDS app
echo "Running BIDS app for {subject}"

apptainer run \\"""
    
    # Add apptainer arguments
    if app.get("apptainer_args"):
        for arg in app["apptainer_args"]:
            script_content += f" {arg} \\\n"
    else:
        script_content += " --containall \\\n"
    
    # Add bind mounts
    script_content += f"""    -B {tmp_dir}:/tmp \\
    -B {common['templateflow_dir']}:/templateflow \\
    -B {output_dir}:/output \\
    -B {bids_dir}:/bids \\"""
    
    if common.get("optional_folder"):
        script_content += f"\n    -B {common['optional_folder']}:/base \\"
    
    # Add custom mounts
    for mount in app.get("mounts", []):
        if mount.get("source") and mount.get("target"):
            script_content += f"\n    -B {mount['source']}:{mount['target']} \\"
    
    script_content += f"""
    --env TEMPLATEFLOW_HOME=/templateflow \\
    {common['container']} \\
    /bids /output {app.get('analysis_level', 'participant')} \\"""
    
    # Add app options
    for option in app.get("options", []):
        script_content += f"\n    {option} \\"
    
    script_content += f"""
    --participant-label {subject} \\
    -w /tmp

# Check if processing was successful
if [ $? -eq 0 ]; then
    echo "Processing completed successfully for {subject}"
    
    # Save results to output repository
    cd {output_dir}
    
    # Create output branch if it doesn't exist
    git checkout {datalad.get('output_branch', 'results')} || git checkout -b {datalad.get('output_branch', 'results')}
    
    # Add and save results
    datalad save -m "Add results for {subject} (job $SLURM_JOB_ID)"
    
    # Push to remote if configured
    if [ "{datalad.get('auto_push', 'false')}" = "true" ]; then
        datalad push
    fi
    
    # Clean up temporary directory
    rm -rf {tmp_dir}
    
else
    echo "Processing failed for {subject}"
    echo "Temporary directory preserved at: {tmp_dir}"
    exit 1
fi

echo "Job completed at: $(date)"
"""
    
    # Write job script
    if not dry_run:
        with open(job_script, 'w') as f:
            f.write(script_content)
        os.chmod(job_script, 0o755)
    else:
        logging.info(f"Would create job script: {job_script}")
        logging.debug(f"Job script content:\n{script_content}")
    
    return job_script

def submit_slurm_job(job_script, dry_run=False):
    """Submit a SLURM job and return job ID."""
    cmd = ["sbatch", job_script]
    
    if dry_run:
        logging.info(f"DRY RUN - Would submit: {' '.join(cmd)}")
        return "DRY_RUN_JOB_ID"
    
    result = run_command(cmd, capture_output=True)
    
    # Extract job ID from sbatch output
    output = result.stdout.strip()
    if "Submitted batch job" in output:
        job_id = output.split()[-1]
        logging.info(f"Submitted job {job_id}: {job_script}")
        return job_id
    else:
        logging.error(f"Failed to parse job ID from: {output}")
        return None

def get_subjects_from_datalad(bids_dir, datalad_config, dry_run=False):
    """Get list of subjects from DataLad repository."""
    if not os.path.exists(bids_dir):
        logging.error(f"BIDS directory not found: {bids_dir}")
        return []
    
    # Get basic dataset structure first
    cmd = ["datalad", "get", "-n", "."]  # Get directory structure only
    run_command(cmd, dry_run=dry_run)
    
    # Find subjects
    subjects = []
    for item in os.listdir(bids_dir):
        if item.startswith("sub-") and os.path.isdir(os.path.join(bids_dir, item)):
            subjects.append(item)
    
    return sorted(subjects)

def monitor_jobs(job_ids, poll_interval=60):
    """Monitor SLURM jobs and report status."""
    if not job_ids:
        return
    
    logging.info(f"Monitoring {len(job_ids)} jobs...")
    
    import time
    
    while job_ids:
        time.sleep(poll_interval)
        
        # Check job status
        cmd = ["squeue", "-j", ",".join(job_ids), "--format=%i,%T", "--noheader"]
        try:
            result = run_command(cmd, capture_output=True, check=False)
            
            if result.returncode == 0:
                running_jobs = []
                for line in result.stdout.strip().split('\n'):
                    if line:
                        job_id, status = line.split(',')
                        logging.info(f"Job {job_id}: {status}")
                        if status in ['PENDING', 'RUNNING']:
                            running_jobs.append(job_id)
                
                job_ids = running_jobs
            else:
                # No jobs found in queue (likely all completed)
                break
                
        except Exception as e:
            logging.error(f"Error checking job status: {e}")
            break
    
    logging.info("All jobs completed or no longer in queue")

def main():
    args = parse_args()
    
    # Read configuration
    config = read_config(args.config)
    
    # Setup logging
    log_dir = config.get("common", {}).get("log_dir")
    setup_logging(args.log_level, log_dir)
    
    # Validate configuration sections
    if "common" not in config:
        sys.exit("Config must contain 'common' section.")
    
    if "app" not in config:
        sys.exit("Config must contain 'app' section.")
    
    if "datalad" not in config:
        sys.exit("Config must contain 'datalad' section for HPC mode.")
    
    validate_hpc_config(config.get("hpc", {}))
    validate_datalad_config(config["datalad"])
    
    # Create work directory
    work_dir = config["common"].get("work_dir", "/tmp/bids_app_work")
    os.makedirs(work_dir, exist_ok=True)
    
    logging.info(f"Using work directory: {work_dir}")
    
    # Setup DataLad environment
    bids_dir, output_dir = setup_datalad_environment(
        config["datalad"], work_dir, args.dry_run
    )
    
    # Update common config with actual paths
    config["common"]["bids_folder"] = bids_dir
    config["common"]["output_folder"] = output_dir
    
    # Get subjects
    if args.subjects:
        subjects = [s if s.startswith("sub-") else f"sub-{s}" for s in args.subjects]
    else:
        subjects = get_subjects_from_datalad(bids_dir, config["datalad"], args.dry_run)
    
    if not subjects:
        sys.exit("No subjects found.")
    
    logging.info(f"Found {len(subjects)} subjects: {subjects}")
    
    # Handle pilot mode
    if config["common"].get("pilottest", False):
        subject = random.choice(subjects)
        subjects = [subject]
        logging.info(f"Pilot mode: processing only {subject}")
    
    # Create and submit SLURM jobs
    job_ids = []
    script_path = os.path.abspath(__file__)
    
    for subject in subjects:
        logging.info(f"Creating job for subject: {subject}")
        
        job_script = create_slurm_job(
            subject, config, work_dir, script_path, args.dry_run
        )
        
        if not args.slurm_only:
            job_id = submit_slurm_job(job_script, args.dry_run)
            if job_id:
                job_ids.append(job_id)
        else:
            logging.info(f"Job script created: {job_script}")
    
    if job_ids and not args.dry_run:
        logging.info(f"Submitted {len(job_ids)} jobs: {job_ids}")
        
        # Monitor jobs if requested
        if config.get("hpc", {}).get("monitor_jobs", False):
            monitor_jobs(job_ids)
    
    logging.info("HPC BIDS App Runner completed")

if __name__ == "__main__":
    main()
