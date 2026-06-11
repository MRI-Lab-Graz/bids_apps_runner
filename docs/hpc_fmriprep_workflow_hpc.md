# fMRIPrep OpenNeuro Cohort — HPC Workflow

Multi-study fMRIPrep pipeline for 39 OpenNeuro datasets (~2800 subjects) using
SLURM array jobs and DataLad on a shared-filesystem HPC.

---

## Prerequisites

| Tool | Where needed | Notes |
|------|-------------|-------|
| `datalad` | HPC login node | `module load datalad/...` |
| `apptainer` | HPC compute nodes | `module load apptainer/...` |
| `jq` | HPC login node | JSON parsing in the orchestration script |
| `sbatch` / `squeue` | HPC login node | SLURM scheduler |
| SSH access | DataLad server | For creating output repos |
| fMRIPrep `.sif` | HPC shared storage | Build via `scripts/build_apptainer.sh` |
| TemplateFlow cache | HPC shared storage | Pre-populate once, bind-mount at runtime |
| FreeSurfer `license.txt` | HPC shared storage | Bind-mount at runtime |

---

## Step 0 — Configure

Edit **`configs/fmriprep_openneuro_hpc.json`** and replace every `TODO` value:

```json
"paths": {
    "shared_input_base":  "/hpc/shared/openneuro",
    "shared_output_base": "/hpc/shared/derivatives",
    "scratch_dir":        "/scratch/$USER/fmriprep",
    "container":          "/hpc/containers/fmriprep-24.1.0.sif",
    "templateflow_dir":   "/hpc/shared/templateflow",
    "fs_license":         "/hpc/shared/freesurfer/license.txt",
    "log_dir":            "$HOME/logs/fmriprep",
    "subject_lists_dir":  "/hpc/shared/subject_lists"
},
"datalad": {
    "output_url_template": "ria+ssh://datalad.yourserver.edu/data/derivatives/{dataset_id}/fmriprep"
},
"hpc": {
    "partition": "compute",
    "modules": ["datalad/0.19.6", "apptainer/1.3.0"]
}
```

Verify no TODOs remain:
```bash
grep TODO configs/fmriprep_openneuro_hpc.json
# should produce no output
```

---

## Step 1 — Pre-populate TemplateFlow (once)

Run on the HPC login node (needs internet access):
```bash
export TEMPLATEFLOW_HOME=/hpc/shared/templateflow
python3 -c "
from templateflow.api import get
get('MNI152NLin2009cAsym', resolution=1)
get('MNI152NLin2009cAsym', resolution=2)
"
```

---

## Step 2 — Build fMRIPrep container (once)

```bash
# On the HPC login node (or build node):
./scripts/build_apptainer.sh \
    --docker nipreps/fmriprep:24.1.0 \
    --output /hpc/containers/fmriprep-24.1.0.sif
```

Or via the GUI → **Build** tab.

---

## Step 3 — Setup: clone datasets and create output repos

This clones all 39 OpenNeuro datasets to shared HPC storage and creates the
output DataLad dataset on your server. Run **once per dataset**; subsequent runs
are skipped automatically (`--resume` behaviour).

### GUI
1. Open the **HPC** tab → expand **OpenNeuro Cohort** panel
2. Confirm config path is `configs/fmriprep_openneuro_hpc.json`
3. Click **1. Setup**

### CLI
```bash
./scripts/submit_fmriprep_cohort.sh setup \
    --config configs/fmriprep_openneuro_hpc.json
```

Selective (one dataset):
```bash
./scripts/submit_fmriprep_cohort.sh setup -d ds000031
```

Dry run (print commands only):
```bash
./scripts/submit_fmriprep_cohort.sh setup --dry-run
```

---

## Step 4 — Submit: launch SLURM array jobs

For each dataset the script:
1. Reads subject list from the pre-cloned BIDS directory
2. Generates a SLURM array job script via `hpc_datalad_runner.py --array-mode`
3. Submits with `sbatch --array=0-N%50` (max 50 concurrent per dataset)

### GUI
1. Optionally check **Dry run** to preview without submitting
2. Click **2. Submit**

### CLI
```bash
# Dry run first
./scripts/submit_fmriprep_cohort.sh submit --dry-run

# Full submit
./scripts/submit_fmriprep_cohort.sh submit

# Subset of datasets only
./scripts/submit_fmriprep_cohort.sh submit -d ds000031 -d ds000256

# Skip datasets that already have a subject list (re-run after partial failure)
./scripts/submit_fmriprep_cohort.sh submit --resume
```

Job IDs are written to `logs/submission_YYYYMMDD_HHMMSS.log`.

---

## Step 5 — Monitor

### GUI
Click **3. Status** in the Cohort panel — queries `squeue` for all submitted jobs.

### CLI
```bash
# Show live SLURM status for all submitted array jobs
./scripts/submit_fmriprep_cohort.sh status

# Watch with auto-refresh (every 60 s)
watch -n 60 './scripts/submit_fmriprep_cohort.sh status'

# SLURM squeue directly
squeue -u $USER --format="%.18i %.9P %.30j %.8u %.8T %.10M %.9l %R"
```

---

## Step 6 — Identify and resubmit failures

After jobs complete, check which subjects are missing fMRIPrep output:
```bash
python3 scripts/check_app_output.py \
    --bids-dir /hpc/shared/openneuro/ds000031 \
    --derivatives-dir /hpc/shared/derivatives/ds000031/fmriprep \
    --pipeline fmriprep \
    --missing-only
```

Resubmit failed subjects for a specific dataset:
```bash
./scripts/submit_fmriprep_cohort.sh submit -d ds000031
```

---

## Per-subject array job — what happens inside SLURM

Each array task executes the following steps:

```
1. Resolve subject from list:  SUBJECT=$(sed -n "$((SLURM_ARRAY_TASK_ID+1))p" subjects.txt)
2. Clone input (fast):         datalad clone --reckless shared /shared/openneuro/DATASET bids/
3. Get subject files:          datalad get sub-LABEL/
4. Clone output + branch:      datalad clone --reckless shared /shared/derivatives/.../fmriprep out/
                                git checkout -b sub-LABEL
5. Run fMRIPrep:               apptainer exec fmriprep.sif /bids /output participant ...
6. Save + push:                datalad save -m "fMRIPrep sub-LABEL"
                                flock .push.lock datalad push --to origin
7. Cleanup:                    rm -rf $WORK_DIR
```

Generate and inspect the array script for any dataset without submitting:
```bash
python3 scripts/hpc_datalad_runner.py \
    --config configs/fmriprep_openneuro_hpc.json \
    --array-mode \
    --dataset-id ds000031 \
    --subject-list subject_lists/ds000031_subjects.txt \
    --output /tmp/ds000031_array.sh

cat /tmp/ds000031_array.sh
```

---

## Tuning for your cluster

| Parameter | Config key | Notes |
|-----------|-----------|-------|
| Wall time | `hpc.time` | fMRIPrep typically 8–20 h per subject |
| Memory | `hpc.mem` | 32 G is usually enough; raise if OOM |
| CPUs | `hpc.cpus` + `fmriprep.n_cpus` | Keep equal |
| Concurrent jobs | `hpc.max_concurrent` | Lower if cluster has tight queue limits |
| Partition | `hpc.partition` | Check your cluster's available partitions |

---

## Datasets included (39 total)

```
ds000031  ds000256  ds001454  ds002158  ds002372  ds003171  ds003382
ds003404  ds003823  ds003849  ds003929  ds003989  ds004161  ds004182
ds004466  ds004592  ds004627  ds004787  ds004892  ds005073  ds005127
ds005134  ds005339  ds005365  ds005454  ds005467  ds005525  ds005754
ds005795  ds005896  ds005901  ds006072  ds006148  ds006373  ds006707
ds007328  ds007522  ds007637  ds007694
```

To add or remove datasets, edit the `datasets` array in `configs/fmriprep_openneuro_hpc.json`.
