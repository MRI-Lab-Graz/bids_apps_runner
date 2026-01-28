# HPC/SLURM GUI Quick Start

## ✅ Implementation Complete

The HPC/SLURM web interface has been successfully implemented and integrated into the BIDS Apps Runner GUI!

## How to Use

### 1. Start the GUI
```bash
cd /data/local/software/bids_apps_runner
python app_gui.py
```

The GUI will open at `http://localhost:8080` (or next available port)

### 2. Navigate to HPC Tab
Click the **"HPC/SLURM"** tab in the navigation bar (with server icon)

### 3. Check HPC Environment
Click **"Check Environment"** to verify:
- ✅ SLURM (sbatch, squeue, scancel)
- ✅ DataLad
- ✅ Git & Git-Annex
- ✅ Apptainer/Singularity
- ✅ HPC DataLad Runner module

### 4. Load Configuration
1. Enter config file path: `/path/to/config_hpc_datalad.json`
2. Click **"Load"**
3. View SLURM settings (partition, time, memory, CPUs)

### 5. Generate SLURM Script
1. Enter Subject ID (e.g., `sub-001`)
2. Click **"Generate SLURM Script"**
3. (Optional) Click **"Preview Script"** to review
4. Click **"Save Script"** to write to disk

### 6. Submit to HPC
1. Script path will be auto-filled after save
2. (Optional) Check **"Dry Run"** to simulate
3. Click **"Submit to SLURM"**
4. Job ID appears in status

### 7. Monitor Jobs
1. Click **"Check Status"** to refresh
2. View all jobs in the table
3. Click **"Cancel Job"** to stop running job
4. Monitor live console output

## What's Implemented

| Feature | Status | Notes |
|---------|--------|-------|
| HPC Tab in GUI | ✅ | Between "Run BIDS App" and "Check Output" |
| Environment Check | ✅ | Detects SLURM, DataLad, Git, etc. |
| Config Loading | ✅ | Load HPC config JSON files |
| Script Generation | ✅ | Creates SLURM scripts with DataLad workflow |
| Script Preview | ✅ | View generated scripts before saving |
| Script Saving | ✅ | Save to disk with execute permissions |
| Job Submission | ✅ | Submit to SLURM via sbatch |
| Job Monitoring | ✅ | Check status via squeue |
| Job Cancellation | ✅ | Cancel jobs via scancel |
| Console Logging | ✅ | Real-time operation logging |
| Dry Run Mode | ✅ | Test submissions without executing |

## API Endpoints

All HPC operations use the backend Flask endpoints in `app_gui.py`:

```javascript
// Check environment
GET /check_hpc_environment

// Generate script
POST /generate_hpc_script
{
  "config_path": "/path/to/config.json",
  "subject": "sub-001"
}

// Save script
POST /save_hpc_script
{
  "script": "#!/bin/bash\n...",
  "subject": "sub-001",
  "output_dir": "/tmp/hpc_scripts"
}

// Submit to SLURM
POST /submit_hpc_job
{
  "script_path": "/tmp/hpc_scripts/job_sub-001.sh",
  "dry_run": false
}

// Check job status
POST /get_hpc_job_status
{
  "job_ids": ["12345"]
}

// Cancel job
POST /cancel_hpc_job
{
  "job_id": "12345"
}
```

## Example Workflow

### On HPC System with DataLad:

```bash
# 1. Prepare config
cp config_hpc_datalad.json my_config.json
# Edit with your settings...

# 2. Start GUI
python app_gui.py

# 3. In browser:
#   - Click HPC/SLURM tab
#   - Check Environment
#   - Load my_config.json
#   - Enter subject ID
#   - Generate & save script
#   - Submit to SLURM
#   - Monitor status

# 4. Check from command line
squeue -u $USER
```

## Console Output

The HPC tab shows real-time logging:

```
14:23:45 [INFO] Environment check complete
14:23:47 [INFO] Config path set: /path/to/config.json
14:23:50 [INFO] Generating script for sub-001...
14:23:52 [INFO] Script generated successfully for sub-001
14:23:53 [INFO] Script saved: /tmp/hpc_scripts/job_sub-001.sh
14:23:55 [INFO] Submitting job...
14:23:56 [INFO] Job submitted! Job ID: 12345
```

## Error Handling

Errors appear in red in the console:

```
14:23:45 [ERROR] Please specify config path
14:23:47 [ERROR] Failed to generate script: Invalid configuration
```

## Dry Run Mode

Test submissions without actually running:

```
[INFO] DRY RUN: Submitting job...
[INFO] Job submitted! Job ID: DRY_RUN_JOB_ID
```

The dry-run submission shows what would happen without executing.

## Features

🎯 **Single-Click Operations**: All HPC operations available with one click

🔍 **Live Monitoring**: Real-time job status from SLURM

📊 **Status Dashboard**: Table showing all tracked jobs

🎨 **Professional UI**: Clean, intuitive interface matching rest of GUI

⚡ **Full Integration**: Works seamlessly with existing BIDS Apps Runner

## Testing

### Quick Test (without HPC):
```bash
# Start GUI
python app_gui.py

# Click HPC/SLURM tab
# Click "Check Environment"
# Should show available tools (DataLad, Git, Apptainer)
# SLURM will show as unavailable (not on HPC system)
```

### Full Test (on HPC system):
```bash
# On HPC system with SLURM
python app_gui.py

# Click HPC/SLURM tab
# Check Environment - all should be green
# Load valid config_hpc_datalad.json
# Enter subject ID
# Generate and preview script
# Save script
# Optional: Try dry-run first
# Submit job and monitor status
```

## Troubleshooting

### "SLURM not found"
- You're not on an HPC system, or
- SLURM tools not in PATH
- Check: `which sbatch`

### "Config loading failed"
- Path doesn't exist
- Config file isn't valid JSON
- Missing 'hpc', 'datalad', or 'container' sections

### "Job submission failed"
- Script path is incorrect
- Script file permissions
- SLURM queue limitations
- Check SLURM error log: `tail logs/slurm-*.err`

### "Jobs not showing in table"
- Click "Check Status" to refresh
- Jobs may have already completed
- Check SLURM directly: `squeue -u $USER`

## Next Steps

1. **Try it out**: Start the GUI and explore the HPC tab
2. **Test with config**: Load your `config_hpc_datalad.json`
3. **Test script generation**: Generate a script for a subject
4. **On HPC**: Submit actual jobs and monitor

## Documentation

For more details, see:
- `README_HPC_DATALAD.md` - Full HPC/DataLad integration guide
- `HPC_QUICK_REFERENCE.md` - Command reference
- `GUI_HPC_INTEGRATION.md` - GUI technical details
- `EXAMPLES_HPC_DATALAD.md` - Detailed examples

## Support

If you encounter issues:
1. Check the console log in the HPC tab
2. Check system status at top right of GUI
3. Review configuration file for errors
4. Test endpoints directly with curl

```bash
# Test HPC endpoint
curl http://localhost:8080/check_hpc_environment
```

---

**Implementation Status**: ✅ Complete and Ready to Use!
