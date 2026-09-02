#!/usr/bin/env bash
# submit_bids_cohort.sh
#
# Orchestrate any BIDS app across multiple datasets on a SLURM/DataLad HPC.
# Works with fMRIPrep, QSIPrep, MRIQC, or any other BIDS-app container.
#
# Two-phase workflow, built on the `datalad-slurm` extension
# (https://github.com/knuedd/datalad-slurm) so that no datalad/git
# operations ever happen inside a parallel SLURM job -- only in this
# script, sequentially, before and after submission.
# ──────────────────
# Phase 1 – setup (run once, needs DataLad + network access)
#   • Pre-clones every dataset to shared HPC storage (fast subsequent clones;
#     metadata-only -- no file content yet)
#   • Creates the per-dataset output DataLad repos on the DataLad SSH server
#
# Phase 2 – submit (run after setup, submits SLURM array jobs)
#   • Builds subject lists from pre-cloned datasets
#   • Prefetches (`datalad get`) only this cohort's own subject list -- not
#     the whole dataset -- so array tasks never call it themselves; still
#     runs once, sequentially, before scheduling
#   • Generates a plain SLURM array job script per dataset (via
#     hpc_datalad_runner.py) -- the script itself contains no datalad/git calls
#   • `datalad slurm-schedule`s the array job (declares one -o per subject,
#     submits via sbatch itself); records job IDs in submission.log
#   • Chains a dependent finish job (--dependency=afterany) that runs
#     `datalad slurm-finish` (one commit covering the whole array) + push,
#     once the array completes
#
# Usage
# ─────
#   ./scripts/submit_bids_cohort.sh setup              [OPTIONS]
#   ./scripts/submit_bids_cohort.sh submit              [OPTIONS]
#   ./scripts/submit_bids_cohort.sh submit-subregions   [OPTIONS]
#   ./scripts/submit_bids_cohort.sh status
#
#   submit-subregions runs FreeSurfer's segment_subregions (thalamus /
#   hippo-amygdala / brainstem, requires .subregion_segmentation.enabled in
#   the config) directly against an ALREADY-FINISHED output dataset -- no
#   fresh recon-all array, no dependency to wait on. Use this instead of
#   `submit` to add subregion segmentation to a cohort that already
#   completed; `submit` always schedules a full recon-all array too.
#
# Options
#   -c CONFIG      Path to config JSON  (default: configs/cohort_hpc_example.json)
#   -d DATASET_ID  Process only this dataset (can be repeated)
#   --dry-run      Print commands without executing
#   --resume       Skip datasets whose subject list or job script already exist
#   --pilot        submit only: narrow to one randomly-chosen subject per
#                  dataset (separate _pilot-suffixed subject list/array
#                  script, never touches the real cohort's files) -- a real
#                  "submit" through the full container/mount/DataLad
#                  provenance path, just for one subject, to sanity-check
#                  everything before committing to the whole cohort
#
# Prerequisites
# ─────────────
#   • jq             (JSON parsing)
#   • python3        (hpc_datalad_runner.py)
#   • datalad        (available via module or PATH)
#   • datalad-slurm   extension (pip install git+https://github.com/knuedd/datalad-slurm.git;
#                      not on PyPI -- provides slurm-schedule/slurm-finish)
#   • sbatch         (SLURM, only needed for submit phase)
#   • ssh access to the DataLad server (only needed for setup phase)
#   • .datalad-slurm-venv at the repo root -- a dedicated venv pinned to a
#     uv-managed portable Python (not a symlink to the system python3), used
#     only by the dependent "finish" job. Compute nodes on a cluster can run
#     a different system python3 than the login node, which silently breaks
#     any venv/uv-tool install that just symlinks to system python (both
#     .appsrunner and a plain `uv tool install git-annex` hit this). Set up
#     once with:
#       uv python install 3.10
#       uv venv --python 3.10 .datalad-slurm-venv
#       uv pip install --python .datalad-slurm-venv/bin/python datalad git-annex \
#         git+https://github.com/knuedd/datalad-slurm.git
#
# Edit the TODO values in your config JSON before running.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

# ── Defaults ──────────────────────────────────────────────────────────────────
CONFIG="${REPO_DIR}/configs/cohort_hpc_example.json"
DRY_RUN=false
RESUME=false
PILOT=false
FILTER_DATASETS=()
SUBJ_LISTS_DIR=""        # resolved from config
# Only ever set via --batch-idx/--submission-log/--commit-prefix, used by
# the internal `_continue-batch` command (see cmd_continue_batch).
CONTINUE_BATCH_IDX=""
CONTINUE_SUBMISSION_LOG=""
CONTINUE_COMMIT_PREFIX=""

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo "[$(date '+%H:%M:%S')] $*"; }
warn() { echo "[WARN] $*" >&2; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

run() {
    if $DRY_RUN; then
        echo "[DRY-RUN] $*"
    else
        "$@"
    fi
}

# Require jq for JSON parsing
require_jq() {
    command -v jq &>/dev/null || die "jq is required (apt/brew install jq)"
}

# Parse paths from config
cfg() { jq -r "$1" "$CONFIG"; }

check_todos() {
    if grep -q '"TODO' "$CONFIG"; then
        die "Config still has TODO placeholders: $(grep -o '"TODO[^"]*"' "$CONFIG" | head -5 | tr '\n' ' ')\nEdit $CONFIG before running."
    fi
}

# Emits the shell text (evaluated at job-runtime on the compute node, not
# here) that verifies a finish job's `datalad push --to origin` actually
# landed -- both the finish-job heredocs below (cmd_submit and
# submit_subregion_segmentation) interpolate this via `$(push_verification_block)`
# so the check can't drift out of sync between the two call sites.
#
# `datalad push` can exit 0 without the push having actually landed: a
# rejected git ref update (real incident 2026-07-29 -- "remote rejected
# (branch is currently checked out)" against 129/freesurfer, which sat
# unpushed for days before anyone noticed) or a partial annexed-content
# transfer failure are both surfaced only in datalad's own JSON result
# stream, never as a nonzero process exit, so `set -e` never catches them.
# This independently confirms (a) the remote ref for the current branch now
# equals local HEAD via a live but cheap (metadata-only, no data transfer)
# `git ls-remote`, and (b) every annexed file datalad believes it pushed is
# actually present on origin via `git annex find --not --in origin`.
#
# Uses a quoted heredoc (<<'BLOCK') so none of its $vars expand here --
# they're meant to stay literal text, to be evaluated later when the
# generated finish script actually runs on the compute node (the same
# effect the two call sites already get from hand-escaping \$uncommitted
# etc. in their own unquoted <<EOF heredocs).
push_verification_block() {
    cat <<'BLOCK'
# Verify the push actually landed on the remote -- datalad push can exit 0
# without it really landing (see push_verification_block() in
# submit_bids_cohort.sh for why).
current_branch=$(git symbolic-ref --short HEAD)
local_head=$(git rev-parse HEAD)
remote_head=$(DATALAD_SSH_MULTIPLEX__CONNECTIONS=false timeout 30 git ls-remote origin "refs/heads/${current_branch}" 2>/dev/null | cut -f1)
if [[ "$remote_head" != "$local_head" ]]; then
    echo "ERROR: push did not land -- local HEAD (${local_head}) != origin/${current_branch} (${remote_head:-unreachable}). The commit exists locally but the datalad server does not have it." >&2
    exit 1
fi

missing_content=$(git annex find --not --in origin 2>/dev/null | head -20)
if [[ -n "$missing_content" ]]; then
    echo "ERROR: annexed content missing from origin after push (git ref landed, but file content did not). First 20 missing files:" >&2
    echo "$missing_content" >&2
    exit 1
fi
BLOCK
}

# Verify a real commit actually happened after `slurm-finish
# --commit-failed-jobs`. Shared between the array-finish and
# subregion-finish templates the same way push_verification_block() is,
# instead of being hand-copied into both.
#
# --commit-failed-jobs (not --close-failed-jobs -- see the call site's own
# comment for why that distinction matters) already makes datalad_slurm
# commit whatever output a TIMEOUT'd/CANCELLED array element produced
# before removing its DB entry, and that removal already runs after the
# save (scripts/patches/datalad_slurm_finish_db_removal_order.patch,
# applied to .datalad-slurm-venv). So this check is now a safety net for
# genuine anomalies (disk full, a git-annex hash failure) rather than the
# routine occurrence it used to be when only --close-failed-jobs was used.
# Confirmed real incident this originally guarded against: two separate
# "COMPLETED" finish jobs for a 150-subject FreeSurfer cohort never
# committed anything at all. Reported upstream:
# https://github.com/knuedd/datalad-slurm/issues/97. Fail loudly here
# instead of reporting false success, so normal job-failure monitoring
# (email, cmd_status) catches it rather than it going unnoticed.
uncommitted_check_block() {
    cat <<'BLOCK'
uncommitted=$(git status --porcelain | wc -l)
if [[ "$uncommitted" -gt 0 ]]; then
    echo "ERROR: ${uncommitted} uncommitted change(s) remain after slurm-finish -- the real commit likely never happened (see datalad-slurm's known premature-DB-removal issue). Uncommitted paths:" >&2
    git status --short | head -30 >&2
    exit 1
fi
BLOCK
}

# ── Argument parsing ──────────────────────────────────────────────────────────
COMMAND="${1:-help}"
shift || true

while [[ $# -gt 0 ]]; do
    case "$1" in
        -c|--config)      CONFIG="$2";           shift 2 ;;
        -d|--dataset)     FILTER_DATASETS+=("$2"); shift 2 ;;
        --dry-run)        DRY_RUN=true;           shift ;;
        --resume)         RESUME=true;            shift ;;
        --pilot)          PILOT=true;             shift ;;
        # Internal, used only by the finish-job chain a batched cohort
        # submits into itself (see schedule_one_batch/cmd_continue_batch) --
        # not documented in cmd_help's public command list.
        --batch-idx)       CONTINUE_BATCH_IDX="$2";      shift 2 ;;
        --submission-log)  CONTINUE_SUBMISSION_LOG="$2"; shift 2 ;;
        --commit-prefix)   CONTINUE_COMMIT_PREFIX="$2";  shift 2 ;;
        -h|--help)        COMMAND=help;           shift ;;
        *) die "Unknown option: $1" ;;
    esac
done

# ── Resolve config values ─────────────────────────────────────────────────────
resolve_config() {
    require_jq
    [[ -f "$CONFIG" ]] || die "Config not found: $CONFIG"
    # Normalize to an absolute path: finish jobs embed $CONFIG verbatim into
    # a self-chained `_continue-batch` call (see continue_block) that runs
    # after `cd "${output_clone}"` on a compute node -- a relative CONFIG
    # (e.g. from `-c configs/foo.json` on the login node) no longer resolves
    # there, silently breaking multi-batch chaining after batch 1's push
    # already succeeded. Confirmed real incident (2026-08-11): every batched
    # dataset in the megastudy_openneuro_mriqc cohort stalled after batch 1.
    CONFIG="$(realpath "$CONFIG")"

    SHARED_INPUT_BASE="$(jq -r '.paths.shared_input_base // ""' "$CONFIG")"
    SHARED_OUTPUT_BASE="$(jq -r '.paths.shared_output_base // ""' "$CONFIG")"
    # input_dir/output_dir, when present, are used verbatim in place of the
    # shared_input_base/shared_output_base + dataset_id composition below --
    # this lets a caller (e.g. the GUI) point directly at an existing
    # project's bids_folder/output_folder so paths are identical to local
    # execution instead of having to fit this script's own layout convention.
    INPUT_DIR_OVERRIDE="$(jq -r '.paths.input_dir // ""' "$CONFIG")"
    OUTPUT_DIR_OVERRIDE="$(jq -r '.paths.output_dir // ""' "$CONFIG")"
    SUBJ_LISTS_DIR="$(cfg '.paths.subject_lists_dir')"
    OUTPUT_URL_TPL="$(cfg '.datalad.output_url_template')"
    # Support both new generic key and legacy openneuro_url_template
    INPUT_URL_TPL="$(cfg '.datalad.input_url_template // .datalad.openneuro_url_template // ""')"

    # Build dataset list (filtered if -d was given). Each entry may be a
    # plain ID string or an object ({"id": ..., "options_extra": [...],
    # "apptainer_args_extra": [...], "hpc_overrides": {...}}) carrying
    # per-dataset bids_app.options/bids_app.apptainer_args/hpc overrides
    # consumed by hpc_datalad_runner.py --array-mode; only the id is needed
    # here since every path/URL below is keyed off the plain ID string.
    mapfile -t ALL_DATASETS < <(jq -r '.datasets[] | if type=="object" then .id else . end' "$CONFIG")
    if [[ ${#FILTER_DATASETS[@]} -gt 0 ]]; then
        DATASETS=("${FILTER_DATASETS[@]}")
    else
        DATASETS=("${ALL_DATASETS[@]}")
    fi

    APP_OUT_DIR="$(jq -r '.bids_app.output_dir_name // .bids_app.app_name // "output"' "$CONFIG")"
    APP_NAME="$(jq -r '.bids_app.app_name // "bids_app"' "$CONFIG")"
    LOG_DIR_BASE="$(cfg '.paths.log_dir')"
    mapfile -t HPC_MODULES < <(jq -r '.hpc.modules[]? // empty' "$CONFIG")

    # Cohorts get split into sequential batches of this many subjects, each
    # with its own array+finish job pair, so a slow/failed finish only costs
    # one batch instead of the whole study (see schedule_one_batch below for
    # why batches can't just be pre-scheduled with SBATCH --dependency).
    # jq's `//` only substitutes on null/absent, not on 0 -- so an explicit
    # `"batch_size": 0` in the config is a real, working escape hatch back to
    # today's single-array-per-dataset behavior, not accidentally defaulted
    # away.
    BATCH_SIZE="$(jq -r '.hpc.batch_size // 10' "$CONFIG")"
    [[ "$BATCH_SIZE" =~ ^[0-9]+$ ]] || die "hpc.batch_size must be a non-negative integer, got: ${BATCH_SIZE}"

    # Optional FreeSurfer subregion segmentation follow-up (thalamus /
    # hippo-amygdala / brainstem, see submit_subregion_segmentation below).
    SUBREGION_ENABLED="$(jq -r '.subregion_segmentation.enabled // false' "$CONFIG")"
    mapfile -t SUBREGION_STRUCTURES < <(jq -r '.subregion_segmentation.structures[]? // empty' "$CONFIG")
    SUBREGION_MODE="$(jq -r '.subregion_segmentation.mode // "cross"' "$CONFIG")"
    mapfile -t SUBREGION_SESSIONS < <(jq -r '.subregion_segmentation.sessions[]? // empty' "$CONFIG")
}

# Sets INPUT_CLONE for dataset $1 (depends on resolve_config)
resolve_input_clone() {
    local ds="$1"
    if [[ -n "$INPUT_DIR_OVERRIDE" ]]; then
        INPUT_CLONE="$INPUT_DIR_OVERRIDE"
    else
        INPUT_CLONE="${SHARED_INPUT_BASE}/${ds}"
    fi
}

# Sets OUTPUT_CLONE and PUSH_LOCK_DIR for dataset $1 (depends on resolve_config)
resolve_output_clone() {
    local ds="$1"
    if [[ -n "$OUTPUT_DIR_OVERRIDE" ]]; then
        OUTPUT_CLONE="$OUTPUT_DIR_OVERRIDE"
        PUSH_LOCK_DIR="$(dirname "$OUTPUT_DIR_OVERRIDE")"
    else
        OUTPUT_CLONE="${SHARED_OUTPUT_BASE}/${ds}/${APP_OUT_DIR}"
        PUSH_LOCK_DIR="${SHARED_OUTPUT_BASE}/${ds}"
    fi
}

# Refuses to schedule work against a stale output clone. A local "derivatives"
# branch that's behind origin/derivatives (because an earlier run from
# somewhere else already pushed there since this clone last synced) will
# silently reprocess subjects whose derivatives already exist upstream --
# the array job burns hours of compute per subject, then the dependent
# finish job's `datalad push` fails with a non-fast-forward rejection,
# discovered only after everything already ran. Confirmed real incident:
# 069_BW01/qsiprep reprocessed all 23 subjects because its output clone
# hadn't been updated since a prior run pushed 17 days earlier.
# Fast-forwards automatically when that's all that's needed; a true
# divergence (this clone has its own unpushed commit too) needs a human,
# so it aborts instead of guessing which side to keep.
# Returns 1 (caller should skip the dataset) on divergence or fetch failure.
check_output_clone_fresh() {
    local ds="$1" output_clone="$2"
    [[ -d "${output_clone}/.datalad" ]] || return 0

    run git -C "$output_clone" fetch origin \
        || { warn "[$ds] Could not fetch origin to check output clone freshness"; return 1; }
    git -C "$output_clone" show-ref --verify --quiet refs/remotes/origin/derivatives || return 0

    local local_rev origin_rev
    local_rev=$(git -C "$output_clone" rev-parse derivatives 2>/dev/null) || return 0
    origin_rev=$(git -C "$output_clone" rev-parse origin/derivatives 2>/dev/null) || return 0
    [[ "$local_rev" == "$origin_rev" ]] && return 0

    if git -C "$output_clone" merge-base --is-ancestor "$local_rev" origin/derivatives; then
        log "[$ds] Output clone is behind origin/derivatives -- fast-forwarding..."
        run git -C "$output_clone" merge --ff-only origin/derivatives \
            || { warn "[$ds] Fast-forward update of output clone failed"; return 1; }
        return 0
    fi

    # Local is purely ahead (its own unpushed commits, e.g. a finish/recover
    # job still mid-push) with nothing new upstream -- not a real divergence,
    # since origin has no subjects this clone doesn't already know about.
    # Confirmed real false-positive: 082_KK02_Slackline flagged as
    # "DIVERGED" while a recover job was mid-push, purely ahead by 10
    # commits / 0 behind.
    if git -C "$output_clone" merge-base --is-ancestor origin/derivatives "$local_rev"; then
        return 0
    fi

    warn "[$ds] Output clone's derivatives branch has DIVERGED from origin/derivatives" \
         "-- refusing to submit (would reprocess subjects already pushed upstream and" \
         "fail to push its own results). Reconcile manually first: cd ${output_clone}"
    return 1
}

# Picks the currently least-loaded node in the given partition (by
# allocated/total CPU ratio), for finish jobs specifically -- these do
# latency-sensitive git-annex add/commit work (I/O-bound, not CPU-bound),
# and this cluster's default scheduler prefers packing small (1-cpu) jobs
# onto already-partially-allocated big nodes rather than spreading them to
# idle ones. Confirmed real incident: a finish job landed on a node with
# 209-210/288 CPUs allocated to unrelated large jobs (a 128-core
# simulation, a 64-core NetLogo run, etc.) and went from a healthy
# ~90-235s/subject to indefinite (300s+) timeouts on every subsequent
# subject, purely from losing shared filesystem I/O bandwidth to its
# neighbors -- moving the identical work to a genuinely idle node fixed it
# immediately. Echoes nothing and always exits 0: prints the chosen
# hostname on success, empty string if sinfo is unavailable/returns
# nothing usable, so a caller can safely do
# `#SBATCH --nodelist=${node}` only when non-empty and fall through to
# normal scheduling otherwise -- this must never block a finish job
# submission on sinfo being flaky.
pick_idle_node() {
    local partition="$1"
    sinfo -p "$partition" -N --noheader --format="%N %C" 2>/dev/null \
        | awk '{split($2,c,"/"); alloc=c[1]; total=c[4]; if (total+0>0) print alloc/total, $1}' \
        | sort -n \
        | head -1 \
        | awk '{print $2}'
}

# Submit a follow-up array+finish job pair that runs FreeSurfer's
# segment_subregions (thalamus / hippo-amygdala / brainstem subfields,
# https://surfer.nmr.mgh.harvard.edu/fswiki/SubregionSegmentation) against
# an already-completed recon-all output tree, once the main finish job for
# this dataset has safely committed+pushed. Purely post-processing: reads
# no BIDS input, writes new derivative files into subject/timepoint
# directories the main run already produced in output_clone. Only called
# when config .subregion_segmentation.enabled is true (see resolve_config).
submit_subregion_segmentation() {
    local ds="$1" output_clone="$2" main_finish_job_id="$3" commit_prefix="$4"

    [[ ${#SUBREGION_STRUCTURES[@]} -gt 0 ]] || { warn "[$ds] Subregion segmentation enabled but no structures selected -- skipping"; return 0; }

    local timepoint_list="${SUBJ_LISTS_DIR}/${ds}_subregion_timepoints_${SUBREGION_MODE}.txt"

    log "[$ds] Building subregion segmentation timepoint list (${SUBREGION_MODE})..."
    if [[ "$SUBREGION_MODE" == "cross" ]]; then
        # Cross-sectional timepoint dirs: sub-XXX_ses-YYY, excluding
        # longitudinal (.long.) output dirs.
        run bash -c "ls -d '${output_clone}'/sub-*_ses-*/ 2>/dev/null \
            | xargs -n1 basename \
            | grep -v '\.long\.' \
            | sort > '${timepoint_list}'"
        if [[ ${#SUBREGION_SESSIONS[@]} -gt 0 ]]; then
            local session_pattern
            session_pattern=$(printf '_ses-%s$|' "${SUBREGION_SESSIONS[@]}")
            session_pattern="${session_pattern%|}"
            run bash -c "grep -E '${session_pattern}' '${timepoint_list}' > '${timepoint_list}.filtered' && mv '${timepoint_list}.filtered' '${timepoint_list}'"
        fi
    else
        # Longitudinal base dirs: sub-XXX (no _ses- suffix, not a .long.
        # dir) that actually have a base-tps file -- confirms it's a real
        # FreeSurfer -base template, not e.g. a lone single-session
        # subject processed without the longitudinal steps. segment_subregions
        # --long-base processes every timepoint listed in base-tps in one
        # call, so there's no per-session filtering here (SUBREGION_SESSIONS
        # is ignored in this mode).
        run bash -c "for d in '${output_clone}'/sub-*/; do \
                b=\$(basename \"\$d\"); \
                case \"\$b\" in *_ses-*|*.long.*) continue ;; esac; \
                [[ -f \"\${d}base-tps\" ]] && echo \"\$b\"; \
            done | sort > '${timepoint_list}'"
    fi

    local n_timepoints
    n_timepoints=$(wc -l < "$timepoint_list" | tr -d ' ')
    if [[ "$n_timepoints" -eq 0 ]]; then
        warn "[$ds] No ${SUBREGION_MODE} timepoints found for subregion segmentation -- skipping"
        return 0
    fi

    # Split into batches of BATCH_SIZE, same knob/behavior as the main
    # recon-all array (see resolve_config) -- a large subregion run hits the
    # exact same slow-NFS-touch-file problem as the main array (confirmed
    # real incident: dataset 134's single unbatched 117-timepoint schedule
    # took hours just unlocking touch files before it could even sbatch),
    # and datalad-slurm's `-o .` conflict rule means later batches can't be
    # pre-declared up front either -- see schedule_one_batch's header comment
    # for why chaining from each batch's own finish job is required instead.
    if [[ "$BATCH_SIZE" -gt 0 && "$BATCH_SIZE" -lt "$n_timepoints" ]]; then
        local batch01_file
        batch01_file=$(_subregion_batch_timepoint_list "$ds" 1)
        if [[ -f "$batch01_file" ]] && $RESUME; then
            log "[$ds] Subregion batch timepoint lists exist, skipping re-split (--resume)"
        else
            log "[$ds] Splitting ${n_timepoints} subregion timepoints into batches of ${BATCH_SIZE}..."
            local batch_prefix="${SUBJ_LISTS_DIR}/${ds}_subregion_timepoints_${SUBREGION_MODE}_batch"
            bash -c "awk -v n='${BATCH_SIZE}' -v pre='${batch_prefix}' '{b=int((NR-1)/n)+1; fn=sprintf(\"%s%02d.txt\", pre, b); print > fn}' '${timepoint_list}'" \
                || { warn "[$ds] Failed to split subregion timepoint list into batches"; return 1; }
        fi

        local next_timepoint_list
        next_timepoint_list=$(_subregion_batch_timepoint_list "$ds" 2)
        [[ -f "$next_timepoint_list" ]] || next_timepoint_list=""

        schedule_one_subregion_batch "$ds" "$output_clone" "$batch01_file" "$main_finish_job_id" \
            "$commit_prefix" 1 "$next_timepoint_list" "$SUBREGION_SUBMISSION_LOG"
    else
        schedule_one_subregion_batch "$ds" "$output_clone" "$timepoint_list" "$main_finish_job_id" \
            "$commit_prefix" "" "" "$SUBREGION_SUBMISSION_LOG"
    fi
}

# Schedule one subregion segmentation array+concat+finish job trio for a
# batch of timepoints (or, when batch_idx is empty, every timepoint in one
# shot -- the pre-batching behavior). Mirrors schedule_one_batch's chaining
# pattern: once THIS batch's finish job actually commits+pushes (closing its
# DB entry), it invokes `submit_bids_cohort.sh _continue-subregion-batch` for
# the next batch, if any -- see schedule_one_batch's own header comment for
# why this can't just be pre-scheduled up front with SBATCH --dependency
# (the same datalad-slurm `-o .` open-job conflict applies here).
schedule_one_subregion_batch() {
    local ds="$1" output_clone="$2" timepoint_list="$3" main_finish_job_id="$4" \
          commit_prefix="$5" batch_idx="$6" next_timepoint_list="$7" submission_log="$8"

    local n_timepoints
    n_timepoints=$(wc -l < "$timepoint_list" | tr -d ' ')
    if [[ "$n_timepoints" -eq 0 ]]; then
        warn "[$ds] Subregion batch timepoint list is empty: ${timepoint_list}"
        return 1
    fi

    local batch_label="" job_name_suffix=""
    if [[ -n "$batch_idx" ]]; then
        batch_label=" (batch ${batch_idx})"
        job_name_suffix="_batch$(printf '%02d' "$batch_idx")"
    fi
    local has_next=false
    [[ -n "$next_timepoint_list" ]] && has_next=true

    log "[$ds] ${n_timepoints} ${SUBREGION_MODE} timepoint(s)${batch_label} for subregion segmentation: ${SUBREGION_STRUCTURES[*]}"

    local scripts_dir="$(dirname "$(realpath "$CONFIG")")/generated"
    local subregion_array_script="${scripts_dir}/${ds}_bids_subregions_${SUBREGION_MODE}${job_name_suffix}.sh"

    # main_finish_job_id is empty both when called from cmd_submit_subregions
    # (running against an already-finished cohort, no fresh main array to
    # wait on) and for every batch after the first (chained from the
    # previous batch's own finish job instead) -- in either case the array
    # can start right away.
    local dependency_desc="no dependency (runs immediately)"
    [[ -n "$main_finish_job_id" ]] && dependency_desc="depending on finish job ${main_finish_job_id}"

    if $DRY_RUN; then
        echo "[DRY-RUN] Generate+schedule subregion array${batch_label} (${SUBREGION_STRUCTURES[*]}, ${SUBREGION_MODE}) for ${n_timepoints} timepoint(s), ${dependency_desc}"
        echo "[DRY-RUN]   then sbatch a dependent finish job: datalad slurm-finish && datalad push --to origin"
        $has_next && echo "[DRY-RUN]   finish job would then chain subregion batch $((batch_idx + 1)) from ${next_timepoint_list}"
        return 0
    fi

    local -a dependency_args=()
    [[ -n "$main_finish_job_id" ]] && dependency_args=(--dependency "afterany:${main_finish_job_id}")

    run python3 "${SCRIPT_DIR}/hpc_datalad_runner.py" \
        --config "$CONFIG" \
        --subregion-mode \
        --dataset-id "$ds" \
        --timepoint-list "$timepoint_list" \
        --structures "${SUBREGION_STRUCTURES[@]}" \
        --seg-mode "$SUBREGION_MODE" \
        "${dependency_args[@]}" \
        --output "$subregion_array_script" \
        || { warn "[$ds] Subregion segmentation script generation failed${batch_label}"; return 1; }

    mkdir -p "${LOG_DIR_BASE}/${ds}" "${output_clone}/.slurm_logs/${ds}"

    local subregion_stdout subregion_exit subregion_job_id subregion_stderr_file
    subregion_stderr_file=$(mktemp)
    subregion_exit=0
    subregion_stdout=$(datalad -C "$output_clone" -f json slurm-schedule \
        -o . \
        -m "${commit_prefix}Subregion segmentation (${SUBREGION_STRUCTURES[*]}, ${SUBREGION_MODE}) for ${ds}${batch_label}" \
        sbatch "$subregion_array_script" 2>"$subregion_stderr_file") || subregion_exit=$?
    local subregion_stderr
    subregion_stderr=$(cat "$subregion_stderr_file"); rm -f "$subregion_stderr_file"
    if [[ $subregion_exit -ne 0 ]]; then
        local subregion_reason
        subregion_reason=$(printf '%s\n%s\n' "$subregion_stdout" "$subregion_stderr" \
            | jq -r 'select(.message) | .message' 2>/dev/null | tail -1)
        warn "[$ds] datalad slurm-schedule (subregions) failed${batch_label}${subregion_reason:+: $subregion_reason}"
        return 1
    fi

    subregion_job_id=$(printf '%s\n' "$subregion_stdout" \
        | jq -r 'select(.action=="slurm-schedule") | .slurm_run_info.slurm_job_id // empty' \
        | tail -1)
    if [[ -z "$subregion_job_id" ]]; then
        warn "[$ds] Could not determine SLURM job id from subregion slurm-schedule output${batch_label}"
        return 1
    fi
    log "[$ds] Scheduled subregion segmentation array job ${subregion_job_id}${batch_label} (${n_timepoints} timepoints, ${dependency_desc})"

    # Concatenate per-timepoint volume outputs into cohort-wide CSVs
    # (https://surfer.nmr.mgh.harvard.edu/fswiki/ConcatenateSubregionsResults)
    # once the array finishes -- reimplemented in concat_subregion_results.py
    # rather than calling FreeSurfer's own ConcatenateSubregionsResults.sh,
    # which expects <subject>/stats/<file>.stats with a header line;
    # segment_subregions (the tool this pipeline actually runs) writes plain
    # "label value" files with no header straight into <subject>/mri/, so
    # that script finds nothing against this tool's real output (confirmed
    # against the FS 8.2 image's own source). A plain (non-array) sbatch job,
    # not routed through its own `datalad slurm-schedule` -- the array job's
    # own "-o ." already covers the whole output_clone tree, so slurm-finish
    # below picks up these new results/ files the same way it already picks
    # up files array TASKS wrote (slurm-schedule declares scope, not who
    # writes within it). Writes into output_clone/subregion_results/, kept
    # in the dataset next to the raw per-subject output.
    local results_dir="${output_clone}/subregion_results"
    local concat_script="${scripts_dir}/${ds}_bids_subregions_${SUBREGION_MODE}${job_name_suffix}_concat.sh"
    cat > "$concat_script" <<EOF
#!/bin/bash
#SBATCH --job-name=concat_${ds}_subregions${job_name_suffix}
#SBATCH --dependency=afterany:${subregion_job_id}
#SBATCH --partition=$(cfg '.hpc.partition')
#SBATCH --time=00:30:00
#SBATCH --mem=2G
#SBATCH --cpus-per-task=1
#SBATCH --output=${LOG_DIR_BASE}/${ds}/concat-subregions-%j.out
#SBATCH --error=${LOG_DIR_BASE}/${ds}/concat-subregions-%j.err
set -euo pipefail
python3 "${SCRIPT_DIR}/concat_subregion_results.py" \\
    --subjects-dir "${output_clone}" \\
    --mode "${SUBREGION_MODE}" \\
    --structures ${SUBREGION_STRUCTURES[*]} \\
    --timepoint-list "${timepoint_list}" \\
    --results-dir "${results_dir}"
EOF
    chmod +x "$concat_script"

    local concat_job_id
    concat_job_id=$(sbatch "$concat_script" 2>&1 | grep -oP '\d+$') \
        || { warn "[$ds] Failed to submit dependent concat job${batch_label}"; return 1; }
    log "[$ds] Submitted subregion concat job ${concat_job_id}${batch_label} (runs after ${subregion_job_id} completes, writes to ${results_dir})"

    # Chain a finish job for the subregion output -- same pattern as the
    # main finish job in cmd_submit (one commit + push covering the whole
    # subregion array AND the concatenated results, once both complete).
    local module_load_line=""
    if [[ ${#HPC_MODULES[@]} -gt 0 ]]; then
        module_load_line="module load ${HPC_MODULES[*]}"
    fi
    local notify_email mail_lines=""
    notify_email="$(cfg '.hpc.notify_email // ""')"
    if [[ -n "$notify_email" ]]; then
        mail_lines="#SBATCH --mail-user=${notify_email}
#SBATCH --mail-type=END,FAIL"
    fi

    local finish_node finish_node_line=""
    finish_node="$(pick_idle_node "$(cfg '.hpc.partition')")"
    [[ -n "$finish_node" ]] && finish_node_line="#SBATCH --nodelist=${finish_node}"

    local continue_block=""
    if $has_next; then
        continue_block="
# This subregion batch's outputs are committed and pushed above -- its DB
# entry is now closed, so the next subregion batch's slurm-schedule can go
# ahead. Chained from here rather than pre-scheduled up front; see
# schedule_one_batch()'s header comment in submit_bids_cohort.sh for why.
bash \"${REPO_DIR}/scripts/submit_bids_cohort.sh\" _continue-subregion-batch \\
    --config \"${CONFIG}\" -d \"${ds}\" --batch-idx $((batch_idx + 1)) \\
    --submission-log \"${submission_log}\" --commit-prefix \"${commit_prefix}\""
    fi

    local subregion_finish_script="${scripts_dir}/${ds}_bids_subregions_${SUBREGION_MODE}${job_name_suffix}_finish.sh"
    cat > "$subregion_finish_script" <<EOF
#!/bin/bash
#SBATCH --job-name=finish_${ds}_subregions${job_name_suffix}
#SBATCH --dependency=afterany:${concat_job_id}
#SBATCH --partition=$(cfg '.hpc.partition')
${finish_node_line}
# 3h was not enough: job 5505212 (a 3-subject retry finish) exceeded it
# mid-\`datalad save\` with no output at all, leaving the array's outputs
# staged but uncommitted (see the datalad-slurm known-bug comment below).
#SBATCH --time=12:00:00
#SBATCH --mem=2G
# 4, not 1: incremental_datalad_save.sh below runs annex hashing with -J 4
# parallel jobs, so the finish job needs the cpus for that to actually help.
#SBATCH --cpus-per-task=4
#SBATCH --output=${LOG_DIR_BASE}/${ds}/finish-subregions-%j.out
#SBATCH --error=${LOG_DIR_BASE}/${ds}/finish-subregions-%j.err
${mail_lines}
set -euo pipefail
${module_load_line}
# See the matching comment on the main finish job template in cmd_submit
# for what this best-effort ntfy push covers and why it's non-fatal.
_notify_failure() {
    echo "subregion finish job for ${ds}${batch_label} failed at line \$LINENO" >&2
    "${REPO_DIR}/scripts/notify_ntfy.sh" "Cohort finish FAILED: ${ds} (subregions)" \
        "subregion finish job \$SLURM_JOB_ID for ${ds}${batch_label} (concat job ${concat_job_id}) failed at line \$LINENO on \$(hostname) -- see ${LOG_DIR_BASE}/${ds}/finish-subregions-\$SLURM_JOB_ID.err" \
        high x >/dev/null 2>&1 || true
}
trap _notify_failure ERR
export PATH="${REPO_DIR}/.datalad-slurm-venv/bin:\$PATH"
DATALAD_BIN="${REPO_DIR}/.datalad-slurm-venv/bin/datalad"
cd "${output_clone}"
# Commit + push per timepoint BEFORE slurm-finish -- see the matching
# comment on the main finish job template in cmd_submit for why the
# monolithic slurm-finish Save call is the failure mode this avoids.
"${REPO_DIR}/scripts/incremental_datalad_save.sh" -d "${output_clone}" -s "${timepoint_list}" -J 4 --push-every 10
# Guarantee slurm-finish's own Save call below always has something to
# commit -- see the matching comment on the main finish job template in
# cmd_submit for why an empty diff would otherwise silently lose this
# job's entire provenance record, not just skip a redundant save.
echo "${subregion_job_id} \$(date -Iseconds)" > ".slurm_logs/${ds}/finish-marker-${subregion_job_id}.txt"
# --commit-failed-jobs (not --close-failed-jobs): see the matching comment
# on the main finish job template in cmd_submit for why that distinction
# is what actually keeps a TIMEOUT'd concat job's real output from being
# silently abandoned as untracked.
"\$DATALAD_BIN" slurm-finish --commit-failed-jobs --slurm-job-id "${subregion_job_id}" -m "${commit_prefix}Finish subregion segmentation job ${subregion_job_id} for ${ds}${batch_label}"
# See the matching comment on the main finish job template in cmd_submit for
# why this push forces DATALAD_SSH_MULTIPLEX__CONNECTIONS=false (stale
# cross-node SSH control socket under the NFS-shared ~/.cache/datalad/sockets/).
DATALAD_SSH_MULTIPLEX__CONNECTIONS=false "\$DATALAD_BIN" push --to origin

$(uncommitted_check_block)

$(push_verification_block)
${continue_block}
EOF
    chmod +x "$subregion_finish_script"

    local subregion_finish_job_id
    subregion_finish_job_id=$(sbatch "$subregion_finish_script" 2>&1 | grep -oP '\d+$') \
        || { warn "[$ds] Failed to submit dependent subregion finish job${batch_label}"; return 1; }

    mkdir -p "$(dirname "$submission_log")"
    echo "${ds} ${subregion_job_id} ${n_timepoints} ${subregion_finish_job_id} ${batch_idx:--}" >> "$submission_log"
    log "[$ds] Submitted subregion finish job ${subregion_finish_job_id}${batch_label} (runs after ${concat_job_id} completes)"
}

# ── Phase 1: setup ────────────────────────────────────────────────────────────
cmd_setup() {
    check_todos
    resolve_config

    local mkdir_targets=("$SUBJ_LISTS_DIR")
    [[ -n "$SHARED_INPUT_BASE" ]] && mkdir_targets+=("$SHARED_INPUT_BASE")
    [[ -n "$SHARED_OUTPUT_BASE" ]] && mkdir_targets+=("$SHARED_OUTPUT_BASE")
    mkdir -p "${mkdir_targets[@]}"

    log "Setting up ${#DATASETS[@]} dataset(s)..."
    local failed=0

    for DS in "${DATASETS[@]}"; do
        local input_url="${INPUT_URL_TPL/\{dataset_id\}/$DS}"
        resolve_input_clone "$DS"
        local input_clone="$INPUT_CLONE"
        local output_url="${OUTPUT_URL_TPL/\{dataset_id\}/$DS}"
        resolve_output_clone "$DS"
        local output_clone="$OUTPUT_CLONE"

        log "[$DS] --- setup ---"

        # 1. Clone input dataset
        if [[ -d "${input_clone}/.datalad" ]]; then
            log "[$DS] Input already cloned at ${input_clone}"
        else
            log "[$DS] Cloning input from ${input_url}"
            run datalad clone "$input_url" "$input_clone" \
                || { warn "[$DS] Input clone failed – skipping"; failed=$((failed + 1)); continue; }
        fi

        # 2. Create output dataset on DataLad server (requires SSH access)
        #    The server-side path must exist; adjust the remote command for your setup.
        #    Example assumes 'datalad create' on server via SSH; skip if already done.
        #    NOTE: this creates a *plain* dataset, not a real RIA-store layout, so
        #    output_url_template must use ssh:// (plain git+annex over ssh), not
        #    ria+ssh:// -- the latter requires a proper RIA store and will fail
        #    with "RIA URI not recognized" against a plain `datalad create`.
        log "[$DS] Creating output dataset on DataLad server..."
        local server_path
        case "$output_url" in
            ssh://*)     server_path="${output_url#ssh://}" ;;
            ria+ssh://*) server_path="${output_url#ria+ssh://}" ;;
            *) die "[$DS] output_url_template must start with ssh:// (or ria+ssh:// for a real RIA store): $output_url" ;;
        esac
        local ssh_host="${server_path%%/*}"
        local remote_path="/${server_path#*/}"
        run ssh "$ssh_host" \
            "mkdir -p '${remote_path}' && \
             (test -d '${remote_path}/.datalad' || datalad create '${remote_path}')" \
            || warn "[$DS] Could not create output repo on server (may already exist)"

        # 2a. Tell the server-side repo to update its checked-out working
        # tree on push instead of rejecting it. Without this, a push is
        # rejected with "remote rejected (branch is currently checked out)"
        # any time someone has that branch checked out server-side -- which
        # happens routinely, since this repo doubles as a browsable working
        # copy people inspect directly (real incident 2026-07-29: exactly
        # this rejection silently blocked a finish job's push against
        # 129/freesurfer). `updateInstead` (git >=2.3) makes both safe at
        # once: the push succeeds AND the working tree it left checked out
        # updates to match, so anyone browsing it also always sees current
        # data. Idempotent (a plain `git config` overwrite), run
        # unconditionally like the `gc.auto 0` fix below so it also
        # retroactively covers datasets set up before this fix existed --
        # except this one lives server-side, so it needs its own ssh call.
        run ssh "$ssh_host" \
            "git -C '${remote_path}' config receive.denyCurrentBranch updateInstead" \
            || warn "[$DS] Could not set receive.denyCurrentBranch=updateInstead on server repo"

        # 2b. Register the output dataset as a subdataset of the INPUT dataset
        #    on the server, confined to a "derivatives" branch of the input
        #    dataset. The input dataset's default branch (whatever it was
        #    checked out to beforehand, typically master) is restored
        #    afterward, so a plain clone of the input dataset keeps showing
        #    raw BIDS data only -- the derivatives link is opt-in via
        #    `git checkout derivatives`. Idempotent: skips registration if
        #    .gitmodules already references this app's subdataset.
        local input_ssh_host="${input_url%%:*}"
        local input_remote_path="${input_url#*:}"
        run ssh "$input_ssh_host" bash -s -- "$input_remote_path" "$APP_NAME" <<'REMOTE_SCRIPT' \
            || warn "[$DS] Could not register ${APP_NAME} derivatives subdataset on input dataset (may already be registered)"
set -e
cd "$1"
app_name="$2"
default_branch=$(git symbolic-ref --short HEAD)
git checkout derivatives 2>/dev/null || git checkout -b derivatives
if ! grep -q "derivatives/${app_name}" .gitmodules 2>/dev/null; then
    git submodule add "./derivatives/${app_name}" "derivatives/${app_name}"
    datalad save -m "Register ${app_name} derivatives subdataset"
fi
git checkout "$default_branch"
REMOTE_SCRIPT

        # 3. Clone output to shared HPC location (for cheap per-job clones)
        if [[ -d "${output_clone}/.datalad" ]]; then
            log "[$DS] Output already cloned at ${output_clone}"
        else
            log "[$DS] Cloning output from ${output_url}"
            run mkdir -p "$(dirname "$output_clone")"
            run datalad clone "$output_url" "$output_clone" \
                || { warn "[$DS] Output clone failed – skipping"; failed=$((failed + 1)); continue; }
        fi

        # 3a. Disable git's automatic gc on this clone. A cohort's finish
        # job adds thousands of new small files in one batch (e.g. a
        # 150-subject run easily produces tens of thousands of new
        # git-annex objects) -- past git's default 6700-loose-object
        # threshold, `git gc --auto` fires on nearly every add/commit
        # during that batch, and each attempt repacks the *whole*,
        # ever-growing history. Confirmed real incident: a subregion-
        # segmentation finish job for a 150-subject FreeSurfer cohort was
        # on pace to take ~30 hours (vs. its 3-hour SBATCH limit) at
        # ~7 min/timepoint, 96% sustained CPU, entirely from this --
        # unrelated to data volume (only ~283MB of actual new content).
        # Idempotent and safe to re-run against an already-set clone; run
        # unconditionally (not just on first clone) so it also retroactively
        # covers datasets set up before this fix existed.
        run git -C "$output_clone" config gc.auto 0

        # 3b. Work on a local "derivatives" branch, not the remote's checked-out
        #    master. Every BIDS app run through this script pushes here so we
        #    never hit "remote rejected (branch is currently checked out)" --
        #    git only guards the branch that's actually checked out server-side,
        #    so a same-named local branch pushes cleanly with no special-casing.
        #    Merge derivatives -> master later, whenever convenient.
        if git -C "$output_clone" show-ref --verify --quiet refs/heads/derivatives; then
            run git -C "$output_clone" checkout derivatives
        else
            run git -C "$output_clone" checkout -b derivatives
        fi

        log "[$DS] Setup complete"
    done

    log "Setup finished. Failures: ${failed}/${#DATASETS[@]}"
    [[ $failed -eq 0 ]] || warn "Re-run with -d DATASET_ID for failed datasets"
}

# ── Phase 2: submit ───────────────────────────────────────────────────────────

# Prefetches (`datalad get`) only the subjects in $subj_list -- not the
# whole dataset -- since array tasks no longer call `datalad get`
# themselves (datalad-slurm keeps all git/annex operations outside the
# job). git-annex's own parallel transfer workers can transiently race on
# the same lock ("transfer already in progress, or unable to take transfer
# lock") under heavy concurrency across many subjects -- confirmed against
# a real 150-subject dataset: a second `datalad get` cleared 27 such
# errors with zero new failures. Retrying is safe/idempotent
# (already-fetched content just reports "notneeded"), so retry a few times
# before actually giving up -- a real, unrecoverable problem (bad URL,
# missing permissions, etc) will still fail all 3 attempts and surface the
# same way as before.
#
# Scoped by BIDS datatype (anat/func/dwi/...) whenever the app profile
# (scripts/app_profiles.py) declares one via required_datatypes -- e.g. a
# FreeSurfer/FastSurfer/CAT12 cohort only reads anat, so there's no reason
# to pull other subjects' dwi/func data onto shared scratch. Apps with no
# declared (or unrecognized) profile fall back to the previous
# whole-subject-directory behavior, since guessing wrong here means a real
# run failing on missing input.
prefetch_cohort_subjects() {
    local ds="$1" input_clone="$2" subj_list="$3" app_name="${4:-}"
    local -a subj_ids
    mapfile -t subj_ids < "$subj_list"
    if [[ ${#subj_ids[@]} -eq 0 ]]; then
        warn "[$ds] Subject list is empty -- nothing to prefetch"
        return 1
    fi

    local -a datatypes=()
    if [[ -n "$app_name" ]]; then
        mapfile -t datatypes < <(python3 "${SCRIPT_DIR}/app_profiles.py" --required-datatypes "$app_name" 2>/dev/null)
    fi

    local subj_targets=""
    local s
    for s in "${subj_ids[@]}"; do
        subj_targets+="'${s}/' "
    done

    local -a targets_arr=()
    if [[ ${#datatypes[@]} -eq 0 ]]; then
        log "[$ds] Prefetching ${#subj_ids[@]} subject(s) (no datatype restriction for app '${app_name:-unknown}')..."
        for s in "${subj_ids[@]}"; do
            targets_arr+=("${s}/")
        done
    else
        log "[$ds] Prefetching ${#subj_ids[@]} subject(s), scoped to datatype(s) ${datatypes[*]} (per '${app_name}' app profile)..."
        # The datatype subdirectories below don't exist on disk at all until
        # each subject's own subdataset is installed (this dataset is one
        # subdataset per subject) -- install (`-n`/--no-data) the subject
        # subdatasets first, without fetching any file content yet, so the
        # glob below (against the real filesystem, not a pattern string
        # handed to a nested shell) has a real directory tree to match.
        local install_ok=false
        for attempt in 1 2 3; do
            if run bash -c "cd '${input_clone}' && datalad get -n -r --recursion-limit 1 ${subj_targets} 2>/dev/null"; then
                install_ok=true
                break
            fi
            if [[ $attempt -lt 3 ]]; then
                warn "[$ds] Subdataset install attempt ${attempt}/3 had failures, retrying..."
                sleep 5
            fi
        done
        if ! $install_ok; then
            warn "[$ds] Subdataset install failed after 3 attempts"
            return 1
        fi

        # Resolve which datatype dirs actually exist right here (not inside
        # a nested `bash -c` string) so only real, existing paths are ever
        # handed to `datalad get` below -- covers both session-level
        # (sub-X/ses-Y/anat) and session-less (sub-X/anat) BIDS layouts.
        # This matters because `datalad get` given a nonexistent literal
        # path doesn't just skip it: it reports that one target
        # "impossible" and exits non-zero even though every other target
        # succeeded, which would make the retry loop below misreport a
        # fully-successful fetch as a failure.
        # `A && B`/`A || B` as a standalone statement aborts the whole
        # script under `set -e` whenever the left side is false (bash
        # treats the compound statement's own exit status as the trigger,
        # not just the last element of a pipeline) -- every conditional
        # below is an explicit if/then specifically to avoid that, since
        # "directory doesn't exist for this subject/datatype combo" and
        # "nullglob was already off" are both expected, non-error outcomes
        # here, not something that should ever abort submission.
        local dt match nullglob_was_off
        nullglob_was_off=1
        if shopt -q nullglob; then
            nullglob_was_off=0
        fi
        shopt -s nullglob
        for s in "${subj_ids[@]}"; do
            for dt in "${datatypes[@]}"; do
                for match in "${input_clone}/${s}"/*/"${dt}" "${input_clone}/${s}/${dt}"; do
                    if [[ -d "$match" ]]; then
                        targets_arr+=("${match#"${input_clone}"/}")
                    fi
                done
            done
        done
        if [[ $nullglob_was_off -eq 1 ]]; then
            shopt -u nullglob
        fi

        if [[ ${#targets_arr[@]} -eq 0 ]]; then
            warn "[$ds] No ${datatypes[*]} data found under these subjects -- nothing to fetch"
            return 1
        fi
    fi

    log "[$ds] Fetching content..."
    local prefetch_ok=false
    for attempt in 1 2 3; do
        # Each target is %q-escaped individually (rather than relying on the
        # naive single-quote wrapping used elsewhere in this file) since
        # targets_arr entries came from real filesystem paths, not
        # hand-built literals.
        if run bash -c "cd '${input_clone}' && datalad get $(printf '%q ' "${targets_arr[@]}") 2>/dev/null"; then
            prefetch_ok=true
            break
        fi
        if [[ $attempt -lt 3 ]]; then
            warn "[$ds] Prefetch attempt ${attempt}/3 had failures, retrying..."
            sleep 5
        fi
    done
    if ! $prefetch_ok; then
        warn "[$ds] Prefetch failed after 3 attempts"
        return 1
    fi
    return 0
}

# Path a given batch's subject-list file would live at, whether or not it's
# actually been created yet -- callers check -f themselves. batch_idx is
# 1-based, zero-padded to 2 digits (matches the awk splitter in cmd_submit).
_batch_subj_list() {
    local ds="$1" subj_list_suffix="$2" batch_idx="$3"
    echo "${SUBJ_LISTS_DIR}/${ds}_subjects${subj_list_suffix}_batch$(printf '%02d' "$batch_idx").txt"
}

# Same idea as _batch_subj_list, for subregion segmentation timepoint lists
# (see schedule_one_subregion_batch below).
_subregion_batch_timepoint_list() {
    local ds="$1" batch_idx="$2"
    echo "${SUBJ_LISTS_DIR}/${ds}_subregion_timepoints_${SUBREGION_MODE}_batch$(printf '%02d' "$batch_idx").txt"
}

# Schedule one array+finish job pair for a batch of subjects (or, when
# batch_idx is empty, today's single whole-cohort array -- everything below
# reduces to the pre-batching behavior byte-for-byte in that case, same
# filenames included).
#
# Batches are NOT pre-scheduled up front with SBATCH --dependency. Confirmed
# directly against datalad-slurm's own source
# (datalad_slurm/schedule.py::check_output_conflict): `datalad slurm-schedule
# -o .` refuses to declare new outputs against a dataset while *any* prior
# job's outputs are still open (not yet slurm-finish'd) -- SBATCH
# --dependency only delays when a job *runs* on the cluster, it does nothing
# to delay the slurm-schedule call itself, which happens synchronously at
# submit time. (This is also why submit_subregion_segmentation's own
# concurrent -o . schedule, fired right after the main array below, is
# explicitly best-effort/likely-to-conflict with a documented manual
# fallback -- same constraint, pre-dating this batching feature.)
#
# So instead this chains itself: once THIS batch's finish job actually
# commits (closing its DB entry), it invokes `submit_bids_cohort.sh
# _continue-batch` for the next one, if any -- see the "continue_block"
# below and cmd_continue_batch. That only ever runs from inside an
# already-`sbatch`-dispatched finish job, on a real compute node, so it
# never touches the HPC login-node policy.
#
# Sets _SCHEDULED_ARRAY_JOB_ID / _SCHEDULED_FINISH_JOB_ID on success.
# Returns 1 (with a [WARN]) on failure -- callers decide what that means:
# cmd_submit's loop counts one more failed dataset and continues to the
# next; cmd_continue_batch has no such fallback, so its failure propagates
# (no `||` guard) and fails the finish job that invoked it, surfacing via
# SLURM's own --mail-type=FAIL instead of the chain silently stalling.
schedule_one_batch() {
    local ds="$1" output_clone="$2" subj_list="$3" array_script="$4" \
          finish_script="$5" commit_prefix="$6" batch_idx="$7" \
          next_subj_list="$8" submission_log="$9"

    local n_subjects
    n_subjects=$(wc -l < "$subj_list" | tr -d ' ')
    if [[ "$n_subjects" -eq 0 ]]; then
        warn "[$ds] Batch subject list is empty: ${subj_list}"
        return 1
    fi

    local batch_label="" job_name_suffix=""
    if [[ -n "$batch_idx" ]]; then
        batch_label=" (batch ${batch_idx})"
        job_name_suffix="_batch$(printf '%02d' "$batch_idx")"
    fi
    local has_next=false
    [[ -n "$next_subj_list" ]] && has_next=true

    log "[$ds] ${n_subjects} subjects${batch_label}"

    if [[ -f "$array_script" ]] && $RESUME; then
        log "[$ds] Array script exists, skipping generation (--resume)"
    else
        log "[$ds] Generating array script${batch_label}..."
        run python3 "${SCRIPT_DIR}/hpc_datalad_runner.py" \
            --config "$CONFIG" \
            --array-mode \
            --dataset-id "$ds" \
            --subject-list "$subj_list" \
            --output "$array_script" \
            || { warn "[$ds] Script generation failed${batch_label}"; return 1; }
    fi

    if [[ ! -d "${output_clone}/.datalad" ]]; then
        warn "[$ds] Output not cloned at ${output_clone} – run setup first"
        return 1
    fi

    # Declare the whole dataset as output ("-o ." is an explicit, documented
    # special case in `datalad slurm-schedule --help`, not a forbidden
    # wildcard glob like "-o sub-*"). Per-subject -o flags only cover each
    # subject's own subdirectory -- BIDS apps commonly also write loose
    # report files at the dataset root, which per-subject flags would miss.
    local -a output_flags=(-o .)

    if $DRY_RUN; then
        echo "[DRY-RUN] (cd ${output_clone} && datalad -f json slurm-schedule ${output_flags[*]} -m '...' sbatch ${array_script})"
        echo "[DRY-RUN]   then sbatch a dependent finish job: datalad slurm-finish && datalad push --to origin"
        $has_next && echo "[DRY-RUN]   finish job would then chain batch $((batch_idx + 1)) from ${next_subj_list}"
        _SCHEDULED_ARRAY_JOB_ID="DRY-RUN"
        _SCHEDULED_FINISH_JOB_ID="DRY-RUN"
        return 0
    fi

    # Ensure log dir exists before slurm-schedule writes its env.json there,
    # and pre-create the array job's own SBATCH --output/--error location,
    # which lives *inside* the output dataset (see hpc_datalad_runner.py) so
    # datalad-slurm can save those logs as part of the job's provenance.
    mkdir -p "${LOG_DIR_BASE}/${ds}" "${output_clone}/.slurm_logs/${ds}"

    # stdout/stderr captured separately, and `|| schedule_exit=$?` directly
    # on the assignment -- both required for set -e safety and accurate
    # error reporting, see the matching comment historically kept on this
    # exact call in cmd_submit (now here).
    local schedule_stdout schedule_stderr schedule_exit job_id schedule_stderr_file
    schedule_stderr_file=$(mktemp)
    schedule_exit=0
    schedule_stdout=$(datalad -C "$output_clone" -f json slurm-schedule \
        "${output_flags[@]}" \
        -m "${commit_prefix}${APP_NAME} array for ${ds}${batch_label} (${n_subjects} subjects)" \
        sbatch "$array_script" 2>"$schedule_stderr_file") || schedule_exit=$?
    schedule_stderr=$(cat "$schedule_stderr_file"); rm -f "$schedule_stderr_file"
    if [[ $schedule_exit -ne 0 ]]; then
        local schedule_reason
        schedule_reason=$(printf '%s\n%s\n' "$schedule_stdout" "$schedule_stderr" \
            | jq -r 'select(.message) | .message' 2>/dev/null | tail -1)
        warn "[$ds] datalad slurm-schedule failed${batch_label}${schedule_reason:+: $schedule_reason}"
        return 1
    fi

    job_id=$(printf '%s\n' "$schedule_stdout" \
        | jq -r 'select(.action=="slurm-schedule") | .slurm_run_info.slurm_job_id // empty' \
        | tail -1)
    if [[ -z "$job_id" ]]; then
        warn "[$ds] Could not determine SLURM job id from slurm-schedule output${batch_label}"
        return 1
    fi
    log "[$ds] ${commit_prefix}Scheduled array job ${job_id}${batch_label} (${n_subjects} subjects)"

    local module_load_line=""
    if [[ ${#HPC_MODULES[@]} -gt 0 ]]; then
        module_load_line="module load ${HPC_MODULES[*]}"
    fi
    # Notify on the finish job only (not the per-subject array, which would
    # send one email per task).
    local notify_email
    notify_email="$(cfg '.hpc.notify_email // ""')"
    local mail_lines=""
    if [[ -n "$notify_email" ]]; then
        mail_lines="#SBATCH --mail-user=${notify_email}
#SBATCH --mail-type=END,FAIL"
    fi
    local finish_node finish_node_line=""
    finish_node="$(pick_idle_node "$(cfg '.hpc.partition')")"
    [[ -n "$finish_node" ]] && finish_node_line="#SBATCH --nodelist=${finish_node}"

    local continue_block=""
    if $has_next; then
        continue_block="
# This batch's outputs are committed and pushed above -- its DB entry is now
# closed, so the next batch's slurm-schedule can go ahead. Chained from here
# rather than pre-scheduled up front; see schedule_one_batch()'s own comment
# in submit_bids_cohort.sh for why.
bash \"${REPO_DIR}/scripts/submit_bids_cohort.sh\" _continue-batch \\
    --config \"${CONFIG}\" -d \"${ds}\" --batch-idx $((batch_idx + 1)) \\
    --submission-log \"${submission_log}\" --commit-prefix \"${commit_prefix}\""
    fi

    cat > "$finish_script" <<EOF
#!/bin/bash
#SBATCH --job-name=finish_${ds}${job_name_suffix}
#SBATCH --dependency=afterany:${job_id}
#SBATCH --partition=$(cfg '.hpc.partition')
${finish_node_line}
# 3h was not enough: job 5505212 (a 3-subject retry finish) exceeded it
# mid-\`datalad save\` with no output at all, leaving the array's outputs
# staged but uncommitted (see the datalad-slurm known-bug comment below).
#SBATCH --time=12:00:00
#SBATCH --mem=2G
# 4, not 1: incremental_datalad_save.sh below runs annex hashing with -J 4
# parallel jobs, so the finish job needs the cpus for that to actually help.
#SBATCH --cpus-per-task=4
#SBATCH --output=${LOG_DIR_BASE}/${ds}/finish-%j.out
#SBATCH --error=${LOG_DIR_BASE}/${ds}/finish-%j.err
${mail_lines}
set -euo pipefail
${module_load_line}
# Best-effort ntfy push on failure (see scripts/notify_ntfy.sh -- silently
# no-ops if configs/ntfy.conf isn't set up). Catches anything below that
# exits non-zero: slurm-finish itself, the uncommitted-check, or
# push_verification_block's own checks.
_notify_failure() {
    echo "finish job for ${ds}${batch_label} failed at line \$LINENO" >&2
    "${REPO_DIR}/scripts/notify_ntfy.sh" "Cohort finish FAILED: ${ds}" \
        "finish job \$SLURM_JOB_ID for ${ds}${batch_label} (array ${job_id}, ${n_subjects} subjects) failed at line \$LINENO on \$(hostname) -- see ${LOG_DIR_BASE}/${ds}/finish-\$SLURM_JOB_ID.err" \
        high x >/dev/null 2>&1 || true
}
trap _notify_failure ERR
# Use the dedicated datalad-slurm venv's own datalad entry point (pinned to
# a uv-managed portable Python 3.10) instead of .appsrunner -- compute nodes
# on this cluster can have a different system python3 than the login node
# (observed 3.12 vs 3.10), which silently breaks a venv that just symlinks
# to system python. Must call the venv's bin/datalad script directly (not
# \`python -m datalad\`, which uses a different, more limited entry point
# that doesn't recognize e.g. \`-f json\`). Also prepend its bin/ to PATH so
# datalad picks up the venv's git-annex, not the system/uv-tool one (which
# has the same node-dependent-python-version problem).
export PATH="${REPO_DIR}/.datalad-slurm-venv/bin:\$PATH"
DATALAD_BIN="${REPO_DIR}/.datalad-slurm-venv/bin/datalad"
cd "${output_clone}"
# --slurm-job-id must be explicit: datalad-slurm's finish_cmd(), when called
# without one, processes EVERY still-open job in the dataset's bookkeeping DB
# (datalad_slurm/finish.py's get_scheduled_commits()), not just this array.
# Confirmed real incident: job 5578842's finish swept in three stale open-job
# entries left over from an earlier unrelated pilot run and none of the four
# jobs it then tried to finish -- including this one -- ended up committed,
# tripping the uncommitted-check below. Scoping to this array's own job_id
# keeps a finish job's blast radius limited to the array it was dispatched for.
#
# Commit + push the array's real output per-subject BEFORE slurm-finish ever
# touches it. Every incident this codebase's comments document traces back
# to the same thing: slurm-finish's own Save call trying to commit the
# WHOLE dataset in one atomic step. That step is the single point of
# failure -- interrupted mid-save (job 5505212), tripped by
# --close-failed-jobs's early return, or (project 134, 2026-08-13) simply
# never running after a downstream step failed, leaving slurm-schedule's
# `-o .` unlock of the entire dataset with no matching re-lock for three
# weeks. incremental_datalad_save.sh commits per subject, checkpointed and
# resumable, so an interruption here costs at most one subject, not the
# cohort. By the time slurm-finish runs below, the tree is already clean,
# so its own Save call only has to do what it's actually good for: closing
# the datalad-slurm DB entry (keeping slurm-schedule's conflicting-outputs
# guard working).
"${REPO_DIR}/scripts/incremental_datalad_save.sh" -d "${output_clone}" -s "${subj_list}" -J 4 --push-every 10
#
# Guarantee slurm-finish's own Save call actually commits something.
# Verified against the installed datalad source
# (datalad/core/local/save.py:619-633): Save.__call__ on an already-clean
# tree (empty paths_by_ds) yields status='notneeded' and creates ZERO
# commits -- meaning the [DATALAD SLURM RUN] provenance record this whole
# call exists to write would silently never be created once the
# incremental save above has already cleaned the tree, which is now the
# common case. datalad_slurm's remove_from_database() (finish.py:561-572)
# does a hard DELETE with no archival, so that job's entire history would
# be lost from git, not merely a redundant save skipped. This marker file
# (inside the -o . output scope) guarantees a real, non-empty diff for
# Save to attach the provenance message to.
echo "${job_id} \$(date -Iseconds)" > ".slurm_logs/${ds}/finish-marker-${job_id}.txt"
#
# --commit-failed-jobs must be here too, not just on the GUI's manual
# close_open_jobs route: this finish job is dependency-chained with
# `afterany` (not `afterok`), so it runs even when some array elements
# TIMEOUT'd rather than COMPLETED. Without it, datalad_slurm's finish_cmd()
# removes the job's DB entry and returns without ever calling Save on the
# declared outputs -- any real output a timed-out element produced is then
# silently abandoned as untracked. Confirmed real incident (2026-09-01,
# megastudy_openneuro mriqc): see gui_cohort_routes.py's close_open_jobs
# docstring for the incident this same flag fixes on the manual-close path.
# The incremental save above already committed the real output, and the
# marker file above guarantees this call still has the provenance-record
# commit to make -- so this is now a cheap, small commit, not a repeat of
# the heavy lifting, but never a true no-op.
"\$DATALAD_BIN" slurm-finish --commit-failed-jobs --slurm-job-id "${job_id}" -m "${commit_prefix}Finish ${APP_NAME} array job ${job_id} for ${ds}${batch_label}"
# Disable datalad's SSH connection multiplexing for this push. Finish jobs
# land on whichever compute node the scheduler/pick_idle_node picks, but
# \$HOME (and its datalad control-socket cache under ~/.cache/datalad/sockets/)
# is NFS-shared across all of them -- a control socket a previous finish job
# created on a *different* node is a dead reference on this one, and
# connecting to it can hang indefinitely instead of failing fast (confirmed
# real incident: two separate pushes for the 129/freesurfer output dataset
# hung for hours at an identical near-zero byte count before this was found).
# DATALAD_SSH_MULTIPLEX__CONNECTIONS=false forces a plain, direct-per-call
# SSH connection instead, sidestepping the shared/stale-socket risk entirely.
DATALAD_SSH_MULTIPLEX__CONNECTIONS=false "\$DATALAD_BIN" push --to origin

$(uncommitted_check_block)

$(push_verification_block)

"${REPO_DIR}/scripts/notify_ntfy.sh" "Cohort finish OK: ${ds}" "${ds}${batch_label} committed + pushed: array job ${job_id}, ${n_subjects} subjects (commit \$(git rev-parse --short HEAD))." default white_check_mark >/dev/null 2>&1 || true
${continue_block}
EOF
    chmod +x "$finish_script"

    local finish_job_id
    finish_job_id=$(sbatch "$finish_script" 2>&1 | grep -oP '\d+$') \
        || { warn "[$ds] Failed to submit dependent finish job${batch_label}"; return 1; }

    mkdir -p "$(dirname "$submission_log")"
    echo "${ds} ${job_id} ${n_subjects} ${finish_job_id} ${batch_idx:--}" >> "$submission_log"
    log "[$ds] Submitted finish job ${finish_job_id}${batch_label} (runs after ${job_id} completes)"

    _SCHEDULED_ARRAY_JOB_ID="$job_id"
    _SCHEDULED_FINISH_JOB_ID="$finish_job_id"
    return 0
}

cmd_submit() {
    check_todos
    resolve_config

    local scripts_dir="$(dirname "$(realpath "$CONFIG")")/generated"
    # Pilot submissions log to a "pilot_"-prefixed file specifically so
    # `cmd_status`'s `submission_*.log` glob (which always picks the most
    # recent match) can never pick up a pilot run's tiny 1-subject log in
    # place of the real cohort's -- e.g. piloting a fix while a real array
    # job is still in progress must not make status checks blind to it.
    local submission_log_prefix="submission"
    $PILOT && submission_log_prefix="pilot_submission"
    local submission_log="${REPO_DIR}/logs/${submission_log_prefix}_$(date '+%Y%m%d_%H%M%S').log"
    mkdir -p "$scripts_dir" "$(dirname "$submission_log")"
    # Deliberately NOT prefixed "submission_*" -- cmd_status's log_glob
    # picks the most-recently-modified submission_*.log, and this file is
    # always written a few seconds after the main one in the same
    # cmd_submit run, so sharing that prefix would make it shadow the real
    # cohort's submission log there.
    SUBREGION_SUBMISSION_LOG="${REPO_DIR}/logs/subregions_${submission_log_prefix}_$(date '+%Y%m%d_%H%M%S').log"

    log "Submitting ${#DATASETS[@]} dataset(s)..."
    log "Submission log: ${submission_log}"

    local submitted=0 skipped=0 failed=0

    for DS in "${DATASETS[@]}"; do
        # Pilot mode gets its own _pilot-suffixed subject list/array/finish
        # scripts -- entirely separate from the real cohort's files, so a
        # pilot run can never clobber (or be skipped in favor of, under
        # --resume) the real subject list, and vice versa.
        local subj_list_suffix=""
        $PILOT && subj_list_suffix="_pilot"
        local subj_list="${SUBJ_LISTS_DIR}/${DS}_subjects${subj_list_suffix}.txt"
        local array_script="${scripts_dir}/${DS}_bids_array${subj_list_suffix}.sh"
        resolve_input_clone "$DS"
        local input_clone="$INPUT_CLONE"
        resolve_output_clone "$DS"
        local output_clone="$OUTPUT_CLONE"
        local finish_script="${scripts_dir}/${DS}_bids_finish${subj_list_suffix}.sh"

        log "[$DS] --- submit ---"
        $PILOT && log "[$DS] PILOT MODE: will submit only 1 randomly-chosen subject"

        check_output_clone_fresh "$DS" "$output_clone" \
            || { failed=$((failed + 1)); continue; }

        # Build subject list from pre-cloned dataset (reads BIDS directory names)
        if [[ -f "$subj_list" ]] && $RESUME; then
            log "[$DS] Subject list exists, skipping (--resume)"
        else
            if [[ ! -d "$input_clone" ]]; then
                warn "[$DS] Input not cloned at ${input_clone} – run setup first"
                failed=$((failed + 1)); continue
            fi
            if $PILOT; then
                log "[$DS] Picking 1 random subject for pilot..."
                run bash -c "ls -d ${input_clone}/sub-*/ 2>/dev/null \
                    | xargs -I{} basename {} \
                    | shuf -n 1 > ${subj_list}" \
                    || { warn "[$DS] Failed to build pilot subject list"; failed=$((failed + 1)); continue; }
            else
                log "[$DS] Building subject list..."
                run bash -c "ls -d ${input_clone}/sub-*/ 2>/dev/null \
                    | xargs -I{} basename {} \
                    | sort > ${subj_list}" \
                    || { warn "[$DS] Failed to build subject list"; failed=$((failed + 1)); continue; }
            fi
        fi

        local n_subjects
        n_subjects=$(wc -l < "$subj_list" | tr -d ' ')
        if [[ "$n_subjects" -eq 0 ]]; then
            warn "[$DS] Subject list is empty – skipping"
            failed=$((failed + 1)); continue
        fi
        log "[$DS] ${n_subjects} subjects$($PILOT && echo ' (PILOT)')"

        prefetch_cohort_subjects "$DS" "$input_clone" "$subj_list" "$APP_NAME" \
            || { failed=$((failed + 1)); continue; }

        local commit_prefix=""
        $PILOT && commit_prefix="[PILOT] "

        if [[ ! -d "${output_clone}/.datalad" ]]; then
            warn "[$DS] Output not cloned at ${output_clone} – run setup first"
            failed=$((failed + 1)); continue
        fi

        local _SCHEDULED_ARRAY_JOB_ID="" _SCHEDULED_FINISH_JOB_ID=""

        if [[ "$BATCH_SIZE" -gt 0 && "$BATCH_SIZE" -lt "$n_subjects" ]]; then
            # Cohort is bigger than one batch -- split it and schedule only
            # batch 1 here. Batch 2+ get scheduled later, from inside each
            # preceding batch's own finish job (see schedule_one_batch).
            local batch01_file
            batch01_file=$(_batch_subj_list "$DS" "$subj_list_suffix" 1)
            if [[ -f "$batch01_file" ]] && $RESUME; then
                log "[$DS] Batch subject lists exist, skipping re-split (--resume)"
            else
                log "[$DS] Splitting ${n_subjects} subjects into batches of ${BATCH_SIZE}..."
                # Deliberately NOT run()-wrapped/DRY_RUN-gated like the
                # scheduling steps below -- splitting a text file into
                # smaller text files touches no network/git/scheduler state
                # (same harmlessness class as the mkdir calls elsewhere in
                # this script), and schedule_one_batch always needs a real
                # batch01_file to read n_subjects from, dry-run or not. This
                # also makes --dry-run actually usable for previewing the
                # full batch shape without requiring a prior real split.
                local batch_prefix="${SUBJ_LISTS_DIR}/${DS}_subjects${subj_list_suffix}_batch"
                bash -c "awk -v n='${BATCH_SIZE}' -v pre='${batch_prefix}' '{b=int((NR-1)/n)+1; fn=sprintf(\"%s%02d.txt\", pre, b); print > fn}' '${subj_list}'" \
                    || { warn "[$DS] Failed to split subject list into batches"; failed=$((failed + 1)); continue; }
            fi

            local next_batch_file
            next_batch_file=$(_batch_subj_list "$DS" "$subj_list_suffix" 2)
            [[ -f "$next_batch_file" ]] || next_batch_file=""

            schedule_one_batch "$DS" "$output_clone" "$batch01_file" \
                "${scripts_dir}/${DS}_bids_array_batch01.sh" \
                "${scripts_dir}/${DS}_bids_finish_batch01.sh" \
                "$commit_prefix" 1 "$next_batch_file" "$submission_log" \
                || { failed=$((failed + 1)); continue; }
        else
            schedule_one_batch "$DS" "$output_clone" "$subj_list" "$array_script" "$finish_script" \
                "$commit_prefix" "" "" "$submission_log" \
                || { failed=$((failed + 1)); continue; }
        fi

        submitted=$((submitted + 1))

        if [[ "$SUBREGION_ENABLED" == "true" ]] && ! $PILOT; then
            submit_subregion_segmentation "$DS" "$output_clone" "$_SCHEDULED_FINISH_JOB_ID" "$commit_prefix" \
                || warn "[$DS] Subregion segmentation submission failed (main run was still submitted successfully)"
        fi
    done

    log ""
    log "Submitted: ${submitted}  Skipped: ${skipped}  Failed: ${failed}"
    $DRY_RUN || log "Job IDs written to: ${submission_log}"
}

# ── Phase 2b: submit-subregions ──────────────────────────────────────────────
# Runs subregion segmentation directly against an ALREADY-FINISHED output
# dataset -- no fresh recon-all array, no dependency to wait on. Use this
# (rather than re-running `submit`) to add subregion segmentation to a
# cohort that already completed: re-running `submit` would schedule a brand
# new full recon-all array for every subject again, which is both wasteful
# and unnecessary when the output is already there.
cmd_submit_subregions() {
    check_todos
    resolve_config

    [[ "$SUBREGION_ENABLED" == "true" ]] \
        || die "Config .subregion_segmentation.enabled is not true in ${CONFIG} -- nothing to submit."
    [[ ${#SUBREGION_STRUCTURES[@]} -gt 0 ]] \
        || die "Config .subregion_segmentation.structures is empty in ${CONFIG} -- nothing to submit."

    local scripts_dir="$(dirname "$(realpath "$CONFIG")")/generated"
    mkdir -p "$scripts_dir" "$SUBJ_LISTS_DIR"
    local submission_log_prefix="submission"
    $PILOT && submission_log_prefix="pilot_submission"
    SUBREGION_SUBMISSION_LOG="${REPO_DIR}/logs/subregions_${submission_log_prefix}_$(date '+%Y%m%d_%H%M%S').log"

    log "Submitting subregion segmentation (${SUBREGION_STRUCTURES[*]}, ${SUBREGION_MODE}) for ${#DATASETS[@]} dataset(s)..."
    log "Runs directly against each dataset's already-cloned output -- no recon-all re-run."

    local submitted=0 failed=0
    for DS in "${DATASETS[@]}"; do
        resolve_output_clone "$DS"
        local output_clone="$OUTPUT_CLONE"
        log "[$DS] --- submit-subregions ---"

        if [[ ! -d "${output_clone}/.datalad" ]]; then
            warn "[$DS] Output not cloned at ${output_clone} -- run setup first"
            failed=$((failed + 1)); continue
        fi
        check_output_clone_fresh "$DS" "$output_clone" \
            || { failed=$((failed + 1)); continue; }

        if submit_subregion_segmentation "$DS" "$output_clone" "" ""; then
            submitted=$((submitted + 1))
        else
            failed=$((failed + 1))
        fi
    done

    log ""
    log "Submitted: ${submitted}  Failed: ${failed}"
    $DRY_RUN || log "Job IDs written to: ${SUBREGION_SUBMISSION_LOG}"
}

# ── Phase 3: status ───────────────────────────────────────────────────────────
cmd_status() {
    resolve_config

    # Find most recent submission log -- pilot submissions log to a
    # separately-prefixed pilot_submission_*.log (see cmd_submit), so
    # --pilot here reads that instead of the real cohort's log; without
    # it, checking status right after only a pilot submit would silently
    # fall through to whatever unrelated dataset's real submission log
    # happens to be most recent.
    local log_glob="submission_"
    $PILOT && log_glob="pilot_submission_"
    local log_file
    log_file=$(ls -t "${REPO_DIR}/logs/${log_glob}"*.log 2>/dev/null | head -1) \
        || die "No $($PILOT && echo 'pilot ')submission log found in ${REPO_DIR}/logs/"

    log "Reading: ${log_file}"
    echo ""
    printf "%-20s %-6s %-12s %-10s %-10s %-40s %s\n" "DATASET" "BATCH" "JOB_ARRAY" "SUBJECTS" "PROGRESS" "ARRAY_STATUS" "FINISH_STATUS"
    printf "%-20s %-6s %-12s %-10s %-10s %-40s %s\n" "-------" "-----" "---------" "--------" "--------" "------------" "-------------"

    # One row per (dataset, batch) line actually present in the log --
    # batched cohorts append one line per batch as each is scheduled (the
    # later ones potentially hours/days after this file was created, from
    # inside a prior batch's finish job -- see schedule_one_batch), so a
    # batch not yet scheduled simply has no row yet, which is the correct
    # "what's been scheduled so far" view. batch_info is a 5th field added
    # alongside batching; unbatched lines just leave it empty/"-" and `read`
    # handles a short line fine as long as every named var is declared here.
    while read -r ds job_id n_subjects finish_job_id batch_info; do
        local status progress finish_status terminal_count
        [[ -z "$batch_info" ]] && batch_info="-"

        # squeue only shows currently-queued (PENDING/RUNNING) tasks -- once a
        # task finishes (success or failure) it drops out of squeue entirely,
        # so squeue alone is blind to COMPLETED/FAILED/OUT_OF_MEMORY subjects.
        # sacct keeps full historical accounting regardless of queue state.
        status=$(sacct -j "$job_id" --noheader --format=JobID,State --parsable2 2>/dev/null \
            | awk -F'|' -v jid="$job_id" '$1 ~ ("^" jid "_[0-9]+$") {print $2}' \
            | sort | uniq -c | awk '{printf "%s:%s ", $2, $1}' | sed 's/ $//')
        [[ -z "$status" ]] && status="UNKNOWN"

        terminal_count=$(sacct -j "$job_id" --noheader --format=JobID,State --parsable2 2>/dev/null \
            | awk -F'|' -v jid="$job_id" '$1 ~ ("^" jid "_[0-9]+$") && $2 !~ /PENDING|RUNNING/' \
            | wc -l)
        progress="${terminal_count}/${n_subjects}"

        finish_status="-"
        if [[ -n "$finish_job_id" ]]; then
            finish_status=$(sacct -j "$finish_job_id" --noheader --format=JobID,State --parsable2 2>/dev/null \
                | awk -F'|' -v jid="$finish_job_id" '$1 == jid {print $2}')
            [[ -z "$finish_status" ]] && finish_status="UNKNOWN"
        fi

        printf "%-20s %-6s %-12s %-10s %-10s %-40s %s\n" "$ds" "$batch_info" "$job_id" "$n_subjects" "$progress" "$status" "$finish_status"
    done < "$log_file"

    # Subregion segmentation follow-up jobs (see submit_subregion_segmentation),
    # if any were submitted -- same log format, different file/prefix. Unlike
    # the main submission log above, having none here is a normal, common
    # case (most cohorts never run subregion segmentation), not a die()-worthy
    # error -- but `ls` on a glob that matches nothing exits 2, and under
    # `set -euo pipefail` a bare `var=$(...)` assignment DOES abort the
    # script on that (real incident: this took cmd_status from printing a
    # perfectly good status table to reporting the whole command "failed
    # (exit 2)" for any cohort with no subregion job). `|| true` is the fix --
    # explicitly means "no match is fine, leave it empty", the same as the
    # `if` guard three lines down already assumed it would.
    local subregion_log_glob="subregions_submission_"
    $PILOT && subregion_log_glob="subregions_pilot_submission_"
    local subregion_log_file
    subregion_log_file=$(ls -t "${REPO_DIR}/logs/${subregion_log_glob}"*.log 2>/dev/null | head -1) || true
    if [[ -n "$subregion_log_file" ]]; then
        echo ""
        log "Reading: ${subregion_log_file}"
        echo ""
        printf "%-20s %-6s %-14s %-10s %-10s %-40s %s\n" "DATASET" "BATCH" "SUBREGION_JOB" "TIMEPTS" "PROGRESS" "ARRAY_STATUS" "FINISH_STATUS"
        printf "%-20s %-6s %-14s %-10s %-10s %-40s %s\n" "-------" "-----" "-------------" "-------" "--------" "------------" "-------------"
        while read -r ds job_id n_timepoints finish_job_id batch_info; do
            local status progress finish_status terminal_count
            [[ -z "$batch_info" ]] && batch_info="-"
            status=$(sacct -j "$job_id" --noheader --format=JobID,State --parsable2 2>/dev/null \
                | awk -F'|' -v jid="$job_id" '$1 ~ ("^" jid "_[0-9]+$") {print $2}' \
                | sort | uniq -c | awk '{printf "%s:%s ", $2, $1}' | sed 's/ $//')
            [[ -z "$status" ]] && status="UNKNOWN"

            terminal_count=$(sacct -j "$job_id" --noheader --format=JobID,State --parsable2 2>/dev/null \
                | awk -F'|' -v jid="$job_id" '$1 ~ ("^" jid "_[0-9]+$") && $2 !~ /PENDING|RUNNING/' \
                | wc -l)
            progress="${terminal_count}/${n_timepoints}"

            finish_status="-"
            if [[ -n "$finish_job_id" ]]; then
                finish_status=$(sacct -j "$finish_job_id" --noheader --format=JobID,State --parsable2 2>/dev/null \
                    | awk -F'|' -v jid="$finish_job_id" '$1 == jid {print $2}')
                [[ -z "$finish_status" ]] && finish_status="UNKNOWN"
            fi

            printf "%-20s %-6s %-14s %-10s %-10s %-40s %s\n" "$ds" "$batch_info" "$job_id" "$n_timepoints" "$progress" "$status" "$finish_status"
        done < "$subregion_log_file"
    fi
}

# ── Help ──────────────────────────────────────────────────────────────────────
# ── Internal: continue a batched cohort's chain ────────────────────────────────
# Not a user-facing command (undocumented in cmd_help) -- invoked only by a
# batch's own finish job once it has committed+pushed, to schedule the next
# batch. See schedule_one_batch's header comment for why this can't just be
# pre-scheduled up front with SBATCH --dependency.
cmd_continue_batch() {
    [[ -n "$CONTINUE_BATCH_IDX" ]] || die "_continue-batch requires --batch-idx"
    [[ -n "$CONTINUE_SUBMISSION_LOG" ]] || die "_continue-batch requires --submission-log"
    [[ ${#FILTER_DATASETS[@]} -eq 1 ]] || die "_continue-batch requires exactly one -d DATASET_ID"

    resolve_config
    local ds="${DATASETS[0]}"
    local scripts_dir="$(dirname "$(realpath "$CONFIG")")/generated"
    resolve_output_clone "$ds"
    local output_clone="$OUTPUT_CLONE"

    local subj_list
    subj_list=$(_batch_subj_list "$ds" "" "$CONTINUE_BATCH_IDX")
    [[ -f "$subj_list" ]] || die "[$ds] Batch ${CONTINUE_BATCH_IDX} subject list not found: ${subj_list}"

    local padded_idx
    padded_idx="$(printf '%02d' "$CONTINUE_BATCH_IDX")"
    local array_script="${scripts_dir}/${ds}_bids_array_batch${padded_idx}.sh"
    local finish_script="${scripts_dir}/${ds}_bids_finish_batch${padded_idx}.sh"

    local next_subj_list
    next_subj_list=$(_batch_subj_list "$ds" "" $((CONTINUE_BATCH_IDX + 1)))
    [[ -f "$next_subj_list" ]] || next_subj_list=""

    local _SCHEDULED_ARRAY_JOB_ID="" _SCHEDULED_FINISH_JOB_ID=""
    schedule_one_batch "$ds" "$output_clone" "$subj_list" "$array_script" "$finish_script" \
        "$CONTINUE_COMMIT_PREFIX" "$CONTINUE_BATCH_IDX" "$next_subj_list" "$CONTINUE_SUBMISSION_LOG"
}

# ── Internal: continue a batched subregion segmentation chain ─────────────────
# Not a user-facing command (undocumented in cmd_help) -- invoked only by a
# subregion batch's own finish job once it has committed+pushed, to schedule
# the next subregion batch. See schedule_one_batch's header comment for why
# this can't just be pre-scheduled up front with SBATCH --dependency.
cmd_continue_subregion_batch() {
    [[ -n "$CONTINUE_BATCH_IDX" ]] || die "_continue-subregion-batch requires --batch-idx"
    [[ -n "$CONTINUE_SUBMISSION_LOG" ]] || die "_continue-subregion-batch requires --submission-log"
    [[ ${#FILTER_DATASETS[@]} -eq 1 ]] || die "_continue-subregion-batch requires exactly one -d DATASET_ID"

    resolve_config
    local ds="${DATASETS[0]}"
    resolve_output_clone "$ds"
    local output_clone="$OUTPUT_CLONE"

    local timepoint_list
    timepoint_list=$(_subregion_batch_timepoint_list "$ds" "$CONTINUE_BATCH_IDX")
    [[ -f "$timepoint_list" ]] || die "[$ds] Subregion batch ${CONTINUE_BATCH_IDX} timepoint list not found: ${timepoint_list}"

    local next_timepoint_list
    next_timepoint_list=$(_subregion_batch_timepoint_list "$ds" $((CONTINUE_BATCH_IDX + 1)))
    [[ -f "$next_timepoint_list" ]] || next_timepoint_list=""

    schedule_one_subregion_batch "$ds" "$output_clone" "$timepoint_list" "" \
        "$CONTINUE_COMMIT_PREFIX" "$CONTINUE_BATCH_IDX" "$next_timepoint_list" "$CONTINUE_SUBMISSION_LOG"
}

cmd_help() {
    sed -n '2,/^# Edit/p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
case "$COMMAND" in
    setup)             cmd_setup             ;;
    submit)            cmd_submit            ;;
    submit-subregions) cmd_submit_subregions ;;
    status)            cmd_status            ;;
    _continue-batch)   cmd_continue_batch    ;;
    _continue-subregion-batch) cmd_continue_subregion_batch ;;
    help|-h|--help)    cmd_help              ;;
    *) die "Unknown command: $COMMAND  (use setup | submit | submit-subregions | status)" ;;
esac
