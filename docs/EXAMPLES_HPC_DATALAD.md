# HPC/DataLad Integration Examples

This document shows practical examples of using the BIDS Apps Runner with HPC and DataLad.

## Example 1: Submit Single Subject via Web GUI

### Step 1: Check HPC Environment

```javascript
// JavaScript frontend code
fetch('/check_hpc_environment')
  .then(r => r.json())
  .then(data => {
    console.log('SLURM available:', data.slurm);
    console.log('DataLad available:', data.datalad);
    console.log('Apptainer available:', data.apptainer);
  });
```

### Step 2: Generate Script

```javascript
fetch('/generate_hpc_script', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    config_path: '/path/to/config_hpc_datalad.json',
    subject: 'sub-001'
  })
})
.then(r => r.json())
.then(data => {
  console.log('Generated script:');
  console.log(data.script);
  // Now save and submit...
});
```

### Step 3: Save Script

```javascript
fetch('/save_hpc_script', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    script: scriptContent,
    subject: 'sub-001',
    output_dir: '/tmp/hpc_scripts'
  })
})
.then(r => r.json())
.then(data => console.log(data.path));
```

### Step 4: Submit Job

```javascript
fetch('/submit_hpc_job', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    script_path: '/tmp/hpc_scripts/job_sub-001.sh',
    dry_run: false
  })
})
.then(r => r.json())
.then(data => {
  console.log('Job ID:', data.job_id);
  // Monitor job...
});
```

### Step 5: Monitor Job Status

```javascript
// Poll job status
setInterval(() => {
  fetch('/get_hpc_job_status', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      job_ids: ['12345']
    })
  })
  .then(r => r.json())
  .then(data => {
    data.jobs.forEach(job => {
      console.log(`Job ${job.job_id}: ${job.status} (${job.time})`);
    });
  });
}, 10000); // Check every 10 seconds
```

---

## Example 2: Batch Submit Multiple Subjects

### Command Line

```bash
# Generate and submit for all subjects
python hpc_batch_submit.py \
  -c config_hpc_datalad.json \
  --script-dir ./scripts \
  --logs-dir ./logs

# Output:
# ============================================================
# BATCH SUBMISSION SUMMARY
# ============================================================
# Total subjects: 50
# Submitted: 50
# Failed: 0
#
# Submitted jobs:
#   001: 12345
#   002: 12346
#   003: 12347
#   ...
# ============================================================
```

### Web GUI Batch Endpoint (Proposed Addition)

```javascript
fetch('/batch_submit_hpc_jobs', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({
    config_path: '/path/to/config.json',
    subjects: ['sub-001', 'sub-002', 'sub-003'],
    script_dir: '/tmp/hpc_scripts',
    logs_dir: './logs',
    max_jobs: 50,
    dry_run: false
  })
})
.then(r => r.json())
.then(results => {
  console.log(`Submitted ${results.submitted} jobs`);
  console.log(`Failed: ${results.failed}`);
  results.jobs.forEach(job => {
    console.log(`${job.subject}: ${job.job_id}`);
  });
});
```

---

## Example 3: Auto-discover and Submit

### Discover Subjects from Local BIDS Dataset

```bash
# Generate SLURM config with local BIDS path
cat > config_hpc_local.json << 'EOF'
{
  "common": {
    "work_dir": "/scratch/user/work",
    "bids_folder": "/data/bids"
  },
  "datalad": {
    "input_repo": "https://github.com/mylab/bids.git",
    "output_repos": ["https://github.com/mylab/derivatives.git"]
  },
  "hpc": {
    "partition": "standard",
    "time": "24:00:00",
    "mem": "32G",
    "cpus": 8,
    "modules": ["datalad/0.19.0"]
  },
  "container": {
    "name": "fmriprep",
    "image": "/opt/containers/fmriprep.sif",
    "outputs": ["fmriprep"],
    "bids_args": {"analysis_level": "participant"}
  }
}
EOF

# Auto-discover subjects and submit
python hpc_batch_submit.py -c config_hpc_local.json
```

---

## Example 4: Monitor Multiple Jobs

### Check All Running Jobs

```bash
# Get all job IDs
JOB_IDS=$(squeue -u $USER -h -o "%i" | tr '\n' ',')

# Check status via Python
python -c "
import requests
import json

job_ids = '${JOB_IDS}'.rstrip(',').split(',')

response = requests.post('http://localhost:8080/get_hpc_job_status',
  json={'job_ids': job_ids})

jobs = response.json()['jobs']
for job in jobs:
    print(f\"Job {job['job_id']}: {job['status']} ({job['time']})\")
"
```

### Watch Queue

```bash
# Continuously monitor
watch -n 5 "squeue -u \$USER"
```

---

## Example 5: Handle Job Failures and Resubmit

### Identify Failed Jobs

```bash
# Check SLURM error logs
for log in logs/slurm-*.err; do
    if [ -s "$log" ]; then
        echo "Job failed: $log"
        cat "$log" | head -20
    fi
done
```

### Extract Subject from Failed Job

```bash
# Get subject from failed script
SCRIPT=$(find scripts -name "job_*.sh" -newer $TIMESTAMP)
SUBJECT=$(basename $SCRIPT | sed 's/job_\(.*\)\.sh/\1/')

# Regenerate and resubmit
python hpc_datalad_runner.py -c config.json -s "$SUBJECT" -o "scripts/retry_${SUBJECT}.sh" --submit
```

---

## Example 6: Stream Data with DataLad

### Clone and Stream Data

```bash
# On HPC compute node (happens automatically in SLURM script)
cd /tmp/bids_work

# Clone with lock file
flock --verbose /tmp/datalad.lock datalad clone \
  https://github.com/mylab/bids.git ds

cd ds

# Get only structure (no actual data)
datalad get -n -r -R1 .

# When container runs, DataLad tracks which files are accessed
# After processing, push results back with lock file
flock --verbose /tmp/datalad.lock datalad push -d fmriprep --to origin
```

### Monitor Data Transfer

```bash
# Watch transfer progress
watch -n 1 "ls -lh ds/sub-001/ | head -20"

# Check git-annex status
cd ds
git annex status
```

---

## Example 7: Production Setup with Error Handling

### Complete Workflow Script

```bash
#!/bin/bash

set -e
set -u

CONFIG="/path/to/config_hpc_datalad.json"
SCRIPT_DIR="./scripts"
LOGS_DIR="./logs"
MAX_JOBS=100
BATCH_SIZE=20  # Submit in batches to avoid queue overload

echo "Starting batch submission..."

# Generate scripts for all subjects
python hpc_batch_submit.py \
  -c "$CONFIG" \
  --script-dir "$SCRIPT_DIR" \
  --logs-dir "$LOGS_DIR" \
  --generate-only

SCRIPTS=$(ls "$SCRIPT_DIR"/job_*.sh | sort)
TOTAL=$(echo "$SCRIPTS" | wc -l)

echo "Generated $TOTAL job scripts"
echo ""

# Submit in batches
SUBMITTED=0
for SCRIPT in $SCRIPTS; do
    if [ $SUBMITTED -ge $MAX_JOBS ]; then
        echo "Reached max jobs limit ($MAX_JOBS)"
        break
    fi
    
    JOB_ID=$(sbatch "$SCRIPT" | awk '{print $NF}')
    echo "Submitted: $SCRIPT -> $JOB_ID"
    
    SUBMITTED=$((SUBMITTED + 1))
    
    # Rate limiting
    if [ $((SUBMITTED % BATCH_SIZE)) -eq 0 ]; then
        echo "Submitted $SUBMITTED/$TOTAL jobs, waiting before next batch..."
        sleep 5
    fi
done

echo ""
echo "Total submitted: $SUBMITTED"
echo ""
echo "Monitor with: squeue -u \$USER"
echo "View logs with: tail -f logs/slurm-*.out"
```

---

## Example 8: Config for Different Use Cases

### Minimal Config (Quick Test)

```json
{
  "common": {"work_dir": "/tmp/work"},
  "datalad": {
    "input_repo": "git@github.com:lab/data.git",
    "output_repos": ["git@github.com:lab/results.git"]
  },
  "hpc": {
    "partition": "quick",
    "time": "01:00:00",
    "mem": "16G",
    "cpus": 4
  },
  "container": {
    "name": "mriqc",
    "image": "/opt/mriqc.sif",
    "outputs": ["mriqc"],
    "bids_args": {"analysis_level": "participant"}
  }
}
```

### Production Config (Full Pipeline)

```json
{
  "common": {
    "work_dir": "/scratch/pipeline/work",
    "log_dir": "/scratch/pipeline/logs"
  },
  "datalad": {
    "input_repo": "git@hpc:/data/datasets/bids.git",
    "output_repos": [
      "git@hpc:/data/results/fmriprep.git",
      "git@hpc:/data/results/freesurfer.git"
    ],
    "clone_method": "clone",
    "lock_file": "/scratch/.pipeline.lock"
  },
  "hpc": {
    "partition": "gpu_v100",
    "time": "36:00:00",
    "mem": "128G",
    "cpus": 32,
    "job_name": "fmriprep_prod",
    "output_log": "/scratch/pipeline/logs/slurm-%j.out",
    "error_log": "/scratch/pipeline/logs/slurm-%j.err",
    "modules": [
      "cuda/11.8",
      "datalad/0.19.0",
      "git-annex/10.20230127"
    ],
    "environment": {
      "CUDA_VISIBLE_DEVICES": "0,1",
      "OMP_NUM_THREADS": "8",
      "APPTAINER_CACHEDIR": "/scratch/containers/.cache"
    }
  },
  "container": {
    "name": "fmriprep",
    "image": "/opt/containers/fmriprep_24.0.0.sif",
    "outputs": ["fmriprep", "freesurfer"],
    "inputs": ["sourcedata"],
    "bids_args": {
      "bids_folder": "sourcedata",
      "output_folder": ".",
      "analysis_level": "participant",
      "n_cpus": 32,
      "mem-mb": 120000,
      "skip-bids-validation": true,
      "output-spaces": "MNI152NLin2009cAsym",
      "use-aroma": true,
      "cifti-output": true,
      "fmap-bspline": true
    }
  }
}
```

---

## Troubleshooting Guide

### Issue: "datalad clone" takes forever

**Solution:**
```bash
# Check network connectivity
ssh -T git@github.com
git ls-remote https://github.com/mylab/bids.git

# Try with verbose output
datalad clone -v https://github.com/mylab/bids.git test_ds
```

### Issue: "Permission denied" on push

**Solution:**
```bash
# Ensure SSH keys are configured
ssh-keyscan github.com >> ~/.ssh/known_hosts

# Test SSH access
ssh -T git@github.com

# Verify remote URL
cd /path/to/repo
git remote -v
```

### Issue: Job runs out of memory

**Solution:**
```json
{
  "hpc": {
    "mem": "256G",
    "cpus": 64
  },
  "container": {
    "bids_args": {
      "mem-mb": 250000,
      "n_cpus": 64
    }
  }
}
```

### Issue: Container not found

**Solution:**
```bash
# Verify container exists
ls -lh /opt/containers/fmriprep.sif

# Check if readable
file /opt/containers/fmriprep.sif

# Update config with absolute path
# In JSON: "image": "/absolute/path/to/container.sif"
```

---

## Performance Optimization

### Tips for Large-Scale Processing

1. **Use fast storage for work_dir**
   ```bash
   # Instead of NFS: /home/user/work
   # Use: /scratch/user/work (local SSD)
   ```

2. **Pre-clone input dataset once**
   ```bash
   datalad clone <input_repo> /shared/bids-cached
   # Then reference in script without re-cloning
   ```

3. **Submit jobs in waves**
   ```bash
   # Don't submit all 1000 at once
   # Submit 100-200 at a time with delays between batches
   ```

4. **Use fast lock file location**
   ```json
   {
     "datalad": {
       "lock_file": "/scratch/local/.datalad.lock"
     }
   }
   ```

5. **Optimize container arguments**
   ```json
   {
     "container": {
       "bids_args": {
         "n_cpus": 16,
         "mem-mb": 30000,
         "output-spaces": "MNI152NLin2009cAsym"
       }
     }
   }
   ```
