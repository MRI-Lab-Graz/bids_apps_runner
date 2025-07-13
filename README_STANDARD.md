# BIDS App Runner - Standard Version

**Version 2.0.0** - Production-ready BIDS App execution with DataLad auto-detection

## Overview

The standard BIDS App Runner (`run_bids_apps.py`) is a robust tool for executing BIDS Apps on local machines or workstations with:

- **Automatic DataLad detection** - Works seamlessly with both standard BIDS folders and DataLad datasets
- **Local parallel processing** with configurable job counts
- **Debug mode** with detailed container execution logs
- **Comprehensive error handling** and logging
- **Production-ready features** for reliable processing

## Quick Start

### Installation
```bash
# One-command setup with UV (recommended)
./install.sh

# Or manually create environment
python -m venv .appsrunner
source .appsrunner/bin/activate
pip install -r requirements.txt
```

### Basic Usage
```bash
# Process all subjects (standard BIDS folder or DataLad dataset - auto-detected)
./run_bids_apps.py -x config.json

# Process specific subjects (space-separated)
./run_bids_apps.py -x config.json --subjects sub-001 sub-002 sub-003

# Debug mode with detailed container logs
./run_bids_apps.py -x config.json --debug --subjects sub-001

# Test configuration without running
./run_bids_apps.py -x config.json --dry-run

# Force reprocessing even if output exists
./run_bids_apps.py -x config.json --force --subjects sub-001
```

## Configuration

Create a JSON configuration file:

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
      "pattern": "sub-{subject}.html"
    }
  }
}
```

### Configuration Sections

#### Common Section
- **`bids_folder`**: Path to BIDS dataset directory
- **`output_folder`**: Path to output directory  
- **`tmp_folder`**: Path to temporary directory for processing
- **`container`**: Path to Apptainer/Singularity container
- **`templateflow_dir`**: Path to TemplateFlow directory
- **`jobs`**: Number of parallel jobs (default: CPU count)
- **`pilottest`**: If true, process only one random subject

#### App Section
- **`analysis_level`**: Analysis level (participant, group)
- **`options`**: List of command-line options to pass to the app
- **`apptainer_args`**: Additional Apptainer arguments
- **`output_check`**: Pattern to check for successful processing
- **`mounts`**: Additional bind mounts for the container

### Example: QSIPrep Configuration
```json
{
  "common": {
    "bids_folder": "/data/bids",
    "output_folder": "/data/derivatives/qsiprep",
    "tmp_folder": "/tmp/qsiprep_work",
    "container": "/containers/qsiprep_0.24.0.sif",
    "templateflow_dir": "/data/templateflow",
    "jobs": 4
  },
  "app": {
    "analysis_level": "participant",
    "options": [
      "--fs-license-file", "/fs/license.txt",
      "--nprocs", "8",
      "--skip-bids-validation",
      "--output-resolution", "1.2"
    ],
    "mounts": [
      { "source": "/usr/local/freesurfer", "target": "/fs" }
    ],
    "output_check": {
      "pattern": "sub-{subject}.html"
    }
  }
}
```

## DataLad Integration

The script **automatically detects** DataLad datasets and provides enhanced functionality without requiring any configuration changes:

### Auto-Detection Features
- **Dataset detection**: Checks for `.datalad/config` in input/output folders
- **Data retrieval**: Automatically runs `datalad get` for required subjects
- **Result saving**: Automatically runs `datalad save` after successful processing
- **Graceful fallback**: Works normally with standard BIDS folders when DataLad not detected

### DataLad Workflow Example
```bash
# Clone DataLad dataset
datalad clone https://example.com/dataset.git my_dataset
cd my_dataset

# Run BIDS app (DataLad features automatically enabled)
./run_bids_apps.py -x config.json

# Results are automatically saved with version control
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
  --subjects SUB [SUB ...] Process only specified subjects (space-separated)
  --force              Force reprocessing even if output exists
  --debug              Enable detailed container execution logs
  --version            Show program version
```

## Debug Mode

Enable debug mode for detailed troubleshooting:

```bash
# Debug single subject with detailed logs
./run_bids_apps.py -x config.json --debug --subjects sub-001

# Debug multiple subjects
./run_bids_apps.py -x config.json --debug --subjects sub-001 sub-002
```

### Debug Features
- **Real-time container output** streaming to console
- **Detailed log files** saved to `logs/container_logs/`
- **Error context** with last 20 lines of stderr on failure
- **Container execution timing** information
- **Serial processing** (parallel processing disabled in debug mode)

### Log Structure
```
logs/
├── bids_app_runner_20240101_120000.log    # Main application log
└── container_logs/                        # Debug mode container logs
    ├── container_sub-001_20240101_120000.log
    ├── container_sub-001_20240101_120000.err
    ├── container_sub-002_20240101_120000.log
    └── container_sub-002_20240101_120000.err
```

## Output Validation

Configure output checking to skip already processed subjects:

```json
{
  "app": {
    "output_check": {
      "pattern": "sub-{subject}.html",
      "directory": "reports"  // Optional subdirectory
    }
  }
}
```

The script will look for files matching the pattern and skip subjects that have already been processed (unless `--force` is used).

## Examples

### Process All Subjects
```bash
./run_bids_apps.py -x config.json
```

### Process Specific Subjects
```bash
# With sub- prefix
./run_bids_apps.py -x config.json --subjects sub-001 sub-002 sub-003

# Without sub- prefix (automatically added)
./run_bids_apps.py -x config.json --subjects 001 002 003
```

### Testing and Debugging
```bash
# Test configuration
./run_bids_apps.py -x config.json --dry-run

# Debug specific subject
./run_bids_apps.py -x config.json --debug --subjects sub-001

# Force reprocessing
./run_bids_apps.py -x config.json --force --subjects sub-001
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
- **Signal handling**: Clean shutdown on interruption (Ctrl+C)
- **Output verification**: Validates successful processing completion

## Performance Tips

1. **Parallel processing**: Adjust `jobs` in config based on available CPUs
2. **Debug mode**: Use only for troubleshooting (forces serial processing)
3. **Output checking**: Configure `output_check` to skip completed subjects
4. **Temporary storage**: Use fast local storage for `tmp_folder`
5. **Memory management**: Monitor system resources and adjust job count accordingly

## Troubleshooting

### Common Issues

1. **Container not found**
   ```
   ERROR: Missing container file: /path/to/app.sif
   ```
   - Check container path in configuration
   - Ensure container file exists and is readable

2. **Processing reported as failed but container succeeded**
   ```
   ERROR: Processing failed for sub-001 - no expected output found
   ```
   - Check `output_check` pattern in configuration
   - Verify the pattern matches actual output files
   - Use `--debug` mode to see detailed container logs

3. **Permission errors**
   ```
   ERROR: Cannot create directory 'output_folder'
   ```
   - Check write permissions for output and temp directories
   - Ensure parent directories exist

4. **DataLad issues** (when using DataLad datasets)
   ```
   WARNING: DataLad command failed
   ```
   - Check DataLad installation: `datalad --version`
   - Verify dataset is properly initialized
   - Check network connectivity for remote datasets

### Debug Workflow

```bash
# 1. Test configuration
./run_bids_apps.py -x config.json --dry-run

# 2. Debug single subject
./run_bids_apps.py -x config.json --debug --subjects sub-001

# 3. Check container logs
ls -la logs/container_logs/
cat logs/container_logs/container_sub-001_*.log

# 4. View real-time logs
tail -f logs/container_logs/container_sub-001_*.log
```

## Requirements

- **Python 3.8+**
- **Apptainer/Singularity**
- **DataLad** (optional, for enhanced features)
- **Git** (for DataLad datasets)
- **Sufficient disk space** for temporary files and outputs

## Files Created

- **`logs/`** - Application and container logs
- **`.bidsignore`** - BIDS validation exclusions
- **`requirements.txt`** - Python dependencies
- **`install.sh`** - Automated environment setup script

---

**For HPC/SLURM environments, use the HPC version instead.**
