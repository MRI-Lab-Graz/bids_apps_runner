#!/usr/bin/env python3
"""
PRISM HPC - HPC/SLURM execution mode

Handles BIDS app execution on HPC systems with SLURM job scheduling.
Generates job scripts and submits them to the SLURM queue.

Extracted from: run_bids_apps_hpc.py
Author: BIDS Apps Runner Team (PRISM Edition)
Version: 3.0.0
"""

import os
import logging
import time
import random
from typing import Dict, Any
from argparse import Namespace

# Import from PRISM modules
from prism_core import get_subjects_from_bids, run_command
import prism_datalad


def _is_mriqc_container(container_ref):
    """Return True only for MRIQC container references."""
    ref = str(container_ref or "").strip().lower()
    if not ref:
        return False

    if "/mriqc:" in ref:
        return True
    if ref.startswith("mriqc:"):
        return True

    base = os.path.basename(ref)
    return base.startswith("mriqc")


def _ensure_mriqc_no_sub_option(container_ref, options):
    """Ensure MRIQC does not fail on network upload timeout by default."""
    opts = [str(x) for x in (options or [])]
    if not _is_mriqc_container(container_ref):
        return opts

    if "--no-sub" in opts:
        return opts

    opts.append("--no-sub")
    logging.info("MRIQC detected: auto-appending --no-sub to disable metrics upload")
    return opts


def _infer_execution_adapter(common, app):
    """Resolve app execution adapter from explicit config or pipeline metadata."""
    app_cfg = app if isinstance(app, dict) else {}
    common_cfg = common if isinstance(common, dict) else {}

    explicit = str(app_cfg.get("execution_adapter", "")).strip().lower()
    if explicit in {"fastsurfer", "fastsurfer-cross", "bids-fastsurfer"}:
        return "fastsurfer-cross"

    pipeline_app = str(common_cfg.get("pipeline_app_name", "")).strip().lower()
    if pipeline_app == "fastsurfer":
        return "fastsurfer-cross"

    container_ref = str(common_cfg.get("container", "")).strip().lower()
    if "fastsurfer" in container_ref:
        return "fastsurfer-cross"

    return ""


def _drop_runtime_flags(options, flag_names):
    """Drop flags (and optional values) from tokenized CLI options."""
    cleaned = []
    i = 0
    flags = set(flag_names)
    while i < len(options):
        token = str(options[i])
        if token in flags:
            if i + 1 < len(options) and not str(options[i + 1]).startswith("-"):
                i += 2
            else:
                i += 1
            continue

        if token.startswith("--") and "=" in token:
            left = token.split("=", 1)[0]
            if left in flags:
                i += 1
                continue

        cleaned.append(token)
        i += 1

    return cleaned


def _prepare_fastsurfer_options(options):
    """Normalize options for /fastsurfer/run_fastsurfer.sh."""
    opts = [str(x) for x in (options or [])]
    normalized = []
    for token in opts:
        if token == "--qc":
            normalized.append("--qc_snap")
        else:
            normalized.append(token)
    forbidden = {
        "--t1",
        "--sid",
        "--sd",
        "--fs_license",
        "--fs",
        "--participant-label",
        "-w",
    }
    return _drop_runtime_flags(normalized, forbidden)


# ============================================================================
# SLURM Job Management Functions
# ============================================================================


def create_slurm_job(subject, config, work_dir, dry_run=False, debug=False):
    """Create SLURM job script for a single subject."""
    logging.info(f"Creating SLURM job script for subject: {subject}")

    try:
        common = config["common"]
        app = config["app"]
        hpc = config["hpc"]
        datalad = config.get("datalad", {})
        analysis_level = (
            str(app.get("analysis_level", "participant")).strip() or "participant"
        )
        execution_adapter = _infer_execution_adapter(common, app)
        fastsurfer_mode = execution_adapter == "fastsurfer-cross"
        if fastsurfer_mode and analysis_level != "participant":
            raise ValueError(
                "FastSurfer adapter supports participant-level runs only in HPC mode."
            )

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
        output_file = os.path.join(
            logs_dir, hpc["output_pattern"].replace("%j", "$SLURM_JOB_ID")
        )
        error_file = os.path.join(
            logs_dir, hpc["error_pattern"].replace("%j", "$SLURM_JOB_ID")
        )

        # Build SLURM script header
        script_content = f"""#!/bin/bash
#SBATCH --job-name={job_name}
#SBATCH --partition={hpc["partition"]}
#SBATCH --time={hpc["time"]}
#SBATCH --mem={hpc["mem"]}
#SBATCH --cpus-per-task={hpc["cpus"]}
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
{f'echo "Debug mode: Container logs will be saved to {container_logs_dir}"' if debug else ""}
echo ""

# Load modules
"""

        # Add additional SBATCH directives from hpc config.
        # Example: "sbatch_gres": "gpu:1" -> #SBATCH --gres=gpu:1
        for key, value in hpc.items():
            if not key.startswith("sbatch_"):
                continue
            if value in (None, "", False):
                continue

            directive = key.replace("sbatch_", "").replace("_", "-")
            if value is True:
                script_content += f"#SBATCH --{directive}\n"
            else:
                script_content += f"#SBATCH --{directive}={value}\n"

        script_content += "\n"

        # Add module loads
        for module in hpc.get("modules", []):
            script_content += f"module load {module}\n"

        # Add environment variables
        for key, value in hpc.get("environment", {}).items():
            script_content += f"export {key}={value}\n"

        # Add temporary directory creation
        script_content += f"""
# Create temporary directory
mkdir -p {tmp_dir}
echo "Created temporary directory: {tmp_dir}"

# Setup DataLad environment for this subject
cd {bids_dir}
echo "Changed to BIDS directory: {bids_dir}"
"""

        # Add DataLad branch management if configured
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

        # Add data retrieval
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
"""

        # Add debug logging setup if debug mode is enabled
        container_log_redirection = ""
        if debug:
            timestamp = "$(date +%Y%m%d_%H%M%S)"
            container_stdout_log = (
                f"{container_logs_dir}/container_{subject}_{timestamp}.log"
            )
            container_stderr_log = (
                f"{container_logs_dir}/container_{subject}_{timestamp}.err"
            )
            container_log_redirection = (
                f" > >(tee {container_stdout_log}) 2> >(tee {container_stderr_log} >&2)"
            )

        script_content += f"""
# Run the BIDS app
echo "Running BIDS app for {subject}"
echo "Container: {common["container"]}"
"""

        if fastsurfer_mode:
            script_content += f"""
FASTSURFER_SUBJECT="{subject}"
mapfile -t FASTSURFER_T1_LIST < <(find "{bids_dir}/$FASTSURFER_SUBJECT" -type f \\( -name "*_desc-preproc_T1w.nii.gz" -o -name "*_T1w.nii.gz" -o -name "*_T1w.nii" \\) | sort)
if [ ${{#FASTSURFER_T1_LIST[@]}} -eq 0 ]; then
    echo "Error: No T1w image found for $FASTSURFER_SUBJECT"
    exit 1
fi
echo "FastSurfer mode: found ${{#FASTSURFER_T1_LIST[@]}} T1w image(s) for $FASTSURFER_SUBJECT"

FASTSURFER_EXIT=0
for FASTSURFER_T1_HOST in "${{FASTSURFER_T1_LIST[@]}}"; do
    FASTSURFER_T1_REL=$(echo "$FASTSURFER_T1_HOST" | sed 's#^{bids_dir}/##')
    FASTSURFER_T1_CONTAINER="/bids/$FASTSURFER_T1_REL"
    FASTSURFER_SID="$FASTSURFER_SUBJECT"
    FASTSURFER_SESSION=$(echo "$FASTSURFER_T1_REL" | grep -o 'ses-[^/]*' | head -n 1 || true)
    if [ -n "$FASTSURFER_SESSION" ]; then
        FASTSURFER_SID="$FASTSURFER_SUBJECT"_"$FASTSURFER_SESSION"
    fi

    echo "FastSurfer mode: using $FASTSURFER_T1_CONTAINER with SID $FASTSURFER_SID"

    apptainer exec \\
"""

            apptainer_args = [str(arg) for arg in app.get("apptainer_args", [])]
            if not apptainer_args:
                apptainer_args = ["--containall"]
            if "--nv" not in apptainer_args:
                apptainer_args.append("--nv")

            for arg in apptainer_args:
                script_content += f"        {arg} \\\n"

            script_content += f"""        -B {tmp_dir}:/tmp \\
        -B {output_dir}:/output \\
        -B {bids_dir}:/bids \\
"""

            if common.get("templateflow_dir"):
                script_content += (
                    f"\n        -B {common['templateflow_dir']}:/templateflow \\"
                )

            if common.get("optional_folder"):
                script_content += f"\n        -B {common['optional_folder']}:/base \\"

            if common.get("fs_license_file"):
                script_content += (
                    f"\n        -B {common['fs_license_file']}:/fs/license.txt:ro \\"
                )

            for mount in app.get("mounts", []):
                if mount.get("source") and mount.get("target"):
                    script_content += (
                        f"\n        -B {mount['source']}:{mount['target']} \\"
                    )

            script_content += f"""
        --env TEMPLATEFLOW_HOME=/templateflow \\
        {common["container"]} \\
        /fastsurfer/run_fastsurfer.sh \\
        --t1 "$FASTSURFER_T1_CONTAINER" \\
        --sid "$FASTSURFER_SID" \\
        --sd /output \\
"""

            if common.get("fs_license_file"):
                script_content += "\n        --fs_license /fs/license.txt \\\\"

            app_options = _prepare_fastsurfer_options(app.get("options", []))
            for option in app_options:
                script_content += f"\n        {option} \\"

            script_content += f"""
        {container_log_redirection} ;

    FASTSURFER_CMD_RC=$?
    if [ $FASTSURFER_CMD_RC -ne 0 ]; then
        echo "FastSurfer failed for SID $FASTSURFER_SID (exit $FASTSURFER_CMD_RC)"
        FASTSURFER_EXIT=$FASTSURFER_CMD_RC
        break
    fi
done

PROCESS_EXIT_CODE=$FASTSURFER_EXIT
"""
        if not fastsurfer_mode:
            script_content += """
apptainer run \\
"""

            apptainer_args = [str(arg) for arg in app.get("apptainer_args", [])]
            if not apptainer_args:
                apptainer_args = ["--containall"]
            for arg in apptainer_args:
                script_content += f"    {arg} \\\n"

            script_content += f"""    -B {tmp_dir}:/tmp \\
    -B {output_dir}:/output \\
    -B {bids_dir}:/bids \\
"""

            if common.get("templateflow_dir"):
                script_content += f"\n    -B {common['templateflow_dir']}:/templateflow \\"
            if common.get("optional_folder"):
                script_content += f"\n    -B {common['optional_folder']}:/base \\"
            if common.get("fs_license_file"):
                script_content += (
                    f"\n    -B {common['fs_license_file']}:/fs/license.txt:ro \\"
                )
            for mount in app.get("mounts", []):
                if mount.get("source") and mount.get("target"):
                    script_content += f"\n    -B {mount['source']}:{mount['target']} \\"

            script_content += f"""
    --env TEMPLATEFLOW_HOME=/templateflow \\
    {common["container"]} \\
    /bids /output {analysis_level} \\
"""

            app_options = _ensure_mriqc_no_sub_option(
                common.get("container", ""), app.get("options", [])
            )
            if common.get("fs_license_file"):
                if "--fs-license-file" not in app_options and not any(
                    str(a).startswith("--fs-license-file=") for a in app_options
                ):
                    app_options.extend(["--fs-license-file", "/fs/license.txt"])

            for option in app_options:
                script_content += f"\n    {option} \\"

            subject_label = subject.replace("sub-", "")
            script_content += f"""
    --participant-label {subject_label} \\
    -w /tmp{container_log_redirection}
"""

            script_content += """
PROCESS_EXIT_CODE=$?
"""

        script_content += f"""
# Check if processing was successful
if [ $PROCESS_EXIT_CODE -eq 0 ]; then
    echo "Processing completed successfully for {subject}"
    
    # Save results to output repository
    cd {output_dir}
    echo "Changed to output directory: {output_dir}"
    
    # Create output branch if it doesn't exist
    echo "Setting up output branch: {datalad.get("output_branch", "results")}" 
    git checkout {datalad.get("output_branch", "results")} 2>/dev/null || git checkout -b {datalad.get("output_branch", "results")}
    
    # Add and save results
    echo "Saving results for {subject}"
    datalad save -m "Add results for {subject} (job $SLURM_JOB_ID)" || echo "Warning: Could not save results"
    
    # Push to remote if configured
    if [ "{datalad.get("auto_push", "false")}" = "true" ]; then
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
    exit $PROCESS_EXIT_CODE
fi

echo "Job completed at: $(date)"
echo "Total job duration: $SECONDS seconds"
"""

        # Write job script
        if not dry_run:
            with open(job_script, "w") as f:
                f.write(script_content)
            os.chmod(job_script, 0o755)
            logging.info(f"Created job script: {job_script}")
        else:
            logging.info(f"Would create job script: {job_script}")
            logging.debug("Job script preview (first 50 lines):")
            for i, line in enumerate(script_content.split("\n")[:50]):
                logging.debug(f"  {line}")

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


def monitor_jobs(job_ids, poll_interval=60):
    """Monitor SLURM jobs and report status."""
    if not job_ids:
        return

    logging.info(f"Monitoring {len(job_ids)} jobs...")

    while job_ids:
        time.sleep(poll_interval)

        # Check job status
        cmd = ["squeue", "-j", ",".join(job_ids), "--format=%i,%T", "--noheader"]
        try:
            result = run_command(cmd, capture_output=True, check=False)

            if result.returncode == 0:
                running_jobs = []
                for line in result.stdout.strip().split("\n"):
                    if line:
                        job_id, status = line.split(",")
                        logging.info(f"Job {job_id}: {status}")
                        if status in ["PENDING", "RUNNING"]:
                            running_jobs.append(job_id)

                job_ids = running_jobs
            else:
                # No jobs found in queue (likely all completed)
                break

        except Exception as e:
            logging.error(f"Error checking job status: {e}")
            break

    logging.info("All jobs completed or no longer in queue")


def setup_hpc_environment(config, work_dir, dry_run=False):
    """Setup HPC working environment with DataLad repositories."""
    logging.info("Setting up HPC environment...")

    datalad = config.get("datalad", {})

    # Create work directory structure
    bids_dir = os.path.join(work_dir, "input_data")
    output_dir = os.path.join(work_dir, "output_data")

    if not dry_run:
        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(os.path.join(work_dir, "logs"), exist_ok=True)
        os.makedirs(os.path.join(work_dir, "tmp"), exist_ok=True)

    # Clone or setup input dataset
    if datalad.get("input_dataset"):
        logging.info("Setting up input DataLad dataset...")
        if not dry_run:
            prism_datalad.clone_dataset(
                datalad["input_dataset"],
                bids_dir,
                branch=datalad.get("input_branch", "main"),
                dry_run=dry_run,
            )
        else:
            logging.info(f"Would clone {datalad['input_dataset']} to {bids_dir}")

    # Clone or setup output dataset
    if datalad.get("output_dataset"):
        logging.info("Setting up output DataLad dataset...")
        if not dry_run:
            prism_datalad.clone_dataset(
                datalad["output_dataset"],
                output_dir,
                branch=datalad.get("output_branch", "results"),
                dry_run=dry_run,
            )
        else:
            logging.info(f"Would clone {datalad['output_dataset']} to {output_dir}")

    # Update config with actual paths
    config["common"]["bids_folder"] = bids_dir
    config["common"]["output_folder"] = output_dir

    logging.info(f"HPC environment ready at: {work_dir}")
    return bids_dir, output_dir


# ============================================================================
# Main Execution Function
# ============================================================================


def execute_hpc(config: Dict[str, Any], args: Namespace) -> bool:
    """Execute BIDS app in HPC/SLURM mode.

    Args:
        config: Configuration dictionary
        args: Parsed command-line arguments

    Returns:
        True if execution successful, False otherwise
    """
    logging.info("=" * 60)
    logging.info("HPC/SLURM EXECUTION MODE")
    logging.info("=" * 60)

    common = config.get("common", {})
    config.get("app", {})
    hpc = config.get("hpc", {})
    datalad_config = config.get("datalad", {})

    start_time = time.time()

    # Setup working directory
    work_dir = common.get("work_dir", "/tmp/bids_app_work")
    try:
        if not args.dry_run:
            os.makedirs(work_dir, exist_ok=True)
        logging.info(f"Using work directory: {work_dir}")
    except Exception as e:
        logging.error(f"Cannot create work directory: {e}")
        return False

    # Setup DataLad environment if configured
    if datalad_config:
        try:
            bids_dir, output_dir = setup_hpc_environment(config, work_dir, args.dry_run)
        except Exception as e:
            logging.error(f"Error setting up DataLad environment: {e}")
            return False

    # Get subjects
    if args.subjects:
        subjects = [s if s.startswith("sub-") else f"sub-{s}" for s in args.subjects]
        logging.info(f"Processing specified subjects: {subjects}")
    else:
        bids_folder = common.get("bids_folder")
        if bids_folder:
            subjects = get_subjects_from_bids(bids_folder, args.dry_run)
        else:
            logging.warning("No bids_folder specified in config")
            subjects = []

        if not subjects and not args.dry_run:
            logging.error("No subjects found")
            return False
        elif not subjects and args.dry_run:
            logging.info("Dry-run mode: using placeholder subject")
            subjects = ["sub-example"]
        else:
            logging.info(f"Auto-discovered {len(subjects)} subjects")

    # Handle pilot mode
    pilot = common.get("pilottest", False)
    if pilot:
        subject = random.choice(subjects)
        subjects = [subject]
        logging.info(f"Pilot mode: processing only {subject}")

    # Create and submit SLURM jobs
    submitted_jobs = []
    failed_jobs = []

    debug = args.debug if hasattr(args, "debug") else False
    dry_run = args.dry_run if hasattr(args, "dry_run") else False
    slurm_only = args.slurm_only if hasattr(args, "slurm_only") else False
    start_delay_sec = args.start_delay_sec if hasattr(args, "start_delay_sec") else 0.0

    try:
        start_delay_sec = float(start_delay_sec or 0.0)
    except (TypeError, ValueError):
        start_delay_sec = 0.0

    if start_delay_sec < 0:
        logging.warning("Negative --start-delay-sec provided; using 0")
        start_delay_sec = 0.0

    logging.info(f"Creating job scripts for {len(subjects)} subjects...")
    if start_delay_sec > 0 and not dry_run and len(subjects) > 1:
        logging.info(
            f"Staggered launches enabled: waiting {start_delay_sec:.1f}s between SLURM submissions"
        )

    for idx, subject in enumerate(subjects):
        try:
            if idx > 0 and start_delay_sec > 0:
                logging.info(
                    f"Waiting {start_delay_sec:.1f}s before launching next subject ({subject})"
                )
                time.sleep(start_delay_sec)

            logging.info(f"Creating job for subject: {subject}")

            job_script = create_slurm_job(subject, config, work_dir, dry_run, debug)

            if not slurm_only:
                job_id = submit_slurm_job(job_script, dry_run)
                if job_id:
                    submitted_jobs.append(job_id)
                else:
                    failed_jobs.append(subject)
            else:
                logging.info(f"Job script created: {job_script}")

        except Exception as e:
            logging.error(f"Error creating/submitting job for {subject}: {e}")
            failed_jobs.append(subject)

    # Print summary
    end_time = time.time()
    logging.info("=" * 60)
    logging.info("HPC PROCESSING SUMMARY")
    logging.info("=" * 60)
    logging.info(f"Total subjects: {len(subjects)}")

    if slurm_only:
        logging.info(f"Job scripts created: {len(subjects) - len(failed_jobs)}")
        logging.info(f"Failed to create: {len(failed_jobs)}")
    else:
        logging.info(f"Jobs submitted: {len(submitted_jobs)}")
        logging.info(f"Failed to submit: {len(failed_jobs)}")

    logging.info(f"Setup time: {end_time - start_time:.2f} seconds")

    if failed_jobs:
        logging.warning("Failed subjects:")
        for subject in failed_jobs:
            logging.warning(f"  - {subject}")

    logging.info("=" * 60)

    # Monitor jobs if requested and jobs were submitted
    if hasattr(args, "monitor") and args.monitor and submitted_jobs and not dry_run:
        logging.info("Starting job monitoring...")
        monitor_jobs(submitted_jobs, poll_interval=hpc.get("poll_interval", 60))

    return len(failed_jobs) == 0
