# BIDS App Runner - HPC Version

**Version 2.0.0** - Production-ready BIDS App execution for High Performance Computing

## Overview

The HPC BIDS App Runner (`run_bids_apps_hpc.py`) is designed for High Performance Computing environments with:
- **SLURM job scheduling** instead of multiprocessing
- **DataLad integration** for BIDS dataset management
- **Git/Git-annex** for data versioning
- **Debug mode** with detailed container execution logs in SLURM jobs
- **Separate output repositories** for results

## Quick Start

### 1. Installation on HPC
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
# Submit SLURM jobs for all subjects
./run_bids_apps_hpc.py -x config_hpc.json

# Create job scripts only (no submission)
./run_bids_apps_hpc.py -x config_hpc.json --slurm-only

# Debug mode with container logs
./run_bids_apps_hpc.py -x config_hpc.json --debug --subjects sub-001

# Dry run to test configuration
./run_bids_apps_hpc.py -x config_hpc.json --dry-run
```

## Configuration

HPC configuration requires additional sections for SLURM and DataLad:

```json
{
  "common": {
    "bids_folder": "/data/bids/dataset",
    "output_folder": "/data/output",
    "tmp_folder": "/scratch/bids_processing",
    "container": "/data/containers/app.sif",
    "templateflow_dir": "/data/shared/templateflow",
    "work_dir": "/scratch/work"
  },
  "app": {
    "analysis_level": "participant",
    "options": ["--skip-bids-validation"],
    "apptainer_args": ["--containall"]
  },
  "hpc": {
    "job_name": "bids_app",
    "partition": "compute",
    "time": "24:00:00",
    "mem": "32GB",
    "cpus": 8,
    "modules": ["apptainer", "datalad"],
    "environment": {
      "TEMPLATEFLOW_HOME": "/data/shared/templateflow"
    },
    "output_pattern": "output_%j.log",
    "error_pattern": "error_%j.log",
    "monitor_jobs": true
  },
  "datalad": {
    "input_url": "https://example.com/dataset.git",
    "output_url": "https://example.com/results.git",
    "branch_per_subject": true,
    "output_branch": "results",
    "auto_push": false
  }
}
```

## DataLad Workflow

The HPC script is built around DataLad for reproducible data management:

### Workflow Steps
1. **Setup**: Clones input and output DataLad datasets
2. **Job Creation**: Generates SLURM job scripts for each subject
3. **Data Retrieval**: Each job gets subject data via `datalad get`
4. **Processing**: Runs BIDS app in isolated environment
5. **Result Saving**: Saves outputs to DataLad dataset with versioning

### Branch Management
- **Input branches**: `processing-{subject}` for each subject
- **Output branch**: Configurable (default: `results`)
- **Automatic cleanup**: Temporary branches cleaned after successful processing

## Command Line Options

```
usage: run_bids_apps_hpc.py [-h] -x CONFIG [--log-level {DEBUG,INFO,WARNING,ERROR}]
                            [--dry-run] [--subjects SUBJECTS [SUBJECTS ...]]
                            [--slurm-only] [--job-template JOB_TEMPLATE]
                            [--force] [--debug] [--version]

options:
  -x, --config CONFIG   Path to JSON config file
  --log-level LEVEL     Set logging level (default: INFO)
  --dry-run            Show commands without executing them
  --subjects SUB [SUB ...] Process only specified subjects
  --slurm-only         Create job scripts but don't submit to SLURM
  --job-template FILE  Custom SLURM job template
  --force              Force reprocessing even if output exists
  --debug              Enable detailed container execution logs in SLURM jobs
  --version            Show program version
```

## Debug Mode for HPC

Debug mode in HPC environments provides detailed container logging within SLURM jobs:

```bash
./run_bids_apps_hpc.py -x config.json --debug --subjects sub-001
```

**Debug features:**
- Container stdout/stderr saved to dedicated log files
- Real-time log streaming during execution
- Log files stored in `work_dir/container_logs/`
- Integrates with SLURM job logging

**Log structure:**
```
work_dir/
├── logs/                    # SLURM job logs
│   ├── output_123456.log
│   └── error_123456.log
└── container_logs/          # Container execution logs (debug mode)
    ├── container_sub-001_20240101_120000.log
    └── container_sub-001_20240101_120000.err
```

## Requirements

- **Python 3.8+**
- **SLURM workload manager**
- **Apptainer/Singularity**
- **DataLad**
- **Git with Git-annex**

---

For standard/local environments, see `README_STANDARD.md`.
