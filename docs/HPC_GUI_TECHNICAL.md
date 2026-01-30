# HPC/SLURM GUI - Technical Implementation Details

## Files Modified

### 1. `templates/index.html`
**Changes**: Added HPC/SLURM tab and comprehensive JavaScript functions

**HTML Elements Added**:
- New navigation tab button with server icon
- Complete HPC panel with 4 main sections:
  1. Configuration panel
  2. Script generation controls
  3. Job submission interface
  4. Job monitoring table
- Console log output area
- Environment status display

**JavaScript Functions Added** (11 functions):

```javascript
checkHPCEnvironment()        // Check available HPC tools
loadHPCConfig()              // Load and display config settings
generateHPCScript()          // Generate SLURM script
toggleScriptPreview()        // Show/hide script preview
saveHPCScript()              // Save script to disk
submitHPCJob()               // Submit job to SLURM
checkJobStatus()             // Refresh job status from squeue
cancelHPCJob()               // Cancel current job
cancelSpecificJob(jobId)     // Cancel specific job by ID
logHPC(message, isError)     // Log to HPC console
clearHPCLog()                // Clear console output
```

**State Variables**:
```javascript
hpcCurrentScript    // Stores generated SLURM script
hpcCurrentJobId     // Current job ID
hpcTrackedJobs      // Array of all tracked job IDs
```

## Backend Integration

All HPC operations call existing Flask endpoints in `app_gui.py`:

### Endpoint: `/check_hpc_environment`
**Method**: GET  
**Response**:
```json
{
  "slurm": true,
  "datalad": true,
  "git": true,
  "git_annex": true,
  "apptainer": true,
  "singularity": true,
  "hpc_datalad_available": true
}
```

**Frontend Integration**:
```javascript
// User clicks "Check Environment" button
checkHPCEnvironment() 
  → fetch /check_hpc_environment
  → Display badges for each tool
```

### Endpoint: `/generate_hpc_script`
**Method**: POST  
**Request**:
```json
{
  "config_path": "/path/to/config.json",
  "subject": "sub-001"
}
```

**Response**:
```json
{
  "script": "#!/bin/bash\n#SBATCH ...",
  "subject": "sub-001",
  "config": "/path/to/config.json"
}
```

**Frontend Flow**:
```javascript
generateHPCScript()
  → Validate inputs
  → fetch /generate_hpc_script
  → Store script in hpcCurrentScript
  → Display in preview
```

### Endpoint: `/save_hpc_script`
**Method**: POST  
**Request**:
```json
{
  "script": "#!/bin/bash\n...",
  "subject": "sub-001",
  "output_dir": "/tmp/hpc_scripts"
}
```

**Response**:
```json
{
  "message": "Script saved to /tmp/hpc_scripts/job_sub-001.sh",
  "path": "/tmp/hpc_scripts/job_sub-001.sh"
}
```

### Endpoint: `/submit_hpc_job`
**Method**: POST  
**Request**:
```json
{
  "script_path": "/tmp/hpc_scripts/job_sub-001.sh",
  "dry_run": false
}
```

**Response**:
```json
{
  "message": "Job submitted successfully",
  "job_id": "12345",
  "command": "sbatch /tmp/hpc_scripts/job_sub-001.sh"
}
```

### Endpoint: `/get_hpc_job_status`
**Method**: POST  
**Request**:
```json
{
  "job_ids": ["12345", "12346"]
}
```

**Response**:
```json
{
  "jobs": [
    {
      "job_id": "12345",
      "subject": "sub-001",
      "status": "RUNNING",
      "time": "00:05:30",
      "nodelist": "node01"
    }
  ]
}
```

### Endpoint: `/cancel_hpc_job`
**Method**: POST  
**Request**:
```json
{
  "job_id": "12345"
}
```

**Response**:
```json
{
  "message": "Job 12345 cancelled",
  "job_id": "12345"
}
```

## User Flow Diagrams

### Configuration & Script Generation
```
User
  ↓
Enter Config Path
  ↓
Click Load
  ↓ (fetch /load_config if available)
Display HPC Settings
  ↓
Enter Subject ID
  ↓
Click Generate Script
  ↓ (POST /generate_hpc_script)
Backend: Load config, validate, generate SLURM script
  ↓ (return script)
Display in Preview
  ↓
Click Save Script
  ↓ (POST /save_hpc_script)
Backend: Write to disk, set permissions
  ↓ (return path)
Update Script Path field
```

### Job Submission
```
User
  ↓
Click Submit to SLURM
  ↓ (POST /submit_hpc_job)
Backend: Check script exists, run sbatch
  ↓ (return job_id)
Store job ID
  ↓
Add to tracked jobs
  ↓
Display status badge
  ↓
User can now monitor/cancel
```

### Job Monitoring
```
User
  ↓
Click Check Status
  ↓ (POST /get_hpc_job_status)
Backend: Run squeue for tracked job IDs
  ↓ (return status for each)
Populate job table:
  - Job ID
  - Subject
  - Status (RUNNING, COMPLETED, etc)
  - Time elapsed
  - Assigned node(s)
  - Cancel button
```

## Error Handling

### Client-Side Validation
```javascript
// Check inputs before API call
if (!configPath || !subject) {
    logHPC('Please specify config path and subject ID', true);
    return;
}
```

### Network Error Handling
```javascript
try {
    const resp = await fetch('/generate_hpc_script', {...});
    const data = await resp.json();
    if (resp.ok && data.script) {
        // Success
    } else {
        logHPC('Error: ' + (data.error || 'Failed'), true);
    }
} catch (e) {
    logHPC('Error: ' + e.message, true);
}
```

### Server-Side Validation
Backend checks:
- Config file exists and is valid JSON
- Required sections present (hpc, datalad, container)
- Subject ID is valid
- Script path is accessible
- SLURM tools available (sbatch, squeue, scancel)

## Performance Considerations

### Polling Strategy
```javascript
// Job status polling
function pollJobStatus() {
    checkJobStatus();
    setTimeout(pollJobStatus, 30000); // Refresh every 30 seconds
}
```

### Memory Management
- Scripts stored in memory (not file-based unless saved)
- Job tracking limited to current session
- Console log auto-scrolls to bottom

### API Call Optimization
- Batch status checks: one request for multiple jobs
- Debounced status updates
- No redundant config loads

## Security Considerations

1. **Path Validation**: 
   - Backend validates all file paths
   - Prevents directory traversal attacks

2. **Script Execution**:
   - Only submits existing, readable scripts
   - No dynamic command construction

3. **Session Scope**:
   - Job tracking per browser session
   - No persistent state in GUI
   - Full SLURM job info from backend

4. **HTTPS Ready**:
   - No hardcoded credentials
   - All requests use same protocol as page

## Browser Compatibility

- ✅ Chrome/Chromium 90+
- ✅ Firefox 88+
- ✅ Safari 14+
- ✅ Edge 90+
- ✅ Mobile browsers (responsive design)

Requires:
- Fetch API support
- ES6 async/await
- JSON support
- CSS Grid/Flexbox

## Testing Recommendations

### Unit Tests
```javascript
// Test environment check
await checkHPCEnvironment();
// Verify badges displayed

// Test script generation
await generateHPCScript();
// Verify preview populated

// Test error handling
// Missing config path
// Invalid subject format
// Network failures
```

### Integration Tests
```javascript
// Full workflow
1. Load config
2. Generate script
3. Save script
4. Submit job (dry-run)
5. Check status
6. Cancel job
```

### Manual Testing
```bash
# On HPC system
python app_gui.py

# In browser console
fetch('/check_hpc_environment').then(r => r.json()).then(console.log)
# Should show SLURM available

# Full workflow in GUI
```

## Debugging

### Console Logging
All operations logged to HPC Console in GUI:
```javascript
logHPC('Message here');           // Info log
logHPC('Error message', true);    // Error log
```

### Browser DevTools
```javascript
// Check network requests
// Open DevTools → Network tab
// Look for POST /generate_hpc_script, etc.

// Check console for errors
// Open DevTools → Console tab
// May see fetch errors, JS errors

// Check stored state
// In DevTools Console:
// hpcCurrentScript  // Current script
// hpcTrackedJobs    // Tracked job IDs
```

### Backend Logging
```bash
# Check app_gui.py logs
# Provides server-side operation details
# May show SLURM command output
```

## Future Enhancements

### Planned Features
- [ ] Template library for configs
- [ ] Batch submission (multiple subjects)
- [ ] Notifications (email/Slack)
- [ ] Cost estimation
- [ ] Job history/archival
- [ ] Output directory linking
- [ ] SLURM parameter presets
- [ ] Advanced job filtering

### Potential Optimizations
- WebSocket for real-time updates
- Server-side session storage
- Database for job history
- Job queue management UI
- Resource usage graphs

## Code Structure

```
templates/index.html
├── HTML Structure (lines 1-391)
├── HPC Tab Panel (lines 391-490)
├── JavaScript Functions (lines 1600+)
│   ├── HPC Functions
│   └── Event Handlers
└── Styles (inline CSS)

app_gui.py
├── Flask Routes
├── HPC Endpoints (lines 1324+)
│   ├── check_hpc_environment
│   ├── generate_hpc_script
│   ├── save_hpc_script
│   ├── submit_hpc_job
│   ├── get_hpc_job_status
│   └── cancel_hpc_job
└── Backend Logic
```

## Related Files

- `hpc_datalad_runner.py` - Script generation backend
- `run_bids_apps_hpc.py` - HPC batch runner
- `config_hpc_datalad.json` - Example config
- `README_HPC_DATALAD.md` - Full documentation
- `HPC_QUICK_REFERENCE.md` - Command reference

## Version History

- **v1.0** (Jan 2026): Initial implementation
  - Environment check
  - Config loading
  - Script generation & saving
  - Job submission & monitoring
  - Console logging
