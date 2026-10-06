import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import pytest


@pytest.fixture(autouse=True)
def _gui_test_client_sends_auth_token():
    """The GUI never trusts loopback (shared login node), so every request
    needs the per-run token. Route tests get a client that sends whatever
    token prism_app_runner holds *at request time* -- tests that monkeypatch
    GUI_AUTH_TOKEN, or build a plain FlaskClient, still exercise auth for real.
    """
    runner = sys.modules.get("prism_app_runner")
    if runner is None:
        yield
        return

    from flask.testing import FlaskClient

    class TokenClient(FlaskClient):
        def open(self, *args, **kwargs):
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault(runner.GUI_AUTH_HEADER, runner.GUI_AUTH_TOKEN)
            return super().open(*args, headers=headers, **kwargs)

    previous = runner.app.test_client_class
    runner.app.test_client_class = TokenClient
    yield
    runner.app.test_client_class = previous


SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(autouse=True)
def _not_on_a_login_node_by_default(monkeypatch):
    """Tests must not change behaviour depending on which host runs them:
    on IT010128 the real check says "login node" and every GUI subprocess
    call would turn into an `srun`. Tests that care set it explicitly."""
    import login_node_hygiene

    monkeypatch.setattr(login_node_hygiene, "on_bare_slurm_login_node", lambda: False)


@pytest.fixture(autouse=True)
def _site_has_a_remote_server_by_default(monkeypatch):
    """Cohort config derivation needs a DataLad server (it refuses to emit a
    broken URL without one). Tests that care about the unconfigured case
    patch it away explicitly."""
    runner = sys.modules.get("prism_app_runner")
    if runner is None:
        return
    monkeypatch.setattr(
        runner,
        "SITE",
        {**runner.SITE, "remote_ssh_host": "test-server", "remote_base_path": "/srv/test-datasets"},
    )
