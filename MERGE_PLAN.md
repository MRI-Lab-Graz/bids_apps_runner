# Merge Plan: Unified prism_runner.py

## Current State
- `run_bids_apps.py` (1951 lines) - Local/cluster execution with multiprocessing
- `run_bids_apps_hpc.py` (905 lines) - HPC/SLURM execution with job submission

## Key Differences

### Execution Mode
- **Local**: Uses Python multiprocessing for parallel subjects
- **HPC**: Generates SLURM job scripts and submits them

### Shared Functions (can be unified)
1. `setup_logging()` - Nearly identical
2. `read_config()` - Identical
3. `validate_common_config()` - Similar
4. `validate_app_config()` - Similar
5. `subject_processed()` - Similar logic
6. DataLad functions - Can be shared

### HPC-Specific Functions (keep separate)
1. `validate_hpc_config()` - HPC only
2. `validate_datalad_config()` - HPC only  
3. `create_slurm_job()` - HPC only
4. `submit_slurm_job()` - HPC only
5. `monitor_jobs()` - HPC only

### Local-Specific Functions (keep separate)
1. `build_common_mounts()` - Local execution
2. `run_subject()` - Local multiprocessing
3. Validation integration - Local only

## Merge Strategy

### Phase 1: Create unified prism_runner.py
1. Auto-detect HPC mode from config (presence of "hpc" section)
2. Add `--hpc` flag for explicit mode
3. Shared code at top
4. Mode-specific code in conditional blocks
5. Single main() that routes to appropriate execution

### Phase 2: Update references
1. Update prism_app_runner.py to use new script name
2. Update activate script references
3. Update README examples

### Phase 3: Rename other scripts
1. `check_app_output.py` → `prism_check.py`
2. `build_apptainer.sh` → `prism_build.sh`
3. `check_system_deps.py` → `prism_deps.py`

### Phase 4: Create symlinks for backward compatibility
1. `run_bids_apps.py` → `prism_runner.py`
2. `run_bids_apps_hpc.py` → `prism_runner.py`

## Config Detection Logic

```python
def detect_execution_mode(config):
    """Auto-detect execution mode from config."""
    if 'hpc' in config:
        return 'hpc'
    return 'local'
```

## Command Examples After Merge

```bash
# Auto-detect (checks for "hpc" section in config)
python scripts/prism_runner.py -c config.json

# Explicit local mode
python scripts/prism_runner.py -c config.json --local

# Explicit HPC mode
python scripts/prism_runner.py -c config.json --hpc

# All other flags remain the same
python scripts/prism_runner.py -c config.json --subjects sub-001 --dry-run
```
