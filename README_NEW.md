# BIDS App Runner

**Version 2.0.0** - Production-ready BIDS App execution with DataLad support

## Overview

The BIDS App Runner provides two scripts for flexible BIDS App execution:

### 🖥️ Standard Script (`run_bids_apps.py`)
- **Automatic DataLad detection** - Works with both standard BIDS folders and DataLad datasets
- **Local parallel processing** with multiprocessing
- **Debug mode** with detailed container logs
- **Comprehensive error handling**

### 🚀 HPC Script (`run_bids_apps_hpc.py`)
- **SLURM job scheduling** for HPC environments
- **DataLad integration** for data management
- **Debug mode** with container logs in SLURM jobs
- **Git/Git-annex support** for version control

## Quick Start

### Installation
```bash
# One-command setup with UV
./install.sh

# Or manually
python -m venv .appsrunner
source .appsrunner/bin/activate
pip install -r requirements.txt
```

### Usage Examples

#### Standard Script
```bash
# Standard BIDS folder or DataLad dataset (auto-detected)
./run_bids_apps.py -x config.json

# Debug mode with detailed container logs
./run_bids_apps.py -x config.json --debug --subjects sub-001

# Process specific subjects
./run_bids_apps.py -x config.json --subjects sub-001 sub-002
```

#### HPC Script
```bash
# Submit SLURM jobs for all subjects
./run_bids_apps_hpc.py -x config_hpc.json

# Debug mode with container logs in SLURM jobs
./run_bids_apps_hpc.py -x config_hpc.json --debug --subjects sub-001

# Create job scripts only (no submission)
./run_bids_apps_hpc.py -x config_hpc.json --slurm-only
```

## Documentation

📖 **[README_STANDARD.md](README_STANDARD.md)** - Complete guide for the standard script
- DataLad auto-detection and integration
- Configuration options
- Debug mode usage
- Troubleshooting

📖 **[README_HPC.md](README_HPC.md)** - Complete guide for the HPC script
- SLURM configuration
- DataLad workflow
- Job management
- Performance optimization

## Key Features

### DataLad Integration
Both scripts automatically detect DataLad datasets and provide:
- **Automatic data retrieval** with `datalad get`
- **Result versioning** with `datalad save`
- **Seamless fallback** to standard BIDS folders
- **No configuration changes required**

### Debug Mode
Enhanced debugging capabilities:
- **Real-time container output** streaming
- **Detailed log files** per subject
- **Error context** with last 20 lines of stderr
- **Performance timing** information

### Production-Ready Features
- **Comprehensive error handling** with detailed messages
- **Signal handling** for graceful shutdown
- **Configuration validation** with helpful error messages
- **Structured logging** with timestamps and levels
- **Performance monitoring** and statistics

## Configuration

### Basic Configuration
```json
{
  "common": {
    "bids_folder": "/path/to/bids/dataset",
    "output_folder": "/path/to/output",
    "tmp_folder": "/tmp/bids_processing",
    "container": "/path/to/app.sif",
    "templateflow_dir": "/path/to/templateflow"
  },
  "app": {
    "analysis_level": "participant",
    "options": ["--skip-bids-validation"]
  }
}
```

### HPC Configuration (additional sections)
```json
{
  "hpc": {
    "job_name": "bids_app",
    "partition": "compute",
    "time": "24:00:00",
    "mem": "32GB",
    "cpus": 8
  },
  "datalad": {
    "input_url": "https://example.com/dataset.git",
    "output_url": "https://example.com/results.git"
  }
}
```

## Requirements

- **Python 3.8+**
- **Apptainer/Singularity**
- **DataLad** (optional, for enhanced features)
- **SLURM** (for HPC script)

## Files

- `run_bids_apps.py` - Standard script with DataLad auto-detection
- `run_bids_apps_hpc.py` - HPC script with SLURM integration
- `install.sh` - Automated environment setup
- `requirements.txt` - Python dependencies
- `.bidsignore` - BIDS validation exclusions

---

**Choose your script based on your environment:**
- **Local/workstation**: Use `run_bids_apps.py` (see [README_STANDARD.md](README_STANDARD.md))
- **HPC/cluster**: Use `run_bids_apps_hpc.py` (see [README_HPC.md](README_HPC.md))
