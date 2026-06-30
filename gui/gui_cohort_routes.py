import json
import logging
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from flask import jsonify, request

_cohort_jobs: dict[str, Any] = {}
_cohort_jobs_lock = threading.Lock()


def _run_cohort_async(job_id: str, cmd: list[str], log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with _cohort_jobs_lock:
        _cohort_jobs[job_id]["status"] = "running"

    try:
        import os as _os
        env = _os.environ.copy()
        # Ensure ~/.local/bin is on PATH so datalad-slurm extension is visible
        local_bin = str(Path.home() / ".local" / "bin")
        if local_bin not in env.get("PATH", ""):
            env["PATH"] = local_bin + ":" + env.get("PATH", "")
        with open(log_file, "w") as lf:
            process = subprocess.Popen(
                cmd,
                stdout=lf,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
        with _cohort_jobs_lock:
            _cohort_jobs[job_id]["pid"] = process.pid

        returncode = process.wait()
        with _cohort_jobs_lock:
            _cohort_jobs[job_id]["returncode"] = returncode
            _cohort_jobs[job_id]["status"] = (
                "completed" if returncode == 0 else "failed"
            )
            _cohort_jobs[job_id]["finished_at"] = time.time()
    except Exception as exc:
        logging.exception("Cohort job %s crashed", job_id)
        with _cohort_jobs_lock:
            _cohort_jobs[job_id]["status"] = "failed"
            _cohort_jobs[job_id]["error"] = str(exc)
            _cohort_jobs[job_id]["finished_at"] = time.time()


def register_cohort_routes(
    app,
    *,
    base_dir: Path,
    log_dir: Path,
    ensure_logs_dir: Callable[[], None],
    resolve_project_dir: Callable[[str], Path],
    load_project: Callable[[str], dict[str, Any] | None],
    extract_runtime_config: Callable[[dict[str, Any], str | None], dict[str, Any]],
    derive_cohort_config: Callable[..., dict[str, Any]],
) -> None:
    cohort_script = base_dir / "scripts" / "submit_bids_cohort.sh"

    def _build_cohort_config(project_id, pipeline_id, max_concurrent):
        """Load the project, derive its cohort config, and validate it's
        actually runnable. Returns (cohort_cfg, error_response_or_None)."""
        if not project_id:
            return None, (jsonify({"error": "project_id is required"}), 400)

        project_json = load_project(project_id)
        if project_json is None:
            return None, (jsonify({"error": f"Project not found: {project_id}"}), 404)

        runtime_cfg = extract_runtime_config(project_json, pipeline_id or None)
        project_dir = resolve_project_dir(project_id)

        try:
            cohort_cfg = derive_cohort_config(
                runtime_cfg,
                project_dir=project_dir,
                max_concurrent=max_concurrent,
            )
        except ValueError as exc:
            return None, (jsonify({"error": str(exc)}), 400)

        container = cohort_cfg["paths"]["container"]
        if not container or not os.path.exists(container):
            return None, (
                jsonify({"error": f"Container Image not found: {container}"}),
                400,
            )

        return cohort_cfg, None

    @app.route("/cohort/preview_config", methods=["GET"])
    def cohort_preview_config():
        """Return the cohort config that would be generated for a project,
        without running anything -- lets the GUI show "what will actually
        run" before Setup/Submit, since the config is no longer hand-edited.
        """
        project_id = (request.args.get("project_id") or "").strip()
        pipeline_id = (request.args.get("pipeline_id") or "").strip()
        max_concurrent = request.args.get("max_concurrent")

        cohort_cfg, error_response = _build_cohort_config(
            project_id, pipeline_id, max_concurrent
        )
        if error_response:
            return error_response
        return jsonify({"config": cohort_cfg})

    @app.route("/cohort/run", methods=["POST"])
    def cohort_run():
        data = request.get_json(silent=True) or {}
        command = (data.get("command") or "").strip()
        if command not in {"setup", "submit", "status"}:
            return jsonify({"error": f"Invalid command: {command}"}), 400

        project_id = (data.get("project_id") or "").strip()
        pipeline_id = (data.get("pipeline_id") or "").strip()
        dry_run = bool(data.get("dry_run", False))
        max_concurrent = data.get("max_concurrent")

        cohort_cfg, error_response = _build_cohort_config(
            project_id, pipeline_id, max_concurrent
        )
        if error_response:
            return error_response

        project_dir = resolve_project_dir(project_id)
        cohort_dir = project_dir / "logs" / "cohort"
        cohort_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        generated_config_path = cohort_dir / f"cohort_config_{timestamp}.json"
        generated_config_path.write_text(
            json.dumps(cohort_cfg, indent=2) + "\n", encoding="utf-8"
        )

        ensure_logs_dir()
        job_id = (
            f"cohort_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        )
        log_file = log_dir / f"{job_id}.log"

        cmd = ["bash", str(cohort_script), command, "--config", str(generated_config_path)]
        if dry_run:
            cmd.append("--dry-run")

        with _cohort_jobs_lock:
            _cohort_jobs[job_id] = {
                "id": job_id,
                "command": command,
                "status": "starting",
                "log_file": str(log_file),
                "config_path": str(generated_config_path),
                "pid": None,
                "returncode": None,
                "error": None,
                "started_at": time.time(),
                "finished_at": None,
            }

        threading.Thread(
            target=_run_cohort_async, args=(job_id, cmd, log_file), daemon=True
        ).start()

        return jsonify(
            {
                "job_id": job_id,
                "status": "starting",
                "log_file": str(log_file),
                "config_path": str(generated_config_path),
            }
        )

    @app.route("/cohort/job_status", methods=["GET"])
    def cohort_job_status():
        job_id = (request.args.get("job_id") or "").strip()
        if not job_id:
            return jsonify({"error": "job_id required"}), 400

        with _cohort_jobs_lock:
            state = _cohort_jobs.get(job_id)
        if not state:
            return jsonify({"error": "job not found"}), 404

        log_file = state.get("log_file", "")
        log_tail = ""
        if log_file and Path(log_file).exists():
            try:
                with open(log_file, "r", errors="replace") as f:
                    log_tail = f.read()
            except OSError:
                pass

        return jsonify(
            {
                "job_id": job_id,
                "command": state.get("command"),
                "status": state.get("status"),
                "returncode": state.get("returncode"),
                "error": state.get("error"),
                "log_tail": log_tail,
            }
        )

    @app.route("/cohort/cancel", methods=["POST"])
    def cohort_cancel():
        data = request.get_json(silent=True) or {}
        job_id = (data.get("job_id") or "").strip()
        if not job_id:
            return jsonify({"error": "job_id required"}), 400

        with _cohort_jobs_lock:
            state = _cohort_jobs.get(job_id)
        if not state:
            return jsonify({"error": "job not found"}), 404

        pid = state.get("pid")
        if pid:
            try:
                os.kill(pid, 15)
                with _cohort_jobs_lock:
                    _cohort_jobs[job_id]["status"] = "cancelled"
            except ProcessLookupError:
                pass

        return jsonify({"job_id": job_id, "status": "cancelled"})
