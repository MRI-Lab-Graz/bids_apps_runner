# HPC/DataLad Examples

See [README_HPC_DATALAD.md](README_HPC_DATALAD.md) for the full workflow and config schema.

## Example 1: Full cohort run

```bash
# Edit configs/cohort_hpc_example.json (datasets, paths, hpc, bids_app), then:
./scripts/submit_bids_cohort.sh setup   -c configs/cohort_hpc_example.json
./scripts/submit_bids_cohort.sh submit  -c configs/cohort_hpc_example.json
./scripts/submit_bids_cohort.sh status
```

## Example 2: Dry run before committing

```bash
./scripts/submit_bids_cohort.sh submit -c configs/cohort_hpc_example.json --dry-run
```

Prints the subject-list build, the generated-script command, the `datalad slurm-schedule`
invocation, and the dependent finish-job submission -- without touching the cluster or the
dataset.

## Example 3: One dataset only, resuming a partial run

```bash
./scripts/submit_bids_cohort.sh submit -c configs/cohort_hpc_example.json \
  -d dataset_001 --resume
```

`--resume` skips subject-list building and script generation if they already exist for that
dataset (useful after fixing a config typo without rebuilding everything).

## Example 4: Watch the queue

```bash
watch -n 5 "squeue -u \$USER"
./scripts/submit_bids_cohort.sh status   # array + finish job state per dataset
```

## Example 5: A single ad-hoc subject, outside the cohort config

```bash
python3 scripts/hpc_datalad_runner.py \
  -c configs/cohort_hpc_example.json \
  -s sub-001 \
  -o scripts/generated/sub-001.sh

cd /shared/derivatives/dataset_001/fmriprep
datalad slurm-schedule -o sub-001 -m "fmriprep sub-001 (ad hoc)" \
  sbatch /path/to/scripts/generated/sub-001.sh

# once SLURM shows it COMPLETED:
datalad slurm-finish && datalad push --to origin
```

## Example 6: Inspect the generated compute script

```bash
python3 scripts/hpc_datalad_runner.py \
  -c configs/cohort_hpc_example.json \
  --array-mode --dataset-id dataset_001 \
  --subject-list /shared/subject_lists/dataset_001_subjects.txt
# (no -o: prints the script to stdout instead of saving it)
```

The script contains no `datalad`/`git` calls -- only module loads, scratch setup, an
`apptainer exec`, and cleanup. All provenance recording happens in `submit_bids_cohort.sh`
via `datalad slurm-schedule`/`datalad slurm-finish`, not in the job.

## Example 7: Recovering from a failed subject

```bash
# Check sacct/squeue for the failing array task, fix the underlying issue, then either:

# (a) close it out without committing its (partial/broken) output:
cd /shared/derivatives/dataset_001/fmriprep
datalad slurm-finish --close-failed-jobs

# (b) or commit whatever it did manage to write:
datalad slurm-finish --commit-failed-jobs

# Then re-run just that subject (Example 5) and schedule/finish it on its own.
```

## Example 8: Minimal vs. production config

### Minimal (quick test)

```json
{
  "datasets": ["smoketest"],
  "paths": {
    "shared_input_base": "/tmp/bids_input",
    "shared_output_base": "/tmp/bids_output",
    "scratch_dir": "/tmp/bids_scratch",
    "container": "/opt/mriqc.sif",
    "log_dir": "/tmp/bids_logs",
    "subject_lists_dir": "/tmp/bids_lists"
  },
  "datalad": {
    "input_url_template": "ria+ssh://server/data/{dataset_id}",
    "output_url_template": "ria+ssh://server/derivatives/{dataset_id}"
  },
  "hpc": {"partition": "quick", "time": "01:00:00", "mem": "16G", "cpus": 4},
  "bids_app": {"app_name": "mriqc", "analysis_level": "participant"}
}
```

### Production

```json
{
  "datasets": ["dataset_001", "dataset_002"],
  "paths": {
    "shared_input_base": "/scratch/pipeline/input",
    "shared_output_base": "/scratch/pipeline/derivatives",
    "scratch_dir": "/scratch/pipeline/work",
    "container": "/opt/containers/fmriprep_24.0.0.sif",
    "templateflow_dir": "/scratch/pipeline/templateflow",
    "fs_license": "/scratch/pipeline/license.txt",
    "log_dir": "/scratch/pipeline/logs",
    "subject_lists_dir": "/scratch/pipeline/subject_lists"
  },
  "datalad": {
    "input_url_template": "ria+ssh://datalad-server/data/{dataset_id}",
    "output_url_template": "ria+ssh://datalad-server/derivatives/{dataset_id}"
  },
  "hpc": {
    "partition": "gpu_v100",
    "time": "36:00:00",
    "mem": "128G",
    "cpus": 32,
    "max_concurrent": 50,
    "modules": ["cuda/11.8", "datalad/0.19.0", "apptainer/1.2.0"],
    "environment": {
      "CUDA_VISIBLE_DEVICES": "0,1",
      "OMP_NUM_THREADS": "8"
    }
  },
  "bids_app": {
    "app_name": "fmriprep",
    "analysis_level": "participant",
    "output_dir_name": "fmriprep",
    "options": [
      "--skip-bids-validation",
      "--n_cpus", "32",
      "--mem-mb", "120000",
      "--output-spaces", "MNI152NLin2009cAsym",
      "--use-aroma",
      "--cifti-output"
    ]
  }
}
```
