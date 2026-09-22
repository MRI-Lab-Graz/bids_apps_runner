#!/bin/bash
#SBATCH --job-name=relock_unlocked_annex_134
#SBATCH --partition=hpc
#SBATCH --time=12:00:00
#SBATCH --mem=8G
#SBATCH --cpus-per-task=4
#SBATCH --output=/cl_tmp/mrilabgraz/bids_apps_runner_cohort_logs/134/logs/cohort/relock_unlocked_annex-%j.out
#SBATCH --error=/cl_tmp/mrilabgraz/bids_apps_runner_cohort_logs/134/logs/cohort/relock_unlocked_annex-%j.err
#
# Re-locks annexed files left sitting unlocked (symlink -> regular file,
# content unchanged) in a dataset's working tree.
#
# Why: study 134's freesurfer derivatives carries ~95300 such paths, which show
# up as `T` (typechange) between index and HEAD. Two consequences:
#   * `git status` becomes unusable -- it must run every unlocked annexed file
#     through git-annex's clean filter to decide whether content changed, i.e.
#     hash tens of GB of .mgz over NFS. That is why a scoped `git status` on a
#     SINGLE subject sat at 0% CPU in I/O wait for 4+ hours on 2026-09-22 and
#     took down three recovery job attempts. It was never a deadlock.
#   * any unscoped `datalad save` would commit a mass typechange.
# Precedent: the 2026-09-02 incident left 142592 files unlocked when an
# interactive slurm-schedule was killed mid-run, and was recovered with exactly
# this -- `git annex lock` is safe, the content is identical either way.
#
# `git annex lock` is deliberately called WITHOUT --force: on a file whose
# working-tree content genuinely differs from the recorded key it refuses
# rather than discarding the difference. Refusals are counted and reported, not
# suppressed -- if any appear, inspect them before doing anything else, because
# they are files with real uncommitted modifications.
#
# The 182 `D` divergences are reported but NOT acted on, and they are not
# damage: they are sub-134001/002/003's subregion files (7 session-instances x
# 26 files), present in HEAD but missing from the index -- the pre-existing
# index drift this dataset's tooling warns about. They are intact in HEAD, and
# the 2026-09-22 recovery commit seeded its tree from HEAD, so nothing
# committed them as deletions. Re-syncing the index to HEAD for those paths is
# a separate decision, deliberately out of scope here.
#
# Usage:
#   sbatch scripts/relock_unlocked_annex.sh           # REPORT ONLY (default)
#   sbatch scripts/relock_unlocked_annex.sh --lock    # actually re-lock

set -uo pipefail

DATASET="${DATASET:-/cl_tmp/mrilab/134/derivatives/freesurfer}"
REPO_DIR="${REPO_DIR:-/usr/people/mrilabgraz/github/bids_apps_runner}"
BATCH="${BATCH:-500}"
VERIFY_SUBJECT="${VERIFY_SUBJECT:-sub-134006}"

DO_LOCK=false
[[ "${1:-}" == "--lock" ]] && DO_LOCK=true

die() { echo "[ERROR] $*" >&2; exit 1; }

# ~/.local/bin/git-annex is a venv keyed to each node's SYSTEM python3 and is
# broken wherever that is not 3.10 (ModuleNotFoundError: No module named
# 'git_annex'). Use the repo venv, whose python is uv-managed and in home.
[[ -x "${REPO_DIR}/.datalad-slurm-venv/bin/git-annex" ]] \
    || die "working git-annex not found at ${REPO_DIR}/.datalad-slurm-venv/bin/git-annex"
export PATH="${REPO_DIR}/.datalad-slurm-venv/bin:$PATH"

if command -v sbatch >/dev/null 2>&1 && [[ -z "${SLURM_JOB_ID:-}${SLURM_JOBID:-}" ]]; then
    die "Refusing to run on a bare login node. Use sbatch, or salloc/srun first."
fi

cd "$DATASET" || die "cannot cd to ${DATASET}"

echo "========================================"
echo "Node:        $(hostname)"
echo "Started:     $(date)"
echo "Dataset:     ${DATASET}"
echo "Mode:        $($DO_LOCK && echo 'LOCK' || echo 'REPORT ONLY')"
echo "git-annex:   $(command -v git-annex)"
# Free diagnostic for the ~/.local/bin/git-annex breakage: records this node's
# system python, so a node where it differs from 3.10 is visible in the log.
echo "node /usr/bin/python3: $(/usr/bin/python3 --version 2>&1 || echo 'MISSING')"
echo "========================================"

WORK=$(mktemp -d) || die "mktemp failed"
trap 'rm -rf "$WORK"' EXIT
UNLOCKED="${WORK}/unlocked"
DELETED="${WORK}/deleted"

echo "[1/4] finding unlocked paths (HEAD=symlink, index=regular file)..."
# --raw gives ":<srcmode> <dstmode> <srcsha> <dstsha> <status>\t<path>", so the
# mode transition is explicit rather than inferred from the T letter alone.
git -c core.quotePath=false diff-index --cached --raw HEAD > "${WORK}/raw"
# Both 100644 and 100755: an unlocked annexed file keeps whatever exec bit it
# had, and study 134 carries 287 paths at 100755 alongside 95015 at 100644.
# Matching only 100644 (the first version of this script did) silently left
# those 287 behind.
awk -F'\t' '$1 ~ /^:120000 10(0644|0755)/ && $1 ~ /T$/ {print $2}' "${WORK}/raw" > "$UNLOCKED"
awk -F'\t' '$1 ~ /D$/ {print $2}' "${WORK}/raw" > "$DELETED"
N_UNLOCKED=$(wc -l < "$UNLOCKED")
N_DELETED=$(wc -l < "$DELETED")
N_RAW=$(wc -l < "${WORK}/raw")

echo "      total index/HEAD divergences : ${N_RAW}"
echo "      unlocked (120000 -> 100644/755): ${N_UNLOCKED}"
echo "      deletions (reported only)    : ${N_DELETED}"
echo
echo "      other transitions (NOT touched by this script):"
awk -F'\t' '!($1 ~ /^:120000 10(0644|0755)/ && $1 ~ /T$/) {split($1,a," "); print a[1], a[2], a[5]}' "${WORK}/raw" \
    | sort | uniq -c | sort -rn | head -10

if [[ "$N_DELETED" -gt 0 ]]; then
    echo
    echo "      deletion sample (investigate separately -- this script does not act on them):"
    head -5 "$DELETED" | sed 's/^/        /'
fi

if [[ "$N_UNLOCKED" -eq 0 ]]; then
    echo
    echo "[2/4] nothing unlocked -- skipping lock step."
else
    if ! $DO_LOCK; then
        echo
        echo "[2/4] REPORT ONLY -- would re-lock ${N_UNLOCKED} path(s) in batches of ${BATCH}."
        echo "      sample:"
        head -5 "$UNLOCKED" | sed 's/^/        /'
    else
        echo
        echo "[2/4] re-locking ${N_UNLOCKED} path(s) in batches of ${BATCH}..."
        # No --force: a genuinely modified file must be refused, not reverted.
        batch_no=0
        failed_batches=0
        split -l "$BATCH" -d "$UNLOCKED" "${WORK}/chunk."
        total_chunks=$(ls "${WORK}"/chunk.* 2>/dev/null | wc -l)
        for chunk_file in "${WORK}"/chunk.*; do
            batch_no=$((batch_no + 1))
            # xargs rather than `git annex lock --batch`: paths come one per
            # line and xargs -d '\n' handles them without relying on lock's
            # own stdin mode.
            if xargs -a "$chunk_file" -d '\n' git annex lock -- >> "${WORK}/lock.log" 2>&1; then
                echo "      batch ${batch_no}/${total_chunks} OK ($(date +%H:%M:%S))"
            else
                failed_batches=$((failed_batches + 1))
                echo "      batch ${batch_no}/${total_chunks} had refusals ($(date +%H:%M:%S))"
            fi
        done
        echo "      batches with refusals: ${failed_batches}"
        REFUSED=$(grep -ciE 'cannot|fail|error|modified' "${WORK}/lock.log" 2>/dev/null || echo 0)
        echo "      lines in lock log suggesting refusal: ${REFUSED}"
        if [[ "$REFUSED" -gt 0 ]]; then
            echo "      sample:"
            grep -iE 'cannot|fail|error|modified' "${WORK}/lock.log" | head -10 | sed 's/^/        /'
        fi
    fi
fi

echo
echo "[3/4] re-counting divergences..."
git -c core.quotePath=false diff-index --cached --raw HEAD \
    | awk -F'\t' '$1 ~ /^:120000 100644/ && $1 ~ /T$/' | wc -l \
    | sed 's/^/      unlocked remaining: /'

echo
echo "[4/4] git status check (scoped to ${VERIFY_SUBJECT}, 120s cap)..."
# This is the check that sat at 0% CPU for 4+ hours. If re-locking removed the
# underlying cost it now returns promptly. Capped so it cannot hang the job.
# Skipped by default in report mode: nothing was re-locked, so it would just
# burn the full 120s re-establishing a baseline we already know. Set
# VERIFY_STATUS=1 to run it anyway.
if ! $DO_LOCK && [[ "${VERIFY_STATUS:-0}" != "1" ]]; then
    echo "      skipped in report mode (nothing re-locked; set VERIFY_STATUS=1 to force)"
else
    mapfile -t vpaths < <(compgen -G "${VERIFY_SUBJECT}*" 2>/dev/null || true)
    if [[ ${#vpaths[@]} -eq 0 ]]; then
        echo "      no paths match ${VERIFY_SUBJECT}* -- skipped"
    else
        start=$SECONDS
        if timeout 120 git status --porcelain -- "${vpaths[@]}" > "${WORK}/status.out" 2>&1; then
            echo "      COMPLETED in $((SECONDS - start))s ($(wc -l < "${WORK}/status.out") lines)"
            $DO_LOCK && echo "      => issue #2 was caused by the unlocked files; re-locking fixed it"
        else
            echo "      did NOT finish within 120s"
            $DO_LOCK && echo "      => re-locking did NOT fix issue #2; the cost is something else"
        fi
    fi
fi

echo
echo "Finished: $(date)"
