#!/usr/bin/env python3
import re
import os
import platform
import sys
import json
import glob
import subprocess
import time
import logging
import uuid
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
import tempfile
import webbrowser
import threading
from datetime import datetime
from flask import Flask, render_template, request, jsonify
from waitress import serve
from pathlib import Path
from version import __version__

# Add scripts directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))

from check_app_output import BIDSOutputValidator

# Try to import HPC DataLad runner
try:
    from hpc_datalad_runner import (
        DataLadHPCScriptGenerator,
        validate_datalad_config,
        validate_hpc_config,
    )

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
    for p in extra_paths:
        if p not in current_path and os.path.exists(p):
            current_path.append(p)
            path_changed = True

    if path_changed:
        os.environ["PATH"] = os.pathsep.join(current_path)
        print(
            f"[GUI] Updated PATH to include common locations: {os.environ['PATH']}",
            flush=True,
        )


# Important to fix path before any shutil.which calls
_fix_system_path()

if getattr(sys, "frozen", False):
    # Running in a bundle
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS"))
    app = Flask(
        __name__,
        template_folder=str(BUNDLE_DIR / "templates"),
        static_folder=str(BUNDLE_DIR / "static"),
    )
else:
    # Running in normal Python environment
    BUNDLE_DIR = Path(__file__).resolve().parent
    app = Flask(__name__)

app.secret_key = "bids-app-runner-secret-key"
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["APP_VERSION"] = __version__

# Application base directory (for scripts, etc.)
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = BUNDLE_DIR


# Data directory (for logs, configs, etc.) - must be writable
def _get_data_dir():
    # If BASE_DIR is writable (e.g. during development), use it
    try:
        test_file = BASE_DIR / ".write_test"
        test_file.touch()
        test_file.unlink()
        return BASE_DIR
    except (PermissionError, OSError):
        # Otherwise, use a standard user-writable location
        if platform.system() == "Darwin":
            d = Path.home() / "Library" / "Application Support" / "BIDSAppsRunner"
        else:
            d = Path.home() / ".bids_apps_runner"
        d.mkdir(parents=True, exist_ok=True)
        return d


DATA_DIR = _get_data_dir()
LOG_DIR = DATA_DIR / "logs"
PROJECTS_DIR = DATA_DIR / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


class ProjectManager:
    """Manages BIDS App Runner projects."""

    @staticmethod
    def create_project(name, description=""):
        """Create a new project with the given name."""
        # Generate unique project ID from name
        project_id = name.lower().replace(" ", "_").replace("-", "_")
        project_id = re.sub(r"[^a-z0-9_]", "", project_id)
        if not project_id:
            project_id = "project_" + str(int(time.time()))

        # Ensure uniqueness
        counter = 1
        original_id = project_id
        while (PROJECTS_DIR / project_id).exists():
            project_id = f"{original_id}_{counter}"
            counter += 1

        project_dir = PROJECTS_DIR / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "logs").mkdir(exist_ok=True)

        project_json = {
            "id": project_id,
            "name": name,
            "description": description,
            "created": datetime.now().isoformat(),
            "last_modified": datetime.now().isoformat(),
            "last_log": None,
            "config": {
                "common": {
                    "bids_folder": "",
                    "output_folder": "",
                    "tmp_folder": "",
                    "templateflow_dir": "",
                    "notify_email": "",
                    "container_engine": "apptainer",
                    "container": "",
                    "jobs": 1,
                },
                "app": {"analysis_level": "participant", "options": [], "mounts": []},
            },
        }

        project_json_path = project_dir / "project.json"
        with open(project_json_path, "w") as f:
            json.dump(project_json, f, indent=2)

        return project_id, project_json

    @staticmethod
    def load_project(project_id):
        """Load a project by ID."""
        project_dir = PROJECTS_DIR / project_id
        project_json_path = project_dir / "project.json"

        if not project_json_path.exists():
            return None

        with open(project_json_path, "r") as f:
            return json.load(f)

    @staticmethod
    def save_project(project_id, config):
        """Save project configuration."""
        project_dir = PROJECTS_DIR / project_id
        project_json_path = project_dir / "project.json"

        if not project_json_path.exists():
            return False

        with open(project_json_path, "r") as f:
            project_json = json.load(f)

        project_json["config"] = config
        project_json["last_modified"] = datetime.now().isoformat()

        with open(project_json_path, "w") as f:
            json.dump(project_json, f, indent=2)

        return True

    @staticmethod
    def update_project_log(project_id, log_filename):
        """Update the last_log field in project.json."""
        project_dir = PROJECTS_DIR / project_id
        project_json_path = project_dir / "project.json"

        if not project_json_path.exists():
            return False

        with open(project_json_path, "r") as f:
            project_json = json.load(f)

        project_json["last_log"] = log_filename
        project_json["last_modified"] = datetime.now().isoformat()

        with open(project_json_path, "w") as f:
            json.dump(project_json, f, indent=2)

        return True

    @staticmethod
    def list_projects(limit=None):
        """List all projects, sorted by last_modified (newest first)."""
        projects = []

        if not PROJECTS_DIR.exists():
            return projects

        for project_dir in sorted(
            PROJECTS_DIR.iterdir(), key=lambda x: x.is_dir(), reverse=True
        ):
            if not project_dir.is_dir():
                continue

            project_json_path = project_dir / "project.json"
            if project_json_path.exists():
                try:
                    with open(project_json_path, "r") as f:
                        project_data = json.load(f)
                    projects.append(project_data)
                except Exception as e:
                    print(f"[ERROR] Failed to load project {project_dir.name}: {e}")

        # Sort by last_modified, newest first
        projects.sort(key=lambda x: x.get("last_modified", ""), reverse=True)

        if limit:
            projects = projects[:limit]

        return projects

    @staticmethod
    def delete_project(project_id):
        """Delete a project and all its data."""
        project_dir = PROJECTS_DIR / project_id

        if project_dir.exists():
            shutil.rmtree(project_dir)
            return True

        return False


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


@app.before_request
def log_request_info():
    print(
        f"[GUI] {request.method} {request.path} from {request.remote_addr}", flush=True
    )


# Common BIDS Apps mapping to Docker Hub repos
APP_REPO_MAPPING = {
    "mriqc": "nipreps/mriqc",
    "fmriprep": "nipreps/fmriprep",
    "qsiprep": "pennlinc/qsiprep",
    "nibabies": "nipreps/nibabies",
    "mritools": "bids/mritools",
    "freesurfer": "freesurfer/freesurfer",
    "synthseg": "freesurfer/synthseg",
}


def _ensure_logs_dir():
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _record_build_log(path, command, stdout, stderr):
    try:
        with open(path, "w", encoding="utf-8") as logf:
            logf.write(f"Command: {' '.join(command)}\n\n")
            logf.write("STDOUT:\n")
            logf.write((stdout or "<no output>") + "\n\n")
            logf.write("STDERR:\n")
            logf.write((stderr or "<no errors>") + "\n")
    except OSError:
        pass


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
        return jsonify({"error": f"Could not read voxel size from: {dwi_file.name}"}), 500

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

    repo = data["repo"]
    print(f"[GUI] Fetching tags for repo: {repo}", flush=True)

    try:
        # Some environments need a user-agent for Docker Hub
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        url = f"https://registry.hub.docker.com/v2/repositories/{repo}/tags?page_size=100&ordering=last_updated"

        # Try with a longer timeout and verify=True first, but handle SSLError
        try:
            response = requests.get(url, headers=headers, timeout=15)
        except requests.exceptions.SSLError:
            print(
                f"[DEBUG] SSL Error for {repo}, retrying without verification...",
                flush=True,
            )
            response = requests.get(url, headers=headers, timeout=15, verify=False)

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
    except Exception as e:
        print(f"[DEBUG] Exception fetching tags for {repo}: {str(e)}", flush=True)
        return jsonify({"error": str(e)}), 500


# Global state to track if a job was started in this GUI session
GUI_SESSION_STARTED = False

# Marker propagated to child processes so stop-all can target app-launched jobs.
APP_LAUNCH_ENV_KEY = "BIDS_APPS_RUNNER_LAUNCHED_BY_GUI"
APP_LAUNCH_ENV_VALUE = "1"

# Track runner processes launched from this GUI session
RUN_JOBS = {}
RUN_JOBS_LOCK = threading.Lock()

# Simple cache for log polling to reduce file system calls
_log_cache = {"timestamp": 0, "content": "", "filename": "none", "is_active": False}
_log_cache_ttl = 1.0  # Cache for 1 second

# Track background Apptainer builds started from GUI utility tab
APPTAINER_BUILDS = {}
APPTAINER_BUILDS_LOCK = threading.Lock()


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
        return raw.replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip().lower()
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


def _terminate_tracked_run(state):
    process = state.get("process")
    if process is None:
        return False
    state["stop_requested"] = True
    return _terminate_pid_group(process.pid)


def _terminate_tracked_build(state):
    process = state.get("process")
    if process is None:
        return False
    state["cancel_requested"] = True
    return _terminate_pid_group(process.pid)


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
        print(f"[GUI] Failed to read SMTP config file {smtp_config_path}: {exc}", flush=True)
        file_settings = {}

    host = (os.environ.get("BIDS_RUNNER_SMTP_HOST") or file_settings.get("host") or "").strip()
    sender = (os.environ.get("BIDS_RUNNER_SMTP_SENDER") or file_settings.get("sender") or "").strip()
    username = (os.environ.get("BIDS_RUNNER_SMTP_USERNAME") or file_settings.get("username") or "").strip()
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
                    auth_methods = [m.strip().upper() for m in auth_raw.split() if m.strip()]
                    result["auth_methods"] = sorted(list(set(auth_methods)))
                else:
                    result["starttls_error"] = "TLS requested but STARTTLS not advertised"

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
    ) + failure_summary_block + f"\nLast 30 log lines:\n{log_excerpt}\n"

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


def _prepare_apptainer_build(data):
    output_dir = (data.get("output_dir") or "").strip()
    tmp_dir = (data.get("tmp_dir") or "").strip()
    if not output_dir or not tmp_dir:
        return None, (jsonify({"error": "Output directory and temporary directory are required."}), 400)

    output_path = Path(os.path.expanduser(output_dir))
    tmp_path = Path(os.path.expanduser(tmp_dir))
    output_path.mkdir(parents=True, exist_ok=True)
    tmp_path.mkdir(parents=True, exist_ok=True)

    _ensure_logs_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"build_apptainer_{timestamp}.log"

    dockerfile = (data.get("dockerfile") or "").strip()
    docker_repo = (data.get("docker_repo") or "").strip()
    docker_tag = (data.get("docker_tag") or "").strip()
    keep_temp = bool(data.get("keep_temp"))
    timeout_seconds = int(data.get("timeout", 7200))

    cmd = []
    built_image = None
    per_build_dir = None
    build_env = os.environ.copy()
    cache_dir = None

    if dockerfile:
        script_path = BASE_DIR / "scripts" / "build_apptainer.sh"
        if not script_path.exists():
            return None, (jsonify({"error": "Build script missing from project."}), 500)
        cmd = ["bash", str(script_path), "-o", str(output_path), "-t", str(tmp_path)]
        if keep_temp:
            cmd.append("--no-temp-del")
        cmd.extend(["-d", dockerfile])
        if docker_repo:
            cmd.extend(["--docker-repo", docker_repo])
        if docker_tag:
            cmd.extend(["--docker-tag", docker_tag])
        cache_dir = tmp_path / "apptainer_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
    else:
        if not docker_repo or not docker_tag:
            return (
                None,
                (
                    jsonify(
                        {
                            "error": "Docker repository and tag are required when no Dockerfile is provided."
                        }
                    ),
                    400,
                ),
            )
        if shutil.which("apptainer") is None:
            return None, (jsonify({"error": "Apptainer is not available on this host."}), 500)
        per_build_dir = tempfile.mkdtemp(prefix="apptainer_build_", dir=str(tmp_path))
        cache_dir = Path(per_build_dir) / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        built_image = output_path / f"{Path(docker_repo).name}_{docker_tag}.sif"
        cmd = [
            "apptainer",
            "build",
            "--force",
            "--tmpdir",
            per_build_dir,
            str(built_image),
            f"docker://{docker_repo}:{docker_tag}",
        ]

    build_env["APPTAINER_TMPDIR"] = str(tmp_path)
    build_env["SINGULARITY_TMPDIR"] = str(tmp_path)
    build_env["TMPDIR"] = str(tmp_path)
    build_env[APP_LAUNCH_ENV_KEY] = APP_LAUNCH_ENV_VALUE
    if cache_dir is not None:
        build_env["APPTAINER_CACHEDIR"] = str(cache_dir)
        build_env["SINGULARITY_CACHEDIR"] = str(cache_dir)

    return {
        "cmd": cmd,
        "log_file": str(log_file),
        "output_image": str(built_image) if built_image else None,
        "per_build_dir": per_build_dir,
        "keep_temp": keep_temp,
        "timeout_seconds": timeout_seconds,
        "cwd": str(BASE_DIR),
        "env": build_env,
    }, None


def _run_apptainer_build_async(build_id):
    with APPTAINER_BUILDS_LOCK:
        state = APPTAINER_BUILDS.get(build_id)
        if not state:
            return

    log_file = state["log_file"]
    cmd = state["cmd"]
    per_build_dir = state.get("per_build_dir")
    keep_temp = bool(state.get("keep_temp"))

    try:
        with open(log_file, "w", encoding="utf-8") as logf:
            logf.write(f"Command: {' '.join(cmd)}\n")
            logf.write(f"Started: {datetime.now().isoformat()}\n\n")
            logf.flush()

            process = subprocess.Popen(
                cmd,
                stdout=logf,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=state["cwd"],
                env=state["env"],
                start_new_session=True,
            )

            with APPTAINER_BUILDS_LOCK:
                if build_id in APPTAINER_BUILDS:
                    APPTAINER_BUILDS[build_id]["process"] = process
                    APPTAINER_BUILDS[build_id]["pid"] = process.pid

            return_code = process.wait(timeout=state.get("timeout_seconds", 7200))

        with APPTAINER_BUILDS_LOCK:
            if build_id in APPTAINER_BUILDS:
                cancelled = bool(APPTAINER_BUILDS[build_id].get("cancel_requested"))
                APPTAINER_BUILDS[build_id]["returncode"] = return_code
                APPTAINER_BUILDS[build_id]["process"] = None
                APPTAINER_BUILDS[build_id]["finished_at"] = time.time()
                if cancelled:
                    APPTAINER_BUILDS[build_id]["status"] = "cancelled"
                elif return_code == 0:
                    APPTAINER_BUILDS[build_id]["status"] = "completed"
                else:
                    APPTAINER_BUILDS[build_id]["status"] = "failed"
    except subprocess.TimeoutExpired:
        with APPTAINER_BUILDS_LOCK:
            process = APPTAINER_BUILDS.get(build_id, {}).get("process")
        if process:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                pass
        with APPTAINER_BUILDS_LOCK:
            if build_id in APPTAINER_BUILDS:
                APPTAINER_BUILDS[build_id]["status"] = "failed"
                APPTAINER_BUILDS[build_id]["returncode"] = 124
                APPTAINER_BUILDS[build_id]["error"] = "Apptainer build timed out"
                APPTAINER_BUILDS[build_id]["process"] = None
                APPTAINER_BUILDS[build_id]["finished_at"] = time.time()
    except Exception as exc:
        with APPTAINER_BUILDS_LOCK:
            if build_id in APPTAINER_BUILDS:
                APPTAINER_BUILDS[build_id]["status"] = "failed"
                APPTAINER_BUILDS[build_id]["returncode"] = 1
                APPTAINER_BUILDS[build_id]["error"] = str(exc)
                APPTAINER_BUILDS[build_id]["process"] = None
                APPTAINER_BUILDS[build_id]["finished_at"] = time.time()
    finally:
        if per_build_dir and not keep_temp:
            shutil.rmtree(per_build_dir, ignore_errors=True)


@app.route("/get_log", methods=["GET"])
def get_log():
    global _log_cache
    try:
        # Get project_id from query params (optional)
        project_id = request.args.get("project_id")

        # Check cache first
        current_time = time.time()
        if current_time - _log_cache["timestamp"] < _log_cache_ttl:
            return jsonify(
                {
                    "content": _log_cache["content"],
                    "filename": _log_cache["filename"],
                    "is_active": _log_cache["is_active"],
                }
            )

        # Check if there are any active run_bids_apps.py processes
        result = subprocess.run(
            ["pgrep", "-f", "scripts/run_bids_apps.py"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        has_active_job = bool(result.stdout.strip())

        # If project_id is specified, look for logs in that project
        if project_id:
            project_dir = PROJECTS_DIR / project_id / "logs"
            if project_dir.exists():
                log_files = sorted(
                    list(project_dir.glob("*.log")), key=os.path.getmtime, reverse=True
                )
            else:
                log_files = []
        else:
            # Fallback to old location (DATA_DIR) for backward compatibility
            log_files = glob.glob(str(DATA_DIR / "nohup_bids_runner_*.log"))

        if not log_files:
            return jsonify({"content": "", "filename": "none", "is_active": False}), 200

        latest_log = log_files[0]

        # Check if log file was modified within the last 5 minutes (300 seconds)
        current_time = time.time()
        log_mtime = os.path.getmtime(latest_log)
        is_recently_active = (current_time - log_mtime) < 300

        # Only show logs if there's an active job OR the log was recently modified
        if not (has_active_job or is_recently_active):
            return jsonify({"content": "", "filename": "none", "is_active": False}), 200

        # Use tail to get last 150 lines efficiently
        result = subprocess.run(
            ["tail", "-n", "150", latest_log], capture_output=True, text=True
        )
        content = result.stdout

        # Strip ANSI escape sequences (colors)
        ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        content = ansi_escape.sub("", content)

        # Add idle indicator if no active job and log is stale
        if not has_active_job and not is_recently_active:
            content = (
                "[Idle - Last activity: "
                + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(log_mtime))
                + "]\n"
                + content
            )

        # Update cache
        _log_cache = {
            "timestamp": current_time,
            "content": content,
            "filename": (
                os.path.basename(latest_log)
                if (has_active_job or is_recently_active)
                else "none"
            ),
            "is_active": has_active_job or is_recently_active,
        }

        return jsonify(
            {
                "filename": _log_cache["filename"],
                "content": _log_cache["content"],
                "is_active": _log_cache["is_active"],
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/get_projects", methods=["GET"])
def get_projects():
    """Get list of recent projects (limit 5)."""
    try:
        projects = ProjectManager.list_projects(limit=5)
        return jsonify({"projects": projects}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/create_project", methods=["POST"])
def create_project():
    """Create a new project."""
    try:
        data = request.json
        name = data.get("name", "").strip()
        description = data.get("description", "").strip()

        if not name:
            return jsonify({"error": "Project name is required"}), 400

        project_id, project_json = ProjectManager.create_project(name, description)
        return jsonify({"project_id": project_id, "project": project_json}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/load_project/<project_id>", methods=["GET"])
def load_project(project_id):
    """Load a project configuration."""
    try:
        project_json = ProjectManager.load_project(project_id)
        if not project_json:
            return jsonify({"error": f"Project {project_id} not found"}), 404

        return jsonify(project_json), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/save_project/<project_id>", methods=["POST"])
def save_project(project_id):
    """Save project configuration."""
    try:
        data = request.json
        config = data.get("config")

        if not config:
            return jsonify({"error": "Config is required"}), 400

        success = ProjectManager.save_project(project_id, config)
        if not success:
            return jsonify({"error": f"Project {project_id} not found"}), 404

        return jsonify({"message": "Project saved successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/delete_project/<project_id>", methods=["DELETE"])
def delete_project(project_id):
    """Delete a project."""
    try:
        success = ProjectManager.delete_project(project_id)
        if not success:
            return jsonify({"error": f"Project {project_id} not found"}), 404

        return jsonify({"message": "Project deleted successfully"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health")
def health():
    return f"Flask is running and responding! (v{__version__})", 200


@app.route("/check_system", methods=["GET"])
def check_system():
    """Endpoint to check system dependencies."""
    return jsonify(check_system_dependencies())


@app.route("/smtp_diagnostics", methods=["POST"])
def smtp_diagnostics():
    """Inspect SMTP capabilities and optionally send a test email."""
    data = request.get_json(silent=True) or {}
    send_test = bool(data.get("send_test", False))
    recipient = (data.get("recipient") or "").strip()

    diagnostics = _run_smtp_diagnostics()
    response = {"diagnostics": diagnostics}

    if send_test:
        if not recipient:
            return jsonify({"error": "recipient is required when send_test=true"}), 400

        subject = "BIDS App Runner SMTP diagnostic test"
        body = (
            "This is a diagnostic test email from BIDS App Runner.\n"
            f"Time: {datetime.now().isoformat()}\n"
        )
        sent, details = _send_run_completion_email(recipient, subject, body)
        response["test_email"] = {
            "recipient": recipient,
            "sent": bool(sent),
            "details": details,
        }

    return jsonify(response), 200


@app.route("/list_reports", methods=["POST"])
def list_reports():
    """List HTML reports in derivatives folder for a specific pipeline."""
    data = request.json or {}
    derivatives_dir = data.get("derivatives_dir")
    pipeline = data.get("pipeline")

    if not derivatives_dir:
        return jsonify({"error": "Derivatives folder required"}), 400

    p = Path(os.path.expanduser(derivatives_dir))
    if pipeline:
        p = p / pipeline

    if not p.exists():
        return jsonify({"reports": []})

    # Find all .html files that look like subject reports
    reports = []
    # common patterns: sub-xxx.html or sub-xxx_desc-report.html
    html_files = sorted(list(p.glob("sub-*.html")))
    for hf in html_files:
        reports.append(
            {
                "name": hf.name,
                "path": str(hf),
                "subject": hf.name.split("_")[0].split(".")[0],
                "modified": datetime.fromtimestamp(hf.stat().st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        )

    return jsonify({"reports": reports})


@app.route("/run_output_check", methods=["POST"])
def run_output_check():
    data = request.get_json(silent=True) or {}
    bids_dir = (data.get("bids_dir") or "").strip()
    derivatives_dir = (data.get("derivatives_dir") or "").strip()
    if not bids_dir or not derivatives_dir:
        return (
            jsonify({"error": "Both BIDS and derivatives folders must be provided."}),
            400,
        )

    bids_path = Path(os.path.expanduser(bids_dir))
    derivatives_path = Path(os.path.expanduser(derivatives_dir))
    if not bids_path.exists():
        return jsonify({"error": f"BIDS folder does not exist: {bids_path}"}), 400
    if not derivatives_path.exists():
        return (
            jsonify(
                {"error": f"Derivatives folder does not exist: {derivatives_path}"}
            ),
            400,
        )

    _ensure_logs_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"check_app_output_{timestamp}.log"

    script_path = BASE_DIR / "scripts" / "check_app_output.py"
    if not script_path.exists():
        script_path = BASE_DIR / "check_app_output.py"

    cmd = [
        sys.executable,
        str(script_path),
        str(bids_path),
        str(derivatives_path),
        "--json",
        "--log",
        str(log_file),
    ]
    pipeline = (data.get("pipeline") or "").strip()
    if pipeline:
        cmd.extend(["-p", pipeline])
    if data.get("verbose"):
        cmd.append("--verbose")
    if data.get("quiet"):
        cmd.append("--quiet")
    if data.get("list_missing"):
        cmd.append("--list-missing-subjects")

    timeout_seconds = int(data.get("timeout", 900))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            cwd=str(BASE_DIR),
        )
    except subprocess.TimeoutExpired as exc:
        return jsonify({"error": "Validation timed out", "details": str(exc)}), 500

    parsed = None
    try:
        # Try direct parsing first
        parsed = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        # Fallback: find the first { and last } to extract JSON block
        try:
            start = result.stdout.find("{")
            end = result.stdout.rfind("}")
            if start != -1 and end != -1:
                json_str = result.stdout[start : end + 1]
                parsed = json.loads(json_str)
        except:
            parsed = None

    return jsonify(
        {
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "parsed": parsed,
            "log_file": str(log_file),
        }
    )


@app.route("/detect_validation_pipelines", methods=["POST"])
def detect_validation_pipelines():
    data = request.get_json(silent=True) or {}
    bids_dir = (data.get("bids_dir") or "").strip()
    derivatives_dir = (data.get("derivatives_dir") or "").strip()

    if not bids_dir or not derivatives_dir:
        return (
            jsonify({"error": "Both BIDS and derivatives folders are required."}),
            400,
        )

    try:
        bids_path = Path(os.path.expanduser(bids_dir))
        derivatives_path = Path(os.path.expanduser(derivatives_dir))

        if not bids_path.exists() or not derivatives_path.exists():
            return jsonify({"pipelines": []})  # No folder, no pipelines

        validator = BIDSOutputValidator(bids_path, derivatives_path)
        pipelines = validator.discover_pipelines()
        return jsonify({"pipelines": pipelines})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/pull_image", methods=["POST"])
def pull_image():
    data = request.json
    image = data.get("image")
    engine = data.get("engine", "docker")

    if not image:
        return jsonify({"error": "No image name provided"}), 400

    try:
        _ensure_logs_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Use the same naming pattern so get_log picks it up
        log_file = DATA_DIR / f"nohup_bids_runner_pull_{timestamp}.log"

        print(f"[GUI] Pulling image: {image} using {engine}...", flush=True)
        if engine == "docker":
            cmd = ["docker", "pull", image]
        else:
            # For apptainer, pulling usually requires a destination path.
            # This is more complex so we might just focus on Docker for now
            # as requested by the user.
            return jsonify({"error": "Pull only implemented for Docker engine"}), 400

        # Run in background to not block
        def run_pull():
            try:
                with open(log_file, "w") as f:
                    f.write(
                        f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Started pulling {image}...\n\n"
                    )
                    f.flush()
                    print(f"[GUI] Pulling image: {image}...", flush=True)
                    # Use Popen to stream output to the log file (stdout)
                    process = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
                    )
                    for line in process.stdout:
                        if line.strip():
                            line_out = f"[DOCKER] {line.strip()}"
                            print(line_out, flush=True)
                            f.write(line_out + "\n")
                            f.flush()
                    process.wait()

                    if process.returncode == 0:
                        msg = f"\n[GUI] Successfully pulled {image}"
                        print(msg, flush=True)
                        f.write(msg + "\n")
                    else:
                        msg = f"\n[GUI] Docker pull failed for {image} with return code {process.returncode}"
                        print(msg, flush=True)
                        f.write(msg + "\n")
            except Exception as e:
                err_msg = f"\n[GUI] Error pulling {image}: {str(e)}"
                print(err_msg, flush=True)
                with open(log_file, "a") as f:
                    f.write(err_msg + "\n")

        threading.Thread(target=run_pull).start()

        return jsonify(
            {
                "message": f"Started pulling {image} in the background. Check console output."
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/make_dir", methods=["POST"])
def make_dir():
    data = request.json
    path = data.get("path")
    name = data.get("name")
    if not path or not name:
        return jsonify({"error": "Path and name are required"}), 400

    try:
        new_dir = Path(os.path.expanduser(path)) / name
        new_dir.mkdir(parents=True, exist_ok=True)
        return jsonify(
            {"message": f"Directory created: {new_dir}", "path": str(new_dir)}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/build_apptainer", methods=["POST"])
def build_apptainer():
    data = request.get_json(silent=True) or {}
    prepared, error_response = _prepare_apptainer_build(data)
    if error_response:
        return error_response

    build_id = f"build_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    with APPTAINER_BUILDS_LOCK:
        APPTAINER_BUILDS[build_id] = {
            "id": build_id,
            "status": "running",
            "cmd": prepared["cmd"],
            "log_file": prepared["log_file"],
            "output_image": prepared["output_image"],
            "per_build_dir": prepared["per_build_dir"],
            "keep_temp": prepared["keep_temp"],
            "timeout_seconds": prepared["timeout_seconds"],
            "cwd": prepared["cwd"],
            "env": prepared["env"],
            "process": None,
            "pid": None,
            "cancel_requested": False,
            "returncode": None,
            "error": None,
            "started_at": time.time(),
            "finished_at": None,
        }

    worker = threading.Thread(
        target=_run_apptainer_build_async,
        args=(build_id,),
        daemon=True,
    )
    worker.start()

    return jsonify(
        {
            "success": True,
            "build_id": build_id,
            "status": "running",
            "log_file": prepared["log_file"],
            "output_image": prepared["output_image"],
        }
    )


@app.route("/build_apptainer_status", methods=["GET"])
def build_apptainer_status():
    build_id = (request.args.get("build_id") or "").strip()
    if not build_id:
        return jsonify({"error": "build_id is required"}), 400

    with APPTAINER_BUILDS_LOCK:
        state = APPTAINER_BUILDS.get(build_id)
        if not state:
            return jsonify({"error": "Build not found"}), 404
        status = state.get("status", "unknown")
        returncode = state.get("returncode")
        output_image = state.get("output_image")
        log_file = state.get("log_file")
        error = state.get("error")
        pid = state.get("pid")

    log_tail = _read_log_tail(log_file)

    if not output_image and status in {"completed", "failed"}:
        match = re.search(r"Apptainer image built successfully at:\s*(.+)", log_tail)
        if match:
            output_image = match.group(1).strip()

    return jsonify(
        {
            "build_id": build_id,
            "status": status,
            "success": status == "completed",
            "returncode": returncode,
            "output_image": output_image,
            "log_file": log_file,
            "log_tail": log_tail,
            "error": error,
            "pid": pid,
        }
    )


@app.route("/build_apptainer_cancel", methods=["POST"])
def build_apptainer_cancel():
    data = request.get_json(silent=True) or {}
    build_id = (data.get("build_id") or "").strip()
    if not build_id:
        return jsonify({"error": "build_id is required"}), 400

    with APPTAINER_BUILDS_LOCK:
        state = APPTAINER_BUILDS.get(build_id)
        if not state:
            return jsonify({"error": "Build not found"}), 404
        status = state.get("status")
        process = state.get("process")

        if status != "running":
            return jsonify({"success": True, "status": status, "message": "Build already finished."})

        state["cancel_requested"] = True

    if process is not None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            pass

    return jsonify({"success": True, "status": "cancelling", "build_id": build_id})


@app.route("/get_app_help", methods=["POST"])
def get_app_help():
    container = request.json.get("container")
    engine = request.json.get("container_engine", "apptainer")

    if not container:
        return jsonify({"error": "Container name or path required"}), 400

    if engine == "apptainer" and not os.path.exists(container):
        return jsonify({"error": f"Apptainer image not found at: {container}"}), 400

    try:
        # Run container help
        print(f"[GUI] Fetching help for {container} using {engine}...", flush=True)
        if engine == "docker":
            # Check if image exists locally first to avoid long timeouts/auto-pulls
            inspect_cmd = ["docker", "image", "inspect", container]
            inspect_result = subprocess.run(inspect_cmd, capture_output=True)
            if inspect_result.returncode != 0:
                return (
                    jsonify(
                        {
                            "error": f'Docker image "{container}" not found locally. You must pull it before parameters can be analyzed.',
                            "need_pull": True,
                        }
                    ),
                    400,
                )
            if platform.system() == "Darwin" and platform.machine() == "arm64":
                cmd = [
                    "docker",
                    "run",
                    "--rm",
                    "--platform",
                    "linux/amd64",
                    container,
                    "--help",
                ]
            else:
                cmd = ["docker", "run", "--rm", container, "--help"]
        else:
            cmd = ["apptainer", "run", "--containall", container, "--help"]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        output = result.stdout + result.stderr

        usage_lines = []
        lines = output.splitlines()
        usage_started = False
        for line in lines:
            if not usage_started and line.strip().startswith("usage:"):
                usage_started = True
                usage_lines.append(line)
                continue
            if usage_started:
                if line.startswith(" "):
                    usage_lines.append(line)
                    continue
                break

        usage_block = "\n".join(usage_lines)
        usage_all_flags = set(re.findall(r"--[a-zA-Z0-9-]+", usage_block))
        usage_optional_flags = set(re.findall(r"\[\s*(--[a-zA-Z0-9-]+)", usage_block))
        usage_required_flags = usage_all_flags - usage_optional_flags

        deprecated_flags = set()
        for line in output.splitlines():
            lower_line = line.lower()
            if "deprecated" not in lower_line:
                continue
            for dep_flag in re.findall(r"--[a-zA-Z0-9-]+", line):
                deprecated_flags.add(dep_flag)

        # Compatibility guard: some apps still list legacy flags in --help
        # without marking them deprecated. If the modern replacement is present,
        # hide the legacy flag from dynamic UI generation.
        if (
            "--subject-anatomical-reference" in output
            and "--longitudinal" in output
        ):
            deprecated_flags.add("--longitudinal")

        if result.returncode != 0:
            print(
                f"[GUI] {engine.capitalize()} help returned exit code {result.returncode}",
                flush=True,
            )

        # IMPROVED: Clean up output (remove usage summary from the top to prevent it from confusing the parser)
        # Headers in argparse usually start with a Capitalized string followed by a colon.
        # We allow letters, numbers, spaces, and punctuation like hyphens or parentheses.
        parts = re.split(r"\n(?=[A-Z][A-Za-z0-9\-\(\) ]+:)", output)

        sections = []
        # Standard BIDS args to exclude (already handled by the GUI common section)
        exclude = {
            "--help",
            "--version",
            "--participant-label",
            "--space",
            "--bids-filter-file",
        }

        for part in parts:
            lines = part.strip().split("\n")
            if not lines:
                continue

            header = lines[0].strip().rstrip(":")

            # Skip usage/help summary sections
            if any(x in header.lower() for x in ["usage", "synopsis", "description"]):
                continue

            content = "\n".join(lines[1:])

            # Only process sections that look like they have definitions
            if "--" not in part:
                continue

            options = []
            # Split by flags that are at the beginning of a line (with 2+ spaces or start of block)
            arg_blocks = re.split(r"\n\s*(?=--)", "\n" + content)

            for block in arg_blocks:
                block = block.strip()
                if not block.startswith("--"):
                    # Try to find a flag anyway if it's not at the start
                    flag_match = re.search(r"(--[a-zA-Z0-9-]+)", block)
                    if not flag_match:
                        continue

                # Extract first flag found in the definition line
                flag_match = re.search(r"(--[a-zA-Z0-9-]+)", block)
                if not flag_match:
                    continue
                flag = flag_match.group(1)
                if flag in exclude:
                    continue
                if flag in deprecated_flags:
                    continue

                # Detect if it's already in options (sometimes flags ARE repeated in help)
                if any(o["flag"] == flag for o in options):
                    continue

                # Choices / Type detection
                choices = []
                choice_match = re.search(r"\{([^}]+)\}", block)
                if choice_match:
                    choices = [c.strip() for c in choice_match.group(1).split(",")]
                else:
                    choice_text_match = re.search(
                        r"Possible choices:\s*([^\n]+)", block
                    )
                    if choice_text_match:
                        # Split by comma or space and clean up
                        choices = [
                            c.strip().strip(",")
                            for c in re.split(r"[,\s]+", choice_text_match.group(1))
                        ]
                        choices = [c for c in choices if c and not c.startswith("-")]

                # Description: take everything AFTER the flag/metavar definition line
                block_lines = block.strip().split("\n")
                description = ""
                if len(block_lines) > 1:
                    # Often the first line contains the flag and maybe the metavar
                    # Everything from the second line onwards is description
                    # OR if there's only one line, the description might be after many spaces
                    description = " ".join([l.strip() for l in block_lines[1:]])
                elif "  " in block:
                    # Handle single line case: --flag METAVAR  description
                    parts_of_line = re.split(r"\s{2,}", block.strip())
                    if len(parts_of_line) > 1:
                        description = " ".join(parts_of_line[1:])

                description = re.sub(r"\s+", " ", description)
                # Cleanup common artifacts
                description = re.sub(r"\(default:.*?\)", "", description).strip()

                # DROP: Skip flags explicitly marked as deprecated
                if "deprecated" in description.lower():
                    continue

                # Determine whether this option takes a value by inspecting the *signature* column
                # of the first definition line, not the description text.
                # Example signatures:
                #   --fs-no-reconall
                #   --output-spaces OUTPUT_SPACES [OUTPUT_SPACES ...]
                #   --bids-filter-file FILE
                definition_line = block_lines[0] if block_lines else block
                # Split the line into columns: signature + description (2+ spaces)
                columns = re.split(r"\s{2,}", definition_line.strip())
                signature = columns[0] if columns else definition_line.strip()
                # Extract the signature portion beginning at the flag (handles leading text)
                sig_match = re.search(rf"{re.escape(flag)}(?:\s+[^\s].*)?$", signature)
                signature_tail = sig_match.group(0) if sig_match else signature
                sig_tokens = signature_tail.split()

                has_value = False
                if choices:
                    has_value = True
                elif sig_tokens and sig_tokens[0] == flag and len(sig_tokens) > 1:
                    has_value = True

                # Mark multi-value options (argparse prints "..." for nargs)
                is_multiple = bool(re.search(r"\[.*\.\.\..*\]|\.\.\.", signature_tail))

                # Clean up display name and identify negated flags
                display_name = flag.lstrip("-")
                is_negated = False
                # Common negation patterns in BIDS apps
                negation_match = re.search(
                    r"^(no-|skip[-_]|without-|fs-no-)(.*)", display_name
                )
                if negation_match and not has_value:
                    is_negated = True
                    # Use the positive part as the display name
                    display_name = negation_match.group(2)

                display_name = display_name.replace("-", " ").replace("_", " ").title()

                options.append(
                    {
                        "flag": flag,
                        "name": display_name,
                        "is_negated": is_negated,
                        "choices": choices,
                        "description": description,
                        "has_value": has_value,
                        "is_multiple": is_multiple,
                        "required": flag in usage_required_flags,
                    }
                )

            if options:
                sections.append(
                    {
                        "title": header,
                        "options": sorted(options, key=lambda x: x["name"]),
                    }
                )

        parsed_flags = {
            o.get("flag")
            for section in sections
            for o in section.get("options", [])
            if o.get("flag")
        }

        # Fallback for QSIPrep compatibility: if legacy longitudinal is hidden and
        # the replacement signature exists in help output but was not parsed,
        # add it explicitly so users can still select it in the UI.
        if (
            "--subject-anatomical-reference" not in parsed_flags
            and "--subject-anatomical-reference" in output
        ):
            choices = []
            choice_match = re.search(
                r"--subject-anatomical-reference\s+\{([^}]+)\}", output
            )
            if choice_match:
                choices = [
                    c.strip() for c in choice_match.group(1).split(",") if c.strip()
                ]

            fallback_option = {
                "flag": "--subject-anatomical-reference",
                "name": "Subject Anatomical Reference",
                "is_negated": False,
                "choices": choices,
                "description": "Replacement for deprecated --longitudinal behavior.",
                "has_value": True,
                "is_multiple": False,
                "required": "--subject-anatomical-reference" in usage_required_flags,
            }

            target_section = None
            for section in sections:
                if section.get("title", "").lower() == "workflow configuration":
                    target_section = section
                    break

            if target_section is None:
                sections.append(
                    {
                        "title": "Workflow configuration",
                        "options": [fallback_option],
                    }
                )
            else:
                target_section_options = target_section.get("options", [])
                target_section_options.append(fallback_option)
                target_section["options"] = sorted(
                    target_section_options, key=lambda x: x["name"]
                )

        # Identify app for Doc link
        app_name = "BIDS App"
        doc_url = "https://bids-apps.neuroimaging.io/"
        container_lower = os.path.basename(container).lower()
        if "qsiprep" in container_lower:
            app_name = "QSIPrep"
            doc_url = "https://qsiprep.readthedocs.io/"
        elif "fmriprep" in container_lower:
            app_name = "fMRIPrep"
            doc_url = "https://fmriprep.org/"
        elif "mriqc" in container_lower:
            app_name = "MRIQC"
            doc_url = "https://mriqc.readthedocs.io/"

        return jsonify(
            {
                "sections": sections,
                "app_info": {"name": app_name, "url": doc_url},
                "deprecated_flags": sorted(list(deprecated_flags)),
                "raw_help": output if not sections else None,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/get_templateflow_templates", methods=["POST"])
def get_templateflow_templates():
    tf_dir = request.json.get("path")
    if not tf_dir or not os.path.exists(tf_dir):
        return jsonify({"error": "TemplateFlow directory not found"}), 400

    try:
        templates = []
        # Templates are usually in folders named tpl-<TemplateName>
        for entry in os.scandir(tf_dir):
            if entry.is_dir() and entry.name.startswith("tpl-"):
                template_name = entry.name[4:]
                # Check for resolutions/cohorts inside
                resolutions = set()
                res_pattern = re.compile(r"res-([a-zA-Z0-9]+)")

                # Walk a bit to find resolution files (e.g., in tpl-MNI/...)
                # Limit depth to avoid massive hangs
                try:
                    for root, dirs, files in os.walk(entry.path):
                        if root.count(os.sep) - entry.path.count(os.sep) > 2:
                            continue
                        for f in files:
                            match = res_pattern.search(f)
                            if match:
                                resolutions.add(match.group(1))
                        if len(resolutions) > 20:
                            break
                except:
                    pass

                templates.append(
                    {"name": template_name, "resolutions": sorted(list(resolutions))}
                )

        return jsonify({"templates": sorted(templates, key=lambda x: x["name"])})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/list_dirs", methods=["POST"])
def list_dirs():
    payload = request.json or {}
    path = payload.get("path", "/")
    include_files = bool(payload.get("include_files"))
    include_hidden = bool(payload.get("include_hidden"))
    extensions = payload.get("extensions") or []
    file_name = payload.get("file_name") or ""
    if not path:
        path = "/"

    try:
        p = Path(path)
        if p.exists() and p.is_file():
            p = p.parent
        if not p.exists() or not p.is_dir():
            # Try to go to parent or root if path is invalid
            p = Path("/")

        items = []
        # Add parent directory entry
        if p.parent != p:
            items.append({"name": "..", "path": str(p.parent), "is_dir": True})

        for child in sorted(p.iterdir()):
            if child.is_dir() and (include_hidden or not child.name.startswith(".")):
                items.append(
                    {"name": child.name, "path": str(child.absolute()), "is_dir": True}
                )
            elif include_files and child.is_file() and (include_hidden or not child.name.startswith(".")):
                if file_name and child.name != file_name:
                    continue
                if extensions:
                    if not any(child.name.endswith(ext) for ext in extensions):
                        continue
                items.append(
                    {"name": child.name, "path": str(child.absolute()), "is_dir": False}
                )
        return jsonify({"current_path": str(p.absolute()), "items": items})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/load_project_file", methods=["POST"])
def load_project_file():
    """Load a project.json from an explicit file path."""
    try:
        payload = request.json or {}
        path = payload.get("path", "").strip()
        if not path:
            return jsonify({"error": "Path is required"}), 400

        p = Path(path)
        if not p.exists() or not p.is_file():
            return jsonify({"error": "File not found"}), 404

        if p.name != "project.json":
            return jsonify({"error": "Please select a project.json file"}), 400

        with open(p, "r") as f:
            project_json = json.load(f)

        return jsonify(project_json), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/")
def index():
    try:
        return render_template("index.html")
    except Exception as e:
        print(f"[DEBUG] Template error: {e}", flush=True)
        return str(e), 500


@app.route("/list_containers", methods=["POST"])
def list_containers():
    folder = request.json.get("folder")
    if not folder:
        return jsonify({"error": "No folder provided"}), 400

    try:
        # Expand user path if needed
        folder_path = os.path.expanduser(folder)
        # Search for .sif and .simg files
        containers = glob.glob(os.path.join(folder_path, "*.sif")) + glob.glob(
            os.path.join(folder_path, "*.simg")
        )
        containers = [os.path.basename(c) for c in containers]
        return jsonify({"containers": sorted(containers)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/list_configs", methods=["GET"])
def list_configs():
    try:
        combined_configs = set()
        # Defaults
        default_dir = BASE_DIR / "configs"
        if default_dir.exists():
            combined_configs.update(
                [f for f in os.listdir(default_dir) if f.endswith(".json")]
            )

        # User configs
        user_dir = DATA_DIR / "configs"
        if user_dir.exists():
            combined_configs.update(
                [f for f in os.listdir(user_dir) if f.endswith(".json")]
            )

        return jsonify({"configs": sorted(list(combined_configs))})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/get_config", methods=["GET"])
def get_config():
    name = request.args.get("name")
    if not name:
        return jsonify({"error": "No name provided"}), 400
    try:
        # Check user dir first
        config_path = DATA_DIR / "configs" / name
        if not config_path.exists():
            # Check default dir
            config_path = BASE_DIR / "configs" / name

        with open(config_path, "r") as f:
            data = json.load(f)
        return jsonify({"config": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/save_config", methods=["POST"])
def save_config():
    data = request.json
    filename = data.get("filename", "config.json")
    config_data = data.get("config")
    save_dir = data.get("config_folder", "").strip()
    project_id = data.get("project_id")  # NEW: project context

    if not config_data:
        return jsonify({"error": "No config data provided"}), 400

    try:
        # If project_id provided, save to project
        if project_id:
            success = ProjectManager.save_project(project_id, config_data)
            if not success:
                return jsonify({"error": f"Project {project_id} not found"}), 404

            project = ProjectManager.load_project(project_id)
            config_path = f"projects/{project_id}/project.json"
            return jsonify(
                {
                    "message": "Project config saved successfully",
                    "path": config_path,
                    "project": project,
                }
            )

        # Otherwise, save as standalone config file (backward compatibility)
        # Ensure filename ends with .json
        if not filename.endswith(".json"):
            filename += ".json"

        if save_dir:
            config_path = Path(os.path.expanduser(save_dir)) / filename
        else:
            config_path = DATA_DIR / "configs" / filename

        os.makedirs(config_path.parent, exist_ok=True)

        with open(config_path, "w") as f:
            json.dump(config_data, f, indent=2)

        return jsonify(
            {
                "message": f"Config saved successfully to {config_path}",
                "path": str(config_path),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
        if container_path == target_norm or container_path.startswith(target_norm + "/"):
            rel = container_path[len(target_norm):].lstrip("/")
            return os.path.join(source, rel) if rel else source
    return None


@app.route("/run_app", methods=["POST"])
def run_app():
    global GUI_SESSION_STARTED
    data = request.get_json(silent=True) or {}
    config_path = data.get("config_path")
    project_id = data.get("project_id")  # NEW: project context
    runner_args = data.get("runner_args", [])
    notify_email = (data.get("notify_email") or "").strip()
    if not config_path:
        return jsonify({"error": "No config path provided"}), 400

    try:
        # 1. Path Validation
        with open(config_path, "r") as f:
            cfg = json.load(f)

        common = cfg.get("common", {})
        if not notify_email:
            notify_email = str(common.get("notify_email", "")).strip()

        if notify_email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", notify_email):
            return jsonify({"error": "Notification email format is invalid."}), 400

        paths_to_check = {
            "BIDS Folder": common.get("bids_folder"),
            "Container Image": common.get("container"),
            "Templateflow Folder": common.get("templateflow_dir"),
        }

        if common.get("fs_license_file"):
            paths_to_check["FreeSurfer License File"] = common.get("fs_license_file")

        missing = []
        for name, path in paths_to_check.items():
            if path and not os.path.exists(path):
                missing.append(f"{name}: {path}")

        if missing:
            return (
                jsonify(
                    {
                        "error": "Validation Failed",
                        "details": "The following paths do not exist:\n"
                        + "\n".join(missing),
                    }
                ),
                400,
            )

        # FreeSurfer license preflight for fMRIPrep
        app_cfg = cfg.get("app", {})
        options = app_cfg.get("options", [])
        mounts = app_cfg.get("mounts", [])
        container_name = str(common.get("container", "")).lower()

        if "fmriprep" in container_name or "qsiprep" in container_name:
            fs_license_path = common.get("fs_license_file")
            fs_license_arg = _extract_fs_license_path(options)

            if not fs_license_path and not fs_license_arg:
                return (
                    jsonify(
                        {
                            "error": "FreeSurfer license required",
                            "details": "fMRIPrep/QSIPrep requires a FreeSurfer license. Provide it in the FreeSurfer License File field or add custom args: --fs-license-file /fs/license.txt with an appropriate bind mount.",
                        }
                    ),
                    400,
                )

            if fs_license_path and not os.path.exists(fs_license_path):
                return (
                    jsonify(
                        {
                            "error": "FreeSurfer license file not found",
                            "details": f"FreeSurfer license file not found at {fs_license_path}.",
                        }
                    ),
                    400,
                )

            if fs_license_arg and fs_license_arg.startswith("/"):
                host_license = _map_container_path_to_host(fs_license_arg, mounts)
                if host_license is None:
                    return (
                        jsonify(
                            {
                                "error": "FreeSurfer license path not mounted",
                                "details": f"--fs-license-file {fs_license_arg} is not under any custom mount target. Add a mount that makes the license file available in the container.",
                            }
                        ),
                        400,
                    )
                if not os.path.exists(host_license):
                    return (
                        jsonify(
                            {
                                "error": "FreeSurfer license file not found",
                                "details": f"--fs-license-file points to {fs_license_arg} but host file not found at {host_license}. Check custom mounts/args.",
                            }
                        ),
                        400,
                    )

        # Check container engine availability
        engine = common.get("container_engine", "apptainer")
        if engine == "docker":
            if not shutil.which("docker"):
                return (
                    jsonify({"error": "Docker requested but not found on system."}),
                    400,
                )
            try:
                subprocess.run(
                    ["docker", "info"], capture_output=True, timeout=2, check=True
                )
            except (subprocess.SubprocessError, FileNotFoundError):
                return (
                    jsonify(
                        {
                            "error": "Docker is installed but the DAEMON IS NOT RUNNING. Please start Docker Desktop."
                        }
                    ),
                    400,
                )
        elif engine == "apptainer" and not (
            shutil.which("apptainer") or shutil.which("singularity")
        ):
            return (
                jsonify(
                    {
                        "error": "Apptainer/Singularity requested but not found on system."
                    }
                ),
                400,
            )

        # 2. Determine working directory (project-specific if available)
        if project_id:
            work_dir = PROJECTS_DIR / project_id / "logs"
            work_dir.mkdir(parents=True, exist_ok=True)
        else:
            work_dir = DATA_DIR

        # 2. Launch run_bids_apps.py in background
        if getattr(sys, "frozen", False):
            script_path = BUNDLE_DIR / "scripts" / "run_bids_apps.py"
        else:
            script_path = BASE_DIR / "scripts" / "run_bids_apps.py"

        # Build command - use ABSOLUTE paths to avoid working directory issues
        abs_config_path = os.path.abspath(config_path)

        cmd = [
            PYTHON_EXE,
            str(script_path),
            "-c",
            abs_config_path,
        ]

        # Append runner arguments from UI
        if runner_args:
            cmd.extend(runner_args)

        # Generate log filename and create output redirection
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_filename = f"run_{timestamp}.log"
        log_file_path = work_dir / log_filename
        
        print(f"[GUI] Executing: {' '.join(cmd)} in {work_dir}")
        print(f"[GUI] Logging output to: {log_file_path}")
        
        # Open log file and redirect both stdout and stderr to it
        with open(log_file_path, "w") as log_f:
            # Write command that's being executed for reference
            log_f.write(f"[{datetime.now().isoformat()}] Executing: {' '.join(cmd)}\n")
            log_f.write(f"[{datetime.now().isoformat()}] Working directory: {work_dir}\n")
            log_f.write("=" * 80 + "\n\n")
            log_f.flush()

            launch_env = os.environ.copy()
            launch_env[APP_LAUNCH_ENV_KEY] = APP_LAUNCH_ENV_VALUE
            
            # Launch subprocess with output redirected to log file
            process = subprocess.Popen(
                cmd, 
                cwd=str(work_dir),
                stdout=log_f,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setpgrp,  # Detach from terminal group
                env=launch_env,
            )

        run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        with RUN_JOBS_LOCK:
            RUN_JOBS[run_id] = {
                "id": run_id,
                "process": process,
                "pid": process.pid,
                "project_id": project_id,
                "log_file": str(log_file_path),
                "started_at": time.time(),
                "cmd": cmd,
                "notify_email": notify_email,
                "stop_requested": False,
                "returncode": None,
                "finished_at": None,
            }

        monitor_thread = threading.Thread(
            target=_monitor_run_job,
            args=(run_id,),
            daemon=True,
        )
        monitor_thread.start()

        # Update project's last_log if project_id is provided
        if project_id:
            ProjectManager.update_project_log(project_id, log_filename)

        GUI_SESSION_STARTED = True

        return jsonify(
            {
                "message": (
                    f"BIDS App Runner started in background. Command: {' '.join(cmd)}"
                    + (
                        f". Completion email will be sent to {notify_email}."
                        if notify_email
                        else ""
                    )
                ),
                "run_id": run_id,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/kill_job", methods=["POST"])
def kill_job():
    # Stop current run or all runs
    try:
        data = request.get_json(silent=True) or {}
        scope = str(data.get("scope", "current")).strip().lower()
        if scope not in {"current", "all"}:
            return jsonify({"error": "Invalid scope. Use 'current' or 'all'."}), 400

        tracked_jobs = _get_active_tracked_run_jobs()
        killed = 0

        if scope == "current":
            target = None
            if tracked_jobs:
                target = max(tracked_jobs, key=lambda state: state.get("started_at", 0))

            if target is not None:
                if _terminate_tracked_run(target):
                    killed += 1
            else:
                # Fallback for runs not tracked in current GUI memory.
                candidate_pids = sorted(_find_app_related_pids(include_marked=True))
                if candidate_pids:
                    newest_pid = max(candidate_pids)
                    if _terminate_pid_group(newest_pid):
                        killed += 1

            if killed == 0:
                return jsonify({"message": "No active BIDS App Runner process found to stop."}), 200

            return jsonify({"message": "Stop signal sent to current run."}), 200

        # scope == "all"
        for state in tracked_jobs:
            if _terminate_tracked_run(state):
                killed += 1

        # Include active app-initiated apptainer builds.
        build_pids = []
        with APPTAINER_BUILDS_LOCK:
            for state in APPTAINER_BUILDS.values():
                process = state.get("process")
                if process is not None and process.poll() is None:
                    build_pids.append(process.pid)
                    _terminate_tracked_build(state)

        killed += _terminate_pid_groups(build_pids)

        # Catch untracked app-related processes (including inherited container children).
        killed += _terminate_pid_groups(_find_app_related_pids(include_marked=True))

        if killed == 0:
            return jsonify({"message": "No active BIDS App Runner processes found."}), 200

        return jsonify({"message": f"Stop signal sent to all runs ({killed} process target(s))."}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/shutdown", methods=["POST"])
def shutdown():
    """Shutdown the Flask server when requested by the frontend."""
    print("[GUI] Shutdown requested via web interface", flush=True)

    def kill_server():
        time.sleep(1)
        os._exit(0)

    threading.Thread(target=kill_server).start()
    return jsonify(success=True)


# ============================================================================
# HPC/DataLad Endpoints
# ============================================================================


@app.route("/check_hpc_environment", methods=["GET"])
def check_hpc_environment():
    """Check if HPC tools are available."""
    return jsonify(
        {
            "slurm": shutil.which("sbatch") is not None,
            "datalad": shutil.which("datalad") is not None,
            "git": shutil.which("git") is not None,
            "git_annex": shutil.which("git-annex") is not None,
            "apptainer": shutil.which("apptainer") is not None,
            "singularity": shutil.which("singularity") is not None,
            "hpc_datalad_available": HPC_DATALAD_AVAILABLE,
        }
    )


@app.route("/generate_hpc_script", methods=["POST"])
def generate_hpc_script():
    """Legacy endpoint - script generation now happens client-side."""
    return jsonify({"error": "Script generation is now handled client-side"}), 400



@app.route("/save_hpc_script", methods=["POST"])
def save_hpc_script():
    """Save a generated HPC script to disk."""
    data = request.json
    script_content = data.get("script")
    subject = data.get("subject")
    output_dir = data.get("output_dir", "/tmp/hpc_scripts")

    if not script_content or not subject:
        return jsonify({"error": "script and subject are required"}), 400

    try:
        output_path = Path(output_dir) / f"job_{subject}.sh"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w") as f:
            f.write(script_content)

        os.chmod(output_path, 0o755)

        return jsonify(
            {"message": f"Script saved to {output_path}", "path": str(output_path)}
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/submit_hpc_job", methods=["POST"])
def submit_hpc_job():
    """Submit a SLURM job script."""
    data = request.json
    script_path = data.get("script_path")
    dry_run = data.get("dry_run", False)

    if not script_path:
        return jsonify({"error": "script_path is required"}), 400

    if not os.path.exists(script_path):
        return jsonify({"error": f"Script not found: {script_path}"}), 400

    try:
        if dry_run:
            return jsonify(
                {
                    "message": "DRY RUN - Would submit job",
                    "command": f"sbatch {script_path}",
                    "job_id": "DRY_RUN_JOB_ID",
                }
            )

        # Submit job
        cmd = ["sbatch", script_path]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)

        output = result.stdout.strip()
        if "Submitted batch job" in output:
            job_id = output.split()[-1]
            logging.info(f"[GUI] Submitted HPC job {job_id}: {script_path}")
            return jsonify(
                {
                    "message": "Job submitted successfully",
                    "job_id": job_id,
                    "command": " ".join(cmd),
                }
            )
        else:
            return jsonify({"error": f"Failed to parse job ID: {output}"}), 500

    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"Failed to submit job: {e.stderr}"}), 500
    except FileNotFoundError:
        return (
            jsonify({"error": "sbatch not found - SLURM not available on this system"}),
            400,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/get_hpc_job_status", methods=["POST"])
def get_hpc_job_status():
    """Get status of SLURM jobs."""
    data = request.json
    job_ids = data.get("job_ids", [])

    if not job_ids:
        return jsonify({"error": "job_ids required"}), 400

    try:
        cmd = ["squeue", "-j", ",".join(job_ids), "--format=%i,%T,%M,%e"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)

        jobs = []
        for line in result.stdout.strip().split("\n")[1:]:  # Skip header
            if line:
                parts = line.split(",")
                if len(parts) >= 4:
                    jobs.append(
                        {
                            "job_id": parts[0],
                            "status": parts[1],
                            "time": parts[2],
                            "end_time": parts[3] if len(parts) > 3 else "",
                        }
                    )

        return jsonify({"jobs": jobs})

    except FileNotFoundError:
        return jsonify({"error": "squeue not found - SLURM not available"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/cancel_hpc_job", methods=["POST"])
def cancel_hpc_job():
    """Cancel a SLURM job."""
    data = request.json
    job_id = data.get("job_id")

    if not job_id:
        return jsonify({"error": "job_id is required"}), 400

    try:
        cmd = ["scancel", job_id]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)

        return jsonify({"message": f"Job {job_id} cancelled", "job_id": job_id})

    except subprocess.CalledProcessError as e:
        return jsonify({"error": f"Failed to cancel job: {e.stderr}"}), 500
    except FileNotFoundError:
        return jsonify({"error": "scancel not found - SLURM not available"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    import socket

    port = 8080
    max_tries = 20

    # Simple loop to find an available port
    for _ in range(max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("0.0.0.0", port)) != 0:
                # Port is available
                break
            else:
                port += 1

    print(f"🌐 Starting BIDS App Runner GUI v{__version__}")
    print(f"🔗 URL: http://localhost:{port}")
    print("💡 Press Ctrl+C to stop the server\n")
    print(f"🚀 Running with Waitress server on 0.0.0.0:{port}")

    # Automatically open the browser after a short delay
    def open_browser():
        webbrowser.open(f"http://localhost:{port}")
        print("✅ Browser opened automatically")

    threading.Timer(1, open_browser).start()

    serve(app, host="0.0.0.0", port=port, threads=4)
