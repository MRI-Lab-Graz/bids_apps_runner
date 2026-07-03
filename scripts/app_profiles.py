#!/usr/bin/env python3
"""
BIDS App Profile Catalog

Single source of truth for "what does this BIDS app need/support" --
replaces the previously-scattered, independently-duplicated container-name
sniffing that lived in prism_local.py, prism_hpc.py, gui_misc_routes.py,
hpc_datalad_runner.py, and templates/index.html. Each app profile captures
things like whether the app understands the NiPreps --nprocs/--omp-nthreads
/--mem CLI convention, quirk auto-options (e.g. MRIQC's --no-sub), execution
adapter defaults, and per-app HPC starting points.

Resolution is a 3-tier precedence, mirroring the pattern the codebase
already used for fastsurfer execution-adapter detection:
  1. Explicit per-pipeline override (app.app_profile)
  2. common.pipeline_app_name (already the closest thing to a normalized
     app identity key, threaded through both JS and Python)
  3. Container filename/docker-ref sniffing (precise prefix/tag matching,
     not loose substring matching)

Per-pipeline capability overrides (app.app_profile_overrides) can then
patch individual fields on top of whichever profile was resolved -- e.g. a
custom MRIQC fork lacking --nprocs support: {"supports_nipreps_resource_flags": false}.
"""

import copy
import os
from typing import Any, Dict, Optional

DEFAULT_PROFILE: Dict[str, Any] = {
    "display_name": "BIDS App",
    "docs_url": "https://bids-apps.neuroimaging.io/",
    "container_match_names": [],
    "supports_nipreps_resource_flags": False,
    "auto_options": [],
    "execution_adapter_default": "",
    "execution_adapter_aliases": {},
    "completion_wait_seconds": 90,
    "supports_datalad_self_fetch": False,
    "recommended_hpc": None,
}

# recommended_hpc values are starting points tuned from a real production run
# on this cluster (partition=hpc; MRIQC completed 23/23 subjects at cpus=4/
# mem=32G in 45-55min each, with plenty of node headroom to spare -- cpus=8/
# mem=40G raises per-subject internal parallelism for datasets with several
# runs per subject). They only ever pre-fill an editable GUI form field,
# never silently override a submission. Adjust freely per your cluster.
#
# GPU-capable apps (qsiprep, fastsurfer) request a GPU via the sbatch_gres
# key -- any "sbatch_"-prefixed key becomes a literal #SBATCH directive
# (scripts/prism_hpc.py), and prism_hpc.py auto-adds apptainer's --nv flag
# whenever a requested sbatch_* value contains "gpu". qsiprep can offload
# FSL's eddy current/motion correction to eddy_cuda when the container
# includes it, unlike CPU-only apps such as MRIQC. The "gpu" partition name
# and gres syntax ("gpu:1") are placeholders -- match them to your cluster's
# actual GPU partition/gres naming before relying on them.
CATALOG: Dict[str, Dict[str, Any]] = {
    "mriqc": {
        "display_name": "MRIQC",
        "docs_url": "https://mriqc.readthedocs.io/",
        "container_match_names": ["mriqc"],
        "supports_nipreps_resource_flags": True,
        "auto_options": ["--no-sub"],
        "supports_datalad_self_fetch": True,
        "recommended_hpc": {
            "partition": "hpc",
            "time": "04:00:00",
            "mem": "40G",
            "cpus": 8,
        },
    },
    "fmriprep": {
        "display_name": "fMRIPrep",
        "docs_url": "https://fmriprep.org/",
        "container_match_names": ["fmriprep"],
        "supports_nipreps_resource_flags": True,
        "supports_datalad_self_fetch": False,
        "recommended_hpc": {
            "partition": "hpc",
            "time": "18:00:00",
            "mem": "32G",
            "cpus": 8,
        },
    },
    "qsiprep": {
        "display_name": "QSIPrep",
        "docs_url": "https://qsiprep.readthedocs.io/",
        "container_match_names": ["qsiprep"],
        "supports_nipreps_resource_flags": True,
        "recommended_hpc": {
            "partition": "gpu",
            "time": "24:00:00",
            "mem": "32G",
            "cpus": 8,
            "sbatch_gres": "gpu:1",
        },
    },
    "qsiprep_cpu": {
        "display_name": "QSIPrep (CPU)",
        "docs_url": "https://qsiprep.readthedocs.io/",
        # Empty on purpose: container/auto-detection should still resolve a
        # qsiprep container to the "qsiprep" (GPU) entry above by default.
        # This CPU-only variant is only reachable by explicitly picking it
        # from the Compute Preset dropdown -- e.g. while a GPU partition is
        # unavailable (LDAP/sssd not configured on those nodes yet, etc.).
        # QSIPrep's only GPU-accelerated step is FSL eddy (eddy_cuda); it
        # falls back to the CPU eddy_openmp implementation automatically,
        # just slower for that one step -- everything else is CPU-bound
        # regardless of this preset.
        "container_match_names": [],
        "supports_nipreps_resource_flags": True,
        "recommended_hpc": {
            "partition": "hpc",
            "time": "24:00:00",
            "mem": "32G",
            "cpus": 8,
        },
    },
    "qsirecon": {
        "display_name": "QSIRecon",
        "docs_url": "https://qsirecon.readthedocs.io/",
        "container_match_names": ["qsirecon"],
        "supports_nipreps_resource_flags": True,
        "completion_wait_seconds": 300,
        "recommended_hpc": {
            "partition": "hpc",
            "time": "12:00:00",
            "mem": "32G",
            "cpus": 8,
        },
    },
    "fastsurfer": {
        "display_name": "FastSurfer",
        "docs_url": "https://deep-mi.org/research/fastsurfer/",
        "container_match_names": ["fastsurfer"],
        "execution_adapter_default": "fastsurfer-cross",
        "execution_adapter_aliases": {
            "fastsurfer": "fastsurfer-cross",
            "fastsurfer-cross": "fastsurfer-cross",
            "bids-fastsurfer": "fastsurfer-cross",
        },
        # FastSurfer's HPC script path (prism_hpc.py fastsurfer_mode) always
        # passes --nv to apptainer, so it effectively requires a GPU node
        # regardless of this profile -- request one here too so SLURM
        # actually schedules it onto a GPU-equipped node.
        "recommended_hpc": {
            "partition": "gpu",
            "time": "02:00:00",
            "mem": "16G",
            "cpus": 4,
            "sbatch_gres": "gpu:1",
        },
    },
    "freesurfer": {
        "display_name": "FreeSurfer",
        "docs_url": "https://surfer.nmr.mgh.harvard.edu/",
        "container_match_names": ["freesurfer"],
        "recommended_hpc": {
            "partition": "hpc",
            "time": "20:00:00",
            "mem": "16G",
            "cpus": 4,
        },
    },
    "cat12": {
        "display_name": "CAT12",
        "docs_url": "https://neuro-jena.github.io/cat/",
        "container_match_names": ["cat12"],
    },
    "nibabies": {
        "display_name": "NiBabies",
        "docs_url": "https://nibabies.readthedocs.io/",
        "container_match_names": ["nibabies"],
        "supports_nipreps_resource_flags": True,
    },
}


def container_matches_app(container_ref: Optional[str], app_key: str) -> bool:
    """Precise container-ref match for a single catalog app key: a docker
    registry:tag ref ("/{app_key}:"), a bare docker ref ("{app_key}:"
    prefix), or a sif/img filename starting with app_key. Generalizes the
    previously-duplicated _is_mriqc_container/_is_qsiprep_container/
    _is_qsirecon_container matchers."""
    ref = str(container_ref or "").strip().lower()
    if not ref or not app_key:
        return False
    if f"/{app_key}:" in ref:
        return True
    if ref.startswith(f"{app_key}:"):
        return True
    return os.path.basename(ref).startswith(app_key)


def resolve_app_name(
    common: Optional[Dict[str, Any]],
    app: Optional[Dict[str, Any]],
    container_ref: Optional[str] = None,
) -> str:
    """Resolve which catalog entry applies, 3-tier precedence:
    1. app.app_profile (explicit override)
    2. common.pipeline_app_name
    3. container_ref sniffed against each entry's container_match_names

    Returns "" if nothing matches (caller should treat this as the
    DEFAULT_PROFILE / unknown app case)."""
    app_cfg = app if isinstance(app, dict) else {}
    common_cfg = common if isinstance(common, dict) else {}

    explicit = str(app_cfg.get("app_profile", "")).strip().lower()
    if explicit in CATALOG:
        return explicit

    pipeline_app = str(common_cfg.get("pipeline_app_name", "")).strip().lower()
    if pipeline_app in CATALOG:
        return pipeline_app

    ref = container_ref if container_ref is not None else common_cfg.get("container")
    if ref:
        for name, profile in CATALOG.items():
            match_names = profile.get("container_match_names") or [name]
            if any(container_matches_app(ref, m) for m in match_names):
                return name

    return ""


def resolve_app_profile(
    common: Optional[Dict[str, Any]],
    app: Optional[Dict[str, Any]],
    container_ref: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve the full profile dict for this pipeline: catalog lookup via
    resolve_app_name(), falling back to DEFAULT_PROFILE, then shallow-merge
    any app.app_profile_overrides on top (only known DEFAULT_PROFILE keys
    are honored)."""
    name = resolve_app_name(common, app, container_ref=container_ref)
    profile = copy.deepcopy(CATALOG.get(name, DEFAULT_PROFILE))
    profile["name"] = name

    app_cfg = app if isinstance(app, dict) else {}
    overrides = app_cfg.get("app_profile_overrides")
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if key in DEFAULT_PROFILE:
                profile[key] = value

    return profile
