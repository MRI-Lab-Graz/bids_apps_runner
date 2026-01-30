#!/usr/bin/env python3
"""
HPC DataLad Runner - Generate and manage SLURM jobs with DataLad workflow

This script generates SLURM job scripts that follow the DataLad pattern:
- Clone DataLad dataset with lock file
- Get required data on-demand
- Create job-specific git branches
- Run container via datalad containers-run
- Push results back to origin

Author: BIDS Apps Runner Team
Version: 2.0.0
"""

import os
import sys
import json
import logging
import subprocess
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


def setup_logging(log_level="INFO"):
    """Setup logging configuration."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )


def validate_datalad_config(config: Dict) -> bool:
    """Validate DataLad configuration."""
    required = ["input_repo", "output_repos"]
    missing = [k for k in required if k not in config]
    
    if missing:
        logging.error(f"Missing required DataLad config: {', '.join(missing)}")
        return False
    
    return True


def validate_hpc_config(config: Dict) -> bool:
    """Validate HPC configuration."""
    required = ["partition", "time", "mem", "cpus", "job_name"]
    missing = [k for k in required if k not in config]
    
    if missing:
        logging.warning(f"Missing HPC config, using defaults: {', '.join(missing)}")
    
    return True


def validate_container_config(config: Dict) -> bool:
    """Validate container configuration."""
    required = ["image", "outputs"]
    missing = [k for k in required if k not in config]
    
    if missing:
        logging.error(f"Missing required container config: {', '.join(missing)}")
        return False
    
    return True


class DataLadHPCScriptGenerator:
    """Generate SLURM scripts with DataLad workflow."""
    
    def __init__(self, config: Dict, subject: str, job_id: str = "SLURM_JOB_ID"):
        """Initialize script generator.
        
        Args:
            config: Full configuration dictionary
            subject: Subject ID to process
            job_id: SLURM job ID variable (default uses $SLURM_JOB_ID)
        """
        self.config = config
        self.subject = subject.replace("sub-", "") if subject.startswith("sub-") else subject
        self.job_id_var = f"${job_id}"
        self.job_id_prefix = job_id.lstrip("$")
        
        self.common = config.get("common", {})
        self.datalad = config.get("datalad", {})
        self.hpc = config.get("hpc", {})
        self.container = config.get("container", {})
    
    def generate_script(self) -> str:
        """Generate the full SLURM job script."""
        script_parts = [
            self._header(),
            self._setup(),
            self._datalad_clone(),
            self._datalad_get_structure(),
            self._git_setup(),
            self._container_run(),
            self._push_results(),
            self._cleanup(),
            self._footer(),
        ]
        
        return "\n".join(script_parts)
    
    def _header(self) -> str:
        """Generate SLURM header."""
        job_name = f"{self.hpc.get('job_name', 'bids_app')}_{self.subject}"
        
        header = f"""#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --partition={self.hpc.get('partition', 'standard')}
#SBATCH --time={self.hpc.get('time', '24:00:00')}
#SBATCH --mem={self.hpc.get('mem', '32G')}
#SBATCH --cpus-per-task={self.hpc.get('cpus', 8)}
#SBATCH --output={self.hpc.get('output_log', 'slurm-%j.out')}
#SBATCH --error={self.hpc.get('error_log', 'slurm-%j.err')}
"""
        
        # Add additional SLURM directives if provided
        for key, value in self.hpc.items():
            if key.startswith('sbatch_'):
                directive = key.replace('sbatch_', '').replace('_', '-')
                header += f"#SBATCH --{directive}={value}\n"
        
        return header
    
    def _setup(self) -> str:
        """Generate environment setup section."""
        setup = """
# Environment Setup
set -e
set -u
trap 'echo "ERROR: Command failed at line $LINENO"' ERR

echo "=========================================="
echo "SLURM Job Information"
echo "=========================================="
echo "Job ID: $SLURM_JOB_ID"
echo "Node: $SLURM_JOB_NODELIST"
echo "Partition: $SLURM_JOB_PARTITION"
echo "Subject: {subject}
echo "Start time: $(date)"
echo ""
""".format(subject=self.subject)
        
        # Load modules
        modules = self.hpc.get("modules", [])
        if modules:
            setup += "# Load modules\n"
            for module in modules:
                setup += f"module load {module}\n"
            setup += "\n"
        
        # Set environment variables
        env_vars = self.hpc.get("environment", {})
        if env_vars:
            setup += "# Set environment variables\n"
            for key, value in env_vars.items():
                setup += f"export {key}={value}\n"
            setup += "\n"
        
        # Setup work directory and lock file
        work_dir = self.common.get("work_dir", "/tmp/bids_work")
        setup += f"""
# Setup directories
export WORK_DIR={work_dir}
export DS_DIR="${{WORK_DIR}}/ds"
export DS_LOCKFILE="${{WORK_DIR}}/datalad.lock"
export TMPDIR="${{WORK_DIR}}/tmp/$SLURM_JOB_ID"

mkdir -p "${{TMPDIR}}"
mkdir -p "$(dirname "${{DS_LOCKFILE}}")"

echo "Work directory: ${{WORK_DIR}}"
echo "DataLad lock file: ${{DS_LOCKFILE}}"
echo ""
"""
        return setup
    
    def _datalad_clone(self) -> str:
        """Generate DataLad clone section."""
        input_repo = self.datalad.get("input_repo")
        clone_method = self.datalad.get("clone_method", "clone")
        
        section = """
# Clone DataLad Dataset
echo "=========================================="
echo "DataLad Clone"
echo "=========================================="
if [ -d "$DS_DIR" ]; then
    echo "Dataset already cloned at $DS_DIR"
else
    echo "Cloning dataset from {input_repo}..."
    flock --verbose "$DS_LOCKFILE" datalad {clone_method} {input_repo} "$DS_DIR"
    if [ $? -eq 0 ]; then
        echo "Successfully cloned dataset"
    else
        echo "ERROR: Failed to clone dataset"
        exit 1
    fi
fi
cd "$DS_DIR"
echo ""
""".format(
            input_repo=input_repo,
            clone_method=clone_method
        )
        
        return section
    
    def _datalad_get_structure(self) -> str:
        """Generate DataLad get section for directory structure."""
        section = """
# Get Directory Structure (no data yet)
echo "=========================================="
echo "DataLad Get Structure"
echo "=========================================="
echo "Getting directory structure without actual data..."
datalad get -n -r -R1 .
echo "Directory structure retrieved"
echo ""
"""
        return section
    
    def _git_setup(self) -> str:
        """Generate git branch setup section."""
        section = f"""
# Setup Git Branches per Output Repository
echo "=========================================="
echo "Git Branch Setup"
echo "=========================================="

# Mark git annex as dead in this working copy
echo "Marking git-annex as dead for local branch..."
git submodule foreach --recursive git annex dead here 2>/dev/null || true

# Create job-specific branches for each output repository
"""
        
        output_repos = self.container.get("outputs", [])
        if isinstance(output_repos, list):
            for repo in output_repos:
                section += f"""
if [ -d "{repo}" ]; then
    echo "Creating branch job-{self.job_id_var} in {repo}..."
    git -C {repo} checkout -b "job-{self.job_id_var}" 2>/dev/null || \\
    git -C {repo} checkout "job-{self.job_id_var}"
else
    echo "WARNING: Output directory {repo} not found"
fi
"""
        
        section += "\necho \"\"\n"
        return section
    
    def _container_run(self) -> str:
        """Generate datalad containers-run section."""
        container_image = self.container.get("image")
        container_name = self.container.get("name", "bids_app")
        outputs = self.container.get("outputs", [])
        inputs = self.container.get("inputs", [])
        
        # Build outputs argument
        outputs_arg = " ".join([f"-o {o}" for o in outputs]) if outputs else ""
        
        # Build inputs argument
        inputs_arg = " ".join([f"-i {i}" for i in inputs]) if inputs else ""
        
        # Get container arguments
        container_args = self.container.get("bids_args", {})
        bids_folder = container_args.get("bids_folder", "sourcedata")
        output_folder = container_args.get("output_folder", ".")
        analysis_level = container_args.get("analysis_level", "participant")
        
        # Build container command
        container_cmd = f"{bids_folder} {output_folder} {analysis_level}"
        
        # Add optional arguments
        optional_args = ""
        for key, value in container_args.items():
            if key not in ["bids_folder", "output_folder", "analysis_level"]:
                if isinstance(value, bool):
                    if value:
                        optional_args += f" \\\n    --{key}"
                else:
                    optional_args += f" \\\n    --{key} {value}"
        
        # Add participant label
        optional_args += f" \\\n    --participant-label {self.subject}"
        
        # Add working directory
        optional_args += " \\\n    -w .git/tmp/wdir"
        
        section = f"""
# Run Container via DataLad
echo "=========================================="
echo "Container Execution"
echo "=========================================="
echo "Running {container_name} for subject sub-{self.subject}..."

datalad containers-run \\
   -m "{{container_name}} sub-{self.subject} (job {self.job_id_var})" \\
   --explicit \\
   {outputs_arg} \\
   {inputs_arg} \\
   -n code/pipelines/{container_name} \\
   {container_cmd}{optional_args}

if [ $? -eq 0 ]; then
    echo "Container execution completed successfully"
else
    echo "ERROR: Container execution failed"
    exit 1
fi
echo ""
""".format(container_name=container_name)
        
        return section
    
    def _push_results(self) -> str:
        """Generate push results section."""
        output_repos = self.container.get("outputs", [])
        
        section = """
# Push Results to Origin
echo "=========================================="
echo "Push Results"
echo "=========================================="
"""
        
        if isinstance(output_repos, list):
            for repo in output_repos:
                section += f"""
echo "Pushing results from {repo}..."
flock --verbose "${{DS_LOCKFILE}}" datalad push -d {repo} --to origin
if [ $? -eq 0 ]; then
    echo "Successfully pushed {repo}"
else
    echo "WARNING: Failed to push {repo}"
fi
"""
        
        section += "\necho \"\"\n"
        return section
    
    def _cleanup(self) -> str:
        """Generate cleanup section."""
        section = """
# Cleanup
echo "=========================================="
echo "Cleanup"
echo "=========================================="
echo "Removing temporary directory..."
rm -rf "$TMPDIR"
echo "Cleanup completed"
echo ""
"""
        return section
    
    def _footer(self) -> str:
        """Generate footer with completion info."""
        section = """
# Job Completion
echo "=========================================="
echo "Job Completion"
echo "=========================================="
echo "End time: $(date)"
echo "Total duration: $SECONDS seconds"
echo "=========================================="
"""
        return section


def generate_script(config_path: str, subject: str, output_path: Optional[str] = None) -> str:
    """Generate a SLURM script for a subject.
    
    Args:
        config_path: Path to JSON config file
        subject: Subject ID to process
        output_path: Optional path to save script (if None, just returns string)
    
    Returns:
        Generated script content
    """
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
    except Exception as e:
        logging.error(f"Failed to load config: {e}")
        sys.exit(1)
    
    # Validate configurations
    if not validate_datalad_config(config.get("datalad", {})):
        sys.exit(1)
    
    validate_hpc_config(config.get("hpc", {}))
    
    if not validate_container_config(config.get("container", {})):
        sys.exit(1)
    
    # Generate script
    generator = DataLadHPCScriptGenerator(config, subject)
    script = generator.generate_script()
    
    # Save if output path provided
    if output_path:
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w') as f:
                f.write(script)
            os.chmod(output_path, 0o755)
            logging.info(f"Script saved to: {output_path}")
        except Exception as e:
            logging.error(f"Failed to save script: {e}")
            sys.exit(1)
    
    return script


def submit_job(script_path: str, dry_run: bool = False) -> Optional[str]:
    """Submit a SLURM job script.
    
    Args:
        script_path: Path to the job script
        dry_run: If True, print command but don't execute
    
    Returns:
        Job ID if successful, None otherwise
    """
    cmd = ["sbatch", script_path]
    
    if dry_run:
        logging.info(f"DRY RUN - Would execute: {' '.join(cmd)}")
        return "DRY_RUN_JOB_ID"
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # Extract job ID from sbatch output
        output = result.stdout.strip()
        if "Submitted batch job" in output:
            job_id = output.split()[-1]
            logging.info(f"Submitted job {job_id}: {script_path}")
            return job_id
        else:
            logging.error(f"Failed to parse job ID: {output}")
            return None
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to submit job: {e.stderr}")
        return None
    except FileNotFoundError:
        logging.error("sbatch not found - are you on an HPC system with SLURM?")
        return None


def main():
    """CLI interface for the HPC DataLad runner."""
    parser = argparse.ArgumentParser(
        description="Generate SLURM scripts with DataLad workflow"
    )
    
    parser.add_argument(
        "-c", "--config",
        required=True,
        help="Path to JSON config file"
    )
    
    parser.add_argument(
        "-s", "--subject",
        required=True,
        help="Subject ID to process"
    )
    
    parser.add_argument(
        "-o", "--output",
        help="Path to save generated script"
    )
    
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Submit the job to SLURM after generation"
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without actually doing it"
    )
    
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level"
    )
    
    args = parser.parse_args()
    
    setup_logging(args.log_level)
    
    # Generate script
    script = generate_script(args.config, args.subject, args.output)
    
    if not args.output:
        print(script)
    
    # Submit if requested
    if args.submit and args.output:
        submit_job(args.output, args.dry_run)


if __name__ == "__main__":
    main()
