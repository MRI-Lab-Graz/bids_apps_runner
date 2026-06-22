# HPC/DataLad Integration Guide

## Overview

`submit_bids_cohort.sh` runs any BIDS app (fMRIPrep, QSIPrep, MRIQC, ...) across one or more
datasets on a SLURM cluster, with DataLad managing dataset provenance. It's built around the
[`datalad-slurm`](https://github.com/knuedd/datalad-slurm) extension
(see also the [DataLad handbook chapter on SLURM](https://handbook.datalad.org/en/latest/beyond_basics/101-174-slurm.html)),
which keeps every datalad/git operation **outside** the parallel SLURM job:

- SLURM array jobs are plain compute scripts (apptainer only) -- they never touch git.
- `datalad slurm-schedule` runs once, before submission, to declare each subject's output
  path and submit the array job via `sbatch` itself.
- `datalad slurm-finish` runs once, after the array completes, to commit every subject's
  output in a single commit; a `datalad push` follows.

This avoids the classic problem of many parallel jobs each cloning/branching/pushing a shared
git-annex dataset (lock contention, races, "concurrent git access" warnings) -- there's simply
no git happening while jobs run.

### Prerequisites

- `datalad` and the [`datalad-slurm`](https://github.com/knuedd/datalad-slurm) extension
  (not on PyPI: `pip install git+https://github.com/knuedd/datalad-slurm.git`)
- `jq`, `python3`, `sbatch`/`squeue` (SLURM), SSH access to your DataLad server for setup
- `apptainer` (or another container runtime your config's `paths.container` expects)

## Config (`configs/cohort_hpc_example.json` schema)

```json
{
  "datasets": ["dataset_001"],
  "paths": {
    "shared_input_base":  "/shared/input",
    "shared_output_base": "/shared/derivatives",
    "scratch_dir":         "/scratch/$USER/bids_app",
    "container":           "/containers/fmriprep_24.0.0.sif",
    "templateflow_dir":    "/shared/templateflow",
    "fs_license":          "/shared/license.txt",
    "log_dir":             "$HOME/logs/bids_app",
    "subject_lists_dir":   "/shared/subject_lists"
  },
  "datalad": {
    "input_url_template":  "ria+ssh://server/data/{dataset_id}",
    "output_url_template": "ria+ssh://server/derivatives/{dataset_id}"
  },
  "hpc": {
    "partition": "compute",
    "time": "24:00:00",
    "mem": "32G",
    "cpus": 8,
    "max_concurrent": 50,
    "modules": ["datalad/TODO", "apptainer/TODO"],
    "environment": {"DATALAD_RESULT_RENDERER": "disabled"}
  },
  "bids_app": {
    "app_name": "fmriprep",
    "analysis_level": "participant",
    "output_dir_name": "fmriprep",
    "options": ["--skip-bids-validation", "--n_cpus", "8"]
  }
}
```

There is exactly one config schema; `hpc_datalad_runner.py` and `submit_bids_cohort.sh` both
read it. `paths.scratch_dir` is the only per-task isolated directory you need to think about --
it's where each array task's compute scratch (`$SLURM_ARRAY_JOB_ID`/`$SLURM_ARRAY_TASK_ID`)
lives. Everything else (`shared_input_base`, `shared_output_base`) is one persistent dataset
clone, shared and bind-mounted by every task -- safe because tasks only write to their own
`sub-XXX/` subdirectory and never call git.

## Workflow

```bash
./scripts/submit_bids_cohort.sh setup   [-c CONFIG] [-d DATASET_ID]
./scripts/submit_bids_cohort.sh submit  [-c CONFIG] [-d DATASET_ID] [--dry-run] [--resume]
./scripts/submit_bids_cohort.sh status
```

**`setup`** (run once, needs network/SSH access): clones the input dataset and the output
dataset to shared HPC storage, creates the output dataset on the DataLad server if needed, and
prefetches (`datalad get`) all discovered subjects so array tasks never need to.

**`submit`**: builds a subject list, generates the plain compute script
(`hpc_datalad_runner.py --array-mode`), then:
1. `datalad slurm-schedule -o sub-001 -o sub-002 ... sbatch <script>` from inside the output
   clone -- one explicit, non-overlapping `-o` per subject (wildcards are rejected).
2. Submits a dependent finish job (`--dependency=afterany:<job_id>`) that runs
   `datalad slurm-finish && datalad push --to origin` once the array completes.

Both job IDs (array + finish) are recorded in `logs/submission_<timestamp>.log`.

**`status`**: shows `squeue` state for both the array job and its finish job per dataset.

### A single ad-hoc subject

`hpc_datalad_runner.py -s sub-001 -o job.sh` generates the same kind of plain script as a
one-task array (`--array=0-0`); there's no separate single-subject code path. To actually run
it through the datalad-slurm pipeline rather than a bare `sbatch`, schedule it the same way
`submit_bids_cohort.sh` does:

```bash
cd /shared/derivatives/dataset_001/fmriprep
datalad slurm-schedule -o sub-001 -m "fmriprep sub-001" sbatch job.sh
# later, once it's done:
datalad slurm-finish && datalad push --to origin
```

`hpc_datalad_runner.py --submit` (plain `sbatch`, no `datalad slurm-schedule`) exists only for
testing the compute script itself -- outputs from a job submitted that way are never recorded
in the dataset.

## Troubleshooting

### `datalad slurm-schedule` refuses with "conflicting outputs"

Another scheduled-but-not-yet-finished job already declared one of your `-o` paths. Run
`datalad slurm-finish --list-open-jobs` in the output clone to see what's still open.

### `datalad slurm-finish` says jobs are "not complete"

It checks `sacct` for every array task. If some tasks failed, re-run with
`--close-failed-jobs` (drops them without committing) or `--commit-failed-jobs` (commits
whatever they did write). Pending/running tasks must finish first -- it won't wait for you.

### Permission denied on push

```bash
ssh-keyscan <your-datalad-server> >> ~/.ssh/known_hosts
ssh -T <your-datalad-server>
```

### Job runs out of memory

Raise `hpc.mem` / `hpc.cpus` in the config and regenerate.

## References

- [DataLad Documentation](https://handbook.datalad.org/)
- [DataLad SLURM chapter](https://handbook.datalad.org/en/latest/beyond_basics/101-174-slurm.html)
- [`datalad-slurm` extension](https://github.com/knuedd/datalad-slurm)
- [SLURM Documentation](https://slurm.schedmd.com/)
