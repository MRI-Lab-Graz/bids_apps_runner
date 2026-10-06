"""scripts/check_output_sync.sh -- cron monitor for output clones with state
that never reached the server.

A cohort that is simply still mid-run always has local-only state, so the
monitor must not report it. The "is it still running?" signal used to be
`slurm-finish --list-open-jobs` (datalad-slurm's bookkeeping DB), which hangs
on repos with git-annex keys-DB drift and no longer exists under annex-slurm.
SLURM itself (`squeue`) is now the only job state: a live job whose name
carries the dataset id (array `<app>_<ds>`, finish `finish_<ds>`) means mid-run.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def world(tmp_path):
    """A fake repo root holding the script, one project, and a dirty clone."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    for name in ("check_output_sync.sh", "lib_clone_check.sh"):
        shutil.copy(SCRIPTS / name, root / "scripts" / name)

    clone = tmp_path / "derivatives" / "ds999"
    clone.mkdir(parents=True)
    _git(clone, "init", "-q", "-b", "main")
    _git(clone, "config", "user.email", "t@example.org")
    _git(clone, "config", "user.name", "T")
    (clone / "a.txt").write_text("a\n")
    _git(clone, "add", "a.txt")
    _git(clone, "commit", "-q", "-m", "init")
    (clone / "left_behind.txt").write_text("never committed\n")  # the dirty state

    project = root / "projects" / "p1"
    project.mkdir(parents=True)
    (project / "project.json").write_text(
        '{"config": {"common": {"output_folder": "%s"}}}' % clone
    )
    return root


def _run(world, tmp_path, squeue_output=None, slurm_finish_marker=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if squeue_output is not None:
        squeue = bin_dir / "squeue"
        squeue.write_text(f"#!/bin/sh\nprintf '%s\\n' '{squeue_output}'\n")
        squeue.chmod(0o755)
    return subprocess.run(
        ["bash", str(world / "scripts" / "check_output_sync.sh")],
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )


def test_a_dirty_clone_with_no_live_job_is_reported(world, tmp_path):
    proc = _run(world, tmp_path, squeue_output="")
    assert proc.returncode == 1
    assert "PROBLEM" in proc.stdout and "ds999" in proc.stdout


@pytest.mark.parametrize("job_name", ["finish_ds999", "finish_ds999_batch02", "fmriprep_ds999", "finish_ds999_subregions"])
def test_a_live_job_for_the_dataset_means_mid_run_not_a_problem(world, tmp_path, job_name):
    proc = _run(world, tmp_path, squeue_output=f"{job_name} RUNNING")
    assert proc.returncode == 0, proc.stdout
    assert "OK" in proc.stdout


def test_a_live_job_for_another_dataset_does_not_hide_the_problem(world, tmp_path):
    proc = _run(world, tmp_path, squeue_output="finish_ds998 RUNNING")
    assert proc.returncode == 1


def test_a_dataset_id_that_is_only_a_prefix_does_not_match(world, tmp_path):
    # ds9999 must not mask ds999.
    proc = _run(world, tmp_path, squeue_output="finish_ds9999 RUNNING")
    assert proc.returncode == 1


def test_an_inconclusive_job_check_never_suppresses_a_problem(world, tmp_path):
    # squeue missing/failing: silence is exactly the failure mode this script
    # exists to catch, so "couldn't tell" must still be reported.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "squeue").write_text("#!/bin/sh\nexit 1\n")
    (bin_dir / "squeue").chmod(0o755)
    proc = subprocess.run(
        ["bash", str(world / "scripts" / "check_output_sync.sh")],
        capture_output=True, text=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )
    assert proc.returncode == 1


def test_the_monitor_never_calls_the_old_bookkeeping_command():
    code = "\n".join(
        l for l in (SCRIPTS / "check_output_sync.sh").read_text().splitlines()
        if not l.lstrip().startswith("#")
    )
    assert "slurm-finish" not in code
    assert "DATALAD_SLURM_BIN" not in code
