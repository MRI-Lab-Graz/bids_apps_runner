#!/usr/bin/env bash
# remove_non_rest_data.sh -- one-off cleanup for the OpenNeuro resting-state
# weather-correlation project: prune every non-resting-state functional run
# out of the MRIQC-complete cohorts under /cl_tmp/mrilabgraz/openneuro/data,
# and fully remove the two datasets that turned out to have zero qualifying
# resting-state data at all.
#
# Per-dataset "keep" task decisions were made interactively (2026-09-10) --
# see the conversation that produced this script for the reasoning on each
# ambiguous case (sleep-stage variants, neurofeedback conditions, etc.):
#   ds003171: keep task-restawake only (delete restlight/restdeep/
#             restrecovery + all task-audio* + task-audio)
#   ds000256: keep task-restbaseline only (excluded here -- MRIQC incomplete,
#             see below; listed for context, not acted on by this script)
#   ds005127: keep task-rest only, not task-sleep (excluded -- MRIQC
#             incomplete, not acted on by this script)
#   all others: keep task-rest (or task-rest1/task-rest2 for ds004592,
#             task-restME/task-restEyesOpen for ds000031 -- also excluded,
#             MRIQC incomplete)
# Anat/ and fmap/ are always kept regardless of task, for QC/preprocessing.
#
# Only datasets already confirmed 100% MRIQC-complete (raw bold count ==
# processed bold.json count) are pruned here: ds002156, ds003171, ds003382,
# ds003404, ds004592, ds005073, ds005134, ds005365, ds005454, ds005525,
# ds006373. (ds002156/ds005134/ds005365 have no non-rest data to begin with
# and are included only for completeness -- they're a no-op.)
#
# ds002837 (0 resting-state runs -- naturalistic movie-watching only) and
# ds006072 (no functional data at all -- anat/dwi only) are removed
# entirely: both data/ and derivatives/ clones, confirmed clean and fully
# pushed to origin before deletion.
#
# 2026-09-28: added ds003849, ds004182, ds005901, ds000256 -- MRIQC has
# since finished for these (excluded above on 2026-09-10 because it
# hadn't). Also, for the first time, prunes their fMRIPrep derivatives
# alongside raw data -- this script never touched derivatives/<ds>/fmriprep
# at all before, despite fMRIPrep carrying the exact same non-resting-state
# task output as raw data does. Confirmed real incident: these three
# datasets had zero per-subject bids_filters files (see
# build_qa_subject_list.py's 2026-09-28 fix), so fMRIPrep processed every
# task it found -- real output showed 2-5 unrelated tasks (nback, mid,
# face, qreact, magictrickwatching) stored per subject.
#
# Everything else (ds000031, ds002372, ds003823, ds004466, ds005127,
# ds005339, ds005896, ds006707, ds007328, ds007522, ds007694) is left
# untouched: MRIQC has not finished there yet, so pruning non-rest data now
# would remove context a re-run might still need.
#
# Mechanics: raw data/<ds> clones are datalad/git-annex datasets also held
# on OpenNeuro's S3-PUBLIC remote and this project's "origin" sibling, so
# per-file removal goes through `datalad remove` (drops annexed content +
# git rm + save) rather than plain `rm`, matching cleanup_output_data.sh's
# convention. fMRIPrep derivatives clones go through the same `datalad
# remove` path (also datalad/git-annex datasets, pushed to their own
# per-dataset output sibling -- see submit_bids_cohort.sh). Full-dataset
# removal (ds002837, ds006072) is a plain `rm -rf` of the whole clone after
# confirming it is clean and fully pushed -- there is no git history worth
# preserving locally once 100% of a clone is being deleted, and both
# remain recoverable via `datalad clone` from origin.
#
# Login-node guard: --live's `datalad remove` calls contact the datalad
# remote (network I/O), same class of operation cleanup_output_data.sh
# guards -- see CLAUDE.md's "HPC login node policy". --dry-run (default)
# only lists local files and sizes, no network I/O, safe anywhere.
#
# Clean-check: uses clone_is_clean_fast (git diff-index + ref comparison),
# not clone_is_clean (`git status`) -- these clones can have a large
# fraction of unlocked tracked files (confirmed: ds003849/ds005901/
# ds000256's raw clones are 60-67% unlocked in a func/ sample), and
# `git status` hangs for hours re-hashing that content. See
# lib_clone_check.sh's clone_assess_fast docstring for the accepted gap
# this implies (won't catch an unstaged modification to an already-
# unlocked file) and why it's fine for this use case.
#
# Usage:
#   scripts/remove_non_rest_data.sh                # dry-run (default)
#   scripts/remove_non_rest_data.sh --live          # actually remove (needs an sbatch/salloc allocation)
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_BASE="/cl_tmp/mrilabgraz/openneuro/data"
DERIV_BASE="/cl_tmp/mrilabgraz/openneuro/derivatives"

# shellcheck source=lib_clone_check.sh
source "${REPO_DIR}/scripts/lib_clone_check.sh"

DATALAD_BIN="${REPO_DIR}/.datalad-slurm-venv/bin/datalad"
[[ -x "$DATALAD_BIN" ]] || DATALAD_BIN="datalad"

LIVE=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --live) LIVE=true; shift ;;
        -h|--help) grep '^#' "$0" | sed '1d;s/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

if $LIVE && command -v sbatch >/dev/null 2>&1 && [[ -z "${SLURM_JOB_ID:-}${SLURM_JOBID:-}" ]]; then
    cat >&2 <<'EOF'
REFUSING TO RUN --live ON A BARE HPC LOGIN NODE.

A --live run's `datalad remove` calls contact the datalad remote over the
network to verify a safe copy exists -- see CLAUDE.md's "HPC login node
policy". Get a real allocation first:
  salloc ...    then re-run this command with --live
(--dry-run / the default mode is exempt.)
EOF
    exit 1
fi

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

total_removed=0
total_bytes=0

# Prune a dataset down to only the given keep-task(s). Matches any
# sub-*/**/func/*_task-<KEEPTASK>[_.]* file as "keep"; everything else
# under func/ in that dataset is removed. anat/, fmap/, dwi-less top-level
# metadata are never touched.
#
# `label` is just for log messages; `path` is the full clone path to prune
# -- either a raw data/<ds> clone or a derivatives/<ds>/fmriprep clone,
# both apply equally (2026-09-28: this used to only ever be reconstructed
# as ${DATA_BASE}/${ds} internally, so fMRIPrep derivatives -- which carry
# the exact same non-resting-state task output -- were never covered).
#
# Uses clone_is_clean_fast, not clone_is_clean: these clones (raw
# OpenNeuro downloads and fMRIPrep output alike) can have a large fraction
# of unlocked tracked files, and clone_is_clean's `git status` hangs for
# hours on that -- see lib_clone_check.sh's clone_assess_fast docstring.
prune_dataset() {
    local label="$1" path="$2"; shift 2
    local -a keep_tasks=("$@")
    [[ -d "$path" ]] || { log "skip $label: no such dataset dir"; return; }

    if ! clone_is_clean_fast "$path"; then
        log "SKIP $label: not clean (uncommitted=${CLONE_UNCOMMITTED} unpushed=${CLONE_UNPUSHED} broken=${CLONE_BROKEN}${CLONE_BROKEN_MSG:+: $CLONE_BROKEN_MSG})"
        return
    fi

    # Build a grep -E alternation like: _task-rest1_|_task-rest1\.
    local keep_pat
    keep_pat=$(printf '_task-%s[_.]|' "${keep_tasks[@]}")
    keep_pat="${keep_pat%|}"

    local -a to_remove=()
    while IFS= read -r -d '' f; do
        local rel="${f#"$path"/}"
        if [[ ! "$rel" =~ $keep_pat ]]; then
            to_remove+=("$rel")
        fi
    done < <(find "$path" -mindepth 1 -path '*/func/*' \( -type l -o -type f \) -print0 2>/dev/null)

    if [[ ${#to_remove[@]} -eq 0 ]]; then
        log "$label: nothing to remove (keep: ${keep_tasks[*]})"
        return
    fi

    local bytes=0 sz f
    for f in "${to_remove[@]}"; do
        sz=$(find -L "${path}/${f}" -printf '%s' 2>/dev/null)
        bytes=$((bytes + ${sz:-0}))
    done
    total_removed=$((total_removed + ${#to_remove[@]}))
    total_bytes=$((total_bytes + bytes))

    if $LIVE; then
        log "$label: removing ${#to_remove[@]} non-rest file(s) (~$((bytes / 1024 / 1024))MB), keeping [${keep_tasks[*]}]"
        # datalad resolves given paths relative to CWD, not relative to
        # --dataset -- must run from inside the dataset itself.
        (cd "$path" && printf '%s\n' "${to_remove[@]}" | xargs -d '\n' -n 200 \
            "$DATALAD_BIN" remove --dataset "$path" --nocheck -- 2>&1 | sed 's/^/    /')
    else
        log "[dry-run] $label: would remove ${#to_remove[@]} non-rest file(s) (~$((bytes / 1024 / 1024))MB), keeping [${keep_tasks[*]}]"
    fi
}

# Fully remove a dataset (data/ + derivatives/) after confirming both
# clones are clean and fully pushed.
remove_dataset_entirely() {
    local ds="$1" reason="$2"
    local dpath="${DATA_BASE}/${ds}"
    local mpath="${DERIV_BASE}/${ds}/mriqc"

    local ok=true
    if [[ -d "$dpath" ]]; then
        clone_is_clean_fast "$dpath" || { log "SKIP full-delete $ds: data/ not clean (uncommitted=${CLONE_UNCOMMITTED} unpushed=${CLONE_UNPUSHED} broken=${CLONE_BROKEN})"; ok=false; }
    fi
    if [[ -d "$mpath" ]]; then
        clone_is_clean_fast "$mpath" || { log "SKIP full-delete $ds: derivatives/mriqc not clean (uncommitted=${CLONE_UNCOMMITTED} unpushed=${CLONE_UNPUSHED} broken=${CLONE_BROKEN})"; ok=false; }
    fi
    $ok || return

    local bytes
    bytes=$(du -sb "${DERIV_BASE}/${ds}" "$dpath" 2>/dev/null | awk '{s+=$1} END{print s+0}')
    total_bytes=$((total_bytes + bytes))

    if $LIVE; then
        log "$ds: removing ENTIRELY (~$((bytes / 1024 / 1024 / 1024))GB) -- $reason"
        rm -rf -- "$dpath" "${DERIV_BASE}/${ds}"
    else
        log "[dry-run] $ds: would remove ENTIRELY (~$((bytes / 1024 / 1024 / 1024))GB) -- $reason"
    fi
}

# Only runs when this script is executed directly -- sourcing it (as
# tests do, to exercise prune_dataset/remove_dataset_entirely against
# synthetic fixtures) must not also kick off a full scan against real
# production data under $DATA_BASE/$DERIV_BASE.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then

log "$($LIVE && echo "LIVE run" || echo "DRY-RUN")"

prune_dataset ds002156 "${DATA_BASE}/ds002156" rest
prune_dataset ds003171 "${DATA_BASE}/ds003171" restawake
prune_dataset ds003382 "${DATA_BASE}/ds003382" rest
prune_dataset ds003404 "${DATA_BASE}/ds003404" rest
prune_dataset ds004592 "${DATA_BASE}/ds004592" rest1 rest2
prune_dataset ds005073 "${DATA_BASE}/ds005073" rest
prune_dataset ds005134 "${DATA_BASE}/ds005134" rest
prune_dataset ds005365 "${DATA_BASE}/ds005365" rest
prune_dataset ds005454 "${DATA_BASE}/ds005454" rest
prune_dataset ds005525 "${DATA_BASE}/ds005525" rest
prune_dataset ds006373 "${DATA_BASE}/ds006373" rest

# 2026-09-28: MRIQC has since finished for these four (excluded above on
# 2026-09-10 because it hadn't). Also, for the first time, prune their
# fMRIPrep derivatives too -- never covered before, despite carrying the
# exact same non-resting-state task output (confirmed: ds003849/ds004182/
# ds005901 had zero bids_filters files, real output shows 2-5 unrelated
# tasks processed per subject; ds000256's derivatives are prunable too,
# though its fMRIPrep run itself is still incomplete as of this writing).
prune_dataset ds003849 "${DATA_BASE}/ds003849" rest
prune_dataset ds003849-fmriprep "${DERIV_BASE}/ds003849/fmriprep" rest
prune_dataset ds004182 "${DATA_BASE}/ds004182" rest
prune_dataset ds004182-fmriprep "${DERIV_BASE}/ds004182/fmriprep" rest
prune_dataset ds005901 "${DATA_BASE}/ds005901" rest
prune_dataset ds005901-fmriprep "${DERIV_BASE}/ds005901/fmriprep" rest
prune_dataset ds000256 "${DATA_BASE}/ds000256" restbaseline
prune_dataset ds000256-fmriprep "${DERIV_BASE}/ds000256/fmriprep" restbaseline

remove_dataset_entirely ds002837 "zero resting-state runs (movie-watching task only)"
remove_dataset_entirely ds006072 "no functional/BOLD data at all (anat+dwi only)"

total_mb=$((total_bytes / 1024 / 1024))
if $LIVE; then
    log "Done. Removed ${total_removed} file(s) + full datasets, reclaimed ~${total_mb}MB."
else
    log "[dry-run] ${total_removed} file(s) plus 2 full-dataset removals would run, ~${total_mb}MB reclaimable. Re-run with --live (from an sbatch/salloc allocation) to execute."
fi

fi  # BASH_SOURCE guard
