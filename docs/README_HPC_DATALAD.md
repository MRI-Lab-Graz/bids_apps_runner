# HPC/DataLad Integration Guide (Current)

This guide describes DataLad usage with the CLI. The GUI does not submit jobs for DataLad.

## Overview

Use DataLad when input/output datasets are stored in Git/Annex and you want per-subject streaming on HPC.
The SLURM settings live in project.json under hpc and can be edited in the GUI (Advanced), but execution remains CLI-driven.

## DataLad Configuration (project.json or config file)

```json
"datalad": {
  "input_repo": "https://github.com/your-lab/bids-dataset.git",
  "output_repo": "https://github.com/your-lab/derivatives.git",
  "clone_method": "clone",
  "get_data": true,
  "branch_per_subject": true,
  "output_branch": "results",
  "merge_strategy": "merge",
  "auto_push": false
}
```

## SLURM Configuration

```json
"hpc": {
  "partition": "compute",
  "time": "24:00:00",
  "mem": "32G",
  "cpus": 8,
  "modules": ["datalad/0.19.0", "apptainer/1.2.0"],
  "environment": {
    "APPTAINER_CACHEDIR": "/tmp/.apptainer"
  }
}
```

## CLI Execution

```bash
python scripts/prism_runner.py -c configs/config_hpc_datalad.json --hpc
```

The runner auto-detects SLURM and uses the hpc/datalad sections to configure execution.
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
