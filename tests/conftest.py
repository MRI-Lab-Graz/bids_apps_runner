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
