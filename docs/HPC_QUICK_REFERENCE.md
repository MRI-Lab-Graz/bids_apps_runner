# HPC/SLURM Quick Reference (Current)

## Summary

The HPC tab provides an Advanced editor for SLURM settings stored in project.json.
Job submission is handled from Run App (GUI) or from the CLI.

## GUI workflow

1. Load a project in Projects.
2. Open HPC tab.
3. Expand Advanced: SLURM Settings.
4. Edit and Save Settings.
5. Run from Run App.

## SLURM settings stored in project.json

```json
"hpc": {
  "partition": "compute",
  "time": "24:00:00",
  "mem": "32G",
  "cpus": 8,
  "job_name": "fmriprep",
  "output_pattern": "slurm-%j.out",
  "error_pattern": "slurm-%j.err",
  "modules": ["apptainer/1.2.0"],
  "environment": {
    "TEMPLATEFLOW_HOME": "/data/shared/templateflow",
    "APPTAINER_CACHEDIR": "/tmp/.apptainer"
  },
  "monitor_jobs": true
}
```

## CLI

```bash
python scripts/prism_runner.py -c configs/config.json --hpc
```

SLURM mode can be forced with --hpc and is otherwise auto-detected.
