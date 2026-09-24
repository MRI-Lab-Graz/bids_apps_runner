#!/bin/bash
#SBATCH --job-name=check_fmriprep_all
#SBATCH --partition=hpc
#SBATCH --time=02:00:00
#SBATCH --mem=8G
#SBATCH --cpus-per-task=2
#SBATCH --output=/cl_tmp/mrilabgraz/openneuro/logs/check_fmriprep_all-%j.out
#SBATCH --error=/cl_tmp/mrilabgraz/openneuro/logs/check_fmriprep_all-%j.err
#
# Runs check_fmriprep_complete.py across every OpenNeuro dataset whose fMRIPrep
# jobs have reported COMPLETED, because a COMPLETED job state is not evidence
# the output exists: fMRIPrep writes its .html report before the functional
# pipeline necessarily finishes (2026-09-17, ds003849 -- two subjects had
# reports but no desc-preproc_bold.nii.gz at all). The checker opens the real
# output with nibabel and compares volume counts against the raw BOLD.
#
# One job for all datasets rather than one srun each: opening many NIfTIs over
# NFS is real I/O and belongs on a compute node, and batching keeps the node
# footprint to a single allocation.
#
# nibabel is deliberately NOT a repo dependency -- the checker's own --help
# documents `uv run --with nibabel`, which is what this uses, so no persistent
# install is required on any node.

set -uo pipefail

REPO_DIR="${REPO_DIR:-/usr/people/mrilabgraz/github/bids_apps_runner}"
BASE="${BASE:-/cl_tmp/mrilabgraz/openneuro}"
OUTDIR="${OUTDIR:-${BASE}/logs/fmriprep_completeness}"
DATASETS=("${@:-}")
[[ -z "${DATASETS[0]:-}" ]] && DATASETS=(ds004592 ds005339 ds004466 ds006707 ds003404)

mkdir -p "$OUTDIR"
cd "$REPO_DIR" || { echo "cannot cd to $REPO_DIR" >&2; exit 1; }

echo "========================================"
echo "Node:     $(hostname)"
echo "Started:  $(date)"
echo "Datasets: ${DATASETS[*]}"
echo "Reports:  ${OUTDIR}"
echo "========================================"

rc=0
for ds in "${DATASETS[@]}"; do
    fp="${BASE}/derivatives/${ds}/fmriprep"
    raw="${BASE}/data/${ds}"
    echo
    echo "=================== ${ds} ==================="
    if [[ ! -d "$fp" || ! -d "$raw" ]]; then
        echo "  SKIP: missing fmriprep dir or raw dir"
        continue
    fi
    if uv run --with nibabel scripts/check_fmriprep_complete.py \
            "$ds" "$fp" "$raw" \
            --report-json "${OUTDIR}/${ds}.json"; then
        echo "  checker exited 0 for ${ds}"
    else
        echo "  checker exited non-zero for ${ds} (incomplete subjects found, or an error)"
        rc=1
    fi
done

echo
echo "========================================"
echo "Per-dataset JSON reports in ${OUTDIR}"
echo "Finished: $(date)"
echo "========================================"
exit "$rc"
