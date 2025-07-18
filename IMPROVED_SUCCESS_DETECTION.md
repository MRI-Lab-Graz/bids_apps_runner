# Improved Success Detection in BIDS App Runner

## Problem

The original implementation relied solely on a single `output_check` pattern in the configuration file to determine if a subject had been successfully processed. This approach had several limitations:

1. **Container-specific outputs**: Different BIDS apps produce different file structures and naming conventions
2. **Configuration dependency**: Required users to know exactly what files each app produces
3. **False negatives**: Apps might succeed but produce output in unexpected locations
4. **No exit code checking**: Ignored whether the container actually ran successfully

## Solution

The improved implementation uses a **multi-strategy approach** to detect successful processing:

### 1. Success Marker Files (Primary Strategy)
- Creates `.bids_app_runner/{subject}_success.txt` files when processing completes successfully
- Most reliable method as it's based on actual container exit codes
- Includes timestamp and version information
- Can be cleaned with `--clean-success-markers` flag

### 2. Generic Output Detection (Fallback)
When success markers aren't available, the system checks for common BIDS app output patterns:

```python
patterns_to_check = [
    # Subject-specific directories
    os.path.join(output_dir, subject),
    os.path.join(output_dir, "derivatives", "*", subject),
    
    # fMRIPrep-style patterns
    os.path.join(output_dir, subject, "func", f"{subject}_*"),
    os.path.join(output_dir, subject, "anat", f"{subject}_*"),
    
    # FreeSurfer-style patterns
    os.path.join(output_dir, subject, "scripts", "*"),
    os.path.join(output_dir, subject, "surf", "*"),
    
    # QSIPrep-style patterns  
    os.path.join(output_dir, subject, "dwi", f"{subject}_*"),
    
    # HTML reports (common across many apps)
    os.path.join(output_dir, f"{subject}.html"),
    os.path.join(output_dir, f"{subject}_report.html"),
    
    # Log files indicating completion
    os.path.join(output_dir, "logs", f"{subject}_*"),
    os.path.join(output_dir, f"{subject}_log.txt"),
]
```

### 3. Configured Pattern Check (Legacy Support)
- Still supports the original `output_check` pattern from config files
- Maintains backward compatibility

### 4. Directory Existence Check (Final Fallback)
- Checks if subject directory exists and contains files
- Last resort detection method

## New Features

### Container Exit Code Validation
- Now properly checks if the container exited with code 0
- Distinguishes between container failures and output detection issues

### Enhanced Debugging
- `list_output_structure()` function shows directory contents when output isn't detected
- Helps identify where apps are actually placing their output
- Shows file sizes and directory structure up to 3 levels deep

### Success Marker Management
```bash
# Clean all success markers to force re-detection
./run_bids_apps.py -x config.json --clean-success-markers

# Force reprocessing (removes individual markers)
./run_bids_apps.py -x config.json --force --subjects sub-001

# Run in pilot mode (one random subject, jobs=1)
./run_bids_apps.py -x config.json --pilot
```

### Pilot Mode (New)
- Moved from JSON config to command line argument: `--pilot`
- Automatically sets jobs to 1 for better debugging
- Processes one randomly selected subject
- Useful for testing configurations and containers

## Improved Processing Logic

1. **Check for existing success marker** → Skip if found (unless `--force`)
2. **Run container** → Verify exit code 0
3. **Check for output** → Use multiple detection strategies
4. **Create success marker** → Mark as successfully completed
5. **Clean up** → Remove temporary files

## Benefits

1. **Container-agnostic**: Works with any BIDS app without configuration
2. **Reliable detection**: Based on actual container success + output existence
3. **Better debugging**: Shows exactly where output is (or isn't) when problems occur
4. **Backward compatible**: Existing configs continue to work
5. **Flexible**: Multiple fallback strategies prevent false negatives

## Example Usage

```bash
# Standard run with improved detection
./run_bids_apps.py -x config.json

# Debug mode to see exactly what's happening
./run_bids_apps.py -x config.json --debug --subjects sub-001

# Pilot mode: test with one random subject
./run_bids_apps.py -x config.json --pilot

# Clean success markers and reprocess
./run_bids_apps.py -x config.json --clean-success-markers --subjects sub-001

# Force reprocessing even with success markers
./run_bids_apps.py -x config.json --force --subjects sub-001

# Combine pilot mode with debug for thorough testing
./run_bids_apps.py -x config.json --pilot --debug
```

## Configuration Changes

### JSON Config Files
The `output_check` section in config files is now **optional**, and `pilottest` has been **removed** (moved to command line):

```json
{
  "common": {
    "bids_folder": "/path/to/bids",
    "output_folder": "/path/to/output",
    "tmp_folder": "/path/to/tmp",
    "container": "/path/to/container.sif",
    "templateflow_dir": "/path/to/templateflow",
    "jobs": 4
    // pilottest field removed - use --pilot command line argument instead
  },
  "app": {
    "analysis_level": "participant",
    "options": ["--some-option"],
    // output_check is now optional - will use generic detection if not specified
    "output_check": {
      "pattern": "sub-{subject}/func/*_preproc_bold.nii.gz",
      "directory": ""
    }
  }
}
```

### Command Line Changes
- **New**: `--pilot` flag replaces `"pilottest": true` in config
- **New**: `--clean-success-markers` for resetting detection
- Pilot mode automatically sets jobs=1 for better debugging

This makes the runner much more user-friendly and robust across different BIDS applications.
