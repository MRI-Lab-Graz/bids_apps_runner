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
import re
import shlex
import subprocess
import argparse
from pathlib import Path
from typing import Dict, Optional


_SAFE_SUBJECT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SAFE_SHELL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_SLURM_DIRECTIVE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")
_SAFE_SLURM_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9._%/@:+,=~-]+$")
_SAFE_OPTION_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")


def _shell_quote(value: object) -> str:
    return shlex.quote(str(value))


def _validate_subject(subject: str) -> str:
    normalized = subject.replace("sub-", "", 1) if subject.startswith("sub-") else subject
    if not _SAFE_SUBJECT_PATTERN.fullmatch(normalized):
        raise ValueError(f"Invalid subject identifier: {subject}")
    return normalized


def _validate_shell_name(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_SHELL_NAME_PATTERN.fullmatch(normalized):
        raise ValueError(f"Invalid {label}: {value}")
    return normalized


def _validate_sbatch_directive_name(value: object) -> str:
    normalized = str(value or "").strip()
    if not _SAFE_SLURM_DIRECTIVE_PATTERN.fullmatch(normalized):
        raise ValueError(f"Invalid SBATCH directive: {value}")
    return normalized


def _validate_sbatch_value(value: object, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or not _SAFE_SLURM_VALUE_PATTERN.fullmatch(normalized):
        raise ValueError(f"Invalid {label}: {value}")
    return normalized


def setup_logging(log_level="INFO"):
    """Setup logging configuration."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
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
        self.subject = _validate_subject(subject)
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
        job_name = _validate_sbatch_value(
            f"{self.hpc.get('job_name', 'bids_app')}_{self.subject}", "job_name"
        )
        partition = _validate_sbatch_value(
            self.hpc.get("partition", "standard"), "partition"
        )
        time_limit = _validate_sbatch_value(self.hpc.get("time", "24:00:00"), "time")
        mem = _validate_sbatch_value(self.hpc.get("mem", "32G"), "mem")
        cpus = int(self.hpc.get("cpus", 8))
        output_log = _validate_sbatch_value(
            self.hpc.get("output_log", "slurm-%j.out"), "output_log"
        )
        error_log = _validate_sbatch_value(
            self.hpc.get("error_log", "slurm-%j.err"), "error_log"
        )

        header = f"""#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --partition={partition}
#SBATCH --time={time_limit}
#SBATCH --mem={mem}
#SBATCH --cpus-per-task={cpus}
#SBATCH --output={output_log}
#SBATCH --error={error_log}
"""

        # Add additional SLURM directives if provided
        for key, value in self.hpc.items():
            if key.startswith("sbatch_"):
                directive = _validate_sbatch_directive_name(
                    key.replace("sbatch_", "").replace("_", "-")
                )
                directive_value = _validate_sbatch_value(value, directive)
                header += f"#SBATCH --{directive}={directive_value}\n"

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
echo "Subject: sub-{subject}"
echo "Start time: $(date)"
echo ""
""".format(subject=self.subject)

        # Load modules
        modules = self.hpc.get("modules", [])
        if modules:
            setup += "# Load modules\n"
            for module in modules:
                setup += f"module load {_shell_quote(module)}\n"
            setup += "\n"

        # Set environment variables
        env_vars = self.hpc.get("environment", {})
        if env_vars:
            setup += "# Set environment variables\n"
            for key, value in env_vars.items():
                key = str(key or "").strip()
                if not _SAFE_ENV_KEY_PATTERN.fullmatch(key):
                    raise ValueError(f"Invalid environment variable name: {key}")
                setup += f"export {key}={_shell_quote(value)}\n"
            setup += "\n"

        # Setup work directory and lock file
        work_dir = _shell_quote(self.common.get("work_dir", "/tmp/bids_work"))
        setup += f"""
# Setup directories
export WORK_DIR={work_dir}
export DS_DIR="${{WORK_DIR}}/ds"
export DS_LOCKFILE="${{WORK_DIR}}/datalad.lock"
export TMPDIR="${{WORK_DIR}}/tmp/$SLURM_JOB_ID"

export PRISM_JOB_BRANCH="job-${{{self.job_id_prefix}}}"

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
        if clone_method not in {"clone", "install"}:
            raise ValueError(f"Unsupported DataLad clone method: {clone_method}")
        quoted_input_repo = _shell_quote(input_repo)
        quoted_display_repo = _shell_quote(f"Cloning dataset from {input_repo}...")

        section = """
# Clone DataLad Dataset
echo "=========================================="
echo "DataLad Clone"
echo "=========================================="
if [ -d "$DS_DIR" ]; then
    echo "Dataset already cloned at $DS_DIR"
else
    printf '%s\n' {quoted_display_repo}
    flock --verbose "$DS_LOCKFILE" datalad {clone_method} {quoted_input_repo} "$DS_DIR"
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
        clone_method=clone_method,
        quoted_input_repo=quoted_input_repo,
        quoted_display_repo=quoted_display_repo,
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
        section = """
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
                quoted_repo = _shell_quote(repo)
                quoted_repo_display = _shell_quote(str(repo))
                section += f"""
if [ -d {quoted_repo} ]; then
    printf '%s\n' {_shell_quote(f'Creating branch job in {repo}...')}
    git -C {quoted_repo} checkout -b "$PRISM_JOB_BRANCH" 2>/dev/null || \\
    git -C {quoted_repo} checkout "$PRISM_JOB_BRANCH"
else
    printf '%s\n' {_shell_quote(f'WARNING: Output directory {repo} not found')}
fi
"""

        section += '\necho ""\n'
        return section

    def _container_run(self) -> str:
        """Generate datalad containers-run section."""
        self.container.get("image")
        container_name = _validate_shell_name(
            self.container.get("name", "bids_app"), "container name"
        )
        outputs = self.container.get("outputs", [])
        inputs = self.container.get("inputs", [])

        # Get container arguments
        container_args = self.container.get("bids_args", {})
        bids_folder = container_args.get("bids_folder", "sourcedata")
        output_folder = container_args.get("output_folder", ".")
        analysis_level = container_args.get("analysis_level", "participant")

        # Build container command
        container_cmd = " ".join(
            [
                _shell_quote(bids_folder),
                _shell_quote(output_folder),
                _shell_quote(analysis_level),
            ]
        )

        # Add optional arguments
        optional_args = ""
        for key, value in container_args.items():
            if key not in ["bids_folder", "output_folder", "analysis_level"]:
                key = str(key or "").strip()
                if not _SAFE_OPTION_KEY_PATTERN.fullmatch(key):
                    raise ValueError(f"Invalid container option: {key}")
                if isinstance(value, bool):
                    if value:
                        optional_args += f" \\\n    --{key}"
                else:
                    optional_args += f" \\\n+    --{key} {_shell_quote(value)}"

        # Add participant label
        optional_args += f" \\\n    --participant-label {self.subject}"

        # Add working directory
        optional_args += " \\\n    -w .git/tmp/wdir"

        continuation = "\\"
        command_lines = [
            "datalad containers-run " + continuation,
            f"   -m {_shell_quote(f'{container_name} sub-{self.subject}')} " + continuation,
            "   --explicit " + continuation,
        ]
        command_lines.extend(
            [f"   -o {_shell_quote(output)} " + continuation for output in outputs]
        )
        command_lines.extend(
            [f"   -i {_shell_quote(input_path)} " + continuation for input_path in inputs]
        )
        command_lines.append(f"   -n code/pipelines/{container_name} " + continuation)
        command_lines.append(f"   {container_cmd}{optional_args}")
        run_command = "\n".join(command_lines)

        section = f"""
# Run Container via DataLad
echo "=========================================="
echo "Container Execution"
echo "=========================================="
printf '%s\n' {_shell_quote(f'Running {container_name} for subject sub-{self.subject}...')}

{run_command}

if [ $? -eq 0 ]; then
    echo "Container execution completed successfully"
else
    echo "ERROR: Container execution failed"
    exit 1
fi
echo ""
"""

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
                quoted_repo = _shell_quote(repo)
                section += f"""
printf '%s\n' {_shell_quote(f'Pushing results from {repo}...')}
flock --verbose "${{DS_LOCKFILE}}" datalad push -d {quoted_repo} --to origin
if [ $? -eq 0 ]; then
    printf '%s\n' {_shell_quote(f'Successfully pushed {repo}')}
else
    printf '%s\n' {_shell_quote(f'WARNING: Failed to push {repo}')}
fi
"""

        section += '\necho ""\n'
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


def generate_script(
    config_path: str, subject: str, output_path: Optional[str] = None
) -> str:
    """Generate a SLURM script for a subject.

    Args:
        config_path: Path to JSON config file
        subject: Subject ID to process
        output_path: Optional path to save script (if None, just returns string)

    Returns:
        Generated script content
    """
    try:
        with open(config_path, "r") as f:
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
            with open(output_path, "w") as f:
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
        "-c", "--config", required=True, help="Path to JSON config file"
    )

    parser.add_argument("-s", "--subject", required=True, help="Subject ID to process")

    parser.add_argument("-o", "--output", help="Path to save generated script")

    parser.add_argument(
        "--submit", action="store_true", help="Submit the job to SLURM after generation"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without actually doing it",
    )

    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level",
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
