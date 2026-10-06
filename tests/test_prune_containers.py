"""Daily pruning of local .sif images nobody has used for a while.

Only safe because the DataLad server's container catalog can give them
back. Checked against the real catalog on 2026-10-06: an age-only rule
would have deleted freesurfer_8.2.0.sif (not on the server at all) and
qsiprep_26.0.0.sif (server copy is a different build, 16 KB smaller).
"""

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import prune_containers  # noqa: E402

DAY = 86400


def _sif(root: Path, rel: str, size: int, age_days: float) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)
    t = time.time() - age_days * DAY
    os.utime(p, (t, t))
    return p


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Local folder + fake server catalog + fake SLURM queue."""
    state = {"remote": {}, "jobs": {}, "squeue_fails": False}

    monkeypatch.setattr(
        prune_containers, "remote_sizes", lambda remote: dict(state["remote"])
    )

    def fake_job_scripts():
        if state["squeue_fails"]:
            raise RuntimeError("squeue: slurm_load_jobs error")
        return list(state["jobs"].values())

    monkeypatch.setattr(prune_containers, "queued_job_scripts", fake_job_scripts)
    state["root"] = tmp_path
    return state


def _run(env, *extra):
    return prune_containers.main(
        ["--folder", str(env["root"]), "--remote", "srv:/c/", *extra]
    )


def test_old_image_that_the_server_has_is_deleted_with_live(env):
    p = _sif(env["root"], "mriqc/mriqc_24.0.2.sif", 10, age_days=40)
    env["remote"]["mriqc/mriqc_24.0.2.sif"] = 10

    assert _run(env, "--live") == 0

    assert not p.exists()


def test_dry_run_is_the_default(env, capsys):
    p = _sif(env["root"], "mriqc/mriqc_24.0.2.sif", 10, age_days=40)
    env["remote"]["mriqc/mriqc_24.0.2.sif"] = 10

    assert _run(env) == 0

    assert p.exists()
    assert "would delete mriqc/mriqc_24.0.2.sif" in capsys.readouterr().out


def test_recently_used_image_is_kept(env):
    p = _sif(env["root"], "mriqc/mriqc_24.0.2.sif", 10, age_days=5)
    env["remote"]["mriqc/mriqc_24.0.2.sif"] = 10

    _run(env, "--live")

    assert p.exists()


def test_image_missing_from_server_is_kept(env, capsys):
    p = _sif(env["root"], "freesurfer/freesurfer_8.2.0.sif", 10, age_days=90)

    _run(env, "--live")

    assert p.exists()
    assert "not on the server" in capsys.readouterr().out


def test_image_whose_server_copy_differs_is_kept(env, capsys):
    p = _sif(env["root"], "qsiprep/qsiprep_26.0.0.sif", 10, age_days=90)
    env["remote"]["qsiprep/qsiprep_26.0.0.sif"] = 9

    _run(env, "--live")

    assert p.exists()
    assert "differs from the server" in capsys.readouterr().out


def test_image_referenced_by_a_queued_job_is_kept(env, capsys):
    p = _sif(env["root"], "mriqc/mriqc_24.0.2.sif", 10, age_days=40)
    env["remote"]["mriqc/mriqc_24.0.2.sif"] = 10
    env["jobs"]["123"] = f"#!/bin/bash\nsingularity run {p} /bids /out participant\n"

    _run(env, "--live")

    assert p.exists()
    assert "queued/running job" in capsys.readouterr().out


def test_nothing_is_deleted_when_the_queue_cannot_be_read(env):
    p = _sif(env["root"], "mriqc/mriqc_24.0.2.sif", 10, age_days=40)
    env["remote"]["mriqc/mriqc_24.0.2.sif"] = 10
    env["squeue_fails"] = True

    assert _run(env, "--live") == 1

    assert p.exists()


def test_nothing_is_deleted_when_the_server_cannot_be_reached(env, monkeypatch):
    p = _sif(env["root"], "mriqc/mriqc_24.0.2.sif", 10, age_days=40)

    def unreachable(remote):
        raise RuntimeError("Timed out contacting srv")

    monkeypatch.setattr(prune_containers, "remote_sizes", unreachable)

    assert _run(env, "--live") == 1

    assert p.exists()


def test_days_threshold_is_configurable(env):
    p = _sif(env["root"], "mriqc/mriqc_24.0.2.sif", 10, age_days=10)
    env["remote"]["mriqc/mriqc_24.0.2.sif"] = 10

    _run(env, "--live", "--days", "7")

    assert not p.exists()


def test_remote_sizes_parses_the_catalog_listing(monkeypatch):
    seen = {}

    def fake_run_remote_script(host, script, timeout):
        seen["host"] = host
        seen["script"] = script
        return "mriqc/mriqc_24.0.2.sif 4953468928\nqsiprep/qsiprep_1.1.1.sif 8767295488\n"

    monkeypatch.setattr(
        prune_containers.prism_datalad, "run_remote_script", fake_run_remote_script
    )

    sizes = prune_containers.remote_sizes("datalad-server:/datalad/mri/container/")

    assert seen["host"] == "datalad-server"
    assert "/datalad/mri/container/" in seen["script"]
    assert sizes == {
        "mriqc/mriqc_24.0.2.sif": 4953468928,
        "qsiprep/qsiprep_1.1.1.sif": 8767295488,
    }


def test_queued_job_scripts_reads_each_jobs_batch_script(monkeypatch):
    calls = []

    def fake_check_output(cmd, text, timeout):
        calls.append(cmd)
        if cmd[0] == "squeue":
            return "100\n100\n200\n"  # array tasks share their master's script
        return f"script of {cmd[3]}"

    monkeypatch.setattr(prune_containers.subprocess, "check_output", fake_check_output)

    assert prune_containers.queued_job_scripts() == ["script of 100", "script of 200"]
    assert calls[0][:2] == ["squeue", "--me"]


def test_queued_job_scripts_turns_slurm_errors_into_runtime_error(monkeypatch):
    def boom(cmd, text, timeout):
        raise FileNotFoundError("squeue")

    monkeypatch.setattr(prune_containers.subprocess, "check_output", boom)

    with pytest.raises(RuntimeError):
        prune_containers.queued_job_scripts()
