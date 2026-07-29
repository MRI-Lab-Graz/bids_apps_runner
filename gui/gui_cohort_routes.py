import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from flask import jsonify, request

_cohort_jobs: dict[str, Any] = {}
_cohort_jobs_lock = threading.Lock()


def _datalad_subprocess_env() -> dict:
    env = os.environ.copy()
    # Ensure ~/.local/bin is on PATH so datalad-slurm extension is visible
    local_bin = str(Path.home() / ".local" / "bin")
    if local_bin not in env.get("PATH", ""):
        env["PATH"] = local_bin + ":" + env.get("PATH", "")
    return env


def _git_sync_status(path: str) -> dict[str, Any]:
    """Whether a git-annex/datalad clone at `path` is fully synced to its
    origin remote: clean working tree, nothing committed locally that
    hasn't made it to `refs/remotes/origin/<branch>`. Deliberately does no
    network I/O (no `git fetch`) -- it only compares against the
    remote-tracking ref left behind by the last successful push, the same
    no-network property scripts/check_output_sync.sh already relies on
    (see that script's own rationale comment) so this check can never hang
    the way a live push/fetch can. Returns
    {ok, branch, uncommitted, unpushed, error}.
    """
    result: dict[str, Any] = {
        "ok": False,
        "branch": None,
        "uncommitted": 0,
        "unpushed": 0,
        "error": None,
    }
    if not (Path(path) / ".git").is_dir():
        result["error"] = "Not a git repository."
        return result

    try:
        status = subprocess.run(
            ["git", "-C", path, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if status.returncode != 0:
            error_text = (status.stderr or status.stdout).strip().splitlines()
            result["error"] = (
                f"broken or unreadable git repository: {error_text[0] if error_text else ''}"
            )
            return result
        result["uncommitted"] = len([line for line in status.stdout.splitlines() if line])

        branch_proc = subprocess.run(
            ["git", "-C", path, "symbolic-ref", "--short", "-q", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        branch = branch_proc.stdout.strip() or None
        result["branch"] = branch

        if branch:
            ref_check = subprocess.run(
                ["git", "-C", path, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if ref_check.returncode == 0:
                count_proc = subprocess.run(
                    ["git", "-C", path, "rev-list", "--count", f"origin/{branch}..HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                result["unpushed"] = int((count_proc.stdout or "0").strip() or 0)
    except subprocess.TimeoutExpired:
        result["error"] = "Timed out checking git status."
        return result

    result["ok"] = (
        result["error"] is None and result["uncommitted"] == 0 and result["unpushed"] == 0
    )
    return result


def _pipeline_completeness_status(cohort_cfg: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    """Whether the BIDS App that actually ran for this cohort produced its
    full expected output set, per scripts/check_app_output.py's per-pipeline
    checkers (the same ones behind the "Verify Output" panel). A clean/
    pushed git clone (_git_sync_status) only proves whatever got committed
    made it to the remote -- it says nothing about whether the run itself
    was complete, and different BIDS Apps expect entirely different file
    sets, so that has to be checked per pipeline, not inferred from git
    state. Returns {ok, supported, pipeline, missing_items, stats, error}.
    `supported=False` means no checker is registered for this project's
    pipeline_app_name at all (e.g. a custom/unlisted BIDS App) -- callers
    must not treat that the same as `ok=True`.
    """
    bids_dir = cohort_cfg["paths"]["input_dir"]
    derivatives_dir = cohort_cfg["paths"]["output_dir"]
    app_name = cohort_cfg["bids_app"]["app_name"]

    script_path = base_dir / "scripts" / "check_app_output.py"
    cmd = [
        sys.executable, str(script_path),
        bids_dir, derivatives_dir,
        "-p", app_name,
        "--json", "--quiet",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600, cwd=str(base_dir)
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "supported": True, "pipeline": app_name,
            "missing_items": [], "stats": None,
            "error": "Timed out running output completeness check.",
        }

    try:
        parsed = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        parsed = None

    if parsed is None:
        # Either the checker crashed, or the pipeline isn't one it knows
        # about -- check_app_output.py's main() prints "Error: Unknown
        # pipeline: ..." to stderr and exits non-zero without emitting JSON
        # in that case (BIDSOutputValidator.PIPELINE_CHECKERS lookup).
        return {
            "ok": False, "supported": False, "pipeline": app_name,
            "missing_items": [], "stats": None,
            "error": (
                (proc.stderr or "").strip()[-500:]
                or f"No automated output-completeness checker is available for pipeline '{app_name}'."
            ),
        }

    pipeline_result = (parsed.get("pipelines") or {}).get(app_name) or {}
    missing_items = pipeline_result.get("missing_items") or []
    return {
        "ok": not missing_items,
        "supported": True,
        "pipeline": app_name,
        "missing_items": missing_items,
        "stats": pipeline_result.get("stats"),
        "error": None,
    }


def _parse_open_slurm_jobs(stdout: str) -> list[dict[str, str]]:
    """Parse the plain-text table `datalad slurm-finish --list-open-jobs`
    prints, e.g.:
        The following jobs are open:

        slurm-job-id   slurm-job-status
        5352559        FAILED
    """
    jobs = []
    for line in stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        job_id, status = parts
        if job_id.lower() == "slurm-job-id":
            continue
        jobs.append({"job_id": job_id, "status": status})
    return jobs


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

    def _cohort_readiness_checks(project_id, pipeline_id, max_concurrent):
        """Load the project fresh and run every cohort-run precondition as a
        full checklist (not stop-at-first-error), mirroring
        gui_run_routes.py::_local_run_checks for the Quick Test path so both
        entry points' step-tracker UI can render the same shape. Returns
        (cohort_cfg_or_None, checks) where each check is
        {id, label, ok, blocking, detail}.
        """
        checks = []

        def add(check_id, label, ok, blocking=True, detail=""):
            checks.append(
                {"id": check_id, "label": label, "ok": ok, "blocking": blocking, "detail": detail}
            )

        if not project_id:
            add("project", "Project selected", False, detail="No project_id provided.")
            return None, checks

        project_json = load_project(project_id)
        if project_json is None:
            add("project", "Project selected", False, detail=f"Project not found: {project_id}")
            return None, checks
        add("project", "Project selected", True)

        runtime_cfg = extract_runtime_config(project_json, pipeline_id or None)
        common = runtime_cfg.get("common", {})
        hpc = runtime_cfg.get("hpc", {})

        bids_folder = common.get("bids_folder")
        add(
            "bids_folder", "BIDS Dataset Folder", bool(bids_folder),
            detail="Set." if bids_folder else "Not set.",
        )

        output_folder = common.get("output_folder")
        add(
            "output_folder", "Output Folder", bool(output_folder),
            detail="Set." if output_folder else "Not set.",
        )

        engine = common.get("container_engine", "apptainer")
        add(
            "container_engine",
            "Container engine is Apptainer/Singularity",
            engine != "docker",
            detail=(
                "OK." if engine != "docker"
                else "SLURM array execution only supports Apptainer/Singularity, not Docker."
            ),
        )

        for field, label in (
            ("partition", "SLURM Partition"),
            ("time", "SLURM Time"),
            ("mem", "SLURM Memory"),
            ("cpus", "SLURM CPUs"),
        ):
            value = hpc.get(field)
            add(
                f"hpc_{field}", label, bool(value),
                detail="Set." if value else "Not set -- fill in Advanced: SLURM Settings.",
            )

        # Only attempt to derive the full cohort config (and the checks that
        # depend on it -- container existence, GPU feasibility) once the
        # basics above are in place; derive_cohort_config would just raise
        # on the same missing fields anyway.
        cohort_cfg = None
        if all(c["ok"] for c in checks if c["blocking"]):
            project_dir = resolve_project_dir(project_id)
            try:
                cohort_cfg = derive_cohort_config(
                    runtime_cfg, project_dir=project_dir, max_concurrent=max_concurrent
                )
            except ValueError as exc:
                add("cohort_config", "Cohort config derivable", False, detail=str(exc))
                return None, checks

            container = cohort_cfg["paths"]["container"]
            add(
                "container",
                "Container Image",
                bool(container) and os.path.exists(container),
                detail=(
                    "Found." if container and os.path.exists(container)
                    else f"Path does not exist: {container}"
                ),
            )

            import app_profiles  # lazy -- scripts/ is on sys.path at runtime

            gpu_warning = app_profiles.check_gpu_request_feasible(cohort_cfg.get("hpc", {}))
            add(
                "gpu_feasibility",
                "GPU partition feasibility",
                not gpu_warning,
                detail=gpu_warning or "OK.",
            )

            resolution = app_profiles.describe_execution_adapter_resolution(
                common, runtime_cfg.get("app", {})
            )
            if resolution:
                add(
                    "execution_adapter",
                    "Execution mode",
                    True,
                    blocking=False,
                    detail=(
                        f"Will use '{resolution['resolved_adapter']}' ({resolution['source']}). "
                        "Confirm this matches your intent."
                    ),
                )

        return cohort_cfg, checks

    @app.route("/cohort/readiness", methods=["GET"])
    def cohort_readiness():
        """Read-only readiness report for the Full Cohort Run (DataLad SLURM
        array) path -- lets the GUI show everything that needs fixing before
        Setup/Submit, instead of one error per click. Mirrors
        /local_run_readiness's shape for the Quick Test path."""
        project_id = (request.args.get("project_id") or "").strip()
        pipeline_id = (request.args.get("pipeline_id") or "").strip()
        max_concurrent = request.args.get("max_concurrent")

        _cohort_cfg, checks = _cohort_readiness_checks(project_id, pipeline_id, max_concurrent)
        ready = all(c["ok"] for c in checks if c["blocking"])
        return jsonify({"ready": ready, "checks": checks})

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

        import app_profiles  # lazy -- scripts/ is on sys.path at runtime

        gpu_warning = app_profiles.check_gpu_request_feasible(cohort_cfg.get("hpc", {}))
        return jsonify({"config": cohort_cfg, "gpu_warning": gpu_warning})

    @app.route("/cohort/check_open_jobs", methods=["GET"])
    def cohort_check_open_jobs():
        """Report any datalad-slurm jobs left open (unfinished) in this
        project's output dataset -- e.g. a crashed slurm-finish job leaves
        the previous slurm-schedule's outputs permanently claimed, which
        makes every subsequent `datalad slurm-schedule` fail with a cryptic
        "conflicting outputs" error until someone closes it out manually.
        """
        project_id = (request.args.get("project_id") or "").strip()
        pipeline_id = (request.args.get("pipeline_id") or "").strip()
        max_concurrent = request.args.get("max_concurrent")

        cohort_cfg, error_response = _build_cohort_config(
            project_id, pipeline_id, max_concurrent
        )
        if error_response:
            return error_response

        output_dir = cohort_cfg["paths"]["output_dir"]
        result: dict[str, Any] = {
            "dataset": cohort_cfg["datasets"][0],
            "output_dir": output_dir,
            "open_jobs": [],
            "error": None,
        }

        if not (Path(output_dir) / ".datalad").is_dir():
            result["error"] = "Output dataset not cloned yet -- run Setup first."
            return jsonify(result)

        try:
            proc = subprocess.run(
                ["datalad", "slurm-finish", "--list-open-jobs"],
                cwd=output_dir,
                capture_output=True,
                text=True,
                timeout=20,
                env=_datalad_subprocess_env(),
            )
            result["open_jobs"] = _parse_open_slurm_jobs(proc.stdout)
        except subprocess.TimeoutExpired:
            result["error"] = "Timed out checking datalad-slurm job status."
        except FileNotFoundError:
            result["error"] = "datalad executable not found."

        return jsonify(result)

    @app.route("/cohort/close_open_jobs", methods=["POST"])
    def cohort_close_open_jobs():
        """Close failed/cancelled datalad-slurm jobs so a new slurm-schedule
        stops being rejected for "conflicting outputs". Never touches
        pending or running jobs (datalad-slurm itself refuses to)."""
        data = request.get_json(silent=True) or {}
        project_id = (data.get("project_id") or "").strip()
        pipeline_id = (data.get("pipeline_id") or "").strip()
        max_concurrent = data.get("max_concurrent")

        cohort_cfg, error_response = _build_cohort_config(
            project_id, pipeline_id, max_concurrent
        )
        if error_response:
            return error_response

        output_dir = cohort_cfg["paths"]["output_dir"]
        if not (Path(output_dir) / ".datalad").is_dir():
            return (
                jsonify({"ok": False, "error": "Output dataset not cloned yet -- run Setup first."}),
                400,
            )

        try:
            proc = subprocess.run(
                ["datalad", "slurm-finish", "--close-failed-jobs"],
                cwd=output_dir,
                capture_output=True,
                text=True,
                timeout=60,
                env=_datalad_subprocess_env(),
            )
        except subprocess.TimeoutExpired:
            return jsonify({"ok": False, "error": "Timed out closing failed jobs."}), 504

        return jsonify(
            {
                "ok": proc.returncode == 0,
                "output": (proc.stdout or "") + (proc.stderr or ""),
            }
        )

    @app.route("/cohort/check_storage_sync", methods=["GET"])
    def cohort_check_storage_sync():
        """Read-only: reports whether this project's output clone is fully
        synced to the datalad server -- the precondition for reclaiming
        local HPC disk via /cohort/cleanup_local_storage. The input clone
        is a read-only mirror of the datalad server (nothing local-only can
        ever be lost there), so only the output clone's sync state gates
        reclaiming.
        """
        project_id = (request.args.get("project_id") or "").strip()
        pipeline_id = (request.args.get("pipeline_id") or "").strip()
        max_concurrent = request.args.get("max_concurrent")

        cohort_cfg, error_response = _build_cohort_config(
            project_id, pipeline_id, max_concurrent
        )
        if error_response:
            return error_response

        input_dir = cohort_cfg["paths"]["input_dir"]
        output_dir = cohort_cfg["paths"]["output_dir"]
        output_sync = _git_sync_status(output_dir)

        # Only run the (potentially slow -- walks every subject/session)
        # content-completeness check once git sync already passes; no point
        # deep-scanning a clone that isn't even pushed yet.
        completeness = None
        can_reclaim = output_sync["ok"]
        if output_sync["ok"]:
            completeness = _pipeline_completeness_status(cohort_cfg, base_dir)
            can_reclaim = completeness["ok"]

        return jsonify(
            {
                "dataset": cohort_cfg["datasets"][0],
                "input_dir": input_dir,
                "output_dir": output_dir,
                "input_cloned": (Path(input_dir) / ".git").is_dir(),
                "output_cloned": (Path(output_dir) / ".git").is_dir(),
                "output_sync": output_sync,
                "completeness": completeness,
                "can_reclaim": can_reclaim,
            }
        )

    @app.route("/cohort/cleanup_local_storage", methods=["POST"])
    def cohort_cleanup_local_storage():
        """Manually-triggered only: reclaims local HPC disk for both the
        input and output clones via `datalad drop` -- git-annex's own safe
        drop, which refuses to remove content it can't confirm still
        exists elsewhere (never a raw delete). The dataset's .git/.datalad
        structure stays intact, so a later run can `datalad get` again
        without a full re-clone. Gated on the output clone being fully
        synced, re-verified here server-side rather than trusting an
        earlier GET check -- unlike close_open_jobs (which only closes
        already-dead jobs), this is destructive if wrong.
        """
        data = request.get_json(silent=True) or {}
        project_id = (data.get("project_id") or "").strip()
        pipeline_id = (data.get("pipeline_id") or "").strip()
        max_concurrent = data.get("max_concurrent")
        force_unverified = bool(data.get("force_unverified"))

        cohort_cfg, error_response = _build_cohort_config(
            project_id, pipeline_id, max_concurrent
        )
        if error_response:
            return error_response

        input_dir = cohort_cfg["paths"]["input_dir"]
        output_dir = cohort_cfg["paths"]["output_dir"]

        if (Path(output_dir) / ".git").is_dir():
            sync = _git_sync_status(output_dir)
            if not sync["ok"]:
                return (
                    jsonify(
                        {
                            "ok": False,
                            "error": "Output clone is not fully synced -- refusing to drop local content.",
                            "output_sync": sync,
                        }
                    ),
                    409,
                )

            completeness = _pipeline_completeness_status(cohort_cfg, base_dir)
            if not completeness["ok"]:
                if completeness["supported"]:
                    error = (
                        f"Output is incomplete for pipeline '{completeness['pipeline']}' -- "
                        f"{len(completeness['missing_items'])} missing item(s). "
                        "Refusing to drop local content."
                    )
                elif not force_unverified:
                    error = (
                        completeness["error"]
                        + " Content completeness cannot be automatically verified for this "
                        "pipeline -- resend with force_unverified=true after manually "
                        "confirming the output is complete."
                    )
                else:
                    error = None

                if error:
                    return (
                        jsonify(
                            {"ok": False, "error": error, "completeness": completeness}
                        ),
                        409,
                    )

        results: dict[str, Any] = {}
        for label, path in (("input", input_dir), ("output", output_dir)):
            if not (Path(path) / ".git").is_dir():
                results[label] = {"ok": True, "skipped": "not cloned"}
                continue
            try:
                proc = subprocess.run(
                    ["datalad", "drop", "-d", path, "-r", "."],
                    cwd=path,
                    capture_output=True,
                    text=True,
                    timeout=1800,
                    env=_datalad_subprocess_env(),
                )
                results[label] = {
                    "ok": proc.returncode == 0,
                    "output": (proc.stdout or "") + (proc.stderr or ""),
                }
            except subprocess.TimeoutExpired:
                results[label] = {"ok": False, "error": "Timed out running datalad drop."}

        return jsonify({"ok": all(r["ok"] for r in results.values()), "results": results})

    @app.route("/cohort/run", methods=["POST"])
    def cohort_run():
        data = request.get_json(silent=True) or {}
        command = (data.get("command") or "").strip()
        if command not in {"setup", "submit", "submit-subregions", "status"}:
            return jsonify({"error": f"Invalid command: {command}"}), 400

        project_id = (data.get("project_id") or "").strip()
        pipeline_id = (data.get("pipeline_id") or "").strip()
        dry_run = bool(data.get("dry_run", False))
        resume = bool(data.get("resume", False))
        pilot = bool(data.get("pilot", False))
        max_concurrent = data.get("max_concurrent")

        cohort_cfg, error_response = _build_cohort_config(
            project_id, pipeline_id, max_concurrent
        )
        if error_response:
            return error_response

        if command == "submit":
            import app_profiles  # lazy -- scripts/ is on sys.path at runtime

            gpu_error = app_profiles.check_gpu_request_feasible(cohort_cfg.get("hpc", {}))
            if gpu_error:
                return jsonify({"error": gpu_error}), 400

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
        if resume:
            cmd.append("--resume")
        if pilot:
            cmd.append("--pilot")

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
