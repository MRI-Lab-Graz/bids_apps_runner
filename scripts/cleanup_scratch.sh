#!/usr/bin/env bash
# cleanup_scratch.sh -- reclaim orphaned per-task scratch directories left
# behind by failed/timed-out/killed SLURM jobs.
#
# Background: every array task's WORK_DIR follows
#   <scratch_dir>/<dataset_id>/<SLURM_ARRAY_JOB_ID>_<SLURM_ARRAY_TASK_ID>
# (see hpc_datalad_runner.py's _workdirs()). The normal cleanup step
# (rm -rf "$WORK_DIR") only runs on the success path -- `set -e` skips it
# on any failure, and a SLURM TERM-killed task never reaches it either.
# hpc_datalad_runner.py's failure trap now also cleans up after itself
# going forward, but this script is a safety net for everything that trap
# can't see (scancel -9, OOM-kill, node crash) and for the backlog that
# had already accumulated by the time this was written (498G / 687 dirs on
# the openneuro MRIQC scratch volume, 48G / 11 dirs on study 134's
# freesurfer scratch -- found 2026-08-07, /cl_tmp at 90% capacity).
#
# Deletion rule: a per-task dir is a candidate only if BOTH
#   (a) older than --min-age-days, AND
#   (b) its <jobid>_<taskid> is not a live SLURM job (RUNNING/PENDING) --
#       checked regardless of age, so a legitimately long-running job
#       (like study 134's freesurfer subjects, which ran past 20h) is
#       never touched even if it outlives the age threshold.
#
# Scratch roots are discovered dynamically, not hardcoded: every
# .paths.scratch_dir found across configs/*.json and
# projects/*/logs/cohort/*.json that starts with --scope-prefix (default
# /cl_tmp/mrilabgraz) -- new studies land here all the time and shouldn't
# need this script edited to be covered.
#
# Only ever descends into the exact <root>/<dataset_id>/<jobid>_<taskid>
# shape _workdirs() actually creates (checked via a strict regex) -- never
# a bare age-based sweep of arbitrary paths under a scratch root, so a
# stray non-scratch file/dir living there for other reasons is untouched.
#
# Usage:
#   scripts/cleanup_scratch.sh                    # dry-run (default), 7 days
#   scripts/cleanup_scratch.sh --live              # actually delete
#   scripts/cleanup_scratch.sh --min-age-days 3
#   scripts/cleanup_scratch.sh --live --min-age-days 3
#   scripts/cleanup_scratch.sh --scope-prefix /cl_tmp/mrilab
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MIN_AGE_DAYS=7
LIVE=false
SCOPE_PREFIXES=()

usage() {
    grep '^#' "$0" | sed '1d;s/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --live)          LIVE=true; shift ;;
        --min-age-days)  MIN_AGE_DAYS="$2"; shift 2 ;;
        # Repeatable, not a single value -- e.g. --scope-prefix /cl_tmp/mrilabgraz
        # --scope-prefix /cl_tmp/mrilab covers both lab scratch roots (they're
        # separate top-level dirs, not one a subdir of the other, so a single
        # prefix can't cover both -- deliberately not relying on "/cl_tmp/mrilab"
        # substring-matching "/cl_tmp/mrilabgraz/..." too, since that's fragile
        # if either directory naming ever changes).
        --scope-prefix)  SCOPE_PREFIXES+=("$2"); shift 2 ;;
        -h|--help)       usage ;;
        *) echo "Unknown option: $1" >&2; usage ;;
    esac
done
[[ ${#SCOPE_PREFIXES[@]} -eq 0 ]] && SCOPE_PREFIXES=("/cl_tmp/mrilabgraz")

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

_in_scope() {
    local path="$1" prefix
    for prefix in "${SCOPE_PREFIXES[@]}"; do
        [[ "$path" == "$prefix"* ]] && return 0
    done
    return 1
}

# ── Discover scratch roots from every known config, not a hardcoded list ──
mapfile -t ALL_SCRATCH_DIRS < <(
    find "${REPO_DIR}/configs" "${REPO_DIR}/projects" -name "*.json" -type f 2>/dev/null \
        -exec jq -r '.paths.scratch_dir // empty' {} \; 2>/dev/null \
        | sort -u
)
SCRATCH_ROOTS=()
for d in "${ALL_SCRATCH_DIRS[@]:-}"; do
    [[ -z "$d" ]] && continue
    _in_scope "$d" && SCRATCH_ROOTS+=("$d")
done

if [[ ${#SCRATCH_ROOTS[@]} -eq 0 ]]; then
    log "No scratch_dir values found under any of: ${SCOPE_PREFIXES[*]} -- nothing to do."
    exit 0
fi

log "$($LIVE && echo "LIVE run" || echo "DRY-RUN") -- min age ${MIN_AGE_DAYS}d, scope ${SCOPE_PREFIXES[*]}"
log "Scratch roots in scope:"
printf '  %s\n' "${SCRATCH_ROOTS[@]}"

# ── Live SLURM job/task ids -- never touch one of these regardless of age ──
mapfile -t LIVE_TASK_IDS < <(squeue -u "$(whoami)" -h -o "%i" 2>/dev/null)
_is_live() {
    local jt="$1" id
    for id in "${LIVE_TASK_IDS[@]:-}"; do
        [[ "$id" == "$jt" ]] && return 0
    done
    return 1
}

total_candidates=0
total_bytes=0
deleted=0

for root in "${SCRATCH_ROOTS[@]}"; do
    [[ -d "$root" ]] || continue
    while IFS= read -r dir; do
        [[ -z "$dir" ]] && continue
        jt="$(basename "$dir")"
        if _is_live "$jt"; then
            log "skip (live job) ${dir}"
            continue
        fi
        size=$(du -sb "$dir" 2>/dev/null | cut -f1)
        size=${size:-0}
        total_candidates=$((total_candidates + 1))
        total_bytes=$((total_bytes + size))
        if $LIVE; then
            log "DELETE ${dir} ($((size / 1024 / 1024))MB)"
            rm -rf "$dir"
            deleted=$((deleted + 1))
        else
            log "[dry-run] would delete ${dir} ($((size / 1024 / 1024))MB)"
        fi
    done < <(find "$root" -mindepth 2 -maxdepth 2 -type d \
                  -regextype posix-extended -regex '.*/[0-9]+_[0-9]+' \
                  -mtime "+${MIN_AGE_DAYS}" 2>/dev/null)
done

total_mb=$((total_bytes / 1024 / 1024))
if $LIVE; then
    summary="Deleted ${deleted} orphaned scratch dir(s), reclaimed ~${total_mb}MB."
else
    summary="[dry-run] ${total_candidates} orphaned scratch dir(s) would be deleted, ~${total_mb}MB reclaimable. Re-run with --live to actually delete."
fi
log "$summary"

notify_tag="mag"
$LIVE && notify_tag="wastebasket"
"${REPO_DIR}/scripts/notify_ntfy.sh" "Scratch cleanup: $($LIVE && echo LIVE || echo DRY-RUN)" \
    "$summary" default "$notify_tag" >/dev/null 2>&1 || true
