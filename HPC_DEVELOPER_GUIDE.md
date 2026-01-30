# BIDS Apps Runner - Developer & HPC Guide (Current)

This document reflects the current behavior of the BIDS App Runner GUI + CLI.

---

## 1) Installation

### Prerequisites
- Python 3.8+
- UV package manager
- Apptainer/Singularity (for container execution)
- SLURM available on HPC (optional for local runs)

### Clone & Install
```bash
git clone https://github.com/MRI-Lab-Graz/bids_apps_runner.git
cd bids_apps_runner

# Core (CLI only)
./scripts/install.sh

# Full (GUI + CLI)
./scripts/install.sh --full

source .appsrunner/bin/activate
```

### Verify
```bash
python scripts/prism_runner.py --version
python scripts/check_system_deps.py
```

---

## 2) How the System Works (High Level)

The project uses **project.json** as the single source of truth for a project. The GUI edits this file; the CLI runner consumes it.

Key concepts:
- **Project** = a folder under projects/ containing project.json
- **Container options** are dynamically loaded the first time (from container help)
- **After a project is saved**, container options are locked to avoid unexpected changes
- **HPC settings** live in the project.json under the `hpc` section (editable in the GUI under Advanced)

---

## 3) GUI Usage (Recommended)

Start the GUI:
```bash
python prism_app_runner.py
```

### Project Workflow
1. Create or load a project in the **Projects** tab.
2. Configure container + options in **Run App**.
3. Save → writes to project.json.
4. For HPC settings, open **HPC** tab → expand **Advanced: SLURM Settings** → edit + save.

### Container Locking (Important)

Behavior:
- **First time:** container options auto-load from the container help.
- **After saving:** options are locked and will **not** auto-reload.
- **If container path changes:** options unlock and auto-reload again on next load.

This is controlled via:
```json
"common": {
  "container": "/path/to/container.sif",
  "container_locked": true
}
```

---

## 4) CLI Usage

The CLI still works directly with JSON configs:
```bash
python scripts/prism_runner.py -c configs/config.json
```

Useful flags:
```bash
--dry-run
--subjects sub-001 sub-002
--force
--debug
--log-level DEBUG
--jobs 4
```

---

## 5) SLURM / HPC Settings (project.json)

The following keys are used for SLURM execution and are editable in the GUI under **Advanced: SLURM Settings**.

### Required/Supported Fields
```json
"hpc": {
  "partition": "compute",
  "time": "24:00:00",
  "mem": "32G",
  "cpus": 8,
  "job_name": "fmriprep",
  "output_pattern": "slurm-%j.out",
  "error_pattern": "slurm-%j.err",
  "modules": [
    "apptainer/1.2.0",
    "datalad/0.19.0"
  ],
  "environment": {
    "TEMPLATEFLOW_HOME": "/data/shared/templateflow",
    "APPTAINER_CACHEDIR": "/tmp/.apptainer"
  },
  "monitor_jobs": true
}
```

Notes:
- `modules` is a list; the GUI uses one module per line.
- `environment` is JSON in the GUI; it must be valid JSON.
- These settings are **not** automatically changed by the system.

---

## 6) Output Validation

Validation is available from the GUI or CLI.

CLI example:
```bash
python scripts/check_app_output.py /path/to/bids /path/to/derivatives --output-json missing.json
```

---

## 7) Container Build (Apptainer)

Build from Docker Hub (interactive):
```bash
./scripts/build_apptainer.sh -o /path/to/containers/fmriprep.sif -t /tmp/apptainer_build
```

Build from Docker Hub (non-interactive):
```bash
./scripts/build_apptainer.sh --docker-repo nipreps/fmriprep --docker-tag 25.2.3 -o /data/containers/fmriprep_25.2.3.sif -t /tmp/apptainer_build
```

---

## 8) File Locations

- Project storage: projects/<project_id>/project.json
- Logs: logs/
- Templates and GUI: templates/, static/

---

## 9) Troubleshooting

### GUI missing dependencies
Install full mode:
```bash
./scripts/install.sh --full
```

### Validation fails to run
Ensure check_app_output.py is in scripts/ and that GUI runs from repo root.

### Container options not updating
Check container lock:
```json
"container_locked": true
```
Set to false (or change container path) to re-fetch options.
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
