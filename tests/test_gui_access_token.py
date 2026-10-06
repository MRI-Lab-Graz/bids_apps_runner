"""The GUI must never trust "the request came from 127.0.0.1".

On a shared login node (IT010128: 22 distinct users logged in at once,
2026-10-06) every user shares loopback, so a loopback-bound GUI with login
disabled handed every one of them the owner's account: arbitrary file
reads, sbatch of arbitrary scripts, scancel, datalad drop. Access now
always needs the per-run token (Jupyter-style: open /?token=... once, a
cookie carries it after that).
"""

import pytest
from flask.testing import FlaskClient

import prism_app_runner
from gui.gui_security import load_or_generate_auth_token

TOKEN = "per-run-token"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(prism_app_runner, "GUI_LOGIN_ENABLED", False)
    monkeypatch.setattr(prism_app_runner, "GUI_AUTH_TOKEN", TOKEN)
    prism_app_runner.app.config["TESTING"] = True
    # Plain FlaskClient: no default auth header from conftest.
    with FlaskClient(prism_app_runner.app, use_cookies=True) as c:
        yield c


def test_loopback_without_token_is_rejected(client):
    response = client.get("/get_projects")
    assert response.status_code == 401


def test_wrong_token_is_rejected(client):
    response = client.get("/get_projects", headers={"X-Prism-Auth": "nope"})
    assert response.status_code == 401


def test_query_token_sets_cookie_and_strips_token_from_url(client):
    response = client.get(f"/?token={TOKEN}", follow_redirects=False)

    assert response.status_code == 302
    assert "token=" not in response.headers["Location"]
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie

    assert client.get("/get_projects").status_code == 200


def test_foreign_host_header_is_rejected_even_with_token(client):
    # DNS rebinding: attacker.example resolves to 127.0.0.1.
    response = client.get(
        "/get_projects",
        headers={"X-Prism-Auth": TOKEN, "Host": "attacker.example:5000"},
    )
    assert response.status_code == 400


def test_cookie_authed_post_from_foreign_origin_is_rejected(client):
    client.get(f"/?token={TOKEN}")

    response = client.post(
        "/create_project",
        json={"name": "x"},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403


def test_cookie_authed_post_from_own_origin_is_allowed(client):
    client.get(f"/?token={TOKEN}")

    response = client.post(
        "/make_dir",
        json={},
        headers={"Origin": "http://localhost"},
    )
    # Reaches the route (400 = route's own validation), not the auth layer.
    assert response.status_code == 400
    assert "Path and name are required" in response.get_json()["error"]


def test_auth_token_is_generated_when_env_unset(monkeypatch):
    monkeypatch.delenv("PRISM_GUI_AUTH_TOKEN", raising=False)
    first, second = load_or_generate_auth_token(), load_or_generate_auth_token()
    assert len(first) >= 32
    assert first != second


def test_auth_token_from_env_is_used(monkeypatch):
    monkeypatch.setenv("PRISM_GUI_AUTH_TOKEN", " from-env ")
    assert load_or_generate_auth_token() == "from-env"


@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/save_hpc_script"),
        ("post", "/cancel_hpc_job"),
        ("post", "/clone_openneuro"),
        ("get", "/clone_openneuro_status"),
        ("get", "/check_datalad_dataset"),
    ],
)
def test_unused_dangerous_routes_are_gone(client, method, path):
    response = getattr(client, method)(path, headers={"X-Prism-Auth": TOKEN})
    assert response.status_code in (404, 405)
