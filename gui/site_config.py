"""Site-specific settings: where scratch lives, which DataLad server to use,
where the GUI keeps its own data. Everything comes from the environment (or
`site.env`, which start_gui.sh sources), with defaults rooted at the *current
user's* scratch -- never at another user's or a lab-shared folder.
"""

import getpass
import os
import re
from pathlib import Path
from typing import Mapping

# ssh host/alias: no leading '-' (option injection), no spaces or shell syntax.
_SSH_HOST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]*$")


def load_site_config(env: Mapping[str, str] | None = None) -> dict[str, str]:
    env = os.environ if env is None else env

    def get(name: str) -> str:
        return str(env.get(name) or "").strip()

    user = get("USER") or getpass.getuser()
    scratch = get("PRISM_SCRATCH_ROOT").rstrip("/") or f"/cl_tmp/{user}"
    return {
        "scratch_root": scratch,
        "remote_ssh_host": get("PRISM_REMOTE_SSH_HOST"),
        "remote_base_path": get("PRISM_REMOTE_BASE_PATH").rstrip("/"),
        "local_dataset_base_dir": get("PRISM_LOCAL_DATASET_BASE_DIR").rstrip("/")
        or f"{scratch}/datasets",
        "cohort_log_base_dir": get("PRISM_COHORT_LOG_BASE_DIR").rstrip("/")
        or f"{scratch}/bids_apps_runner_cohort_logs",
    }


def remote_configured(site: Mapping[str, str]) -> bool:
    """True when a remote DataLad server is set up (a usable host + base path)."""
    host = site.get("remote_ssh_host") or ""
    return bool(_SSH_HOST_PATTERN.match(host) and site.get("remote_base_path"))


def pick_data_dir(
    base_dir: Path,
    env: Mapping[str, str],
    home: Path,
    system: str,
    uid: int | None = None,
) -> Path:
    """Where the GUI keeps projects, logs and its secret key.

    PRISM_DATA_DIR wins. A checkout that already holds its own `projects/`
    keeps using itself (single-user checkouts, the lab's existing data).
    Anything else -- including a group-writable install shared by many users,
    where "can I write here?" is true for everybody -- uses a per-user folder,
    so users never share projects or a secret key.
    """
    explicit = str(env.get("PRISM_DATA_DIR") or "").strip()
    if explicit:
        return Path(explicit).expanduser()

    uid = os.getuid() if uid is None else uid
    projects = base_dir / "projects"
    try:
        if projects.is_dir() and projects.stat().st_uid == uid and os.access(projects, os.W_OK):
            return base_dir
    except OSError:
        pass

    if system == "Darwin":
        return home / "Library" / "Application Support" / "BIDSAppsRunner"
    return home / ".bids_apps_runner"


def datalad_url_templates(site: Mapping[str, str], app_name: str) -> dict[str, str]:
    """Input/output DataLad URL templates for the cohort config. Refuses an
    unconfigured server rather than emit a broken URL that fails per dataset."""
    if not remote_configured(site):
        raise ValueError(
            "No remote DataLad server configured: set PRISM_REMOTE_SSH_HOST (an ssh "
            "alias from ~/.ssh/config) and PRISM_REMOTE_BASE_PATH (see site.env.example)."
        )
    host, base = site["remote_ssh_host"], site["remote_base_path"].lstrip("/")
    return {
        "input_url_template": f"{host}:/{base}/{{dataset_id}}",
        "output_url_template": f"ssh://{host}/{base}/{{dataset_id}}/derivatives/{app_name}",
    }


_ENV_LINE = re.compile(r"^\s*(PRISM_[A-Z0-9_]+)\s*=\s*(.*?)\s*$")


def _read_site_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        match = _ENV_LINE.match(line)  # comments/blank/foreign keys don't match
        if not match:
            continue
        key, value = match.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values.setdefault(key, value)
    return values


def apply_site_env_files(env: dict[str, str], repo_dir: Path, home: Path) -> None:
    """Fill `env` from site.env files without overriding anything already set.

    Precedence (first set wins): what is already in `env`, then
    ~/.config/bids_apps_runner/site.env, then <repo_dir>/site.env. The files
    are data, not code: only `PRISM_*=value` lines are read, never executed.
    """
    for path in (home / ".config" / "bids_apps_runner" / "site.env", repo_dir / "site.env"):
        for key, value in _read_site_env(path).items():
            env.setdefault(key, value)
