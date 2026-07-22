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

from app_profiles import (
    CATALOG,
    check_gpu_request_feasible,
    resolve_app_name,
    resolve_app_profile,
)

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


_MEM_UNIT_TO_GB = {"K": 1 / (1024 * 1024), "M": 1 / 1024, "G": 1, "T": 1024}


_NPROCS_ALIASES = ("--nprocs", "--n_procs", "--n-procs", "--n_cpus", "--n-cpus", "-n-cpus")
_OMP_ALIASES = ("--omp-nthreads", "--omp_nthreads", "--ants-nthreads")
_MEM_ALIASES = ("--mem", "--mem_gb", "--mem-gb")


def _has_flag(options, aliases) -> bool:
    for opt in options:
        token = str(opt).split("=", 1)[0]
        if token in aliases:
            return True
    return False


def _mem_to_gb(mem: str) -> int:
    """Convert a SLURM-style --mem value (e.g. "16G", "32000M") to whole GB,
    for passing to nipreps-style --mem/--mem-gb flags. Falls back to 4 if the
    value can't be parsed rather than failing script generation."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([KMGT]?)", str(mem or "").strip())
    if not match:
        return 4
    value, unit = match.groups()
    gb = float(value) * _MEM_UNIT_TO_GB.get(unit or "M", 1 / 1024)
    return max(1, round(gb))


def _infer_execution_adapter(bids_app: Dict, container_ref: str) -> str:
    """Resolve execution adapter the same way prism_hpc.py/prism_local.py do:
    an explicit bids_app.execution_adapter checked against every catalog
    entry's own alias table (not just fastsurfer's), then falling through to
    the normal app_profile/container-sniffing resolution's
    execution_adapter_default."""
    explicit = str(bids_app.get("execution_adapter", "")).strip().lower()
    if explicit:
        for profile in CATALOG.values():
            aliases = profile.get("execution_adapter_aliases", {})
            if explicit in aliases:
                return aliases[explicit]

    name = resolve_app_name(
        {"container": container_ref},
        {"app_profile": bids_app.get("app_profile", "")},
        container_ref=container_ref,
    )
    return CATALOG.get(name, {}).get("execution_adapter_default", "")


def _prepare_fastsurfer_bids_options(options) -> list:
    """Normalize passthrough options for run_fastsurfer_bids.py (mirrors
    prism_hpc.py/prism_local.py's helper of the same purpose): drop
    runtime-managed flags so they can't be passed through twice."""
    forbidden = {
        "--participant_label",
        "--participant-label",
        "--session_label",
        "--session-label",
        "--fs_license",
    }
    opts = [str(x) for x in (options or [])]
    cleaned = []
    i = 0
    while i < len(opts):
        token = opts[i]
        if token in forbidden:
            if i + 1 < len(opts) and not opts[i + 1].startswith("-"):
                i += 2
            else:
                i += 1
            continue
        if token.startswith("--") and "=" in token and token.split("=", 1)[0] in forbidden:
            i += 1
            continue
        cleaned.append(token)
        i += 1
    return cleaned


def _prepare_freesurfer_bids_options(options) -> list:
    """Normalize passthrough options for run.py (bids-apps/freesurfer,
    fs8.2 branch; mirrors prism_hpc.py/prism_local.py's helper of the same
    purpose): drop runtime-managed flags so they can't be passed through
    twice. --session_label is deliberately NOT forbidden -- unlike
    participant_label/license_file, the adapter never sets it itself, so
    there's no double-set conflict, and it's the documented way to
    restrict a longitudinal run to a subset of a subject's BIDS sessions
    (default with no flag: every session found)."""
    forbidden = {
        "--participant_label",
        "--license_file",
    }
    opts = [str(x) for x in (options or [])]
    cleaned = []
    i = 0
    while i < len(opts):
        token = opts[i]
        if token in forbidden:
            if i + 1 < len(opts) and not opts[i + 1].startswith("-"):
                i += 2
            else:
                i += 1
            continue
        if token.startswith("--") and "=" in token and token.split("=", 1)[0] in forbidden:
            i += 1
            continue
        cleaned.append(token)
        i += 1
    return cleaned


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

    gpu_error = check_gpu_request_feasible(config.get("hpc", {}))
    if gpu_error:
        logging.error(gpu_error)
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
      3. Run BIDS app via apptainer run (invokes the container's entrypoint/
         runscript), reading from the shared input clone and writing into
         the shared output clone
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

        # datalad-slurm's own bookkeeping (get_slurm_output_files in the
        # datalad-slurm package) reads back the SBATCH --output/--error paths
        # via `scontrol show job` and expects them to live *inside* the
        # dataset it's scheduling from, so it can save them for provenance
        # alongside the job's declared outputs. Pointing them at a location
        # outside the dataset (e.g. this repo's own logs/ folder) makes
        # `datalad slurm-finish` fail with "path not underneath the reference
        # dataset" for every log file, aborting before it can push. Use a
        # dedicated subdirectory of the output dataset itself instead.
        output_dir = self.paths.get("output_dir", "")
        log_dir = (
            f"{output_dir}/.slurm_logs"
            if output_dir
            else self.paths.get("log_dir", "$HOME/logs/bids_app")
        )
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

# Pre-flight: verify user is resolvable (apptainer requires getpwuid to succeed)
if ! getent passwd "$(id -u)" > /dev/null 2>&1; then
    echo "FATAL: Cannot resolve user UID $(id -u) on $(hostname)" >&2
    echo "       Apptainer will fail with: Couldn't determine user account information" >&2
    echo "       HPC admin must configure LDAP/sssd on compute nodes so getent passwd $(id -u) works" >&2
    exit 1
fi

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

    def _run_fastsurfer_bids(self) -> str:
        """FastSurfer-bids adapter: run_fastsurfer_bids.py (inside the
        container) auto-discovers a subject's sessions via pybids and
        dispatches to FastSurfer's longitudinal pipeline (long_fastsurfer.sh)
        for subjects with >=2 sessions, or the cross-sectional pipeline
        (brun_fastsurfer.sh) otherwise. Mirrors the fastsurfer_bids_mode
        branch in prism_hpc.py/prism_local.py, adapted for this array job's
        runtime $SUBJECT_LABEL/$BIDS_DIR/$OUT_DIR/$TMP_DIR bash variables --
        the subject isn't known at script-generation time here, it's
        resolved from the subject list file via $SLURM_ARRAY_TASK_ID.
        """
        analysis_level = self.bids_app.get("analysis_level", "participant")
        options = _prepare_fastsurfer_bids_options(self.bids_app.get("options", []))
        container = _shell_quote(self.paths.get("container", "TODO_CONTAINER_PATH"))

        # slurm_nohog (the cluster's GPU-misuse watchdog) kills any job that
        # doesn't use the exact GPU index SLURM assigned it, or that touches
        # a GPU device at all with no gres/gpu allocation -- confirmed by
        # the HPC admin against job IDs cancelled today. --cleanenv strips
        # CUDA_VISIBLE_DEVICES (which SLURM sets on the host to the assigned
        # index) before the container starts, so the container always fell
        # back to physical device 0 regardless of what was actually granted.
        # Mirrors the fix already applied in _run_bids_app below.
        hpc_requests_gpu = any(
            "gpu" in str(v).lower()
            for k, v in self.hpc.items()
            if k.startswith("sbatch_") and v
        )
        env_pairs = (
            ["CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-}"]
            if hpc_requests_gpu
            else ["CUDA_VISIBLE_DEVICES="]
        )
        extra_env = f"    --env {_shell_quote(','.join(env_pairs))} \\\n"

        apptainer_args = [str(a) for a in (self.bids_app.get("apptainer_args") or [])]
        if hpc_requests_gpu and "--nv" not in apptainer_args:
            apptainer_args.append("--nv")
        extra_apptainer_args = "".join(f"    {a} \\\n" for a in apptainer_args)

        fs = (self.paths.get("fs_license") or "").strip()
        extra_binds = f"    -B {_shell_quote(fs)}:/fs/license.txt:ro \\\n" if fs else ""

        # Built as a list and joined explicitly (rather than juxtaposed
        # f-string fragments) so the very last argument never ends up with a
        # trailing " \" immediately followed by a blank line -- that pattern
        # silently truncates the command in bash (fixed for the same reason
        # in prism_hpc.py::create_slurm_job).
        fs_args = [
            "python3 /fastsurfer/run_fastsurfer_bids.py",
            "/bids",
            "/output",
            _shell_quote(analysis_level),
            "--participant_label",
            '"${SUBJECT_LABEL}"',
        ]
        if fs:
            fs_args += ["--fs_license", "/fs/license.txt"]
        if options:
            fs_args += ["--"] + [_shell_quote(str(opt)) for opt in options]
        fs_cmd = " \\\n    ".join(fs_args)

        return f"""
# Run FastSurfer (BIDS, longitudinal-aware): {self._app_name}
echo "--- Running run_fastsurfer_bids.py for sub-${{SUBJECT_LABEL}} ---"
"$APPTAINER_BIN" exec \\
    --cleanenv \\
{extra_env}{extra_apptainer_args}    -B "${{BIDS_DIR}}":/bids:ro \\
    -B "${{OUT_DIR}}":/output \\
{extra_binds}    -B "${{TMP_DIR}}":/tmp \\
    {container} \\
    {fs_cmd}

echo "run_fastsurfer_bids.py finished for sub-${{SUBJECT_LABEL}}"
"""

    def _run_freesurfer_bids(self) -> str:
        """FreeSurfer-bids adapter: run.py (bids-apps/freesurfer, fs8.2
        branch, inside the container) auto-discovers a subject's sessions
        and dispatches cross-sectional -> base template -> longitudinal
        internally in one call. Mirrors the freesurfer_bids_mode branch in
        prism_hpc.py/prism_local.py, adapted for this array job's runtime
        $SUBJECT_LABEL/$BIDS_DIR/$OUT_DIR/$TMP_DIR bash variables -- the
        subject isn't known at script-generation time here, it's resolved
        from the subject list file via $SLURM_ARRAY_TASK_ID.

        Unlike _run_fastsurfer_bids above, recon-all is CPU-only: no --nv,
        no CUDA_VISIBLE_DEVICES handling needed.
        """
        analysis_level = self.bids_app.get("analysis_level", "participant")
        options = _prepare_freesurfer_bids_options(self.bids_app.get("options", []))
        container = _shell_quote(self.paths.get("container", "TODO_CONTAINER_PATH"))

        apptainer_args = [str(a) for a in (self.bids_app.get("apptainer_args") or [])]
        extra_apptainer_args = "".join(f"    {a} \\\n" for a in apptainer_args)

        fs = (self.paths.get("fs_license") or "").strip()
        extra_binds = f"    -B {_shell_quote(fs)}:/fs/license.txt:ro \\\n" if fs else ""

        # Built as a list and joined explicitly, same reasoning as
        # _run_fastsurfer_bids above: avoids a trailing " \" immediately
        # followed by a blank line, which silently truncates the command.
        fs_args = ["python /run.py", "/bids", "/output", _shell_quote(analysis_level)]
        if analysis_level == "participant":
            fs_args += ["--participant_label", '"${SUBJECT_LABEL}"']
        if fs:
            fs_args += ["--license_file", "/fs/license.txt"]
        fs_args += [_shell_quote(str(opt)) for opt in options]
        fs_cmd = " \\\n    ".join(fs_args)

        return f"""
# Run FreeSurfer (BIDS, longitudinal-aware): {self._app_name}
echo "--- Running run.py for sub-${{SUBJECT_LABEL}} ---"
"$APPTAINER_BIN" exec \\
{extra_apptainer_args}    -B "${{BIDS_DIR}}":/bids:ro \\
    -B "${{OUT_DIR}}":/output \\
{extra_binds}    -B "${{TMP_DIR}}":/tmp \\
    {container} \\
    {fs_cmd}

echo "run.py finished for sub-${{SUBJECT_LABEL}}"
"""

    def _run_bids_app(self) -> str:
        app_name = self._app_name
        analysis_level = self.bids_app.get("analysis_level", "participant")
        options = list(self.bids_app.get("options", []))

        # Nipreps-convention resource caps (mriqc/fmriprep/qsiprep/qsirecon
        # honor these) so the app's own worker pool respects the SLURM
        # allocation instead of auto-detecting the whole (possibly much
        # larger) node. Without this, internal process/thread counts can
        # exceed --cpus-per-task and exhaust a node's shared process limit
        # (ulimit -u) or --mem when several array tasks share one node.
        # Resolved via the shared app profile catalog (scripts/app_profiles.py)
        # -- only known NiPreps apps get these flags; other BIDS apps may not
        # recognize them at all.
        container_path = self.paths.get("container", "")
        profile = resolve_app_profile(
            {"pipeline_app_name": app_name, "container": container_path},
            {
                "app_profile": self.bids_app.get("app_profile", ""),
                "app_profile_overrides": self.bids_app.get("app_profile_overrides") or {},
            },
            container_ref=container_path,
        )

        execution_adapter = _infer_execution_adapter(self.bids_app, container_path)
        if execution_adapter == "fastsurfer-bids":
            return self._run_fastsurfer_bids()
        if execution_adapter == "freesurfer-bids":
            return self._run_freesurfer_bids()

        # Force CPU-only execution unless a GPU was actually requested via
        # sbatch_gres (mirrors the --nv gating in prism_hpc.py/prism_local.py).
        # DSI Studio (bundled in qsiprep) auto-probes CUDA at startup even
        # when no --nv/GPU device is mounted into the container; on nodes
        # with a partial/broken NVIDIA userspace stack that probe can hang
        # indefinitely instead of failing fast, which is what stalled every
        # task of job 5365074 at the raw_gqi (DSIStudioGQIReconstruction)
        # step for the full 24h walltime. Setting CUDA_VISIBLE_DEVICES=
        # makes any CUDA-aware tool see zero devices immediately and fall
        # back to its CPU path.
        #
        # When a GPU *is* requested, --cleanenv still strips the
        # CUDA_VISIBLE_DEVICES that SLURM sets on the host to the assigned
        # GPU index -- confirmed by the HPC admin's slurm_nohog watchdog log
        # ("Misuse detected: Job N assigned [X] but using [0]. Action:
        # Immediate Cancel.") against several of our GPU jobs. Explicitly
        # re-passing it through is required, not optional, or a GPU job is
        # just as likely to get killed as a CPU-only one that touches a
        # device it was never allocated.
        hpc_requests_gpu = any(
            "gpu" in str(v).lower()
            for k, v in self.hpc.items()
            if k.startswith("sbatch_") and v
        )
        env_pairs = (
            ["CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-}"]
            if hpc_requests_gpu
            else ["CUDA_VISIBLE_DEVICES="]
        )

        # QSIPrep's ExtendedEddy interface does NOT auto-detect CUDA -- it
        # only runs eddy_cuda if the eddy config it's given has
        # "use_cuda": true (default shipped config has it false, so eddy
        # runs on CPU even with a GPU allocated and --nv passed). It also
        # hardcodes the binary name "eddy_cuda10.2" regardless of which
        # CUDA-versioned eddy binary the image actually ships (this image
        # only has eddy_cuda11.0), so a same-named shim has to be bound in
        # too. Verified against qsiprep_26.0.0.sif: bind-mounting a file
        # onto a not-yet-existing path inside the image works fine even
        # though the SIF itself is read-only.
        qsiprep_gpu_dir = Path(__file__).resolve().parent.parent / "patches" / "qsiprep_gpu"
        if hpc_requests_gpu and app_name == "qsiprep":
            eddy_shim = qsiprep_gpu_dir / "eddy_cuda10.2"
            eddy_config = qsiprep_gpu_dir / "eddy_params_gpu.json"
            apptainer_args_gpu_eddy = [
                f'-B "{eddy_shim}":/app/.pixi/envs/qsiprep/bin/eddy_cuda10.2:ro',
                f'-B "{eddy_config}":/opt/eddy_params_gpu.json:ro',
            ]
            if not _has_flag(options, ("--eddy-config",)):
                options.append("--eddy-config=/opt/eddy_params_gpu.json")
        else:
            apptainer_args_gpu_eddy = []

        extra_env = ""
        if profile.get("supports_nipreps_resource_flags"):
            cpus = int(self.hpc.get("cpus", 8))
            mem_gb = _mem_to_gb(self.hpc.get("mem", "32G"))
            if not _has_flag(options, _NPROCS_ALIASES):
                options.append(f"--nprocs={cpus}")
            if not _has_flag(options, _OMP_ALIASES):
                options.append(f"--omp-nthreads={cpus}")
            if not _has_flag(options, _MEM_ALIASES):
                options.append(f"--mem={mem_gb}")

            # --nprocs/--omp-nthreads alone are not always sufficient: some
            # underlying libraries (e.g. AFNI, invoked by nipype's afni
            # interfaces) don't consistently honor MRIQC's own CLI flags and
            # instead read thread-count env vars directly, defaulting to the
            # *node's* full core count if unset. With --cleanenv, plain host
            # `export` doesn't reach the container -- these must be passed
            # explicitly via --env so several array tasks sharing one large
            # node don't collectively exhaust its shared process limit
            # (ulimit -u) the same way unconstrained internal threading did.
            env_pairs.extend(
                f"{var}={cpus}"
                for var in (
                    "OMP_NUM_THREADS",
                    "MKL_NUM_THREADS",
                    "OPENBLAS_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS",
                    "ITK_GLOBAL_DEFAULT_NUMBER_OF_THREADS",
                )
            )
        if env_pairs:
            extra_env = f"    --env {_shell_quote(','.join(env_pairs))} \\\n"
        for auto_opt in profile.get("auto_options") or []:
            if auto_opt not in options:
                options.append(auto_opt)

        container = _shell_quote(self.paths.get("container", "TODO_CONTAINER_PATH"))

        # --nv exposes the node's NVIDIA driver/devices to the container --
        # without it, requesting --gres=gpu:1 reserves a GPU at the SLURM
        # level but the container still can't see it at all. Mirrors
        # prism_hpc.py's/prism_local.py's gating. Note this alone is *not*
        # sufficient for qsiprep's eddy step to actually use the GPU -- see
        # the --eddy-config/eddy_cuda10.2 shim wiring above.
        apptainer_args = [str(a) for a in (self.bids_app.get("apptainer_args") or [])]
        if hpc_requests_gpu and "--nv" not in apptainer_args:
            apptainer_args.append("--nv")
        apptainer_args.extend(apptainer_args_gpu_eddy)
        extra_apptainer_args = "".join(f"    {a} \\\n" for a in apptainer_args)

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
"$APPTAINER_BIN" run \\
    --cleanenv \\
{extra_apptainer_args}{extra_env}    -B "${{BIDS_DIR}}":/bids:ro \\
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
