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
import shutil
import subprocess
from typing import Any, Dict, List, Optional

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
    # Command run inside the container to fetch its --help text for option
    # auto-detection (gui_misc_routes.get_app_help). None means "run the
    # container with a bare --help", which works for BIDS-App-style images
    # with a single CLI entrypoint. Apps whose image has no such entrypoint
    # (e.g. freesurfer below, which defaults to /bin/bash) need an explicit
    # in-container executable instead.
    "help_args": None,
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
        # QSIPrep's only GPU-accelerated step is FSL eddy (eddy_cuda). Eddy
        # does NOT auto-detect CUDA -- hpc_datalad_runner.py has to opt in
        # explicitly via --eddy-config (only done when this app is the
        # "qsiprep" profile, not this "qsiprep_cpu" one), so picking this
        # preset just means eddy runs on CPU the whole time, same as if
        # --eddy-config were simply omitted.
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
    "fastsurfer_bids": {
        "display_name": "FastSurfer (BIDS, longitudinal-aware)",
        "docs_url": "https://github.com/karl-koschutnig/FastSurfer-bids",
        # Same container family as "fastsurfer" (a FastSurfer-bids fork build),
        # so it can't be told apart by container-name sniffing -- explicit
        # selection only, same precedent as "qsiprep_cpu" above.
        "container_match_names": [],
        "execution_adapter_default": "fastsurfer-bids",
        "execution_adapter_aliases": {
            "fastsurfer-bids": "fastsurfer-bids",
            "fastsurfer_bids": "fastsurfer-bids",
        },
        # Longer walltime than the cross-sectional "fastsurfer" default
        # (02:00:00): one job now runs run_fastsurfer_bids.py for a whole
        # subject, which auto-dispatches to long_fastsurfer.sh and covers
        # recon-surf for every timepoint (e.g. 3 sessions) in a single job.
        "recommended_hpc": {
            "partition": "gpu",
            "time": "06:00:00",
            "mem": "24G",
            "cpus": 8,
            "sbatch_gres": "gpu:1",
        },
    },
    "freesurfer": {
        "display_name": "FreeSurfer",
        "docs_url": "https://surfer.nmr.mgh.harvard.edu/",
        "container_match_names": ["freesurfer"],
        # The official freesurfer/freesurfer image has no CLI entrypoint --
        # it defaults to /bin/bash, so "run <container> --help" fails (the
        # bare --help gets misparsed by the container's own runscript
        # rather than reaching anything help-aware). recon-all is the
        # actual pipeline driver and does understand --help.
        "help_args": ["recon-all", "--help"],
        # mem/cpus validated against a real staged cross->base->long run
        # (template_run.sbatch): recon-all/segment_subregions are
        # single-threaded per call, and FreeSurfer 8.x's own docs cite
        # ~24GB peak (SynthSeg) -- 32G gives headroom, 16G OOM'd in practice.
        "recommended_hpc": {
            "partition": "hpc",
            "time": "20:00:00",
            "mem": "32G",
            "cpus": 1,
        },
    },
    "freesurfer_bids": {
        "display_name": "FreeSurfer (BIDS, longitudinal-aware)",
        "docs_url": "https://github.com/karl-koschutnig/freesurfer/tree/fs8.2",
        # Unlike "fastsurfer_bids" below, this doesn't need to be empty --
        # resolve_app_name() prefers the longest matching app_key across all
        # catalog entries, so a "freesurfer_bids_*" filename resolves here
        # even though it also technically starts with "freesurfer".
        "container_match_names": ["freesurfer_bids"],
        # The fs8.2 branch's %runscript is a bare "python /run.py" with no
        # "$@" -- verified against the built image: "apptainer run <image>
        # --help" silently drops --help entirely (0 args reach run.py) and
        # argparse errors on missing positionals instead of printing help.
        # "apptainer exec <image> python /run.py --help" bypasses the
        # runscript and works correctly -- same fix shape as the raw
        # "freesurfer" profile's help_args above, different underlying bug.
        "help_args": ["python", "/run.py", "--help"],
        "execution_adapter_default": "freesurfer-bids",
        "execution_adapter_aliases": {
            "freesurfer-bids": "freesurfer-bids",
            "freesurfer_bids": "freesurfer-bids",
        },
        # run.py (bids-apps/freesurfer, fs8.2 branch) auto-discovers a
        # subject's sessions and runs cross-sectional -> base template ->
        # longitudinal for all of them in one call -- same walltime/mem
        # envelope as the "freesurfer" profile above, just one job per
        # subject instead of manifest-driven staged jobs.
        "recommended_hpc": {
            "partition": "hpc",
            "time": "20:00:00",
            "mem": "32G",
            "cpus": 1,
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
        # Prefer the longest (most specific) matching app_key across every
        # catalog entry, not just the first match in dict-insertion order --
        # e.g. "freesurfer_bids_8.2.0.sif" starts with both "freesurfer" and
        # "freesurfer_bids"; the latter must win regardless of which entry
        # happens to be declared first.
        best_name = ""
        best_match_len = -1
        for name, profile in CATALOG.items():
            match_names = profile.get("container_match_names") or [name]
            for m in match_names:
                if len(m) > best_match_len and container_matches_app(ref, m):
                    best_name = name
                    best_match_len = len(m)
        if best_name:
            return best_name

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


def _sinfo_partition_gres(partition: str) -> Optional[List[str]]:
    """GRES strings (one per node) that `sinfo` reports for a partition.
    Returns None -- "couldn't check" -- if sinfo isn't on PATH or the query
    fails, so callers skip validation rather than fail closed on a machine
    without a live SLURM cluster (e.g. a laptop running the GUI)."""
    if not shutil.which("sinfo"):
        return None
    try:
        result = subprocess.run(
            ["sinfo", "-h", "-p", partition, "-o", "%G", "--noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if result.returncode != 0:
        return None
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _sinfo_gpu_partitions() -> List[str]:
    """Partitions cluster-wide that have at least one node advertising a gpu
    gres -- used only to make the "wrong partition" error actionable."""
    if not shutil.which("sinfo"):
        return []
    try:
        result = subprocess.run(
            ["sinfo", "-h", "-o", "%P %G", "--noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    if result.returncode != 0:
        return []
    seen: Dict[str, None] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and "gpu" in parts[1].lower():
            seen.setdefault(parts[0].rstrip("*"), None)
    return list(seen.keys())


def check_gpu_request_feasible(hpc: Optional[Dict[str, Any]]) -> Optional[str]:
    """Guard against the classic SLURM footgun this cluster has: requesting
    a GPU gres on a partition that has none. SLURM doesn't reject that at
    submission time -- the job just sits PENDING forever, which looks
    identical to "cluster is busy" until someone notices, sometimes days
    later.

    Returns None when the request is fine, or when sinfo/SLURM isn't
    reachable (nothing to validate against, so don't block work on a
    non-cluster machine). Otherwise returns a human-readable error naming
    the partitions that actually have GPUs, for the caller to surface.
    """
    hpc_cfg = hpc if isinstance(hpc, dict) else {}
    partition = str(hpc_cfg.get("partition") or "").strip()
    wants_gpu = any(
        "gpu" in str(v).lower()
        for k, v in hpc_cfg.items()
        if k.startswith("sbatch_") and v
    )
    if not wants_gpu or not partition:
        return None

    gres_list = _sinfo_partition_gres(partition)
    if gres_list is None:
        return None
    if any("gpu" in g.lower() for g in gres_list):
        return None

    alternatives = _sinfo_gpu_partitions()
    hint = (
        f" GPU-capable partitions on this cluster: {', '.join(alternatives)}."
        if alternatives
        else ""
    )
    return (
        f"Partition '{partition}' has no GPU nodes (sinfo reports no gpu gres "
        f"there), but this job requests one via sbatch_gres -- it would sit "
        f"PENDING forever instead of failing outright.{hint}"
    )


def describe_execution_adapter_resolution(
    common: Optional[Dict[str, Any]], app: Optional[Dict[str, Any]]
) -> Optional[Dict[str, str]]:
    """Surfaces which execution adapter will actually be used and how it was
    resolved (mirrors the 3-tier resolution in
    prism_local.py/prism_hpc.py/hpc_datalad_runner.py's own
    _infer_execution_adapter, and scripts/hpc_datalad_runner.py's copy) --
    shared here so the Quick Test and Cohort readiness checklists
    (gui/gui_run_routes.py, gui/gui_cohort_routes.py) can't independently
    drift on how they describe this.

    Returns None when the resolved app has no special execution_adapter
    concept at all (the standard BIDS-App convention applies, nothing to
    surface). Otherwise returns {"resolved_adapter": ..., "source": ...}.
    """
    common_cfg = common if isinstance(common, dict) else {}
    app_cfg = app if isinstance(app, dict) else {}
    container_ref = str(common_cfg.get("container") or "")
    explicit = str(app_cfg.get("execution_adapter", "")).strip().lower()

    resolved_adapter = ""
    source = ""
    if explicit:
        for profile in CATALOG.values():
            aliases = profile.get("execution_adapter_aliases", {})
            if explicit in aliases:
                resolved_adapter = aliases[explicit]
                source = f"app.execution_adapter = '{explicit}'"
                break

    if not resolved_adapter:
        app_name = resolve_app_name(common_cfg, app_cfg, container_ref=container_ref)
        resolved_adapter = CATALOG.get(app_name, {}).get("execution_adapter_default", "")
        if resolved_adapter:
            source = f"auto-detected from container name ('{app_name}')"

    if not resolved_adapter:
        return None
    return {"resolved_adapter": resolved_adapter, "source": source}
