#!/usr/bin/env python3
import re
import os
import platform
import sys
import json
import copy
import subprocess
import time
import signal
import gzip
import struct
import smtplib
import ssl
from email.message import EmailMessage

# Check for GUI dependencies
try:
    import requests
    from flask import Flask, render_template, request, jsonify
    from waitress import serve
except ImportError as e:
    print(f"\n[ERROR] Missing GUI dependency: {e.name}")
    print("The GUI requires additional packages not included in the core installation.")
    print("\nPlease run the installer with the full flag to install them:")
    print("  ./scripts/install.sh --full")
    print("\nAlternatively, install them manually:")
    print("  pip install flask waitress requests")
    sys.exit(1)

import shutil
import webbrowser
import threading
import socket
from typing import Any
from datetime import datetime
from pathlib import Path

from gui.gui_auth_routes import register_auth_handlers
from gui.gui_cohort_routes import register_cohort_routes
from gui.gui_misc_routes import register_misc_routes
from gui.gui_project_routes import register_project_config_handlers
from gui.gui_projects import ProjectStore
from gui.gui_run_routes import register_run_routes
from gui.gui_system_routes import register_system_routes
from gui.gui_utility_routes import (
    register_utility_routes,
    REMOTE_DATASET_SSH_HOST,
    REMOTE_DATASET_BASE_PATH,
)
from gui.gui_security import (
    is_loopback_host as _is_loopback_host,
    load_gui_password_config,
    load_or_create_secret_key as _load_or_create_secret_key,
    normalize_json_filename as _normalize_json_filename,
    normalize_project_id as _normalize_project_id,
    request_is_loopback as _request_is_loopback,
    resolve_config_storage_dir as _resolve_config_storage_dir,
    resolve_named_config_path as _resolve_named_config_path,
    resolve_project_dir as _resolve_project_dir,
)
from version import __version__

# Add scripts directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from check_app_output import BIDSOutputValidator

try:
    import hpc_datalad_runner  # noqa: F401

    HPC_DATALAD_AVAILABLE = True
except ImportError:
    HPC_DATALAD_AVAILABLE = False
    print("[WARNING] HPC DataLad runner not available - HPC mode disabled")


def _fix_system_path():
    """Ensure common paths are in PATH, especially when running as a bundled app on macOS."""
    extra_paths = [
        "/usr/local/bin",
        "/opt/homebrew/bin",
        "/opt/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    current_path = os.environ.get("PATH", "").split(os.pathsep)
    path_changed = False
    for path_entry in extra_paths:
        if path_entry not in current_path and os.path.exists(path_entry):
            current_path.append(path_entry)
            path_changed = True

    if path_changed:
        os.environ["PATH"] = os.pathsep.join(current_path)
        print(
            f"[GUI] Updated PATH to include common locations: {os.environ['PATH']}",
            flush=True,
        )


_fix_system_path()

if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS"))
    app = Flask(
        __name__,
        template_folder=str(BUNDLE_DIR / "templates"),
        static_folder=str(BUNDLE_DIR / "static"),
    )
else:
    BUNDLE_DIR = Path(__file__).resolve().parent
    app = Flask(__name__)

app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["APP_VERSION"] = __version__

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = BUNDLE_DIR


def _get_data_dir():
    try:
        test_file = BASE_DIR / ".write_test"
        test_file.touch()
        test_file.unlink()
        return BASE_DIR
    except (PermissionError, OSError):
        if platform.system() == "Darwin":
            data_dir = (
                Path.home() / "Library" / "Application Support" / "BIDSAppsRunner"
            )
        else:
            data_dir = Path.home() / ".bids_apps_runner"
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir


DATA_DIR = _get_data_dir()
LOG_DIR = DATA_DIR / "logs"
PROJECTS_DIR = DATA_DIR / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
GLOBAL_SETTINGS_DIR = DATA_DIR / "configs"
GLOBAL_SETTINGS_PATH = GLOBAL_SETTINGS_DIR / "global_settings.json"
GUI_HOST = (os.environ.get("PRISM_GUI_HOST") or "127.0.0.1").strip() or "127.0.0.1"
GUI_AUTH_TOKEN = (os.environ.get("PRISM_GUI_AUTH_TOKEN") or "").strip()
GUI_AUTH_HEADER = "X-Prism-Auth"
CSRF_HEADER = "X-CSRF-Token"
GUI_LOGIN_CONFIG = load_gui_password_config()
GUI_LOGIN_ENABLED = bool(GUI_LOGIN_CONFIG.get("enabled"))
GUI_LOGIN_PASSWORD_HASH = str(GUI_LOGIN_CONFIG.get("password_hash") or "")
GUI_BOOTSTRAP_PASSWORD = GUI_LOGIN_CONFIG.get("bootstrap_password")
PUBLIC_ENDPOINTS = {"/health", "/login"}


def _current_machine_id():
    """Return a stable machine identifier for per-host settings overrides."""
    system_name = (platform.system() or "unknown").strip().lower()
    host_name = (platform.node() or "unknown").strip().lower()
    machine_id = f"{system_name}:{host_name}"
    return re.sub(r"\s+", "_", machine_id)


def _default_machine_settings():
    system_name = (platform.system() or "").strip().lower()
    preferred_engine = "apptainer"
    if system_name in {"darwin", "windows"}:
        preferred_engine = "docker"

    return {
        "preferred_container_engine": preferred_engine,
        "allow_apptainer": True,
        "allow_docker": True,
        "default_apptainer_folder": "",
        "default_apptainer_container": "",
        "default_tmp_folder": "",
        "default_docker_repo": "nipreps/fmriprep",
        "default_docker_tag": "latest",
        "default_jobs": 1,
        # rsync/scp destination (user@host:/path) on the DataLad server where
        # the dedicated container-build repo pushes finished .sif files --
        # not a local path like the other apptainer_* settings above.
        "remote_container_path": "",
    }


def _default_global_settings_doc():
    return {
        "version": 1,
        "default": _default_machine_settings(),
        "machines": {},
    }


def _sanitize_machine_settings(raw):
    """Normalize user-provided machine settings and drop unsupported keys."""
    if not isinstance(raw, dict):
        return {}

    cleaned = {}

    preferred = str(raw.get("preferred_container_engine", "")).strip().lower()
    if preferred in {"auto", "apptainer", "docker"}:
        cleaned["preferred_container_engine"] = preferred

    for key in ("allow_apptainer", "allow_docker"):
        if key in raw:
            cleaned[key] = bool(raw.get(key))

    for key in (
        "default_apptainer_folder",
        "default_apptainer_container",
        "default_tmp_folder",
        "default_templateflow_dir",
        "default_docker_repo",
        "default_docker_tag",
        "remote_container_path",
    ):
        if key in raw:
            cleaned[key] = str(raw.get(key) or "").strip()

    if "default_jobs" in raw:
        try:
            jobs = int(raw.get("default_jobs"))
        except (TypeError, ValueError):
            jobs = 1
        cleaned["default_jobs"] = max(1, min(jobs, 256))

    return cleaned


def _read_global_settings_doc():
    """Load global settings file and coerce to known schema."""
    doc = _default_global_settings_doc()
    if not GLOBAL_SETTINGS_PATH.exists():
        return doc

    try:
        with open(GLOBAL_SETTINGS_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as exc:
        print(f"[GUI] Failed to read global settings {GLOBAL_SETTINGS_PATH}: {exc}")
        return doc

    if not isinstance(raw, dict):
        return doc

    default_raw = raw.get("default")
    if isinstance(default_raw, dict):
        doc["default"].update(_sanitize_machine_settings(default_raw))

    machines_raw = raw.get("machines")
    if isinstance(machines_raw, dict):
        cleaned_machines = {}
        for machine_id, value in machines_raw.items():
            if not isinstance(machine_id, str) or not machine_id.strip():
                continue
            cleaned = _sanitize_machine_settings(value)
            if cleaned:
                cleaned_machines[machine_id.strip().lower()] = cleaned
        doc["machines"] = cleaned_machines

    return doc


def _request_auth_token() -> str:
    header_token = str(request.headers.get(GUI_AUTH_HEADER) or "").strip()
    if header_token:
        return header_token

    auth_header = str(request.headers.get("Authorization") or "").strip()
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()

    return ""


def _find_available_port(host, start_port=5000, max_tries=50):
    bind_host = str(host or "127.0.0.1").strip() or "127.0.0.1"
    family = (
        socket.AF_INET6
        if ":" in bind_host and bind_host != "localhost"
        else socket.AF_INET
    )

    for offset in range(max_tries):
        port = start_port + offset
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                if family == socket.AF_INET6:
                    sock.bind((bind_host, port, 0, 0))
                else:
                    sock.bind((bind_host, port))
                return port
            except OSError:
                continue

    raise RuntimeError(f"No available port found for {host} after {max_tries} attempts")


app.secret_key = _load_or_create_secret_key(DATA_DIR)

register_auth_handlers(
    app,
    login_enabled=lambda: GUI_LOGIN_ENABLED,
    login_password_hash=lambda: GUI_LOGIN_PASSWORD_HASH,
    auth_token=lambda: GUI_AUTH_TOKEN,
    auth_header=GUI_AUTH_HEADER,
    csrf_header=CSRF_HEADER,
    request_auth_token=_request_auth_token,
    request_is_loopback=_request_is_loopback,
    public_paths=PUBLIC_ENDPOINTS,
    index_endpoint="index",
    login_template="login.html",
)


def _write_global_settings_doc(doc):
    GLOBAL_SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(GLOBAL_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)


def _resolve_container_engine(
    preferred_engine,
    allow_apptainer=True,
    allow_docker=True,
    dependencies=None,
):
    """Resolve effective container engine for this host from preferences + availability."""
    deps = dependencies or check_system_dependencies()
    apptainer_available = bool(deps.get("apptainer") or deps.get("singularity"))
    docker_available = bool(deps.get("docker"))

    preferred = str(preferred_engine or "auto").strip().lower()
    if preferred not in {"auto", "apptainer", "docker"}:
        preferred = "auto"

    ordered = ["apptainer", "docker"] if preferred == "auto" else [preferred]
    if preferred != "auto":
        ordered.extend([e for e in ("apptainer", "docker") if e != preferred])

    for engine in ordered:
        if engine == "apptainer" and allow_apptainer and apptainer_available:
            return "apptainer"
        if engine == "docker" and allow_docker and docker_available:
            return "docker"

    # If nothing is available, retain a deterministic fallback by policy.
    if allow_apptainer:
        return "apptainer"
    if allow_docker:
        return "docker"
    return "apptainer"


def _get_effective_machine_settings(machine_id=None, dependencies=None):
    machine_key = (machine_id or _current_machine_id()).strip().lower()
    defaults = _default_machine_settings()
    doc = _read_global_settings_doc()

    effective = copy.deepcopy(defaults)
    effective.update(_sanitize_machine_settings(doc.get("default", {})))
    machine_overrides = doc.get("machines", {}).get(machine_key, {})
    effective.update(_sanitize_machine_settings(machine_overrides))

    effective["resolved_container_engine"] = _resolve_container_engine(
        effective.get("preferred_container_engine", "auto"),
        allow_apptainer=bool(effective.get("allow_apptainer", True)),
        allow_docker=bool(effective.get("allow_docker", True)),
        dependencies=dependencies,
    )
    return effective


def _sanitize_pipeline_id(value):
    """Normalize pipeline identifiers for stable storage and lookup."""
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "default"


def _normalize_project_pipelines(config):
    """Return (pipelines, active_pipeline_id) from mixed legacy/new config shapes."""
    if not isinstance(config, dict):
        return {}, ""

    pipelines = {}
    raw_pipelines = config.get("pipelines")
    if isinstance(raw_pipelines, dict):
        for raw_id, raw_entry in raw_pipelines.items():
            if not isinstance(raw_entry, dict):
                continue

            pipeline_id = _sanitize_pipeline_id(raw_id)
            entry_name = str(raw_entry.get("name") or raw_id or pipeline_id).strip()
            entry_desc = str(raw_entry.get("description") or "").strip()

            entry_common = raw_entry.get("common")
            entry_app = raw_entry.get("app")

            # Backward compatibility for flat pipeline entries that are app-like objects.
            if not isinstance(entry_app, dict) and any(
                key in raw_entry for key in ("analysis_level", "options", "mounts")
            ):
                entry_app = raw_entry

            if not isinstance(entry_common, dict):
                entry_common = {}
            if not isinstance(entry_app, dict):
                entry_app = {}

            pipelines[pipeline_id] = {
                "name": entry_name or pipeline_id,
                "description": entry_desc,
                "common": copy.deepcopy(entry_common),
                "app": copy.deepcopy(entry_app),
            }

    # Legacy fallback: one implicit default pipeline from config.common + config.app.
    if not pipelines:
        legacy_common = config.get("common")
        legacy_app = config.get("app")
        if isinstance(legacy_common, dict) and isinstance(legacy_app, dict):
            pipelines["default"] = {
                "name": "Default Pipeline",
                "description": "",
                "common": copy.deepcopy(legacy_common),
                "app": copy.deepcopy(legacy_app),
            }

    preferred_active = _sanitize_pipeline_id(config.get("active_pipeline"))
    if preferred_active and preferred_active in pipelines:
        active_pipeline = preferred_active
    elif pipelines:
        active_pipeline = next(iter(pipelines.keys()))
    else:
        active_pipeline = ""

    return pipelines, active_pipeline


def _coerce_project_config_shape(config):
    """Normalize config for persistence while retaining legacy common/app fields."""
    if not isinstance(config, dict):
        config = {}

    normalized = copy.deepcopy(config)
    pipelines, active_pipeline = _normalize_project_pipelines(normalized)
    if not pipelines:
        normalized.setdefault("common", {})
        normalized.setdefault(
            "app", {"analysis_level": "participant", "options": [], "mounts": []}
        )
        return normalized

    selected = pipelines.get(active_pipeline)
    if selected is None:
        active_pipeline = next(iter(pipelines.keys()))
        selected = pipelines.get(active_pipeline, {})

    selected_common = (
        selected.get("common") if isinstance(selected.get("common"), dict) else {}
    )
    selected_app = selected.get("app") if isinstance(selected.get("app"), dict) else {}

    base_common = (
        normalized.get("common") if isinstance(normalized.get("common"), dict) else {}
    )
    merged_common = copy.deepcopy(base_common)
    merged_common.update(copy.deepcopy(selected_common))

    base_app = normalized.get("app") if isinstance(normalized.get("app"), dict) else {}
    merged_app = copy.deepcopy(base_app)
    merged_app.update(copy.deepcopy(selected_app))

    normalized["common"] = merged_common
    normalized["app"] = merged_app
    normalized["pipelines"] = pipelines
    normalized["active_pipeline"] = active_pipeline
    return normalized


ProjectManager = ProjectStore(
    PROJECTS_DIR,
    machine_settings_provider=_get_effective_machine_settings,
    config_normalizer=_coerce_project_config_shape,
    project_dir_resolver=lambda project_id: _resolve_project_dir(
        PROJECTS_DIR, project_id
    ),
    timestamp_factory=lambda: datetime.now().isoformat(),
)


def _validate_project_json_shape(project_json):
    """Validate minimal shape for externally loaded project.json files."""
    if not isinstance(project_json, dict):
        return "Invalid project.json: top-level JSON value must be an object"

    required_top_level = ["id", "name", "config"]
    missing_top_level = [k for k in required_top_level if k not in project_json]
    if missing_top_level:
        return "Invalid project.json: missing top-level key(s): " + ", ".join(
            missing_top_level
        )

    project_id = project_json.get("id")
    if not isinstance(project_id, str) or not project_id.strip():
        return "Invalid project.json: id must be a non-empty string"

    project_name = project_json.get("name")
    if not isinstance(project_name, str) or not project_name.strip():
        return "Invalid project.json: name must be a non-empty string"

    config = project_json.get("config")
    if not isinstance(config, dict):
        return "Invalid project.json: config must be an object"

    has_common = isinstance(config.get("common"), dict)
    has_legacy_app = isinstance(config.get("app"), dict)
    has_pipelines = isinstance(config.get("pipelines"), dict) and bool(
        config.get("pipelines")
    )

    if not has_common and not has_pipelines:
        return "Invalid project.json: config.common must be an object"

    if not has_legacy_app and not has_pipelines:
        return (
            "Invalid project.json: config must include config.app or "
            "non-empty config.pipelines"
        )

    if has_pipelines:
        for pipeline_id, entry in config.get("pipelines", {}).items():
            if not isinstance(entry, dict):
                return (
                    "Invalid project.json: each config.pipelines entry must be an object "
                    f"(invalid: {pipeline_id})"
                )
            if not isinstance(entry.get("app"), dict):
                return (
                    "Invalid project.json: each pipeline must include app object "
                    f"(invalid: {pipeline_id})"
                )
            if "common" in entry and not isinstance(entry.get("common"), dict):
                return (
                    "Invalid project.json: pipeline common section must be an object "
                    f"(invalid: {pipeline_id})"
                )

    return None


def _find_python_interpreter():
    """Find a Python 3 interpreter in the current environment."""
    if not getattr(sys, "frozen", False):
        return sys.executable
    # In frozen mode, look for common python names in PATH
    for cmd in ["python3", "python", "python.exe"]:
        if shutil.which(cmd):
            return cmd
    return "python3"  # Fallback


PYTHON_EXE = _find_python_interpreter()


def check_system_dependencies():
    """Check for availability of docker, apptainer, and singularity."""
    docker_installed = shutil.which("docker") is not None
    docker_running = False
    if docker_installed:
        try:
            # Check if daemon is responsive
            subprocess.run(
                ["docker", "info"], capture_output=True, timeout=5, check=True
            )
            docker_running = True
        except (
            subprocess.SubprocessError,
            FileNotFoundError,
            subprocess.TimeoutExpired,
        ) as e:
            print(f"[GUI] Docker check failed: {e}")
            docker_running = False

    return {
        "docker": docker_installed,
        "docker_running": docker_running,
        "apptainer": shutil.which("apptainer") is not None,
        "singularity": shutil.which("singularity") is not None,
        "datalad": shutil.which("datalad") is not None,
        "slurm": shutil.which("sbatch") is not None,
    }


SILENT_ENDPOINTS = {
    "/get_log",
    "/pilot_estimator_status",
}


@app.before_request
def log_request_info():
    if request.path not in SILENT_ENDPOINTS:
        print(
            f"[GUI] {request.method} {request.path} from {request.remote_addr}",
            flush=True,
        )


# Common BIDS Apps mapping to Docker Hub repos
APP_REPO_MAPPING = {
    "mriqc": "nipreps/mriqc",
    "fmriprep": "nipreps/fmriprep",
    "qsiprep": "pennlinc/qsiprep",
    "qsirecon": "pennlinc/qsirecon",
    "nibabies": "nipreps/nibabies",
    "mritools": "bids/mritools",
    "freesurfer": "freesurfer/freesurfer",
    "synthseg": "freesurfer/synthseg",
}


def _ensure_logs_dir():
    LOG_DIR.mkdir(parents=True, exist_ok=True)



def resolve_config_path(config_path):
    """Resolve config paths across common runtime locations."""
    cfg = Path(os.path.expanduser(str(config_path)))
    if cfg.is_absolute() and cfg.exists():
        return cfg
    if cfg.exists():
        return cfg.resolve()

    candidates = [
        DATA_DIR / cfg,
        BASE_DIR / cfg,
        (BASE_DIR / "scripts") / cfg,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()

    raise FileNotFoundError(f"Config file not found: {config_path}")


def _materialize_runtime_config(config, preferred_pipeline_id=None):
    """Resolve a single runnable config from legacy or multi-pipeline shapes."""
    if not isinstance(config, dict):
        return {}

    pipelines, active_pipeline = _normalize_project_pipelines(config)
    if not pipelines:
        return config

    selected_pipeline = (
        _sanitize_pipeline_id(preferred_pipeline_id)
        if preferred_pipeline_id
        else active_pipeline
    )
    if selected_pipeline not in pipelines:
        selected_pipeline = active_pipeline
    if selected_pipeline not in pipelines and pipelines:
        selected_pipeline = next(iter(pipelines.keys()))

    selected = pipelines.get(selected_pipeline) or {}
    selected_common = (
        selected.get("common") if isinstance(selected.get("common"), dict) else {}
    )
    selected_app = selected.get("app") if isinstance(selected.get("app"), dict) else {}

    base_common = config.get("common") if isinstance(config.get("common"), dict) else {}
    merged_common = copy.deepcopy(base_common)
    merged_common.update(copy.deepcopy(selected_common))

    base_app = config.get("app") if isinstance(config.get("app"), dict) else {}
    merged_app = copy.deepcopy(base_app)
    merged_app.update(copy.deepcopy(selected_app))

    runtime = {}
    for key, value in config.items():
        if key in {"common", "app", "pipelines", "active_pipeline"}:
            continue
        runtime[key] = copy.deepcopy(value)

    runtime["common"] = merged_common
    runtime["app"] = merged_app
    runtime["active_pipeline"] = selected_pipeline
    return runtime


def _extract_runtime_config(cfg, selected_pipeline_id=None):
    """Return runnable config payload from either config.json or project.json."""
    if isinstance(cfg, dict) and isinstance(cfg.get("config"), dict):
        return _materialize_runtime_config(
            cfg["config"], preferred_pipeline_id=selected_pipeline_id
        )
    if isinstance(cfg, dict):
        return _materialize_runtime_config(
            cfg, preferred_pipeline_id=selected_pipeline_id
        )
    return {}


class CohortConfigError(ValueError):
    """Raised when a project's settings can't be used for a SLURM array run."""


def _derive_cohort_config(runtime_cfg, *, project_dir, max_concurrent=50, batch_size=10):
    """Build a cohort_hpc-schema dict (datasets/paths/datalad/hpc/bids_app)
    straight from a project's already-materialized runtime config -- the
    same dict Local and single-job HPC execution already use. This is the
    single point where "what to run" (container, flags, folders) carries
    over unchanged into the SLURM array/datalad-slurm path; only execution
    mechanics (resource requests, shared-storage bookkeeping) are added here.
    """
    common = runtime_cfg.get("common", {})
    app = runtime_cfg.get("app", {})
    hpc = runtime_cfg.get("hpc", {})

    bids_folder = str(common.get("bids_folder") or "").rstrip("/")
    output_folder = str(common.get("output_folder") or "").rstrip("/")
    if not bids_folder:
        raise CohortConfigError("BIDS Dataset Folder is not set on this project.")
    if not output_folder:
        raise CohortConfigError("Output Folder is not set on this project.")
    if str(common.get("container_engine") or "apptainer") == "docker":
        raise CohortConfigError(
            "SLURM array execution only supports Apptainer/Singularity containers, "
            "not Docker. Switch the container engine for this project."
        )
    for label, value in (
        ("partition", hpc.get("partition")),
        ("time", hpc.get("time")),
        ("mem", hpc.get("mem")),
        ("cpus", hpc.get("cpus")),
    ):
        if not value:
            raise CohortConfigError(
                f"HPC setting '{label}' is not set. Fill in Advanced: SLURM "
                "Settings and save before running a SLURM array."
            )

    dataset_id = os.path.basename(bids_folder) or "dataset"
    app_name = str(common.get("pipeline_app_name") or "").strip() or "bids_app"
    # .resolve(): a relative project_dir would silently write a relative
    # log_dir/subject_lists_dir into the generated cohort config. Harmless
    # for the main array (its own bash steps run from REPO_DIR too, so it
    # happens to resolve), but datalad-slurm submits via
    # `datalad -C output_clone ... sbatch`, which changes the *job's* cwd to
    # the output dataset -- a relative timepoint-list path then resolves
    # against output_clone on the compute node instead, failing every array
    # task instantly with a confusing "No such file" deep in a SLURM log.
    cohort_log_dir = Path(project_dir).resolve() / "logs" / "cohort"

    # Custom SBATCH directives (e.g. sbatch_gres: "gpu:1" for GPU-capable
    # apps like QSIPrep/FastSurfer) -- forwarded the same way the single-job
    # HPC path already does (prism_hpc.py), so array/cohort runs actually
    # request the resources the project's HPC settings ask for.
    sbatch_directives = {
        key: value
        for key, value in hpc.items()
        if key.startswith("sbatch_") and value
    }

    return {
        "datasets": [dataset_id],
        "paths": {
            "input_dir": bids_folder,
            "output_dir": output_folder,
            "scratch_dir": common.get("tmp_folder") or "",
            "container": common.get("container") or "",
            "templateflow_dir": common.get("templateflow_dir") or "",
            "fs_license": common.get("fs_license_file") or "",
            "log_dir": str(cohort_log_dir),
            "subject_lists_dir": str(cohort_log_dir / "subject_lists"),
        },
        "datalad": {
            "input_url_template": (
                f"{REMOTE_DATASET_SSH_HOST}:{REMOTE_DATASET_BASE_PATH}/{{dataset_id}}"
            ),
            "output_url_template": (
                f"ssh://{REMOTE_DATASET_SSH_HOST}"
                f"/{REMOTE_DATASET_BASE_PATH.lstrip('/')}"
                f"/{{dataset_id}}/derivatives/{app_name}"
            ),
        },
        "hpc": {
            "partition": hpc.get("partition"),
            "time": hpc.get("time"),
            "mem": hpc.get("mem"),
            "cpus": hpc.get("cpus"),
            "max_concurrent": int(max_concurrent or 50),
            # Cohorts get split into sequential batches of this many
            # subjects, each with its own array+finish job pair, so a
            # slow/failed finish only costs one batch instead of the whole
            # study -- see schedule_one_batch() in submit_bids_cohort.sh.
            # 0 disables batching (today's single-array-per-dataset
            # behavior); unset/None falls back to 10, the same way
            # max_concurrent falls back to 50 above. Lowered from 30 after a
            # 117-subject FreeSurfer cohort (134) sat with nothing pushed to
            # the datalad server for 2+ days: a single slurm-finish covering
            # a whole batch has to hash+commit everything in that batch
            # before anything lands, and a 30-subject batch (or worse, a
            # ~150-subject one with batching off) can run long enough to hit
            # datalad-slurm's known premature-DB-removal bug on interruption
            # (https://github.com/knuedd/datalad-slurm/issues/97). Smaller
            # batches mean more frequent, cheaper finish jobs and a smaller
            # blast radius if one of them fails.
            "batch_size": 10 if batch_size in (None, "") else int(batch_size),
            "modules": hpc.get("modules") or [],
            "environment": hpc.get("environment") or {},
            "notify_email": hpc.get("notify_email") or "",
            **sbatch_directives,
        },
        "bids_app": {
            "app_name": app_name,
            "analysis_level": app.get("analysis_level") or "participant",
            "output_dir_name": os.path.basename(output_folder) or app_name,
            "options": app.get("options") or [],
            # Carried through so the SLURM-array path (hpc_datalad_runner.py)
            # resolves the same app profile catalog entry/overrides as the
            # local/single-job HPC path, e.g. a custom app fork that doesn't
            # support the NiPreps --nprocs/--omp-nthreads/--mem convention.
            "app_profile": app.get("app_profile") or "",
            "app_profile_overrides": app.get("app_profile_overrides") or {},
            # Carried through the same way -- FastSurfer's execution_adapter
            # field (fastsurfer-cross / fastsurfer-bids) is a separate,
            # pre-existing concept from app_profile (see
            # prism_hpc.py/prism_local.py::_infer_execution_adapter); without
            # this the SLURM-array path would silently fall back to the
            # generic apptainer run /bids /output participant convention,
            # which FastSurfer's container entrypoint doesn't understand.
            "execution_adapter": app.get("execution_adapter") or "",
            # Which BIDS sessions this pipeline is expected to have output
            # for -- e.g. FreeSurfer deliberately only run on ses-1/ses-2 of
            # a 3-session dataset. Consumed by
            # gui_cohort_routes.py::_pipeline_completeness_status (via
            # check_app_output.py --sessions) so the HPC housekeeping
            # completeness gate doesn't flag out-of-scope sessions as
            # missing. Empty means "expect every session in the dataset".
            "expected_sessions": app.get("expected_sessions") or [],
        },
        # Cohort/SLURM-array-only follow-up: runs FreeSurfer's
        # segment_subregions (thalamus/hippo-amygdala/brainstem) against
        # this dataset's already-completed recon-all output once the main
        # array's finish job has committed+pushed (see
        # submit_bids_cohort.sh::submit_subregion_segmentation). Not used
        # by Local/single-job HPC execution.
        "subregion_segmentation": app.get("subregion_segmentation") or {},
    }


def _drop_flag_with_value(options, flag):
    cleaned = []
    i = 0
    while i < len(options):
        token = str(options[i])
        if token == flag:
            i += 2
            continue
        if token.startswith(flag + "="):
            i += 1
            continue
        cleaned.append(token)
        i += 1
    return cleaned


def _apply_max_usage_cap(runtime_cfg, percent):
    """Apply CPU cap to a runtime config without mutating the original object."""
    capped = copy.deepcopy(runtime_cfg)

    max_cores = os.cpu_count() or 1
    allowed_cores = max(1, int(max_cores * (float(percent) / 100.0)))

    common = capped.setdefault("common", {})
    current_jobs = common.get("jobs", 1)
    try:
        current_jobs = int(current_jobs)
    except (TypeError, ValueError):
        current_jobs = 1
    common["jobs"] = max(1, min(current_jobs, allowed_cores))

    app_cfg = capped.setdefault("app", {})
    options = [str(x) for x in app_cfg.get("options", [])]
    cpu_flags = ["--nprocs", "--nthreads", "--n_cpus", "--n-cpus"]
    for flag in cpu_flags:
        options = _drop_flag_with_value(options, flag)

    options.extend(["--nprocs", str(allowed_cores)])
    app_cfg["options"] = options

    return capped, allowed_cores, max_cores


def _get_total_memory_bytes():
    """Best-effort total memory detection without external dependencies."""
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        phys_pages = os.sysconf("SC_PHYS_PAGES")
        if page_size and phys_pages and page_size > 0 and phys_pages > 0:
            return int(page_size) * int(phys_pages)
    except (AttributeError, OSError, ValueError):
        pass

    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    return int(parts[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass

    return None


def _compute_auto_nprocs_values(nprocs_min=2, nprocs_max=None, nprocs_step=1):
    """Compute auto nprocs sweep list for the pilot estimator."""
    detected_max = os.cpu_count() or 1
    start = max(1, int(nprocs_min))
    step = max(1, int(nprocs_step))
    stop = detected_max if nprocs_max is None else int(nprocs_max)
    stop = min(stop, detected_max)

    if start > stop:
        start = stop

    values = list(range(start, stop + 1, step))
    if values and values[-1] != stop:
        values.append(stop)
    if not values:
        values = [max(1, stop)]

    return values


def _is_process_alive(proc):
    if proc is None:
        return False
    try:
        return proc.poll() is None
    except Exception:
        return False


def _is_pilot_process_running(output_dir):
    """Best-effort process detection for a pilot estimator by output dir."""
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        marker = str(output_dir)
        for line in result.stdout.splitlines():
            if "pilot_resource_estimator.py" in line and marker in line:
                return True
    except Exception:
        pass
    return False


def _pilot_progress_from_output_dir(output_dir, expected_total=None):
    """Estimate pilot progress from generated per-step logs."""
    completed = 0
    if output_dir.exists():
        for f in output_dir.glob("stdout_n*.log"):
            try:
                if f.stat().st_size > 0:
                    completed += 1
            except OSError:
                continue

    total = int(expected_total) if expected_total else None
    percent = None
    if total and total > 0:
        percent = round(min(100.0, (completed / total) * 100.0), 1)

    return completed, total, percent


def _read_nifti_zooms(nifti_path):
    try:
        if str(nifti_path).endswith(".gz"):
            with gzip.open(nifti_path, "rb") as f:
                header = f.read(348)
        else:
            with open(nifti_path, "rb") as f:
                header = f.read(348)

        if len(header) < 108:
            return None

        little = struct.unpack("<i", header[0:4])[0]
        big = struct.unpack(">i", header[0:4])[0]
        if little == 348:
            endian = "<"
        elif big == 348:
            endian = ">"
        else:
            return None

        pixdim = struct.unpack(f"{endian}8f", header[76:108])
        zooms = [abs(float(pixdim[1])), abs(float(pixdim[2])), abs(float(pixdim[3]))]
        if any(z <= 0 for z in zooms):
            return None
        return zooms
    except Exception:
        return None


def _find_first_dwi_nifti(bids_dir):
    patterns = [
        "sub-*/dwi/*_dwi.nii.gz",
        "sub-*/dwi/*_dwi.nii",
        "sub-*/ses-*/dwi/*_dwi.nii.gz",
        "sub-*/ses-*/dwi/*_dwi.nii",
    ]
    for pattern in patterns:
        matches = sorted(Path(bids_dir).glob(pattern))
        if matches:
            return matches[0]
    return None


@app.route("/get_dwi_native_resolution", methods=["POST"])
def get_dwi_native_resolution():
    data = request.get_json(silent=True) or {}
    bids_dir = (data.get("bids_dir") or "").strip()
    if not bids_dir:
        return jsonify({"error": "bids_dir is required"}), 400

    bids_path = Path(os.path.expanduser(bids_dir))
    if not bids_path.exists() or not bids_path.is_dir():
        return jsonify({"error": f"BIDS folder not found: {bids_path}"}), 400

    dwi_file = _find_first_dwi_nifti(bids_path)
    if dwi_file is None:
        return jsonify({"error": "No DWI NIfTI file found in BIDS folder."}), 404

    zooms = _read_nifti_zooms(dwi_file)
    if not zooms:
        return (
            jsonify({"error": f"Could not read voxel size from: {dwi_file.name}"}),
            500,
        )

    is_near_isotropic = (max(zooms) - min(zooms)) <= 0.05
    default_resolution = zooms[0] if is_near_isotropic else max(zooms)

    return jsonify(
        {
            "resolution_mm": round(default_resolution, 1),
            "voxel_sizes_mm": [round(z, 1) for z in zooms],
            "is_near_isotropic": is_near_isotropic,
            "strategy": "native" if is_near_isotropic else "max_dim_no_upsample",
            "source_file": str(dwi_file),
        }
    )


def get_latest_version_from_dockerhub(repo):
    """Fetch the latest tag from Docker Hub for a given repo."""
    try:
        url = f"https://registry.hub.docker.com/v2/repositories/{repo}/tags?page_size=10&ordering=last_updated"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            # Filter out 'latest' and other non-version tags if possible,
            # but usually the first one that looks like a version is what we want.
            tags = [t["name"] for t in data.get("results", [])]
            for tag in tags:
                # Basic check to avoid 'latest', 'stable', 'master', etc.
                if re.search(r"\d+\.\d+", tag):
                    return tag
        return None
    except Exception as e:
        print(f"[DEBUG] Error checking Docker Hub for {repo}: {e}")
        return None


def _strip_container_extension(value):
    return re.sub(r"\.(sif|simg|img)$", "", value, flags=re.IGNORECASE)


def _numeric_version_key(version_text):
    match = re.match(r"^(\d+(?:\.\d+)*)", version_text)
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


@app.route("/check_container_version", methods=["POST"])
def check_container_version():
    container_path = request.json.get("container")
    if not container_path:
        return jsonify({"error": "No container path provided"}), 400

    filename = os.path.basename(container_path)
    filename_no_ext = _strip_container_extension(filename)

    # Common pattern: appname_version or appname-version
    match = re.search(
        r"^([a-zA-Z0-9-]+)[_-](v?\d+(?:\.\d+)*(?:[A-Za-z0-9._-]*)?)$",
        filename_no_ext,
    )

    if not match:
        # Fallback: try to just guess app name from string
        app_name = None
        for key in APP_REPO_MAPPING.keys():
            if key in filename.lower():
                app_name = key
                break

        if not app_name:
            return jsonify({"info": "Could not parse app name from filename"}), 200

        current_version = "unknown"
    else:
        app_name = match.group(1).lower()
        current_version = match.group(2).lstrip("v")

    repo = APP_REPO_MAPPING.get(app_name)
    if not repo:
        # Try a guess: bids/app_name
        repo = f"bids/{app_name}"

    latest_version = get_latest_version_from_dockerhub(repo)
    if not latest_version:
        # One more try if it's a known nipreps one
        if app_name in ["mriqc", "fmriprep", "nibabies"]:
            repo = f"nipreps/{app_name}"
            latest_version = get_latest_version_from_dockerhub(repo)

    if latest_version:
        # Normalize versions for comparison
        clean_current = _strip_container_extension(current_version.lower()).lstrip("v")
        clean_latest = _strip_container_extension(latest_version.lower()).lstrip("v")

        if clean_current in ["", "unknown"]:
            is_newer = False
        else:
            current_key = _numeric_version_key(clean_current)
            latest_key = _numeric_version_key(clean_latest)
            if current_key and latest_key:
                is_newer = latest_key > current_key
            else:
                is_newer = clean_latest != clean_current
        return jsonify(
            {
                "app": app_name,
                "current": current_version,
                "latest": latest_version,
                "is_newer": is_newer,
                "repo": repo,
                "changelog_url": f"https://github.com/{repo}/releases/tag/{latest_version if latest_version.startswith('v') else latest_version}",
            }
        )

    return jsonify({"info": "No newer version found or repo not identified"}), 200


@app.route("/get_docker_tags", methods=["POST"])
def get_docker_tags():
    data = request.json
    if not data or "repo" not in data:
        return jsonify({"error": "No repository provided"}), 400

    repo = str(data["repo"] or "").strip()
    if not repo:
        return jsonify({"error": "No repository provided"}), 400
    if " " in repo or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._\/-]*$", repo):
        return jsonify({"error": "Repository format is invalid"}), 400

    print(f"[GUI] Fetching tags for repo: {repo}", flush=True)

    try:
        # Some environments need a user-agent for Docker Hub
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        url = f"https://registry.hub.docker.com/v2/repositories/{repo}/tags?page_size=100&ordering=last_updated"

        response = requests.get(url, headers=headers, timeout=15)

        if response.status_code == 200:
            data = response.json()
            tags = [t["name"] for t in data.get("results", [])]
            print(f"[GUI] Successfully found {len(tags)} tags for {repo}", flush=True)
            return jsonify({"tags": tags})
        else:
            print(
                f"[GUI] Docker Hub returned status {response.status_code}: {response.text[:200]}",
                flush=True,
            )
            return (
                jsonify({"error": f"Docker Hub error {response.status_code}"}),
                response.status_code,
            )
    except requests.exceptions.SSLError:
        return (
            jsonify({"error": "TLS verification failed while contacting Docker Hub"}),
            502,
        )
    except Exception as e:
        print(f"[DEBUG] Exception fetching tags for {repo}: {str(e)}", flush=True)
        return jsonify({"error": str(e)}), 500


# Global state to track if a job was started in this GUI session
GUI_SESSION_STARTED = False


def _mark_gui_session_started():
    global GUI_SESSION_STARTED
    GUI_SESSION_STARTED = True


# Marker propagated to child processes so stop-all can target app-launched jobs.
APP_LAUNCH_ENV_KEY = "BIDS_APPS_RUNNER_LAUNCHED_BY_GUI"
APP_LAUNCH_ENV_VALUE = "1"

# Track runner processes launched from this GUI session
RUN_JOBS: dict[str, dict[str, Any]] = {}
RUN_JOBS_LOCK = threading.Lock()

# Simple cache for log polling to reduce file system calls (keyed by project_id)
_log_cache: dict[str, Any] = {}
_log_cache_ttl = 1.0  # Cache for 1 second

# Track pilot resource estimator jobs
PILOT_JOBS: dict[str, dict[str, Any]] = {}
PILOT_JOBS_LOCK = threading.Lock()


def _get_active_tracked_run_jobs():
    """Return active run jobs launched by this GUI session and prune finished ones."""
    active_jobs = []
    stale_ids = []
    with RUN_JOBS_LOCK:
        for run_id, state in RUN_JOBS.items():
            process = state.get("process")
            if process is None:
                stale_ids.append(run_id)
                continue

            if process.poll() is None:
                active_jobs.append(state)
            else:
                stale_ids.append(run_id)

        for run_id in stale_ids:
            RUN_JOBS.pop(run_id, None)

    return active_jobs


register_project_config_handlers(
    app,
    project_manager_getter=lambda: ProjectManager,
    normalize_project_id=_normalize_project_id,
    resolve_project_dir=lambda project_id: _resolve_project_dir(
        PROJECTS_DIR, project_id
    ),
    normalize_json_filename=_normalize_json_filename,
    resolve_named_config_path=_resolve_named_config_path,
    resolve_config_storage_dir=lambda directory: _resolve_config_storage_dir(
        directory,
        DATA_DIR / "configs",
        BASE_DIR / "configs",
    ),
    validate_project_json_shape=_validate_project_json_shape,
    get_active_tracked_run_jobs=_get_active_tracked_run_jobs,
    data_dir=DATA_DIR,
    base_dir=BASE_DIR,
    log_cache=_log_cache,
    log_cache_ttl=_log_cache_ttl,
)


def _terminate_pid_group(pid):
    """Terminate a process group first, then process as fallback."""
    try:
        pgid = os.getpgid(pid)
        os.killpg(pgid, signal.SIGTERM)
        return True
    except OSError:
        pass

    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def _iter_proc_pids():
    """Yield numeric process IDs from /proc."""
    proc_root = Path("/proc")
    try:
        for entry in proc_root.iterdir():
            if entry.name.isdigit():
                yield int(entry.name)
    except Exception:
        return


def _read_proc_cmdline(pid):
    """Read process command line as a single lowercased string."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        if not raw:
            return ""
        return (
            raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip().lower()
        )
    except Exception:
        return ""


def _is_marked_app_process(pid):
    """Return True when process carries the app launch marker in its environment."""
    needle = f"{APP_LAUNCH_ENV_KEY}={APP_LAUNCH_ENV_VALUE}".encode("utf-8")
    try:
        env_data = Path(f"/proc/{pid}/environ").read_bytes()
    except Exception:
        return False
    return needle in env_data.split(b"\x00")


def _find_app_related_pids(include_marked=True):
    """Find PIDs tied to app-triggered runs for reliable stop behavior."""
    pids = set()
    patterns = ["run_bids_apps.py", "prism_runner.py"]

    for pid in _iter_proc_pids():
        cmdline = _read_proc_cmdline(pid)
        if not cmdline:
            continue

        if any(pattern in cmdline for pattern in patterns):
            pids.add(pid)
            continue

        if include_marked and _is_marked_app_process(pid):
            pids.add(pid)

    return pids


def _terminate_pid_groups(pids):
    """Terminate each unique process group represented by the given PID collection."""
    terminated = 0
    signaled_groups = set()

    for pid in sorted(set(pids)):
        try:
            pgid = os.getpgid(pid)
        except OSError:
            continue

        if pgid in signaled_groups:
            continue

        if _terminate_pid_group(pid):
            signaled_groups.add(pgid)
            terminated += 1

    return terminated


_VSCODE_SERVER_STACK_RE = re.compile(r"\.vscode-server/cli/servers/stable-([a-f0-9]+)/")
# Threshold above which a VS Code Remote-SSH server stack is flagged as
# "stale" rather than just "running" -- see CLAUDE.md's HPC login node
# policy. Not a hard cutoff for anything destructive: nothing auto-kills a
# stale stack, this only decides what the GUI's banner surfaces to a human.
_VSCODE_STALE_THRESHOLD_SECONDS = 12 * 60 * 60

_boot_time_cache = None


def _boot_time():
    """System boot time as a Unix epoch timestamp, or None if unreadable."""
    global _boot_time_cache
    if _boot_time_cache is not None:
        return _boot_time_cache
    try:
        with open("/proc/stat", "r") as f:
            for line in f:
                if line.startswith("btime"):
                    _boot_time_cache = float(line.split()[1])
                    return _boot_time_cache
    except Exception:
        pass
    return None


def _process_start_epoch(pid):
    """Wall-clock epoch timestamp a process started, or None if unavailable."""
    boot = _boot_time()
    if boot is None:
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(errors="ignore")
        # comm (field 2) is parenthesized and may itself contain ")", so
        # split on the LAST ")" to reliably find where the numeric fields
        # begin -- fields after that point, 0-indexed, put starttime (the
        # overall field 22) at index 19.
        after_comm = raw.rsplit(")", 1)[1].split()
        starttime_ticks = float(after_comm[19])
    except Exception:
        return None
    try:
        clk_tck = os.sysconf("SC_CLK_TCK")
    except (AttributeError, ValueError):
        clk_tck = 100
    return boot + starttime_ticks / clk_tck


def _process_rss_bytes(pid):
    """Resident memory of a process in bytes, or 0 if unavailable."""
    try:
        with open(f"/proc/{pid}/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except Exception:
        pass
    return 0


def _find_vscode_remote_ssh_stacks():
    """Group this OS user's own VS Code Remote-SSH server processes by
    version stack (one "Stable-<hash>" directory per spun-up server, which
    in practice tracks roughly one per distinct connected window/session --
    see CLAUDE.md's HPC login node policy on why leftover stacks matter on
    a shared login node). Only ever inspects processes this OS user
    actually owns (checked via /proc/<pid> ownership) -- this GUI can be
    reached by multiple people on a shared login node, and must never
    surface or touch another user's processes.

    Returns a list of dicts, oldest stack first:
        {"hash", "pids", "started_at" (epoch|None), "age_seconds" (float|None),
         "rss_bytes", "stale" (bool)}
    """
    my_uid = os.getuid()
    stacks = {}
    for pid in _iter_proc_pids():
        cmdline = _read_proc_cmdline(pid)
        if not cmdline or ".vscode-server/cli/servers/stable-" not in cmdline:
            continue
        try:
            if os.stat(f"/proc/{pid}").st_uid != my_uid:
                continue
        except OSError:
            continue

        match = _VSCODE_SERVER_STACK_RE.search(cmdline)
        if not match:
            continue
        stacks.setdefault(match.group(1), []).append(pid)

    now = time.time()
    results = []
    for stack_hash, pids in stacks.items():
        starts = [t for t in (_process_start_epoch(p) for p in pids) if t is not None]
        started_at = min(starts) if starts else None
        age_seconds = (now - started_at) if started_at is not None else None
        results.append(
            {
                "hash": stack_hash,
                "pids": sorted(pids),
                "started_at": started_at,
                "age_seconds": age_seconds,
                "rss_bytes": sum(_process_rss_bytes(p) for p in pids),
                "stale": bool(
                    age_seconds is not None
                    and age_seconds >= _VSCODE_STALE_THRESHOLD_SECONDS
                ),
            }
        )

    results.sort(key=lambda s: (s["age_seconds"] is None, -(s["age_seconds"] or 0)))
    return results


def _terminate_tracked_run(state):
    process = state.get("process")
    if process is None:
        return False
    state["stop_requested"] = True
    return _terminate_pid_group(process.pid)



def _normalize_runner_args(runner_args):
    """Normalize runner args, expanding multi-value --subjects inputs safely."""
    if not isinstance(runner_args, list):
        return []

    normalized = []
    i = 0
    while i < len(runner_args):
        token = str(runner_args[i]).strip()
        if not token:
            i += 1
            continue

        if token == "--subjects":
            i += 1
            subjects = []
            while i < len(runner_args):
                value = str(runner_args[i]).strip()
                if not value:
                    i += 1
                    continue
                if value.startswith("--"):
                    break
                subjects.extend([s for s in re.split(r"[\s,]+", value) if s])
                i += 1

            if subjects:
                normalized.append("--subjects")
                normalized.extend(subjects)
            continue

        normalized.append(token)
        i += 1

    return normalized


def _get_smtp_settings():
    """Load SMTP settings from DATA_DIR/configs/smtp_settings.json with env overrides."""
    file_settings = {}
    smtp_config_path = DATA_DIR / "configs" / "smtp_settings.json"
    try:
        if smtp_config_path.exists():
            with open(smtp_config_path, "r", encoding="utf-8") as f:
                file_settings = json.load(f) or {}
            if not isinstance(file_settings, dict):
                file_settings = {}
    except Exception as exc:
        print(
            f"[GUI] Failed to read SMTP config file {smtp_config_path}: {exc}",
            flush=True,
        )
        file_settings = {}

    host = (
        os.environ.get("BIDS_RUNNER_SMTP_HOST") or file_settings.get("host") or ""
    ).strip()
    sender = (
        os.environ.get("BIDS_RUNNER_SMTP_SENDER") or file_settings.get("sender") or ""
    ).strip()
    username = (
        os.environ.get("BIDS_RUNNER_SMTP_USERNAME")
        or file_settings.get("username")
        or ""
    ).strip()
    password = os.environ.get("BIDS_RUNNER_SMTP_PASSWORD")
    if password is None:
        password = file_settings.get("password") or ""

    port_source = os.environ.get("BIDS_RUNNER_SMTP_PORT")
    if port_source is None:
        port_source = file_settings.get("port", 587)

    use_tls_source = os.environ.get("BIDS_RUNNER_SMTP_USE_TLS")
    if use_tls_source is None:
        use_tls_source = file_settings.get("use_tls", True)

    if not host:
        return None

    try:
        port = int(str(port_source).strip())
    except ValueError:
        port = 587

    if isinstance(use_tls_source, bool):
        use_tls = use_tls_source
    else:
        use_tls = str(use_tls_source).strip().lower() not in {"0", "false", "no", "off"}

    if not sender:
        sender = username

    if not sender:
        return None

    return {
        "host": host,
        "port": port,
        "sender": sender,
        "username": username,
        "password": password,
        "use_tls": use_tls,
    }


def _send_run_completion_email(recipient, subject, body):
    settings = _get_smtp_settings()
    if not settings:
        return False, "SMTP not configured"

    message = EmailMessage()
    message["From"] = settings["sender"]
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(settings["host"], settings["port"], timeout=30) as server:
            if settings["use_tls"]:
                server.starttls(context=ssl.create_default_context())
            if settings["username"]:
                server.login(settings["username"], settings["password"])
            server.send_message(message)
        return True, "sent"
    except Exception as exc:
        return False, str(exc)


def _read_log_last_lines(log_path, max_lines=30, max_bytes=131072):
    """Read the last N lines from a log file efficiently."""
    text = _read_log_tail(log_path, max_bytes=max_bytes)
    if not text:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])


def _extract_failure_summary(log_excerpt, max_items=6):
    """Extract compact error-focused lines from a log excerpt."""
    if not log_excerpt:
        return ""

    patterns = [
        r"\berror\b",
        r"\bexception\b",
        r"traceback",
        r"\bfailed\b",
        r"\bfatal\b",
        r"\bcritical\b",
    ]
    regex = re.compile("|".join(patterns), re.IGNORECASE)

    selected = []
    for line in log_excerpt.splitlines():
        clean = line.strip()
        if not clean:
            continue
        if regex.search(clean):
            selected.append(clean)
            if len(selected) >= max_items:
                break

    if not selected:
        return ""

    return "\n".join(f"- {line}" for line in selected)


def _run_smtp_diagnostics():
    settings = _get_smtp_settings()
    if not settings:
        return {
            "configured": False,
            "error": "SMTP not configured",
        }

    result = {
        "configured": True,
        "host": settings.get("host"),
        "port": settings.get("port"),
        "use_tls": bool(settings.get("use_tls")),
        "sender": settings.get("sender"),
        "username_set": bool(settings.get("username")),
        "password_set": bool(settings.get("password")),
        "connected": False,
        "ehlo_ok": False,
        "starttls_advertised": False,
        "starttls_ok": False,
        "auth_methods": [],
        "login_ok": None,
    }

    try:
        with smtplib.SMTP(settings["host"], settings["port"], timeout=20) as server:
            result["connected"] = True

            ehlo_code, ehlo_msg = server.ehlo()
            result["ehlo_ok"] = 200 <= ehlo_code < 300
            result["ehlo_code"] = ehlo_code
            result["ehlo_message"] = (
                ehlo_msg.decode("utf-8", errors="replace")
                if isinstance(ehlo_msg, (bytes, bytearray))
                else str(ehlo_msg)
            )

            features = dict(server.esmtp_features or {})
            result["starttls_advertised"] = "starttls" in features
            auth_raw = features.get("auth", "")
            auth_methods = [m.strip().upper() for m in auth_raw.split() if m.strip()]
            result["auth_methods"] = sorted(list(set(auth_methods)))

            if settings.get("use_tls"):
                if result["starttls_advertised"]:
                    server.starttls(context=ssl.create_default_context())
                    result["starttls_ok"] = True
                    server.ehlo()
                    features = dict(server.esmtp_features or {})
                    auth_raw = features.get("auth", "")
                    auth_methods = [
                        m.strip().upper() for m in auth_raw.split() if m.strip()
                    ]
                    result["auth_methods"] = sorted(list(set(auth_methods)))
                else:
                    result["starttls_error"] = (
                        "TLS requested but STARTTLS not advertised"
                    )

            username = settings.get("username")
            if username:
                try:
                    server.login(username, settings.get("password", ""))
                    result["login_ok"] = True
                except Exception as exc:
                    result["login_ok"] = False
                    result["login_error"] = str(exc)

    except Exception as exc:
        result["error"] = str(exc)

    return result


register_system_routes(
    app,
    version=__version__,
    check_system_dependencies=check_system_dependencies,
    get_active_tracked_run_jobs=_get_active_tracked_run_jobs,
    project_manager_getter=lambda: ProjectManager,
    find_app_related_pids=_find_app_related_pids,
    find_vscode_remote_ssh_stacks=_find_vscode_remote_ssh_stacks,
    terminate_pid_groups=_terminate_pid_groups,
    get_total_memory_bytes=_get_total_memory_bytes,
    current_machine_id=_current_machine_id,
    read_global_settings_doc=_read_global_settings_doc,
    write_global_settings_doc=_write_global_settings_doc,
    sanitize_machine_settings=_sanitize_machine_settings,
    get_effective_machine_settings=_get_effective_machine_settings,
    global_settings_path=GLOBAL_SETTINGS_PATH,
    run_smtp_diagnostics=_run_smtp_diagnostics,
    send_run_completion_email=_send_run_completion_email,
)


def _monitor_run_job(run_id):
    with RUN_JOBS_LOCK:
        state = RUN_JOBS.get(run_id)
        if not state:
            return
        process = state.get("process")

    if process is None:
        return

    return_code = process.wait()

    with RUN_JOBS_LOCK:
        state = RUN_JOBS.get(run_id)
        if not state:
            return

        state["returncode"] = return_code
        finished_at = time.time()
        state["finished_at"] = finished_at
        state["process"] = None

        stop_requested = bool(state.get("stop_requested"))
        notify_email = (state.get("notify_email") or "").strip()
        project_id = state.get("project_id")
        log_file = state.get("log_file")
        cmd = state.get("cmd") or []
        started_at = float(state.get("started_at", time.time()))

    if stop_requested:
        status_label = "Stopped"
        result_label = "STOPPED"
    elif return_code == 0:
        status_label = "Completed"
        result_label = "SUCCESS"
    else:
        status_label = "Failed"
        result_label = "FAILED"

    if not notify_email:
        return

    duration_seconds = max(0, int(time.time() - started_at))
    host_name = platform.node() or "unknown-host"
    command_text = " ".join(str(c) for c in cmd)
    started_iso = datetime.fromtimestamp(started_at).isoformat(timespec="seconds")
    finished_iso = datetime.fromtimestamp(finished_at).isoformat(timespec="seconds")

    project_name = "N/A"
    if project_id:
        try:
            project = ProjectManager.load_project(project_id)
            if project:
                project_name = project.get("name") or project_id
        except Exception:
            project_name = project_id

    log_excerpt = _read_log_last_lines(log_file, max_lines=30) if log_file else ""
    if not log_excerpt:
        log_excerpt = "<No log excerpt available>"
    failure_summary = _extract_failure_summary(log_excerpt)

    subject = f"BIDS App Runner {status_label}: {run_id}"
    failure_summary_block = ""
    if result_label != "SUCCESS":
        if failure_summary:
            failure_summary_block = f"\nFailure summary:\n{failure_summary}\n"
        else:
            failure_summary_block = "\nFailure summary:\n- No explicit ERROR/Traceback lines found in the last 30 log lines.\n"

    body = (
        (
            f"Run ID: {run_id}\n"
            f"Result: {result_label}\n"
            f"Status: {status_label}\n"
            f"Return code: {return_code}\n"
            f"Start time: {started_iso}\n"
            f"End time: {finished_iso}\n"
            f"Duration (seconds): {duration_seconds}\n"
            f"Host: {host_name}\n"
            f"Project: {project_name}\n"
            f"Project ID: {project_id or 'N/A'}\n"
            f"Log file: {log_file or 'N/A'}\n"
            f"\nCommand:\n{command_text}\n"
        )
        + failure_summary_block
        + f"\nLast 30 log lines:\n{log_excerpt}\n"
    )

    sent, details = _send_run_completion_email(notify_email, subject, body)
    with RUN_JOBS_LOCK:
        state = RUN_JOBS.get(run_id)
        if not state:
            return
        state["email_notified"] = bool(sent)
        state["email_details"] = details

    if sent:
        print(f"[GUI] Completion email sent to {notify_email} for {run_id}", flush=True)
    else:
        print(
            f"[GUI] Failed to send completion email to {notify_email} for {run_id}: {details}",
            flush=True,
        )


def _read_log_tail(log_path, max_bytes=65536):
    try:
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            start = max(0, file_size - max_bytes)
            f.seek(start, os.SEEK_SET)
            data = f.read().decode("utf-8", errors="replace")
            if start > 0:
                newline_pos = data.find("\n")
                if newline_pos != -1:
                    data = data[newline_pos + 1 :]
            return data
    except OSError:
        return ""


register_utility_routes(
    app,
    data_dir=DATA_DIR,
    ensure_logs_dir=_ensure_logs_dir,
    read_log_tail=_read_log_tail,
)

register_misc_routes(
    app,
    bids_output_validator_cls=BIDSOutputValidator,
    ensure_logs_dir=_ensure_logs_dir,
    log_dir=LOG_DIR,
    base_dir=BASE_DIR,
)

register_cohort_routes(
    app,
    base_dir=BASE_DIR,
    log_dir=LOG_DIR,
    ensure_logs_dir=_ensure_logs_dir,
    resolve_project_dir=lambda project_id: _resolve_project_dir(
        PROJECTS_DIR, project_id
    ),
    load_project=ProjectManager.load_project,
    extract_runtime_config=_extract_runtime_config,
    derive_cohort_config=_derive_cohort_config,
)


def _extract_fs_license_path(options):
    """Extract FreeSurfer license path from app options."""
    if not options:
        return None
    for i, opt in enumerate(options):
        if opt == "--fs-license-file" and i + 1 < len(options):
            return options[i + 1]
        if opt.startswith("--fs-license-file="):
            return opt.split("=", 1)[1]
    return None


def _map_container_path_to_host(container_path, mounts):
    """Map a container path to a host path using mounts."""
    if not container_path or not mounts:
        return None
    for mount in mounts:
        source = mount.get("source")
        target = mount.get("target")
        if not source or not target:
            continue
        target_norm = target.rstrip("/")
        if container_path == target_norm or container_path.startswith(
            target_norm + "/"
        ):
            rel = container_path[len(target_norm) :].lstrip("/")
            return os.path.join(source, rel) if rel else source
    return None


register_run_routes(
    app,
    resolve_config_path=resolve_config_path,
    resolve_project_dir=lambda project_id: _resolve_project_dir(
        PROJECTS_DIR, project_id
    ),
    extract_runtime_config=_extract_runtime_config,
    normalize_runner_args=_normalize_runner_args,
    apply_max_usage_cap=_apply_max_usage_cap,
    extract_fs_license_path=_extract_fs_license_path,
    map_container_path_to_host=_map_container_path_to_host,
    compute_auto_nprocs_values=_compute_auto_nprocs_values,
    is_process_alive=_is_process_alive,
    is_pilot_process_running=_is_pilot_process_running,
    pilot_progress_from_output_dir=_pilot_progress_from_output_dir,
    read_log_tail=_read_log_tail,
    get_active_tracked_run_jobs=_get_active_tracked_run_jobs,
    terminate_tracked_run=_terminate_tracked_run,
    terminate_pid_group=_terminate_pid_group,
    terminate_pid_groups=_terminate_pid_groups,
    find_app_related_pids=_find_app_related_pids,
    monitor_run_job=_monitor_run_job,
    project_manager_getter=lambda: ProjectManager,
    data_dir=DATA_DIR,
    base_dir=BASE_DIR,
    python_exe=PYTHON_EXE,
    app_launch_env_key=APP_LAUNCH_ENV_KEY,
    app_launch_env_value=APP_LAUNCH_ENV_VALUE,
    hpc_datalad_available=HPC_DATALAD_AVAILABLE,
    run_jobs=RUN_JOBS,
    run_jobs_lock=RUN_JOBS_LOCK,
    pilot_jobs=PILOT_JOBS,
    pilot_jobs_lock=PILOT_JOBS_LOCK,
    mark_gui_session_started=_mark_gui_session_started,
)


if __name__ == "__main__":
    host = GUI_HOST
    if not _is_loopback_host(host) and not GUI_AUTH_TOKEN and not GUI_LOGIN_ENABLED:
        print(
            "[ERROR] Refusing to bind the GUI to a non-loopback host without PRISM_GUI_AUTH_TOKEN or GUI login",
            flush=True,
        )
        sys.exit(1)

    try:
        port = _find_available_port(host)
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", flush=True)
        sys.exit(1)

    display_host = "localhost" if _is_loopback_host(host) else host

    print(f"🌐 Starting BIDS App Runner GUI v{__version__}")
    print(f"🔗 Open in browser: http://{display_host}:{port}")
    print(f"   (VS Code: use the PORTS panel → globe icon next to port {port})")
    print("💡 Press Ctrl+C to stop the server\n")
    print(f"🚀 Running with Waitress server on {host}:{port}", flush=True)
    if GUI_LOGIN_ENABLED:
        print("🔒 Browser login is enabled", flush=True)
        if GUI_BOOTSTRAP_PASSWORD:
            print(
                f"🔑 Generated GUI password for this run: {GUI_BOOTSTRAP_PASSWORD}",
                flush=True,
            )
    if GUI_AUTH_TOKEN:
        print(
            f"🔐 Remote requests must provide {GUI_AUTH_HEADER} or Authorization: Bearer <token>",
            flush=True,
        )

    # Open browser after a short delay so the server is ready to accept connections.
    if _is_loopback_host(host):
        url = f"http://{display_host}:{port}"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    serve(app, host=host, port=port, threads=4)
