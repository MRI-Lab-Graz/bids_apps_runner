#!/bin/bash
#SBATCH --job-name=recover_subregions_134
#SBATCH --partition=hpc
#SBATCH --time=08:00:00
#SBATCH --mem=8G
#SBATCH --cpus-per-task=4
#SBATCH --output=/cl_tmp/mrilabgraz/bids_apps_runner_cohort_logs/134/logs/cohort/recover_subregions_134-%j.out
#SBATCH --error=/cl_tmp/mrilabgraz/bids_apps_runner_cohort_logs/134/logs/cohort/recover_subregions_134-%j.err
#
# Recovery for the job-5665591 empty-commit incident (2026-09-22): the
# freesurfer_subregions_134 array job (submitted outside submit_bids_cohort.sh,
# no matching finish_134 job) wrote real subregion segmentation output to disk
# for ~112 subjects, but its own per-subject commit step never actually staged
# any of it -- every "segment_subregions: sub-XXX (full cohort batch, job
# 5665591)" commit is empty (git diff-tree confirms 0 files changed). The
# finish loop's `setpresentkey` + push afterwards registered annex-key
# presence without ever tying those keys to a tracked path, so nothing is
# reachable via a normal `datalad get` even though the bytes may already be on
# origin. The real files are still sitting untouched in the working tree here
# (confirmed present, non-broken, real content), so this is a re-save, not a
# re-run: scripts/incremental_datalad_save.sh commits + pushes them one
# subject at a time (idempotent -- anything already clean is skipped).

set -uo pipefail

REPO_DIR="/usr/people/mrilabgraz/github/bids_apps_runner"
DATASET_DIR="/cl_tmp/mrilab/134/derivatives/freesurfer"
SUBJECT_LIST="/cl_tmp/mrilabgraz/bids_apps_runner_cohort_logs/134/logs/cohort/subject_lists/134_recovery_subjects.txt"

echo "========================================"
echo "Node:       $(hostname)"
echo "Started:    $(date)"
echo "Dataset:    $DATASET_DIR"
echo "Subjects:   $SUBJECT_LIST ($(wc -l < "$SUBJECT_LIST") entries)"
echo "========================================"

"${REPO_DIR}/scripts/incremental_datalad_save.sh" \
    -d "$DATASET_DIR" \
    -s "$SUBJECT_LIST" \
    -J 4 \
    --push-every 10

echo
echo "Finished:   $(date)"
