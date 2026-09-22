#!/bin/bash
#SBATCH --job-name=push_subregion_recovery_134
#SBATCH --partition=hpc
#SBATCH --time=04:00:00
#SBATCH --mem=4G
#SBATCH --cpus-per-task=2
#SBATCH --output=/cl_tmp/mrilabgraz/bids_apps_runner_cohort_logs/134/logs/cohort/push_subregion_recovery-%j.out
#SBATCH --error=/cl_tmp/mrilabgraz/bids_apps_runner_cohort_logs/134/logs/cohort/push_subregion_recovery-%j.err
#
# Publishes the subregion recovery commit (scripts/commit_staged_subregions.sh)
# to the datalad server, then verifies that the content is actually retrievable
# from there rather than merely claimed to be.
#
# Runs as a job, never on the login node: this is real data movement, the
# category CLAUDE.md's policy names outright (and the 2026-07-29 incident was
# exactly a study-129 push run interactively on IT010128).
#
# SSH connection caching is disabled deliberately. Both datalad's multiplexing
# and git-annex's own sshcaching put control sockets on NFS-shared paths, where
# a socket another node created is a dead reference that can hang a push
# indefinitely instead of failing fast -- two 129/freesurfer pushes hung for
# hours that way, and a 13-day-stale .git/annex/ssh/datalad-server.lock was
# found in this very dataset on 2026-09-22.
#
# The verification step matters because the original finish loop used
# `git annex setpresentkey` to ASSERT that content was present on origin.
# setpresentkey only writes a location-log claim -- it transfers and verifies
# nothing. So a clone could end up with symlinks pointing at content the server
# does not actually hold. This checks a real sample instead of trusting the log.
#
# Usage:
#   sbatch scripts/push_subregion_recovery.sh
#   sbatch scripts/push_subregion_recovery.sh --dry-run   # show what would push

set -uo pipefail

DATASET="${DATASET:-/cl_tmp/mrilab/134/derivatives/freesurfer}"
BRANCH="${BRANCH:-derivatives}"
REMOTE="${REMOTE:-origin}"
REPO_DIR="${REPO_DIR:-/usr/people/mrilabgraz/github/bids_apps_runner}"
SAMPLE_N="${SAMPLE_N:-15}"

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

die() { echo "[ERROR] $*" >&2; exit 1; }

# ~/.local/bin/git-annex is a broken Python shim (ModuleNotFoundError:
# No module named 'git_annex'). Only the venv's binary works, and this script
# genuinely needs a working git-annex for the verification step.
[[ -x "${REPO_DIR}/.datalad-slurm-venv/bin/git-annex" ]] \
    || die "working git-annex not found at ${REPO_DIR}/.datalad-slurm-venv/bin/git-annex"
export PATH="${REPO_DIR}/.datalad-slurm-venv/bin:$PATH"

if command -v sbatch >/dev/null 2>&1 && [[ -z "${SLURM_JOB_ID:-}${SLURM_JOBID:-}" ]]; then
    die "Refusing to run on a bare login node. Use sbatch, or salloc/srun first."
fi

export DATALAD_SSH_MULTIPLEX__CONNECTIONS=false
GIT_ANNEX_OPTS=(-c annex.sshcaching=false)

cd "$DATASET" || die "cannot cd to ${DATASET}"

echo "========================================"
echo "Node:     $(hostname)"
echo "Started:  $(date)"
echo "Dataset:  ${DATASET}"
echo "Remote:   ${REMOTE}"
echo "Mode:     $($DRY_RUN && echo 'DRY RUN' || echo 'PUSH')"
echo "========================================"

echo "git-annex: $(command -v git-annex)"
echo "local  ${BRANCH}: $(git rev-parse "refs/heads/${BRANCH}")"
echo "remote ${BRANCH}: $(git rev-parse "refs/remotes/${REMOTE}/${BRANCH}" 2>/dev/null || echo '<unknown>')"
echo "commits to push: $(git rev-list --count "refs/remotes/${REMOTE}/${BRANCH}..refs/heads/${BRANCH}" 2>/dev/null || echo '?')"

if $DRY_RUN; then
    echo
    echo "[DRY RUN] would run: git ${GIT_ANNEX_OPTS[*]} push ${REMOTE} ${BRANCH} git-annex"
    git push --dry-run "$REMOTE" "$BRANCH" git-annex 2>&1 | head -20
    echo
    echo "Finished: $(date)"
    exit 0
fi

echo
echo "[1/3] pushing git refs (${BRANCH} + git-annex)..."
git "${GIT_ANNEX_OPTS[@]}" push "$REMOTE" "$BRANCH" git-annex \
    || die "git push failed"
echo "      push OK"

echo
echo "[2/3] confirming remote ref moved..."
git fetch "$REMOTE" --quiet || echo "[WARN] fetch failed; remote-tracking ref may be stale below"
LOCAL_TIP=$(git rev-parse "refs/heads/${BRANCH}")
REMOTE_TIP=$(git rev-parse "refs/remotes/${REMOTE}/${BRANCH}" 2>/dev/null || echo "")
echo "      local : ${LOCAL_TIP}"
echo "      remote: ${REMOTE_TIP:-<none>}"
[[ "$LOCAL_TIP" == "$REMOTE_TIP" ]] \
    || die "remote ${BRANCH} is at '${REMOTE_TIP}', expected '${LOCAL_TIP}'"
echo "      OK: remote is at the recovery commit"

echo
echo "[3/3] verifying content is REALLY on ${REMOTE} (not just claimed via setpresentkey)..."
SUBREGION_RE='hippoAmygLabels|hippoSfVolumes|amygNucVolumes|ThalamicNuclei|brainstemSs'
mapfile -t SAMPLE < <(git ls-tree -r HEAD --name-only \
    | grep -E "$SUBREGION_RE" | shuf -n "$SAMPLE_N")
echo "      sampling ${#SAMPLE[@]} files"

missing=0
for f in "${SAMPLE[@]}"; do
    # --in <remote> asks git-annex whether the LOCATION LOG claims presence.
    claimed=$(git annex find --in "$REMOTE" -- "$f" 2>/dev/null | head -1)
    if [[ -z "$claimed" ]]; then
        echo "      [NOT CLAIMED on ${REMOTE}] $f"
        missing=$((missing + 1))
        continue
    fi
    # checkpresentkey actually ASKS the remote, rather than trusting the log.
    key=$(git annex lookupkey -- "$f" 2>/dev/null)
    if [[ -z "$key" ]]; then
        echo "      [NO KEY] $f"
        missing=$((missing + 1))
    elif git "${GIT_ANNEX_OPTS[@]}" annex checkpresentkey "$key" "$REMOTE" >/dev/null 2>&1; then
        echo "      [verified on ${REMOTE}] $f"
    else
        echo "      [CLAIMED BUT ABSENT on ${REMOTE}] $f"
        missing=$((missing + 1))
    fi
done

echo
if [[ "$missing" -eq 0 ]]; then
    echo "RESULT: all ${#SAMPLE[@]} sampled files verified present on ${REMOTE}."
    echo "The analysis server can now run:"
    echo "    datalad update -r --how=merge && datalad get <paths>"
else
    echo "RESULT: ${missing}/${#SAMPLE[@]} sampled files are NOT actually on ${REMOTE}."
    echo "The git history is published, but annex CONTENT still needs transferring:"
    echo "    git -c annex.sshcaching=false annex copy --to ${REMOTE} --in here \\"
    echo "        \$(git ls-tree -r HEAD --name-only | grep -E '${SUBREGION_RE}')"
    echo "Run that as its own job too -- it moves real data."
fi

echo
echo "Finished: $(date)"
