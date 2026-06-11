import os
import re
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from flask import jsonify, request


def register_utility_routes(
    app,
    *,
    data_dir: Path,
    ensure_logs_dir: Callable[[], None],
    prepare_apptainer_build: Callable[[dict[str, Any]], tuple[dict[str, Any] | None, Any]],
    run_apptainer_build_async: Callable[[str], None],
    apptainer_builds: dict[str, dict[str, Any]],
    apptainer_builds_lock,
    read_log_tail: Callable[[str, int], str] | Callable[[str], str],
):
    @app.route("/pull_image", methods=["POST"])
    def pull_image():
        data = request.get_json(silent=True) or {}
        image = data.get("image")
        engine = data.get("engine", "docker")

        if not image:
            return jsonify({"error": "No image name provided"}), 400

        try:
            ensure_logs_dir()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = Path(data_dir) / f"nohup_bids_runner_pull_{timestamp}.log"

            print(f"[GUI] Pulling image: {image} using {engine}...", flush=True)
            if engine == "docker":
                cmd = ["docker", "pull", image]
            else:
                return jsonify({"error": "Pull only implemented for Docker engine"}), 400

            def run_pull():
                try:
                    with open(log_file, "w", encoding="utf-8") as handle:
                        handle.write(
                            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Started pulling {image}...\n\n"
                        )
                        handle.flush()
                        print(f"[GUI] Pulling image: {image}...", flush=True)
                        process = subprocess.Popen(
                            cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                        )
                        for line in process.stdout or []:
                            if line.strip():
                                line_out = f"[DOCKER] {line.strip()}"
                                print(line_out, flush=True)
                                handle.write(line_out + "\n")
                                handle.flush()
                        process.wait()

                        if process.returncode == 0:
                            msg = f"\n[GUI] Successfully pulled {image}"
                            print(msg, flush=True)
                            handle.write(msg + "\n")
                        else:
                            msg = (
                                f"\n[GUI] Docker pull failed for {image} with return code {process.returncode}"
                            )
                            print(msg, flush=True)
                            handle.write(msg + "\n")
                except Exception as exc:
                    err_msg = f"\n[GUI] Error pulling {image}: {str(exc)}"
                    print(err_msg, flush=True)
                    with open(log_file, "a", encoding="utf-8") as handle:
                        handle.write(err_msg + "\n")

            threading.Thread(target=run_pull, daemon=True).start()
            return jsonify(
                {
                    "message": f"Started pulling {image} in the background. Check console output."
                }
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/make_dir", methods=["POST"])
    def make_dir():
        data = request.get_json(silent=True) or {}
        path = data.get("path")
        name = data.get("name")
        if not path or not name:
            return jsonify({"error": "Path and name are required"}), 400

        try:
            name = str(name).strip()
            if not name or "/" in name or "\\" in name or name in {".", ".."}:
                return jsonify({"error": "Directory name must be a single path component"}), 400

            new_dir = Path(os.path.expanduser(str(path))).resolve() / name
            new_dir.mkdir(parents=True, exist_ok=True)
            return jsonify({"message": f"Directory created: {new_dir}", "path": str(new_dir)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/build_apptainer", methods=["POST"])
    def build_apptainer():
        data = request.get_json(silent=True) or {}
        prepared, error_response = prepare_apptainer_build(data)
        if error_response:
            return error_response

        build_id = f"build_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        with apptainer_builds_lock:
            apptainer_builds[build_id] = {
                "id": build_id,
                "status": "running",
                "steps": prepared["steps"],
                "log_file": prepared["log_file"],
                "output_image": prepared["output_image"],
                "per_build_dir": prepared["per_build_dir"],
                "sandbox_dir": prepared.get("sandbox_dir"),
                "keep_temp": prepared["keep_temp"],
                "timeout_seconds": prepared["timeout_seconds"],
                "cwd": prepared["cwd"],
                "env": prepared["env"],
                "process": None,
                "pid": None,
                "cancel_requested": False,
                "returncode": None,
                "error": None,
                "started_at": time.time(),
                "finished_at": None,
            }

        worker = threading.Thread(
            target=run_apptainer_build_async,
            args=(build_id,),
            daemon=True,
        )
        worker.start()

        return jsonify(
            {
                "success": True,
                "build_id": build_id,
                "status": "running",
                "log_file": prepared["log_file"],
                "output_image": prepared["output_image"],
            }
        )

    @app.route("/build_apptainer_status", methods=["GET"])
    def build_apptainer_status():
        build_id = (request.args.get("build_id") or "").strip()
        if not build_id:
            return jsonify({"error": "build_id is required"}), 400

        with apptainer_builds_lock:
            state = apptainer_builds.get(build_id)
            if not state:
                return jsonify({"error": "Build not found"}), 404
            status = state.get("status", "unknown")
            returncode = state.get("returncode")
            output_image = state.get("output_image")
            log_file = state.get("log_file")
            error = state.get("error")
            pid = state.get("pid")

        log_tail = read_log_tail(log_file)
        if not output_image and status in {"completed", "failed"}:
            match = re.search(r"Apptainer image built successfully at:\s*(.+)", log_tail)
            if match:
                output_image = match.group(1).strip()

        return jsonify(
            {
                "build_id": build_id,
                "status": status,
                "success": status == "completed",
                "returncode": returncode,
                "output_image": output_image,
                "log_file": log_file,
                "log_tail": log_tail,
                "error": error,
                "pid": pid,
            }
        )

    @app.route("/build_apptainer_cancel", methods=["POST"])
    def build_apptainer_cancel():
        data = request.get_json(silent=True) or {}
        build_id = (data.get("build_id") or "").strip()
        if not build_id:
            return jsonify({"error": "build_id is required"}), 400

        with apptainer_builds_lock:
            state = apptainer_builds.get(build_id)
            if not state:
                return jsonify({"error": "Build not found"}), 404
            status = state.get("status")
            process = state.get("process")

            if status != "running":
                return jsonify(
                    {
                        "success": True,
                        "status": status,
                        "message": "Build already finished.",
                    }
                )

            state["cancel_requested"] = True

        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                pass

        return jsonify({"success": True, "status": "cancelling", "build_id": build_id})
