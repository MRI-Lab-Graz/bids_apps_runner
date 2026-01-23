# HPC/DataLad Integration Guide

This guide explains how to use the BIDS Apps Runner with HPC systems using DataLad for data streaming.

## Overview

The HPC/DataLad integration enables:
- **Data Streaming**: Pull only required subject data via DataLad
- **SLURM Scheduling**: Submit jobs to HPC cluster via SLURM
- **Results Tracking**: Automatic commit and push of results using Git/DataLad
- **Job Isolation**: Per-job git branches to prevent conflicts
- **Resource Management**: Full SLURM resource control (partition, time, memory, CPUs)

## Architecture

The workflow follows the DataLad pattern from the DataLad homepage:

```bash
# 1. Clone central DataLad dataset
datalad clone <input_repo> ds

# 2. Get directory structure (no data yet)
datalad get -n -r -R1 .

# 3. Create job-specific branches
git checkout -b "job-$JOBID"

# 4. Run container with DataLad tracking
datalad containers-run \
   -m "Processing message" \
   --explicit \
   -o output_dir1 -o output_dir2 \
   -i input_dir \
   -n code/pipelines/app_name \
   <container args>

# 5. Push results back with lock file
flock --verbose $DSLOCKFILE datalad push -d <repo> --to origin
```

## Configuration

### DataLad Section

```json
{
  "datalad": {
    "input_repo": "https://github.com/your-lab/bids-dataset.git",
    "output_repos": [
      "https://github.com/your-lab/fmriprep-outputs.git",
      "https://github.com/your-lab/freesurfer-outputs.git"
    ],
    "clone_method": "clone",
    "lock_file": "/tmp/datalad.lock"
  }
}
```

**Fields:**
- `input_repo`: URL to the input BIDS DataLad repository
- `output_repos`: List of output repository URLs (one per processing branch)
- `clone_method`: Either "clone" or "install"
- `lock_file`: Path to lock file for preventing simultaneous access

### HPC Section

```json
{
  "hpc": {
    "partition": "compute",
    "time": "24:00:00",
    "mem": "32G",
    "cpus": 8,
    "job_name": "bids_app",
    "output_log": "logs/slurm-%j.out",
    "error_log": "logs/slurm-%j.err",
    "modules": [
      "datalad",
      "git",
      "git-annex",
      "apptainer/1.2.0"
    ],
    "environment": {
      "APPTAINER_CACHEDIR": "/tmp/.apptainer",
      "DATALAD_RESULT_RENDERER": "disabled"
    }
  }
}
```

**Fields:**
- `partition`: SLURM partition name
- `time`: Walltime in HH:MM:SS format
- `mem`: Memory allocation (e.g., 32G, 500M)
- `cpus`: Number of CPUs per task
- `job_name`: Base name for SLURM jobs
- `output_log`: Pattern for stdout logs
- `error_log`: Pattern for stderr logs
- `modules`: List of modules to load via `module load`
- `environment`: Environment variables to set

### Container Section

```json
{
  "container": {
    "name": "fmriprep",
    "image": "/containers/fmriprep_24.0.0.sif",
    "outputs": [
      "fmriprep",
      "freesurfer"
    ],
    "inputs": [
      "sourcedata"
    ],
    "bids_args": {
      "bids_folder": "sourcedata",
      "output_folder": ".",
      "analysis_level": "participant",
      "skip-bids-validation": true,
      "output-spaces": "MNI152NLin6Asym",
      "n_cpus": 8,
      "mem-mb": 30000,
      "use-aroma": true,
      "cifti-output": true
    }
  }
}
```

**Fields:**
- `name`: Container name (used in `datalad containers-run`)
- `image`: Path to container image file
- `outputs`: Directories to track as outputs (passed to `-o`)
- `inputs`: Directories to track as inputs (passed to `-i`)
- `bids_args`: Container arguments (passed to the app inside container)

## Usage via Web GUI

### 1. Check HPC Environment

```
GET /check_hpc_environment
```

Returns availability of SLURM, DataLad, Git, Apptainer, etc.

### 2. Generate SLURM Script

```
POST /generate_hpc_script
{
  "config_path": "/path/to/config.json",
  "subject": "sub-001"
}
```

Returns the full generated SLURM script.

### 3. Save Script

```
POST /save_hpc_script
{
  "script": "<full script content>",
  "subject": "sub-001",
  "output_dir": "/tmp/hpc_scripts"
}
```

Saves script to disk with execute permissions.

### 4. Submit Job

```
POST /submit_hpc_job
{
  "script_path": "/tmp/hpc_scripts/job_sub-001.sh",
  "dry_run": false
}
```

Submits job to SLURM via `sbatch`.

### 5. Check Job Status

```
POST /get_hpc_job_status
{
  "job_ids": ["12345", "12346"]
}
```

Returns job status, elapsed time, end time, etc.

### 6. Cancel Job

```
POST /cancel_hpc_job
{
  "job_id": "12345"
}
```

Cancels a SLURM job via `scancel`.

## Command-Line Usage

### Generate Script

```bash
python hpc_datalad_runner.py \
  -c config_hpc_datalad.json \
  -s sub-001 \
  -o scripts/job_sub-001.sh
```

### Generate and Submit

```bash
python hpc_datalad_runner.py \
  -c config_hpc_datalad.json \
  -s sub-001 \
  -o scripts/job_sub-001.sh \
  --submit
```

### Dry Run

```bash
python hpc_datalad_runner.py \
  -c config_hpc_datalad.json \
  -s sub-001 \
  --dry-run
```

## Generated Script Structure

The generated SLURM scripts follow this structure:

1. **SLURM Header** - Resource directives
2. **Environment Setup** - Module loading, variables, work directory
3. **DataLad Clone** - Clone central repository with lock file
4. **Get Structure** - Retrieve directory structure without data
5. **Git Setup** - Create job-specific branches
6. **Container Run** - Execute via `datalad containers-run`
7. **Push Results** - Commit and push results back with lock file
8. **Cleanup** - Remove temporary files
9. **Completion** - Log final status

## Example Workflow

### Step 1: Prepare Configuration

```json
{
  "common": {
    "work_dir": "/scratch/user/bids_work"
  },
  "datalad": {
    "input_repo": "git@github.com:mylab/bids-dataset.git",
    "output_repos": ["git@github.com:mylab/fmriprep-outputs.git"]
  },
  "hpc": {
    "partition": "gpu",
    "time": "12:00:00",
    "mem": "64G",
    "cpus": 16,
    "modules": ["cuda/12.0", "datalad/0.19.0"]
  },
  "container": {
    "name": "fmriprep",
    "image": "/opt/containers/fmriprep_24.0.0.sif",
    "outputs": ["fmriprep"],
    "bids_args": {
      "bids_folder": "sourcedata",
      "output_folder": ".",
      "n_cpus": 16,
      "mem-mb": 60000
    }
  }
}
```

### Step 2: Generate Jobs for All Subjects

```bash
# Discover subjects from the BIDS dataset
SUBJECTS=$(datalad ls -r /path/to/cloned/dataset | grep "sub-" | cut -d/ -f1 | sort -u)

# Generate scripts for each
for subject in $SUBJECTS; do
    python hpc_datalad_runner.py -c config.json -s "$subject" -o "scripts/job_${subject}.sh"
done

# Submit all jobs
for script in scripts/job_*.sh; do
    sbatch "$script"
done
```

### Step 3: Monitor Jobs

```bash
# Watch all jobs
watch 'squeue -u $USER'

# Check specific job
squeue -j 12345

# View job logs
tail -f logs/slurm-12345.out
```

### Step 4: Pull Results

After jobs complete:

```bash
cd /path/to/results

# Pull all results from output repositories
datalad pull

# Or specifically
datalad pull -d fmriprep
```

## DataLad Requirements

The input and output repositories must be DataLad datasets with:
- `.datalad/config` file
- Git/Git-annex initialized
- Special remote configured for `origin`

### Prepare Input Dataset

```bash
cd /path/to/bids
datalad create --force
git remote add origin https://github.com/mylab/bids-dataset.git
datalad push
```

### Prepare Output Dataset

```bash
cd /path/to/derivatives
datalad create --force
git remote add origin https://github.com/mylab/derivatives-outputs.git
datalad save
datalad push
```

## Troubleshooting

### "datalad clone" hangs

Check lock file:
```bash
ls -la /tmp/datalad.lock
# If stale, remove it
rm /tmp/datalad.lock
```

### Job fails to get data

Check DataLad configuration:
```bash
cd /path/to/dataset
datalad status
datalad sibling
```

### Permission denied on push

Ensure SSH keys are configured:
```bash
ssh-keyscan github.com >> ~/.ssh/known_hosts
ssh -T git@github.com
```

### Out of memory errors

Increase memory in HPC section:
```json
{
  "hpc": {
    "mem": "64G"
  }
}
```

## Performance Tips

1. **Pre-clone input dataset**: Clone the central dataset once to avoid repeated clones
2. **Use fast storage**: Put work_dir on fast storage (not NFS if possible)
3. **Adjust batch size**: Submit jobs in batches to avoid queue overload
4. **Monitor lock file**: Ensure lock file is on fast, reliable storage

## References

- [DataLad Documentation](https://handbook.datalad.org/)
- [DataLad Containers](https://docs.datalad.org/en/stable/generated/datalad.interfaces.containers.html)
- [SLURM Documentation](https://slurm.schedmd.com/)
- [Git-Annex Manual](https://git-annex.branchable.com/manual/)
