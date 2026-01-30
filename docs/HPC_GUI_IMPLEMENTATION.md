# HPC/SLURM Web GUI Implementation

## Overview
A complete web-based interface for managing HPC SLURM jobs with DataLad integration has been successfully implemented in the BIDS Apps Runner GUI.

## What Was Added

### 1. New "HPC/SLURM" Tab in Navigation
Located between "Run BIDS App" and "Check App Output" tabs, featuring a server icon and clear labeling.

### 2. HPC Environment Check
- **Feature**: One-click verification of available HPC tools
- **Checks**: SLURM, DataLad, Git, Git-Annex, Apptainer, HPC Runner module
- **Display**: Visual badges showing availability status (green/red)
- **Endpoint**: Uses `/check_hpc_environment` backend endpoint

### 3. Configuration Management
- **Load Config**: Browse and load HPC config files (JSON)
- **Display Settings**: Shows SLURM settings from loaded config:
  - Partition name
  - Walltime
  - Memory allocation
  - CPU count
  - Modules to load
  - Container image path
- **Subject ID**: Input field for specifying which subject to process

### 4. SLURM Script Generation
- **Generate**: Creates SLURM script with DataLad workflow
- **Preview**: View generated script in formatted code block
- **Save**: Write script to disk with execute permissions
- **Endpoint**: Uses `/generate_hpc_script` backend endpoint

### 5. Job Submission Interface
- **Script Path**: Specify saved SLURM script location
- **Dry Run Mode**: Optional checkbox to simulate without submitting
- **Submit Button**: Send job to SLURM queue via `sbatch`
- **Endpoint**: Uses `/submit_hpc_job` backend endpoint

### 6. Job Monitoring
- **Status Display**: Shows current job state and ID
- **Job Table**: Lists all tracked jobs with:
  - Job ID
  - Subject ID
  - Current status
  - Execution time
  - Assigned node(s)
  - Cancel button for each job
- **Check Status Button**: Refresh job status from SLURM
- **Cancel Job**: Cancel running jobs
- **Endpoints**: Uses `/get_hpc_job_status` and `/cancel_hpc_job`

### 7. Console Output
- **Live Log**: Real-time logging of all HPC operations
- **Error Highlighting**: Distinguishes errors from info messages
- **Clear Button**: Reset log output
- **Timestamps**: Each log entry includes timestamp

## File Changes

### `templates/index.html` (Modified)
- Added HPC tab button to navigation bar
- Added complete HPC/SLURM panel with:
  - Configuration section
  - Script generation controls
  - Job submission interface
  - Job monitoring table
  - Console output
- Added 10 JavaScript functions for HPC operations:
  - `checkHPCEnvironment()`
  - `loadHPCConfig()`
  - `generateHPCScript()`
  - `toggleScriptPreview()`
  - `saveHPCScript()`
  - `submitHPCJob()`
  - `checkJobStatus()`
  - `cancelHPCJob()`
  - `cancelSpecificJob(jobId)`
  - `logHPC(message, isError)`
  - `clearHPCLog()`

## Workflow

### Typical User Flow:
```
1. Click "HPC/SLURM" tab
2. Click "Check Environment" to verify HPC tools are available
3. Enter config file path and click "Load"
4. Enter subject ID
5. Click "Generate SLURM Script"
6. (Optional) Click "Preview Script" to review
7. Click "Save Script"
8. (Optional) Check "Dry Run" for simulation
9. Click "Submit to SLURM"
10. View job status in table
11. Click "Check Status" to refresh
12. Click "Cancel Job" if needed
```

## Backend API Integration

The GUI calls these existing Flask endpoints in `app_gui.py`:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/check_hpc_environment` | GET | Check available HPC tools |
| `/generate_hpc_script` | POST | Generate SLURM script |
| `/save_hpc_script` | POST | Save script to disk |
| `/submit_hpc_job` | POST | Submit to SLURM queue |
| `/get_hpc_job_status` | POST | Check job status |
| `/cancel_hpc_job` | POST | Cancel running job |

## Features

✅ **Environment Detection**: Automatically checks for SLURM, DataLad, and other tools
✅ **Config Loading**: Parse and display HPC settings from JSON config
✅ **Script Generation**: Automatic SLURM script creation with DataLad workflow
✅ **Script Preview**: View generated scripts before saving
✅ **Job Submission**: Submit scripts to SLURM queue
✅ **Real-time Monitoring**: Check job status via `squeue`
✅ **Job Cancellation**: Cancel running jobs via `scancel`
✅ **Console Logging**: Live log of all operations
✅ **Error Handling**: Clear error messages for debugging
✅ **Dry Run Mode**: Test submissions without actually running jobs

## UI/UX Design

- **Responsive Layout**: Works on desktop and tablet
- **Color Coded**: Success (green), warning (yellow), error (red)
- **Progress Indicators**: Loading states and status badges
- **Organized Sections**: Logical grouping of related controls
- **Clear Labels**: Helpful tooltips and descriptions
- **Professional Styling**: Consistent with rest of GUI

## Testing Recommendations

1. **Environment Check**:
   ```bash
   # Should show SLURM available if on HPC
   Click "Check Environment"
   ```

2. **Config Loading**:
   ```bash
   # Test with config_hpc_datalad.json
   # Should display SLURM settings
   ```

3. **Script Generation**:
   ```bash
   # Test with valid config and subject
   # Should generate and preview script
   ```

4. **Job Submission** (on HPC system):
   ```bash
   # Test with dry-run first
   # Then actual submission
   sbatch --dry-run job.sh
   sbatch job.sh
   ```

5. **Status Monitoring**:
   ```bash
   # Should sync with squeue
   squeue -u $USER
   ```

## Next Steps / Future Enhancements

- [ ] Template library for common HPC configurations
- [ ] Batch subject submission (multiple subjects)
- [ ] Email/Slack notifications on job completion
- [ ] Cost estimation based on SLURM settings
- [ ] Job history and archival
- [ ] Advanced filtering and search for job status
- [ ] Output directory auto-linking
- [ ] SLURM command examples in tooltips

## Compatibility

- ✅ Works with existing HPC DataLad runner
- ✅ Compatible with SLURM job scheduler
- ✅ Supports DataLad workflows
- ✅ Works with Apptainer/Singularity containers
- ✅ No breaking changes to existing GUI functionality

## Notes

- The frontend provides the UI for all major HPC operations
- Backend endpoints in `app_gui.py` handle actual SLURM interaction
- All operations logged to console for debugging
- Configuration validation done by backend
- Job tracking maintained in frontend during session
