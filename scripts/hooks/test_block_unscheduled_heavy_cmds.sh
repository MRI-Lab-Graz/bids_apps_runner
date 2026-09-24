#!/usr/bin/env bash
# Tests for the PreToolUse hook in block_unscheduled_heavy_cmds.sh.
#
# The allow-cases matter as much as the block-cases: a hook that over-blocks
# makes ordinary work impossible and invites people to route around it, which
# would defeat the point.
set -uo pipefail

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/block_unscheduled_heavy_cmds.sh"
fail=0

# The hook short-circuits when it sees an allocation; tests must run as if on a
# bare login node.
unset SLURM_JOB_ID SLURM_JOBID

decision() {
    local out
    out=$(jq -n --arg c "$1" '{tool_name:"Bash", tool_input:{command:$c}}' | bash "$HOOK")
    # The hook prints nothing when it has no opinion, which means "allow".
    [[ -z "$out" ]] && { echo allow; return; }
    printf '%s' "$out" | jq -r '.hookSpecificOutput.permissionDecision // "allow"' 2>/dev/null
}

expect_deny() {
    local got; got=$(decision "$1")
    if [[ "$got" == "deny" ]]; then
        echo "  ok   (blocked) $1"
    else
        echo "  FAIL (expected block, got '$got') $1" >&2
        fail=$((fail + 1))
    fi
}

expect_allow() {
    local got; got=$(decision "$1")
    if [[ "$got" == "allow" ]]; then
        echo "  ok   (allowed) $1"
    else
        echo "  FAIL (expected allow, got '$got') $1" >&2
        fail=$((fail + 1))
    fi
}

echo "MUST BLOCK -- heavy work aimed at a big dataset, unscheduled:"
expect_deny 'cd /cl_tmp/mrilab/134/derivatives/freesurfer && git status --porcelain'
expect_deny 'git -C /cl_tmp/mrilab/134/derivatives/freesurfer status'
expect_deny 'git -C /cl_tmp/mrilab/134/derivatives/freesurfer annex lock -- sub-134006'
expect_deny 'cd /cl_tmp/mrilab/134/derivatives/freesurfer && git add sub-134006'
expect_deny 'cd /cl_tmp/mrilab/134/derivatives/freesurfer && git diff HEAD'
expect_deny 'datalad get /cl_tmp/mrilab/134/derivatives/freesurfer/sub-134006'
expect_deny 'datalad push --to origin'
expect_deny 'apptainer exec -B /cl_tmp:/data image.sif recon-all'
expect_deny 'singularity run image.sif foo'
expect_deny 'find /cl_tmp/mrilab -name "*.mgz"'
expect_deny 'rsync -aR --files-from=list . server:/datalad/mri/x/'
expect_deny 'du -sh /cl_tmp/mrilab/134'

echo
echo "MUST ALLOW -- scheduler use, status checks, cheap reads, repo work:"
# The sanctioned path itself
expect_allow 'sbatch scripts/relock_unlocked_annex.sh --lock'
expect_allow 'srun --partition=hpc --time=00:10:00 --mem=4G scripts/relock_unlocked_annex.sh'
expect_allow 'srun --jobid=5697617 --overlap ps -u me -o pid,time,cmd'
expect_allow 'cd /usr/people/mrilabgraz/github/bids_apps_runner && srun --partition=hpc bash -c "cd /cl_tmp/mrilab/134 && git status"'
# SLURM status
expect_allow 'squeue -u mrilabgraz --format="%.10i %.8T"'
expect_allow 'sacct -j 5697617 --format=JobID,State -P'
expect_allow 'scancel 5697617'
# Ordinary work in the code repo must stay unaffected
expect_allow 'cd /usr/people/mrilabgraz/github/bids_apps_runner && git status --short'
expect_allow 'git log --oneline -5'
expect_allow 'python3 -m pytest tests/ -q'
# Cheap object-graph reads, even on the big dataset
expect_allow 'cd /cl_tmp/mrilab/134/derivatives/freesurfer && git ls-tree -r HEAD --name-only'
expect_allow 'cd /cl_tmp/mrilab/134/derivatives/freesurfer && git diff-index --cached --raw HEAD'
expect_allow 'cd /cl_tmp/mrilab/134/derivatives/freesurfer && git diff-tree --no-commit-id --name-only -r dd8f303'
expect_allow 'cd /cl_tmp/mrilab/134/derivatives/freesurfer && git rev-parse HEAD'
expect_allow 'cd /cl_tmp/mrilab/134/derivatives/freesurfer && git log --grep="job 5665591" --format=%H'
# Reading logs and listing directories under /cl_tmp is not the problem
expect_allow 'tail -40 /cl_tmp/mrilabgraz/bids_apps_runner_cohort_logs/134/logs/cohort/recover-5697630.out'
expect_allow 'ls -la /cl_tmp/mrilab/134/derivatives/freesurfer/.slurm_logs'
expect_allow 'git annex lock --help'

echo
if [[ "$fail" -eq 0 ]]; then
    echo "OK: all hook cases behaved as expected"
else
    echo "FAILED: ${fail} case(s) wrong" >&2
    exit 1
fi
