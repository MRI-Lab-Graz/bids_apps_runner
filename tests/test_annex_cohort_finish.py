"""scripts/annex_cohort_finish.sh -- the cohort finish step on annex-slurm.

Replaces `datalad slurm-finish` + incremental_datalad_save.sh + the
`git status`-based "anything uncommitted?" check, all of which compare index
against worktree and hang on repos with git-annex keys-DB drift (dataset 134,
docs/superpowers/specs/2026-09-09-annex-slurm-design.md).

The contract it enforces: after it exits 0, no regular (unannexed) file is
left under any listed subject. "Done" is a re-scan of the filesystem, not the
finish tool's exit code.

Hermetic: annex-slurm-finish is replaced by a stub that records its argv and
turns each given path into a symlink, which is what the real tool leaves
behind. No git-annex, no network.
"""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
FINISH = SCRIPTS / "annex_cohort_finish.sh"
LIB = SCRIPTS / "lib_annex_paths.sh"

STUB = """#!/usr/bin/env bash
# args: -m MESSAGE PATH...
echo "$*" >> "$CALLS"
[[ "$STUB_MODE" == "noop" ]] && exit 0
shift 2
for p in "$@"; do
    [[ "$STUB_FAIL_ON" == *"$p"* ]] && exit 1
    rm -f -- "$p"; ln -s /nonexistent-annex-object "$p"
done
"""


@pytest.fixture
def ds(tmp_path):
    root = tmp_path / "derivatives"
    (root / ".git").mkdir(parents=True)
    return root


def _touch(root, *rels):
    for rel in rels:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n")


@pytest.fixture
def run(tmp_path, ds):
    calls = tmp_path / "calls.log"
    stub = tmp_path / "annex-slurm-finish"
    stub.write_text(STUB)
    stub.chmod(0o755)

    def _run(*args, subjects=("sub-01",), env=None, in_allocation=True):
        lst = tmp_path / "subjects.txt"
        lst.write_text("".join(f"{s}\n" for s in subjects))
        full_env = {
            **os.environ,
            "ANNEX_SLURM_FINISH": str(stub),
            "CALLS": str(calls),
            "STUB_MODE": "",
            "STUB_FAIL_ON": "",
        }
        full_env.pop("SLURM_JOB_ID", None)
        full_env.pop("SLURM_JOBID", None)
        if in_allocation:
            full_env["SLURM_JOB_ID"] = "1"
        full_env.update(env or {})
        proc = subprocess.run(
            ["bash", str(FINISH), "-d", str(ds), "-s", str(lst), "-m", "msg", *args],
            capture_output=True,
            text=True,
            env=full_env,
        )
        logged = calls.read_text().splitlines() if calls.exists() else []
        return proc, logged

    return _run


# ── lib_annex_paths.sh ──────────────────────────────────────────────────────


def _entries(ds, *names):
    out = subprocess.run(
        ["bash", "-c", f'source "{LIB}"; annex_entries_for_names "$1" "${{@:2}}"', "_", str(ds), *names],
        capture_output=True,
        text=True,
    )
    return sorted(out.stdout.split())


def test_entries_match_exact_dot_and_underscore_siblings_only(ds):
    # FreeSurfer-longitudinal layout puts these side by side at the root.
    for name in (
        "sub-01", "sub-01.html", "sub-01_ses-1", "sub-01_ses-1.long.sub-01",
        "sub-010", "sub-010.html", "sub-02",
    ):
        (ds / name).mkdir() if "." not in name or "long" in name else (ds / name).write_text("x")
    assert _entries(ds, "sub-01") == [
        "sub-01", "sub-01.html", "sub-01_ses-1", "sub-01_ses-1.long.sub-01",
    ]


def _root_entries(ds):
    out = subprocess.run(
        ["bash", "-c", f'source "{LIB}"; annex_root_entries "$1"', "_", str(ds)],
        capture_output=True,
        text=True,
    )
    return sorted(out.stdout.split())


def test_root_entries_are_regular_files_and_annexed_symlinks_only(ds):
    # 134's root really holds `fsaverage -> /usr/local/freesurfer/...`:
    # a symlink OUT of the dataset, not annexed content. It must never be
    # offered for unlocking or committing.
    (ds / "dataset_description.json").write_text("x")
    (ds / ".gitattributes").write_text("x")
    os.symlink(".git/annex/objects/ab/cd/SHA256E-s1--ff.txt/SHA256E-s1--ff.txt", ds / "group_report.txt")
    os.symlink("/usr/local/freesurfer/8.2.0/subjects/fsaverage", ds / "fsaverage")
    (ds / "sub-01").mkdir()

    assert _root_entries(ds) == ["dataset_description.json", "group_report.txt"]


def test_entries_for_a_name_with_no_output_is_empty(ds):
    assert _entries(ds, "sub-99") == []


# ── what gets committed ─────────────────────────────────────────────────────


def test_commits_regular_files_of_each_listed_subject_one_call_each(run, ds):
    _touch(ds, "sub-01/a.txt", "sub-01/anat/b.txt", "sub-02/c.txt", "sub-03/never_listed.txt")

    proc, calls = run(subjects=("sub-01", "sub-02"))

    assert proc.returncode == 0, proc.stderr
    assert len(calls) == 2
    assert calls[0].startswith("-m msg: sub-01 ")
    assert calls[1].startswith("-m msg: sub-02 ")
    assert "sub-01/a.txt" in calls[0] and "sub-01/anat/b.txt" in calls[0]
    assert "sub-02/c.txt" in calls[1]
    assert (ds / "sub-03/never_listed.txt").is_file() and not (ds / "sub-03/never_listed.txt").is_symlink()


def test_already_annexed_symlinks_are_left_alone(run, ds):
    _touch(ds, "sub-01/new.txt")
    os.symlink("/obj", ds / "sub-01/old.txt")

    proc, calls = run()

    assert proc.returncode == 0, proc.stderr
    assert len(calls) == 1
    assert "old.txt" not in calls[0] and "sub-01/new.txt" in calls[0]


def test_subject_with_only_annexed_files_makes_no_call(run, ds):
    (ds / "sub-01").mkdir()
    os.symlink("/obj", ds / "sub-01/old.txt")

    proc, calls = run()

    assert proc.returncode == 0
    assert calls == []


def test_subject_without_any_output_is_skipped_not_failed(run):
    proc, calls = run(subjects=("sub-404",))
    assert proc.returncode == 0
    assert calls == []
    assert "sub-404" in proc.stdout + proc.stderr


def test_a_subject_is_committed_in_chunks(run, ds):
    _touch(ds, *(f"sub-01/f{i}.txt" for i in range(5)))

    proc, calls = run("--chunk", "2")

    assert proc.returncode == 0, proc.stderr
    assert len(calls) == 3
    assert sum(c.count("sub-01/f") for c in calls) == 5


def test_root_files_and_extra_paths_are_committed_but_hidden_files_are_not(run, ds):
    _touch(ds, "sub-01/a.txt", "dataset_description.json", ".gitattributes",
           "results/vol.csv", ".slurm_logs/ds1/slurm-1_0.out")

    proc, calls = run("--root-files", "--extra", "results", "--extra", ".slurm_logs/ds1")

    assert proc.returncode == 0, proc.stderr
    joined = "\n".join(calls)
    assert "dataset_description.json" in joined
    assert "results/vol.csv" in joined
    assert ".slurm_logs/ds1/slurm-1_0.out" in joined
    assert ".gitattributes" not in joined


# ── the contract: done = nothing left unannexed ─────────────────────────────


def test_fails_loudly_when_files_remain_after_the_finish_tool_exits_zero(run, ds):
    _touch(ds, "sub-01/a.txt")

    proc, _ = run(env={"STUB_MODE": "noop"})

    assert proc.returncode != 0
    assert "sub-01/a.txt" in proc.stderr


def test_one_failing_subject_does_not_stop_the_others_but_fails_the_run(run, ds):
    _touch(ds, "sub-01/a.txt", "sub-02/b.txt")

    proc, calls = run(subjects=("sub-01", "sub-02"), env={"STUB_FAIL_ON": "sub-01/a.txt"})

    assert proc.returncode != 0
    assert len(calls) == 2, "sub-02 must still be attempted"
    assert (ds / "sub-02/b.txt").is_symlink()
    assert "sub-01" in proc.stderr


# ── safety ──────────────────────────────────────────────────────────────────


def test_refuses_on_a_bare_login_node(run, ds, tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "sbatch").write_text("#!/bin/sh\nexit 0\n")
    (fake_bin / "sbatch").chmod(0o755)
    _touch(ds, "sub-01/a.txt")

    proc, calls = run(env={"PATH": f"{fake_bin}:{os.environ['PATH']}"}, in_allocation=False)

    assert proc.returncode != 0
    assert "login node" in proc.stderr
    assert calls == []


def test_dry_run_touches_nothing_and_needs_no_allocation(run, ds, tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "sbatch").write_text("#!/bin/sh\nexit 0\n")
    (fake_bin / "sbatch").chmod(0o755)
    _touch(ds, "sub-01/a.txt")

    proc, calls = run("--dry-run", env={"PATH": f"{fake_bin}:{os.environ['PATH']}"}, in_allocation=False)

    assert proc.returncode == 0, proc.stderr
    assert calls == []
    assert not (ds / "sub-01/a.txt").is_symlink()
    assert "sub-01/a.txt" in proc.stdout


def test_requires_a_dataset_that_looks_like_a_repo(run, ds):
    (ds / ".git").rmdir()
    proc, _ = run()
    assert proc.returncode != 0
    assert "Not a git" in proc.stderr
