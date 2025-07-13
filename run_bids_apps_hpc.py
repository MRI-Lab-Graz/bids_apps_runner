#!/usr/bin/env python3
"""
BIDS App Runner for HPC with SLURM and DataLad integration - Production Version

This script is designed for High Performance Computing environments with:
- SLURM job scheduling instead of multiprocessing
- DataLad for BIDS dataset management 
- Git/Git-annex for data versioning
- Separate output repositories for results

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
import logging
import tempfile
import signal
import time
from datetime import datetime
from pathlib import Path

def setup_logging(log_level="INFO", log_dir=None):
    """Setup logging configuration with optional log directory."""
    # Create logs directory if not specified
    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f'bids_app_runner_hpc_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
    else:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f'bids_app_runner_hpc_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
    
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
        description="Run a BIDS App on HPC using SLURM and DataLad",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s -x config_hpc.json
  %(prog)s -x config_hpc.json --dry-run
  %(prog)s -x config_hpc.json --slurm-only
  %(prog)s -x config_hpc.json --subjects sub-001 sub-002
  %(prog)s -x config_hpc.json --log-level DEBUG
  %(prog)s -x config_hpc.json --debug --subjects sub-001  # Debug with container logs
  
For more information, see README_HPC.md
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
        "--slurm-only", 
        action="store_true",
        help="Only generate SLURM job scripts without submitting"
    )
    
    parser.add_argument(
        "--subjects", 
        nargs="+", 
        help="Specific subjects to process (overrides config)"
    )
    
    parser.add_argument(
        "--job-template", 
        help="Custom SLURM job template file"
    )
    
    parser.add_argument(
        "--force", 
        action="store_true", 
        help="Force reprocessing of subjects even if output exists"
    )
    
    parser.add_argument(
        "--debug", 
        action="store_true", 
        help="Enable debug mode with detailed container execution logs in SLURM jobs"
    )
    
    parser.add_argument(
        "--version", 
        action="version", 
        version="BIDS App Runner HPC 2.0.0"
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
            "environment": {},
            "monitor_jobs": False
        }
        
        for key, value in hpc_defaults.items():
            if key not in config["hpc"]:
                config["hpc"][key] = value
                
        return config
        
    except json.JSONDecodeError as e:
        logging.error(f"Invalid JSON in config file: {e}")
        sys.exit(f"Error parsing config file: {e}")
    except Exception as e:
        logging.error(f"Error reading config file: {e}")
        sys.exit(f"Error reading config: {e}")

def validate_hpc_config(hpc_config):
    """Validate HPC-specific configuration."""
    logging.info("Validating HPC configuration...")
    
    required_hpc = ["partition", "time", "mem", "cpus"]
    missing = []
    
    for key in required_hpc:
        if key not in hpc_config:
            missing.append(key)
    
    if missing:
        logging.warning(f"Missing HPC config keys: {', '.join(missing)}, using defaults")
    
    # Validate resource specifications
    if "mem" in hpc_config:
        mem = hpc_config["mem"]
        if not isinstance(mem, str) or not any(mem.endswith(unit) for unit in ["G", "M", "K", "GB", "MB", "KB"]):
            logging.warning(f"Invalid memory specification: {mem}, using default")
            hpc_config["mem"] = "32G"
    
    if "cpus" in hpc_config:
        cpus = hpc_config["cpus"]
        if not isinstance(cpus, int) or cpus < 1:
            logging.warning(f"Invalid CPU count: {cpus}, using default")
            hpc_config["cpus"] = 8
    
    # Validate time format (HH:MM:SS)
    if "time" in hpc_config:
        time_str = hpc_config["time"]
        if not isinstance(time_str, str) or len(time_str.split(':')) != 3:
            logging.warning(f"Invalid time format: {time_str}, using default")
            hpc_config["time"] = "24:00:00"
    
    logging.info("HPC configuration validation completed")

def validate_datalad_config(datalad_config):
    """Validate DataLad-specific configuration."""
    logging.info("Validating DataLad configuration...")
    
    required_datalad = ["input_repo", "output_repo"]
    missing = []
    
    for key in required_datalad:
        if key not in datalad_config:
            missing.append(key)
    
    if missing:
        logging.error(f"Missing required DataLad config keys: {', '.join(missing)}")
        sys.exit(f"ERROR: Missing required DataLad config: {', '.join(missing)}")
    
    # Validate repository URLs
    for key in ["input_repo", "output_repo"]:
        repo_url = datalad_config[key]
        if not isinstance(repo_url, str) or not repo_url.strip():
            logging.error(f"Invalid repository URL for {key}: {repo_url}")
            sys.exit(f"ERROR: Invalid repository URL for {key}")
    
    # Optional configurations with defaults
    defaults = {
        "clone_method": "clone",  # or "install"
        "get_data": True,
        "branch_per_subject": True,
        "output_branch": "results",
        "merge_strategy": "merge",  # or "rebase"
        "auto_push": False
    }
    
    for key, value in defaults.items():
        if key not in datalad_config:
            datalad_config[key] = value
    
    # Validate clone method
    if datalad_config["clone_method"] not in ["clone", "install"]:
        logging.warning(f"Invalid clone method: {datalad_config['clone_method']}, using 'clone'")
        datalad_config["clone_method"] = "clone"
    
    logging.info("DataLad configuration validation completed")

def validate_common_config(cfg):
    """Validate common configuration section."""
    logging.info("Validating common configuration...")
    
    required = ["templateflow_dir", "container"]
    missing = []
    
    for key in required:
        if key not in cfg:
            missing.append(key)
    
    if missing:
        logging.error(f"Missing required 'common' config keys: {', '.join(missing)}")
        sys.exit(f"ERROR: Missing required 'common' config: {', '.join(missing)}")
    
    # Set default work_dir if not specified
    if "work_dir" not in cfg:
        cfg["work_dir"] = "/tmp/bids_app_work"
        logging.info(f"Using default work_dir: {cfg['work_dir']}")
    
    # Validate templateflow_dir exists
    if not os.path.isdir(cfg["templateflow_dir"]):
        logging.error(f"TemplateFlow directory not found: {cfg['templateflow_dir']}")
        sys.exit(f"ERROR: Missing templateflow_dir: {cfg['templateflow_dir']}")
    
    # Validate container file exists
    if not os.path.isfile(cfg["container"]):
        logging.error(f"Container file not found: {cfg['container']}")
        sys.exit(f"ERROR: Missing container file: {cfg['container']}")
    
    logging.info("Common configuration validation completed")

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

def run_command(cmd, dry_run=False, check=True, capture_output=False):
    """Execute command with optional dry run mode and comprehensive error handling."""
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
            shell=isinstance(cmd, str),
            timeout=3600  # 1 hour timeout for most commands
        )
        
        if capture_output:
            if result.stdout:
                logging.debug(f"Command stdout: {result.stdout}")
            if result.stderr:
                logging.debug(f"Command stderr: {result.stderr}")
        
        return result
        
    except subprocess.CalledProcessError as e:
        logging.error(f"Command failed with exit code {e.returncode}: {cmd_str}")
        if e.stdout:
            logging.error(f"stdout: {e.stdout}")
        if e.stderr:
            logging.error(f"stderr: {e.stderr}")
        raise
    except subprocess.TimeoutExpired as e:
        logging.error(f"Command timed out after {e.timeout} seconds: {cmd_str}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error executing command: {e}")
        raise

def setup_datalad_environment(datalad_config, work_dir, dry_run=False):
    """Setup DataLad repositories and environment."""
    logging.info("Setting up DataLad environment...")
    
    input_repo = datalad_config["input_repo"]
    output_repo = datalad_config["output_repo"]
    
    # Create working directory structure
    bids_dir = os.path.join(work_dir, "input_data")
    output_dir = os.path.join(work_dir, "output_data")
    
    try:
        os.makedirs(work_dir, exist_ok=True)
        logging.info(f"Created work directory: {work_dir}")
        
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
        
        logging.info("DataLad environment setup completed")
        return bids_dir, output_dir
        
    except Exception as e:
        logging.error(f"Error setting up DataLad environment: {e}")
        raise

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

def create_slurm_job(subject, config, work_dir, script_path, dry_run=False, debug=False):
    """Create SLURM job script for a single subject."""
    logging.info(f"Creating SLURM job script for subject: {subject}")
    
    try:
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
        
        # Create logs directory in work_dir
        logs_dir = os.path.join(work_dir, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        
        # Create container logs directory if debug mode is enabled
        container_logs_dir = None
        if debug:
            container_logs_dir = os.path.join(work_dir, "container_logs")
            os.makedirs(container_logs_dir, exist_ok=True)
        
        # Update output/error patterns with full paths
        output_file = os.path.join(logs_dir, hpc['output_pattern'].replace('%j', '$SLURM_JOB_ID'))
        error_file = os.path.join(logs_dir, hpc['error_pattern'].replace('%j', '$SLURM_JOB_ID'))
        
        script_content = f"""#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --partition={hpc['partition']}
#SBATCH --time={hpc['time']}
#SBATCH --mem={hpc['mem']}
#SBATCH --cpus-per-task={hpc['cpus']}
#SBATCH --output={output_file}
#SBATCH --error={error_file}

# Set up environment
set -e
set -u

echo "Starting job for subject: {subject}"
echo "Job ID: $SLURM_JOB_ID"
echo "Node: $SLURM_JOB_NODELIST"
echo "Start time: $(date)"
echo "Working directory: {work_dir}"
{f'echo "Debug mode: Container logs will be saved to {container_logs_dir}"' if debug else ''}
echo ""

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
echo "Created temporary directory: {tmp_dir}"

# Setup DataLad environment for this subject
cd {bids_dir}
echo "Changed to BIDS directory: {bids_dir}"
"""
        
        if datalad.get("branch_per_subject", True):
            script_content += f"""
# Create and checkout subject branch
echo "Creating subject branch: processing-{subject}"
git checkout -b processing-{subject} 2>/dev/null || git checkout processing-{subject}
if [ $? -eq 0 ]; then
    echo "Successfully switched to branch processing-{subject}"
else
    echo "Warning: Could not switch to branch processing-{subject}"
fi
"""
        
        script_content += f"""
# Get subject data
echo "Getting subject data for {subject}"
datalad get {subject}
if [ $? -eq 0 ]; then
    echo "Successfully retrieved data for {subject}"
else
    echo "Error: Failed to retrieve data for {subject}"
    exit 1
fi

# Run the BIDS app
echo "Running BIDS app for {subject}"
echo "Container: {common['container']}"

apptainer run \\"""
        
        # Add apptainer arguments
        if app.get("apptainer_args"):
            for arg in app["apptainer_args"]:
                script_content += f"    {arg} \\\n"
        else:
            script_content += "    --containall \\\n"
        
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
        
        # Clean subject label (remove sub- prefix if present)
        subject_label = subject.replace("sub-", "")
        
        # Add debug logging setup if debug mode is enabled
        container_log_redirection = ""
        if debug:
            timestamp = "$(date +%Y%m%d_%H%M%S)"
            container_stdout_log = f"{container_logs_dir}/container_{subject}_{timestamp}.log"
            container_stderr_log = f"{container_logs_dir}/container_{subject}_{timestamp}.err"
            container_log_redirection = f" > >(tee {container_stdout_log}) 2> >(tee {container_stderr_log} >&2)"
            
        script_content += f"""
    --participant-label {subject_label} \\
    -w /tmp{container_log_redirection}

# Check if processing was successful
if [ $? -eq 0 ]; then
    echo "Processing completed successfully for {subject}"
    
    # Save results to output repository
    cd {output_dir}
    echo "Changed to output directory: {output_dir}"
    
    # Create output branch if it doesn't exist
    echo "Setting up output branch: {datalad.get('output_branch', 'results')}"
    git checkout {datalad.get('output_branch', 'results')} 2>/dev/null || git checkout -b {datalad.get('output_branch', 'results')}
    
    # Add and save results
    echo "Saving results for {subject}"
    datalad save -m "Add results for {subject} (job $SLURM_JOB_ID)" || echo "Warning: Could not save results"
    
    # Push to remote if configured
    if [ "{datalad.get('auto_push', 'false')}" = "true" ]; then
        echo "Pushing results to remote"
        datalad push || echo "Warning: Could not push results"
    fi
    
    # Clean up temporary directory
    echo "Cleaning up temporary directory: {tmp_dir}"
    rm -rf {tmp_dir}
    
    echo "Job completed successfully for {subject}"
    
else
    echo "Processing failed for {subject}"
    echo "Temporary directory preserved at: {tmp_dir}"
    echo "Check logs for details"
    exit 1
fi

echo "Job completed at: $(date)"
echo "Total job duration: $SECONDS seconds"
"""
        
        # Write job script
        if not dry_run:
            with open(job_script, 'w') as f:
                f.write(script_content)
            os.chmod(job_script, 0o755)
            logging.info(f"Created job script: {job_script}")
        else:
            logging.info(f"Would create job script: {job_script}")
            logging.debug(f"Job script content:\n{script_content}")
        
        return job_script
        
    except Exception as e:
        logging.error(f"Error creating job script for {subject}: {e}")
        raise

def submit_slurm_job(job_script, dry_run=False):
    """Submit a SLURM job and return job ID."""
    cmd = ["sbatch", job_script]
    
    if dry_run:
        logging.info(f"DRY RUN - Would submit: {' '.join(cmd)}")
        return "DRY_RUN_JOB_ID"
    
    try:
        result = run_command(cmd, capture_output=True)
        
        # Extract job ID from sbatch output
        output = result.stdout.strip()
        if "Submitted batch job" in output:
            job_id = output.split()[-1]
            logging.info(f"Submitted job {job_id}: {job_script}")
            return job_id
        else:
            logging.error(f"Failed to parse job ID from sbatch output: {output}")
            return None
            
    except Exception as e:
        logging.error(f"Error submitting job {job_script}: {e}")
        return None

def get_subjects_from_datalad(bids_dir, datalad_config, dry_run=False):
    """Get list of subjects from DataLad repository."""
    logging.info("Discovering subjects from DataLad repository...")
    
    if not os.path.exists(bids_dir):
        logging.error(f"BIDS directory not found: {bids_dir}")
        return []
    
    try:
        # Get basic dataset structure first
        os.chdir(bids_dir)
        cmd = ["datalad", "get", "-n", "."]  # Get directory structure only
        run_command(cmd, dry_run=dry_run)
        
        # Find subjects
        subjects = []
        for item in os.listdir(bids_dir):
            if item.startswith("sub-") and os.path.isdir(os.path.join(bids_dir, item)):
                subjects.append(item)
        
        subjects = sorted(subjects)
        logging.info(f"Found {len(subjects)} subjects: {subjects}")
        return subjects
        
    except Exception as e:
        logging.error(f"Error discovering subjects: {e}")
        return []

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

def signal_handler(signum, frame):
    """Handle interrupt signals gracefully."""
    logging.warning(f"Received signal {signum}, attempting graceful shutdown...")
    sys.exit(1)

def subject_processed(subject, output_dir, app, force=False):
    """Check if a subject has already been processed."""
    if force:
        return False
        
    pattern = app.get("output_check", {}).get("pattern", "")
    if not pattern:
        logging.debug(f"No output check pattern defined for {subject}")
        return False
        
    check_dir = os.path.join(output_dir, app["output_check"].get("directory", ""))
    full_pattern = os.path.join(check_dir, pattern.replace("{subject}", subject))
    
    matches = glob.glob(full_pattern)
    if matches:
        logging.debug(f"Found existing output for {subject}: {matches}")
        return True
    
    return False

def print_summary(processed_subjects, failed_subjects, total_time):
    """Print a summary of the processing results."""
    logging.info("=" * 60)
    logging.info("HPC PROCESSING SUMMARY")
    logging.info("=" * 60)
    logging.info(f"Total subjects processed: {len(processed_subjects)}")
    logging.info(f"Successfully submitted: {len(processed_subjects) - len(failed_subjects)}")
    logging.info(f"Failed to submit: {len(failed_subjects)}")
    logging.info(f"Total setup time: {total_time:.2f} seconds")
    
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
        # Parse arguments
        args = parse_args()
        
        # Read configuration
        config = read_config(args.config)
        
        # Setup logging
        log_dir = config.get("common", {}).get("log_dir")
        log_file = setup_logging(args.log_level, log_dir)
        
        logging.info("BIDS App Runner HPC 2.0.0 starting...")
        logging.info(f"Command line: {' '.join(sys.argv)}")
        
        # Validate configuration sections
        if "common" not in config:
            logging.error("Config must contain 'common' section")
            sys.exit("ERROR: Config must contain 'common' section.")
        
        if "app" not in config:
            logging.error("Config must contain 'app' section")
            sys.exit("ERROR: Config must contain 'app' section.")
        
        if "datalad" not in config:
            logging.error("Config must contain 'datalad' section for HPC mode")
            sys.exit("ERROR: Config must contain 'datalad' section for HPC mode.")
        
        # Validate configurations
        validate_hpc_config(config.get("hpc", {}))
        validate_datalad_config(config["datalad"])
        validate_common_config(config["common"])
        validate_app_config(config["app"])
        
        # Create work directory
        work_dir = config["common"].get("work_dir", "/tmp/bids_app_work")
        try:
            os.makedirs(work_dir, exist_ok=True)
            logging.info(f"Using work directory: {work_dir}")
        except Exception as e:
            logging.error(f"Cannot create work directory: {e}")
            sys.exit(f"ERROR: Cannot create work directory: {e}")
        
        # Setup DataLad environment
        try:
            bids_dir, output_dir = setup_datalad_environment(
                config["datalad"], work_dir, args.dry_run
            )
            
            # Update common config with actual paths
            config["common"]["bids_folder"] = bids_dir
            config["common"]["output_folder"] = output_dir
            
        except Exception as e:
            logging.error(f"Error setting up DataLad environment: {e}")
            sys.exit(f"ERROR: DataLad setup failed: {e}")
        
        # Get subjects
        subjects = []
        if args.subjects:
            subjects = [s if s.startswith("sub-") else f"sub-{s}" for s in args.subjects]
            logging.info(f"Using subjects from command line: {subjects}")
        else:
            subjects = get_subjects_from_datalad(bids_dir, config["datalad"], args.dry_run)
        
        if not subjects:
            logging.error("No subjects found to process")
            sys.exit("ERROR: No subjects found.")
        
        logging.info(f"Found {len(subjects)} subjects to process")
        
        # Handle pilot mode
        if config["common"].get("pilottest", False):
            subject = random.choice(subjects)
            subjects = [subject]
            logging.info(f"Pilot mode: processing only {subject}")
        
        # Create and submit SLURM jobs
        submitted_jobs = []
        failed_jobs = []
        script_path = os.path.abspath(__file__)
        
        for subject in subjects:
            try:
                logging.info(f"Creating job for subject: {subject}")
                
                job_script = create_slurm_job(
                    subject, config, work_dir, script_path, args.dry_run, args.debug
                )
                
                if not args.slurm_only:
                    job_id = submit_slurm_job(job_script, args.dry_run)
                    if job_id:
                        submitted_jobs.append(job_id)
                    else:
                        failed_jobs.append(f"Subject {subject} - job submission failed")
                else:
                    logging.info(f"Job script created: {job_script}")
                    
            except Exception as e:
                logging.error(f"Error creating/submitting job for subject {subject}: {e}")
                failed_jobs.append(f"Subject {subject} - {str(e)}")
        
        # Print summary
        end_time = time.time()
        print_summary(submitted_jobs, failed_jobs, end_time - start_time)
        
        # Monitor jobs if requested and jobs were submitted
        if (submitted_jobs and not args.dry_run and 
            config.get("hpc", {}).get("monitor_jobs", False)):
            logging.info("Starting job monitoring...")
            monitor_jobs(submitted_jobs)
        
        # Final status
        if failed_jobs:
            logging.error(f"Completed with {len(failed_jobs)} failures")
            sys.exit(1)
        else:
            logging.info("HPC BIDS App Runner completed successfully")
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
