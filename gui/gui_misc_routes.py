import glob
import json
import os
import platform
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from flask import jsonify, render_template, request


def register_misc_routes(
    app,
    *,
    bids_output_validator_cls,
    ensure_logs_dir: Callable[[], None],
    log_dir: Path,
    base_dir: Path,
):
    @app.route("/list_reports", methods=["POST"])
    def list_reports():
        data = request.get_json(silent=True) or {}
        derivatives_dir = data.get("derivatives_dir")
        pipeline = data.get("pipeline")

        if not derivatives_dir:
            return jsonify({"error": "Derivatives folder required"}), 400

        path = Path(os.path.expanduser(derivatives_dir))
        if pipeline:
            path = path / pipeline

        if not path.exists():
            return jsonify({"reports": []})

        reports = []
        for html_file in sorted(list(path.glob("sub-*.html"))):
            reports.append(
                {
                    "name": html_file.name,
                    "path": str(html_file),
                    "subject": html_file.name.split("_")[0].split(".")[0],
                    "modified": datetime.fromtimestamp(html_file.stat().st_mtime).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                }
            )

        return jsonify({"reports": reports})

    @app.route("/run_output_check", methods=["POST"])
    def run_output_check():
        data = request.get_json(silent=True) or {}
        bids_dir = (data.get("bids_dir") or "").strip()
        derivatives_dir = (data.get("derivatives_dir") or "").strip()
        verbose = bool(data.get("verbose"))
        quiet = bool(data.get("quiet"))

        if not bids_dir or not derivatives_dir:
            return jsonify({"error": "Both BIDS and derivatives folders must be provided."}), 400

        if verbose and quiet:
            return jsonify({"error": "Verbose and quiet modes cannot both be enabled."}), 400

        bids_path = Path(os.path.expanduser(bids_dir))
        derivatives_path = Path(os.path.expanduser(derivatives_dir))
        if not bids_path.exists():
            return jsonify({"error": f"BIDS folder does not exist: {bids_path}"}), 400
        if not derivatives_path.exists():
            return jsonify({"error": f"Derivatives folder does not exist: {derivatives_path}"}), 400

        ensure_logs_dir()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = log_dir / f"check_app_output_{timestamp}.log"

        script_path = base_dir / "scripts" / "check_app_output.py"
        if not script_path.exists():
            script_path = base_dir / "check_app_output.py"
        if not script_path.exists():
            return jsonify({"error": f"Checker script not found: {script_path}"}), 500

        cmd = [
            sys.executable,
            str(script_path),
            str(bids_path),
            str(derivatives_path),
            "--json",
            "--log",
            str(log_file),
        ]
        pipeline = (data.get("pipeline") or "").strip()
        if pipeline:
            cmd.extend(["-p", pipeline])
        if verbose:
            cmd.append("--verbose")
        if quiet:
            cmd.append("--quiet")
        if data.get("list_missing"):
            cmd.append("--list-missing-subjects")

        try:
            timeout_seconds = int(data.get("timeout", 900))
        except (TypeError, ValueError):
            timeout_seconds = 900
        timeout_seconds = max(30, min(timeout_seconds, 7200))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                cwd=str(base_dir),
            )
        except subprocess.TimeoutExpired as exc:
            return jsonify({"error": "Validation timed out", "details": str(exc)}), 500

        parsed = None
        try:
            parsed = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            try:
                start = result.stdout.find("{")
                end = result.stdout.rfind("}")
                if start != -1 and end != -1:
                    parsed = json.loads(result.stdout[start : end + 1])
            except (json.JSONDecodeError, TypeError, AttributeError):
                parsed = None

        return jsonify(
            {
                "success": result.returncode == 0,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "parsed": parsed,
                "log_file": str(log_file),
            }
        )

    @app.route("/detect_validation_pipelines", methods=["POST"])
    def detect_validation_pipelines():
        data = request.get_json(silent=True) or {}
        bids_dir = (data.get("bids_dir") or "").strip()
        derivatives_dir = (data.get("derivatives_dir") or "").strip()

        if not bids_dir or not derivatives_dir:
            return jsonify({"error": "Both BIDS and derivatives folders are required."}), 400

        try:
            bids_path = Path(os.path.expanduser(bids_dir))
            derivatives_path = Path(os.path.expanduser(derivatives_dir))

            if not bids_path.exists() or not derivatives_path.exists():
                return jsonify({"pipelines": []})

            validator = bids_output_validator_cls(bids_path, derivatives_path)
            pipelines = validator.discover_pipelines()
            return jsonify({"pipelines": pipelines})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/get_app_help", methods=["POST"])
    def get_app_help():
        payload = request.get_json(silent=True) or {}
        container = payload.get("container")
        engine = payload.get("container_engine", "apptainer")

        if not container:
            return jsonify({"error": "Container name or path required"}), 400

        if engine == "apptainer" and not os.path.exists(container):
            return jsonify({"error": f"Apptainer image not found at: {container}"}), 400

        try:
            print(f"[GUI] Fetching help for {container} using {engine}...", flush=True)
            if engine == "docker":
                inspect_cmd = ["docker", "image", "inspect", container]
                inspect_result = subprocess.run(inspect_cmd, capture_output=True)
                if inspect_result.returncode != 0:
                    return (
                        jsonify(
                            {
                                "error": f'Docker image "{container}" not found locally. You must pull it before parameters can be analyzed.',
                                "need_pull": True,
                            }
                        ),
                        400,
                    )
                if platform.system() == "Darwin" and platform.machine() == "arm64":
                    cmd = ["docker", "run", "--rm", "--platform", "linux/amd64", container, "--help"]
                else:
                    cmd = ["docker", "run", "--rm", container, "--help"]
            else:
                cmd = ["apptainer", "run", "--containall", container, "--help"]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            output = result.stdout + result.stderr

            usage_lines = []
            lines = output.splitlines()
            usage_started = False
            for line in lines:
                if not usage_started and line.strip().startswith("usage:"):
                    usage_started = True
                    usage_lines.append(line)
                    continue
                if usage_started:
                    if line.startswith(" "):
                        usage_lines.append(line)
                        continue
                    break

            usage_block = "\n".join(usage_lines)
            usage_all_flags = set(re.findall(r"--[a-zA-Z0-9-]+", usage_block))
            usage_optional_flags = set(re.findall(r"\[\s*(--[a-zA-Z0-9-]+)", usage_block))
            usage_required_flags = usage_all_flags - usage_optional_flags

            deprecated_flags = set()
            for line in output.splitlines():
                if "deprecated" not in line.lower():
                    continue
                for dep_flag in re.findall(r"--[a-zA-Z0-9-]+", line):
                    deprecated_flags.add(dep_flag)

            if "--subject-anatomical-reference" in output and "--longitudinal" in output:
                deprecated_flags.add("--longitudinal")

            if result.returncode != 0:
                print(f"[GUI] {engine.capitalize()} help returned exit code {result.returncode}", flush=True)

            parts = re.split(r"\n(?=[A-Z][A-Za-z0-9\-\(\) ]+:)", output)
            sections = []
            exclude = {
                "--help",
                "--version",
                "--participant-label",
                "--space",
                "--bids-filter-file",
            }

            for part in parts:
                lines = part.strip().split("\n")
                if not lines:
                    continue

                header = lines[0].strip().rstrip(":")
                if any(token in header.lower() for token in ["usage", "synopsis", "description"]):
                    continue

                content = "\n".join(lines[1:])
                if "--" not in part:
                    continue

                options = []
                arg_blocks = re.split(r"\n\s*(?=--)", "\n" + content)
                for block in arg_blocks:
                    block = block.strip()
                    if not block.startswith("--"):
                        flag_match = re.search(r"(--[a-zA-Z0-9-]+)", block)
                        if not flag_match:
                            continue

                    flag_match = re.search(r"(--[a-zA-Z0-9-]+)", block)
                    if not flag_match:
                        continue
                    flag = flag_match.group(1)
                    if flag in exclude or flag in deprecated_flags:
                        continue
                    if any(option["flag"] == flag for option in options):
                        continue

                    choices = []
                    choice_match = re.search(r"\{([^}]+)\}", block)
                    if choice_match:
                        choices = [choice.strip() for choice in choice_match.group(1).split(",")]
                    else:
                        choice_text_match = re.search(r"Possible choices:\s*([^\n]+)", block)
                        if choice_text_match:
                            choices = [
                                choice.strip().strip(",")
                                for choice in re.split(r"[,\s]+", choice_text_match.group(1))
                            ]
                            choices = [choice for choice in choices if choice and not choice.startswith("-")]

                    block_lines = block.strip().split("\n")
                    description = ""
                    if len(block_lines) > 1:
                        description = " ".join(line.strip() for line in block_lines[1:])
                    elif "  " in block:
                        parts_of_line = re.split(r"\s{2,}", block.strip())
                        if len(parts_of_line) > 1:
                            description = " ".join(parts_of_line[1:])

                    description = re.sub(r"\s+", " ", description)
                    description = re.sub(r"\(default:.*?\)", "", description).strip()
                    if "deprecated" in description.lower():
                        continue

                    definition_line = block_lines[0] if block_lines else block
                    columns = re.split(r"\s{2,}", definition_line.strip())
                    signature = columns[0] if columns else definition_line.strip()
                    sig_match = re.search(rf"{re.escape(flag)}(?:\s+[^\s].*)?$", signature)
                    signature_tail = sig_match.group(0) if sig_match else signature
                    sig_tokens = signature_tail.split()

                    has_value = False
                    if choices:
                        has_value = True
                    elif sig_tokens and sig_tokens[0] == flag and len(sig_tokens) > 1:
                        has_value = True

                    is_multiple = bool(re.search(r"\[.*\.\.\..*\]|\.\.\.", signature_tail))
                    display_name = flag.lstrip("-")
                    is_negated = False
                    negation_match = re.search(r"^(no-|skip[-_]|without-|fs-no-)(.*)", display_name)
                    if negation_match and not has_value:
                        is_negated = True
                        display_name = negation_match.group(2)

                    options.append(
                        {
                            "flag": flag,
                            "name": display_name.replace("-", " ").replace("_", " ").title(),
                            "is_negated": is_negated,
                            "choices": choices,
                            "description": description,
                            "has_value": has_value,
                            "is_multiple": is_multiple,
                            "required": flag in usage_required_flags,
                        }
                    )

                if options:
                    sections.append({"title": header, "options": sorted(options, key=lambda option: option["name"])})

            parsed_flags = {
                option.get("flag")
                for section in sections
                for option in section.get("options", [])
                if option.get("flag")
            }

            if (
                "--subject-anatomical-reference" not in parsed_flags
                and "--subject-anatomical-reference" in output
            ):
                choices = []
                choice_match = re.search(r"--subject-anatomical-reference\s+\{([^}]+)\}", output)
                if choice_match:
                    choices = [choice.strip() for choice in choice_match.group(1).split(",") if choice.strip()]

                fallback_option = {
                    "flag": "--subject-anatomical-reference",
                    "name": "Subject Anatomical Reference",
                    "is_negated": False,
                    "choices": choices,
                    "description": "Replacement for deprecated --longitudinal behavior.",
                    "has_value": True,
                    "is_multiple": False,
                    "required": "--subject-anatomical-reference" in usage_required_flags,
                }

                target_section = None
                for section in sections:
                    if section.get("title", "").lower() == "workflow configuration":
                        target_section = section
                        break

                if target_section is None:
                    sections.append({"title": "Workflow configuration", "options": [fallback_option]})
                else:
                    target_section_options = target_section.get("options", [])
                    target_section_options.append(fallback_option)
                    target_section["options"] = sorted(target_section_options, key=lambda option: option["name"])

            app_name = "BIDS App"
            doc_url = "https://bids-apps.neuroimaging.io/"
            container_lower = os.path.basename(container).lower()
            if "qsirecon" in container_lower:
                app_name = "QSIRecon"
                doc_url = "https://qsirecon.readthedocs.io/"
            elif "qsiprep" in container_lower:
                app_name = "QSIPrep"
                doc_url = "https://qsiprep.readthedocs.io/"
            elif "fmriprep" in container_lower:
                app_name = "fMRIPrep"
                doc_url = "https://fmriprep.org/"
            elif "mriqc" in container_lower:
                app_name = "MRIQC"
                doc_url = "https://mriqc.readthedocs.io/"

            return jsonify(
                {
                    "sections": sections,
                    "app_info": {"name": app_name, "url": doc_url},
                    "deprecated_flags": sorted(list(deprecated_flags)),
                    "raw_help": output if not sections else None,
                }
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/get_templateflow_templates", methods=["POST"])
    def get_templateflow_templates():
        payload = request.get_json(silent=True) or {}
        tf_dir = payload.get("path")
        if not tf_dir or not os.path.exists(tf_dir):
            return jsonify({"error": "TemplateFlow directory not found"}), 400

        try:
            templates = []
            for entry in os.scandir(tf_dir):
                if entry.is_dir() and entry.name.startswith("tpl-"):
                    template_name = entry.name[4:]
                    resolutions = set()
                    res_pattern = re.compile(r"res-([a-zA-Z0-9]+)")
                    try:
                        for root, _, files in os.walk(entry.path):
                            if root.count(os.sep) - entry.path.count(os.sep) > 2:
                                continue
                            for filename in files:
                                match = res_pattern.search(filename)
                                if match:
                                    resolutions.add(match.group(1))
                            if len(resolutions) > 20:
                                break
                    except OSError:
                        pass

                    templates.append({"name": template_name, "resolutions": sorted(list(resolutions))})

            return jsonify({"templates": sorted(templates, key=lambda item: item["name"])})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/list_dirs", methods=["POST"])
    def list_dirs():
        payload = request.get_json(silent=True) or {}
        path = payload.get("path", "/")
        include_files = bool(payload.get("include_files"))
        include_hidden = bool(payload.get("include_hidden"))
        extensions = payload.get("extensions") or []
        file_name = payload.get("file_name") or ""
        if not path:
            path = "/"

        try:
            current = Path(path)
            if current.exists() and current.is_file():
                current = current.parent
            if not current.exists() or not current.is_dir():
                current = Path("/")

            items = []
            if current.parent != current:
                items.append({"name": "..", "path": str(current.parent), "is_dir": True})

            for child in sorted(current.iterdir()):
                if child.is_dir() and (include_hidden or not child.name.startswith(".")):
                    items.append({"name": child.name, "path": str(child.absolute()), "is_dir": True})
                elif include_files and child.is_file() and (include_hidden or not child.name.startswith(".")):
                    if file_name and child.name != file_name:
                        continue
                    if extensions and not any(child.name.endswith(extension) for extension in extensions):
                        continue
                    items.append({"name": child.name, "path": str(child.absolute()), "is_dir": False})

            return jsonify({"current_path": str(current.absolute()), "items": items})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @app.route("/")
    def index():
        try:
            return render_template("index.html")
        except Exception as exc:
            print(f"[DEBUG] Template error: {exc}", flush=True)
            return str(exc), 500

    @app.route("/list_containers", methods=["POST"])
    def list_containers():
        payload = request.get_json(silent=True) or {}
        folder = payload.get("folder")
        if not folder:
            return jsonify({"error": "No folder provided"}), 400

        try:
            folder_path = os.path.expanduser(folder)
            containers = glob.glob(os.path.join(folder_path, "*.sif")) + glob.glob(
                os.path.join(folder_path, "*.simg")
            )
            containers = [os.path.basename(container) for container in containers]
            return jsonify({"containers": sorted(containers)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500
