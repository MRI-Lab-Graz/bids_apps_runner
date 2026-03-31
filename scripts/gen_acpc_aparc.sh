#!/bin/bash
# gen_acpc_aparc.sh
#
# Generates sub-XXXXX_space-ACPC_desc-aparc_dseg.nii.gz for all subjects
# by registering the FreeSurfer/FastSurfer aparc+aseg to QSIPrep ACPC space.
#
# This is needed when QSIPrep was run WITHOUT --fs-subjects-dir.
# QSIRecon requires acpc_aparc for the ACT-hsvs workflow transform_aseg node.
#
# Replicates QSIRecon's internal register_fs_to_qsiprep_wf exactly.
#
# Auto-detects FastSurfer vs FreeSurfer per subject:
#   FastSurfer: uses mri/aparc.DKTatlas+aseg.deep.mgz
#   FreeSurfer: uses mri/aparc+aseg.mgz
#
# Tools required (all available on the host):
#   mrconvert   (/usr/local/mrtrix3/bin/mrconvert)
#   antsRegistration  (/opt/ANTs/bin/antsRegistration)
#   antsApplyTransforms  (/opt/ANTs/bin/antsApplyTransforms)

set -euo pipefail

FASTSURFER_DIR="${1:-/data/local/134_AF19/derivatives/fastsurfer}"
QSIPREP_DIR="${2:-/data/local/134_AF19/derivatives/qsiprep}"
NJOBS="${3:-4}"   # parallel subjects
SUBJECT_FILTER="${4:-}"  # optional: run only this subject (e.g. sub-134002)

ANTS=/opt/ANTs/bin
MRCONVERT=/usr/local/mrtrix3/bin/mrconvert
TMPDIR_BASE=/data/local/tmp_big/gen_acpc_aparc
mkdir -p "$TMPDIR_BASE"

process_subject() {
    local subid="$1"
    local fa_dir="$FASTSURFER_DIR/$subid"
    local qp_anat="$QSIPREP_DIR/$subid/anat"

    local brain_mgz="$fa_dir/mri/brain.mgz"
    local acpc_t1w="$qp_anat/${subid}_space-ACPC_desc-preproc_T1w.nii.gz"
    local brain_mask="$qp_anat/${subid}_space-ACPC_desc-brain_mask.nii.gz"
    local out_aparc="$qp_anat/${subid}_space-ACPC_desc-aparcaseg_dseg.nii.gz"

    # Skip if already done
    if [[ -f "$out_aparc" ]]; then
        echo "[SKIP] $subid (already exists)"
        return
    fi

    # Auto-detect FastSurfer vs FreeSurfer by looking for the DKT atlas file
    # FastSurfer produces aparc.DKTatlas+aseg.deep.mgz; FreeSurfer produces aparc+aseg.mgz
    local aparc_mgz
    if [[ -f "$fa_dir/mri/aparc.DKTatlas+aseg.deep.mgz" ]]; then
        aparc_mgz="$fa_dir/mri/aparc.DKTatlas+aseg.deep.mgz"
        echo "[INFO] $subid: FastSurfer detected (using aparc.DKTatlas+aseg.deep.mgz)"
    elif [[ -f "$fa_dir/mri/aparc+aseg.mgz" ]]; then
        aparc_mgz="$fa_dir/mri/aparc+aseg.mgz"
        echo "[INFO] $subid: FreeSurfer detected (using aparc+aseg.mgz)"
    else
        echo "[SKIP] $subid (no aparc+aseg.mgz or aparc.DKTatlas+aseg.deep.mgz — incomplete run)"
        return
    fi

    if [[ ! -f "$acpc_t1w" ]]; then
        echo "[SKIP] $subid (no QSIPrep ACPC T1w)"
        return
    fi

    echo "[START] $subid"
    local tmpdir="$TMPDIR_BASE/$subid"
    mkdir -p "$tmpdir"

    # Step 1: Convert FS brain and aparc to NIfTI with ANTs-compatible strides
    # -strides -1,-2,3 produces LPS orientation expected by ANTs
    "$MRCONVERT" -strides -1,-2,3 "$brain_mgz" "$tmpdir/fs_brain.nii" \
        -force -nthreads 1 -quiet 2>/dev/null
    "$MRCONVERT" -strides -1,-2,3 "$aparc_mgz" "$tmpdir/aparc.nii" \
        -force -nthreads 1 -quiet 2>/dev/null

    # Step 2: Register FS brain to QSIPrep ACPC T1w (rigid registration)
    # Exactly replicates QSIRecon's register_to_qsiprep node.
    # Note: --masks only uses the fixed (QSIPrep) brain mask; no moving mask.
    local mask_args=()
    [[ -f "$brain_mask" ]] && mask_args=(--masks "[$brain_mask]")

    "$ANTS/antsRegistration" \
        --collapse-output-transforms 1 \
        --dimensionality 3 \
        --float 0 \
        --initial-moving-transform "[$acpc_t1w,$tmpdir/fs_brain.nii,1]" \
        --initialize-transforms-per-stage 0 \
        --interpolation BSpline \
        --output "[$tmpdir/transform,$tmpdir/transform_Warped.nii.gz]" \
        --transform "Rigid[0.1]" \
        --metric "Mattes[$acpc_t1w,$tmpdir/fs_brain.nii,1,32,Random,0.25]" \
        --convergence "[1000x500x250x100,1e-06,10]" \
        --smoothing-sigmas "3.0x2.0x1.0x0.0mm" \
        --shrink-factors "8x4x2x1" \
        --use-histogram-matching 0 \
        "${mask_args[@]}" \
        --winsorize-image-intensities "[0.002,0.998]" \
        --verbose 0 \
        2>/dev/null

    # Step 3: Apply transform to aparc+aseg using NearestNeighbor (label image!)
    "$ANTS/antsApplyTransforms" \
        --dimensionality 3 \
        --input "$tmpdir/aparc.nii" \
        --reference-image "$acpc_t1w" \
        --transform "$tmpdir/transform0GenericAffine.mat" \
        --output "$out_aparc" \
        --interpolation NearestNeighbor \
        --verbose 0 \
        2>/dev/null

    rm -rf "$tmpdir"
    echo "[DONE] $subid → $out_aparc"
}

export -f process_subject
export FASTSURFER_DIR QSIPREP_DIR ANTS MRCONVERT TMPDIR_BASE

# Collect subjects that have FreeSurfer/FastSurfer base-subject dirs (no ses- or .long. suffix)
subjects=()
if [[ -n "$SUBJECT_FILTER" ]]; then
    # Single-subject pilot mode
    subjects=("$SUBJECT_FILTER")
else
    for fs_subdir in "$FASTSURFER_DIR"/sub-*/; do
        subid=$(basename "$fs_subdir")
        [[ "$subid" =~ _ses- ]] && continue
        [[ "$subid" =~ \.long\. ]] && continue
        subjects+=("$subid")
    done
fi

echo "Found ${#subjects[@]} base subjects in $FASTSURFER_DIR"
echo "Running $NJOBS jobs in parallel..."
echo ""

# Run in parallel using GNU parallel if available, else sequential
if command -v parallel &>/dev/null; then
    printf '%s\n' "${subjects[@]}" | parallel -j "$NJOBS" process_subject {}
else
    echo "(GNU parallel not found — running sequentially)"
    for subid in "${subjects[@]}"; do
        process_subject "$subid"
    done
fi

echo ""
echo "Done. Verify with:"
echo "  find $QSIPREP_DIR -name '*desc-aparc*' | wc -l"
