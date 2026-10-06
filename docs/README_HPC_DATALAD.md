# HPC/DataLad Integration Guide

## Overview

`submit_bids_cohort.sh` runs any BIDS app (fMRIPrep, QSIPrep, MRIQC, ...) across one or more
datasets on a SLURM cluster, with git-annex/DataLad datasets holding the data. It's built
around **annex-slurm** (`annex-slurm/bin/`, design in
`docs/superpowers/specs/2026-09-09-annex-slurm-design.md`), which keeps every git operation
**outside** the parallel SLURM job:

- SLURM array jobs are plain compute scripts (apptainer only) -- they never touch git.
- `annex-slurm-schedule` runs once, before submission: it unlocks only the *existing* output
  of this batch's subjects (explicit paths, never the whole tree) and submits the array job.
- A dependent finish job runs `scripts/annex_cohort_finish.sh` once the array completes: per
  subject it finds the not-yet-annexed files with `find`, commits them through
  `annex-slurm-finish` (which pushes and checksum-verifies every object on the server), then
  re-scans and fails unless nothing is left unannexed.

This avoids the classic problem of many parallel jobs each cloning/branching/pushing a shared
git-annex dataset (lock contention, races, "concurrent git access" warnings) -- there's simply
no git happening while jobs run. It also never runs `git status`/`git diff`/`datalad save`:
those compare the index against the working tree and hang on large repos with git-annex
keys-DB drift, which is why this replaced the earlier `datalad-slurm` extension.

### Prerequisites

- `datalad` (setup/prefetch only) and a working `git-annex` -- the repo's
  `.datalad-slurm-venv` provides one that runs on login *and* compute nodes (see the header of
  `scripts/submit_bids_cohort.sh`; the `datalad-slurm` extension itself is no longer needed)
- `jq`, `python3`, `sbatch`/`squeue` (SLURM), SSH + `rsync` access to your DataLad server
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
    "input_url_template":  "server:/data/{dataset_id}",
    "output_url_template": "ssh://server/derivatives/{dataset_id}"
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

`output_url_template` must use `ssh://` (or `ria+ssh://` only if `server` is a real
RIA store) -- `setup` creates the output dataset with a plain `datalad create`
over SSH, which `ria+ssh://` cannot clone.

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
1. `annex-slurm-schedule -o sub-001 -o sub-002 ... -- --parsable <script>` from inside the
   output clone -- unlocks the batch subjects' existing output (explicit paths) and submits.
2. Submits a dependent finish job (`--dependency=afterany:<job_id>`) that runs
   `scripts/annex_cohort_finish.sh` once the array completes: commit + push + verify per
   subject, loose dataset-root files and the array's own SLURM logs included.

Batches (`batch_size`) run strictly one after another: each batch's finish job schedules the
next. Overlapping batches are out of scope for annex-slurm.

Both job IDs (array + finish) are recorded in `logs/submission_<timestamp>.log`.

**`status`**: shows `squeue` state for both the array job and its finish job per dataset.

### A single ad-hoc subject

`hpc_datalad_runner.py -s sub-001 -o job.sh` generates the same kind of plain script as a
one-task array (`--array=0-0`); there's no separate single-subject code path. To actually run
it through the annex-slurm pipeline rather than a bare `sbatch`, schedule and finish it the
same way `submit_bids_cohort.sh` does (on a compute node -- `salloc`/`srun` -- not the login
node):

```bash
cd /shared/derivatives/dataset_001/fmriprep
printf 'sub-001\n' > /shared/lists/sub-001.txt
annex-slurm/bin/annex-slurm-schedule -o sub-001 -- --parsable job.sh
# later, once it's done:
scripts/annex_cohort_finish.sh -d "$PWD" -s /shared/lists/sub-001.txt -m "fmriprep sub-001"
```

`hpc_datalad_runner.py --submit` (plain `sbatch`, no unlock/finish) exists only for testing
the compute script itself -- outputs from a job submitted that way are never committed to the
dataset.

## Troubleshooting

### A finish job failed, or output is still unannexed afterwards

`annex_cohort_finish.sh` is idempotent: committed files are symlinks and are skipped, so just
re-run it (same `-d`/`-s`) on a compute node. It fails loudly -- naming the files -- if a re-scan
still finds regular (unannexed) files after the finish tool returned, and it keeps going past
one failing subject, reporting all of them at the end. Job state is `squeue`/`sacct`; there is
no bookkeeping database to close.

### Submit says a subject "has no output"

The finish job skips a name with no entries at the dataset root (the array task failed before
writing anything) with a warning, not an error. Check the array's logs in
`.slurm_logs/<dataset>/`.

### Permission denied on push

```bash
ssh-keyscan <your-datalad-server> >> ~/.ssh/known_hosts
ssh -T <your-datalad-server>
```

### Job runs out of memory

Raise `hpc.mem` / `hpc.cpus` in the config and regenerate.

## References

- [DataLad Documentation](https://handbook.datalad.org/)
- annex-slurm design: `docs/superpowers/specs/2026-09-09-annex-slurm-design.md`
- [SLURM Documentation](https://slurm.schedmd.com/)
