#!/bin/bash
#SBATCH --job-name=audit_subregion_commits
#SBATCH --partition=hpc
#SBATCH --time=00:30:00
#SBATCH --mem=4G
#SBATCH --cpus-per-task=1
#SBATCH --output=/cl_tmp/mrilabgraz/bids_apps_runner_cohort_logs/134/logs/audit_subregion_commits-%j.out
#SBATCH --error=/cl_tmp/mrilabgraz/bids_apps_runner_cohort_logs/134/logs/audit_subregion_commits-%j.err
#
# Diagnostic only -- makes no changes. For each commit matching --grep in the
# given git-annex dataset, checks via `git diff-tree` whether that commit
# actually changed any files, or whether it's an empty marker commit (the
# datalad-slurm finish_cmd failure mode already seen on study 129/134 -- see
# the 134_subregions incident notes in prism_app_runner.py and
# gui/gui_cohort_routes.py). Run via sbatch, or under `salloc`/`srun` for an
# interactive check -- never bare on the login node (see CLAUDE.md).
#
# Usage: sbatch scripts/audit_subregion_commits.sh [repo_path] [grep_pattern]

set -u

REPO_PATH="${1:-/cl_tmp/mrilab/134/derivatives/freesurfer}"
GREP_PATTERN="${2:-job 5665591}"

echo "========================================"
echo "Repo:       $REPO_PATH"
echo "Grep:       $GREP_PATTERN"
echo "Node:       $(hostname)"
echo "Started:    $(date)"
echo "========================================"

cd "$REPO_PATH" || { echo "FATAL: cannot cd to $REPO_PATH" >&2; exit 1; }

commits=$(git log --grep="$GREP_PATTERN" --format="%H %s")
total=0
empty=0
empty_subjects=()
nonempty_subjects=()

while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    commit="${line%% *}"
    msg="${line#* }"
    subject=$(grep -oE 'sub-[0-9]+(_ses-[0-9]+)?' <<<"$msg" | head -1)
    n=$(git diff-tree --no-commit-id --name-only -r "$commit" | wc -l)
    total=$((total + 1))
    if [[ "$n" -eq 0 ]]; then
        empty=$((empty + 1))
        empty_subjects+=("$subject ($commit)")
    else
        nonempty_subjects+=("$subject: $n files ($commit)")
    fi
done <<<"$commits"

echo
echo "=== Summary ==="
echo "Commits matching \"$GREP_PATTERN\": $total"
echo "Empty (0 files changed):          $empty"
echo "Non-empty:                        $((total - empty))"

echo
echo "=== Empty commits (marker only, no content actually saved) ==="
printf '%s\n' "${empty_subjects[@]}"

echo
echo "=== Non-empty commits (content actually saved) ==="
printf '%s\n' "${nonempty_subjects[@]}"

echo
echo "=== Cross-check: files currently tracked in HEAD's tree matching subfield outputs ==="
git ls-tree -r HEAD --name-only | grep -c 'hippoAmygLabels\.long\.mgz$'
echo "(compare this to 121 subjects x up to 3 sessions = up to 363 possible; each real"
echo " longitudinal timepoint contributes one lh.hippoAmygLabels.long.mgz if truly saved)"

echo
echo "Finished:   $(date)"
