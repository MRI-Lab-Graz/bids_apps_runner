#!/usr/bin/env python3
"""
PRISM Local - Local/cluster execution mode

Handles BIDS app execution on local machines or traditional compute clusters
using Python multiprocessing for parallel subject processing.

Extracted from: run_bids_apps.py
Author: BIDS Apps Runner Team (PRISM Edition)
Version: 3.0.0
"""

import os
import logging
import platform
import subprocess
import shutil
import time
import glob
import multiprocessing
import concurrent.futures
import random
import json
import re
import hashlib
from collections import deque
from pathlib import Path
from typing import Dict, Any, Optional
from argparse import Namespace
from datetime import datetime

# Import from PRISM modules
from prism_core import get_subjects_from_bids, print_summary
import prism_datalad

# ============================================================================
# Helper Functions for Container Execution
# ============================================================================


def _sanitize_apptainer_args(apptainer_args):
    """Sanitize apptainer args to avoid invalid invocations."""
    if not apptainer_args:
        return []

    sanitized = []
    i = 0
    while i < len(apptainer_args):
        token = str(apptainer_args[i])

        if token.startswith("--env="):
            if "=" not in token[len("--env=") :]:
                logging.warning(f"Ignoring invalid apptainer arg '{token}'")
                i += 1
                continue
            sanitized.append(token)
            i += 1
            continue

        if token == "--env":
            if i + 1 >= len(apptainer_args):
                logging.warning(
                    "Ignoring invalid apptainer arg '--env' (missing KEY=VALUE)"
                )
                i += 1
                continue

            value = str(apptainer_args[i + 1])
            if value.startswith("-") or "=" not in value:
                logging.warning(f"Ignoring invalid apptainer args '--env {value}'")
                i += 2 if not value.startswith("-") else 1
                continue

            sanitized.extend([token, value])
            i += 2
            continue

        sanitized.append(token)
        i += 1

    return sanitized


def _build_common_mounts(common, tmp_dir, bids_folder_override=None):
    """Build common mount points for the container."""
    bids_source = bids_folder_override or common["bids_folder"]
    bids_mount = f"{bids_source}:/bids:ro"
    mounts = [
        f"{tmp_dir}:/tmp",
        f"{common['output_folder']}:/output",
        bids_mount,
    ]

    # FreeSurfer license file (optional)
    if common.get("fs_license_file") and os.path.exists(common["fs_license_file"]):
        mounts.append(f"{common['fs_license_file']}:/fs/license.txt:ro")

    # Only add templateflow if it's specified and exists
    if common.get("templateflow_dir") and os.path.exists(common["templateflow_dir"]):
        mounts.append(f"{common['templateflow_dir']}:/templateflow")

    if common.get("optional_folder"):
        mounts.append(f"{common['optional_folder']}:/base")

    return mounts


def _fix_bids_uri_intendedfor_for_subject(bids_folder, subject):
    """Normalize IntendedFor entries using bids:: URIs for one subject.

    Older pybids/upath stacks may fail with "Protocol not known: 'bids'".
    This rewrites only subject fmap JSON files that include bids:: in IntendedFor.
    """
    subject_label = subject.replace("sub-", "")
    subject_dir = os.path.join(bids_folder, f"sub-{subject_label}")
    if not os.path.isdir(subject_dir):
        return 0

    fixed_files = 0
    fmap_jsons = glob.glob(
        os.path.join(subject_dir, "**", "fmap", "*.json"), recursive=True
    )

    for json_file in fmap_jsons:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        intended_for = data.get("IntendedFor")
        if not intended_for:
            continue

        was_string = isinstance(intended_for, str)
        intended_list = [intended_for] if was_string else list(intended_for)
        changed = False
        normalized = []

        for item in intended_list:
            if not isinstance(item, str):
                normalized.append(item)
                continue

            new_item = item
            if new_item.startswith("bids::"):
                new_item = new_item[len("bids::") :]
                changed = True

            if new_item.startswith("/"):
                new_item = new_item.lstrip("/")
                changed = True

            normalized.append(new_item)

        if not changed:
            continue

        data["IntendedFor"] = (
            normalized[0] if was_string and len(normalized) == 1 else normalized
        )
        try:
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
                f.write("\n")
            fixed_files += 1
        except Exception as exc:
            logging.warning(f"Could not update IntendedFor in {json_file}: {exc}")

    return fixed_files


def _prepare_qsiprep_runtime_bids_view(bids_folder, tmp_dir, subject):
    """Create a subject-scoped temporary BIDS tree for non-destructive fixes."""
    subject_label = subject if subject.startswith("sub-") else f"sub-{subject}"
    source_root = os.path.abspath(bids_folder)
    source_subject = os.path.join(source_root, subject_label)
    if not os.path.isdir(source_subject):
        raise FileNotFoundError(
            f"Subject folder not found in BIDS dataset: {source_subject}"
        )

    runtime_root = os.path.join(tmp_dir, "bids_runtime")
    os.makedirs(runtime_root, exist_ok=True)

    # Copy minimal top-level BIDS files that many tools expect.
    for filename in [
        "dataset_description.json",
        "participants.tsv",
        "participants.json",
        "README",
        "CHANGES",
    ]:
        src = os.path.join(source_root, filename)
        dst = os.path.join(runtime_root, filename)
        if os.path.isfile(src):
            shutil.copy2(src, dst)

    # Copy only the target subject tree.
    dst_subject = os.path.join(runtime_root, subject_label)
    if os.path.exists(dst_subject):
        shutil.rmtree(dst_subject)
    shutil.copytree(source_subject, dst_subject)

    return runtime_root


def _is_qsiprep_container(container_ref):
    """Return True only for QSIPrep container references."""
    ref = str(container_ref or "").strip().lower()
    if not ref:
        return False

    # Match common refs:
    # - /path/to/qsiprep_1.1.1.sif
    # - pennlinc/qsiprep:1.1.1
    # - qsiprep:latest
    if "/qsiprep:" in ref:
        return True
    if ref.startswith("qsiprep:"):
        return True

    base = os.path.basename(ref)
    return base.startswith("qsiprep")


def _is_qsirecon_container(container_ref):
    """Return True only for QSIRecon container references."""
    ref = str(container_ref or "").strip().lower()
    if not ref:
        return False

    if "/qsirecon:" in ref:
        return True
    if ref.startswith("qsirecon:"):
        return True

    base = os.path.basename(ref)
    return base.startswith("qsirecon")


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

    # Disable uploading anonymous metrics unless user explicitly opted out of this behavior.
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
    """Normalize options for /fastsurfer/run_fastsurfer.sh.

    Runtime-specific flags are managed by the adapter and must not be passed
    from generic app options.
    """
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


def _discover_fastsurfer_subject_inputs(bids_folder, subject):
    """Discover per-subject T1 inputs and derive FastSurfer SIDs.

    Mirrors the wrapper strategy: find T1w files under the subject tree and
    derive SID as sub-<id>[_ses-<id>].
    """
    subject_id = _normalize_subject_id(subject)
    if not subject_id or subject_id == "group":
        return []

    subject_dir = os.path.join(str(bids_folder or ""), subject_id)
    if not os.path.isdir(subject_dir):
        return []

    patterns = [
        "*_desc-preproc_T1w.nii.gz",
        "*_T1w.nii.gz",
        "*_T1w.nii",
    ]

    candidates = []
    seen_paths = set()
    for pattern in patterns:
        for path in sorted(
            glob.glob(os.path.join(subject_dir, "**", pattern), recursive=True)
        ):
            abs_path = os.path.abspath(path)
            if abs_path in seen_paths or not os.path.isfile(abs_path):
                continue
            seen_paths.add(abs_path)
            candidates.append(abs_path)

    resolved = []
    seen_sids = set()
    for path in candidates:
        rel = os.path.relpath(path, bids_folder).replace(os.sep, "/")
        match = re.search(r"(sub-[^/_]+)(?:/(ses-[^/_]+))?", rel)

        sid_base = subject_id
        ses_part = ""
        if match:
            sid_base = match.group(1)
            ses_part = match.group(2) or ""

        sid = f"{sid_base}_{ses_part}" if ses_part else sid_base

        if sid in seen_sids:
            logging.warning(
                "FastSurfer adapter: multiple T1w files for SID %s; keeping first and skipping %s",
                sid,
                rel,
            )
            continue

        seen_sids.add(sid)
        resolved.append({"sid": sid, "t1_relpath": rel})

    return resolved


def _run_container(
    cmd, env=None, dry_run=False, debug=False, subject=None, log_dir=None
):
    """Execute container command with optional dry run mode and detailed logging."""
    cmd_str = " ".join(cmd)

    if dry_run:
        logging.info(f"DRY RUN - Would execute: {cmd_str}")
        logging.info("✅ Command syntax validated successfully")
        return None

    logging.info(f"Running command: {cmd_str}")

    # Create container log files if debug mode is enabled
    container_log_file = None
    container_error_file = None

    if debug and subject and log_dir:
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        container_log_file = os.path.join(
            log_dir, f"container_{subject}_{timestamp}.log"
        )
        container_error_file = os.path.join(
            log_dir, f"container_{subject}_{timestamp}.err"
        )
        logging.info(f"Debug mode: Container logs saved to {container_log_file}")

    try:
        run_env = env or os.environ.copy()

        if debug:
            logging.info("Debug mode: Starting container with real-time logging...")

            with (
                (
                    open(container_log_file, "w")
                    if container_log_file
                    else open(os.devnull, "w")
                ) as stdout_file,
                (
                    open(container_error_file, "w")
                    if container_error_file
                    else open(os.devnull, "w")
                ) as stderr_file,
            ):
                process = subprocess.Popen(
                    cmd,
                    env=run_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )

                stdout_data, stderr_data = process.communicate()
                return_code = process.wait()

                if container_log_file and stdout_data:
                    stdout_file.write(stdout_data)
                if container_error_file and stderr_data:
                    stderr_file.write(stderr_data)

                class DebugResult:
                    def __init__(self, returncode, stdout, stderr):
                        self.returncode = returncode
                        self.stdout = stdout
                        self.stderr = stderr

                result = DebugResult(return_code, stdout_data, stderr_data)

                if return_code != 0:
                    raise subprocess.CalledProcessError(
                        return_code, cmd, result.stdout, result.stderr
                    )
        else:
            process = subprocess.Popen(
                cmd,
                env=run_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            output_tail = deque(maxlen=2000)
            if process.stdout:
                for line in process.stdout:
                    cleaned = line.rstrip("\n")
                    if cleaned:
                        logging.info(cleaned)
                    output_tail.append(cleaned)

            return_code = process.wait()
            stdout_combined = "\n".join(output_tail)

            class RunResult:
                def __init__(self, returncode, stdout, stderr):
                    self.returncode = returncode
                    self.stdout = stdout
                    self.stderr = stderr

            result = RunResult(return_code, stdout_combined, "")

            if return_code != 0:
                raise subprocess.CalledProcessError(
                    return_code, cmd, result.stdout, result.stderr
                )

        logging.info("Container execution completed successfully")
        return result

    except subprocess.CalledProcessError as e:
        logging.error(f"Container execution failed with exit code {e.returncode}")
        if e.stdout:
            logging.error(f"stdout: {e.stdout[:500]}")
        if e.stderr:
            logging.error(f"stderr: {e.stderr[:500]}")
        raise
    except Exception as e:
        logging.error(f"Unexpected error during execution: {e}")
        raise


# ============================================================================
# Subject Processing Functions
# ============================================================================


def _normalize_subject_id(subject: str) -> str:
    """Normalize subject identifiers to the canonical sub-<label> form."""
    subject_str = str(subject or "").strip()
    if not subject_str:
        return ""
    if subject_str.lower() == "group":
        return "group"
    return subject_str if subject_str.startswith("sub-") else f"sub-{subject_str}"


def _sanitize_marker_component(value, default=""):
    """Normalize marker namespace components to filesystem-safe tokens."""
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_.-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if text:
        return text
    return str(default or "")


def _derive_marker_namespace(config: Dict[str, Any]) -> str:
    """Derive a stable namespace for success markers and project completion state."""
    common = config.get("common", {}) if isinstance(config, dict) else {}
    active_pipeline = _sanitize_marker_component(
        (config or {}).get("active_pipeline"), default="default"
    )
    version = _sanitize_marker_component(common.get("pipeline_version"), default="")

    output_folder = str(common.get("output_folder") or "").strip()
    output_hash = ""
    if output_folder:
        output_hash = hashlib.sha1(
            os.path.abspath(output_folder).encode("utf-8")
        ).hexdigest()[:10]

    parts = [active_pipeline or "default"]
    if version:
        parts.append(version)
    if output_hash:
        parts.append(output_hash)

    return "__".join(parts)


def _get_success_marker_paths(subject, common, marker_namespace=None):
    """Return candidate success marker paths, preferring namespaced markers."""
    marker_dir = os.path.join(common["output_folder"], ".bids_app_runner")
    candidates = []
    if marker_namespace:
        candidates.append(
            os.path.join(marker_dir, f"{marker_namespace}__{subject}_success.txt")
        )
    # Legacy marker fallback for backward compatibility.
    candidates.append(os.path.join(marker_dir, f"{subject}_success.txt"))

    deduped = []
    for path in candidates:
        if path not in deduped:
            deduped.append(path)
    return deduped


def _resolve_project_json_path(config_path: Optional[str]) -> Optional[str]:
    """Resolve a CLI config path to a valid project.json path when applicable."""
    if not config_path:
        return None

    cfg = Path(os.path.expanduser(str(config_path)))
    scripts_dir = Path(__file__).resolve().parent
    app_root = scripts_dir.parent

    candidates = (
        [cfg]
        if cfg.is_absolute()
        else [Path.cwd() / cfg, scripts_dir / cfg, app_root / cfg]
    )

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            continue

        if (
            not resolved.exists()
            or not resolved.is_file()
            or resolved.name != "project.json"
        ):
            continue

        try:
            with open(resolved, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        if isinstance(data, dict) and isinstance(data.get("config"), dict):
            return str(resolved)

    return None


def _read_project_subject_state(
    project_json_path: Optional[str], subject: str, marker_namespace: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Read per-subject runner state from project.json."""
    if not project_json_path:
        return None

    try:
        with open(project_json_path, "r", encoding="utf-8") as f:
            project_data = json.load(f)
    except Exception as e:
        logging.warning(f"Could not read project state from {project_json_path}: {e}")
        return None

    runner_state = project_data.get("runner_state", {})
    subject_id = _normalize_subject_id(subject)
    if not subject_id:
        return None

    if marker_namespace:
        namespaces = runner_state.get("namespaces", {})
        if not isinstance(namespaces, dict):
            return None

        ns_state = namespaces.get(marker_namespace, {})
        if not isinstance(ns_state, dict):
            return None

        subjects_state = ns_state.get("subjects", {})
        if not isinstance(subjects_state, dict):
            return None

        subject_state = subjects_state.get(subject_id)
        return subject_state if isinstance(subject_state, dict) else None

    subjects_state = runner_state.get("subjects", {})
    if not isinstance(subjects_state, dict):
        return None

    subject_state = subjects_state.get(subject_id)
    return subject_state if isinstance(subject_state, dict) else None


def _write_project_subject_state(
    project_json_path: Optional[str],
    subject: str,
    status: str,
    reason: str = "",
    marker_namespace: Optional[str] = None,
) -> bool:
    """Persist subject completion state in project.json."""
    if not project_json_path:
        return False

    subject_id = _normalize_subject_id(subject)
    if not subject_id:
        return False

    try:
        with open(project_json_path, "r", encoding="utf-8") as f:
            project_data = json.load(f)
    except Exception as e:
        logging.warning(f"Could not load project.json for subject state update: {e}")
        return False

    now_iso = datetime.now().isoformat()
    runner_state = project_data.setdefault("runner_state", {})

    if marker_namespace:
        namespaces = runner_state.setdefault("namespaces", {})
        if not isinstance(namespaces, dict):
            namespaces = {}
            runner_state["namespaces"] = namespaces

        ns_state = namespaces.setdefault(marker_namespace, {})
        if not isinstance(ns_state, dict):
            ns_state = {}
            namespaces[marker_namespace] = ns_state

        subjects_state = ns_state.setdefault("subjects", {})
        if not isinstance(subjects_state, dict):
            subjects_state = {}
            ns_state["subjects"] = subjects_state
    else:
        subjects_state = runner_state.setdefault("subjects", {})
        if not isinstance(subjects_state, dict):
            subjects_state = {}
            runner_state["subjects"] = subjects_state

    state = subjects_state.get(subject_id, {})
    if not isinstance(state, dict):
        state = {}

    state["status"] = status
    state["finished"] = status == "finished"
    state["updated_at"] = now_iso
    if status == "finished":
        state["finished_at"] = now_iso
    elif status == "failed":
        state["failed_at"] = now_iso
    if reason:
        state["reason"] = reason
    if marker_namespace:
        state["namespace"] = marker_namespace

    subjects_state[subject_id] = state
    if marker_namespace:
        namespaces = runner_state.get("namespaces", {})
        ns_state = namespaces.get(marker_namespace, {}) if isinstance(namespaces, dict) else {}
        if isinstance(ns_state, dict):
            ns_state["updated_at"] = now_iso
    project_data["last_modified"] = now_iso

    tmp_path = f"{project_json_path}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(project_data, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, project_json_path)
        return True
    except Exception as e:
        logging.warning(f"Could not write project.json subject state: {e}")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False


def _clear_success_markers(
    common: Dict[str, Any], project_json_path: Optional[str] = None
) -> None:
    """Clear filesystem and project success markers."""
    removed_files = 0
    marker_dir = os.path.join(common["output_folder"], ".bids_app_runner")

    if os.path.isdir(marker_dir):
        for marker_path in glob.glob(os.path.join(marker_dir, "*_success.txt")):
            try:
                os.remove(marker_path)
                removed_files += 1
            except Exception as e:
                logging.warning(f"Could not remove marker {marker_path}: {e}")

    cleared_project_markers = 0
    if project_json_path:
        try:
            with open(project_json_path, "r", encoding="utf-8") as f:
                project_data = json.load(f)

            runner_state = project_data.get("runner_state", {})
            any_removed = False
            subjects_state = runner_state.get("subjects", {})
            if isinstance(subjects_state, dict):
                to_remove = [
                    sid
                    for sid, state in subjects_state.items()
                    if isinstance(state, dict)
                    and (state.get("finished") or state.get("status") == "finished")
                ]
                for sid in to_remove:
                    subjects_state.pop(sid, None)
                if to_remove:
                    any_removed = True
                cleared_project_markers += len(to_remove)

            namespaces = runner_state.get("namespaces", {})
            if isinstance(namespaces, dict):
                for ns_name, ns_state in namespaces.items():
                    if not isinstance(ns_state, dict):
                        continue
                    ns_subjects = ns_state.get("subjects", {})
                    if not isinstance(ns_subjects, dict):
                        continue
                    ns_to_remove = [
                        sid
                        for sid, state in ns_subjects.items()
                        if isinstance(state, dict)
                        and (state.get("finished") or state.get("status") == "finished")
                    ]
                    for sid in ns_to_remove:
                        ns_subjects.pop(sid, None)
                    if ns_to_remove:
                        ns_state["updated_at"] = datetime.now().isoformat()
                        any_removed = True
                    cleared_project_markers += len(ns_to_remove)

            if any_removed:
                project_data["last_modified"] = datetime.now().isoformat()
                tmp_path = f"{project_json_path}.tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(project_data, f, indent=2)
                    f.write("\n")
                os.replace(tmp_path, project_json_path)
        except Exception as e:
            logging.warning(f"Could not clear project.json success markers: {e}")

    logging.info(
        "Cleared %d success marker file(s) and %d project marker(s)",
        removed_files,
        cleared_project_markers,
    )


def _check_generic_output_exists(subject, common):
    """Check for generic output patterns that most BIDS apps produce."""
    output_dir = common["output_folder"]

    subject_raw = str(subject).strip()
    subject_no_prefix = (
        subject_raw[4:] if subject_raw.startswith("sub-") else subject_raw
    )
    subject_with_prefix = f"sub-{subject_no_prefix}"

    patterns_to_check = [
        os.path.join(output_dir, subject_raw),
        os.path.join(output_dir, subject_with_prefix),
        os.path.join(output_dir, "derivatives", "*", subject_raw),
        os.path.join(output_dir, "derivatives", "*", subject_with_prefix),
        os.path.join(output_dir, subject_raw, "func", f"{subject_raw}_*"),
        os.path.join(
            output_dir, subject_with_prefix, "func", f"{subject_with_prefix}_*"
        ),
        os.path.join(output_dir, subject_raw, "anat", f"{subject_raw}_*"),
        os.path.join(
            output_dir, subject_with_prefix, "anat", f"{subject_with_prefix}_*"
        ),
        os.path.join(output_dir, subject_raw, "dwi", f"{subject_raw}_*"),
        os.path.join(
            output_dir, subject_with_prefix, "dwi", f"{subject_with_prefix}_*"
        ),
        os.path.join(output_dir, f"{subject_raw}.html"),
        os.path.join(output_dir, f"{subject_with_prefix}.html"),
        # MRIQC often emits modality reports as <sub-XXX>_*.html at output root.
        os.path.join(output_dir, f"{subject_raw}_*.html"),
        os.path.join(output_dir, f"{subject_with_prefix}_*.html"),
        # QSIRecon: outputs go to <output>/qsirecon-<workflow>/sub-X/ (flat)
        os.path.join(output_dir, "qsirecon-*", subject_raw),
        os.path.join(output_dir, "qsirecon-*", subject_with_prefix),
        # QSIRecon: outputs may also go to <output>/derivatives/qsirecon-<workflow>/sub-X/
        os.path.join(output_dir, "derivatives", "qsirecon-*", subject_raw),
        os.path.join(output_dir, "derivatives", "qsirecon-*", subject_with_prefix),
        os.path.join(output_dir, "derivatives", "qsirecon-*", f"{subject_raw}_*.html"),
        os.path.join(
            output_dir, "derivatives", "qsirecon-*", f"{subject_with_prefix}_*.html"
        ),
    ]

    for pattern in patterns_to_check:
        matches = glob.glob(pattern)
        if matches:
            logging.debug(f"Found output for {subject}: {matches[0]}")
            return True

    # Check if subject directory exists and is non-empty
    for candidate_subject in [subject_raw, subject_with_prefix]:
        subject_dir = os.path.join(output_dir, candidate_subject)
        if os.path.isdir(subject_dir):
            try:
                for root, dirs, files in os.walk(subject_dir):
                    if files:
                        return True
            except Exception:
                pass

    return False


def _subject_processed(
    subject,
    common,
    app,
    force=False,
    project_json_path=None,
    marker_namespace=None,
):
    """Check if a subject has already been processed via explicit markers."""
    if force:
        logging.info(f"Force flag - will reprocess {subject}")
        return False, ""

    subject_state = _read_project_subject_state(
        project_json_path, subject, marker_namespace=marker_namespace
    )
    if subject_state and (
        subject_state.get("finished") or subject_state.get("status") == "finished"
    ):
        logging.info(f"Subject '{subject}' already processed (project marker found)")
        return True, "project-marker"

    # Check success marker file
    success_markers = _get_success_marker_paths(
        subject, common, marker_namespace=marker_namespace
    )
    marker_exists = next((p for p in success_markers if os.path.exists(p)), None)
    if marker_exists:
        logging.info(
            f"Subject '{subject}' already processed (success marker found: {marker_exists})"
        )
        return True, "success-marker"

    # In project mode, rely only on explicit markers to avoid false positives from
    # partial output folders/files left by interrupted runs.
    if project_json_path:
        return False, ""

    # Check configured output pattern
    pattern = app.get("output_check", {}).get("pattern", "")
    pattern_matches = []
    if pattern:
        subject_raw = str(subject).strip()
        subject_no_prefix = (
            subject_raw[4:] if subject_raw.startswith("sub-") else subject_raw
        )
        subject_with_prefix = f"sub-{subject_no_prefix}"

        check_dir = os.path.join(
            common["output_folder"], app["output_check"].get("directory", "")
        )
        pattern_variants = {
            pattern.replace("{subject}", subject_raw),
            pattern.replace("{subject}", subject_no_prefix),
            pattern.replace("{subject}", subject_with_prefix),
        }
        for pattern_variant in pattern_variants:
            full_pattern = os.path.join(check_dir, pattern_variant)
            pattern_matches.extend(glob.glob(full_pattern))

        pattern_matches = list(set(pattern_matches))
        if pattern_matches:
            logging.info(
                f"Subject '{subject}' already processed (output pattern matched)"
            )
            return True, "pattern"

    return False, ""


def _create_success_marker(subject, common, marker_namespace=None):
    """Create a success marker file for a subject."""
    marker_dir = os.path.join(common["output_folder"], ".bids_app_runner")
    os.makedirs(marker_dir, exist_ok=True)

    if marker_namespace:
        marker_file = os.path.join(
            marker_dir, f"{marker_namespace}__{subject}_success.txt"
        )
    else:
        marker_file = os.path.join(marker_dir, f"{subject}_success.txt")
    try:
        with open(marker_file, "w") as f:
            f.write(f"Subject {subject} processed successfully\n")
            f.write(f"Timestamp: {datetime.now().isoformat()}\n")
            f.write("Runner version: PRISM 3.0.0\n")
        return True
    except Exception as e:
        logging.warning(f"Could not create success marker for {subject}: {e}")
        return False


def _wait_for_output_detection(
    subject, common, app, max_wait_seconds=90, interval_seconds=5
):
    """Wait briefly for outputs to appear after container exits successfully."""
    deadline = time.time() + max_wait_seconds

    while True:
        output_exists = _check_generic_output_exists(subject, common)

        if not output_exists:
            pattern = app.get("output_check", {}).get("pattern", "")
            if pattern:
                subject_raw = str(subject).strip()
                subject_no_prefix = (
                    subject_raw[4:] if subject_raw.startswith("sub-") else subject_raw
                )
                subject_with_prefix = f"sub-{subject_no_prefix}"

                check_dir = os.path.join(
                    common["output_folder"],
                    app["output_check"].get("directory", ""),
                )
                pattern_variants = {
                    pattern.replace("{subject}", subject_raw),
                    pattern.replace("{subject}", subject_no_prefix),
                    pattern.replace("{subject}", subject_with_prefix),
                }
                for pattern_variant in pattern_variants:
                    full_pattern = os.path.join(check_dir, pattern_variant)
                    if glob.glob(full_pattern):
                        output_exists = True
                        break

        if output_exists:
            return True

        if time.time() >= deadline:
            return False

        time.sleep(interval_seconds)


def _process_subject(
    subject,
    common,
    app,
    dry_run=False,
    force=False,
    debug=False,
    project_json_path=None,
    marker_namespace=None,
):
    """Process a single subject with comprehensive error handling."""
    logging.info(f"Starting processing for subject: {subject}")

    # Check if input/output is a DataLad dataset
    is_input_datalad = prism_datalad.is_datalad_dataset(common["bids_folder"])
    is_output_datalad = prism_datalad.is_datalad_dataset(common["output_folder"])

    # Create temporary directory
    tmp_dir = os.path.join(common["tmp_folder"], subject)
    analysis_level = (
        str(app.get("analysis_level", "participant")).strip() or "participant"
    )

    # Create debug log directory if in debug mode
    debug_log_dir = None
    if debug:
        debug_log_dir = os.path.join(common.get("log_dir", "logs"), "container_logs")
        os.makedirs(debug_log_dir, exist_ok=True)

    try:
        os.makedirs(tmp_dir, exist_ok=True)

        # Check if already processed
        already_processed, skip_reason = _subject_processed(
            subject,
            common,
            app,
            force,
            project_json_path=project_json_path,
            marker_namespace=marker_namespace,
        )
        if already_processed:
            try:
                shutil.rmtree(tmp_dir)
            except OSError:
                pass
            return True, f"skipped-{skip_reason or 'marker'}"

        # Get subject data if DataLad dataset (participant mode only)
        if is_input_datalad and analysis_level == "participant":
            prism_datalad.get_subject_data(common["bids_folder"], subject, dry_run)

        # Build container command
        engine = common.get("container_engine", "apptainer")
        container_ref = common.get("container", "")
        bids_mount_source = common["bids_folder"]

        # QSIPrep compatibility translation layer:
        # create subject-scoped runtime BIDS view and apply IntendedFor fixes there,
        # leaving raw dataset unchanged.
        if not dry_run and _is_qsiprep_container(container_ref):
            bids_mount_source = _prepare_qsiprep_runtime_bids_view(
                common["bids_folder"], tmp_dir, subject
            )
            fixed_count = _fix_bids_uri_intendedfor_for_subject(
                bids_mount_source, subject
            )
            if fixed_count > 0:
                logging.info(
                    f"Normalized IntendedFor bids:: URIs in runtime BIDS view ({fixed_count} fmap JSON file(s)) for {subject}."
                )

        execution_adapter = _infer_execution_adapter(common, app)
        fastsurfer_mode = execution_adapter == "fastsurfer-cross"
        if fastsurfer_mode and analysis_level != "participant":
            logging.error(
                "FastSurfer adapter supports participant-level runs only. "
                "Set analysis_level to 'participant'."
            )
            return False, "failed"

        if engine == "docker":
            base_cmd = ["docker", "run", "--rm"]

            # Apple Silicon support
            if platform.system() == "Darwin" and platform.machine() == "arm64":
                logging.info("Apple Silicon detected - adding platform flag")
                base_cmd.extend(["--platform", "linux/amd64"])

            base_cmd.extend(["-e", "TEMPLATEFLOW_HOME=/templateflow"])

            for mnt in _build_common_mounts(common, tmp_dir, bids_mount_source):
                base_cmd.extend(["-v", mnt])

            for mount in app.get("mounts", []):
                if mount.get("source") and mount.get("target"):
                    base_cmd.extend(["-v", f"{mount['source']}:{mount['target']}"])

            base_cmd.append(common["container"])
        else:
            # Apptainer/Singularity
            action = "exec" if fastsurfer_mode else "run"
            base_cmd = ["apptainer", action]

            if app.get("apptainer_args"):
                safe_args = _sanitize_apptainer_args(app["apptainer_args"])
                if fastsurfer_mode and "--nv" not in safe_args:
                    safe_args.append("--nv")
                base_cmd.extend(safe_args)
            else:
                base_cmd.append("--containall")
                if fastsurfer_mode:
                    base_cmd.append("--nv")

            for mnt in _build_common_mounts(common, tmp_dir, bids_mount_source):
                base_cmd.extend(["-B", mnt])

            for mount in app.get("mounts", []):
                if mount.get("source") and mount.get("target"):
                    base_cmd.extend(["-B", f"{mount['source']}:{mount['target']}"])

            base_cmd.extend(["--env", "TEMPLATEFLOW_HOME=/templateflow"])
            base_cmd.append(common["container"])

        commands = []
        if fastsurfer_mode:
            fs_inputs = _discover_fastsurfer_subject_inputs(bids_mount_source, subject)
            if not fs_inputs:
                if dry_run:
                    placeholder_subject = _normalize_subject_id(subject) or "sub-example"
                    fs_inputs = [
                        {
                            "sid": placeholder_subject,
                            "t1_relpath": f"{placeholder_subject}/anat/{placeholder_subject}_T1w.nii.gz",
                        }
                    ]
                    logging.info(
                        "FastSurfer adapter dry-run: no T1w found for %s; using placeholder input path.",
                        subject,
                    )
                else:
                    logging.error(
                        "FastSurfer adapter: no T1w images found for %s in %s",
                        subject,
                        bids_mount_source,
                    )
                    return False, "failed"

            fs_options = _prepare_fastsurfer_options(app.get("options", []))
            fs_license_file = common.get("fs_license_file")

            if len(fs_inputs) > 1:
                logging.info(
                    "FastSurfer adapter: discovered %d T1w input(s) for %s",
                    len(fs_inputs),
                    subject,
                )

            for fs_input in fs_inputs:
                cmd = list(base_cmd)
                cmd.extend(
                    [
                        "/fastsurfer/run_fastsurfer.sh",
                        "--t1",
                        f"/bids/{fs_input['t1_relpath']}",
                        "--sid",
                        fs_input["sid"],
                        "--sd",
                        "/output",
                    ]
                )

                if fs_license_file:
                    cmd.extend(["--fs_license", "/fs/license.txt"])

                if fs_options:
                    cmd.extend(fs_options)

                commands.append((cmd, fs_input["sid"]))
        else:
            cmd = list(base_cmd)
            cmd.extend(["/bids", "/output", analysis_level])

            app_options = _ensure_mriqc_no_sub_option(
                container_ref, app.get("options", [])
            )
            if app_options:
                cmd.extend(app_options)

            # Ensure FreeSurfer license is passed if provided
            fs_license_file = common.get("fs_license_file")
            if fs_license_file:
                if "--fs-license-file" not in cmd and not any(
                    a.startswith("--fs-license-file=") for a in cmd
                ):
                    cmd.extend(["--fs-license-file", "/fs/license.txt"])

            if analysis_level == "participant":
                cmd.extend(["--participant-label", subject.replace("sub-", "")])
            else:
                logging.info(
                    "Group analysis selected: skipping --participant-label for subject %s",
                    subject,
                )

            cmd.extend(["-w", "/tmp"])
            commands.append((cmd, subject))

        for cmd, run_id in commands:
            _run_container(
                cmd,
                dry_run=dry_run,
                debug=debug,
                subject=run_id,
                log_dir=debug_log_dir,
            )

        if not dry_run:
            container_success = True

            if container_success:
                logging.info(f"Container execution successful for {subject}")

                # Save results if DataLad output dataset (participant mode only)
                if is_output_datalad and analysis_level == "participant":
                    prism_datalad.save_results(
                        common["output_folder"], subject, dry_run
                    )

                # Group mode runs once across all participants and should not be
                # gated on participant-style output detection.
                if analysis_level == "group":
                    _create_success_marker(
                        subject, common, marker_namespace=marker_namespace
                    )
                    logging.info("Group analysis completed successfully")

                    try:
                        shutil.rmtree(tmp_dir)
                    except OSError:
                        pass
                    return True, "finished"

                # QSIRecon writes large outputs and may need extra time to flush
                # on network filesystems; use a longer wait for it.
                max_wait = 300 if _is_qsirecon_container(container_ref) else 90

                output_exists = _wait_for_output_detection(
                    subject,
                    common,
                    app,
                    max_wait_seconds=max_wait,
                    interval_seconds=5,
                )

                if output_exists:
                    _create_success_marker(
                        subject, common, marker_namespace=marker_namespace
                    )
                    logging.info(f"Subject {subject} completed successfully")

                    try:
                        shutil.rmtree(tmp_dir)
                    except OSError:
                        pass
                    return True, "finished"
                else:
                    logging.warning(
                        f"Container completed for {subject} but no output detected after waiting {max_wait}s"
                    )
                    return False, "failed"
            else:
                logging.error(f"Container execution failed for {subject}")
                return False, "failed"

        return True, "dry-run"
    except Exception as e:
        logging.error(f"Error processing subject {subject}: {e}")
        return False, "failed"


# ============================================================================
# Main Execution Function
# ============================================================================


def execute_local(config: Dict[str, Any], args: Namespace) -> bool:
    """Execute BIDS app in local/cluster mode.

    Args:
        config: Configuration dictionary
        args: Parsed command-line arguments

    Returns:
        True if execution successful, False otherwise
    """
    logging.info("=" * 60)
    logging.info("LOCAL/CLUSTER EXECUTION MODE")
    logging.info("=" * 60)

    common = config.get("common", {})
    app = config.get("app", {})
    marker_namespace = _derive_marker_namespace(config)
    project_json_path = _resolve_project_json_path(getattr(args, "config", None))

    if project_json_path:
        logging.info(f"Project marker tracking enabled: {project_json_path}")
        logging.info(f"Project marker namespace: {marker_namespace}")

    # Create output directory if it doesn't exist
    output_folder = common.get("output_folder")
    if output_folder:
        os.makedirs(output_folder, exist_ok=True)
        logging.info(f"Ensured output directory exists: {output_folder}")

    start_time = time.time()

    # Get subjects
    if args.subjects:
        expanded = []
        for raw in args.subjects:
            expanded.extend([s for s in re.split(r"[\s,]+", str(raw).strip()) if s])

        # Preserve order while removing accidental duplicates.
        seen = set()
        subjects = []
        for s in expanded:
            subj = s if s.startswith("sub-") else f"sub-{s}"
            if subj not in seen:
                seen.add(subj)
                subjects.append(subj)

        logging.info(f"Processing specified subjects: {subjects}")
    else:
        bids_folder = common.get("bids_folder")
        subjects = get_subjects_from_bids(bids_folder, args.dry_run)

        if not subjects and not args.dry_run:
            logging.error(f"No subjects found in BIDS folder: {bids_folder}")
            return False
        elif not subjects and args.dry_run:
            logging.info("Dry-run mode: using placeholder subject")
            subjects = ["sub-example"]
        else:
            logging.info(f"Auto-discovered {len(subjects)} subjects")

    analysis_level = str(app.get("analysis_level", "participant")).strip().lower()
    if not analysis_level:
        analysis_level = "participant"

    if analysis_level == "group":
        if args.subjects:
            logging.info(
                "Group analysis selected: ignoring subject filter (--subjects)"
            )
        subjects = ["group"]
        logging.info("Group analysis selected: running a single group-level execution")

    # Handle pilot mode
    pilot = args.pilot if hasattr(args, "pilot") else False
    if analysis_level == "group" and pilot:
        logging.info("Group analysis selected: ignoring pilot mode")
        pilot = False

    if pilot:
        subject = random.choice(subjects)
        subjects = [subject]
        logging.info(f"Pilot mode: processing only {subject}")

    # Determine number of parallel jobs
    jobs = common.get("jobs", multiprocessing.cpu_count())
    if pilot:
        jobs = 1
        logging.info("Pilot mode: forcing jobs=1")
    if analysis_level == "group" and jobs != 1:
        jobs = 1
        logging.info("Group analysis selected: forcing jobs=1")

    # Handle debug mode
    debug = args.debug if hasattr(args, "debug") else False
    force = args.force if hasattr(args, "force") else False
    dry_run = args.dry_run if hasattr(args, "dry_run") else False
    clean_success_markers = (
        args.clean_success_markers if hasattr(args, "clean_success_markers") else False
    )
    start_delay_sec = args.start_delay_sec if hasattr(args, "start_delay_sec") else 0.0

    try:
        start_delay_sec = float(start_delay_sec or 0.0)
    except (TypeError, ValueError):
        start_delay_sec = 0.0

    if start_delay_sec < 0:
        logging.warning("Negative --start-delay-sec provided; using 0")
        start_delay_sec = 0.0

    if debug and jobs > 1:
        logging.warning("Debug mode not supported with parallel processing")
        logging.warning("Running in serial mode (jobs=1)")
        jobs = 1

    logging.info(f"Processing {len(subjects)} subjects with {jobs} parallel jobs")

    if start_delay_sec > 0 and not dry_run and len(subjects) > 1:
        logging.info(
            f"Staggered launches enabled: waiting {start_delay_sec:.1f}s between subject starts"
        )

    if debug:
        logging.info("Debug mode enabled - detailed container logs will be saved")

    if clean_success_markers:
        if dry_run:
            logging.info("Dry-run mode: skipping marker cleanup request")
        else:
            _clear_success_markers(common, project_json_path=project_json_path)

    def _normalize_subject_result(result):
        if isinstance(result, tuple) and len(result) == 2:
            return bool(result[0]), str(result[1] or "")
        return bool(result), "finished" if result else "failed"

    def _record_project_status(subject, success, status):
        if not project_json_path or dry_run:
            return

        if success and status == "finished":
            _write_project_subject_state(
                project_json_path,
                subject,
                "finished",
                reason="container-success",
                marker_namespace=marker_namespace,
            )
        elif success and status == "skipped-success-marker":
            _write_project_subject_state(
                project_json_path,
                subject,
                "finished",
                reason="success-marker-skip",
                marker_namespace=marker_namespace,
            )
        elif not success:
            _write_project_subject_state(
                project_json_path,
                subject,
                "failed",
                reason="container-failed-or-incomplete",
                marker_namespace=marker_namespace,
            )

    # Process subjects
    processed_subjects = []
    failed_subjects = []

    if dry_run:
        logging.info("DRY RUN MODE - No actual processing will occur")
        for subject in subjects:
            success_raw = _process_subject(
                subject,
                common,
                app,
                dry_run=True,
                force=force,
                debug=debug,
                project_json_path=project_json_path,
                marker_namespace=marker_namespace,
            )
            success, status = _normalize_subject_result(success_raw)
            if success:
                processed_subjects.append(subject)
            else:
                failed_subjects.append(subject)
            _record_project_status(subject, success, status)
    elif jobs == 1:
        # Serial processing (supports debug mode)
        for idx, subject in enumerate(subjects):
            if idx > 0 and start_delay_sec > 0:
                logging.info(
                    f"Waiting {start_delay_sec:.1f}s before launching next subject ({subject})"
                )
                time.sleep(start_delay_sec)

            success_raw = _process_subject(
                subject,
                common,
                app,
                False,
                force,
                debug,
                project_json_path,
                marker_namespace,
            )
            success, status = _normalize_subject_result(success_raw)
            if success:
                processed_subjects.append(subject)
            else:
                failed_subjects.append(subject)
            _record_project_status(subject, success, status)
    else:
        # Parallel processing
        with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as executor:
            future_to_subject = {}
            for idx, subject in enumerate(subjects):
                if idx > 0 and start_delay_sec > 0:
                    logging.info(
                        f"Waiting {start_delay_sec:.1f}s before queueing next subject ({subject})"
                    )
                    time.sleep(start_delay_sec)

                future = executor.submit(
                    _process_subject,
                    subject,
                    common,
                    app,
                    False,
                    force,
                    False,
                    project_json_path,
                    marker_namespace,
                )
                future_to_subject[future] = subject

            for future in concurrent.futures.as_completed(future_to_subject):
                subject = future_to_subject[future]

                try:
                    success_raw = future.result()
                    success, status = _normalize_subject_result(success_raw)
                    if success:
                        processed_subjects.append(subject)
                    else:
                        failed_subjects.append(subject)
                    _record_project_status(subject, success, status)
                except Exception as e:
                    logging.error(f"Exception processing {subject}: {e}")
                    failed_subjects.append(subject)
                    _record_project_status(subject, False, "failed")

    # Print summary
    end_time = time.time()
    print_summary(processed_subjects, failed_subjects, end_time - start_time)

    return len(failed_subjects) == 0
