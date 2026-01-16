#!/bin/bash
# standardize_fmriprep.sh
# Standardizes fMRIPrep output for consistency across single & multi-session subjects.

BASE_DIR=$(pwd)

echo "Starting reorganization in $BASE_DIR..."

# 1. Identify and process single-session subjects
# These subjects often have anatomy trapped in ses-1/anat
for sub_path in "$BASE_DIR"/sub-*; do
    [ -d "$sub_path" ] || continue
    SUB=$(basename "$sub_path")
    
    # Count sessions
    SES_COUNT=$(ls -d "$sub_path"/ses-* 2>/dev/null | wc -l)
    
    if [ "$SES_COUNT" -eq 1 ]; then
        SES_DIR=$(ls -d "$sub_path"/ses-* 2>/dev/null)
        SES=$(basename "$SES_DIR")
        
        # If anatomy is only in the session folder, move it up
        if [ ! -d "$sub_path/anat" ] && [ -d "$SES_DIR/anat" ]; then
            echo "Standardizing single-session subject: $SUB"
            mkdir -p "$sub_path/anat"
            
            # Move preprocessed images and masks to top-level anat
            # We remove the session label from the filename to match multi-session patterns
            for f in "$SES_DIR/anat"/*; do
                [ -f "$f" ] || continue
                FILE_NAME=$(basename "$f")
                
                # If it's a co-registration transform (xfm.txt), we keep it in the session folder
                if [[ "$FILE_NAME" == *"xfm.txt" ]]; then
                    continue 
                fi
                
                NEW_NAME=$(echo "$FILE_NAME" | sed "s/_${SES}_/_/")
                mv "$f" "$sub_path/anat/$NEW_NAME"
            done
            
            # Update internal references in JSON files moved to the new anat folder
            find "$sub_path/anat" -name "*.json" -type f -exec sed -i '' "s|${SES}/anat/${SUB}_${SES}_|anat/${SUB}_|g" {} +
            find "$sub_path/anat" -name "*.json" -type f -exec sed -i '' "s|${SUB}_${SES}_|${SUB}_|g" {} +
            
            # Update HTML reports
            HTML_FILE="$BASE_DIR/$SUB.html"
            if [ -f "$HTML_FILE" ]; then
                sed -i '' "s|${SUB}/${SES}/anat/${SUB}_${SES}_|${SUB}/anat/${SUB}_|g" "$HTML_FILE"
                sed -i '' "s|${SUB}_${SES}_acq-mprage|${SUB}_acq-mprage|g" "$HTML_FILE"
            fi
        fi
    fi
done

# 2. Ensure all session-specific anatomical transforms are in their respective session folders
# This handles both single and multi-session subjects
find "$BASE_DIR" -maxdepth 2 -type d -name "anat" | while read anat_dir; do
    SUB_PATH=$(dirname "$anat_dir")
    SUB=$(basename "$SUB_PATH")
    
    # Find any xfm.txt files that might be in the top-level anat (if you moved them there earlier)
    find "$anat_dir" -name "*_ses-*_xfm.txt" | while read xfm; do
        XFM_FILE=$(basename "$xfm")
        SES=$(echo "$XFM_FILE" | grep -o "ses-[0-9a-zA-Z]*")
        TARGET_SES_ANAT="$SUB_PATH/$SES/anat"
        
        echo "Ensuring co-registration transform is in session folder: $SUB/$SES"
        mkdir -p "$TARGET_SES_ANAT"
        mv "$xfm" "$TARGET_SES_ANAT/"
        
        # Correct references in HTML/JSON to point back to the session-specific anat
        HTML_FILE="$BASE_DIR/$SUB.html"
        [ -f "$HTML_FILE" ] && sed -i '' "s|${SUB}/anat/${SUB}_${SES}_|${SUB}/${SES}/anat/${SUB}_${SES}_|g" "$HTML_FILE"
        find "$SUB_PATH" -name "*.json" -type f -exec sed -i '' "s|anat/${SUB}_${SES}_|${SES}/anat/${SUB}_${SES}_|g" {} +
    done
done

echo "Reorganization complete."