import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from flask import jsonify, request


def register_project_config_handlers(
    app,
    *,
    project_manager_getter: Callable[[], Any],
    normalize_project_id: Callable[[Any], str],
    resolve_project_dir: Callable[[Any], Path],
    normalize_json_filename: Callable[[Any], str],
    resolve_named_config_path: Callable[[Path, str], Path],
    resolve_config_storage_dir: Callable[[str], Path],
    validate_project_json_shape: Callable[[Any], str | None],
    get_active_tracked_run_jobs: Callable[[], list[dict[str, Any]]],
    data_dir: Path,
    base_dir: Path,
    log_cache: dict[str, Any],
    log_cache_ttl: float,
):
    @app.route("/get_log", methods=["GET"])
    def get_log():
        try:
            raw_project_id = (request.args.get("project_id") or "").strip()
            if not raw_project_id:
                return (
                    jsonify({"content": "", "filename": "none", "is_active": False}),
                    200,
                )

            try:
                project_id = normalize_project_id(raw_project_id)
                project_dir = resolve_project_dir(project_id) / "logs"
            except ValueError:
                return jsonify({"error": "Invalid project id"}), 400

            cache_key = project_id
            cache_entry = log_cache.get(cache_key)
            current_time = time.time()
            if cache_entry and (
                current_time - cache_entry["timestamp"] < log_cache_ttl
            ):
                return jsonify(
                    {
                        "content": cache_entry["content"],
                        "filename": cache_entry["filename"],
                        "is_active": cache_entry["is_active"],
                    }
                )

            tracked_jobs = get_active_tracked_run_jobs()
            has_active_job = any(
                (job.get("project_id") or "") == project_id for job in tracked_jobs
            )

            log_files = (
                sorted(
                    list(project_dir.glob("*.log")), key=os.path.getmtime, reverse=True
                )
                if project_dir.exists()
                else []
            )
            if not log_files:
                return (
                    jsonify({"content": "", "filename": "none", "is_active": False}),
                    200,
                )

            latest_log = log_files[0]
            log_mtime = os.path.getmtime(latest_log)
            is_recently_active = (current_time - log_mtime) < 300
            if not (has_active_job or is_recently_active):
                return (
                    jsonify({"content": "", "filename": "none", "is_active": False}),
                    200,
                )

            result = subprocess.run(
                ["tail", "-n", "150", latest_log], capture_output=True, text=True
            )
            content = result.stdout
            ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
            content = ansi_escape.sub("", content)

            if not has_active_job and not is_recently_active:
                content = (
                    "[Idle - Last activity: "
                    + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(log_mtime))
                    + "]\n"
                    + content
                )

            log_cache[cache_key] = {
                "timestamp": current_time,
                "content": content,
                "filename": (
                    os.path.basename(latest_log)
                    if (has_active_job or is_recently_active)
                    else "none"
                ),
                "is_active": has_active_job or is_recently_active,
            }
            return jsonify(
                {
                    "filename": log_cache[cache_key]["filename"],
                    "content": log_cache[cache_key]["content"],
                    "is_active": log_cache[cache_key]["is_active"],
                }
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/get_projects", methods=["GET"])
    def get_projects():
        try:
            manager = project_manager_getter()
            limit = 5
            projects = manager.list_projects(limit=limit)
            total_projects = manager.count_projects()
            return (
                jsonify(
                    {
                        "projects": projects,
                        "limit": limit,
                        "total_projects": total_projects,
                    }
                ),
                200,
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/create_project", methods=["POST"])
    def create_project():
        try:
            manager = project_manager_getter()
            data = request.get_json(silent=True) or {}
            name = str(data.get("name") or "").strip()
            description = str(data.get("description") or "").strip()
            if not name:
                return jsonify({"error": "Project name is required"}), 400
            project_id, project_json = manager.create_project(name, description)
            return jsonify({"project_id": project_id, "project": project_json}), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/load_project/<project_id>", methods=["GET"])
    def load_project(project_id):
        try:
            manager = project_manager_getter()
            normalize_project_id(project_id)
            project_json = manager.load_project(project_id)
            if not project_json:
                return jsonify({"error": f"Project {project_id} not found"}), 404
            return jsonify(project_json), 200
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/save_project/<project_id>", methods=["POST"])
    def save_project(project_id):
        try:
            manager = project_manager_getter()
            normalize_project_id(project_id)
            data = request.get_json(silent=True) or {}
            config = data.get("config")
            if not config:
                return jsonify({"error": "Config is required"}), 400
            success = manager.save_project(project_id, config)
            if not success:
                return jsonify({"error": f"Project {project_id} not found"}), 404
            return jsonify({"message": "Project saved successfully"}), 200
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/delete_project/<project_id>", methods=["DELETE"])
    def delete_project(project_id):
        try:
            manager = project_manager_getter()
            normalize_project_id(project_id)
            success = manager.delete_project(project_id)
            if not success:
                return jsonify({"error": f"Project {project_id} not found"}), 404
            return jsonify({"message": "Project deleted successfully"}), 200
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/load_project_file", methods=["POST"])
    def load_project_file():
        try:
            payload = request.get_json(silent=True) or {}
            path = str(payload.get("path") or "").strip()
            if not path:
                return jsonify({"error": "Path is required"}), 400

            selected = Path(path)
            if not selected.exists() or not selected.is_file():
                return jsonify({"error": "File not found"}), 404
            if selected.name != "project.json":
                return jsonify({"error": "Please select a project.json file"}), 400

            with open(selected, "r", encoding="utf-8") as handle:
                project_json = json.load(handle)

            validation_error = validate_project_json_shape(project_json)
            if validation_error:
                return jsonify({"error": validation_error}), 400
            return jsonify(project_json), 200
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/list_configs", methods=["GET"])
    def list_configs():
        try:
            combined_configs = set()
            default_dir = base_dir / "configs"
            if default_dir.exists():
                combined_configs.update(
                    [item for item in os.listdir(default_dir) if item.endswith(".json")]
                )

            user_dir = data_dir / "configs"
            if user_dir.exists():
                combined_configs.update(
                    [item for item in os.listdir(user_dir) if item.endswith(".json")]
                )

            return jsonify({"configs": sorted(list(combined_configs))})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/get_config", methods=["GET"])
    def get_config():
        try:
            name = normalize_json_filename(request.args.get("name"))
            config_path = resolve_named_config_path(data_dir / "configs", name)
            if not config_path.exists():
                config_path = resolve_named_config_path(base_dir / "configs", name)
            if not config_path.exists():
                return jsonify({"error": f"Config {name} not found"}), 404

            with open(config_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return jsonify({"config": data})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/save_config", methods=["POST"])
    def save_config():
        data = request.get_json(silent=True) or {}
        filename = data.get("filename", "config.json")
        config_data = data.get("config")
        save_dir = str(data.get("config_folder") or "").strip()
        project_id = data.get("project_id")

        if not config_data:
            return jsonify({"error": "No config data provided"}), 400

        try:
            manager = project_manager_getter()
            if project_id:
                normalize_project_id(project_id)
                success = manager.save_project(project_id, config_data)
                if not success:
                    return jsonify({"error": f"Project {project_id} not found"}), 404

                project = manager.load_project(project_id)
                config_path = f"projects/{project_id}/project.json"
                return jsonify(
                    {
                        "message": "Project config saved successfully",
                        "path": config_path,
                        "project": project,
                    }
                )

            filename = normalize_json_filename(filename)
            config_dir = resolve_config_storage_dir(save_dir)
            config_path = resolve_named_config_path(config_dir, filename)
            os.makedirs(config_path.parent, exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as handle:
                json.dump(config_data, handle, indent=2)

            return jsonify(
                {
                    "message": f"Config saved successfully to {config_path}",
                    "path": str(config_path),
                }
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
