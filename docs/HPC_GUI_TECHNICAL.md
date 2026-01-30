# HPC GUI Technical Notes (Current)

## Scope

The HPC tab is now an Advanced editor for SLURM settings stored in project.json.
It does not submit jobs or generate scripts on the backend.

## Frontend

Key functions:

- checkHPCEnvironment()
- updateHpcConfigDetails(config)
- loadHpcSettingsToForm(hpc)
- getHpcSettingsFromForm()
- saveHPCSettings()
- resetHPCSettings()
- toggleHPCPreview()
- generateSLURMScriptPreview(hpc, container)

## Backend

Required endpoints:

- GET /check_hpc_environment
- POST /save_project/<project_id>

## Data Storage

SLURM settings are stored in:

```json
"hpc": {
  "partition": "compute",
  "time": "24:00:00",
  "mem": "32G",
  "cpus": 8,
  "job_name": "bids-app",
  "output_pattern": "slurm-%j.out",
  "error_pattern": "slurm-%j.err",
  "modules": ["apptainer/1.2.0"],
  "environment": {"APPTAINER_CACHEDIR": "/tmp/.apptainer"},
  "monitor_jobs": true
}
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
