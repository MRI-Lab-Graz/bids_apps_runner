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
        with open(log_file, "w") as lf:
            process = subprocess.Popen(
                cmd,
                stdout=lf,
                stderr=subprocess.STDOUT,
                text=True,
            )
        with _cohort_jobs_lock:
            _cohort_jobs[job_id]["pid"] = process.pid

        returncode = process.wait()
        with _cohort_jobs_lock:
            _cohort_jobs[job_id]["returncode"] = returncode
            _cohort_jobs[job_id]["status"] = "completed" if returncode == 0 else "failed"
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
) -> None:
    cohort_script = base_dir / "scripts" / "submit_bids_cohort.sh"
    default_config = base_dir / "configs" / "cohort_hpc_example.json"

    @app.route("/cohort/run", methods=["POST"])
    def cohort_run():
        data = request.get_json(silent=True) or {}
        command = (data.get("command") or "").strip()
        if command not in {"setup", "submit", "status"}:
            return jsonify({"error": f"Invalid command: {command}"}), 400

        config_path = (data.get("config_path") or str(default_config)).strip()
        dry_run = bool(data.get("dry_run", False))
        datasets = [d.strip() for d in (data.get("datasets") or "").split() if d.strip()]

        if not Path(config_path).exists():
            return jsonify({"error": f"Config not found: {config_path}"}), 400

        ensure_logs_dir()
        job_id = f"cohort_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        log_file = log_dir / f"{job_id}.log"

        cmd = ["bash", str(cohort_script), command, "--config", config_path]
        if dry_run:
            cmd.append("--dry-run")
        for ds in datasets:
            cmd += ["-d", ds]

        with _cohort_jobs_lock:
            _cohort_jobs[job_id] = {
                "id": job_id,
                "command": command,
                "status": "starting",
                "log_file": str(log_file),
                "pid": None,
                "returncode": None,
                "error": None,
                "started_at": time.time(),
                "finished_at": None,
            }

        threading.Thread(
            target=_run_cohort_async, args=(job_id, cmd, log_file), daemon=True
        ).start()

        return jsonify({"job_id": job_id, "status": "starting", "log_file": str(log_file)})

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

        return jsonify({
            "job_id": job_id,
            "command": state.get("command"),
            "status": state.get("status"),
            "returncode": state.get("returncode"),
            "error": state.get("error"),
            "log_tail": log_tail,
        })

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

    @app.route("/cohort/load_config", methods=["GET"])
    def cohort_load_config():
        path = (request.args.get("path") or "").strip()
        if not path:
            path = str(default_config)
        target = Path(path)
        if not target.exists():
            return jsonify({"error": f"File not found: {path}"}), 404
        try:
            content = target.read_text(encoding="utf-8")
            json.loads(content)  # validate before sending
        except (OSError, json.JSONDecodeError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"path": str(target), "content": content})

    @app.route("/cohort/save_config", methods=["POST"])
    def cohort_save_config():
        data = request.get_json(silent=True) or {}
        path = (data.get("path") or "").strip()
        content = data.get("content", "")
        if not path:
            return jsonify({"error": "path required"}), 400
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            return jsonify({"error": f"Invalid JSON: {exc}"}), 400
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(parsed, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"path": str(target), "saved": True})
