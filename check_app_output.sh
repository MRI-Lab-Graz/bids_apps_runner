#!/bin/bash
#
# check_app_output.sh
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
#   ./check_app_output.sh -p <pipeline> <BIDS_directory> <pipeline_output_directory>
#
# Examples:
#   ./check_app_output.sh -p fmriprep    /path/to/BIDS /path/to/fmriprep
#   ./check_app_output.sh -p freesurfer  /path/to/BIDS /path/to/freesurfer
#   ./check_app_output.sh -p qsiprep     /path/to/BIDS /path/to/qsiprep
#   ./check_app_output.sh -p qsirecon    /path/to/BIDS /path/to/qsiprep_outputs
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

  # For later cross-subject consistency check of the surface-based outputs
  declare -A has_surface_output
  all_subjects=()
  surface_found_global=0

  # Loop over subjects in the BIDS directory
  for subj_dir in "$BIDS_DIR"/sub-*; do
    [ -d "$subj_dir" ] || continue
    subj=$(basename "$subj_dir")
    echo "Checking $subj ..."
    all_subjects+=( "$subj" )
    surface_found_for_subject=0

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

      # Process each BIDS functional file for volumetric (preprocessed) data
      for bids_func in "$func_dir"/*_bold.nii*; do
        [ -e "$bids_func" ] || continue
        bids_base=$(basename "$bids_func")
        # Get the part before "_bold" to match subject, session, and task info
        prefix=$(echo "$bids_base" | sed 's/_bold.*//')

        # Build the expected fmriprep path (assumes similar subject/session structure)
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
        fi
      done

      # --- New: Check for surface-based outputs in this session ---
      # These files are named like: sub-<ID>_ses-<X>_task-*_hemi-<L or R>_space-<...>_bold.func.gii
      if [ -d "$fmriprep_func_dir" ]; then
         surface_files=( "$fmriprep_func_dir"/*_hemi-*_bold.func.gii )
         if [ ${#surface_files[@]} -gt 0 ]; then
            surface_found_for_subject=1
            surface_found_global=1
            # For each surface file, ensure that the corresponding hemisphere pair exists
            for file in "${surface_files[@]}"; do
               if [[ "$file" == *"hemi-L"* ]]; then
                  expected_r="${file/hemi-L/hemi-R}"
                  if [ ! -f "$expected_r" ]; then
                     echo "  [MISSING] Corresponding hemi-R file for: $file"
                     missing_items+=( "$file: corresponding hemi-R file not found" )
                  fi
               elif [[ "$file" == *"hemi-R"* ]]; then
                  expected_l="${file/hemi-R/hemi-L}"
                  if [ ! -f "$expected_l" ]; then
                     echo "  [MISSING] Corresponding hemi-L file for: $file"
                     missing_items+=( "$file: corresponding hemi-L file not found" )
                  fi
               fi
            done
         fi
      fi
      # --- End new check for surface outputs ---
    done

    # Record for this subject whether any surface file was found (in any session)
    has_surface_output[$subj]=$surface_found_for_subject
  done

  # --- Global check: if any subject has surface-based outputs, then all subjects must have them ---
  if [ "$surface_found_global" -eq 1 ]; then
    for s in "${all_subjects[@]}"; do
      if [ "${has_surface_output[$s]}" != "1" ]; then
         echo "  [MISSING] Surface-based outputs missing for subject $s, but present in others."
         missing_items+=( "$s: Missing surface-based outputs" )
      fi
    done
  fi
  ;;

  freesurfer)
    echo "Running check for freesurfer pipeline..."
    # Prepare to track hippocampal segmentation files per subject.
    declare -A subject_cross_hippoSf
    declare -A subject_cross_amyg
    declare -A subject_long_hippoSf
    declare -A subject_long_amyg
    declare -A subject_is_multisession
    all_subjects=()

    # Loop over subjects in the BIDS directory
    for subj_dir in "$BIDS_DIR"/sub-*; do
      [ -d "$subj_dir" ] || continue
      subj=$(basename "$subj_dir")
      echo "Checking freesurfer outputs for subject: $subj"
      all_subjects+=( "$subj" )
      # Initialize flags for this subject.
      subject_cross_hippoSf[$subj]=0
      subject_cross_amyg[$subj]=0
      subject_long_hippoSf[$subj]=0
      subject_long_amyg[$subj]=0

      # Count sessions with anatomical data.
      anat_sessions=0
      if compgen -G "$subj_dir/ses-*" > /dev/null; then
        for sess_dir in "$subj_dir"/ses-*; do
          anat_dir="$sess_dir/anat"
          if [ -d "$anat_dir" ]; then
            if compgen -G "$anat_dir"/*_T1w.nii* > /dev/null; then
              anat_sessions=$((anat_sessions+1))
            fi
          fi
        done
      else
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

      # Mark subject as multi-session if more than one anatomical session was found.
      if [ "$anat_sessions" -gt 1 ]; then
          subject_is_multisession[$subj]=1
      else
          subject_is_multisession[$subj]=0
      fi

      # Determine expected freesurfer folder count.
      if [ "$anat_sessions" -eq 1 ]; then
        expected_count=1
        echo "  Expected freesurfer outputs: 1 (single cross-sectional analysis)."
      else
        expected_count=$((2 * anat_sessions + 1))
        echo "  Expected freesurfer outputs: $expected_count (for $anat_sessions sessions: $anat_sessions cross-sectional, 1 base, and $anat_sessions longitudinal)."
      fi

      # Find all freesurfer output directories for this subject.
      fs_dirs=( "$OUT_DIR"/${subj}* )
      fs_dirs_filtered=()
      for d in "${fs_dirs[@]}"; do
        [ -d "$d" ] && fs_dirs_filtered+=( "$d" )
      done

      fs_count=${#fs_dirs_filtered[@]}
      if [ "$fs_count" -ne "$expected_count" ]; then
        echo "  [MISSING] For subject $subj: expected $expected_count freesurfer folders, but found $fs_count."
        missing_items+=( "$subj: expected $expected_count freesurfer folders, found $fs_count" )
      fi

      # Check each freesurfer directory.
      for fsd in "${fs_dirs_filtered[@]}"; do
        recon_done="$fsd/scripts/recon-all.done"
        if [ ! -f "$recon_done" ]; then
          echo "  [MISSING] recon-all.done in folder: $fsd"
          missing_items+=( "$fsd: recon-all.done not found" )
        else
          echo "  [FOUND] recon-all.done in folder: $fsd"
        fi

        # Determine folder type by checking if its name contains ".long"
        if [[ "$fsd" == *".long"* ]]; then
          folder_type="long"
        else
          folder_type="cross"
        fi

        mri_dir="$fsd/mri"
        if [ -d "$mri_dir" ]; then
          if [ "$folder_type" == "long" ]; then
            # In longitudinal folders, expect files that include ".long" in their filename.
            if compgen -G "$mri_dir/"*hippoSfVolumes*.long*.txt > /dev/null; then
              echo "  [FOUND] Longitudinal hippocampal subfield volumes file in $mri_dir"
              subject_long_hippoSf[$subj]=1
            else
              echo "  [INFO] No longitudinal hippocampal subfield volumes file found in $mri_dir"
            fi

            # For amygdala files, accept either pattern:
            if compgen -G "$mri_dir/"*hippoAmygLabels*.long*.txt > /dev/null || \
               compgen -G "$mri_dir/"*amygNucVolumes*.long*.txt > /dev/null; then
              echo "  [FOUND] Longitudinal hippocampal/amygdala file in $mri_dir"
              subject_long_amyg[$subj]=1
            else
              echo "  [INFO] No longitudinal hippocampal/amygdala file found in $mri_dir"
            fi
          else
            # In cross-sectional folders, ensure that no file contains ".long" in its name.
            if compgen -G "$mri_dir/"*hippoSfVolumes*.long*.txt > /dev/null; then
              echo "  [ERROR] Found longitudinal hippocampal subfield volumes file in cross-sectional folder $mri_dir"
              missing_items+=( "$fsd/mri: longitudinal hippocampal subfield volumes file found in cross-sectional folder" )
            fi
            if compgen -G "$mri_dir/"*hippoAmygLabels*.long*.txt > /dev/null || \
               compgen -G "$mri_dir/"*amygNucVolumes*.long*.txt > /dev/null; then
              echo "  [ERROR] Found longitudinal hippocampal/amygdala file in cross-sectional folder $mri_dir"
              missing_items+=( "$fsd/mri: longitudinal hippocampal/amygdala file found in cross-sectional folder" )
            fi
            # Record cross-sectional files (ensuring they do not include ".long")
            cs_hippoSf=$(find "$mri_dir" -maxdepth 1 -type f -name "*hippoSfVolumes*.txt" ! -name "*long*")
            if [ -n "$cs_hippoSf" ]; then
              echo "  [FOUND] Cross-sectional hippocampal subfield volumes file in $mri_dir"
              subject_cross_hippoSf[$subj]=1
            fi
            cs_amyg=$(find "$mri_dir" -maxdepth 1 -type f \( -name "*hippoAmygLabels*.txt" -o -name "*amygNucVolumes*.txt" \) ! -name "*long*")
            if [ -n "$cs_amyg" ]; then
              echo "  [FOUND] Cross-sectional hippocampal/amygdala file in $mri_dir"
              subject_cross_amyg[$subj]=1
            fi
          fi
        else
          echo "  [WARNING] No mri directory in $fsd, skipping hippocampal segmentation check."
        fi
      done
    done

    # --- Global consistency check for multi-session subjects ---
    for s in "${all_subjects[@]}"; do
      if [ "${subject_is_multisession[$s]}" -eq 1 ]; then
        if [ "${subject_long_hippoSf[$s]}" -ne 1 ]; then
          echo "  [MISSING] Subject $s is missing longitudinal hippocampal subfield volumes file"
          missing_items+=( "$s: missing longitudinal hippocampal subfield volumes file" )
        fi
        if [ "${subject_long_amyg[$s]}" -ne 1 ]; then
          echo "  [MISSING] Subject $s is missing longitudinal hippocampal/amygdala file"
          missing_items+=( "$s: missing longitudinal hippocampal/amygdala file" )
        fi
      fi
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
