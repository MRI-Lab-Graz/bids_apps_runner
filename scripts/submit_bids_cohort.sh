#!/usr/bin/env bash
# submit_bids_cohort.sh
#
# Orchestrate any BIDS app across multiple datasets on a SLURM/DataLad HPC.
# Works with fMRIPrep, QSIPrep, MRIQC, or any other BIDS-app container.
#
# Two-phase workflow
# ──────────────────
# Phase 1 – setup (run once, needs DataLad + network access)
#   • Pre-clones every dataset to shared HPC storage (fast subsequent clones)
#   • Creates the per-dataset output DataLad repos on the DataLad SSH server
#
# Phase 2 – submit (run after setup, submits SLURM array jobs)
#   • Builds subject lists from pre-cloned datasets
#   • Generates a SLURM array job script per dataset (via hpc_datalad_runner.py)
#   • Submits each array job; records job IDs in submission.log
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
#
# Prerequisites
# ─────────────
#   • jq       (JSON parsing)
#   • python3  (hpc_datalad_runner.py)
#   • datalad  (available via module or PATH)
#   • sbatch   (SLURM, only needed for submit phase)
#   • ssh access to the DataLad server (only needed for setup phase)
#
# Edit the TODO values in your config JSON before running.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

# ── Defaults ──────────────────────────────────────────────────────────────────
CONFIG="${REPO_DIR}/configs/cohort_hpc_example.json"
DRY_RUN=false
RESUME=false
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
        -h|--help)        COMMAND=help;           shift ;;
        *) die "Unknown option: $1" ;;
    esac
done

# ── Resolve config values ─────────────────────────────────────────────────────
resolve_config() {
    require_jq
    [[ -f "$CONFIG" ]] || die "Config not found: $CONFIG"

    SHARED_INPUT_BASE="$(cfg '.paths.shared_input_base')"
    SHARED_OUTPUT_BASE="$(cfg '.paths.shared_output_base')"
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
}

# ── Phase 1: setup ────────────────────────────────────────────────────────────
cmd_setup() {
    check_todos
    resolve_config

    mkdir -p "$SHARED_INPUT_BASE" "$SHARED_OUTPUT_BASE" "$SUBJ_LISTS_DIR"

    log "Setting up ${#DATASETS[@]} dataset(s)..."
    local failed=0

    for DS in "${DATASETS[@]}"; do
        local input_url="${INPUT_URL_TPL/\{dataset_id\}/$DS}"
        local input_clone="${SHARED_INPUT_BASE}/${DS}"
        local output_url="${OUTPUT_URL_TPL/\{dataset_id\}/$DS}"
        # Derive output_dir_name from bids_app config (default: same as app_name or "output")
        local app_out_dir
        app_out_dir="$(jq -r '.bids_app.output_dir_name // .bids_app.app_name // "output"' "$CONFIG")"
        local output_clone="${SHARED_OUTPUT_BASE}/${DS}/${app_out_dir}"
        local push_lock_dir="${SHARED_OUTPUT_BASE}/${DS}"

        log "[$DS] --- setup ---"

        # 1. Clone input dataset
        if [[ -d "${input_clone}/.datalad" ]]; then
            log "[$DS] Input already cloned at ${input_clone}"
        else
            log "[$DS] Cloning input from ${input_url}"
            run datalad clone "$input_url" "$input_clone" \
                || { warn "[$DS] Input clone failed – skipping"; ((failed++)); continue; }
        fi

        # 2. Create output dataset on DataLad server (requires SSH access)
        #    The server-side path must exist; adjust the remote command for your setup.
        #    Example assumes 'datalad create' on server via SSH; skip if already done.
        log "[$DS] Creating output dataset on DataLad server..."
        local server_path="${output_url#ria+ssh://}"   # strip ria+ssh://
        local ssh_host="${server_path%%/*}"
        local remote_path="/${server_path#*/}"
        run ssh "$ssh_host" \
            "mkdir -p '${remote_path}' && \
             (test -d '${remote_path}/.datalad' || datalad create '${remote_path}')" \
            || warn "[$DS] Could not create output repo on server (may already exist)"

        # 3. Clone output to shared HPC location (for cheap per-job clones)
        if [[ -d "${output_clone}/.datalad" ]]; then
            log "[$DS] Output already cloned at ${output_clone}"
        else
            log "[$DS] Cloning output from ${output_url}"
            run mkdir -p "$(dirname "$output_clone")"
            run datalad clone "$output_url" "$output_clone" \
                || { warn "[$DS] Output clone failed – skipping"; ((failed++)); continue; }
        fi

        # 4. Ensure push lock directory exists
        run mkdir -p "$push_lock_dir"

        log "[$DS] Setup complete"
    done

    log "Setup finished. Failures: ${failed}/${#DATASETS[@]}"
    [[ $failed -eq 0 ]] || warn "Re-run with -d DATASET_ID for failed datasets"
}

# ── Phase 2: submit ───────────────────────────────────────────────────────────
cmd_submit() {
    check_todos
    resolve_config

    local scripts_dir="${REPO_DIR}/scripts/generated"
    local submission_log="${REPO_DIR}/logs/submission_$(date '+%Y%m%d_%H%M%S').log"
    mkdir -p "$scripts_dir" "$(dirname "$submission_log")"

    log "Submitting ${#DATASETS[@]} dataset(s)..."
    log "Submission log: ${submission_log}"

    local submitted=0 skipped=0 failed=0

    for DS in "${DATASETS[@]}"; do
        local subj_list="${SUBJ_LISTS_DIR}/${DS}_subjects.txt"
        local array_script="${scripts_dir}/${DS}_bids_array.sh"
        local input_clone="${SHARED_INPUT_BASE}/${DS}"

        log "[$DS] --- submit ---"

        # Build subject list from pre-cloned dataset (reads BIDS directory names)
        if [[ -f "$subj_list" ]] && $RESUME; then
            log "[$DS] Subject list exists, skipping (--resume)"
        else
            if [[ ! -d "$input_clone" ]]; then
                warn "[$DS] Input not cloned at ${input_clone} – run setup first"
                ((failed++)); continue
            fi
            log "[$DS] Building subject list..."
            run bash -c "ls -d ${input_clone}/sub-*/ 2>/dev/null \
                | xargs -I{} basename {} \
                | sort > ${subj_list}" \
                || { warn "[$DS] Failed to build subject list"; ((failed++)); continue; }
        fi

        local n_subjects
        n_subjects=$(wc -l < "$subj_list" | tr -d ' ')
        if [[ "$n_subjects" -eq 0 ]]; then
            warn "[$DS] Subject list is empty – skipping"
            ((failed++)); continue
        fi
        log "[$DS] ${n_subjects} subjects"

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
                || { warn "[$DS] Script generation failed"; ((failed++)); continue; }
        fi

        # Submit
        if $DRY_RUN; then
            echo "[DRY-RUN] sbatch ${array_script}  # ${n_subjects} subjects"
            ((submitted++))
        else
            local job_id
            job_id=$(sbatch "$array_script" 2>&1 | grep -oP '\d+$') \
                || { warn "[$DS] sbatch failed"; ((failed++)); continue; }
            echo "${DS} ${job_id} ${n_subjects}" >> "$submission_log"
            log "[$DS] Submitted array job ${job_id} (${n_subjects} subjects)"
            ((submitted++))
        fi
    done

    log ""
    log "Submitted: ${submitted}  Skipped: ${skipped}  Failed: ${failed}"
    $DRY_RUN || log "Job IDs written to: ${submission_log}"
}

# ── Phase 3: status ───────────────────────────────────────────────────────────
cmd_status() {
    resolve_config

    # Find most recent submission log
    local log_file
    log_file=$(ls -t "${REPO_DIR}/logs/submission_"*.log 2>/dev/null | head -1) \
        || die "No submission log found in ${REPO_DIR}/logs/"

    log "Reading: ${log_file}"
    echo ""
    printf "%-20s %-12s %-10s %s\n" "DATASET" "JOB_ARRAY" "SUBJECTS" "SLURM_STATUS"
    printf "%-20s %-12s %-10s %s\n" "-------" "---------" "--------" "------------"

    while read -r ds job_id n_subjects; do
        local status
        status=$(squeue --job "$job_id" --noheader --format="%T" 2>/dev/null \
            | sort -u | tr '\n' ',' | sed 's/,$//')
        [[ -z "$status" ]] && status="DONE/UNKNOWN"
        printf "%-20s %-12s %-10s %s\n" "$ds" "$job_id" "$n_subjects" "$status"
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
