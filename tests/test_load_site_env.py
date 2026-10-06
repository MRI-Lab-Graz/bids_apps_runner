"""scripts/load_site_env.sh: start_gui.sh's way of picking up site settings.

Order (later wins): repo site.env, then the user's own
~/.config/bids_apps_runner/site.env, then variables already in the
environment (explicit always beats a file)."""

import subprocess
from pathlib import Path

LOADER = Path(__file__).resolve().parent.parent / "scripts" / "load_site_env.sh"


def _load(tmp_path, repo_env=None, user_env=None, preset=""):
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    (repo).mkdir(exist_ok=True)
    (home / ".config" / "bids_apps_runner").mkdir(parents=True, exist_ok=True)
    if repo_env is not None:
        (repo / "site.env").write_text(repo_env)
    if user_env is not None:
        (home / ".config" / "bids_apps_runner" / "site.env").write_text(user_env)
    script = f'{preset}\nsource "{LOADER}" "{repo}"\necho "host=${{PRISM_REMOTE_SSH_HOST-unset}} base=${{PRISM_REMOTE_BASE_PATH-unset}}"'
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_no_files_changes_nothing(tmp_path):
    assert _load(tmp_path) == "host=unset base=unset"


def test_the_repo_file_is_loaded(tmp_path):
    assert _load(tmp_path, repo_env="PRISM_REMOTE_SSH_HOST=lab-server\nPRISM_REMOTE_BASE_PATH=/srv/x\n") == "host=lab-server base=/srv/x"


def test_the_users_own_file_overrides_the_repo_file(tmp_path):
    out = _load(tmp_path, repo_env="PRISM_REMOTE_SSH_HOST=lab-server\n", user_env="PRISM_REMOTE_SSH_HOST=mine\n")
    assert out.startswith("host=mine")


def test_an_already_exported_variable_beats_both_files(tmp_path):
    out = _load(tmp_path, repo_env="PRISM_REMOTE_SSH_HOST=lab-server\n", preset="export PRISM_REMOTE_SSH_HOST=explicit")
    assert out.startswith("host=explicit")


def test_values_are_data_not_code(tmp_path):
    # A site.env is sourced as plain KEY=VALUE lines only; command substitution
    # or other shell must not run.
    marker = tmp_path / "pwned"
    out = _load(tmp_path, repo_env=f"PRISM_REMOTE_SSH_HOST=$(touch {marker})\nPRISM_REMOTE_BASE_PATH=/ok\n")
    assert not marker.exists()
    assert out.endswith("base=/ok")
