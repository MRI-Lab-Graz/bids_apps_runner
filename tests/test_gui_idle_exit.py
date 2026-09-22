"""The GUI's self-imposed lifetime cap on a login node.

Background (2026-09-22): a prism_app_runner.py daemon had been running on
login node IT010128 for 62 days. It started 2026-07-22; the login-node
execution guard landed 2026-07-29 in c5edee6. A running process keeps the
code it loaded at startup, so for its entire life that daemon had no
guard at all -- its Run button would have launched containers directly on
the login node. Capping the GUI's lifetime is therefore a safety control,
not housekeeping: it bounds how long a process can outlive the code meant
to constrain it.

The cap must never fire on a collaborator's Mac (install_macos.sh ships
this GUI to people without SLURM) or inside a real allocation, and must
never interrupt an in-flight `datalad clone` or container pull.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import login_node_hygiene
import prism_app_runner


def test_job_registries_cover_every_background_job_store():
    """A registry missing here means the GUI could exit mid-job.

    This test fails if someone adds a new background-job registry without
    teaching the watchdog about it.
    """
    import gui.gui_cohort_routes as cohort
    import gui.gui_misc_routes as misc
    import gui.gui_utility_routes as utility

    registries = prism_app_runner._job_registries()

    for expected in (
        prism_app_runner.RUN_JOBS,
        prism_app_runner.PILOT_JOBS,
        cohort._cohort_jobs,
        utility._clone_jobs,
        misc._tf_jobs,
        misc._container_jobs,
    ):
        assert any(reg is expected for reg in registries)


def test_no_shutdown_when_not_on_a_login_node(monkeypatch):
    # A collaborator's Mac has no sbatch; the cap must not apply.
    monkeypatch.setattr(prism_app_runner, "_job_registries", lambda: [{}])
    monkeypatch.setattr(prism_app_runner, "_LAST_REQUEST_AT", 0.0)
    monkeypatch.setattr(
        login_node_hygiene, "on_bare_slurm_login_node", lambda: False
    )

    assert prism_app_runner._should_shut_down_now() is False


def test_no_shutdown_while_a_clone_is_running(monkeypatch):
    monkeypatch.setattr(
        prism_app_runner, "_job_registries", lambda: [{"j": {"status": "running"}}]
    )
    monkeypatch.setattr(prism_app_runner, "_LAST_REQUEST_AT", 0.0)
    monkeypatch.setattr(login_node_hygiene, "on_bare_slurm_login_node", lambda: True)

    assert prism_app_runner._should_shut_down_now() is False


def test_no_shutdown_shortly_after_a_request(monkeypatch):
    import time

    monkeypatch.setattr(prism_app_runner, "_job_registries", lambda: [{}])
    monkeypatch.setattr(prism_app_runner, "_LAST_REQUEST_AT", time.time())
    monkeypatch.setattr(login_node_hygiene, "on_bare_slurm_login_node", lambda: True)

    assert prism_app_runner._should_shut_down_now() is False


def test_shutdown_when_idle_on_login_node_with_no_jobs(monkeypatch):
    monkeypatch.setattr(prism_app_runner, "_job_registries", lambda: [{}])
    monkeypatch.setattr(prism_app_runner, "_LAST_REQUEST_AT", 0.0)
    monkeypatch.setattr(login_node_hygiene, "on_bare_slurm_login_node", lambda: True)

    assert prism_app_runner._should_shut_down_now() is True


def test_finished_jobs_do_not_keep_the_gui_alive(monkeypatch):
    monkeypatch.setattr(
        prism_app_runner,
        "_job_registries",
        lambda: [{"a": {"status": "completed"}}, {"b": {"status": "failed"}}],
    )
    monkeypatch.setattr(prism_app_runner, "_LAST_REQUEST_AT", 0.0)
    monkeypatch.setattr(login_node_hygiene, "on_bare_slurm_login_node", lambda: True)

    assert prism_app_runner._should_shut_down_now() is True


def test_recording_activity_pushes_the_idle_clock_forward(monkeypatch):
    monkeypatch.setattr(prism_app_runner, "_LAST_REQUEST_AT", 0.0)

    prism_app_runner._note_request_activity()

    assert prism_app_runner._LAST_REQUEST_AT > 0.0


# ── background polling must not count as "in use" ───────────────────────────
#
# templates/index.html polls status endpoints on a timer. A browser tab
# left open on a forgotten laptop would otherwise refresh the idle clock
# forever -- which is precisely how a GUI stays up for 62 days. Only real
# interaction counts; a genuinely running job is kept alive by the job
# registries instead.


def test_status_polling_does_not_count_as_user_activity():
    for path in sorted(prism_app_runner.SILENT_ENDPOINTS):
        assert prism_app_runner._is_user_activity(path) is False, path


def test_real_interaction_counts_as_user_activity():
    assert prism_app_runner._is_user_activity("/run_app") is True
    assert prism_app_runner._is_user_activity("/load_project") is True
