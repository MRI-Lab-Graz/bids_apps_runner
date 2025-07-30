# BIDS App Runner - Two-Step Workflow for Missing Subjects

## Overview

This document describes the elegant two-step workflow for finding and reprocessing missing subjects in BIDS pipelines.

## Philosophy

Instead of complex integration, we use two focused tools that work together:
- **`check_app_output.py`**: Find missing subjects/sessions (does one thing well)
- **`run_bids_apps.py`**: Process subjects (does one thing well)

## Workflow

### Step 1: Find Missing Subjects

```bash
# Basic usage - list missing subjects
python check_app_output.py /path/to/bids /path/to/derivatives --list-missing-subjects

# For specific pipeline
python check_app_output.py /path/to/bids /path/to/derivatives -p qsiprep --list-missing-subjects

# Save detailed report for analysis
python check_app_output.py /path/to/bids /path/to/derivatives --output-json missing_report.json
```

### Step 2: Process Missing Subjects

```bash
# Basic reprocessing
missing_subjects=$(python check_app_output.py /path/to/bids /path/to/derivatives --list-missing-subjects)
python run_bids_apps.py -x config.json --subjects $missing_subjects

# One-liner approach
python run_bids_apps.py -x config.json --subjects $(python check_app_output.py /path/to/bids /path/to/derivatives -p qsiprep --list-missing-subjects)
```

## Examples

### QSIPrep Workflow

```bash
# Step 1: Find missing QSIPrep subjects
python check_app_output.py /data/bids /data/derivatives -p qsiprep --list-missing-subjects > missing_qsiprep.txt

# Step 2: Process only missing subjects
python run_bids_apps.py -x qsiprep_config.json --subjects $(cat missing_qsiprep.txt)
```

### fMRIPrep Workflow

```bash
# Step 1: Find missing fMRIPrep subjects
python check_app_output.py /data/bids /data/derivatives -p fmriprep --list-missing-subjects

# Step 2: Process with specific configuration
python run_bids_apps.py -x fmriprep_config.json --subjects sub-001 sub-042 sub-105
```

### Session-Specific Analysis

For pipelines that support session-level processing:

```bash
# Step 1: Get detailed missing report
python check_app_output.py /data/bids /data/derivatives --output-json missing_analysis.json

# Step 2a: Process all missing subjects (let app handle sessions)
python run_bids_apps.py -x config.json --subjects $(python check_app_output.py /data/bids /data/derivatives --list-missing-subjects)

# Step 2b: Process specific sessions (if app supports --session-id)
python run_bids_apps.py -x config.json --subjects sub-001 --session-id 2
```

## Advantages

### Clean Separation of Concerns
- **`check_app_output.py`**: Expert at finding missing data
- **`run_bids_apps.py`**: Expert at running containers
- No code duplication or complex integration

### Flexibility
- User can inspect results between steps
- Easy to modify subject lists
- Can use different processing strategies
- Works with existing scripts and pipelines

### Unix Philosophy
- Small tools that do one thing well
- Tools that work together
- Everything is a file/stream

## Output Formats

### `--list-missing-subjects` Output
```
sub-001
sub-042
sub-105
```

### `--output-json` Output
```json
{
  "metadata": {
    "generated_by": "BIDS App Output Checker",
    "timestamp": "2025-07-30T20:30:00",
    "pipeline_filter": "qsiprep"
  },
  "missing_data_by_pipeline": {
    "qsiprep": {
      "missing_items": ["sub-001/ses-1/dwi/...", "sub-042/ses-2/dwi/..."],
      "total_missing": 2,
      "subjects_with_missing_data": ["sub-001", "sub-042"]
    }
  },
  "summary": {
    "all_missing_subjects": ["sub-001", "sub-042"]
  }
}
```

## Integration with Existing Scripts

This workflow integrates seamlessly with existing processing scripts:

```bash
#!/bin/bash
# reprocess_missing.sh

BIDS_DIR="/data/bids"
DERIVATIVES_DIR="/data/derivatives" 
CONFIG_FILE="qsiprep_config.json"

echo "Finding missing subjects..."
missing_subjects=$(python check_app_output.py "$BIDS_DIR" "$DERIVATIVES_DIR" -p qsiprep --list-missing-subjects)

if [ -z "$missing_subjects" ]; then
    echo "No missing subjects found!"
    exit 0
fi

echo "Found missing subjects: $missing_subjects"
echo "Starting reprocessing..."

python run_bids_apps.py -x "$CONFIG_FILE" --subjects $missing_subjects
```

## Error Handling

- `check_app_output.py --list-missing-subjects` exits with code 1 if subjects are missing, 0 if all complete
- `run_bids_apps.py` exits with code 1 if processing fails
- Easy to chain commands and handle errors in scripts

## Configuration Requirements

For session-aware detection, ensure your BIDS app config includes `output_check` patterns:

```json
{
  "app": {
    "output_check": {
      "pattern": "{subject}/{session}/dwi/{subject}_{session}_*desc-preproc_dwi.nii.gz",
      "directory": ""
    }
  }
}
```

This ensures both tools understand the expected output structure.
