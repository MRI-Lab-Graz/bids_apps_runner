#!/usr/bin/env bash
# cleanup_output_data.sh -- reclaim /cl_tmp space by dropping the *local
# content* of datalad-managed BIDS data that (a) hasn't been touched in
# --min-age-days-* days and (b) is already safely on its datalad remote.
#
# This does NOT delete the dataset clone or its git/git-annex history --
# it runs `datalad drop`, which removes only the annexed file content and
# leaves the lightweight git-annex pointer in place. A later `datalad get`
# re-fetches it. `datalad drop` refuses on its own to drop content below
# the dataset's configured numcopies unless it can verify another copy
# exists (this repo's datasets are numcopies=1 against an annex-enabled
# `origin`), so the actual "is it safe" check is datalad's own, not ours.
#
# Two independently-configurable scopes, since re-fetch cost/risk differs:
#   outputs -- datalad-slurm-managed derivative clones (expensive to
#              regenerate: costs a full pipeline re-run)
#   inputs  -- raw BIDS clones staged locally via `datalad get` (cheap to
#              regenerate: just re-fetches from the input remote)
# Discovered the same way cleanup_scratch.sh discovers scratch roots --
# dynamically from every known config, not a hardcoded list:
#   outputs: projects/*/project.json .config.common.output_folder, plus
#            sibling clones under .config.common.pipeline_output_root, plus
#            configs/*.json .paths.shared_output_base (a parent dir of
#            several per-dataset clones -- descended one level)
#   inputs:  projects/*/project.json .config.common.bids_folder, plus
#            configs/*.json .paths.shared_input_base (descended one level
#            the same way)
#
# Precondition before touching a clone at all: it must be "clean" by
# check_output_sync.sh's own definition (lib_clone_check.sh, shared with
# that script) -- no uncommitted changes, no commits unpushed to
# origin/<branch>, not a broken repo. A clone with any local-only state is
# skipped entirely regardless of file age: we can't trust that dropping
# its content wouldn't strand something that was never actually pushed.
#
# Granularity is per-file, not per-clone: within a clean clone, only the
# individual annexed files whose content is older than the threshold are
# dropped (via `find -L ... -mtime`, which follows the annex symlink to
# the real content object -- works for both locked and unlocked annex
# layouts) and still actually present locally (`git annex find --in
# here`). A dataset with one actively-growing subject and nine finished
# ones still reclaims space from the nine.
#
# Login-node guard: unlike check_output_sync.sh (read-only, no network
# I/O), a --live run's `datalad drop` calls DO contact the datalad remote
# to verify a safe copy exists -- that's real data-movement I/O and must
# not run on a bare HPC login node (see CLAUDE.md's "HPC login node
# policy" -- this is the same class of violation as the 2026-07-29
# incident). --live therefore refuses outside an active SLURM allocation,
# using the same detection prism_local.py's execute_local() guard uses.
# --dry-run (the default) does no network I/O and is exempt.
#
# Usage:
#   scripts/cleanup_output_data.sh                          # dry-run, both scopes, 5 days
#   scripts/cleanup_output_data.sh --live                    # actually drop (needs an sbatch/salloc allocation)
#   scripts/cleanup_output_data.sh --outputs-only
#   scripts/cleanup_output_data.sh --inputs-only
#   scripts/cleanup_output_data.sh --min-age-days-outputs 10 --min-age-days-inputs 3
#   scripts/cleanup_output_data.sh --scope-prefix /cl_tmp/mrilab
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECTS_DIR="${REPO_DIR}/projects"
CONFIGS_DIR="${REPO_DIR}/configs"

# shellcheck source=lib_clone_check.sh
source "${REPO_DIR}/scripts/lib_clone_check.sh"

# Same venv-pinned datalad binary the finish-job scripts use (see
# submit_bids_cohort.sh) -- a plain `datalad` on PATH may be a different
# install without the datalad-slurm extension enabled at all.
DATALAD_BIN="${REPO_DIR}/.datalad-slurm-venv/bin/datalad"
[[ -x "$DATALAD_BIN" ]] || DATALAD_BIN="datalad"

LIVE=false
MIN_AGE_OUTPUTS=5
MIN_AGE_INPUTS=5
DO_OUTPUTS=true
DO_INPUTS=true
SCOPE_PREFIXES=()

usage() {
    grep '^#' "$0" | sed '1d;s/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --live)                  LIVE=true; shift ;;
        --min-age-days-outputs)  MIN_AGE_OUTPUTS="$2"; shift 2 ;;
        --min-age-days-inputs)   MIN_AGE_INPUTS="$2"; shift 2 ;;
        --outputs-only)          DO_INPUTS=false; shift ;;
        --inputs-only)           DO_OUTPUTS=false; shift ;;
        # Repeatable -- see cleanup_scratch.sh's --scope-prefix for why this
        # can't just be a substring check.
        --scope-prefix)          SCOPE_PREFIXES+=("$2"); shift 2 ;;
        -h|--help)                usage ;;
        *) echo "Unknown option: $1" >&2; usage ;;
    esac
done
# Both roots hold real study data on disk and are confirmed ours (see
# scripts/cleanup_scratch.sh's --scope-prefix comment) -- unlike other
# users' folders under /cl_tmp, which must never be touched.
[[ ${#SCOPE_PREFIXES[@]} -eq 0 ]] && SCOPE_PREFIXES=("/cl_tmp/mrilabgraz" "/cl_tmp/mrilab")

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

_in_scope() {
    local path="$1" prefix
    for prefix in "${SCOPE_PREFIXES[@]}"; do
        [[ "$path" == "$prefix"* ]] && return 0
    done
    return 1
}

is_datalad_dataset() { [[ -f "$1/.datalad/config" ]]; }

if $LIVE && command -v sbatch >/dev/null 2>&1 && [[ -z "${SLURM_JOB_ID:-}${SLURM_JOBID:-}" ]]; then
    cat >&2 <<'EOF'
REFUSING TO RUN --live ON A BARE HPC LOGIN NODE.

A --live run calls `datalad drop`, which contacts the datalad remote over
the network to verify a safe copy exists before removing local content.
That is real data-movement I/O and must not run interactively on a
cluster login node -- see CLAUDE.md's "HPC login node policy" (this is
the same class of incident as the 2026-07-29 datalad-push-on-IT010128
event).

Run this via an actual allocation instead:
  salloc ...    then re-run this command with --live
or submit it as a batch job with sbatch.

(--dry-run / the default mode is exempt -- it does no network I/O.)
EOF
    exit 1
fi

declare -A SEEN
total_candidates=0
total_bytes=0
touched_clones=0

# Assess one clone and, if clean, drop its stale locally-present content.
process_clone() {
    local path="$1" min_age="$2" label="$3"
    [[ -d "$path" ]] || return 0
    [[ -n "${SEEN[$path]:-}" ]] && return 0
    SEEN["$path"]=1

    if ! _in_scope "$path"; then
        log "skip (outside scope ${SCOPE_PREFIXES[*]}): $path"
        return 0
    fi
    is_datalad_dataset "$path" || return 0

    if ! clone_is_clean "$path"; then
        log "skip (not clean -- uncommitted=${CLONE_UNCOMMITTED} unpushed=${CLONE_UNPUSHED} broken=${CLONE_BROKEN}): $path"
        return 0
    fi

    # Locally-present content older than the threshold. -L follows the
    # annex symlink (locked layout) to the real object's mtime; for
    # unlocked-layout files (already a regular file) it's a no-op.
    # Bounded the same way as clone_is_clean above -- a full-tree find or
    # git-annex index walk over a huge clone on this filesystem can run far
    # longer than is useful for a housekeeping pass; skip it for now rather
    # than stall every other clone queued behind it.
    # A pipeline's $? (or a process-substitution `< <(...)`'s) reflects the
    # reading command, not the producer -- capture via $(...) instead, whose
    # exit status IS the command's, so a `timeout` kill (124) is visible.
    local old_files_raw find_rc
    old_files_raw=$(timeout "$CLONE_CHECK_TIMEOUT_SECS" find -L "$path" -type f -mtime "+${min_age}" 2>/dev/null)
    find_rc=$?
    if [[ "$find_rc" -eq 124 ]]; then
        log "skip (find timed out after ${CLONE_CHECK_TIMEOUT_SECS}s): $path"
        return 0
    fi
    local -a old_files=()
    [[ -n "$old_files_raw" ]] && mapfile -t old_files <<<"$old_files_raw"
    [[ ${#old_files[@]} -eq 0 ]] && return 0

    local present_raw annex_rc
    present_raw=$(timeout "$CLONE_CHECK_TIMEOUT_SECS" git -C "$path" annex find --in here 2>/dev/null)
    annex_rc=$?
    if [[ "$annex_rc" -eq 124 ]]; then
        log "skip (git annex find timed out after ${CLONE_CHECK_TIMEOUT_SECS}s): $path"
        return 0
    fi
    local -a present=()
    [[ -n "$present_raw" ]] && mapfile -t present <<<"$present_raw"
    [[ ${#present[@]} -eq 0 ]] && return 0

    local -A is_old=()
    local f rel
    for f in "${old_files[@]}"; do
        rel="${f#"$path"/}"
        is_old["$rel"]=1
    done

    local -a candidates=()
    for f in "${present[@]}"; do
        [[ -n "${is_old[$f]:-}" ]] && candidates+=("$f")
    done
    [[ ${#candidates[@]} -eq 0 ]] && return 0

    local bytes=0 sz
    for f in "${candidates[@]}"; do
        sz=$(find -L "${path}/${f}" -printf '%s' 2>/dev/null)
        bytes=$((bytes + ${sz:-0}))
    done

    total_candidates=$((total_candidates + ${#candidates[@]}))
    total_bytes=$((total_bytes + bytes))
    touched_clones=$((touched_clones + 1))

    if $LIVE; then
        log "DROP ${#candidates[@]} ${label} file(s) (~$((bytes / 1024 / 1024))MB) in $path"
        printf '%s\n' "${candidates[@]}" | xargs -d '\n' -n 200 \
            "$DATALAD_BIN" drop --dataset "$path" -- 2>&1 | sed 's/^/    /'
    else
        log "[dry-run] would drop ${#candidates[@]} ${label} file(s) (~$((bytes / 1024 / 1024))MB) in $path"
    fi
}

# A shared_input_base/shared_output_base is a parent directory holding
# several per-dataset clones (e.g. one per OpenNeuro accession) -- unlike
# project.json's output_folder/bids_folder, which already point straight
# at a single clone. Handle both shapes: if the base itself is a dataset,
# process it directly; otherwise descend one level.
process_base_dir() {
    local base="$1" min_age="$2" label="$3"
    [[ -d "$base" ]] || return 0
    if is_datalad_dataset "$base"; then
        process_clone "$base" "$min_age" "$label"
        return 0
    fi
    local sub
    for sub in "$base"/*/; do
        [[ -d "$sub" ]] || continue
        process_clone "${sub%/}" "$min_age" "$label"
    done
}

log "$($LIVE && echo "LIVE run" || echo "DRY-RUN") -- outputs min-age ${MIN_AGE_OUTPUTS}d, inputs min-age ${MIN_AGE_INPUTS}d, scope ${SCOPE_PREFIXES[*]}"

if $DO_OUTPUTS; then
    for pj in "$PROJECTS_DIR"/*/project.json; do
        [[ -s "$pj" ]] || continue
        out_folder=$(jq -r '.config.common.output_folder // empty' "$pj" 2>/dev/null)
        out_root=$(jq -r '.config.common.pipeline_output_root // empty' "$pj" 2>/dev/null)
        [[ -n "$out_folder" ]] && process_clone "$out_folder" "$MIN_AGE_OUTPUTS" "output"
        if [[ -n "$out_root" && -d "$out_root" ]]; then
            for sub in "$out_root"/*/; do
                [[ -d "$sub" ]] && process_clone "${sub%/}" "$MIN_AGE_OUTPUTS" "output"
            done
        fi
    done

    mapfile -t shared_output_bases < <(
        find "$CONFIGS_DIR" -maxdepth 1 -name "*.json" -type f 2>/dev/null \
            -exec jq -r '.paths.shared_output_base // empty' {} \; 2>/dev/null | sort -u
    )
    for d in "${shared_output_bases[@]:-}"; do
        [[ -z "$d" ]] && continue
        process_base_dir "$d" "$MIN_AGE_OUTPUTS" "output"
    done
fi

if $DO_INPUTS; then
    for pj in "$PROJECTS_DIR"/*/project.json; do
        [[ -s "$pj" ]] || continue
        bids_folder=$(jq -r '.config.common.bids_folder // empty' "$pj" 2>/dev/null)
        [[ -n "$bids_folder" ]] && process_clone "$bids_folder" "$MIN_AGE_INPUTS" "input"
    done

    mapfile -t shared_input_bases < <(
        find "$CONFIGS_DIR" -maxdepth 1 -name "*.json" -type f 2>/dev/null \
            -exec jq -r '.paths.shared_input_base // empty' {} \; 2>/dev/null | sort -u
    )
    for d in "${shared_input_bases[@]:-}"; do
        [[ -z "$d" ]] && continue
        process_base_dir "$d" "$MIN_AGE_INPUTS" "input"
    done
fi

total_mb=$((total_bytes / 1024 / 1024))
if $LIVE; then
    summary="Dropped content in ${touched_clones} clone(s), ${total_candidates} file(s), reclaimed ~${total_mb}MB."
else
    summary="[dry-run] ${total_candidates} file(s) across ${touched_clones} clone(s) would be dropped, ~${total_mb}MB reclaimable. Re-run with --live (from an sbatch/salloc allocation) to actually drop."
fi
log "$summary"

notify_tag="mag"
$LIVE && notify_tag="wastebasket"
"${REPO_DIR}/scripts/notify_ntfy.sh" "Output data cleanup: $($LIVE && echo LIVE || echo DRY-RUN)" \
    "$summary" default "$notify_tag" >/dev/null 2>&1 || true
