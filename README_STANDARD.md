# BIDS App Runner - Standard Version

**Version 2.0.0** - Production-ready BIDS App execution with DataLad auto-detection

## Overview

The standard BIDS App Runner (`run_bids_apps.py`) is a robust tool for executing BIDS Apps with:
- **Automatic DataLad detection** - Works with both standard BIDS folders and DataLad datasets
- **Comprehensive error handling** and logging
- **Debug mode** with detailed container execution logs
- **Parallel processing** support
- **Flexible configuration** via JSON

## Quick Start

### 1. Installation
```bash
# Install with UV (recommended)
./install.sh

# Or manually create environment
python -m venv .appsrunner
source .appsrunner/bin/activate
pip install -r requirements.txt
```

### 2. Basic Usage
```bash
# Standard BIDS folder
./run_bids_apps.py -x config.json

# DataLad dataset (auto-detected)
./run_bids_apps.py -x config.json

# Debug mode with detailed logs
./run_bids_apps.py -x config.json --debug

# Process specific subjects
./run_bids_apps.py -x config.json --subjects sub-001 sub-002
```

## Configuration

Create a JSON configuration file with the following structure:

```json
{
  "common": {
    "bids_folder": "/path/to/bids/dataset",
    "output_folder": "/path/to/output",
    "tmp_folder": "/tmp/bids_processing",
    "container": "/path/to/app.sif",
    "templateflow_dir": "/path/to/templateflow",
    "jobs": 4,
    "pilottest": false
  },
  "app": {
    "analysis_level": "participant",
    "options": ["--skip-bids-validation"],
    "apptainer_args": ["--containall"],
    "output_check": {
      "pattern": "{subject}/*",
      "directory": "fmriprep"
    }
  }
}
```

## DataLad Integration

The script automatically detects DataLad datasets and provides enhanced functionality:

### Auto-Detection Features:
- **Dataset detection**: Automatically identifies if input is a DataLad dataset
- **Branch management**: Creates processing branches per subject (optional)
- **Data retrieval**: Automatically gets required data using `datalad get`
- **Result saving**: Saves outputs to DataLad dataset with version control

### DataLad Configuration (Optional):
```json
{
  "datalad": {
    "branch_per_subject": true,
    "output_branch": "results",
    "auto_push": false,
    "get_derivatives": true
  }
}
```

## Command Line Options

```
usage: run_bids_apps.py [-h] -x CONFIG [--log-level {DEBUG,INFO,WARNING,ERROR}]
                        [--dry-run] [--subjects SUBJECTS [SUBJECTS ...]]
                        [--force] [--debug] [--version]

options:
  -x, --config CONFIG   Path to JSON config file
  --log-level LEVEL     Set logging level (default: INFO)
  --dry-run            Show commands without executing them
  --subjects SUB [SUB ...] Process only specified subjects
  --force              Force reprocessing even if output exists
  --debug              Enable detailed container execution logs
  --version            Show program version
```

## Debug Mode

Enable debug mode for detailed troubleshooting:

```bash
./run_bids_apps.py -x config.json --debug --subjects sub-001
```

**Debug features:**
- Real-time container output streaming
- Detailed log files saved to `logs/container_logs/`
- Last 20 lines of stderr on failure
- Container execution timing information

**Log structure:**
```
logs/
├── bids_app_runner_20240101_120000.log    # Main application log
└── container_logs/                        # Debug mode container logs
    ├── container_sub-001_20240101_120000.log
    └── container_sub-001_20240101_120000.err
```

## Examples

### Standard BIDS Processing
```bash
# Process all subjects with fMRIPrep
./run_bids_apps.py -x fmriprep_config.json

# Dry run to test configuration
./run_bids_apps.py -x config.json --dry-run

# Force reprocessing of specific subjects
./run_bids_apps.py -x config.json --force --subjects sub-001
```

### DataLad Workflow
```bash
# Clone DataLad dataset
datalad clone https://example.com/dataset.git my_dataset
cd my_dataset

# Run BIDS app (DataLad features auto-enabled)
./run_bids_apps.py -x config.json

# Check processing branches
git branch -a
```

### Group Analysis
```json
{
  "app": {
    "analysis_level": "group",
    "options": ["--analysis-level", "group"]
  }
}
```

## Error Handling

The script provides comprehensive error handling:

- **Configuration validation**: Checks paths, required fields, and data types
- **Container execution**: Detailed error reporting with log preservation
- **DataLad operations**: Graceful fallback for DataLad failures
- **Signal handling**: Clean shutdown on interruption

## Performance Tips

1. **Parallel processing**: Adjust `jobs` in config based on available CPUs
2. **Debug mode**: Use only for troubleshooting (disables parallel processing)
3. **Output checking**: Configure `output_check` to skip completed subjects
4. **Temporary storage**: Use fast local storage for `tmp_folder`

## Troubleshooting

### Common Issues

1. **Container not found**
   ```
   ERROR: Missing container file: /path/to/app.sif
   ```
   - Check container path in configuration
   - Ensure container file exists and is readable

2. **DataLad detection issues**
   ```
   INFO: DataLad dataset detected, enabling enhanced features
   ```
   - Ensure you're in a DataLad dataset directory
   - Check DataLad installation: `datalad --version`

3. **Permission errors**
   ```
   ERROR: Cannot create directory 'output_folder'
   ```
   - Check write permissions for output and temp directories
   - Ensure parent directories exist

### Debug Mode Usage

```bash
# Debug single subject
./run_bids_apps.py -x config.json --debug --subjects sub-001

# Check container logs
ls -la logs/container_logs/

# View real-time logs
tail -f logs/container_logs/container_sub-001_*.log
```

## Requirements

- **Python 3.8+**
- **Apptainer/Singularity**
- **DataLad** (optional, for enhanced features)
- **Git** (for DataLad datasets)

## Files Created

- `.bidsignore` - BIDS validation exclusions
- `logs/` - Application and container logs
- `requirements-core.txt` - Minimal dependencies
- `requirements.txt` - Full dependencies with development tools
- `install.sh` - Automated environment setup script

---

For HPC/SLURM environments, see `README_HPC.md`.
