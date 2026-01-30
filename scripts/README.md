# Scripts Directory

This directory contains all scripts and utilities for the BIDS Apps Runner project.

## Core Scripts (Keep - Essential)

### Main Runners
- **`run_bids_apps.py`** (81KB) - Main BIDS app execution engine for local/cluster processing
  - Handles configuration, parallelization, logging
  - Required by prism_app_runner.py
  
- **`run_bids_apps_hpc.py`** (32KB) - HPC runner with SLURM integration
  - DataLad-aware batch submission
  - Used for HPC environments

- **`hpc_batch_submit.py`** (7.3KB) - SLURM job submission wrapper
  - Required by HPC workflows
  
- **`hpc_datalad_runner.py`** (16KB) - DataLad integration for HPC
  - Script generator for DataLad workflows
  - Required by prism_app_runner.py (HPC mode)

### Validation & Analysis
- **`check_app_output.py`** (77KB) - Output validation for all pipelines
  - Validates fMRIPrep, QSIPrep, FreeSurfer, QSIRecon outputs
  - Required by prism_app_runner.py
  - Heavily used in workflows

- **`bids_validation_integration.py`** (17KB) - BIDS validator integration
  - Not currently imported by main app
  - **ASSESSMENT**: Useful utility but could be standalone

### System Management
- **`check_system_deps.py`** (3.5KB) - Dependency checker
  - Validates Apptainer, containers, system requirements
  - **ASSESSMENT**: Good utility, but not imported by main app

## Installation & Environment (Keep)

- **`install.sh`** (9.1KB) - Main installation script
  - Sets up virtual environment
  - Installs dependencies
  
- **`activate_appsrunner.sh`** (670B) - Virtual environment activation helper
  - Simple wrapper for development

## Build & Container Scripts (Keep)

- **`build_apptainer.sh`** (13KB) - Apptainer/Singularity container builder
  - Converts Docker images to Apptainer
  - Used in documentation

- **`manage_datalad_repos.sh`** (9.0KB) - DataLad repository management
  - Clone, unlock, commit operations
  - Required for HPC DataLad workflows

## BIDS Utility Scripts (Evaluate - May be project-specific)

### Event File Utilities
- **`copy_events_to_bids.py`** (3.6KB) - Copy event TSVs to BIDS structure
  - Validates subject/session/task matching
  - **ASSESSMENT**: Project-specific, consider moving to separate utils repo or examples/

- **`rename_al_events.py`** (4.9KB) - Rename legacy nf_* logs to BIDS format
  - Specific to "AL" project naming conventions
  - **ASSESSMENT**: Project-specific, move to examples/ or remove

### BIDS Metadata Utilities
- **`fix_bids_intendedfor.py`** (2.7KB) - Fix fmap IntendedFor fields
  - Removes BIDS URI prefixes
  - **ASSESSMENT**: Useful utility but project-specific, consider examples/

- **`fmriprep2conn.sh`** (3.4KB) - Reorganize fMRIPrep output structure
  - Standardizes single vs multi-session outputs
  - **ASSESSMENT**: Project-specific, consider examples/

## Process Management (Evaluate)

- **`kill_app.sh`** (2.4KB) - Kill qsirecon processes by search phrase
  - Loop mode with timeout
  - **ASSESSMENT**: Useful but very specific to qsirecon, consider making generic or moving to examples/

## Development/Testing Scripts (Evaluate - Remove?)

- **`test_docker_api.py`** (570B) - Quick test of Docker Hub API
  - Simple requests test
  - **ASSESSMENT**: Development testing only, consider removing or moving to tests/

- **`test_parse.py`** (2.3KB) - Test argparse help output parsing
  - Tests help text parsing logic
  - **ASSESSMENT**: Development testing only, move to tests/ directory

## Large External Script (Evaluate - Remove?)

- **`mri_synthseg_original.py`** (114KB) - FreeSurfer SynthSeg segmentation tool
  - Complete standalone tool (2582 lines)
  - Requires TensorFlow, complex dependencies
  - **ASSESSMENT**: This is an external tool, not part of core BIDS Apps Runner
  - **RECOMMENDATION**: Remove or move to separate tools/ directory
  - If needed, users should use official FreeSurfer installation

---

## Recommendations

### Immediate Actions:

1. **Create `tests/` directory** - Move test scripts there:
   - `test_docker_api.py` → `tests/test_docker_api.py`
   - `test_parse.py` → `tests/test_parse.py`

2. **Create `examples/` or `utils/` directory** - Move project-specific utilities:
   - `copy_events_to_bids.py`
   - `rename_al_events.py`
   - `fix_bids_intendedfor.py`
   - `fmriprep2conn.sh`
   - `kill_app.sh` (or make it generic)

3. **Remove or relocate**:
   - `mri_synthseg_original.py` - This doesn't belong in BIDS Apps Runner
     - It's a complete external tool (114KB, 2582 lines)
     - Users should use official FreeSurfer/SynthSeg
     - If needed, create separate `external_tools/` directory

4. **Add documentation**:
   - Add this README.md with script descriptions
   - Add usage examples for each utility

### Proposed New Structure:

```
scripts/
├── README.md                          # This file
├── run_bids_apps.py                   # Core runners
├── run_bids_apps_hpc.py
├── hpc_batch_submit.py
├── hpc_datalad_runner.py
├── check_app_output.py                # Validation
├── bids_validation_integration.py
├── check_system_deps.py               # System checks
├── install.sh                         # Installation
├── activate_appsrunner.sh
├── build_apptainer.sh                 # Containers
└── manage_datalad_repos.sh

tests/                                 # NEW
├── test_docker_api.py
└── test_parse.py

utils/                                 # NEW - Optional BIDS utilities
├── README.md
├── copy_events_to_bids.py
├── rename_al_events.py
├── fix_bids_intendedfor.py
├── fmriprep2conn.sh
└── kill_app.sh
```

### Files to Remove Entirely:
- `mri_synthseg_original.py` - External tool, not part of this project

---

## Size Summary
- **Core essential scripts**: ~230KB (8 files)
- **Installation/build**: ~22KB (4 files)  
- **BIDS utilities** (move to utils/): ~17KB (5 files)
- **Tests** (move to tests/): ~3KB (2 files)
- **Remove**: 114KB (1 file - mri_synthseg_original.py)

**Total reduction**: Can reduce scripts/ from 358KB to ~252KB by moving/removing non-core files.
