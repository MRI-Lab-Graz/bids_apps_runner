#!/bin/bash
#SBATCH --job-name=commit_staged_subregions_134
#SBATCH --partition=hpc
#SBATCH --time=00:30:00
#SBATCH --mem=4G
#SBATCH --cpus-per-task=1
#SBATCH --output=/cl_tmp/mrilabgraz/bids_apps_runner_cohort_logs/134/logs/cohort/commit_staged_subregions-%j.out
#SBATCH --error=/cl_tmp/mrilabgraz/bids_apps_runner_cohort_logs/134/logs/cohort/commit_staged_subregions-%j.err
#
# Recovery for the job-5665591 empty-commit incident (2026-09-22).
#
# What happened: the subregion segmentation array job's `git annex add` DID
# succeed -- 7721 subfield output files across 114 subjects are staged in the
# dataset's index right now -- but its commit step never used that staged
# state, so all 132 "segment_subregions: sub-XXX" commits are empty
# (`git diff-tree` reports 0 files changed) and nothing is reachable via
# `datalad get` on any clone.
#
# Why this script instead of re-saving: nothing needs to be re-added or
# re-hashed. The fix is a commit that actually uses the already-staged index.
# A plain `git commit` won't do, because the index ALSO carries 95302 `T`
# (typechange) and 182 `D` divergences of unrelated//pre-existing origin that
# must NOT be committed; and `git commit -- <pathspec>` would re-read those
# paths from the WORKING TREE, re-invoking git-annex's smudge/clean filters.
# On this dataset that never finishes: a plain `git status` scoped to ONE
# subject sat at 0% CPU for 4+ hours. Most likely cause is not a deadlock but
# sheer cost -- ~95300 annexed files are sitting unlocked, so the clean filter
# has to hash tens of GB of .mgz over NFS to decide whether content changed
# (0% CPU is I/O wait, not a stall). scripts/relock_unlocked_annex.sh tests
# that explanation directly by re-locking and re-running the same check.
#
# So the commit is built purely from git objects, via plumbing:
#   temp index <- HEAD, apply ONLY the staged subregion additions,
#   write-tree -> commit-tree -> update-ref.
# The working tree is never read and no content filter is ever invoked, so
# this cannot hang. Before moving the branch it verifies that the candidate
# tree differs from HEAD by exactly those additions and nothing else, and it
# saves a backup ref so the move is trivially reversible.
#
# Usage:
#   sbatch scripts/commit_staged_subregions.sh              # DRY RUN (verify only)
#   sbatch scripts/commit_staged_subregions.sh --commit     # actually commit
# Or under an allocation: salloc ... ; scripts/commit_staged_subregions.sh [--commit]
# Pushing is deliberately NOT done here -- see the note printed at the end.

set -uo pipefail

DATASET="${DATASET:-/cl_tmp/mrilab/134/derivatives/freesurfer}"
BRANCH="${BRANCH:-derivatives}"
REPO_DIR="${REPO_DIR:-/usr/people/mrilabgraz/github/bids_apps_runner}"

DO_COMMIT=false
[[ "${1:-}" == "--commit" ]] && DO_COMMIT=true

die() { echo "[ERROR] $*" >&2; exit 1; }

# Only the venv's git-annex works; ~/.local/bin/git-annex is a broken Python
# shim (ModuleNotFoundError: No module named 'git_annex'). Nothing here should
# invoke git-annex at all, but keep PATH sane in case git consults a filter.
[[ -x "${REPO_DIR}/.datalad-slurm-venv/bin/git-annex" ]] \
    && export PATH="${REPO_DIR}/.datalad-slurm-venv/bin:$PATH"

# Login-node policy (CLAUDE.md): even read-only git on this dataset is real
# work. Plumbing-only or not, this does not run on a bare login node.
if command -v sbatch >/dev/null 2>&1 && [[ -z "${SLURM_JOB_ID:-}${SLURM_JOBID:-}" ]]; then
    die "Refusing to run on a bare login node. Use sbatch, or salloc/srun first."
fi

cd "$DATASET" || die "cannot cd to ${DATASET}"

HEAD_SHA=$(git rev-parse HEAD) || die "git rev-parse HEAD failed"
CUR_BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo DETACHED)

echo "========================================"
echo "Node:     $(hostname)"
echo "Started:  $(date)"
echo "Dataset:  ${DATASET}"
echo "Branch:   ${CUR_BRANCH} (expected ${BRANCH})"
echo "HEAD:     ${HEAD_SHA}"
echo "Mode:     $($DO_COMMIT && echo 'COMMIT' || echo 'DRY RUN')"
echo "========================================"

[[ "$CUR_BRANCH" == "$BRANCH" ]] || die "on branch '${CUR_BRANCH}', expected '${BRANCH}'"

WORK=$(mktemp -d) || die "mktemp failed"
trap 'rm -rf "$WORK"' EXIT
APATHS="${WORK}/apaths"
STAGEINFO="${WORK}/stageinfo"
TMPINDEX="${WORK}/index"
TREEDIFF="${WORK}/treediff"

SUBREGION_RE='hippoAmygLabels|hippoSfVolumes|amygNucVolumes|ThalamicNuclei|brainstemSs'

echo "[1/5] collecting staged additions (index vs HEAD)..."
git -c core.quotePath=false diff-index --cached --name-status HEAD \
    | awk -F'\t' '$1=="A"{print $2}' | LC_ALL=C sort > "$APATHS"
N_ADD=$(wc -l < "$APATHS")
echo "      staged additions: ${N_ADD}"
[[ "$N_ADD" -gt 0 ]] || die "no staged additions found -- nothing to recover"

N_OTHER=$(grep -vcE "$SUBREGION_RE" "$APATHS" || true)
echo "      of which NOT subregion outputs: ${N_OTHER}"
if [[ "$N_OTHER" -ne 0 ]]; then
    echo "      offending sample:" >&2
    grep -vE "$SUBREGION_RE" "$APATHS" | head -20 >&2
    die "refusing to commit: ${N_OTHER} staged additions are not subregion outputs"
fi
echo "      subjects covered: $(grep -oE '^sub-134[0-9]+' "$APATHS" | LC_ALL=C sort -u | wc -l)"

echo "[2/5] extracting index entries for exactly those paths..."
git -c core.quotePath=false ls-files --cached --stage \
    | awk -F'\t' 'NR==FNR{want[$0]=1; next} ($2 in want){print}' "$APATHS" - > "$STAGEINFO"
N_INFO=$(wc -l < "$STAGEINFO")
echo "      index entries collected: ${N_INFO}"
[[ "$N_INFO" -eq "$N_ADD" ]] || die "collected ${N_INFO} entries for ${N_ADD} paths -- refusing"

echo "[3/5] building candidate tree (HEAD + those entries only, no working tree read)..."
GIT_INDEX_FILE="$TMPINDEX" git read-tree "$HEAD_SHA" || die "read-tree failed"
GIT_INDEX_FILE="$TMPINDEX" git update-index --index-info < "$STAGEINFO" || die "update-index failed"
NEW_TREE=$(GIT_INDEX_FILE="$TMPINDEX" git write-tree) || die "write-tree failed"
echo "      candidate tree: ${NEW_TREE}"

echo "[4/5] verifying candidate tree differs from HEAD by ONLY those additions..."
git diff-tree -r --name-status "$HEAD_SHA" "$NEW_TREE" > "$TREEDIFF"
N_TREE_ADD=$(awk -F'\t' '$1=="A"' "$TREEDIFF" | wc -l)
N_TREE_BAD=$(awk -F'\t' '$1!="A"' "$TREEDIFF" | wc -l)
echo "      additions:            ${N_TREE_ADD}"
echo "      non-addition changes: ${N_TREE_BAD}"
if [[ "$N_TREE_BAD" -ne 0 ]]; then
    awk -F'\t' '$1!="A"' "$TREEDIFF" | head -20 >&2
    die "candidate tree contains non-addition changes -- refusing"
fi
[[ "$N_TREE_ADD" -eq "$N_ADD" ]] \
    || die "tree has ${N_TREE_ADD} additions but ${N_ADD} were staged -- refusing"
echo "      OK: tree == HEAD + ${N_ADD} subregion files, nothing else"

if ! $DO_COMMIT; then
    echo "[5/5] DRY RUN -- verified, nothing written."
    echo
    echo "Re-run with --commit to create the commit and move ${BRANCH}."
    echo "Finished: $(date)"
    exit 0
fi

echo "[5/5] committing..."
BACKUP_REF="refs/backup/${BRANCH}-pre-subregion-recovery"
git update-ref "$BACKUP_REF" "$HEAD_SHA" || die "could not write backup ref"
echo "      backup ref: ${BACKUP_REF} -> ${HEAD_SHA}"

NEW_COMMIT=$(git commit-tree "$NEW_TREE" -p "$HEAD_SHA" <<EOF
Recover subregion segmentation outputs staged by job 5665591

The freesurfer_subregions_134 array job segmented hippocampal/amygdalar,
thalamic and brainstem subregions for 114 subjects and successfully staged
its ${N_ADD} output files via git annex add, but its commit step never used
the staged index: all 132 "segment_subregions: sub-XXX" commits it produced
are empty, so none of the results were reachable from any clone.

This commit is built from the already-staged index entries via plumbing
(read-tree/update-index/write-tree/commit-tree), so no content was re-hashed
and no working-tree read or annex filter was involved. It adds exactly those
${N_ADD} files and changes nothing else; pre-existing typechange/deletion
divergence in the index is deliberately left untouched.

Previous branch tip saved at ${BACKUP_REF}.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
) || die "commit-tree failed"

git update-ref "refs/heads/${BRANCH}" "$NEW_COMMIT" "$HEAD_SHA" \
    || die "update-ref failed (branch moved concurrently?)"

echo "      new commit: ${NEW_COMMIT}"
echo
echo "=== post-commit verification ==="
echo "branch tip: $(git rev-parse "refs/heads/${BRANCH}")"
echo "files changed vs previous tip: $(git diff-tree -r --name-only "$HEAD_SHA" "$NEW_COMMIT" | wc -l)"
echo "subregion files now tracked in HEAD: $(git ls-tree -r HEAD --name-only | grep -cE "$SUBREGION_RE")"
echo
echo "NOT pushed. To publish (from an allocation, not the login node):"
echo "    cd ${DATASET} && git push origin ${BRANCH} git-annex"
echo "To undo this commit:"
echo "    git update-ref refs/heads/${BRANCH} ${HEAD_SHA}"
echo
echo "Finished: $(date)"
