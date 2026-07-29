import sys
from argparse import Namespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import prism_local


def test_not_login_node_when_sbatch_absent(monkeypatch):
    monkeypatch.setattr(prism_local.shutil, "which", lambda name: None)
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    monkeypatch.delenv("SLURM_JOBID", raising=False)
    assert prism_local._on_bare_slurm_login_node() is False


def test_login_node_when_sbatch_present_and_no_allocation(monkeypatch):
    monkeypatch.setattr(
        prism_local.shutil, "which", lambda name: "/usr/bin/sbatch" if name == "sbatch" else None
    )
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    monkeypatch.delenv("SLURM_JOBID", raising=False)
    assert prism_local._on_bare_slurm_login_node() is True


def test_not_login_node_inside_slurm_job_id_allocation(monkeypatch):
    monkeypatch.setattr(
        prism_local.shutil, "which", lambda name: "/usr/bin/sbatch" if name == "sbatch" else None
    )
    monkeypatch.setenv("SLURM_JOB_ID", "123456")
    monkeypatch.delenv("SLURM_JOBID", raising=False)
    assert prism_local._on_bare_slurm_login_node() is False


def test_not_login_node_inside_slurm_jobid_allocation(monkeypatch):
    monkeypatch.setattr(
        prism_local.shutil, "which", lambda name: "/usr/bin/sbatch" if name == "sbatch" else None
    )
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    monkeypatch.setenv("SLURM_JOBID", "123456")
    assert prism_local._on_bare_slurm_login_node() is False


def test_execute_local_refuses_on_bare_login_node(monkeypatch):
    monkeypatch.setattr(prism_local, "_on_bare_slurm_login_node", lambda: True)
    args = Namespace(dry_run=False, subjects=None, config=None)
    with pytest.raises(prism_local.LoginNodeExecutionError, match="REFUSING TO RUN"):
        prism_local.execute_local({"common": {}, "app": {}}, args)


def test_execute_local_dry_run_bypasses_login_node_guard(monkeypatch):
    # `not args.dry_run` short-circuits the `and` before
    # _on_bare_slurm_login_node() is ever evaluated -- even forcing it to
    # return True here must not raise. Downstream execute_local() may still
    # fail for unrelated reasons (no real bids_folder in this minimal
    # config), which is fine; only LoginNodeExecutionError is under test.
    monkeypatch.setattr(prism_local, "_on_bare_slurm_login_node", lambda: True)
    args = Namespace(dry_run=True, subjects=None, config=None)
    try:
        prism_local.execute_local({"common": {}, "app": {}}, args)
    except prism_local.LoginNodeExecutionError:
        pytest.fail("dry-run must bypass the login-node guard")
    except Exception:
        pass
