#!/usr/bin/env bash
# incremental_datalad_save.sh
#
# Commit + push a BIDS-app output dataset ONE SUBJECT AT A TIME, instead of
# the single monolithic `datalad slurm-finish` save that submit_bids_cohort.sh
# normally chains after an array job.
#
# Why this exists
# ───────────────
# `datalad slurm-finish` saves the whole array's declared output scope ("-o .",
# the entire dataset) in one operation, and refuses entirely unless every array
# task COMPLETED. For a large cohort that means:
#   • nothing at all reaches the datalad server until the very last subject is
#     hashed -- a 117-subject FreeSurfer longitudinal cohort (729 top-level
#     dirs, ~300k files) took >12h and still hadn't committed anything;
#   • if that one job is interrupted mid-save it hits datalad-slurm's known
#     premature-DB-removal bug (https://github.com/knuedd/datalad-slurm/issues/97),
#     leaving the outputs uncommitted AND the bookkeeping entry gone.
# This script trades one big atomic save for N small checkpointed ones: after
# each subject is saved, that subject is durably committed and can be pushed,
# so an interruption costs you one subject rather than the whole cohort.
#
# The saves MUST be path-scoped (that's the whole trick). An unscoped
# `datalad save` re-scans the entire working tree to find changes, so doing it
# once per subject would be O(n^2) and far slower than the monolithic save.
# Scoping to "<subject>*" means git only stats that subject's own subtree.
# For FreeSurfer longitudinal that glob deliberately catches all of:
#     sub-134001                        (base/template)
#     sub-134001_ses-1                  (cross-sectional)
#     sub-134001_ses-1.long.sub-134001  (longitudinal)
# which live as SIBLINGS at the dataset root -- FreeSurfer's SUBJECTS_DIR
# requires that layout, which is also why per-subject subdatasets are a poor
# fit for this data.
#
# Idempotent / resumable: a subject whose paths are already clean (i.e. already
# committed) is skipped, so this can be re-run after an interruption, or run
# after a successful slurm-finish, in which case it finds nothing to do.
#
# Usage:
#   incremental_datalad_save.sh -d DATASET_DIR -s SUBJECT_LIST [options]
#
#   -d, --dataset DIR      Output dataset (the datalad clone) to save into
#   -s, --subjects FILE    Subject list, one ID per line (e.g. sub-134001)
#   -J, --jobs N           Parallel annex jobs per save (default: 4)
#       --push-every N     Push to origin every N saved subjects (default: 10).
#                          Each `datalad push` re-enumerates the annex against
#                          the remote, which is expensive on a big dataset, so
#                          this batches that cost rather than paying it per
#                          subject. Use 1 for maximum durability.
#       --remote NAME      Push target (default: origin)
#       --no-push          Save only, never push
#       --dry-run          Print what would be saved/pushed, touch nothing
#   -h, --help
#
# Login-node policy: this does real data movement (annex hashing + push), so
# like execute_local() in scripts/prism_local.py it refuses to run on a bare
# SLURM login node. Submit it via sbatch (see the generated wrapper alongside
# your cohort's other scripts), or get an allocation first. --dry-run is exempt.
set -uo pipefail

DATASET_DIR=""
SUBJECT_LIST=""
JOBS=4
PUSH_EVERY=10
REMOTE="origin"
DO_PUSH=true
DRY_RUN=false

log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
warn() { echo "[WARN] $*" >&2; }
die()  { echo "[ERROR] $*" >&2; exit 1; }

usage() { grep '^#' "$0" | sed '1d;s/^# \{0,1\}//'; exit 0; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        -d|--dataset)   DATASET_DIR="$2"; shift 2 ;;
        -s|--subjects)  SUBJECT_LIST="$2"; shift 2 ;;
        -J|--jobs)      JOBS="$2"; shift 2 ;;
        --push-every)   PUSH_EVERY="$2"; shift 2 ;;
        --remote)       REMOTE="$2"; shift 2 ;;
        --no-push)      DO_PUSH=false; shift ;;
        --dry-run)      DRY_RUN=true; shift ;;
        -h|--help)      usage ;;
        *) die "Unknown option: $1" ;;
    esac
done

[[ -n "$DATASET_DIR"  ]] || die "-d/--dataset is required"
[[ -n "$SUBJECT_LIST" ]] || die "-s/--subjects is required"
[[ -d "$DATASET_DIR"  ]] || die "Dataset dir not found: ${DATASET_DIR}"
[[ -f "$SUBJECT_LIST" ]] || die "Subject list not found: ${SUBJECT_LIST}"
[[ -d "${DATASET_DIR}/.git" ]] || die "Not a git/datalad dataset: ${DATASET_DIR}"

# ── Login-node guard ──────────────────────────────────────────────────────────
# Same rule (and same reasoning) as execute_local() in scripts/prism_local.py:
# sbatch on PATH means we're on a SLURM cluster; no SLURM_JOB_ID means we hold
# no allocation, i.e. this is a bare login node. Real annex hashing and a push
# of a multi-TB-scale dataset must never run there.
if ! $DRY_RUN && command -v sbatch >/dev/null 2>&1 \
   && [[ -z "${SLURM_JOB_ID:-}${SLURM_JOBID:-}" ]]; then
    cat >&2 <<'GUARD'
[ERROR] Refusing to run on what looks like a SLURM login node.

  This script does real data movement (git-annex hashing + datalad push).
  Per the cluster policy in CLAUDE.md, that must not run on the login node.

  Instead, either:
    • submit it as a batch job:   sbatch <your wrapper>.sh
    • or get an allocation first: salloc ... / srun ...
    • or, to just inspect what it would do:  --dry-run
GUARD
    exit 1
fi

# Prefer the dedicated datalad-slurm venv's datalad/git-annex if present --
# compute nodes here can run a different system python3 than the login node,
# which silently breaks venvs that symlink to system python (see the same
# note in submit_bids_cohort.sh's finish-job template).
REPO_DIR="$(dirname "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")"
if [[ -x "${REPO_DIR}/.datalad-slurm-venv/bin/datalad" ]]; then
    export PATH="${REPO_DIR}/.datalad-slurm-venv/bin:$PATH"
    DATALAD_BIN="${REPO_DIR}/.datalad-slurm-venv/bin/datalad"
else
    DATALAD_BIN="$(command -v datalad)" || die "datalad not found on PATH"
fi

cd "$DATASET_DIR" || die "Cannot cd to ${DATASET_DIR}"

# sed (per-line) rather than `tr -d '[:space:]'` (whole-stream): tr strips the
# newlines too, collapsing every subject onto a single line.
mapfile -t SUBJECTS < <(sed 's/[[:space:]]//g' "$SUBJECT_LIST" | grep -v '^$')
[[ ${#SUBJECTS[@]} -gt 0 ]] || die "Subject list is empty: ${SUBJECT_LIST}"

log "Dataset:  ${DATASET_DIR}"
log "Subjects: ${#SUBJECTS[@]} from ${SUBJECT_LIST}"
log "Push:     $($DO_PUSH && echo "to ${REMOTE} every ${PUSH_EVERY} subject(s)" || echo "disabled")"
$DRY_RUN && log "DRY RUN -- nothing will be modified"

saved=0 skipped=0 failed=0 since_push=0 push_failures=0

# Disable datalad's SSH connection multiplexing for pushes: $HOME (and its
# control-socket cache under ~/.cache/datalad/sockets/) is NFS-shared across
# compute nodes, so a socket another node created is a dead reference here and
# connecting to it can hang indefinitely rather than failing fast. Confirmed
# incident: two 129/freesurfer pushes hung for hours at a near-zero byte count.
export DATALAD_SSH_MULTIPLEX__CONNECTIONS=false

do_push() {
    local label="$1"
    if ! $DO_PUSH; then return 0; fi
    if $DRY_RUN; then log "[DRY-RUN] datalad push --to ${REMOTE}  (${label})"; return 0; fi
    log "Pushing to ${REMOTE} (${label})..."
    if "$DATALAD_BIN" push --to "$REMOTE"; then
        log "Push OK (${label})"
    else
        warn "Push FAILED (${label}) -- continuing; a later push should carry it"
        push_failures=$((push_failures + 1))
    fi
}

for subject in "${SUBJECTS[@]}"; do
    # All top-level dirs belonging to this subject (base + cross + long).
    mapfile -t paths < <(compgen -G "${subject}*" 2>/dev/null || true)
    if [[ ${#paths[@]} -eq 0 ]]; then
        warn "${subject}: no matching paths in dataset -- skipping"
        skipped=$((skipped + 1))
        continue
    fi

    # Cheap, path-scoped cleanliness check: if git reports nothing for these
    # paths they're already committed (previous run, or a slurm-finish that
    # did land), so there is nothing to do.
    if [[ -z "$(git status --porcelain -- "${paths[@]}" 2>/dev/null)" ]]; then
        log "${subject}: already committed (${#paths[@]} paths) -- skipping"
        skipped=$((skipped + 1))
        continue
    fi

    if $DRY_RUN; then
        log "[DRY-RUN] datalad save -J ${JOBS} -m '...' -- ${paths[*]}"
        saved=$((saved + 1))
        continue
    fi

    log "${subject}: saving ${#paths[@]} path(s)..."
    if "$DATALAD_BIN" save -J "$JOBS" \
            -m "Incremental save: ${subject}" \
            -- "${paths[@]}"; then
        saved=$((saved + 1))
        since_push=$((since_push + 1))
        log "${subject}: saved (${saved}/${#SUBJECTS[@]})"
    else
        warn "${subject}: save FAILED -- continuing with the next subject"
        failed=$((failed + 1))
        continue
    fi

    if [[ "$since_push" -ge "$PUSH_EVERY" ]]; then
        do_push "after ${subject}"
        since_push=0
    fi
done

# Catch-all for everything that isn't a subject dir: FreeSurfer's shared
# templates (fsaverage, lh.EC_average, rh.EC_average), .slurm_logs, and any
# loose report files a BIDS app drops at the dataset root. This one save IS
# unscoped -- but it runs exactly once, after the per-subject saves have
# already committed the bulk, so its full-tree scan is paid a single time.
if $DRY_RUN; then
    log "[DRY-RUN] datalad save -J ${JOBS} -m 'Incremental save: dataset root' ."
elif [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
    log "Saving remaining dataset-root files (templates, logs, reports)..."
    "$DATALAD_BIN" save -J "$JOBS" -m "Incremental save: dataset root leftovers" . \
        || warn "Dataset-root save failed"
else
    log "Dataset root already clean -- nothing left to save"
fi

# Always finish with a push so the last partial group and the root save land.
do_push "final"

log "── Summary ─────────────────────────────"
log "Saved:   ${saved}"
log "Skipped: ${skipped} (already committed / no paths)"
log "Failed:  ${failed}"
$DO_PUSH && log "Push failures: ${push_failures}"

if ! $DRY_RUN && $DO_PUSH && [[ "$push_failures" -eq 0 ]]; then
    # Same verification the cohort finish jobs do: `datalad push` can exit 0
    # without the ref actually landing (rejected update, partial annex
    # transfer) -- both are only visible in datalad's JSON result stream.
    current_branch=$(git symbolic-ref --short HEAD 2>/dev/null || echo "")
    local_head=$(git rev-parse HEAD 2>/dev/null || echo "")
    remote_head=$(timeout 60 git ls-remote "$REMOTE" "refs/heads/${current_branch}" 2>/dev/null | cut -f1)
    if [[ -n "$local_head" && "$remote_head" != "$local_head" ]]; then
        warn "Push verification: local HEAD (${local_head}) != ${REMOTE}/${current_branch} (${remote_head:-unreachable})"
        failed=$((failed + 1))
    else
        log "Push verified: ${REMOTE}/${current_branch} == ${local_head}"
    fi
fi

[[ "$failed" -eq 0 && "$push_failures" -eq 0 ]] || exit 1
log "Done."
