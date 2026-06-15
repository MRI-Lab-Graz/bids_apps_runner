import os
import platform
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from flask import jsonify, request


def register_system_routes(
    app,
    *,
    version: str,
    check_system_dependencies: Callable[[], dict[str, Any]],
    get_active_tracked_run_jobs: Callable[[], list[dict[str, Any]]],
    project_manager_getter: Callable[[], Any],
    find_app_related_pids: Callable[[bool], set[int] | list[int]],
    get_total_memory_bytes: Callable[[], int | None],
    current_machine_id: Callable[[], str],
    read_global_settings_doc: Callable[[], dict[str, Any]],
    write_global_settings_doc: Callable[[dict[str, Any]], None],
    sanitize_machine_settings: Callable[[dict[str, Any]], dict[str, Any]],
    get_effective_machine_settings: Callable[..., dict[str, Any]],
    global_settings_path: Path,
    run_smtp_diagnostics: Callable[[], dict[str, Any]],
    send_run_completion_email: Callable[[str, str, str], tuple[bool, Any]],
):
    @app.route("/health")
    def health():
        return f"Flask is running and responding! (v{version})", 200

    @app.route("/check_system", methods=["GET"])
    def check_system():
        deps = check_system_dependencies()
        deps["os"] = (platform.system() or "unknown").strip().lower()
        return jsonify(deps)

    @app.route("/run_status", methods=["GET"])
    def run_status():
        tracked_jobs = get_active_tracked_run_jobs()
        manager = project_manager_getter()
        jobs = []
        project_counts = {}
        project_names = {}

        for state in tracked_jobs:
            project_id = str(state.get("project_id") or "").strip() or None
            project_name = None
            if project_id:
                project_counts[project_id] = project_counts.get(project_id, 0) + 1
                if project_id not in project_names:
                    try:
                        project = manager.load_project(project_id)
                        if project:
                            project_names[project_id] = (
                                str(project.get("name") or "").strip() or project_id
                            )
                        else:
                            project_names[project_id] = project_id
                    except Exception:
                        project_names[project_id] = project_id
                project_name = project_names.get(project_id, project_id)

            started_at = state.get("started_at")
            started_iso = None
            if isinstance(started_at, (int, float)):
                started_iso = datetime.fromtimestamp(started_at).isoformat(
                    timespec="seconds"
                )

            jobs.append(
                {
                    "run_id": state.get("id"),
                    "project_id": project_id,
                    "project_name": project_name,
                    "pid": state.get("pid"),
                    "started_at": started_iso,
                    "source": "tracked",
                }
            )

        active_projects = [
            {
                "id": project_id,
                "name": project_names.get(project_id, project_id),
                "count": count,
            }
            for project_id, count in sorted(project_counts.items())
        ]

        tracked_pids = {
            int(state.get("pid"))
            for state in tracked_jobs
            if isinstance(state.get("pid"), int)
        }
        detected_pids = sorted(find_app_related_pids(include_marked=True))
        fallback_pids = [pid for pid in detected_pids if pid not in tracked_pids]
        for pid in fallback_pids:
            jobs.append(
                {
                    "run_id": None,
                    "project_id": None,
                    "project_name": None,
                    "pid": pid,
                    "started_at": None,
                    "source": "detected",
                }
            )

        unscoped_count = sum(1 for job in jobs if not job.get("project_id"))
        running = len(jobs) > 0
        return jsonify(
            {
                "running": running,
                "count": len(jobs),
                "active_projects": active_projects,
                "jobs": jobs,
                "detected_pid_count": len(fallback_pids),
                "unscoped_count": unscoped_count,
            }
        )

    @app.route("/system_resources", methods=["GET"])
    def system_resources():
        mem_bytes = get_total_memory_bytes()
        mem_gib = round(mem_bytes / (1024**3), 2) if mem_bytes is not None else None
        return jsonify(
            {
                "cpu_count": os.cpu_count() or 1,
                "memory_total_bytes": mem_bytes,
                "memory_total_gib": mem_gib,
                "gpu_available": shutil.which("nvidia-smi") is not None,
                "slurm_available": shutil.which("sbatch") is not None,
            }
        )

    @app.route("/global_settings", methods=["GET"])
    def get_global_settings():
        machine_id = current_machine_id()
        settings_file_exists = global_settings_path.exists()
        deps = check_system_dependencies()
        doc = read_global_settings_doc()
        effective = get_effective_machine_settings(
            machine_id=machine_id, dependencies=deps
        )
        return (
            jsonify(
                {
                    "machine_id": machine_id,
                    "os": platform.system(),
                    "autodetected": not settings_file_exists,
                    "effective": effective,
                    "default": doc.get("default", {}),
                    "machine_override": doc.get("machines", {}).get(machine_id, {}),
                    "system": deps,
                    "path": str(global_settings_path),
                }
            ),
            200,
        )

    @app.route("/global_settings", methods=["POST"])
    def save_global_settings():
        data = request.get_json(silent=True) or {}
        scope = str(data.get("scope") or "machine").strip().lower()
        settings = data.get("settings")
        if not isinstance(settings, dict):
            return jsonify({"error": "settings must be a JSON object"}), 400

        cleaned = sanitize_machine_settings(settings)
        machine_id = current_machine_id()
        doc = read_global_settings_doc()
        if scope == "default":
            doc["default"].update(cleaned)
        elif scope == "machine":
            doc.setdefault("machines", {})
            doc["machines"][machine_id] = cleaned
        else:
            return jsonify({"error": "scope must be 'machine' or 'default'"}), 400

        try:
            write_global_settings_doc(doc)
        except Exception as exc:
            return jsonify({"error": f"Failed to save global settings: {exc}"}), 500

        deps = check_system_dependencies()
        effective = get_effective_machine_settings(
            machine_id=machine_id, dependencies=deps
        )
        return (
            jsonify(
                {
                    "message": "Global settings saved successfully",
                    "machine_id": machine_id,
                    "effective": effective,
                    "path": str(global_settings_path),
                }
            ),
            200,
        )

    @app.route("/smtp_diagnostics", methods=["POST"])
    def smtp_diagnostics():
        data = request.get_json(silent=True) or {}
        send_test = bool(data.get("send_test", False))
        recipient = str(data.get("recipient") or "").strip()

        diagnostics = run_smtp_diagnostics()
        response = {"diagnostics": diagnostics}
        if send_test:
            if not recipient:
                return (
                    jsonify({"error": "recipient is required when send_test=true"}),
                    400,
                )

            subject = "BIDS App Runner SMTP diagnostic test"
            body = (
                "This is a diagnostic test email from BIDS App Runner.\n"
                f"Time: {datetime.now().isoformat()}\n"
            )
            sent, details = send_run_completion_email(recipient, subject, body)
            response["test_email"] = {
                "recipient": recipient,
                "sent": bool(sent),
                "details": details,
            }

        return jsonify(response), 200
