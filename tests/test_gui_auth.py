from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

import prism_app_runner
from gui_projects import ProjectStore


@pytest.fixture
def isolated_project_store(tmp_path):
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    return ProjectStore(
        projects_dir,
        machine_settings_provider=prism_app_runner._get_effective_machine_settings,
        config_normalizer=prism_app_runner._coerce_project_config_shape,
        project_dir_resolver=lambda project_id: prism_app_runner._resolve_project_dir(
            projects_dir, project_id
        ),
        timestamp_factory=lambda: "2026-05-28T00:00:00",
    )


@pytest.fixture
def client(monkeypatch, isolated_project_store):
    monkeypatch.setattr(prism_app_runner, "GUI_LOGIN_ENABLED", True)
    monkeypatch.setattr(
        prism_app_runner, "GUI_LOGIN_PASSWORD_HASH", generate_password_hash("secret-pass")
    )
    monkeypatch.setattr(prism_app_runner, "GUI_AUTH_TOKEN", "")
    monkeypatch.setattr(prism_app_runner, "ProjectManager", isolated_project_store)
    prism_app_runner.app.config["TESTING"] = True

    with prism_app_runner.app.test_client() as test_client:
        yield test_client


def test_index_redirects_to_login_when_auth_required(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_health_is_public_without_login(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert b"Flask is running and responding" in response.data


def test_login_requires_csrf_token(client):
    response = client.post("/login", data={"password": "secret-pass"})

    assert response.status_code == 400
    assert b"CSRF token missing or invalid" in response.data


def test_login_and_csrf_protected_post_flow(client):
    login_page = client.get("/login")
    assert login_page.status_code == 200

    with client.session_transaction() as session:
        login_csrf = session["csrf_token"]

    response = client.post(
        "/login",
        data={"password": "secret-pass", "csrf_token": login_csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302

    with client.session_transaction() as session:
        assert session["authenticated"] is True
        post_login_csrf = session["csrf_token"]

    missing_csrf = client.post(
        "/create_project",
        json={"name": "NoToken", "description": "blocked"},
    )
    assert missing_csrf.status_code == 400

    created = client.post(
        "/create_project",
        json={"name": "WithToken", "description": "allowed"},
        headers={"X-CSRF-Token": post_login_csrf},
    )
    assert created.status_code == 201
    payload = created.get_json()
    assert payload["project_id"] == "withtoken"


def _login_client(client):
    login_page = client.get("/login")
    assert login_page.status_code == 200

    with client.session_transaction() as session:
        login_csrf = session["csrf_token"]

    response = client.post(
        "/login",
        data={"password": "secret-pass", "csrf_token": login_csrf},
        follow_redirects=False,
    )
    assert response.status_code == 302

    with client.session_transaction() as session:
        assert session["authenticated"] is True
        return session["csrf_token"]


def test_make_dir_rejects_path_traversal_name(client, tmp_path):
    csrf_token = _login_client(client)

    response = client.post(
        "/make_dir",
        json={"path": str(tmp_path), "name": "../bad"},
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "Directory name must be a single path component"


def test_build_apptainer_status_requires_build_id(client):
    csrf_token = _login_client(client)

    response = client.get(
        "/build_apptainer_status",
        headers={"X-CSRF-Token": csrf_token},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "build_id is required"


def test_remote_token_bypasses_login_and_csrf(monkeypatch, isolated_project_store):
    monkeypatch.setattr(prism_app_runner, "GUI_LOGIN_ENABLED", True)
    monkeypatch.setattr(
        prism_app_runner, "GUI_LOGIN_PASSWORD_HASH", generate_password_hash("secret-pass")
    )
    monkeypatch.setattr(prism_app_runner, "GUI_AUTH_TOKEN", "remote-token")
    monkeypatch.setattr(prism_app_runner, "ProjectManager", isolated_project_store)
    prism_app_runner.app.config["TESTING"] = True

    with prism_app_runner.app.test_client() as test_client:
        response = test_client.post(
            "/create_project",
            json={"name": "RemoteToken", "description": "token auth"},
            headers={prism_app_runner.GUI_AUTH_HEADER: "remote-token"},
        )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["project_id"] == "remotetoken"
