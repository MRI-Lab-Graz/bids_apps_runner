# BIDS Apps Runner - HPC Developer Quick Start

## Installation (5 minutes)

### 1. Clone & Setup
```bash
# Clone the repository
git clone https://github.com/MRI-Lab-Graz/bids_apps_runner.git
cd bids_apps_runner

# Install dependencies (uses UV - ultra-fast Python package manager)
./scripts/install.sh

# Activate virtual environment
source .appsrunner/bin/activate
```

**Prerequisites:**
- Python 3.8+
- UV (install via: `curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Apptainer/Singularity available on HPC
- SLURM (if using job submission)

### 2. Verify Installation
```bash
# Check the runner works
python scripts/prism_runner.py --version

# Quick validation
python scripts/check_system_deps.py
```

---

## Basic Usage - The PRISM Runner

The main entry point is `prism_runner.py`. It auto-detects local vs HPC mode based on environment.

### Local/Direct Execution
```bash
# Run with config file
python scripts/prism_runner.py -c configs/config.json

# Dry run (test config without executing)
python scripts/prism_runner.py -c configs/config.json --dry-run

# Specific subjects
python scripts/prism_runner.py -c configs/config.json --subjects sub-001 sub-002 sub-003

# Force reprocessing
python scripts/prism_runner.py -c configs/config.json --force

# With debug logging
python scripts/prism_runner.py -c configs/config.json --debug --log-level DEBUG

# Parallel jobs
python scripts/prism_runner.py -c configs/config.json --jobs 4
```

### HPC/SLURM Submission
```bash
# Auto-detect SLURM environment and submit jobs
python scripts/prism_runner.py -c configs/config.json --hpc

# Force HPC mode explicitly
python scripts/prism_runner.py -c configs/config.json --hpc --slurm-only

# With monitoring
python scripts/prism_runner.py -c configs/config.json --hpc --monitor

# Dry run (show jobs that would be submitted)
python scripts/prism_runner.py -c configs/config.json --hpc --dry-run
```

---

## Configuration Files

### Create Your Config
```bash
# Copy template
cp configs/config_example.json configs/my_pipeline.json

# Or for HPC with DataLad:
cp configs/config_hpc.json configs/my_hpc_pipeline.json
```

### Minimal Config (Local/HPC)
```json
{
  "common": {
    "bids_folder": "/path/to/bids",
    "output_folder": "/path/to/derivatives",
    "container": "/path/to/app.sif",
    "work_dir": "/tmp/work",
    "log_dir": "/tmp/logs"
  },
  "app": {
    "analysis_level": "participant",
    "options": [
      "--fs-license-file", "/path/to/freesurfer/license.txt",
      "--skip_bids_validation"
    ]
  }
}
```

### HPC Extension (SLURM)
```json
{
  "common": { ... },
  "app": { ... },
  "hpc": {
    "partition": "compute",
    "time": "24:00:00",
    "mem": "32G",
    "cpus": 8,
    "job_name": "fmriprep",
    "modules": [
      "apptainer/1.2.0"
    ],
    "environment": {
      "APPTAINER_CACHEDIR": "/tmp/.apptainer"
    },
    "monitor_jobs": true
  }
}
```

### HPC + DataLad Extension
```json
{
  "common": { ... },
  "app": { ... },
  "hpc": { ... },
  "datalad": {
    "input_repo": "https://github.com/your-lab/bids-dataset.git",
    "output_repo": "https://github.com/your-lab/derivatives.git",
    "get_data": true,
    "branch_per_subject": true,
    "auto_push": false
  }
}
```

See `configs/` directory for more examples.

---

## Output Validation & Reprocessing

### Check for Missing Outputs
```bash
# Validate pipeline outputs
python scripts/check_app_output.py /path/to/bids /path/to/derivatives --output-json missing.json

# This generates a JSON file with subjects missing pipeline outputs
cat missing.json
```

### Auto-Reprocess Missing Subjects
```bash
# Use the missing subjects JSON to automatically reprocess
python scripts/prism_runner.py -c configs/my_pipeline.json --from-json missing.json

# The --force flag is automatically enabled when using --from-json
```

### Validation Only (No Processing)
```bash
python scripts/prism_runner.py -c configs/my_pipeline.json --validate-only
```

---

## Container Building - Apptainer Images

### Build from Docker Hub (Interactive)
```bash
# This will prompt you to select an app and tag
./scripts/build_apptainer.sh \
  -o /path/to/containers/fmriprep.sif \
  -t /tmp/apptainer_build

# It will:
# 1. Let you choose from popular apps (fMRIPrep, QSIPrep, FreeSurfer, etc.)
# 2. Show available Docker tags
# 3. Download and convert to Apptainer format
```

### Build from Docker Hub (Non-Interactive)
```bash
# Direct build without prompts
./scripts/build_apptainer.sh \
  --docker-repo nipreps/fmriprep \
  --docker-tag 24.0.0 \
  -o /data/containers/fmriprep-24.0.0.sif \
  -t /tmp/apptainer_build
```

### Build from Custom Dockerfile
```bash
./scripts/build_apptainer.sh \
  -d /path/to/Dockerfile \
  -o /path/to/custom.sif \
  -t /tmp/apptainer_build
```

### Keep Temporary Build Files
```bash
# By default, temp files are cleaned up. Keep them with:
./scripts/build_apptainer.sh \
  --docker-repo nipreps/qsiprep \
  --docker-tag 1.4.0 \
  -o /data/containers/qsiprep.sif \
  -t /tmp/apptainer_build \
  --no-temp-del
```

### Demo Runs - Quick Testing
```bash
# Build a lightweight test container
./scripts/build_apptainer.sh \
  --docker-repo nipreps/fmriprep \
  --docker-tag 24.0.0 \
  -o /tmp/fmriprep_demo.sif \
  -t /tmp/apptainer_demo

# Create minimal test config
cat > demo_config.json << 'EOF'
{
  "common": {
    "bids_folder": "/data/bids_test",
    "output_folder": "/data/derivatives_test",
    "container": "/tmp/fmriprep_demo.sif",
    "work_dir": "/tmp/fmriprep_work",
    "log_dir": "/tmp/fmriprep_logs"
  },
  "app": {
    "analysis_level": "participant",
    "options": [
      "--skip_bids_validation",
      "--anat-only"
    ]
  }
}
EOF

# Test on one subject
python scripts/prism_runner.py -c demo_config.json \
  --subjects sub-001 \
  --dry-run

# Run for real
python scripts/prism_runner.py -c demo_config.json --subjects sub-001
```

---

## HPC Workflow Examples

### Single Job Submission
```bash
# Generate SLURM script for one subject
python scripts/hpc_datalad_runner.py \
  -c configs/my_hpc.json \
  -s sub-001 \
  -o job_sub001.sh

# Submit to SLURM
sbatch job_sub001.sh

# Check status
squeue -u $USER
```

### Batch Job Submission
```bash
# Submit multiple jobs at once (max 50 concurrent)
python scripts/hpc_batch_submit.py \
  -c configs/my_hpc.json \
  --max-jobs 50 \
  --dry-run  # Remove this to actually submit

# Monitor jobs
watch squeue -u $USER
```

### With DataLad (Managed Input/Output)
```bash
# Config with DataLad repos (see config_hpc_datalad.json)
python scripts/prism_runner.py \
  -c configs/config_hpc_datalad.json \
  --hpc

# This will:
# 1. Clone BIDS repo automatically
# 2. Get data per subject
# 3. Run pipeline
# 4. Push results to output repo
# 5. Update branches
```

---

## Command Reference

### prism_runner.py - Main Runner
```bash
python scripts/prism_runner.py -c CONFIG.json [OPTIONS]

Common Options:
  -c, --config FILE           Config file (required)
  --dry-run                   Test without executing
  --subjects SUBJ [SUBJ ...]  Process specific subjects
  --force                     Force reprocessing
  --debug                     Enable debug logging
  --log-level LEVEL           Set logging level
  --pilot                     Process one random subject (local only)

Local Mode:
  --local                     Force local execution
  --jobs N                    Parallel jobs (default: 1)
  --validate                  Validate outputs after processing
  --validate-only             Only validate, don't process
  --reprocess-missing         Auto-reprocess missing outputs

HPC Mode:
  --hpc                       Force HPC/SLURM mode
  --slurm-only                Skip DataLad processing
  --monitor                   Monitor job completion
```

### check_app_output.py - Validation
```bash
python scripts/check_app_output.py BIDS_FOLDER DERIVATIVES_FOLDER [OPTIONS]

Options:
  --output-json FILE          Save missing subjects to JSON
  --expected-pattern PATTERN  Expected output filename pattern
```

### build_apptainer.sh - Container Building
```bash
./scripts/build_apptainer.sh [OPTIONS]

Options:
  -o, --output DIR            Output directory for .sif file (required)
  -t, --temp DIR              Temp directory for build (required)
  -d, --dockerfile FILE       Use custom Dockerfile
  --docker-repo REPO          Docker repo (e.g., nipreps/fmriprep)
  --docker-tag TAG            Docker image tag
  --no-temp-del               Keep temp files after build
```

### hpc_batch_submit.py - Batch Jobs
```bash
python scripts/hpc_batch_submit.py -c CONFIG.json [OPTIONS]

Options:
  -c, --config FILE           Config file (required)
  --max-jobs N                Max concurrent jobs
  --dry-run                   Show what would be submitted
  --subjects SUBJ [SUBJ ...]  Submit specific subjects
```

---

## Common Patterns

### Pattern 1: Full Pipeline Run with Validation
```bash
# Run
python scripts/prism_runner.py -c configs/my_pipeline.json --jobs 4

# Validate
python scripts/check_app_output.py /path/to/bids /path/to/derivatives

# Reprocess missing
python scripts/prism_runner.py -c configs/my_pipeline.json --reprocess-missing
```

### Pattern 2: HPC with Batch Processing
```bash
# Batch submit to SLURM
python scripts/hpc_batch_submit.py -c configs/my_hpc.json --max-jobs 30

# Monitor
watch -n 5 squeue -u $USER

# Validate when done
python scripts/check_app_output.py /path/to/bids /path/to/derivatives --output-json missing.json

# Resubmit missing
python scripts/hpc_batch_submit.py -c configs/my_hpc.json --from-json missing.json
```

### Pattern 3: Demo Run (Testing)
```bash
# Single subject, dry run
python scripts/prism_runner.py -c configs/my_pipeline.json \
  --subjects sub-001 \
  --dry-run

# If dry run looks good, run it
python scripts/prism_runner.py -c configs/my_pipeline.json --subjects sub-001

# Inspect outputs
ls -lh /path/to/derivatives/sub-001/
```

### Pattern 4: Pilot Test (Random Subject)
```bash
# Pick a random subject and run (local mode only)
python scripts/prism_runner.py -c configs/my_pipeline.json --pilot

# Good for validating setup before full batch
```

---

## Troubleshooting

### Container Not Found
```bash
# Verify container exists
ls -lh /path/to/container.sif

# Check config path matches
grep "container" configs/my_pipeline.json
```

### Subjects Not Found
```bash
# List BIDS subjects
ls -d /path/to/bids/sub-*

# Check subject naming in config
grep "bids_folder" configs/my_pipeline.json
```

### Permission Denied on /tmp
```bash
# Use alternative work directory in config
"work_dir": "/scratch/$USER/work"  # or any writable path

# Or set via environment
export TMPDIR=/scratch/$USER/tmp
```

### SLURM Module Not Loaded
```bash
# Check available modules
module avail

# Add to config HPC section
"modules": [
  "apptainer/1.2.0",
  "gcc/11.2.0"  # or whatever you need
]
```

### Out of Memory
```bash
# Increase in HPC config
"mem": "64G"  # instead of 32G

# Or via command line for local runs
# Some apps accept memory parameters - check their documentation
```

---

## Project Structure

```
bids_apps_runner/
├── scripts/
│   ├── prism_runner.py           # Main entry point
│   ├── build_apptainer.sh        # Container builder
│   ├── hpc_batch_submit.py       # Batch job submission
│   ├── hpc_datalad_runner.py     # DataLad integration
│   ├── check_app_output.py       # Output validation
│   └── install.sh                # Setup script
│
├── configs/
│   ├── config_example.json       # Template
│   ├── config_hpc.json           # HPC template
│   └── config_hpc_datalad.json   # HPC + DataLad template
│
├── docs/
│   ├── HPC_QUICK_REFERENCE.md
│   ├── README_HPC_DATALAD.md
│   └── EXAMPLES_HPC_DATALAD.md
│
└── activate_appsrunner.sh        # Activate venv
```

---

## Advanced: Custom Container Mounting

If your container needs access to external directories:

```json
{
  "app": {
    "mounts": [
      {
        "source": "/usr/local/freesurfer",
        "target": "/fs"
      },
      {
        "source": "/data/templates",
        "target": "/templates"
      }
    ]
  }
}
```

---

## Version Info

Check installed version:
```bash
python scripts/prism_runner.py --version

# View current code version
cat version.py
```

See [CHANGELOG.md](CHANGELOG.md) for updates.

---

**Questions?** Check the full docs in `docs/` or review specific config examples in `configs/`.
