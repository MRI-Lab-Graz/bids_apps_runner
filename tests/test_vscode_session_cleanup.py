import os
import time
from pathlib import Path

import pytest
from flask import Flask

import prism_app_runner
import gui.gui_system_routes as gui_system_routes


# ── _find_vscode_remote_ssh_stacks() unit tests ─────────────────────────────


def test_process_start_epoch_matches_real_process():
    # Unmocked sanity check against this actual test process -- confirms
    # the /proc/<pid>/stat parsing arithmetic (boot time + starttime ticks)
    # is right, not just internally self-consistent.
    started = prism_app_runner._process_start_epoch(os.getpid())
    assert started is not None
    assert started <= time.time()
    assert time.time() - started < 3600


def test_find_vscode_remote_ssh_stacks_groups_by_hash_and_flags_stale(monkeypatch):
    fake_cmdlines = {
        101: "/x/.vscode-server/cli/servers/stable-aaaa1111/server/node server-main.js",
        102: "/x/.vscode-server/cli/servers/stable-aaaa1111/server/node extensionhost",
        201: "/x/.vscode-server/cli/servers/stable-bbbb2222/server/node server-main.js",
        999: "/usr/bin/some-unrelated-process",
    }
    monkeypatch.setattr(prism_app_runner, "_iter_proc_pids", lambda: list(fake_cmdlines))
    monkeypatch.setattr(
        prism_app_runner, "_read_proc_cmdline", lambda pid: fake_cmdlines[pid]
    )
    monkeypatch.setattr(prism_app_runner.os, "getuid", lambda: 4242)
    monkeypatch.setattr(
        prism_app_runner.os, "stat", lambda path: type("S", (), {"st_uid": 4242})()
    )

    now = 1_000_000.0
    monkeypatch.setattr(prism_app_runner.time, "time", lambda: now)
    starts = {101: now - 20 * 3600, 102: now - 19 * 3600, 201: now - 60}
    monkeypatch.setattr(
        prism_app_runner, "_process_start_epoch", lambda pid: starts[pid]
    )
    monkeypatch.setattr(prism_app_runner, "_process_rss_bytes", lambda pid: 100)

    stacks = prism_app_runner._find_vscode_remote_ssh_stacks()

    by_hash = {s["hash"]: s for s in stacks}
    assert set(by_hash) == {"aaaa1111", "bbbb2222"}
    assert sorted(by_hash["aaaa1111"]["pids"]) == [101, 102]
    assert by_hash["aaaa1111"]["stale"] is True  # oldest pid is 20h old
    assert by_hash["bbbb2222"]["stale"] is False  # 60s old
    assert by_hash["aaaa1111"]["rss_bytes"] == 200
    # oldest stack first
    assert stacks[0]["hash"] == "aaaa1111"


def test_find_vscode_remote_ssh_stacks_ignores_other_users(monkeypatch):
    fake_cmdlines = {
        1: "/x/.vscode-server/cli/servers/stable-aaaa0000/server/node server-main.js",
        2: "/x/.vscode-server/cli/servers/stable-bbbb1111/server/node server-main.js",
    }
    uid_by_pid = {1: 111, 2: 222}
    monkeypatch.setattr(prism_app_runner, "_iter_proc_pids", lambda: list(fake_cmdlines))
    monkeypatch.setattr(
        prism_app_runner, "_read_proc_cmdline", lambda pid: fake_cmdlines[pid]
    )
    monkeypatch.setattr(prism_app_runner.os, "getuid", lambda: 111)
    monkeypatch.setattr(
        prism_app_runner.os,
        "stat",
        lambda path: type("S", (), {"st_uid": uid_by_pid[int(path.split("/")[2])]})(),
    )
    monkeypatch.setattr(prism_app_runner, "_process_start_epoch", lambda pid: None)
    monkeypatch.setattr(prism_app_runner, "_process_rss_bytes", lambda pid: 0)

    stacks = prism_app_runner._find_vscode_remote_ssh_stacks()

    assert [s["hash"] for s in stacks] == ["aaaa0000"]


# ── route tests (isolated blueprint, fake callables) ────────────────────────


def _make_app(find_stacks=None, terminate=None):
    app = Flask(__name__)
    gui_system_routes.register_system_routes(
        app,
        version="test",
        check_system_dependencies=lambda: {},
        get_active_tracked_run_jobs=lambda: [],
        project_manager_getter=lambda: None,
        find_app_related_pids=lambda include_marked=True: set(),
        find_vscode_remote_ssh_stacks=find_stacks or (lambda: []),
        terminate_pid_groups=terminate or (lambda pids: 0),
        get_total_memory_bytes=lambda: None,
        current_machine_id=lambda: "test-machine",
        read_global_settings_doc=lambda: {"default": {}, "machines": {}},
        write_global_settings_doc=lambda doc: None,
        sanitize_machine_settings=lambda s: s,
        get_effective_machine_settings=lambda **kw: {},
        global_settings_path=Path("/tmp/does-not-matter.json"),
        run_smtp_diagnostics=lambda: {},
        send_run_completion_email=lambda *a: (True, {}),
    )
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client_with_stacks():
    stacks = [
        {"hash": "old1111", "pids": [10, 11], "age_seconds": 50000, "rss_bytes": 3000, "stale": True},
        {"hash": "new2222", "pids": [20], "age_seconds": 60, "rss_bytes": 500, "stale": False},
    ]
    calls = []
    app = _make_app(
        find_stacks=lambda: stacks,
        terminate=lambda pids: calls.append(list(pids)) or len(pids),
    )
    with app.test_client() as c:
        yield c, calls


def test_check_vscode_sessions_reports_stale_and_totals(client_with_stacks):
    client, _ = client_with_stacks
    resp = client.get("/check_vscode_sessions")
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["total_count"] == 2
    assert data["stale_count"] == 1
    assert data["stale_rss_bytes"] == 3000
    assert {s["hash"] for s in data["stacks"]} == {"old1111", "new2222"}


def test_check_vscode_sessions_empty():
    app = _make_app()
    with app.test_client() as client:
        resp = client.get("/check_vscode_sessions")
        data = resp.get_json()
    assert data == {"stacks": [], "total_count": 0, "stale_count": 0, "stale_rss_bytes": 0}


def test_cleanup_vscode_session_requires_hash():
    app = _make_app()
    with app.test_client() as client:
        resp = client.post("/cleanup_vscode_session", json={})
    assert resp.status_code == 400


def test_cleanup_vscode_session_not_found(client_with_stacks):
    client, calls = client_with_stacks
    resp = client.post("/cleanup_vscode_session", json={"hash": "doesnotexist"})
    assert resp.status_code == 404
    assert calls == []


def test_cleanup_vscode_session_success(client_with_stacks):
    client, calls = client_with_stacks
    resp = client.post("/cleanup_vscode_session", json={"hash": "old1111"})
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["pid_count"] == 2
    assert calls == [[10, 11]]
