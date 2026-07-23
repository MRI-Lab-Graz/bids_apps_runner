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
#   • Pre-clones every dataset to shared HPC storage (fast subsequent clones)
#   • Creates the per-dataset output DataLad repos on the DataLad SSH server
#   • Prefetches all subject data (`datalad get`) so array tasks never call it
#
# Phase 2 – submit (run after setup, submits SLURM array jobs)
#   • Builds subject lists from pre-cloned datasets
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
#   ./scripts/submit_bids_cohort.sh setup   [OPTIONS]
#   ./scripts/submit_bids_cohort.sh submit  [OPTIONS]
#   ./scripts/submit_bids_cohort.sh status
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
        -h|--help)        COMMAND=help;           shift ;;
        *) die "Unknown option: $1" ;;
    esac
done

# ── Resolve config values ─────────────────────────────────────────────────────
resolve_config() {
    require_jq
    [[ -f "$CONFIG" ]] || die "Config not found: $CONFIG"

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

    # Build dataset list (filtered if -d was given)
    mapfile -t ALL_DATASETS < <(jq -r '.datasets[]' "$CONFIG")
    if [[ ${#FILTER_DATASETS[@]} -gt 0 ]]; then
        DATASETS=("${FILTER_DATASETS[@]}")
    else
        DATASETS=("${ALL_DATASETS[@]}")
    fi

    APP_OUT_DIR="$(jq -r '.bids_app.output_dir_name // .bids_app.app_name // "output"' "$CONFIG")"
    APP_NAME="$(jq -r '.bids_app.app_name // "bids_app"' "$CONFIG")"
    LOG_DIR_BASE="$(cfg '.paths.log_dir')"
    mapfile -t HPC_MODULES < <(jq -r '.hpc.modules[]? // empty' "$CONFIG")
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

    warn "[$ds] Output clone's derivatives branch has DIVERGED from origin/derivatives" \
         "-- refusing to submit (would reprocess subjects already pushed upstream and" \
         "fail to push its own results). Reconcile manually first: cd ${output_clone}"
    return 1
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

        # 4. Prefetch all subject data now, since array tasks no longer call
        #    `datalad get` themselves (datalad-slurm keeps all git/annex
        #    operations outside the job). git-annex's own parallel transfer
        #    workers can transiently race on the same lock ("transfer
        #    already in progress, or unable to take transfer lock") under
        #    heavy concurrency across many subjects -- confirmed against a
        #    real 150-subject dataset: a second `datalad get` cleared 27
        #    such errors with zero new failures. Retrying is safe/idempotent
        #    (already-fetched content just reports "notneeded"), so retry a
        #    few times before actually giving up -- a real, unrecoverable
        #    problem (bad URL, missing permissions, etc) will still fail all
        #    3 attempts and surface the same way as before.
        log "[$DS] Prefetching subject data..."
        local prefetch_ok=false
        for attempt in 1 2 3; do
            if run bash -c "cd '${input_clone}' && datalad get sub-*/ 2>/dev/null"; then
                prefetch_ok=true
                break
            fi
            if [[ $attempt -lt 3 ]]; then
                warn "[$DS] Prefetch attempt ${attempt}/3 had failures, retrying..."
                sleep 5
            fi
        done
        $prefetch_ok || warn "[$DS] Prefetch failed after 3 attempts (or found no sub-* dirs)"

        log "[$DS] Setup complete"
    done

    log "Setup finished. Failures: ${failed}/${#DATASETS[@]}"
    [[ $failed -eq 0 ]] || warn "Re-run with -d DATASET_ID for failed datasets"
}

# ── Phase 2: submit ───────────────────────────────────────────────────────────
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

        local commit_prefix=""
        $PILOT && commit_prefix="[PILOT] "

        # Generate SLURM array script
        if [[ -f "$array_script" ]] && $RESUME; then
            log "[$DS] Array script exists, skipping generation (--resume)"
        else
            log "[$DS] Generating array script..."
            run python3 "${SCRIPT_DIR}/hpc_datalad_runner.py" \
                --config "$CONFIG" \
                --array-mode \
                --dataset-id "$DS" \
                --subject-list "$subj_list" \
                --output "$array_script" \
                || { warn "[$DS] Script generation failed"; failed=$((failed + 1)); continue; }
        fi

        if [[ ! -d "${output_clone}/.datalad" ]]; then
            warn "[$DS] Output not cloned at ${output_clone} – run setup first"
            failed=$((failed + 1)); continue
        fi

        # Declare the whole dataset as output ("-o ." is an explicit,
        # documented special case in `datalad slurm-schedule --help`, not a
        # forbidden wildcard glob like "-o sub-*"). Per-subject -o flags
        # (-o sub-01 -o sub-02 ...) only cover each subject's own
        # subdirectory -- BIDS apps commonly also write loose report files
        # at the dataset root (e.g. MRIQC's sub-01_ses-1_T1w.html), which
        # per-subject flags silently miss, leaving real output uncommitted
        # after slurm-finish. "-o ." covers any app's output layout.
        local -a output_flags=(-o .)

        if $DRY_RUN; then
            echo "[DRY-RUN] (cd ${output_clone} && datalad -f json slurm-schedule ${output_flags[*]} -m '...' sbatch ${array_script})"
            echo "[DRY-RUN]   then sbatch a dependent finish job: datalad slurm-finish && datalad push --to origin"
            submitted=$((submitted + 1))
            continue
        fi

        # Ensure log dir exists before slurm-schedule writes its env.json there.
        mkdir -p "${LOG_DIR_BASE}/${DS}"

        # Also pre-create the array job's own SBATCH --output/--error location,
        # which now lives *inside* the output dataset (see hpc_datalad_runner.py)
        # so datalad-slurm can save those logs as part of the job's provenance
        # instead of erroring on paths outside the dataset.
        mkdir -p "${output_clone}/.slurm_logs/${DS}"

        # Schedule the array job. Nothing inside the job touches git --
        # slurm-schedule declares/prepares the per-subject outputs and
        # submits via sbatch itself; we parse the SLURM job id back out of
        # its JSON result.
        #
        # stdout and stderr are captured SEPARATELY (not 2>&1-merged) --
        # confirmed real regression: merging them so the failure branch
        # below could report datalad-slurm's own reason also fed stderr
        # chatter into the job_id jq parse on the *success* path. A 1-subject
        # pilot's schedule call apparently produces little/no stderr, so this
        # went unnoticed there; declaring outputs for a 150-subject array
        # produces enough non-JSON stderr text that jq's parse of the merged
        # stream broke ("Invalid numeric literal...") even though the
        # schedule itself (and the real sbatch submission behind it)
        # succeeded -- silently skipping the dependent finish-job submission
        # for an already-running array job.
        local schedule_stdout schedule_stderr schedule_exit job_id
        local schedule_stderr_file
        schedule_stderr_file=$(mktemp)
        # `|| schedule_exit=$?` directly on the assignment (not a separate
        # `schedule_exit=$?` statement afterward) is required for this to be
        # set -e safe: a bare `var=$(cmd)` with no attached `||`/`if` is a
        # simple command in its own right, and set -e aborts the whole
        # script on its failure immediately -- before a following statement
        # ever runs. Confirmed real regression: a genuine slurm-schedule
        # failure (conflicting outputs from an unfinished prior job) killed
        # the entire submit run silently, with no [WARN] and no per-dataset
        # Submitted/Skipped/Failed summary at all.
        schedule_exit=0
        schedule_stdout=$(datalad -C "$output_clone" -f json slurm-schedule \
            "${output_flags[@]}" \
            -m "${commit_prefix}${APP_NAME} array for ${DS} (${n_subjects} subjects)" \
            sbatch "$array_script" 2>"$schedule_stderr_file") || schedule_exit=$?
        schedule_stderr=$(cat "$schedule_stderr_file"); rm -f "$schedule_stderr_file"
        if [[ $schedule_exit -ne 0 ]]; then
            # Surface datalad-slurm's own reason (e.g. "There are
            # conflicting outputs with previously scheduled jobs..." when
            # an earlier job for this dataset hasn't been finished yet)
            # instead of a bare "failed" -- confirmed real gap: this was
            # previously swallowed entirely, requiring manual reproduction
            # to find out why. Checks both streams since the JSON error
            # record could land on either.
            local schedule_reason
            schedule_reason=$(printf '%s\n%s\n' "$schedule_stdout" "$schedule_stderr" \
                | jq -r 'select(.message) | .message' 2>/dev/null | tail -1)
            warn "[$DS] datalad slurm-schedule failed${schedule_reason:+: $schedule_reason}"
            failed=$((failed + 1)); continue
        fi

        job_id=$(printf '%s\n' "$schedule_stdout" \
            | jq -r 'select(.action=="slurm-schedule") | .slurm_run_info.slurm_job_id // empty' \
            | tail -1)
        if [[ -z "$job_id" ]]; then
            warn "[$DS] Could not determine SLURM job id from slurm-schedule output"
            failed=$((failed + 1)); continue
        fi
        log "[$DS] ${commit_prefix}Scheduled array job ${job_id} (${n_subjects} subjects)"

        # Chain a finish job: once the whole array completes, this is the
        # only step that touches git -- one `datalad slurm-finish` commit
        # covering every subject's output, then a single push.
        local module_load_line=""
        if [[ ${#HPC_MODULES[@]} -gt 0 ]]; then
            module_load_line="module load ${HPC_MODULES[*]}"
        fi
        # Notify on the finish job only (not the per-subject array, which
        # would send one email per task) -- the finish job only runs once
        # the whole array has settled (--dependency=afterany), so its own
        # completion is the single "the cohort submission is done" signal.
        local notify_email
        notify_email="$(cfg '.hpc.notify_email // ""')"
        local mail_lines=""
        if [[ -n "$notify_email" ]]; then
            mail_lines="#SBATCH --mail-user=${notify_email}
#SBATCH --mail-type=END,FAIL"
        fi
        cat > "$finish_script" <<EOF
#!/bin/bash
#SBATCH --job-name=finish_${DS}${subj_list_suffix}
#SBATCH --dependency=afterany:${job_id}
#SBATCH --partition=$(cfg '.hpc.partition')
#SBATCH --time=00:30:00
#SBATCH --mem=2G
#SBATCH --cpus-per-task=1
#SBATCH --output=${LOG_DIR_BASE}/${DS}/finish-%j.out
#SBATCH --error=${LOG_DIR_BASE}/${DS}/finish-%j.err
${mail_lines}
set -euo pipefail
${module_load_line}
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
"\$DATALAD_BIN" slurm-finish -m "${commit_prefix}Finish ${APP_NAME} array job ${job_id} for ${DS}"
"\$DATALAD_BIN" push --to origin
EOF
        chmod +x "$finish_script"

        local finish_job_id
        finish_job_id=$(sbatch "$finish_script" 2>&1 | grep -oP '\d+$') \
            || { warn "[$DS] Failed to submit dependent finish job"; failed=$((failed + 1)); continue; }

        echo "${DS} ${job_id} ${n_subjects} ${finish_job_id}" >> "$submission_log"
        log "[$DS] Submitted finish job ${finish_job_id} (runs after ${job_id} completes)"
        submitted=$((submitted + 1))
    done

    log ""
    log "Submitted: ${submitted}  Skipped: ${skipped}  Failed: ${failed}"
    $DRY_RUN || log "Job IDs written to: ${submission_log}"
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
    printf "%-20s %-12s %-10s %-10s %-40s %s\n" "DATASET" "JOB_ARRAY" "SUBJECTS" "PROGRESS" "ARRAY_STATUS" "FINISH_STATUS"
    printf "%-20s %-12s %-10s %-10s %-40s %s\n" "-------" "---------" "--------" "--------" "------------" "-------------"

    while read -r ds job_id n_subjects finish_job_id; do
        local status progress finish_status terminal_count

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

        printf "%-20s %-12s %-10s %-10s %-40s %s\n" "$ds" "$job_id" "$n_subjects" "$progress" "$status" "$finish_status"
    done < "$log_file"
}

# ── Help ──────────────────────────────────────────────────────────────────────
cmd_help() {
    sed -n '2,/^# Edit/p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
case "$COMMAND" in
    setup)  cmd_setup  ;;
    submit) cmd_submit ;;
    status) cmd_status ;;
    help|-h|--help) cmd_help ;;
    *) die "Unknown command: $COMMAND  (use setup | submit | status)" ;;
esac
