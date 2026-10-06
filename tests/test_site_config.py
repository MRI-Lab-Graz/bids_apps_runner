"""Site-specific values come from the environment, with per-user defaults.

This repo is going to other users of the university. It used to hardwire one
lab: the `datalad-server` SSH alias, `/datalad/mri/MRI-Lab_Repository`,
`/cl_tmp/mrilab` (a shared lab directory) and `/cl_tmp/mrilabgraz/...` (one
person's scratch). Another user would have had their cohort logs written into
someone else's folder. Defaults are now rooted at the *current user's* scratch;
the lab-specific values live in `site.env`, not in the code.
"""

import os
from pathlib import Path

import pytest

from gui.site_config import load_site_config, pick_data_dir, remote_configured

ENV = {"USER": "alice"}


def test_defaults_are_rooted_at_the_current_users_scratch_never_another_user():
    site = load_site_config(ENV)

    assert site["scratch_root"] == "/cl_tmp/alice"
    assert site["local_dataset_base_dir"] == "/cl_tmp/alice/datasets"
    assert site["cohort_log_base_dir"] == "/cl_tmp/alice/bids_apps_runner_cohort_logs"
    assert "mrilab" not in "".join(str(v) for v in site.values())


def test_no_remote_server_is_assumed():
    site = load_site_config(ENV)
    assert site["remote_ssh_host"] == ""
    assert site["remote_base_path"] == ""
    assert remote_configured(site) is False


def test_every_value_can_be_overridden_from_the_environment():
    site = load_site_config(
        {
            **ENV,
            "PRISM_SCRATCH_ROOT": "/scratch/alice",
            "PRISM_REMOTE_SSH_HOST": " data-server ",
            "PRISM_REMOTE_BASE_PATH": "/srv/datasets/",
            "PRISM_LOCAL_DATASET_BASE_DIR": "/scratch/shared/clones",
            "PRISM_COHORT_LOG_BASE_DIR": "/scratch/alice/logs",
        }
    )

    assert site["scratch_root"] == "/scratch/alice"
    assert site["remote_ssh_host"] == "data-server"
    assert site["remote_base_path"] == "/srv/datasets"
    assert site["local_dataset_base_dir"] == "/scratch/shared/clones"
    assert site["cohort_log_base_dir"] == "/scratch/alice/logs"
    assert remote_configured(site) is True


def test_scratch_root_alone_moves_both_derived_defaults():
    site = load_site_config({**ENV, "PRISM_SCRATCH_ROOT": "/scratch/alice"})
    assert site["local_dataset_base_dir"] == "/scratch/alice/datasets"
    assert site["cohort_log_base_dir"] == "/scratch/alice/bids_apps_runner_cohort_logs"


def test_remote_needs_both_host_and_path():
    assert remote_configured({"remote_ssh_host": "h", "remote_base_path": ""}) is False
    assert remote_configured({"remote_ssh_host": "", "remote_base_path": "/p"}) is False


@pytest.mark.parametrize("bad", ["", "-oProxyCommand=evil", "host with space", "a;b"])
def test_an_unsafe_ssh_host_is_rejected(bad):
    # The host is passed to ssh/rsync on the command line; an option-looking
    # or shell-ish value must never get that far.
    site = load_site_config({**ENV, "PRISM_REMOTE_SSH_HOST": bad, "PRISM_REMOTE_BASE_PATH": "/p"})
    assert remote_configured(site) is False


# ── data directory ──────────────────────────────────────────────────────────


def _legacy_checkout(tmp_path, uid=None):
    base = tmp_path / "checkout"
    (base / "projects").mkdir(parents=True)
    return base


def test_explicit_data_dir_wins(tmp_path):
    base = _legacy_checkout(tmp_path)
    chosen = pick_data_dir(base, {"PRISM_DATA_DIR": str(tmp_path / "mine")}, tmp_path / "home", "Linux")
    assert chosen == tmp_path / "mine"


def test_an_existing_own_checkout_keeps_its_data_where_it_is(tmp_path):
    base = _legacy_checkout(tmp_path)
    assert pick_data_dir(base, {}, tmp_path / "home", "Linux") == base


def test_a_shared_writable_install_does_not_share_data_between_users(tmp_path):
    # A group-writable checkout with no projects/ of the caller's own: before,
    # the "can I write here?" probe passed for everyone, so every user shared
    # one projects/ folder and one .secret_key.
    base = tmp_path / "shared_install"
    base.mkdir()
    home = tmp_path / "home"
    assert pick_data_dir(base, {}, home, "Linux") == home / ".bids_apps_runner"


def test_projects_owned_by_someone_else_are_never_adopted(tmp_path):
    base = _legacy_checkout(tmp_path)
    home = tmp_path / "home"
    other_uid = os.getuid() + 1
    assert pick_data_dir(base, {}, home, "Linux", uid=other_uid) == home / ".bids_apps_runner"


def test_macos_uses_application_support(tmp_path):
    base = tmp_path / "bundle"
    base.mkdir()
    chosen = pick_data_dir(base, {}, tmp_path / "home", "Darwin")
    assert chosen == tmp_path / "home" / "Library" / "Application Support" / "BIDSAppsRunner"
