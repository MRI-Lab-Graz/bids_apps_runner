import ipaddress
import os
import re
import secrets
from pathlib import Path
from typing import Any

from werkzeug.security import generate_password_hash

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_CONFIG_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.json$")
_TRUE_VALUES = {"1", "true", "yes", "on"}


def path_is_within(path: Path, base_dir: Path) -> bool:
    try:
        path.relative_to(base_dir)
        return True
    except ValueError:
        return False


def is_loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().strip("[]")
    if not normalized:
        return False
    if normalized in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def request_is_loopback(remote_addr: str) -> bool:
    normalized = str(remote_addr or "").strip().split("%", 1)[0]
    if not normalized:
        return False
    if normalized in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def load_or_create_secret_key(data_dir: Path, env_var: str = "PRISM_SECRET_KEY") -> str:
    env_key = (os.environ.get(env_var) or "").strip()
    if env_key:
        return env_key

    secret_path = data_dir / ".secret_key"
    try:
        if secret_path.exists():
            existing = secret_path.read_text(encoding="utf-8").strip()
            if existing:
                return existing

        data_dir.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_urlsafe(48)
        secret_path.write_text(generated, encoding="utf-8")
        try:
            os.chmod(secret_path, 0o600)
        except OSError:
            pass
        return generated
    except OSError:
        return secrets.token_urlsafe(48)


def load_gui_password_config(
    password_env: str = "PRISM_GUI_PASSWORD",
    password_hash_env: str = "PRISM_GUI_PASSWORD_HASH",
    disable_env: str = "PRISM_GUI_DISABLE_LOGIN",
) -> dict[str, Any]:
    disabled = str(os.environ.get(disable_env) or "").strip().lower() in _TRUE_VALUES
    if disabled:
        return {
            "enabled": False,
            "password_hash": "",
            "bootstrap_password": None,
            "source": "disabled",
        }

    password_hash = str(os.environ.get(password_hash_env) or "").strip()
    if password_hash:
        return {
            "enabled": True,
            "password_hash": password_hash,
            "bootstrap_password": None,
            "source": "env-hash",
        }

    password = os.environ.get(password_env)
    if password and str(password).strip():
        return {
            "enabled": True,
            "password_hash": generate_password_hash(str(password)),
            "bootstrap_password": None,
            "source": "env-password",
        }

    return {
        "enabled": False,
        "password_hash": "",
        "bootstrap_password": None,
        "source": "default-disabled",
    }


def normalize_project_id(project_id: Any) -> str:
    normalized = str(project_id or "").strip()
    if not _PROJECT_ID_PATTERN.fullmatch(normalized):
        raise ValueError("Invalid project id")
    return normalized


def resolve_project_dir(projects_dir: Path, project_id: Any) -> Path:
    base_dir = projects_dir.resolve()
    project_dir = (base_dir / normalize_project_id(project_id)).resolve()
    if not path_is_within(project_dir, base_dir):
        raise ValueError("Invalid project id")
    return project_dir


def normalize_json_filename(name: Any, default: str = "config.json") -> str:
    filename = str(name or default).strip() or default
    if "/" in filename or "\\" in filename:
        raise ValueError("Filename must not contain path separators")
    if not filename.endswith(".json"):
        filename += ".json"
    if not _CONFIG_FILENAME_PATTERN.fullmatch(filename):
        raise ValueError("Invalid config filename")
    return filename


def resolve_named_config_path(root_dir: Path, filename: str) -> Path:
    root_dir = root_dir.resolve()
    config_path = (root_dir / filename).resolve()
    if not path_is_within(config_path, root_dir):
        raise ValueError("Invalid config path")
    return config_path


def resolve_config_storage_dir(
    directory: str,
    data_config_dir: Path,
    base_config_dir: Path,
) -> Path:
    if not directory:
        return data_config_dir.resolve()

    requested = Path(os.path.expanduser(str(directory))).resolve()
    allowed_roots = []
    for root in (data_config_dir, base_config_dir):
        try:
            allowed_roots.append(root.resolve())
        except OSError:
            continue

    if any(
        path_is_within(requested, root) or requested == root for root in allowed_roots
    ):
        return requested

    raise ValueError("Config folder must stay inside the runner config directories")
