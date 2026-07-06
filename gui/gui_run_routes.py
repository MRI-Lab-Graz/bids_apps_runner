import json
import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from flask import jsonify, request


def _get_docker_macos_shared_dirs():
    """Return Docker Desktop's file-sharing directory list on macOS, or None if unknown.

    Docker Desktop stores its settings in one of several locations depending on
    the version.  Returns a list of absolute path strings, or None when the
    settings file cannot be read (caller should treat that as "can't determine").
    """
    if platform.system() != "Darwin":
        return None

    candidates = [
        Path.home() / "Library/Group Containers/group.com.docker/settings-store.json",
        Path.home() / "Library/Group Containers/group.com.docker/settings.json",
        Path.home() / ".docker/desktop/settings.json",
    ]
    for p in candidates:
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
            dirs = (
                data.get("filesharingDirectories")
                or data.get("filesharingdirectories")
                or []
            )
            if dirs:
                return [os.path.abspath(os.path.expanduser(str(d))) for d in dirs]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            continue
    return None


def _docker_inaccessible_paths(mount_paths, shared_dirs):
    """Return the subset of *mount_paths* that Docker cannot access on macOS.

    Args:
        mount_paths: dict mapping label → absolute path string
        shared_dirs: list returned by _get_docker_macos_shared_dirs(), or None

    Returns:
        dict of label → path for paths that are NOT under any shared dir.
        Empty dict means all paths are accessible (or we couldn't determine).
    """
    if shared_dirs is None:
        return {}  # Can't determine — let Docker itself report the problem.

    # Paths Docker always allows on macOS regardless of settings
    always_ok = ("/private", "/tmp", "/var/folders", "/Users", "/Volumes")

    bad = {}
    for label, path in mount_paths.items():
        if not path:
            continue
        abs_path = os.path.abspath(os.path.expanduser(str(path)))
        if any(abs_path.startswith(ok) for ok in always_ok):
            continue
        if any(abs_path.startswith(d) for d in shared_dirs):
            continue
        bad[label] = abs_path
    return bad


def register_run_routes(
    app,
    *,
    resolve_config_path: Callable[[str], Path],
    resolve_project_dir: Callable[[str], Path],
    extract_runtime_config: Callable[[dict[str, Any], str | None], dict[str, Any]],
    normalize_runner_args: Callable[[Any], list[str]],
    apply_max_usage_cap: Callable[
        [dict[str, Any], int], tuple[dict[str, Any], int, int]
    ],
    extract_fs_license_path: Callable[[list[str] | None], str | None],
    map_container_path_to_host: Callable[[str, list[dict[str, Any]]], str | None],
    compute_auto_nprocs_values: Callable[..., list[int]],
    is_process_alive: Callable[[Any], bool],
    is_pilot_process_running: Callable[[Path], bool],
    pilot_progress_from_output_dir: Callable[
        [Path, int | None], tuple[int, int | None, int | None]
    ],
    read_log_tail: Callable[[str | Path], str],
    get_active_tracked_run_jobs: Callable[[], list[dict[str, Any]]],
    terminate_tracked_run: Callable[[dict[str, Any]], bool],
    terminate_tracked_build: Callable[[dict[str, Any]], bool],
    terminate_pid_group: Callable[[int], bool],
    terminate_pid_groups: Callable[[list[int] | set[int]], int],
    find_app_related_pids: Callable[[bool], list[int] | set[int]],
    monitor_run_job: Callable[[str], None],
    project_manager_getter: Callable[[], Any],
    data_dir: Path,
    base_dir: Path,
    python_exe: str,
    app_launch_env_key: str,
    app_launch_env_value: str,
    hpc_datalad_available: bool,
    run_jobs: dict[str, dict[str, Any]],
    run_jobs_lock,
    pilot_jobs: dict[str, dict[str, Any]],
    pilot_jobs_lock,
    apptainer_builds: dict[str, dict[str, Any]],
    apptainer_builds_lock,
    mark_gui_session_started: Callable[[], None],
):
    @app.route("/run_app", methods=["POST"])
    def run_app():
        data = request.get_json(silent=True) or {}
        config_path_raw = data.get("config_path")
        project_id = data.get("project_id")
        pipeline_id = (data.get("pipeline_id") or "").strip()
        runner_args = normalize_runner_args(data.get("runner_args", []))
        max_usage_enabled = bool(data.get("max_usage_enabled", False))
        max_usage_percent = data.get("max_usage_percent", 100)
        notify_email = (data.get("notify_email") or "").strip()
        if not config_path_raw:
            return jsonify({"error": "No config path provided"}), 400

        try:
            config_path = resolve_config_path(config_path_raw)

            with open(config_path, "r", encoding="utf-8") as handle:
                cfg_raw = json.load(handle)

            runtime_cfg = extract_runtime_config(
                cfg_raw, selected_pipeline_id=pipeline_id or None
            )

            common = runtime_cfg.get("common", {})
            if not notify_email:
                notify_email = str(common.get("notify_email", "")).strip()

            if notify_email and not re.match(
                r"^[^@\s]+@[^@\s]+\.[^@\s]+$", notify_email
            ):
                return jsonify({"error": "Notification email format is invalid."}), 400

            engine = common.get("container_engine", "apptainer")

            paths_to_check = {
                "BIDS Folder": common.get("bids_folder"),
                "Templateflow Folder": common.get("templateflow_dir"),
            }

            # Docker container values are image references (e.g. "nipreps/fmriprep:24.1.1"),
            # not filesystem paths — only validate the container path for Apptainer.
            if engine != "docker":
                paths_to_check["Container Image"] = common.get("container")

            if common.get("fs_license_file"):
                paths_to_check["FreeSurfer License File"] = common.get(
                    "fs_license_file"
                )

            missing = []
            for name, path in paths_to_check.items():
                if path and not os.path.exists(path):
                    missing.append(f"{name}: {path}")

            if missing:
                return (
                    jsonify(
                        {
                            "error": "Validation Failed",
                            "details": "The following paths do not exist:\n"
                            + "\n".join(missing),
                        }
                    ),
                    400,
                )

            # On macOS with Docker, verify every mount path is inside Docker
            # Desktop's file-sharing list before attempting to run.
            if engine == "docker" and platform.system() == "Darwin":
                app_cfg_pre = runtime_cfg.get("app", {})
                mounts_pre = app_cfg_pre.get("mounts", [])

                docker_mount_candidates = {
                    "BIDS Folder": common.get("bids_folder"),
                    "Output Folder": common.get("output_folder"),
                    "Work / Tmp Folder": common.get("tmp_folder"),
                    "FreeSurfer License": common.get("fs_license_file"),
                    "TemplateFlow Folder": common.get("templateflow_dir"),
                    "Optional Folder": common.get("optional_folder"),
                }
                for i, m in enumerate(mounts_pre):
                    src = m.get("source") if isinstance(m, dict) else None
                    if src:
                        docker_mount_candidates[f"Custom Mount {i + 1} ({src})"] = src

                shared_dirs = _get_docker_macos_shared_dirs()
                inaccessible = _docker_inaccessible_paths(
                    docker_mount_candidates, shared_dirs
                )

                if inaccessible:
                    lines = [
                        f"  • {label}: {path}" for label, path in inaccessible.items()
                    ]
                    return (
                        jsonify(
                            {
                                "error": "Docker File Sharing Not Configured",
                                "details": (
                                    "The following path(s) are not accessible to Docker Desktop on macOS.\n\n"
                                    + "\n".join(lines)
                                    + "\n\n"
                                    "Fix options:\n"
                                    "  1. Move the file/folder to your home directory (~/) or an external volume (/Volumes/…) — Docker can always access these.\n"
                                    "  2. Add the path in Docker Desktop → Settings → Resources → File Sharing, then Apply & Restart."
                                ),
                            }
                        ),
                        400,
                    )

            app_cfg = runtime_cfg.get("app", {})
            options = app_cfg.get("options", [])
            mounts = app_cfg.get("mounts", [])
            container_name = str(common.get("container", "")).lower()

            if (
                "fmriprep" in container_name
                or "qsiprep" in container_name
                or "qsirecon" in container_name
            ):
                fs_license_path = common.get("fs_license_file")
                fs_license_arg = extract_fs_license_path(options)

                if not fs_license_path and not fs_license_arg:
                    return (
                        jsonify(
                            {
                                "error": "FreeSurfer license required",
                                "details": "fMRIPrep/QSIPrep/QSIRecon requires a FreeSurfer license. Provide it in the FreeSurfer License File field or add custom args: --fs-license-file /fs/license.txt with an appropriate bind mount.",
                            }
                        ),
                        400,
                    )

                if fs_license_path and not os.path.exists(fs_license_path):
                    return (
                        jsonify(
                            {
                                "error": "FreeSurfer license file not found",
                                "details": f"FreeSurfer license file not found at {fs_license_path}.",
                            }
                        ),
                        400,
                    )

                if fs_license_arg and fs_license_arg.startswith("/"):
                    host_license = map_container_path_to_host(fs_license_arg, mounts)
                    if host_license is None:
                        return (
                            jsonify(
                                {
                                    "error": "FreeSurfer license path not mounted",
                                    "details": f"--fs-license-file {fs_license_arg} is not under any custom mount target. Add a mount that makes the license file available in the container.",
                                }
                            ),
                            400,
                        )
                    if not os.path.exists(host_license):
                        return (
                            jsonify(
                                {
                                    "error": "FreeSurfer license file not found",
                                    "details": f"--fs-license-file points to {fs_license_arg} but host file not found at {host_license}. Check custom mounts/args.",
                                }
                            ),
                            400,
                        )

            if engine == "docker":
                if not shutil.which("docker"):
                    return (
                        jsonify({"error": "Docker requested but not found on system."}),
                        400,
                    )
                try:
                    subprocess.run(
                        ["docker", "info"], capture_output=True, timeout=2, check=True
                    )
                except (subprocess.SubprocessError, FileNotFoundError):
                    return (
                        jsonify(
                            {
                                "error": "Docker is installed but the DAEMON IS NOT RUNNING. Please start Docker Desktop."
                            }
                        ),
                        400,
                    )
            elif engine == "apptainer" and not (
                shutil.which("apptainer") or shutil.which("singularity")
            ):
                return (
                    jsonify(
                        {
                            "error": "Apptainer/Singularity requested but not found on system."
                        }
                    ),
                    400,
                )

            if project_id:
                work_dir = resolve_project_dir(str(project_id)) / "logs"
                work_dir.mkdir(parents=True, exist_ok=True)
            else:
                work_dir = data_dir
                work_dir.mkdir(parents=True, exist_ok=True)

            runtime_config_path = config_path
            runtime_note = ""

            if max_usage_enabled:
                try:
                    max_usage_percent = int(max_usage_percent)
                except (TypeError, ValueError):
                    return (
                        jsonify({"error": "max_usage_percent must be an integer"}),
                        400,
                    )

                if max_usage_percent < 10 or max_usage_percent > 100:
                    return (
                        jsonify(
                            {"error": "max_usage_percent must be between 10 and 100"}
                        ),
                        400,
                    )

                capped_cfg, allowed_cores, host_cores = apply_max_usage_cap(
                    runtime_cfg, max_usage_percent
                )
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                runtime_config_path = (
                    work_dir / f"runtime_config_capped_{timestamp}.json"
                )
                with open(runtime_config_path, "w", encoding="utf-8") as handle:
                    json.dump(capped_cfg, handle, indent=2)

                runtime_note = (
                    f" Resource cap enabled: {max_usage_percent}% of {host_cores} cores "
                    f"-> using up to {allowed_cores} core(s)."
                )
            elif runtime_cfg is not cfg_raw:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                runtime_config_path = work_dir / f"runtime_config_{timestamp}.json"
                with open(runtime_config_path, "w", encoding="utf-8") as handle:
                    json.dump(runtime_cfg, handle, indent=2)

            script_path = base_dir / "scripts" / "run_bids_apps.py"
            abs_config_path = os.path.abspath(str(runtime_config_path))

            # --local forces prism_runner's local execution path even if this
            # project also carries a saved "hpc" section (e.g. SLURM settings
            # used by the separate single-job/array submission routes) --
            # without it, prism_runner would auto-detect "HPC mode" from that
            # section's mere presence and route into the unrelated legacy
            # per-subject prism_hpc.py path instead of running locally here.
            cmd = [python_exe, str(script_path), "-c", abs_config_path, "--local"]
            if runner_args:
                cmd.extend(runner_args)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_filename = f"run_{timestamp}.log"
            log_file_path = work_dir / log_filename

            print(f"[GUI] Executing: {' '.join(cmd)} in {work_dir}")
            print(f"[GUI] Logging output to: {log_file_path}")

            with open(log_file_path, "w", encoding="utf-8") as log_handle:
                log_handle.write(
                    f"[{datetime.now().isoformat()}] Executing: {' '.join(cmd)}\n"
                )
                log_handle.write(
                    f"[{datetime.now().isoformat()}] Working directory: {work_dir}\n"
                )
                log_handle.write("=" * 80 + "\n\n")
                log_handle.flush()

                launch_env = os.environ.copy()
                launch_env[app_launch_env_key] = app_launch_env_value

                process = subprocess.Popen(
                    cmd,
                    cwd=str(work_dir),
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    preexec_fn=os.setpgrp,
                    env=launch_env,
                )

            run_id = (
                f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
            )
            with run_jobs_lock:
                run_jobs[run_id] = {
                    "id": run_id,
                    "process": process,
                    "pid": process.pid,
                    "project_id": project_id,
                    "log_file": str(log_file_path),
                    "started_at": time.time(),
                    "cmd": cmd,
                    "notify_email": notify_email,
                    "stop_requested": False,
                    "returncode": None,
                    "finished_at": None,
                }

            monitor_thread = threading.Thread(
                target=monitor_run_job,
                args=(run_id,),
                daemon=True,
            )
            monitor_thread.start()

            if project_id:
                project_manager_getter().update_project_log(project_id, log_filename)

            mark_gui_session_started()

            return jsonify(
                {
                    "message": (
                        f"BIDS App Runner started in background. Command: {' '.join(cmd)}"
                        + runtime_note
                        + (
                            f". Completion email will be sent to {notify_email}."
                            if notify_email
                            else ""
                        )
                    ),
                    "run_id": run_id,
                }
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/run_pilot_estimator", methods=["POST"])
    def run_pilot_estimator():
        req = request.get_json(silent=True) or {}
        config_path_raw = req.get("config_path")
        project_id = req.get("project_id")
        pipeline_id = (req.get("pipeline_id") or "").strip()
        subject = (req.get("subject") or "").strip()
        nprocs_mode = (req.get("nprocs_mode") or "auto").strip().lower()
        nprocs = str(req.get("nprocs") or "").strip()
        force = bool(req.get("force", False))

        if not config_path_raw:
            return jsonify({"error": "config_path is required"}), 400

        try:
            config_path = resolve_config_path(config_path_raw)

            if project_id:
                work_dir = resolve_project_dir(str(project_id)) / "logs"
                work_dir.mkdir(parents=True, exist_ok=True)
            else:
                work_dir = data_dir / "logs"
                work_dir.mkdir(parents=True, exist_ok=True)

            script_path = base_dir / "scripts" / "pilot_resource_estimator.py"
            if not script_path.exists():
                return (
                    jsonify({"error": f"Pilot estimator not found: {script_path}"}),
                    500,
                )

            runtime_config_path = config_path
            with open(config_path, "r", encoding="utf-8") as handle:
                cfg_raw = json.load(handle)

            runtime_cfg = extract_runtime_config(
                cfg_raw, selected_pipeline_id=pipeline_id or None
            )
            if runtime_cfg is not cfg_raw:
                cfg_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                runtime_config_path = (
                    work_dir / f"pilot_runtime_config_{cfg_stamp}.json"
                )
                with open(runtime_config_path, "w", encoding="utf-8") as handle:
                    json.dump(runtime_cfg, handle, indent=2)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            job_id = f"pilot_{timestamp}_{uuid.uuid4().hex[:8]}"
            output_dir = work_dir / f"pilot_resource_estimator_{timestamp}"
            log_file = work_dir / f"pilot_resource_estimator_{timestamp}.log"

            expected_nprocs = []
            cmd = [
                python_exe,
                "-u",
                str(script_path),
                "--config",
                str(runtime_config_path),
                "--output-dir",
                str(output_dir),
            ]

            if subject:
                cmd.extend(["--subject", subject])

            if nprocs_mode == "manual" and nprocs:
                cmd.extend(["--nprocs", nprocs])
                try:
                    expected_nprocs = sorted(
                        set(
                            int(value.strip())
                            for value in nprocs.split(",")
                            if str(value).strip()
                        )
                    )
                except ValueError:
                    expected_nprocs = []
            else:
                nprocs_min = req.get("nprocs_min", 2)
                nprocs_max = req.get("nprocs_max")
                nprocs_step = req.get("nprocs_step", 1)

                try:
                    nprocs_min = int(nprocs_min)
                    nprocs_step = int(nprocs_step)
                    cmd.extend(["--nprocs-min", str(nprocs_min)])
                    cmd.extend(["--nprocs-step", str(nprocs_step)])
                    if nprocs_max not in (None, "", "null"):
                        cmd.extend(["--nprocs-max", str(int(nprocs_max))])

                    expected_nprocs = compute_auto_nprocs_values(
                        nprocs_min=nprocs_min,
                        nprocs_max=(
                            None
                            if nprocs_max in (None, "", "null")
                            else int(nprocs_max)
                        ),
                        nprocs_step=nprocs_step,
                    )
                except (TypeError, ValueError):
                    return (
                        jsonify(
                            {
                                "error": "Invalid nprocs_min/nprocs_max/nprocs_step values"
                            }
                        ),
                        400,
                    )

            expected_runs = len(expected_nprocs)
            if force:
                cmd.append("--force")

            launch_env = os.environ.copy()
            launch_env[app_launch_env_key] = app_launch_env_value

            with open(log_file, "w", encoding="utf-8") as log_handle:
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(work_dir),
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=launch_env,
                )

            with pilot_jobs_lock:
                pilot_jobs[job_id] = {
                    "job_id": job_id,
                    "pid": proc.pid,
                    "process": proc,
                    "started_at": datetime.now().isoformat(),
                    "command": [str(item) for item in cmd],
                    "log_file": str(log_file),
                    "output_dir": str(output_dir),
                    "report_file": str(output_dir / "pilot_resource_report.md"),
                    "results_file": str(output_dir / "pilot_results.json"),
                    "expected_runs": expected_runs,
                    "expected_nprocs": expected_nprocs,
                }

            return jsonify(
                {
                    "message": "Pilot estimator started in background.",
                    "job_id": job_id,
                    "command": " ".join(cmd),
                    "log_file": str(log_file),
                    "output_dir": str(output_dir),
                    "report_file": str(output_dir / "pilot_resource_report.md"),
                    "results_file": str(output_dir / "pilot_results.json"),
                    "expected_runs": expected_runs,
                }
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/pilot_estimator_status", methods=["POST"])
    def pilot_estimator_status():
        req = request.get_json(silent=True) or {}
        job_id = (req.get("job_id") or "").strip()

        entry = None
        if job_id:
            with pilot_jobs_lock:
                entry = pilot_jobs.get(job_id)

        log_file = req.get("log_file")
        output_dir = req.get("output_dir")
        report_file = req.get("report_file")
        results_file = req.get("results_file")
        expected_runs = req.get("expected_runs")

        if entry:
            log_file = entry.get("log_file")
            output_dir = entry.get("output_dir")
            report_file = entry.get("report_file")
            results_file = entry.get("results_file")
            expected_runs = entry.get("expected_runs") or expected_runs

        if not log_file and not output_dir:
            return jsonify({"error": "job_id or log_file/output_dir required"}), 400

        log_path = Path(str(log_file)).expanduser() if log_file else None
        output_dir_path = Path(str(output_dir)).expanduser() if output_dir else None
        report_path = Path(str(report_file)).expanduser() if report_file else None
        results_path = Path(str(results_file)).expanduser() if results_file else None

        running = False
        exit_code = None
        if entry:
            proc = entry.get("process")
            running = is_process_alive(proc)
            if not running and proc is not None:
                try:
                    exit_code = proc.poll()
                except Exception:
                    exit_code = None

        if not running and output_dir_path is not None:
            running = is_pilot_process_running(output_dir_path)

        report_exists = report_path.exists() if report_path else False
        results_exists = results_path.exists() if results_path else False

        completed, total, percent = (0, None, None)
        if output_dir_path is not None:
            completed, total, percent = pilot_progress_from_output_dir(
                output_dir_path, expected_total=expected_runs
            )

        status = "running"
        if report_exists and results_exists:
            status = "completed"
            running = False
        elif not running:
            status = "failed" if exit_code not in (None, 0) else "idle"

        tail = read_log_tail(log_path) if log_path else ""

        return jsonify(
            {
                "job_id": job_id or None,
                "status": status,
                "running": running,
                "exit_code": exit_code,
                "log_file": str(log_path) if log_path else None,
                "output_dir": str(output_dir_path) if output_dir_path else None,
                "report_file": str(report_path) if report_path else None,
                "results_file": str(results_path) if results_path else None,
                "report_exists": report_exists,
                "results_exists": results_exists,
                "progress": {
                    "completed": completed,
                    "total": total,
                    "percent": percent,
                },
                "log_tail": tail,
            }
        )

    @app.route("/kill_job", methods=["POST"])
    def kill_job():
        try:
            data = request.get_json(silent=True) or {}
            scope = str(data.get("scope", "current")).strip().lower()
            project_id = str(data.get("project_id") or "").strip()
            if scope not in {"current", "all"}:
                return jsonify({"error": "Invalid scope. Use 'current' or 'all'."}), 400

            tracked_jobs = get_active_tracked_run_jobs()
            killed = 0

            if scope == "current":
                target = None
                candidate_jobs = tracked_jobs
                if project_id:
                    candidate_jobs = [
                        state
                        for state in tracked_jobs
                        if str(state.get("project_id") or "") == project_id
                    ]

                if candidate_jobs:
                    target = max(
                        candidate_jobs, key=lambda state: state.get("started_at", 0)
                    )

                if target is not None:
                    if terminate_tracked_run(target):
                        killed += 1
                elif not project_id:
                    candidate_pids = sorted(find_app_related_pids(include_marked=True))
                    if candidate_pids:
                        newest_pid = max(candidate_pids)
                        if terminate_pid_group(newest_pid):
                            killed += 1

                if killed == 0:
                    if project_id:
                        return (
                            jsonify(
                                {
                                    "message": f"No active tracked run found for project {project_id}."
                                }
                            ),
                            200,
                        )
                    return (
                        jsonify(
                            {
                                "message": "No active BIDS App Runner process found to stop."
                            }
                        ),
                        200,
                    )

                if project_id:
                    return (
                        jsonify(
                            {
                                "message": f"Stop signal sent to current run for project {project_id}."
                            }
                        ),
                        200,
                    )
                return jsonify({"message": "Stop signal sent to current run."}), 200

            for state in tracked_jobs:
                if terminate_tracked_run(state):
                    killed += 1

            build_pids = []
            with apptainer_builds_lock:
                for state in apptainer_builds.values():
                    process = state.get("process")
                    if process is not None and process.poll() is None:
                        build_pids.append(process.pid)
                        terminate_tracked_build(state)

            killed += terminate_pid_groups(build_pids)
            killed += terminate_pid_groups(find_app_related_pids(include_marked=True))

            if killed == 0:
                return (
                    jsonify({"message": "No active BIDS App Runner processes found."}),
                    200,
                )

            return (
                jsonify(
                    {
                        "message": f"Stop signal sent to all runs ({killed} process target(s))."
                    }
                ),
                200,
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/shutdown", methods=["POST"])
    def shutdown():
        print("[GUI] Shutdown requested via web interface", flush=True)

        def kill_server():
            time.sleep(1)
            os._exit(0)

        threading.Thread(target=kill_server).start()
        return jsonify(success=True)

    @app.route("/check_hpc_environment", methods=["GET"])
    def check_hpc_environment():
        return jsonify(
            {
                "slurm": shutil.which("sbatch") is not None,
                "datalad": shutil.which("datalad") is not None,
                "git": shutil.which("git") is not None,
                "git_annex": shutil.which("git-annex") is not None,
                "apptainer": shutil.which("apptainer") is not None,
                "singularity": shutil.which("singularity") is not None,
                "hpc_datalad_available": hpc_datalad_available,
            }
        )

    @app.route("/generate_hpc_script", methods=["POST"])
    def generate_hpc_script():
        """Generate (and save) a SLURM batch script for this project's BIDS app run.

        Reuses the exact same entry point as a local run
        (``scripts/run_bids_apps.py -c <config>``) so a SLURM job behaves
        identically to "Save & Start Runner" — just submitted through sbatch
        on a compute node instead of executed directly here.

        Request body (JSON):
          config_path (required) – path to project.json / config.json
          project_id  (optional) – used to resolve the project's logs dir
          pipeline_id (optional) – pipeline preset to select from a multi-pipeline project
          hpc (required) – { partition, time, mem, cpus, job_name, output_pattern,
                              error_pattern, modules, environment }

        Response:
          script               – generated script content
          script_path          – saved location (pass to /submit_hpc_job)
          runtime_config_path  – resolved runtime config snapshot used by the job
        """
        data = request.get_json(silent=True) or {}
        config_path_raw = data.get("config_path")
        project_id = data.get("project_id")
        pipeline_id = (data.get("pipeline_id") or "").strip()
        hpc = data.get("hpc") or {}

        if not config_path_raw:
            return jsonify({"error": "config_path is required"}), 400

        partition = str(hpc.get("partition") or "").strip()
        time_limit = str(hpc.get("time") or "").strip()
        mem = str(hpc.get("mem") or "").strip()
        try:
            cpus = int(hpc.get("cpus") or 0)
        except (TypeError, ValueError):
            cpus = 0

        if not partition or not time_limit or not mem or cpus < 1:
            return (
                jsonify(
                    {
                        "error": "hpc.partition, hpc.time, hpc.mem and hpc.cpus (>=1) are required"
                    }
                ),
                400,
            )

        import app_profiles  # lazy -- scripts/ is on sys.path at runtime

        gpu_error = app_profiles.check_gpu_request_feasible(hpc)
        if gpu_error:
            return jsonify({"error": gpu_error}), 400

        def _sanitize_token(value, fallback):
            cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
            cleaned = cleaned.strip("_")
            return cleaned or fallback

        try:
            config_path = resolve_config_path(config_path_raw)

            with open(config_path, "r", encoding="utf-8") as handle:
                cfg_raw = json.load(handle)

            runtime_cfg = extract_runtime_config(
                cfg_raw, selected_pipeline_id=pipeline_id or None
            )
            common = runtime_cfg.get("common", {})

            engine = common.get("container_engine", "apptainer")
            missing = []
            bids_folder = common.get("bids_folder")
            if not bids_folder or not os.path.exists(bids_folder):
                missing.append(f"BIDS Folder: {bids_folder}")
            if engine != "docker":
                container = common.get("container")
                if not container or not os.path.exists(container):
                    missing.append(f"Container Image: {container}")
            if missing:
                return (
                    jsonify(
                        {
                            "error": "Validation Failed",
                            "details": "The following paths do not exist:\n"
                            + "\n".join(missing),
                        }
                    ),
                    400,
                )

            if project_id:
                work_dir = resolve_project_dir(str(project_id)) / "logs"
            else:
                work_dir = data_dir / "logs"
            work_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            runtime_config_path = work_dir / f"runtime_config_hpc_{timestamp}.json"
            with open(runtime_config_path, "w", encoding="utf-8") as handle:
                json.dump(runtime_cfg, handle, indent=2)

            job_name = _sanitize_token(hpc.get("job_name"), "bids-app")
            output_pattern = (
                str(hpc.get("output_pattern") or "").strip().replace("\n", " ")
                or f"{work_dir}/slurm_{job_name}_%j.out"
            )
            error_pattern = (
                str(hpc.get("error_pattern") or "").strip().replace("\n", " ")
                or f"{work_dir}/slurm_{job_name}_%j.err"
            )
            modules = [str(m).strip() for m in (hpc.get("modules") or []) if str(m).strip()]
            environment = hpc.get("environment") or {}

            lines = [
                "#!/bin/bash",
                f"#SBATCH --partition={partition}",
                f"#SBATCH --time={time_limit}",
                f"#SBATCH --mem={mem}",
                f"#SBATCH --cpus-per-task={cpus}",
                f"#SBATCH --job-name={job_name}",
                f"#SBATCH --output={output_pattern}",
                f"#SBATCH --error={error_pattern}",
            ]
            # Extra directives, e.g. "sbatch_gres": "gpu:1" -> #SBATCH --gres=gpu:1
            # (same convention as prism_hpc.py/hpc_datalad_runner.py) -- this is
            # how a GPU actually gets requested/reserved for this job; without
            # it, SLURM won't expose a GPU to the container even on a node
            # that has one.
            for key, value in hpc.items():
                if not key.startswith("sbatch_") or value in (None, "", False):
                    continue
                directive = re.sub(r"[^A-Za-z0-9-]", "", key.replace("sbatch_", "").replace("_", "-"))
                if not directive:
                    continue
                if value is True:
                    lines.append(f"#SBATCH --{directive}")
                else:
                    safe_value = re.sub(r"[^A-Za-z0-9._%/@:+,=~-]", "", str(value))
                    lines.append(f"#SBATCH --{directive}={safe_value}")
            lines += [
                "",
                "set -euo pipefail",
                "",
            ]
            if modules:
                lines.append("# Load required modules")
                lines.extend(f"module load {m}" for m in modules)
                lines.append("")
            if environment:
                lines.append("# Environment variables")
                lines.extend(
                    f'export {key}="{val}"' for key, val in environment.items()
                )
                lines.append("")

            # When the BIDS dataset is a DataLad remote clone, verify that the
            # compute node can reach the SSH server before starting.  DataLad
            # fetches subject data on demand via SSH; a silent connectivity
            # failure would only surface as a cryptic mid-run error.
            from gui.gui_utility_routes import (
                LOCAL_DATASET_BASE_DIR,
                REMOTE_DATASET_SSH_HOST,
            )
            if bids_folder and str(bids_folder).startswith(LOCAL_DATASET_BASE_DIR):
                lines += [
                    "# Verify DataLad SSH connectivity before starting",
                    f'ssh -o BatchMode=yes -o ConnectTimeout=10 {REMOTE_DATASET_SSH_HOST} exit || {{',
                    f'    echo "ERROR: Cannot reach DataLad server ({REMOTE_DATASET_SSH_HOST}) via SSH from this compute node." >&2',
                    '    exit 1',
                    '}',
                    "",
                ]

            lines.append(f'cd "{work_dir}"')
            run_script = base_dir / "scripts" / "run_bids_apps.py"
            # --local forces prism_runner's local execution path even though this
            # project's config carries an "hpc" section (SLURM resource settings)
            # for *this* script's own #SBATCH headers — without it, prism_runner
            # would auto-detect "HPC mode" from that section and route into the
            # unrelated cohort/datalad-slurm execution path, which expects a very
            # different config shape and would fail validation.
            lines.append(
                f'"{python_exe}" "{run_script}" -c "{runtime_config_path}" --local'
            )
            script_content = "\n".join(lines) + "\n"

            script_path = work_dir / f"hpc_job_{job_name}_{timestamp}.sh"
            with open(script_path, "w", encoding="utf-8") as handle:
                handle.write(script_content)
            os.chmod(script_path, 0o755)

            return jsonify(
                {
                    "success": True,
                    "script": script_content,
                    "script_path": str(script_path),
                    "runtime_config_path": str(runtime_config_path),
                }
            )
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/save_hpc_script", methods=["POST"])
    def save_hpc_script():
        data = request.get_json(silent=True) or {}
        script_content = data.get("script")
        subject = data.get("subject")
        output_dir = data.get("output_dir", os.path.join(tempfile.gettempdir(), "hpc_scripts"))

        if not script_content or not subject:
            return jsonify({"error": "script and subject are required"}), 400

        try:
            output_path = Path(output_dir) / f"job_{subject}.sh"
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as handle:
                handle.write(script_content)

            os.chmod(output_path, 0o755)
            return jsonify(
                {"message": f"Script saved to {output_path}", "path": str(output_path)}
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/submit_hpc_job", methods=["POST"])
    def submit_hpc_job():
        data = request.get_json(silent=True) or {}
        script_path = data.get("script_path")
        dry_run = data.get("dry_run", False)

        if not script_path:
            return jsonify({"error": "script_path is required"}), 400

        if not os.path.exists(script_path):
            return jsonify({"error": f"Script not found: {script_path}"}), 400

        try:
            if dry_run:
                return jsonify(
                    {
                        "message": "DRY RUN - Would submit job",
                        "command": f"sbatch {script_path}",
                        "job_id": "DRY_RUN_JOB_ID",
                    }
                )

            cmd = ["sbatch", script_path]
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            output = result.stdout.strip()
            if "Submitted batch job" in output:
                job_id = output.split()[-1]
                logging.info(f"[GUI] Submitted HPC job {job_id}: {script_path}")
                return jsonify(
                    {
                        "message": "Job submitted successfully",
                        "job_id": job_id,
                        "command": " ".join(cmd),
                    }
                )
            return jsonify({"error": f"Failed to parse job ID: {output}"}), 500
        except subprocess.CalledProcessError as exc:
            return jsonify({"error": f"Failed to submit job: {exc.stderr}"}), 500
        except FileNotFoundError:
            return (
                jsonify(
                    {"error": "sbatch not found - SLURM not available on this system"}
                ),
                400,
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/get_hpc_job_status", methods=["POST"])
    def get_hpc_job_status():
        data = request.get_json(silent=True) or {}
        job_ids = data.get("job_ids", [])

        if not job_ids:
            return jsonify({"error": "job_ids required"}), 400

        def _sacct_final(job_id):
            """Query sacct for final state of a completed/failed job."""
            try:
                r = subprocess.run(
                    ["sacct", "-j", job_id, "--noheader", "--parsable2",
                     "--format=JobID,State,ExitCode,Elapsed,Start,End"],
                    capture_output=True, text=True, check=False, timeout=10,
                )
                for line in r.stdout.strip().splitlines():
                    parts = line.split("|")
                    # Skip sub-steps (jobid.batch, jobid.extern)
                    if len(parts) >= 6 and "." not in parts[0]:
                        return {
                            "job_id": parts[0],
                            "status": parts[1],
                            "exit_code": parts[2],
                            "elapsed": parts[3],
                            "start": parts[4],
                            "end": parts[5],
                            "final": True,
                        }
            except Exception:
                pass
            return None

        def _log_tail(job_id, log_pattern, n=40):
            """Try to read the last n lines of the SLURM output log."""
            import glob as _glob
            pattern = log_pattern.replace("%j", job_id).replace("%J", job_id)
            matches = _glob.glob(pattern)
            if not matches:
                return None, None
            log_path = sorted(matches)[-1]
            try:
                with open(log_path, "r", errors="replace") as fh:
                    lines = fh.readlines()
                return log_path, "".join(lines[-n:])
            except Exception:
                return log_path, None

        try:
            cmd = ["squeue", "-j", ",".join(job_ids), "--format=%i,%T,%M,%e"]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)

            active = {}
            for line in result.stdout.strip().split("\n")[1:]:
                if line:
                    parts = line.split(",")
                    if len(parts) >= 4:
                        active[parts[0]] = {
                            "job_id": parts[0],
                            "status": parts[1],
                            "time": parts[2],
                            "end_time": parts[3] if len(parts) > 3 else "",
                            "final": False,
                        }

            jobs = []
            for job_id in job_ids:
                if job_id in active:
                    jobs.append(active[job_id])
                else:
                    # Job left squeue — fetch final state from sacct
                    final = _sacct_final(job_id)
                    if final:
                        jobs.append(final)

            return jsonify({"jobs": jobs})
        except FileNotFoundError:
            return jsonify({"error": "squeue not found - SLURM not available"}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/get_hpc_job_log", methods=["POST"])
    def get_hpc_job_log():
        """Return the tail of a SLURM job's output log."""
        data = request.get_json(silent=True) or {}
        job_id = str(data.get("job_id") or "").strip()
        log_pattern = str(data.get("log_pattern") or "").strip()
        n = min(int(data.get("lines") or 60), 200)

        if not job_id or not log_pattern:
            return jsonify({"error": "job_id and log_pattern required"}), 400

        import glob as _glob
        pattern = log_pattern.replace("%j", job_id).replace("%J", job_id)
        matches = _glob.glob(pattern)
        if not matches:
            return jsonify({"error": f"No log file found matching {pattern}"}), 404
        log_path = sorted(matches)[-1]
        try:
            with open(log_path, "r", errors="replace") as fh:
                lines = fh.readlines()
            return jsonify({"log_path": log_path, "tail": "".join(lines[-n:]), "total_lines": len(lines)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/cancel_hpc_job", methods=["POST"])
    def cancel_hpc_job():
        data = request.get_json(silent=True) or {}
        job_id = data.get("job_id")

        if not job_id:
            return jsonify({"error": "job_id is required"}), 400

        try:
            cmd = ["scancel", job_id]
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            return jsonify({"message": f"Job {job_id} cancelled", "job_id": job_id})
        except subprocess.CalledProcessError as exc:
            return jsonify({"error": f"Failed to cancel job: {exc.stderr}"}), 500
        except FileNotFoundError:
            return jsonify({"error": "scancel not found - SLURM not available"}), 400
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
