import copy
import json
import re
import time
from pathlib import Path
from typing import Any, Callable


class ProjectStore:
    """Persist and load project metadata for the GUI."""

    def __init__(
        self,
        projects_dir: Path,
        machine_settings_provider: Callable[[], dict[str, Any]],
        config_normalizer: Callable[[dict[str, Any]], dict[str, Any]],
        project_dir_resolver: Callable[[Any], Path],
        timestamp_factory: Callable[[], str],
    ) -> None:
        self.projects_dir = Path(projects_dir)
        self.machine_settings_provider = machine_settings_provider
        self.config_normalizer = config_normalizer
        self.project_dir_resolver = project_dir_resolver
        self.timestamp_factory = timestamp_factory

    def create_project(
        self, name: str, description: str = ""
    ) -> tuple[str, dict[str, Any]]:
        project_id = str(name or "").lower().replace(" ", "_").replace("-", "_")
        project_id = re.sub(r"[^a-z0-9_]", "", project_id)
        if not project_id:
            project_id = "project_" + str(int(time.time()))

        counter = 1
        original_id = project_id
        while (self.projects_dir / project_id).exists():
            project_id = f"{original_id}_{counter}"
            counter += 1

        project_dir = self.projects_dir / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "logs").mkdir(exist_ok=True)

        machine_defaults = self.machine_settings_provider()
        default_jobs = machine_defaults.get("default_jobs", 1)
        try:
            default_jobs = max(1, int(default_jobs))
        except (TypeError, ValueError):
            default_jobs = 1

        default_engine = machine_defaults.get("resolved_container_engine", "apptainer")
        default_container = ""
        if default_engine == "docker":
            docker_repo = str(machine_defaults.get("default_docker_repo") or "").strip()
            docker_tag = (
                str(machine_defaults.get("default_docker_tag") or "latest").strip()
                or "latest"
            )
            if docker_repo:
                default_container = f"{docker_repo}:{docker_tag}"
        else:
            default_container = str(
                machine_defaults.get("default_apptainer_container") or ""
            ).strip()

        default_tmp_folder = str(
            machine_defaults.get("default_tmp_folder") or ""
        ).strip()
        default_templateflow_dir = str(
            machine_defaults.get("default_templateflow_dir") or ""
        ).strip()

        default_common = {
            "bids_folder": "",
            "output_folder": "",
            "tmp_folder": default_tmp_folder,
            "templateflow_dir": default_templateflow_dir,
            "pipeline_output_root": "",
            "pipeline_app_name": "",
            "pipeline_version": "",
            "pipeline_auto_versioning": False,
            "notify_email": "",
            "container_engine": default_engine,
            "container": default_container,
            "jobs": default_jobs,
        }
        default_app = {"analysis_level": "participant", "options": [], "mounts": []}
        timestamp = self.timestamp_factory()
        project_json = {
            "id": project_id,
            "name": name,
            "description": description,
            "created": timestamp,
            "last_modified": timestamp,
            "last_log": None,
            "config": {
                "common": copy.deepcopy(default_common),
                "app": copy.deepcopy(default_app),
                "pipelines": {
                    "default": {
                        "name": "Default Pipeline",
                        "description": "",
                        "common": copy.deepcopy(default_common),
                        "app": copy.deepcopy(default_app),
                    }
                },
                "active_pipeline": "default",
            },
        }

        project_json_path = project_dir / "project.json"
        with open(project_json_path, "w", encoding="utf-8") as f:
            json.dump(project_json, f, indent=2)

        return project_id, project_json

    def load_project(self, project_id: Any) -> dict[str, Any] | None:
        try:
            project_dir = self.project_dir_resolver(project_id)
        except ValueError:
            return None
        project_json_path = project_dir / "project.json"

        if not project_json_path.exists():
            return None

        with open(project_json_path, "r", encoding="utf-8") as f:
            project_json = json.load(f)

        if isinstance(project_json, dict) and isinstance(
            project_json.get("config"), dict
        ):
            project_json["config"] = self.config_normalizer(project_json["config"])

        return project_json

    def save_project(self, project_id: Any, config: dict[str, Any]) -> bool:
        try:
            project_dir = self.project_dir_resolver(project_id)
        except ValueError:
            return False
        project_json_path = project_dir / "project.json"

        if not project_json_path.exists():
            return False

        with open(project_json_path, "r", encoding="utf-8") as f:
            project_json = json.load(f)

        project_json["config"] = self.config_normalizer(config)
        project_json["last_modified"] = self.timestamp_factory()

        with open(project_json_path, "w", encoding="utf-8") as f:
            json.dump(project_json, f, indent=2)

        return True

    def patch_pipeline_option_cache(
        self, project_id: Any, pipeline_id: str, cache: dict[str, Any]
    ) -> bool:
        try:
            project_dir = self.project_dir_resolver(project_id)
        except ValueError:
            return False
        project_json_path = project_dir / "project.json"
        if not project_json_path.exists():
            return False
        with open(project_json_path, "r", encoding="utf-8") as f:
            project_json = json.load(f)
        cfg = project_json.get("config", {})
        pipelines = cfg.get("pipelines", {})
        if pipeline_id not in pipelines:
            return False
        pipeline = pipelines[pipeline_id]
        if not isinstance(pipeline.get("app"), dict):
            pipeline["app"] = {}
        pipeline["app"]["option_help_cache"] = cache
        project_json["last_modified"] = self.timestamp_factory()
        with open(project_json_path, "w", encoding="utf-8") as f:
            json.dump(project_json, f, indent=2)
        return True

    def update_project_log(self, project_id: Any, log_filename: str) -> bool:
        try:
            project_dir = self.project_dir_resolver(project_id)
        except ValueError:
            return False
        project_json_path = project_dir / "project.json"

        if not project_json_path.exists():
            return False

        with open(project_json_path, "r", encoding="utf-8") as f:
            project_json = json.load(f)

        project_json["last_log"] = log_filename
        project_json["last_modified"] = self.timestamp_factory()

        with open(project_json_path, "w", encoding="utf-8") as f:
            json.dump(project_json, f, indent=2)

        return True

    def list_projects(self, limit: int | None = None) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []

        if not self.projects_dir.exists():
            return projects

        for project_dir in sorted(
            self.projects_dir.iterdir(), key=lambda item: item.is_dir(), reverse=True
        ):
            if not project_dir.is_dir():
                continue

            project_json_path = project_dir / "project.json"
            if not project_json_path.exists():
                continue

            try:
                with open(project_json_path, "r", encoding="utf-8") as f:
                    project_data = json.load(f)
                if isinstance(project_data, dict) and isinstance(
                    project_data.get("config"), dict
                ):
                    project_data["config"] = self.config_normalizer(
                        project_data["config"]
                    )
                projects.append(project_data)
            except Exception as exc:
                print(f"[ERROR] Failed to load project {project_dir.name}: {exc}")

        projects.sort(key=lambda item: item.get("last_modified", ""), reverse=True)
        if limit:
            projects = projects[:limit]
        return projects

    def count_projects(self) -> int:
        if not self.projects_dir.exists():
            return 0

        total = 0
        for project_dir in self.projects_dir.iterdir():
            if project_dir.is_dir() and (project_dir / "project.json").exists():
                total += 1
        return total

    def delete_project(self, project_id: Any) -> bool:
        try:
            project_dir = self.project_dir_resolver(project_id)
        except ValueError:
            return False

        if project_dir.exists():
            import shutil

            shutil.rmtree(project_dir)
            return True

        return False
