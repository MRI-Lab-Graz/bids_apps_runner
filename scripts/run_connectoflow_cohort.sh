#!/bin/bash
#SBATCH --job-name=run_connectoflow_cohort
#SBATCH --partition=hpc
#SBATCH --time=06:00:00
#SBATCH --mem=8G
#SBATCH --cpus-per-task=2
#SBATCH --output=/cl_tmp/mrilabgraz/openneuro/logs/connectoflow/run_cohort-%j.out
#SBATCH --error=/cl_tmp/mrilabgraz/openneuro/logs/connectoflow/run_cohort-%j.err
#
# Drives connectoflow across all 7 megastudy datasets: `setup` (idempotent with
# --resume) then `submit`, which dispatches one SLURM array task per subject
# plus a dependent finish job per dataset.
#
# WHY THIS WRAPPER EXISTS. connectoflow's own submit_connectoflow_cohort.sh has
# NO login-node guard, and its cmd_setup() runs datalad operations directly --
# creating project superdatasets and one subdataset per subject (~530 of them).
# Run bare, that is exactly the login-node abuse this account has already been
# warned about twice. It is also invisible to
# bids_apps_runner/scripts/hooks/block_unscheduled_heavy_cmds.sh, because the
# command string shows neither /cl_tmp nor `datalad` -- a real instance of that
# hook's documented "command string only, guardrail not sandbox" limit. So the
# whole thing runs inside a job.
#
# connectoflow is CPU-only: no --gres/GPU anywhere in its scripts or configs.
# The 2026-09-23 slurm_nohog GPU warning came from FastSurfer, not this, so
# feedback-gpu-cleanenv-audit does not gate this run.
#
# The 14/30 failures of the 2026-09-17 demo run are already fixed: that run
# dispatched via plain `sbatch`, so `datalad slurm-finish` died with "no such
# table: open_jobs" for every dataset even though the compute and per-subject
# saves had genuinely succeeded. The script now dispatches via `datalad
# slurm-schedule`, which registers the job in that bookkeeping DB, and the
# worktree HEAD (9382dc9) is the regression test for precisely that fix.
#
# Usage:
#   sbatch scripts/run_connectoflow_cohort.sh              # setup + submit, all datasets
#   sbatch scripts/run_connectoflow_cohort.sh --dry-run    # print, touch nothing
#   sbatch scripts/run_connectoflow_cohort.sh -d ds006707  # one dataset

set -uo pipefail

CF_DIR="${CF_DIR:-/usr/people/mrilabgraz/github/connectoflow-slurm-worktrees/slurm-array-adaptation}"
CONFIG="${CONFIG:-configs/megastudy_openneuro_connectoflow.json}"
EXTRA=("$@")

die() { echo "[ERROR] $*" >&2; exit 1; }

if command -v sbatch >/dev/null 2>&1 && [[ -z "${SLURM_JOB_ID:-}${SLURM_JOBID:-}" ]]; then
    die "Refusing to run on a bare login node -- setup creates ~530 subdatasets. Use sbatch."
fi

cd "$CF_DIR" || die "connectoflow worktree not found: ${CF_DIR}"
[[ -f "$CONFIG" ]] || die "config not found: ${CONFIG}"
[[ -x "${CF_DIR}/.datalad-slurm-venv/bin/datalad" ]] \
    || die "connectoflow's own .datalad-slurm-venv is missing -- see that script's header for how to build it"

echo "========================================"
echo "Node:     $(hostname)"
echo "Started:  $(date)"
echo "Worktree: ${CF_DIR}  (HEAD $(git rev-parse --short HEAD 2>/dev/null))"
echo "Config:   ${CONFIG}"
echo "Datasets: $(jq -r '.datasets|join(", ")' "$CONFIG")"
echo "Extra:    ${EXTRA[*]:-<none>}"
echo "========================================"

# --resume makes setup skip datasets already set up, so this is safe to re-run.
# As of 2026-09-25, 5 of 7 are set up; ds004466 and ds006707 are not.
echo
echo "[1/2] setup (idempotent via --resume)..."
if ! scripts/submit_connectoflow_cohort.sh setup -c "$CONFIG" --resume "${EXTRA[@]}"; then
    die "setup failed -- not submitting; fix setup first so no half-built project gets an array job"
fi

echo
echo "[2/2] submit (one array task per subject + dependent finish job per dataset)..."
scripts/submit_connectoflow_cohort.sh submit -c "$CONFIG" --resume "${EXTRA[@]}"
rc=$?

echo
echo "========================================"
echo "submit exit: ${rc}"
echo "Track with:  scripts/submit_connectoflow_cohort.sh status -c ${CONFIG}"
echo "Finished: $(date)"
echo "========================================"
exit "$rc"
