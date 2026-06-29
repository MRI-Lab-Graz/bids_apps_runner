#!/usr/bin/env python3
"""
HPC DataLad Runner - Generate SLURM compute scripts for the datalad-slurm workflow

Generates SLURM job scripts that contain *no* datalad/git calls at all. Git
provenance (input retrieval, output recording, push) is handled outside the
job by the `datalad-slurm` extension (`datalad slurm-schedule` /
`datalad slurm-finish`, see submit_bids_cohort.sh): SLURM jobs only do
compute, reading from an already-cloned, already-fetched input dataset and
writing into an already-cloned output dataset. This avoids per-job git
operations entirely instead of serializing them with flock.

A single subject is just a one-task array (`--array=0-0`); array mode and
single-subject mode share the same generator.

Author: BIDS Apps Runner Team
Version: 3.0.0
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


def _shell_quote(value: object) -> str:
    return shlex.quote(str(value))


def _validate_subject(subject: str) -> str:
    normalized = (
        subject.replace("sub-", "", 1) if subject.startswith("sub-") else subject
    )
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


def validate_compute_config(config: Dict) -> bool:
    """Validate the config sections needed to generate a compute script."""
    paths = config.get("paths", {})
    if not paths.get("container"):
        logging.error("Missing required config: paths.container")
        return False
    return True


class BidsAppComputeScriptGenerator:
    """Generate a plain (no datalad/git calls) SLURM array job script.

    Works with any BIDS app (fMRIPrep, QSIPrep, MRIQC, ...). Covers both a
    full cohort (one array task per subject) and a single ad-hoc subject
    (an array of size 1). Reads input from an already-cloned, already-fetched
    dataset and writes into an already-cloned output dataset; both clones are
    expected to already exist (see submit_bids_cohort.sh `setup`). Git
    provenance (retrieval, commit, push) happens outside the job via
    `datalad slurm-schedule` / `datalad slurm-finish`.

    Workflow per array task:
      1. Resolve subject from list file via $SLURM_ARRAY_TASK_ID
      2. Set up per-task scratch directory
      3. Run BIDS app via apptainer exec, reading from the shared input
         clone and writing into the shared output clone
      4. Clean up scratch

    Required config keys (top-level JSON sections):
      paths.shared_input_base   – local HPC input dataset clone root
      paths.shared_output_base  – local HPC output dataset clone root
      paths.scratch_dir         – per-task compute scratch base
      paths.container           – apptainer .sif path
      paths.templateflow_dir    – (optional) host TemplateFlow directory
      paths.fs_license          – (optional) host FreeSurfer license.txt
      paths.log_dir             – base log directory
      hpc.partition / time / mem / cpus / max_concurrent / modules / environment
      bids_app.app_name         – used in job name and commit messages
      bids_app.analysis_level   – passed as BIDS positional arg (default: participant)
      bids_app.output_dir_name  – output subdirectory name (default: app_name)
      bids_app.options          – flat list of extra CLI flags for the container
    """

    def __init__(
        self,
        config: Dict,
        dataset_id: str,
        subject_list_path: str,
        n_subjects: int,
    ) -> None:
        self.config = config
        self.dataset_id = _validate_shell_name(dataset_id, "dataset_id")
        self.subject_list_path = subject_list_path
        self.n_subjects = n_subjects

        self.hpc = config.get("hpc", {})
        self.paths = config.get("paths", {})
        self.bids_app = config.get("bids_app", {})
        self._app_name = self.bids_app.get("app_name") or "bids_app"
        self._out_dir_name = self.bids_app.get("output_dir_name") or self._app_name

    # ── public ────────────────────────────────────────────────────────────────

    def generate_script(self) -> str:
        parts = [
            self._header(),
            self._resolve_subject(),
            self._info_block(),
            self._module_and_env(),
            self._workdirs(),
            self._run_bids_app(),
            self._cleanup(),
            self._footer(),
        ]
        return "\n".join(parts)

    # ── private sections ──────────────────────────────────────────────────────

    def _header(self) -> str:
        partition = _validate_sbatch_value(
            self.hpc.get("partition", "compute"), "partition"
        )
        time_limit = _validate_sbatch_value(self.hpc.get("time", "24:00:00"), "time")
        mem = _validate_sbatch_value(self.hpc.get("mem", "32G"), "mem")
        cpus = int(self.hpc.get("cpus", 8))
        max_concurrent = int(self.hpc.get("max_concurrent", 50))
        array_spec = f"0-{self.n_subjects - 1}%{max_concurrent}"

        log_dir = self.paths.get("log_dir", "$HOME/logs/bids_app")
        out_log = f"{log_dir}/{self.dataset_id}/slurm-%A_%a.out"
        err_log = f"{log_dir}/{self.dataset_id}/slurm-%A_%a.err"

        header = f"""#!/bin/bash
#SBATCH --job-name={self._app_name}_{self.dataset_id}
#SBATCH --array={array_spec}
#SBATCH --partition={partition}
#SBATCH --time={time_limit}
#SBATCH --mem={mem}
#SBATCH --cpus-per-task={cpus}
#SBATCH --output={out_log}
#SBATCH --error={err_log}
"""
        for key, value in self.hpc.items():
            if key.startswith("sbatch_"):
                directive = _validate_sbatch_directive_name(
                    key.replace("sbatch_", "").replace("_", "-")
                )
                val = _validate_sbatch_value(value, directive)
                header += f"#SBATCH --{directive}={val}\n"
        return header

    def _resolve_subject(self) -> str:
        quoted_list = _shell_quote(self.subject_list_path)
        return f"""
# Resolve subject from list file
SUBJECT_LIST={quoted_list}
SUBJECT=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "$SUBJECT_LIST" | tr -d '[:space:]')
if [[ -z "$SUBJECT" ]]; then
    echo "ERROR: no subject for array task $SLURM_ARRAY_TASK_ID in $SUBJECT_LIST" >&2
    exit 1
fi
SUBJECT_LABEL="${{SUBJECT#sub-}}"
"""

    def _info_block(self) -> str:
        return """
echo "========================================"
echo "Array job:  ${SLURM_ARRAY_JOB_ID}[${SLURM_ARRAY_TASK_ID}]"
echo "Subject:    sub-${SUBJECT_LABEL}"
echo "Node:       ${SLURM_JOB_NODELIST}"
echo "Started:    $(date)"
echo "========================================"

set -e
set -u
trap 'echo "FAILED at line $LINENO (task $SLURM_ARRAY_TASK_ID, sub-${SUBJECT_LABEL})" >&2' ERR

if command -v apptainer &> /dev/null; then
    APPTAINER_BIN=apptainer
elif command -v singularity &> /dev/null; then
    APPTAINER_BIN=singularity
else
    echo "ERROR: neither apptainer nor singularity found on this node" >&2
    exit 1
fi
"""

    def _module_and_env(self) -> str:
        lines = []
        modules = self.hpc.get("modules", [])
        if modules:
            lines.append("# Load modules")
            lines.append("module load " + " ".join(_shell_quote(m) for m in modules))
            lines.append("")

        env_vars = self.hpc.get("environment", {})
        if env_vars:
            lines.append("# Environment variables")
            for key, value in env_vars.items():
                key = str(key).strip()
                if not _SAFE_ENV_KEY_PATTERN.fullmatch(key):
                    raise ValueError(f"Invalid env var name: {key}")
                lines.append(f"export {key}={_shell_quote(value)}")
            lines.append("")

        return "\n" + "\n".join(lines)

    def _workdirs(self) -> str:
        """Compute-only working directories.

        BIDS_DIR/OUT_DIR point directly at the persistent, already-cloned
        input/output dataset working copies (no per-task clone): multiple
        array tasks write to distinct sub-XXX/ subdirectories of the same
        output clone concurrently, which is safe because nothing here ever
        touches git -- that happens later, once, via `datalad slurm-finish`.
        Only the scratch/tmp directory is per-task, matching what SLURM
        already isolates per job.
        """
        scratch = _shell_quote(self.paths.get("scratch_dir", "/scratch/$USER/bids_app"))
        ds_id = _shell_quote(self.dataset_id)
        log_dir = _shell_quote(
            f"{self.paths.get('log_dir', '$HOME/logs/bids_app')}/{self.dataset_id}"
        )
        # input_dir/output_dir, when given, are used verbatim -- this lets a
        # caller point directly at an existing project's bids_folder/output_folder
        # (e.g. the GUI's project-derived config) without it having to fit the
        # shared_input_base/shared_output_base + dataset_id composition below.
        input_dir = self.paths.get("input_dir")
        bids_dir = _shell_quote(
            input_dir
            if input_dir
            else f"{self.paths.get('shared_input_base', '/shared/input')}/{self.dataset_id}"
        )
        output_dir = self.paths.get("output_dir")
        out_dir = _shell_quote(
            output_dir
            if output_dir
            else f"{self.paths.get('shared_output_base', '/shared/derivatives')}"
            f"/{self.dataset_id}/{self._out_dir_name}"
        )

        return f"""
# Working directories
BIDS_DIR={bids_dir}
OUT_DIR={out_dir}
WORK_DIR={scratch}/{ds_id}/${{SLURM_ARRAY_JOB_ID}}_${{SLURM_ARRAY_TASK_ID}}
TMP_DIR="${{WORK_DIR}}/tmp"

mkdir -p "${{TMP_DIR}}" {log_dir}
echo "Input:   ${{BIDS_DIR}}"
echo "Output:  ${{OUT_DIR}}"
echo "Scratch: ${{WORK_DIR}}"
"""

    def _run_bids_app(self) -> str:
        app_name = self._app_name
        analysis_level = self.bids_app.get("analysis_level", "participant")
        options = self.bids_app.get("options", [])

        container = _shell_quote(self.paths.get("container", "TODO_CONTAINER_PATH"))

        extra_binds = ""
        tf = (self.paths.get("templateflow_dir") or "").strip()
        if tf:
            extra_binds += f"    -B {_shell_quote(tf)}:/templateflow:ro \\\n"
        fs = (self.paths.get("fs_license") or "").strip()
        if fs:
            extra_binds += f"    -B {_shell_quote(fs)}:/fs/license.txt:ro \\\n"

        extra_flags = ""
        for opt in options:
            extra_flags += f" \\\n    {_shell_quote(str(opt))}"

        return f"""
# Run BIDS app: {app_name}
echo "--- Running {app_name} for sub-${{SUBJECT_LABEL}} ---"
"$APPTAINER_BIN" exec \\
    --cleanenv \\
    -B "${{BIDS_DIR}}":/bids:ro \\
    -B "${{OUT_DIR}}":/output \\
{extra_binds}    -B "${{TMP_DIR}}":/tmp \\
    {container} \\
    /bids /output {_shell_quote(analysis_level)} \\
    --participant-label "${{SUBJECT_LABEL}}"{extra_flags} \\
    -w /tmp/wdir

echo "{app_name} finished for sub-${{SUBJECT_LABEL}}"
"""

    def _cleanup(self) -> str:
        return """
# Remove per-task scratch
echo "--- Cleanup ---"
rm -rf "${WORK_DIR}"
"""

    def _footer(self) -> str:
        return """
echo "========================================"
echo "Completed sub-${SUBJECT_LABEL}"
echo "End: $(date) | Duration: ${SECONDS}s"
echo "========================================"
"""


def generate_array_script(
    config_path: str,
    dataset_id: str,
    subject_list_path: str,
    output_path: Optional[str] = None,
) -> str:
    """Generate a SLURM array job script for all subjects in one dataset.

    Args:
        config_path: Path to JSON config (cohort_hpc_example.json format)
        dataset_id: Dataset identifier used as directory name and job label
        subject_list_path: Path to plain-text file, one subject per line
        output_path: Optional path to save the script

    Returns:
        Generated script content
    """
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except Exception as e:
        logging.error(f"Failed to load config: {e}")
        sys.exit(1)

    if not validate_compute_config(config):
        sys.exit(1)

    try:
        with open(subject_list_path, "r") as f:
            subjects = [line.strip() for line in f if line.strip()]
    except Exception as e:
        logging.error(f"Failed to read subject list: {e}")
        sys.exit(1)

    if not subjects:
        logging.error(f"Subject list is empty: {subject_list_path}")
        sys.exit(1)

    generator = BidsAppComputeScriptGenerator(
        config=config,
        dataset_id=dataset_id,
        subject_list_path=subject_list_path,
        n_subjects=len(subjects),
    )
    script = generator.generate_script()

    if output_path:
        try:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                f.write(script)
            os.chmod(output_path, 0o755)
            logging.info(f"Array script saved to: {output_path}")
        except Exception as e:
            logging.error(f"Failed to save script: {e}")
            sys.exit(1)

    return script


def generate_script(
    config_path: str, subject: str, output_path: Optional[str] = None
) -> str:
    """Generate a SLURM script for a single ad-hoc subject.

    A single subject is generated as a one-task array job, sharing the same
    `BidsAppComputeScriptGenerator` used for full cohorts. Since the script
    resolves its subject from a list file, a one-line subject list is
    written alongside the generated script.

    Args:
        config_path: Path to JSON config file (cohort_hpc_example.json format)
        subject: Subject ID to process
        output_path: Path to save the script (required: the subject list is
            written next to it and the script needs that fixed path)

    Returns:
        Generated script content
    """
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except Exception as e:
        logging.error(f"Failed to load config: {e}")
        sys.exit(1)

    if not validate_compute_config(config):
        sys.exit(1)

    try:
        normalized_subject = _validate_subject(subject)
    except ValueError as e:
        logging.error(str(e))
        sys.exit(1)

    if not output_path:
        logging.error("--output is required for single-subject mode")
        sys.exit(1)

    subject_list_path = f"{output_path}.subjects.txt"
    try:
        Path(subject_list_path).parent.mkdir(parents=True, exist_ok=True)
        with open(subject_list_path, "w") as f:
            f.write(f"sub-{normalized_subject}\n")
    except Exception as e:
        logging.error(f"Failed to write subject list: {e}")
        sys.exit(1)

    dataset_id = config.get("bids_app", {}).get("app_name") or "adhoc"
    generator = BidsAppComputeScriptGenerator(
        config=config,
        dataset_id=dataset_id,
        subject_list_path=subject_list_path,
        n_subjects=1,
    )
    script = generator.generate_script()

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
    """Submit a plain SLURM job script directly via sbatch.

    NOTE: this bypasses `datalad-slurm` -- it submits the compute script as
    a regular SLURM job with no provenance recording. Outputs written by the
    job will never be committed/pushed unless something else later runs
    `datalad slurm-finish` for it. Production cohort runs should go through
    `submit_bids_cohort.sh submit` (which wraps the script in
    `datalad slurm-schedule` and chains a `datalad slurm-finish` job).

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
        description="Generate plain SLURM compute scripts for the datalad-slurm workflow"
    )
    parser.add_argument(
        "-c", "--config", required=True, help="Path to JSON config file"
    )
    parser.add_argument("-o", "--output", help="Path to save generated script")
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Submit the script directly via sbatch after generation "
        "(bypasses datalad-slurm; prefer submit_bids_cohort.sh for real runs)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without executing",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-s", "--subject", help="Subject ID for a single ad-hoc script")
    mode.add_argument(
        "--array-mode",
        action="store_true",
        help="Generate a SLURM array job script (requires --dataset-id and --subject-list)",
    )

    parser.add_argument("--dataset-id", help="Dataset accession ID (array mode)")
    parser.add_argument(
        "--subject-list", help="Path to subject list file, one per line (array mode)"
    )

    args = parser.parse_args()
    setup_logging(args.log_level)

    if args.array_mode:
        if not args.dataset_id or not args.subject_list:
            parser.error("--array-mode requires --dataset-id and --subject-list")
        script = generate_array_script(
            args.config, args.dataset_id, args.subject_list, args.output
        )
    else:
        script = generate_script(args.config, args.subject, args.output)

    if not args.output:
        print(script)

    if args.submit and args.output:
        submit_job(args.output, args.dry_run)


if __name__ == "__main__":
    main()
