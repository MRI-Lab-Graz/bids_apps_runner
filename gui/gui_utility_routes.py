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

# Clone jobs are stored here by clone_id so the GUI can poll for status.
_clone_jobs: dict[str, dict[str, Any]] = {}
_clone_jobs_lock = threading.Lock()


def register_utility_routes(
    app,
    *,
    data_dir: Path,
    ensure_logs_dir: Callable[[], None],
    prepare_apptainer_build: Callable[
        [dict[str, Any]], tuple[dict[str, Any] | None, Any]
    ],
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
                return (
                    jsonify({"error": "Pull only implemented for Docker engine"}),
                    400,
                )

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
                            msg = f"\n[GUI] Docker pull failed for {image} with return code {process.returncode}"
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
                return (
                    jsonify(
                        {"error": "Directory name must be a single path component"}
                    ),
                    400,
                )

            new_dir = Path(os.path.expanduser(str(path))).resolve() / name
            new_dir.mkdir(parents=True, exist_ok=True)
            return jsonify(
                {"message": f"Directory created: {new_dir}", "path": str(new_dir)}
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/build_apptainer", methods=["POST"])
    def build_apptainer():
        data = request.get_json(silent=True) or {}
        prepared, error_response = prepare_apptainer_build(data)
        if error_response:
            return error_response

        build_id = (
            f"build_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        )
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
            match = re.search(
                r"Apptainer image built successfully at:\s*(.+)", log_tail
            )
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

    # ------------------------------------------------------------------
    # DataLad / OpenNeuro routes
    # ------------------------------------------------------------------

    @app.route("/check_datalad_dataset", methods=["GET"])
    def check_datalad_dataset():
        """Return whether a local folder is a DataLad dataset and whether
        DataLad is available on the server PATH.

        Query params:
          path  (required) – absolute path to the folder to inspect
        """
        path = (request.args.get("path") or "").strip()
        if not path:
            return jsonify({"error": "path query parameter is required"}), 400

        try:
            import prism_datalad  # lazy – scripts/ is on sys.path at runtime
        except ImportError:
            return (
                jsonify(
                    {
                        "path": path,
                        "is_datalad": False,
                        "datalad_available": False,
                        "error": "prism_datalad module not found on server",
                    }
                ),
                500,
            )

        expanded = os.path.expanduser(path)
        is_ds = prism_datalad.is_datalad_dataset(expanded)
        dl_ok = prism_datalad.check_datalad_available()

        return jsonify(
            {
                "path": expanded,
                "is_datalad": is_ds,
                "datalad_available": dl_ok,
            }
        )

    @app.route("/clone_openneuro", methods=["POST"])
    def clone_openneuro():
        """Start an async DataLad clone of an OpenNeuro dataset.

        Request body (JSON):
          dataset   (required) – accession ID ("ds005239"), openneuro.org URL,
                                  or full GitHub URL
          target    (required) – absolute local path where the dataset should
                                  be cloned (must not exist yet)

        Response:
          clone_id  – poll /clone_openneuro_status?clone_id=<id> for progress
        """
        data = request.get_json(silent=True) or {}
        dataset = (data.get("dataset") or "").strip()
        target = (data.get("target") or "").strip()

        if not dataset:
            return jsonify({"error": "dataset is required"}), 400
        if not target:
            return jsonify({"error": "target directory is required"}), 400

        target = os.path.expanduser(target)

        try:
            import prism_datalad
        except ImportError:
            return jsonify({"error": "prism_datalad module not found on server"}), 500

        if not prism_datalad.check_datalad_available():
            return (
                jsonify(
                    {
                        "error": (
                            "DataLad is not available on the server. "
                            "Install it with: pip install datalad (and ensure git-annex is on PATH)."
                        )
                    }
                ),
                400,
            )

        # Validate / resolve URL before spawning the thread so we can return
        # a helpful error immediately rather than after a long delay.
        try:
            resolved_url = prism_datalad.resolve_openneuro_url(dataset)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        if os.path.exists(target):
            return (
                jsonify(
                    {
                        "error": (
                            f"Target path already exists: {target}. "
                            "Remove it or choose a different directory."
                        )
                    }
                ),
                400,
            )

        ensure_logs_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = str(Path(data_dir) / f"nohup_bids_runner_clone_{timestamp}.log")
        clone_id = f"clone_{timestamp}_{uuid.uuid4().hex[:8]}"

        with _clone_jobs_lock:
            _clone_jobs[clone_id] = {
                "clone_id": clone_id,
                "dataset": dataset,
                "resolved_url": resolved_url,
                "target": target,
                "log_file": log_file,
                "status": "running",
                "returncode": None,
                "error": None,
                "started_at": time.time(),
                "finished_at": None,
            }

        def _run_clone():
            try:
                with open(log_file, "w", encoding="utf-8") as fh:
                    fh.write(
                        f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                        f"Cloning {resolved_url} → {target}\n\n"
                    )
                    fh.flush()

                    proc = subprocess.Popen(
                        ["datalad", "clone", resolved_url, target],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                    for line in proc.stdout or []:
                        fh.write(line)
                        fh.flush()
                    returncode = proc.wait()

                    status = "completed" if returncode == 0 else "failed"
                    fh.write(
                        f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                        f"Clone {status} (exit code {returncode})\n"
                    )

                with _clone_jobs_lock:
                    job = _clone_jobs.get(clone_id, {})
                    job["status"] = status
                    job["returncode"] = returncode
                    job["finished_at"] = time.time()
                    if returncode != 0:
                        job["error"] = f"datalad clone exited with code {returncode}"

            except Exception as exc:
                err = str(exc)
                try:
                    with open(log_file, "a", encoding="utf-8") as fh:
                        fh.write(f"\n[ERROR] {err}\n")
                except OSError:
                    pass
                with _clone_jobs_lock:
                    job = _clone_jobs.get(clone_id, {})
                    job["status"] = "failed"
                    job["error"] = err
                    job["finished_at"] = time.time()

        threading.Thread(target=_run_clone, daemon=True).start()

        return jsonify(
            {
                "success": True,
                "clone_id": clone_id,
                "status": "running",
                "resolved_url": resolved_url,
                "target": target,
                "log_file": log_file,
            }
        )

    @app.route("/clone_openneuro_status", methods=["GET"])
    def clone_openneuro_status():
        """Poll the status of a DataLad clone job.

        Query params:
          clone_id  (required)
        """
        clone_id = (request.args.get("clone_id") or "").strip()
        if not clone_id:
            return jsonify({"error": "clone_id is required"}), 400

        with _clone_jobs_lock:
            job = _clone_jobs.get(clone_id)
            if job is None:
                return jsonify({"error": "Clone job not found"}), 404
            snapshot = dict(job)

        log_tail = read_log_tail(snapshot.get("log_file", ""))
        snapshot["log_tail"] = log_tail
        snapshot["success"] = snapshot.get("status") == "completed"

        # Expose whether the cloned directory is recognised as a DataLad dataset
        if snapshot["success"]:
            try:
                import prism_datalad

                snapshot["is_datalad"] = prism_datalad.is_datalad_dataset(
                    snapshot["target"]
                )
            except ImportError:
                snapshot["is_datalad"] = None

        return jsonify(snapshot)
