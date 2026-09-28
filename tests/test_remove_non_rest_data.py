"""Real-execution tests (no mocking) for remove_non_rest_data.sh's
prune_dataset(): sources the real script (a guard keeps the "run
everything against real datasets" tail from firing when sourced) and
calls prune_dataset against synthetic fixture repos, dry-run only -- never
--live, since that needs a real allocation and touches real data by
design.

2026-09-28: prune_dataset was refactored to take an explicit path (was
`${DATA_BASE}/${ds}` reconstructed internally) so the same function covers
both raw data/<ds> clones and derivatives/<ds>/fmriprep -- MRIQC finished
since this script's original 2026-09-10 write-up, so ds003849/ds004182/
ds005901/ds000256 are now in scope, and fMRIPrep derivatives were never
covered by this script at all despite the same non-resting-state tasks
being processed and stored there (confirmed: ds003849/ds004182/ds005901
had zero bids_filters, real fMRIPrep output shows 2-5 unrelated tasks per
subject). Also switched clone_is_clean -> clone_is_clean_fast: these raw
clones have 60-67% unlocked tracked files, and clone_is_clean's `git
status` hangs for hours on that (see test_lib_clone_check.py).
"""
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "remove_non_rest_data.sh"


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)


def _make_dataset(path: Path) -> None:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t.com")
    _git(path, "config", "user.name", "t")


def _write_func_file(path: Path, subject: str, task: str, suffix: str = "bold.nii.gz") -> Path:
    d = path / f"sub-{subject}" / "func"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"sub-{subject}_task-{task}_{suffix}"
    f.write_text("data")
    return f


def _run_prune_dry_run(label: str, path: Path, keep_tasks: list[str]) -> str:
    keep_args = " ".join(f'"{t}"' for t in keep_tasks)
    script = f'''
set -uo pipefail
source "{SCRIPT}"
LIVE=false
prune_dataset "{label}" "{path}" {keep_args}
'''
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return result.stdout


@pytest.fixture
def clean_dataset(tmp_path):
    path = tmp_path / "ds_fake"
    _make_dataset(path)
    _write_func_file(path, "01", "rest")
    _write_func_file(path, "01", "nback")
    _write_func_file(path, "01", "rest")  # dupe path collision avoided by suffix below
    (path / "sub-01" / "func" / "sub-01_task-rest_run-01_bold.nii.gz").write_text("data")
    (path / "sub-01" / "func" / "sub-01_task-nback_run-01_bold.nii.gz").write_text("data")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    return path


def test_dry_run_identifies_non_keep_task_files_for_removal(clean_dataset):
    out = _run_prune_dry_run("ds_fake", clean_dataset, ["rest"])

    assert "would remove" in out
    # 2 nback files (the plain one + the run-01 one), keep is rest
    assert "2 non-rest file(s)" in out


def test_dry_run_keeps_all_when_only_keep_task_present(tmp_path):
    path = tmp_path / "ds_fake2"
    _make_dataset(path)
    _write_func_file(path, "01", "rest")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")

    out = _run_prune_dry_run("ds_fake2", path, ["rest"])

    assert "nothing to remove" in out


def test_dry_run_never_deletes_anything(clean_dataset):
    before = sorted(p.name for p in (clean_dataset / "sub-01" / "func").iterdir())

    _run_prune_dry_run("ds_fake", clean_dataset, ["rest"])

    after = sorted(p.name for p in (clean_dataset / "sub-01" / "func").iterdir())
    assert before == after


def test_skips_a_dataset_with_staged_uncommitted_changes(clean_dataset):
    (clean_dataset / "sub-01" / "func" / "new_untracked_task-nback_bold.nii.gz").write_text("x")
    _git(clean_dataset, "add", "-A")

    out = _run_prune_dry_run("ds_fake", clean_dataset, ["rest"])

    assert "SKIP" in out
    assert "not clean" in out


def test_uses_the_fast_clean_check_not_the_hang_prone_one(clean_dataset):
    """An unstaged modification to a tracked file (the exact shape of an
    unlocked-but-not-yet-rehashed annexed file) must NOT block pruning --
    that's clone_is_clean_fast's documented, accepted gap. If this starts
    reporting SKIP, prune_dataset regressed to the hang-prone clone_is_clean.
    """
    (clean_dataset / "sub-01" / "func" / "sub-01_task-nback_run-01_bold.nii.gz").write_text("changed, unstaged")

    out = _run_prune_dry_run("ds_fake", clean_dataset, ["rest"])

    assert "SKIP" not in out
    assert "would remove" in out


def test_skips_when_path_does_not_exist(tmp_path):
    out = _run_prune_dry_run("ds_missing", tmp_path / "nope", ["rest"])

    assert "no such dataset dir" in out
