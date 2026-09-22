"""Tests for scripts/lib_prefetch.sh -- keeping the cohort prefetch off the login node.

Why this exists
---------------
`submit_bids_cohort.sh` prefetches a cohort's subject data with `datalad
get` before scheduling the array job, because datalad-slurm keeps all
git/annex operations outside the job itself. That prefetch ran
synchronously in the submitting shell -- and the documented workflow is
to submit from the login node.

Measured on 2026-09-22: ds004592 carries 40 GB of annexed content,
ds005339 52 GB, and git-annex checksums every byte it fetches. Across the
26-dataset megastudy that is roughly a terabyte of transfer and hashing
on a shared login node. `prism_local.execute_local()`'s login-node guard
does not cover this path at all -- it is bash, not Python, and never
touches execute_local.

So the prefetch now dispatches itself to a compute node via `sbatch
--wait` whenever it finds itself on a bare login node, and runs inline
everywhere else (a workstation with no SLURM, or an existing allocation).
`--wait` preserves the existing ordering guarantee: submission still
blocks until the content is actually present.
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib_prefetch.sh"


@pytest.fixture
def harness(tmp_path):
    """A hermetic PATH containing only fake tools that record their calls.

    PATH is set to the stub directory ALONE -- never appended to the real
    PATH. An earlier version appended, so hiding `sbatch` from the stub dir
    still left /usr/bin/sbatch reachable and the suite submitted a real
    cluster job. A test for login-node safety must not be able to touch the
    cluster; isolation here is part of what is being tested.
    """
    calls = tmp_path / "calls.log"

    def _make_stubs(directory, names):
        directory.mkdir(exist_ok=True)
        for name in names:
            stub = directory / name
            stub.write_text(
                "#!/usr/bin/env bash\n"
                f'echo "{name} $*" >> "{calls}"\n'
                "exit 0\n"
            )
            stub.chmod(0o755)
        # bash itself must stay reachable: run_prefetch shells out with it.
        link = directory / "bash"
        if not link.exists():
            link.symlink_to("/bin/bash")

    with_dir = tmp_path / "with_sbatch"
    without_dir = tmp_path / "without_sbatch"
    _make_stubs(with_dir, ("sbatch", "datalad"))
    _make_stubs(without_dir, ("datalad",))

    def run(script_body, env=None, with_sbatch=True):
        stub_dir = with_dir if with_sbatch else without_dir
        full_env = {
            "PATH": str(stub_dir),  # ONLY the stubs -- see docstring
            "HOME": str(tmp_path),
            "CALLS": str(calls),
        }
        full_env.update(env or {})

        result = subprocess.run(
            ["/bin/bash", "-c", f'set -uo pipefail; source "{LIB}"\n{script_body}'],
            capture_output=True,
            text=True,
            env=full_env,
        )
        logged = calls.read_text() if calls.exists() else ""
        return result, logged

    return run


def test_harness_cannot_reach_a_real_sbatch(harness):
    """Guard on the guard: proves the isolation that failed once before."""
    result, _ = harness('command -v sbatch', with_sbatch=False)
    assert result.stdout.strip() == "", f"real sbatch reachable: {result.stdout}"

    result, _ = harness('command -v sbatch', with_sbatch=True)
    assert "/usr/bin/sbatch" not in result.stdout
    assert result.stdout.strip().endswith("with_sbatch/sbatch")


# ── on_bare_slurm_login_node() ──────────────────────────────────────────────


def test_detects_bare_login_node(harness):
    result, _ = harness('on_bare_slurm_login_node && echo LOGIN_NODE || echo NOT')
    assert "LOGIN_NODE" in result.stdout


def test_inside_an_allocation_is_not_a_login_node(harness):
    # salloc/srun set SLURM_JOB_ID; work there is already on a compute node.
    result, _ = harness(
        'on_bare_slurm_login_node && echo LOGIN_NODE || echo NOT',
        env={"SLURM_JOB_ID": "12345"},
    )
    assert "NOT" in result.stdout


def test_machine_without_slurm_is_not_a_login_node(harness):
    result, _ = harness(
        'on_bare_slurm_login_node && echo LOGIN_NODE || echo NOT', with_sbatch=False
    )
    assert "NOT" in result.stdout


# ── run_prefetch() dispatch ─────────────────────────────────────────────────


def test_prefetch_on_login_node_goes_through_sbatch(harness):
    """The whole point: 40-52 GB of annex traffic must not run here."""
    result, logged = harness('run_prefetch ds004592 "datalad get sub-001/"')

    assert result.returncode == 0, result.stderr
    assert "sbatch" in logged
    # datalad must NOT have been invoked directly in this shell.
    assert not any(
        line.startswith("datalad ") for line in logged.splitlines()
    ), f"datalad ran on the login node: {logged}"


def test_sbatch_dispatch_waits_for_completion(harness):
    """Without --wait the array would be scheduled before content exists."""
    _, logged = harness('run_prefetch ds004592 "datalad get sub-001/"')

    sbatch_line = next(l for l in logged.splitlines() if l.startswith("sbatch "))
    assert "--wait" in sbatch_line


def test_sbatch_dispatch_requests_modest_resources(harness):
    # Prefetch is pure I/O; it must not sit on a fat allocation.
    _, logged = harness('run_prefetch ds004592 "datalad get sub-001/"')

    sbatch_line = next(l for l in logged.splitlines() if l.startswith("sbatch "))
    assert "--cpus-per-task=1" in sbatch_line


def test_prefetch_inside_an_allocation_runs_inline(harness):
    result, logged = harness(
        'run_prefetch ds004592 "datalad get sub-001/"',
        env={"SLURM_JOB_ID": "12345"},
    )

    assert result.returncode == 0, result.stderr
    assert any(line.startswith("datalad ") for line in logged.splitlines())
    assert "sbatch" not in logged


def test_prefetch_without_slurm_runs_inline(harness):
    result, logged = harness(
        'run_prefetch ds004592 "datalad get sub-001/"', with_sbatch=False
    )

    assert result.returncode == 0, result.stderr
    assert any(line.startswith("datalad ") for line in logged.splitlines())


def test_prefetch_propagates_failure(harness, tmp_path):
    # A silently-swallowed prefetch failure would let the array job start
    # against missing input -- the exact class of bug that wasted a week.
    result, _ = harness(
        'run_prefetch ds004592 "exit 7" && echo OK || echo "FAILED=$?"',
        env={"SLURM_JOB_ID": "1"},
    )
    assert "FAILED=" in result.stdout


def test_prefetch_requires_a_command(harness):
    result, _ = harness('run_prefetch ds004592 "" && echo OK || echo REFUSED')
    assert "REFUSED" in result.stdout


# ── integration: submit_bids_cohort.sh must use the dispatcher ─────────────

SUBMIT = Path(__file__).resolve().parent.parent / "scripts" / "submit_bids_cohort.sh"


def _submit_source(text=None):
    return SUBMIT.read_text() if text is None else text


def test_submit_script_sources_the_prefetch_library(source=None):
    assert "lib_prefetch.sh" in _submit_source(source)


def test_content_fetch_is_dispatched_not_run_inline(source=None):
    """The 40-52 GB `datalad get` must not run in the submitting shell.

    Fails if anyone restores the old `run bash -c "... datalad get ..."`
    form, which is what put a terabyte of annex traffic on the login node.
    """
    text = _submit_source(source)
    content_fetch_lines = [
        line
        for line in text.splitlines()
        if "printf" in line and " get " in line  # the content fetch, not the -n install
    ]
    assert content_fetch_lines, "could not find the content-fetch call site"
    for line in content_fetch_lines:
        assert "run_prefetch" in line, f"content fetch runs inline: {line.strip()}"
        assert 'run bash -c' not in line


# ── the dispatched job must use a datalad that works on a compute node ─────
#
# Discovered 2026-09-22 on the first real dispatch: the login node's
# /usr/people/<user>/.local/bin/datalad is first on PATH and works there,
# but on a compute node the same shim dies with
# "ModuleNotFoundError: No module named 'datalad'" -- compute nodes have a
# different system python3 (submit_bids_cohort.sh's own header warns about
# exactly this for .appsrunner). sbatch exports the submitting PATH, so the
# broken shim is what the job found. The repo's .datalad-slurm-venv binary
# is on shared storage and works on both, which is why the finish-job
# templates already pin DATALAD_BIN to it.
#
# The inline prefetch never hit this because it only ever ran on the login
# node -- moving it to a compute node is what exposed it.


def test_prefetch_resolves_an_explicit_datalad_binary(harness):
    result, _ = harness('prefetch_datalad_bin')
    resolved = result.stdout.strip()
    assert resolved.endswith("/datalad"), resolved
    assert ".datalad-slurm-venv" in resolved, (
        f"must pin the venv datalad that works on compute nodes, got: {resolved}"
    )


def test_prefetch_datalad_bin_is_overridable(harness):
    result, _ = harness(
        'prefetch_datalad_bin', env={"PREFETCH_DATALAD_BIN": "/custom/datalad"}
    )
    assert result.stdout.strip() == "/custom/datalad"


def test_submit_script_prefetch_does_not_use_bare_datalad(source=None):
    """Regression: a bare `datalad get` in the dispatched command resolves
    to the broken login-node shim once it runs on a compute node."""
    text = _submit_source(source)
    fetch_lines = [
        line
        for line in text.splitlines()
        if "run_prefetch" in line and "get" in line
    ]
    assert fetch_lines, "could not find the dispatched prefetch command"
    for line in fetch_lines:
        assert "&& datalad get" not in line, (
            f"bare `datalad` will resolve to the broken shim on a compute node: {line.strip()}"
        )


def test_dispatched_command_puts_the_venv_first_on_path(harness):
    """datalad needs the venv's git-annex, not just the venv's datalad.

    Second failure on the real dispatch (2026-09-22): with only the datalad
    binary pinned, the job got
    "[ERROR] No working git-annex installation of version >= 10.20230126"
    -- git-annex is installed into .datalad-slurm-venv/bin alongside
    datalad, and compute nodes have no other working copy. The finish-job
    templates already export this same PATH for exactly this reason
    (submit_bids_cohort.sh: "datalad picks up the venv's git-annex, not the
    system/uv-tool one").
    """
    _, logged = harness('run_prefetch ds004592 "datalad get sub-001/"')

    sbatch_line = next(l for l in logged.splitlines() if l.startswith("sbatch "))
    assert ".datalad-slurm-venv/bin" in sbatch_line, sbatch_line
    assert "export PATH=" in sbatch_line, sbatch_line
