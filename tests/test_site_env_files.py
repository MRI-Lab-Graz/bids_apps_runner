"""apply_site_env_files: site.env settings, loaded by the app itself so that
`python prism_app_runner.py` and start_gui.sh behave the same.

Order (first one set wins): variables already in the environment, then the
user's ~/.config/bids_apps_runner/site.env, then <repo>/site.env. The files are
data, not code: plain KEY=VALUE lines with a PRISM_ key only."""

from gui.site_config import apply_site_env_files


def _apply(tmp_path, repo_env=None, user_env=None, env=None):
    repo, home = tmp_path / "repo", tmp_path / "home"
    (home / ".config" / "bids_apps_runner").mkdir(parents=True)
    repo.mkdir()
    if repo_env is not None:
        (repo / "site.env").write_text(repo_env)
    if user_env is not None:
        (home / ".config" / "bids_apps_runner" / "site.env").write_text(user_env)
    env = {} if env is None else env
    apply_site_env_files(env, repo, home)
    return env


def test_no_files_changes_nothing(tmp_path):
    assert _apply(tmp_path) == {}


def test_the_repo_file_is_loaded(tmp_path):
    env = _apply(tmp_path, repo_env="PRISM_REMOTE_SSH_HOST=lab-server\nPRISM_REMOTE_BASE_PATH=/srv/x\n")
    assert env == {"PRISM_REMOTE_SSH_HOST": "lab-server", "PRISM_REMOTE_BASE_PATH": "/srv/x"}


def test_the_users_own_file_overrides_the_repo_file(tmp_path):
    env = _apply(tmp_path, repo_env="PRISM_REMOTE_SSH_HOST=lab-server\n", user_env="PRISM_REMOTE_SSH_HOST=mine\n")
    assert env["PRISM_REMOTE_SSH_HOST"] == "mine"


def test_an_already_set_variable_beats_both_files(tmp_path):
    env = _apply(tmp_path, repo_env="PRISM_REMOTE_SSH_HOST=lab-server\n", env={"PRISM_REMOTE_SSH_HOST": "explicit"})
    assert env["PRISM_REMOTE_SSH_HOST"] == "explicit"


def test_comments_blank_lines_quotes_and_foreign_keys(tmp_path):
    env = _apply(
        tmp_path,
        repo_env='# a comment\n\nPRISM_SCRATCH_ROOT="/scratch/a b"\n  PRISM_DATA_DIR = /d \nPATH=/evil\nHOME=/evil\n',
    )
    assert env == {"PRISM_SCRATCH_ROOT": "/scratch/a b", "PRISM_DATA_DIR": "/d"}


def test_values_are_data_not_code(tmp_path):
    env = _apply(tmp_path, repo_env="PRISM_REMOTE_SSH_HOST=$(touch /tmp/pwned)\n")
    assert env["PRISM_REMOTE_SSH_HOST"] == "$(touch /tmp/pwned)"


def test_an_unreadable_or_missing_file_is_ignored(tmp_path):
    repo = tmp_path / "nope"
    env = {}
    apply_site_env_files(env, repo, tmp_path / "no_home")
    assert env == {}


def test_the_app_loads_site_env_before_the_gui_modules_read_their_settings(tmp_path):
    # Settings are read at import time, so the files must be applied first --
    # otherwise `python prism_app_runner.py` (no start_gui.sh) silently ignores
    # site.env. A real import in a clean environment is the only honest check.
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    home = tmp_path / "home"
    (home / ".config" / "bids_apps_runner").mkdir(parents=True)
    (home / ".config" / "bids_apps_runner" / "site.env").write_text(
        "PRISM_REMOTE_SSH_HOST=from-user-file\nPRISM_REMOTE_BASE_PATH=/srv/from-file\n"
        f"PRISM_DATA_DIR={tmp_path / 'data'}\n"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("PRISM_")}
    env["HOME"] = str(home)
    # A different HOME must not hide the interpreter's user-level packages.
    import site

    env["PYTHONUSERBASE"] = site.getuserbase()
    out = subprocess.run(
        [sys.executable, "-c", "import prism_app_runner as p; print(p.SITE['remote_ssh_host'], p.DATA_DIR)"],
        cwd=root, env=env, capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split()[-2:] == ["from-user-file", str(tmp_path / "data")]
