#!/usr/bin/env bash
# annex_cohort_finish.sh -- commit + push a cohort's fresh output via annex-slurm.
#
# The cohort finish step. Replaces `datalad slurm-finish`,
# incremental_datalad_save.sh and the `git status` "anything uncommitted?"
# check: every one of them compares the index against the worktree, which
# hangs on repos carrying git-annex keys-DB drift (dataset 134 -- see
# docs/superpowers/specs/2026-09-09-annex-slurm-design.md and CLAUDE.md).
#
# How: for each subject in the list, find its regular (not yet annexed)
# files with `find` -- a plain filesystem listing -- and hand them to
# annex-slurm-finish in chunks. One subject at a time, so an interruption
# costs at most one chunk and a re-run picks up exactly where it stopped
# (committed files are symlinks by then and no longer listed).
#
# Contract: exit 0 means a re-scan finds NO regular file left under any
# listed subject. The finish tool's own exit code is not trusted for that --
# a step that silently does nothing is what let 132 empty commits through on
# 134 (2026-09-22).
#
# Usage:
#   annex_cohort_finish.sh -d DATASET -s LIST -m MESSAGE [options]
#
#   -d, --dataset DIR   Output dataset (git-annex clone)
#   -s, --subjects FILE Names to commit, one per line (sub-01, sub-01_ses-A, ...)
#   -m, --message TEXT  Commit message prefix (subject name is appended)
#       --root-files    Also commit non-hidden files in the dataset root
#       --extra PATH    Also commit everything under PATH (relative to DIR);
#                       repeatable, e.g. .slurm_logs/<ds>, subregion_results
#       --chunk N       Max paths per annex-slurm-finish call (default 2000)
#       --dry-run       List what would be committed, change nothing
#
# Environment: ANNEX_SLURM_FINISH overrides the finish tool (tests).
#
# Login-node policy: hashing + rsync + push of real content. Refuses on a
# bare SLURM login node like execute_local() does; --dry-run is exempt.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
# shellcheck source=lib_annex_paths.sh
source "${SCRIPT_DIR}/lib_annex_paths.sh"

DATASET_DIR="" SUBJECT_LIST="" MESSAGE="" CHUNK=2000 ROOT_FILES=false DRY_RUN=false
EXTRAS=()

log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
warn() { echo "[WARN] $*" >&2; }
die()  { echo "[ERROR] $*" >&2; exit 1; }
usage() { grep '^#' "$0" | sed '1d;s/^# \{0,1\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        -d|--dataset)  DATASET_DIR="$2"; shift 2 ;;
        -s|--subjects) SUBJECT_LIST="$2"; shift 2 ;;
        -m|--message)  MESSAGE="$2"; shift 2 ;;
        --root-files)  ROOT_FILES=true; shift ;;
        --extra)       EXTRAS+=("$2"); shift 2 ;;
        --chunk)       CHUNK="$2"; shift 2 ;;
        --dry-run)     DRY_RUN=true; shift ;;
        -h|--help)     usage ;;
        *) die "Unknown option: $1" ;;
    esac
done

[[ -n "$DATASET_DIR"  ]] || die "-d/--dataset is required"
[[ -n "$SUBJECT_LIST" ]] || die "-s/--subjects is required"
[[ -n "$MESSAGE"      ]] || die "-m/--message is required"
[[ "$CHUNK" =~ ^[1-9][0-9]*$ ]] || die "--chunk must be a positive integer"
[[ -d "$DATASET_DIR"  ]] || die "Dataset dir not found: ${DATASET_DIR}"
[[ -f "$SUBJECT_LIST" ]] || die "Subject list not found: ${SUBJECT_LIST}"
[[ -d "${DATASET_DIR}/.git" ]] || die "Not a git/annex dataset: ${DATASET_DIR}"

if ! $DRY_RUN && command -v sbatch >/dev/null 2>&1 \
   && [[ -z "${SLURM_JOB_ID:-}${SLURM_JOBID:-}" ]]; then
    cat >&2 <<'GUARD'
[ERROR] Refusing to run on what looks like a SLURM login node.

  This script does real data movement (checksumming + rsync + push).
  Per the cluster policy in CLAUDE.md, that must not run on the login node.
  Submit it with sbatch, get an allocation (salloc/srun), or use --dry-run.
GUARD
    exit 1
fi

# Only the venv's git-annex works on compute nodes (see submit_bids_cohort.sh).
[[ -d "${REPO_DIR}/.datalad-slurm-venv/bin" ]] && export PATH="${REPO_DIR}/.datalad-slurm-venv/bin:$PATH"
FINISH_TOOL="${ANNEX_SLURM_FINISH:-${REPO_DIR}/annex-slurm/bin/annex-slurm-finish}"
$DRY_RUN || [[ -x "$FINISH_TOOL" ]] || die "annex-slurm-finish not found: ${FINISH_TOOL}"

cd "$DATASET_DIR" || die "Cannot cd to ${DATASET_DIR}"

# Regular files (not yet annexed) under the given root-relative paths, NUL-
# separated and sorted. Symlinks are already committed; they are skipped.
unannexed_under() {
    (( $# )) || return 0
    find "$@" -type f -print0 2>/dev/null | sort -z
}

# commit_group LABEL PATH... -- commit everything unannexed under the paths.
# Returns 0 when nothing to do or all committed and re-scan is clean.
commit_group() {
    local label="$1"; shift
    local -a files=()
    mapfile -d '' files < <(unannexed_under "$@")
    if (( ${#files[@]} == 0 )); then
        log "${label}: nothing uncommitted"
        return 0
    fi
    if $DRY_RUN; then
        log "${label}: would commit ${#files[@]} file(s)"
        printf '  %s\n' "${files[@]}"
        return 0
    fi
    log "${label}: committing ${#files[@]} file(s) in chunks of ${CHUNK}"
    local i
    for (( i = 0; i < ${#files[@]}; i += CHUNK )); do
        "$FINISH_TOOL" -m "${MESSAGE}: ${label}" "${files[@]:i:CHUNK}" || {
            warn "${label}: annex-slurm-finish failed (chunk starting at file $((i + 1)))"
            return 1
        }
    done
    # The contract: nothing unannexed may remain.
    mapfile -d '' files < <(unannexed_under "$@")
    if (( ${#files[@]} > 0 )); then
        echo "[ERROR] ${label}: ${#files[@]} file(s) still not annexed after finish -- nothing was really committed. First 20:" >&2
        printf '  %s\n' "${files[@]:0:20}" >&2
        return 1
    fi
    return 0
}

mapfile -t NAMES < <(sed 's/[[:space:]]//g' "$SUBJECT_LIST" | grep -v '^$')
(( ${#NAMES[@]} > 0 )) || die "Subject list is empty: ${SUBJECT_LIST}"
log "Dataset: ${DATASET_DIR} -- ${#NAMES[@]} name(s) from ${SUBJECT_LIST}"

failed=()
for name in "${NAMES[@]}"; do
    mapfile -t entries < <(annex_entries_for_names "$DATASET_DIR" "$name")
    if (( ${#entries[@]} == 0 )); then
        warn "${name}: no output found in the dataset (array task failed or not run?) -- skipping"
        continue
    fi
    commit_group "$name" "${entries[@]}" || failed+=("$name")
done

extra_paths=()
for extra in "${EXTRAS[@]}"; do
    [[ -e "$extra" ]] && extra_paths+=("$extra")
done
if $ROOT_FILES; then
    while IFS= read -r f; do extra_paths+=("$f"); done < <(annex_root_entries "$DATASET_DIR")
fi
if (( ${#extra_paths[@]} > 0 )); then
    commit_group "shared outputs" "${extra_paths[@]}" || failed+=("shared outputs")
fi

if (( ${#failed[@]} > 0 )); then
    echo "[ERROR] ${#failed[@]} group(s) failed: ${failed[*]}" >&2
    exit 1
fi
log "OK: all listed output committed and verified."
