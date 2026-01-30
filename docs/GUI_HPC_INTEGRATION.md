# GUI Integration for HPC (Current)

The HPC tab provides:

- Environment check (SLURM/DataLad/Git/Apptainer availability)
- Advanced editor for hpc settings stored in project.json

The GUI does not submit jobs from this tab. Execution is initiated from Run App.

## Endpoints Used

- GET /check_hpc_environment
- POST /save_project/<project_id>

## Frontend Notes

- Advanced SLURM settings are hidden by default and intended for power users.
- hpc settings are persisted in project.json and reused by the CLI.

```javascript
// hpc_mode.js - New module for HPC functionality

class HPCMode {
  constructor() {
    this.jobs = [];
    this.config = null;
    this.statusInterval = null;
  }
  
  // Check if HPC tools are available
  async checkEnvironment() {
    const response = await fetch('/check_hpc_environment');
    return await response.json();
  }
  
  // Generate SLURM script for a subject
  async generateScript(configPath, subject) {
    const response = await fetch('/generate_hpc_script', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        config_path: configPath,
        subject: subject
      })
    });
    return await response.json();
  }
  
  // Save script to disk
  async saveScript(scriptContent, subject, outputDir) {
    const response = await fetch('/save_hpc_script', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        script: scriptContent,
        subject: subject,
        output_dir: outputDir
      })
    });
    return await response.json();
  }
  
  // Submit job to SLURM
  async submitJob(scriptPath, dryRun = false) {
    const response = await fetch('/submit_hpc_job', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        script_path: scriptPath,
        dry_run: dryRun
      })
    });
    return await response.json();
  }
  
  // Get job status
  async getJobStatus(jobIds) {
    const response = await fetch('/get_hpc_job_status', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        job_ids: jobIds
      })
    });
    return await response.json();
  }
  
  // Start monitoring jobs
  startMonitoring(jobIds, interval = 10000) {
    this.statusInterval = setInterval(async () => {
      const result = await this.getJobStatus(jobIds);
      this.updateJobDisplay(result.jobs);
    }, interval);
  }
  
  // Stop monitoring
  stopMonitoring() {
    if (this.statusInterval) {
      clearInterval(this.statusInterval);
      this.statusInterval = null;
    }
  }
  
  // Update job display in UI
  updateJobDisplay(jobs) {
    // Implementation depends on your frontend framework
    console.log('Job status update:', jobs);
  }
  
  // Cancel job
  async cancelJob(jobId) {
    const response = await fetch('/cancel_hpc_job', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({job_id: jobId})
    });
    return await response.json();
  }
}

// Export for use in GUI
export { HPCMode };
```

### 6. Example HTML Template Addition

```html
<!-- templates/hpc_panel.html -->
<div id="hpc-mode" class="mode-panel" style="display:none;">
  <h2>HPC/DataLad Mode</h2>
  
  <!-- Environment Check -->
  <div class="section">
    <h3>Environment Check</h3>
    <button onclick="checkHPCEnvironment()">Check HPC Tools</button>
    <div id="hpc-status"></div>
  </div>
  
  <!-- HPC Configuration -->
  <div class="section">
    <h3>HPC Configuration</h3>
    
    <h4>DataLad Settings</h4>
    <input type="text" id="input-repo" placeholder="Input Repository URL">
    <input type="text" id="output-repo" placeholder="Output Repository URL(s)">
    
    <h4>SLURM Settings</h4>
    <input type="text" id="partition" placeholder="Partition" value="compute">
    <input type="text" id="time" placeholder="Time (HH:MM:SS)" value="24:00:00">
    <input type="text" id="mem" placeholder="Memory (e.g., 32G)" value="32G">
    <input type="number" id="cpus" placeholder="CPUs" value="8">
    
    <h4>Container Settings</h4>
    <input type="text" id="container-image" placeholder="Container Image Path">
    <input type="text" id="output-dirs" placeholder="Output Directories (comma-separated)">
  </div>
  
  <!-- Subject Selection -->
  <div class="section">
    <h3>Subject Selection</h3>
    <select id="subject-select" multiple>
      <!-- Populated from BIDS discovery -->
    </select>
    <button onclick="discoverSubjects()">Discover Subjects</button>
  </div>
  
  <!-- Job Submission -->
  <div class="section">
    <h3>Job Submission</h3>
    <button onclick="generateAndSubmitJobs()">Generate & Submit</button>
    <label>
      <input type="checkbox" id="dry-run"> Dry Run
    </label>
    <label>
      <input type="checkbox" id="preview-scripts"> Preview Scripts
    </label>
  </div>
  
  <!-- Job Monitoring -->
  <div class="section">
    <h3>Job Monitoring</h3>
    <button onclick="startMonitoring()">Start Monitoring</button>
    <button onclick="stopMonitoring()">Stop Monitoring</button>
    <table id="job-table">
      <thead>
        <tr>
          <th>Job ID</th>
          <th>Subject</th>
          <th>Status</th>
          <th>Time</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody id="job-list">
        <!-- Populated dynamically -->
      </tbody>
    </table>
  </div>
</div>

<style>
#hpc-mode {
  padding: 20px;
  background-color: #f5f5f5;
  border-radius: 5px;
}

.section {
  margin: 20px 0;
  padding: 15px;
  background-color: white;
  border-left: 4px solid #007bff;
}

.section h3 {
  margin-top: 0;
}

#job-table {
  width: 100%;
  border-collapse: collapse;
}

#job-table th, #job-table td {
  padding: 10px;
  text-align: left;
  border-bottom: 1px solid #ddd;
}

#job-table th {
  background-color: #007bff;
  color: white;
}

#job-table tr:hover {
  background-color: #f5f5f5;
}
</style>
```

## Integration Steps

1. **Copy Files**
   - `hpc_datalad_runner.py` → Project root
   - `hpc_batch_submit.py` → Project root

2. **Update app_gui.py**
   - Already done (see commits above)
   - Imports HPC modules
   - Adds 6 new endpoints

3. **Update Frontend Templates**
   - Add HPC mode tab
   - Add configuration panel
   - Add job monitoring interface
   - Add JavaScript handlers

4. **Test the Integration**
   ```bash
   # Start GUI
   python app_gui.py
   
   # Open browser
   # http://localhost:8080
   
   # Check HPC environment
   # Should show SLURM, DataLad, etc. available
   
   # Generate test script
   # Use /generate_hpc_script endpoint
   
   # Verify script looks correct
   ```

## Expected Usage Flow

1. User selects "HPC Mode" in GUI
2. GUI checks HPC environment
3. User loads or creates HPC config (JSON)
4. User selects subjects to process
5. GUI generates SLURM scripts
6. User previews script (optional)
7. User submits jobs
8. GUI monitors job status
9. Jobs complete and push results via DataLad

## Benefits

- ✅ Full DataLad integration for data streaming
- ✅ SLURM job scheduling on HPC
- ✅ Per-job git branching for conflict prevention
- ✅ Automatic result tracking and push
- ✅ Web GUI for job management
- ✅ Real-time job monitoring
- ✅ Batch submission support
- ✅ Dry-run capability for testing
