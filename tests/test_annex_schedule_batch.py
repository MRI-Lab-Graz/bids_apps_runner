"""annex_schedule_batch (scripts/lib_annex_paths.sh): unlock a batch's existing
output, then sbatch -- the schedule half of the cohort flow on annex-slurm.

Shared by the main-array and subregion schedulers in submit_bids_cohort.sh so
there is one tested implementation of "what gets unlocked" and "how the job id
is read back". The scheduler tool is stubbed: git-annex prints its unlock
progress to stdout ahead of sbatch's own output, so the stub does too.
"""

import os
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib_annex_paths.sh"

STUB = """#!/usr/bin/env bash
pwd > "$CALLS.cwd"
printf '%s\\n' "$*" > "$CALLS"
echo "unlock sub-01/out.txt ok"          # git-annex noise on stdout
echo "$STUB_JOB_OUTPUT"
echo "$STUB_STDERR" >&2
exit "${STUB_EXIT:-0}"
"""


@pytest.fixture
def ds(tmp_path):
    root = tmp_path / "derivatives"
    (root / ".git").mkdir(parents=True)
    return root


@pytest.fixture
def schedule(tmp_path, ds):
    stub = tmp_path / "annex-slurm-schedule"
    stub.write_text(STUB)
    stub.chmod(0o755)
    calls = tmp_path / "calls.txt"
    names = tmp_path / "names.txt"

    def _run(*flags, subjects=("sub-01",), job_output="12345", env=None):
        names.write_text("".join(f"{s}\n" for s in subjects))
        proc = subprocess.run(
            ["bash", "-c", f'source "{LIB}"; annex_schedule_batch "$@"', "_",
             str(ds), str(names), "/shared/array.sh", *flags],
            capture_output=True,
            text=True,
            env={**os.environ, "ANNEX_SLURM_SCHEDULE": str(stub), "CALLS": str(calls),
                 "STUB_JOB_OUTPUT": job_output, "STUB_STDERR": "", **(env or {})},
        )
        argv = calls.read_text().strip() if calls.exists() else None
        return proc, argv

    return _run


def _mk(ds, *rels):
    for rel in rels:
        (ds / rel).parent.mkdir(parents=True, exist_ok=True)
        (ds / rel).write_text("x")


def test_unlocks_only_the_batch_subjects_existing_output(schedule, ds):
    _mk(ds, "sub-01/a.txt", "sub-01.html", "sub-02/b.txt", "dataset_description.json")

    proc, argv = schedule(subjects=("sub-01",))

    assert proc.returncode == 0, proc.stderr
    assert argv == "-o sub-01 -o sub-01.html -- --parsable /shared/array.sh"


def test_a_new_subject_with_no_output_unlocks_nothing(schedule):
    proc, argv = schedule(subjects=("sub-99",))
    assert proc.returncode == 0
    assert argv == "-- --parsable /shared/array.sh"


def test_root_files_and_extras_are_added_on_request(schedule, ds):
    _mk(ds, "sub-01/a.txt", "group.tsv", ".hidden")

    proc, argv = schedule("--root-files", "--extra", "subregion_results")

    assert proc.returncode == 0, proc.stderr
    assert argv == "-o sub-01 -o group.tsv -o subregion_results -- --parsable /shared/array.sh"


def test_runs_inside_the_dataset(schedule, ds, tmp_path):
    schedule()
    assert Path((tmp_path / "calls.txt.cwd").read_text().strip()).resolve() == ds.resolve()


@pytest.mark.parametrize("raw,expected", [("12345", "12345"), ("12345;cluster2", "12345")])
def test_job_id_is_the_last_stdout_line(schedule, raw, expected):
    proc, _ = schedule(job_output=raw)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == expected


def test_a_non_numeric_job_id_fails_loudly(schedule):
    proc, _ = schedule(job_output="sbatch: error: invalid partition")
    assert proc.returncode != 0
    assert "invalid partition" in proc.stderr
    assert proc.stdout.strip() == ""


def test_scheduler_failure_is_reported_with_its_output(schedule):
    proc, _ = schedule(env={"STUB_EXIT": "3", "STUB_STDERR": "boom: no such partition"})
    assert proc.returncode != 0
    assert "boom: no such partition" in proc.stderr
