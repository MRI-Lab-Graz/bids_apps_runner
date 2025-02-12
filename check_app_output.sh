#!/bin/bash
#
# check_pipeline.sh
#
# This script compares the (gold-standard) BIDS folder to the outputs
# of a given processing pipeline. Use the -p flag to select the pipeline:
#
#   - fmriprep:
#         For each BIDS functional file there must be a corresponding
#         preprocessed file (containing "desc-preproc_bold") in the fmriprep output folder.
#
#   - freesurfer:
#         For each subject and session that contains an anatomical file
#         (matching *_T1w.nii or *_T1w.nii.gz in an "anat" folder) freesurfer should have
#         produced output folders. For single-session subjects, one output folder is expected.
#         For multi-session subjects (N sessions), the longitudinal pipeline should have been used,
#         yielding 2N+1 output folders (N cross-sectional, 1 base, N longitudinal). Each folder
#         should contain a "scripts/recon-all.done" file.
#
#   - qsiprep:
#         For each subject, the qsiprep output should include a subject-level folder and a corresponding HTML report.
#         For each session in the BIDS folder, the session folder should contain a "dwi" subfolder.
#         For each BIDS DWI file (e.g., sub-1291013_ses-2_acq-multi_run-1_dwi.nii.gz) in the BIDS folder,
#         there should be a corresponding qsiprep file (typically containing "desc-preproc_dwi") in the
#         corresponding qsiprep session's dwi folder.
#
#   - qsirecon:
#         qsirecon outputs (the reconstruction part of qsiprep) are assumed to be organized in
#         a derivatives folder (e.g., <OUT_DIR>/derivatives/qsirecon-MRtrix3_act-HSVS) with a structure:
#
#             <qsirecon_pipeline>/sub-*/ses-*/dwi/
#
#         For each subject and session from the BIDS folder, the script checks that a matching dwi folder exists
#         and that it contains at least one .nii.gz file.
#
# Usage:
#   ./check_pipeline.sh -p <pipeline> <BIDS_directory> <pipeline_output_directory>
#
# Examples:
#   ./check_pipeline.sh -p fmriprep    /path/to/BIDS /path/to/fmriprep
#   ./check_pipeline.sh -p freesurfer  /path/to/BIDS /path/to/freesurfer
#   ./check_pipeline.sh -p qsiprep     /path/to/BIDS /path/to/qsiprep
#   ./check_pipeline.sh -p qsirecon    /path/to/BIDS /path/to/qsiprep_outputs
#

# Display usage message
usage() {
  echo "Usage: $0 -p <pipeline> <BIDS_directory> <pipeline_output_directory>"
  echo "   pipeline: fmriprep, freesurfer, qsiprep, or qsirecon"
  exit 1
}

# Parse the -p option for pipeline
pipeline=""
while getopts "p:" opt; do
  case ${opt} in
    p )
      pipeline="$OPTARG"
      ;;
    * )
      usage
      ;;
  esac
done
shift $((OPTIND -1))

# Verify required arguments
if [ -z "$pipeline" ]; then
  echo "Error: Pipeline (-p) must be specified."
  usage
fi

if [ "$#" -ne 2 ]; then
  usage
fi

BIDS_DIR="$1"
OUT_DIR="$2"

# Check that directories exist
if [ ! -d "$BIDS_DIR" ]; then
  echo "Error: BIDS directory '$BIDS_DIR' does not exist."
  exit 1
fi

if [ ! -d "$OUT_DIR" ]; then
  echo "Error: Pipeline output directory '$OUT_DIR' does not exist."
  exit 1
fi

# Array to store missing items
missing_items=()

case "$pipeline" in
  fmriprep)
    echo "Running check for fmriprep pipeline..."
    # Loop over subjects in the BIDS directory
    for subj_dir in "$BIDS_DIR"/sub-*; do
      [ -d "$subj_dir" ] || continue
      subj=$(basename "$subj_dir")
      echo "Checking $subj ..."
      
      # Check for session folders (if any)
      if compgen -G "$subj_dir/ses-*" > /dev/null; then
        sessions=( "$subj_dir"/ses-* )
      else
        sessions=( "$subj_dir" )
      fi
      
      # Loop over each session (or subject-level folder)
      for sess_dir in "${sessions[@]}"; do
        func_dir="$sess_dir/func"
        if [ ! -d "$func_dir" ]; then
          echo "  [WARNING] No 'func' directory in $(basename "$sess_dir")"
          continue
        fi

        # Process each BIDS functional file
        for bids_func in "$func_dir"/*_bold.nii*; do
          [ -e "$bids_func" ] || continue
          bids_base=$(basename "$bids_func")
          
          # Extract a prefix (everything before _bold)
          prefix=$(echo "$bids_base" | sed 's/_bold.*//')
          
          # Build the expected fmriprep path: assume similar subject and session structure
          fmriprep_subj_dir="$OUT_DIR/$subj"
          sess_basename=$(basename "$sess_dir")
          if [[ "$sess_basename" == ses-* ]]; then
            fmriprep_subj_dir="$fmriprep_subj_dir/$sess_basename"
          fi
          fmriprep_func_dir="$fmriprep_subj_dir/func"
          
          # Look for any file that starts with the prefix and contains "desc-preproc_bold"
          search_pattern="$fmriprep_func_dir/${prefix}*desc-preproc_bold.nii*"
          matches=( $search_pattern )
          if [ ${#matches[@]} -eq 0 ]; then
            echo "  [MISSING] fmriprep file for: $bids_func"
            missing_items+=( "$bids_func" )
          else
            echo "  [FOUND]   fmriprep file for: $bids_func"
          fi
        done
      done
    done
    ;;
   freesurfer)
    echo "Running check for freesurfer pipeline..."
    # Loop over subjects in the BIDS directory
    for subj_dir in "$BIDS_DIR"/sub-*; do
      [ -d "$subj_dir" ] || continue
      subj=$(basename "$subj_dir")
      echo "Checking freesurfer outputs for subject: $subj"
      
      # Count sessions with anatomical data.
      anat_sessions=0
      
      # First, check if the subject directory has session subfolders.
      if compgen -G "$subj_dir/ses-*" > /dev/null; then
        # Loop over session folders
        for sess_dir in "$subj_dir"/ses-*; do
          anat_dir="$sess_dir/anat"
          if [ -d "$anat_dir" ]; then
            # Look for any T1w file (supports nii or nii.gz)
            if compgen -G "$anat_dir"/*_T1w.nii* > /dev/null; then
              anat_sessions=$((anat_sessions+1))
            fi
          fi
        done
      else
        # No sessions; check the subject folder directly.
        anat_dir="$subj_dir/anat"
        if [ -d "$anat_dir" ]; then
          if compgen -G "$anat_dir"/*_T1w.nii* > /dev/null; then
            anat_sessions=1
          fi
        fi
      fi
      
      if [ "$anat_sessions" -eq 0 ]; then
        echo "  [SKIP] No anatomical T1w files found for subject $subj; skipping freesurfer check."
        continue
      fi
      
      # Now, determine what folders are expected in the freesurfer output directory.
      if [ "$anat_sessions" -eq 1 ]; then
        expected_count=1
        echo "  Expected freesurfer outputs: 1 (single cross-sectional analysis)."
      else
        # For N sessions: expect N cross-sectional + 1 base folder + N longitudinal = 2N + 1
        expected_count=$((2 * anat_sessions + 1))
        echo "  Expected freesurfer outputs: $expected_count (for $anat_sessions sessions: $anat_sessions cross-sectional, 1 base, and $anat_sessions longitudinal)."
      fi
      
      # Find all directories in the freesurfer output directory starting with the subject id.
      fs_dirs=( "$OUT_DIR"/${subj}* )
      
      # Filter only those entries that are directories.
      fs_dirs_filtered=()
      for d in "${fs_dirs[@]}"; do
        [ -d "$d" ] && fs_dirs_filtered+=( "$d" )
      done
      
      fs_count=${#fs_dirs_filtered[@]}
      
      if [ "$fs_count" -ne "$expected_count" ]; then
        echo "  [MISSING] For subject $subj: expected $expected_count freesurfer folders, but found $fs_count."
        missing_items+=( "$subj: expected $expected_count freesurfer folders, found $fs_count" )
      fi
      
      # Now check each found freesurfer folder for the recon-all.done file.
      for fsd in "${fs_dirs_filtered[@]}"; do
        recon_done="$fsd/scripts/recon-all.done"
        if [ ! -f "$recon_done" ]; then
          echo "  [MISSING] recon-all.done in folder: $fsd"
          missing_items+=( "$fsd: recon-all.done not found" )
        else
          echo "  [FOUND] recon-all.done in folder: $fsd"
        fi

        # --- New check: hippocampal subfield segmentation files ---
        # For multi-session subjects, we assume that the cross-sectional freesurfer outputs
        # (i.e. those corresponding to a BIDS session) have "ses-" in the folder name.
        # For single-session subjects, check the only output folder.
        if [ $fs_count -eq 1 ] || [[ "$fsd" == *ses-* ]]; then
          mri_dir="$fsd/mri"
          if [ -d "$mri_dir" ]; then
            # Check for the hippoSfVolumes file
            hippoSf=( "$mri_dir/"*hippoSfVolumes-T1-T2.*.txt )
            if [ ${#hippoSf[@]} -eq 0 ]; then
              echo "  [MISSING] HippoSfVolumes-T1-T2 .txt file not found in $mri_dir"
              missing_items+=( "$fsd/mri: hippoSfVolumes-T1-T2 file not found" )
            else
              echo "  [FOUND] HippoSfVolumes-T1-T2 .txt file in $mri_dir"
            fi

            # Check for the hippoAmygLabels file
            hippoAmyg=( "$mri_dir/"*hippoAmygLabels-T1-T2.*.txt )
            if [ ${#hippoAmyg[@]} -eq 0 ]; then
              echo "  [MISSING] HippoAmygLabels-T1-T2 .txt file not found in $mri_dir"
              missing_items+=( "$fsd/mri: hippoAmygLabels-T1-T2 file not found" )
            else
              echo "  [FOUND] HippoAmygLabels-T1-T2 .txt file in $mri_dir"
            fi
          else
            echo "  [WARNING] No mri directory in $fsd, skipping hippocampal segmentation check."
          fi
        fi
        # --- End new check ---
      done
      
    done
    ;;
  qsiprep)
    echo "Running check for qsiprep pipeline..."
    # Loop over subjects in the BIDS directory
    for subj_dir in "$BIDS_DIR"/sub-*; do
      [ -d "$subj_dir" ] || continue
      subj=$(basename "$subj_dir")
      echo "Checking qsiprep outputs for subject: $subj"
      
      # Check that a subject-level folder exists in the qsiprep output
      qsiprep_subj_dir="$OUT_DIR/$subj"
      if [ ! -d "$qsiprep_subj_dir" ]; then
        echo "  [MISSING] qsiprep subject folder for $subj"
        missing_items+=( "$subj: qsiprep subject folder not found" )
        continue
      fi
      
      # Check for the HTML report (e.g., sub-001.html) in the qsiprep output folder.
      html_report="$OUT_DIR/${subj}.html"
      if [ ! -f "$html_report" ]; then
        echo "  [MISSING] qsiprep HTML report for $subj"
        missing_items+=( "$subj: qsiprep HTML report not found" )
      else
        echo "  [FOUND] qsiprep HTML report for $subj"
      fi
      
      # Determine sessions in the BIDS folder
      if compgen -G "$subj_dir/ses-*" > /dev/null; then
        sessions=( "$subj_dir"/ses-* )
      else
        sessions=( "$subj_dir" )
      fi
      
      # For each session, check the dwi folder
      for sess_dir in "${sessions[@]}"; do
        sess=$(basename "$sess_dir")
        dwi_dir="$sess_dir/dwi"
        if [ ! -d "$dwi_dir" ]; then
          echo "  [WARNING] No 'dwi' directory in ${sess} for subject $subj"
          continue
        fi
        
        # Process each BIDS DWI file in the BIDS dwi folder
        for bids_dwi in "$dwi_dir"/*_dwi.nii*; do
          [ -e "$bids_dwi" ] || continue
          bids_base=$(basename "$bids_dwi")
          
          # Extract the prefix up to "_dwi" (this removes run/acq info)
          prefix=$(echo "$bids_base" | sed 's/_dwi.*//')
          
          # Build the expected qsiprep path: assume a similar subject/session structure.
          # The qsiprep dwi file should include "desc-preproc_dwi" in its name.
          qsiprep_dwi_dir="$qsiprep_subj_dir/$sess/dwi"
          search_pattern="$qsiprep_dwi_dir/${prefix}*desc-preproc_dwi.nii*"
          matches=( $search_pattern )
          if [ ${#matches[@]} -eq 0 ]; then
            echo "  [MISSING] qsiprep DWI file for: $bids_dwi"
            missing_items+=( "$bids_dwi" )
          else
            echo "  [FOUND] qsiprep DWI file for: $bids_dwi"
          fi
        done
      done
    done
    ;;
  qsirecon)
    echo "Running check for qsirecon pipelines..."
    # Here we assume that the output directory (OUT_DIR) is the parent of the derivatives folder.
    # We look for all qsirecon derivative pipelines.
    qsirecon_pipelines=( "$OUT_DIR/derivatives"/qsirecon* )
    if [ ${#qsirecon_pipelines[@]} -eq 0 ]; then
      echo "  [WARNING] No qsirecon derivative pipelines found in $OUT_DIR/derivatives"
    fi

    for qsipipeline in "${qsirecon_pipelines[@]}"; do
      [ -d "$qsipipeline" ] || continue
      pipeline_name=$(basename "$qsipipeline")
      echo "Checking qsirecon pipeline: $pipeline_name"
      
      # Loop over subjects in the BIDS folder
      for subj_dir in "$BIDS_DIR"/sub-*; do
        [ -d "$subj_dir" ] || continue
        subj=$(basename "$subj_dir")
        echo "  Checking subject: $subj"
        
        # Determine sessions in the BIDS subject folder
        if compgen -G "$subj_dir/ses-*" > /dev/null; then
          sessions=( "$subj_dir"/ses-* )
        else
          sessions=( "$subj_dir" )
        fi
        
        # For each session, check for the dwi folder in the qsirecon pipeline output.
        for sess_dir in "${sessions[@]}"; do
          sess=$(basename "$sess_dir")
          qsirecon_dwi_dir="$qsipipeline/$subj/$sess/dwi"
          if [ ! -d "$qsirecon_dwi_dir" ]; then
            echo "    [MISSING] Directory $qsirecon_dwi_dir does not exist"
            missing_items+=( "$pipeline_name: $subj/$sess/dwi directory not found" )
          else
            # Check if at least one .nii.gz file exists in the directory.
            nii_files=( "$qsirecon_dwi_dir"/*.nii.gz )
            if [ ${#nii_files[@]} -eq 0 ]; then
              echo "    [MISSING] No .nii.gz file in $qsirecon_dwi_dir"
              missing_items+=( "$pipeline_name: No .nii.gz file in $subj/$sess/dwi" )
            else
              echo "    [FOUND] .nii.gz file(s) present in $qsirecon_dwi_dir"
            fi
          fi
        done
      done
    done
    ;;
  *)
    echo "Error: Unknown pipeline '$pipeline'. Valid options are: fmriprep, freesurfer, qsiprep, qsirecon"
    exit 1
    ;;
esac

# Final overview
echo "---------------------------------------------"
if [ ${#missing_items[@]} -eq 0 ]; then
  echo "All checks passed for pipeline: $pipeline."
else
  echo "Total missing items: ${#missing_items[@]}"
  echo "Missing items:"
  for item in "${missing_items[@]}"; do
    echo "  - $item"
  done
fi
echo "---------------------------------------------"
