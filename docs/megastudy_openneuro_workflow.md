# OpenNeuro Mega-Study Workflow — mriqc + fMRIPrep across many datasets

Run the same container/HPC settings across many independently-downloaded
OpenNeuro BIDS datasets that share a DataLad server, first with mriqc, then
with fMRIPrep. Datasets differ in acquisition parameters and some lack
fieldmaps entirely, so per-dataset option overrides are supported without
needing a separate config per dataset.

CLI-only (no GUI support yet) — see `CLAUDE.md` if you want to extend this
to the GUI later.

---

## Prerequisites

| Tool / thing | Where | Notes |
|---|---|---|
| `datalad`, `git-annex` | HPC login node | Already installed here (`~/.local/bin`) |
| `datalad-slurm` extension | HPC login node | `pip install git+https://github.com/knuedd/datalad-slurm.git` (not on PyPI) |
| `.datalad-slurm-venv` | repo root | Dedicated venv pinned to a portable Python — see `scripts/submit_bids_cohort.sh` header comment for setup |
| `jq` | HPC login node | JSON parsing in `submit_bids_cohort.sh` |
| `sbatch` / `squeue` / `sacct` | HPC login node | SLURM |
| SSH alias `datalad-server` | `~/.ssh/config` | Must carry the right `User`/`IdentityFile` — see `gui/gui_utility_routes.py:22`. Same alias the GUI's remote-dataset browsing already uses |
| mriqc `.sif` | `/usr/people/mrilabgraz/container/mriqc/mriqc_24.0.2.sif` | Already present |
| fMRIPrep `.sif` | — | **Not built yet** — nothing under `/usr/people/mrilabgraz/container/` for fmriprep. Build/pull one before running the fmriprep config |
| FreeSurfer `license.txt` | `/usr/people/mrilabgraz/container/fastsurfer/license.at` | Already present, reused by the fmriprep config |

⚠️ **All bulk data (input clones, derivatives, scratch, logs, subject
lists) lives under `/cl_tmp/mrilabgraz`** — never `/usr/people/mrilabgraz`,
which doesn't have the quota. See `CLAUDE.md`.

⚠️ **Never run real compute on the HPC login node.** Everything below
(`setup`, `submit`, the check/sync scripts) only submits jobs or does
metadata-only network I/O — it's all safe on the login node by design. See
`CLAUDE.md`'s login-node policy section.

---

## The configs

Two cohort config files, sharing the same `datasets` list, one container
each:

- `configs/megastudy_openneuro_mriqc.json`
- `configs/megastudy_openneuro_fmriprep.json`

**mriqc and fmriprep have no dependency on each other** (mriqc is
standalone QC, fmriprep doesn't read mriqc output) — they're two entirely
independent cohort runs, not a chained pipeline. Run them in either order,
or in parallel.

Each dataset entry in `datasets[]` is either a plain ID string, or an
object carrying a per-dataset override:
```json
{ "id": "ds005525", "options_extra": ["--use-syn-sdc"] }
```
`options_extra` is merged into that dataset's `bids_app.options` only —
every other dataset keeps the config's default options. This is how
datasets without fieldmaps get different fmriprep flags without a
separate config file per dataset (see `scripts/hpc_datalad_runner.py`'s
`_apply_dataset_options_override`).

---

## Step 1 — Keep the dataset list in sync

Datasets live under `/datalad/mri/openneuro/<dataset_id>` on
`datalad-server`, kept current by an external OpenNeuro crawler. Don't
hand-maintain the list — sync it:

```bash
python3 scripts/sync_openneuro_datasets.py \
  -c configs/megastudy_openneuro_mriqc.json \
  -c configs/megastudy_openneuro_fmriprep.json
```

- Only **adds** newly-seen `ds######` IDs, as plain strings.
- Never touches existing entries, so `options_extra` overrides are always
  preserved.
- `--dry-run` previews without writing; `--prune` removes IDs no longer
  present remotely (off by default, since a config entry — especially an
  override — reflects a deliberate decision that shouldn't vanish just
  because a listing hiccuped).

---

## Step 2 — Check for pre-existing derivatives conflicts

Some OpenNeuro datasets ship their own pre-computed derivatives (e.g.
mriqc reports from the original study), committed as plain files rather
than as a proper DataLad subdataset. `submit_bids_cohort.sh setup` expects
`derivatives/<app>` to become a nested DataLad dataset — if that path is
already tracked as plain content (on whichever branch `derivatives` would
be created from), `datalad create` and `git submodule add` both fail with
`'derivatives/mriqc' already exists in the index`, and that dataset's
output clone gets skipped (real incidents: `ds000256`, `ds002837`,
`ds003823`, `ds004182`, `ds007328`, 2026-08-03).

Scan for this **before** running `setup`, in one read-only batched SSH
round-trip:

```bash
python3 scripts/check_derivatives_conflicts.py -c configs/megastudy_openneuro_mriqc.json
```

Reports each dataset as:
- `CONFLICT` — plain content blocks registration, needs fixing (below)
- `OK` — already a proper subdataset, nothing to do
- `CLEAN` — no `derivatives/<app>` yet, first `setup` will create it fine
- `MISSING_REPO` — the dataset path doesn't exist on the server at all (separate problem)

Entirely read-only — no branch checkout, no writes — safe to re-run
anytime.

### Fixing CONFLICT datasets

Generate a ready-to-review removal script for every `CONFLICT` dataset
(review before running — this commits a removal to real, shared,
already-committed content):

```bash
python3 scripts/check_derivatives_conflicts.py -c configs/megastudy_openneuro_mriqc.json --emit-removal-script
```

Removes `derivatives/<app>` (only that app's subfolder, not the whole
`derivatives/` tree) and commits the removal via `datalad save`; restores
whichever branch was checked out if a `derivatives` branch already
happened to exist. Read it, then run it — e.g.:

```bash
python3 scripts/check_derivatives_conflicts.py -c configs/megastudy_openneuro_mriqc.json --emit-removal-script \
  | tail -n +5 | ssh datalad-server bash -s
```

Re-run the scanner afterward to confirm `CONFLICT: 0`.

---

## Step 3 — Fieldmap check (fmriprep only)

mriqc doesn't care about fieldmaps; fmriprep does. Check a dataset's
cloned BIDS root for fieldmap coverage before deciding whether it needs an
`options_extra` override:

```bash
python3 scripts/check_fieldmaps.py /cl_tmp/mrilabgraz/openneuro/data/<dataset_id>
```

Reports per-subject presence/absence and flags mixed coverage. Purely
advisory — not wired into `setup`/`submit` — you decide and edit
`configs/megastudy_openneuro_fmriprep.json` yourself, e.g.:

```json
{ "id": "ds005525", "options_extra": ["--use-syn-sdc"] }
```

---

## Step 4 — Setup (once per config)

Pre-clones every dataset to `/cl_tmp/mrilabgraz/openneuro/data/`, creates
each output DataLad repo on the server, registers it on a `derivatives`
branch:

```bash
./scripts/submit_bids_cohort.sh setup -c configs/megastudy_openneuro_mriqc.json
```

Idempotent — safe to re-run. Watch the `Setup finished. Failures: X/31`
line; if non-zero, go back to Step 2.

---

## Step 5 — Pilot run (sanity check)

`--pilot` submits **one randomly-chosen subject per dataset** (not one
subject total) — a real run through the full container/mount/DataLad
path, cheap, across the whole cohort:

```bash
./scripts/submit_bids_cohort.sh submit -c configs/megastudy_openneuro_mriqc.json --pilot
```

To narrow to a single dataset instead of all 31, add `-d`:
```bash
./scripts/submit_bids_cohort.sh submit -c configs/megastudy_openneuro_mriqc.json -d ds000031 --pilot
```

---

## Step 6 — Full submit

```bash
./scripts/submit_bids_cohort.sh submit -c configs/megastudy_openneuro_mriqc.json
```

One SLURM array job per dataset (subjects as array indices), plus one
dependent `slurm-finish` + push job per dataset, chained via
`--dependency=afterany`. `-d <dataset_id>` (repeatable) scopes to specific
datasets; `--resume` skips datasets whose subject list/job script already
exist; `--dry-run` previews without executing.

---

## Step 7 — Monitor

```bash
./scripts/submit_bids_cohort.sh status
```

Reads `submission.log`, queries `sacct`/`squeue` for every logged job.

---

## Step 8 — Repeat for fmriprep

Once the mriqc cohort looks good (and an fmriprep container exists),
repeat Steps 1–7 with `configs/megastudy_openneuro_fmriprep.json` — same
dataset list, independent run, no ordering requirement relative to mriqc.

---

## Quick reference

```bash
# 1. sync dataset list
python3 scripts/sync_openneuro_datasets.py -c configs/megastudy_openneuro_mriqc.json -c configs/megastudy_openneuro_fmriprep.json

# 2. check + fix derivatives conflicts
python3 scripts/check_derivatives_conflicts.py -c configs/megastudy_openneuro_mriqc.json
python3 scripts/check_derivatives_conflicts.py -c configs/megastudy_openneuro_mriqc.json --emit-removal-script | tail -n +5 | ssh datalad-server bash -s

# 3. (fmriprep only) fieldmap check per dataset
python3 scripts/check_fieldmaps.py /cl_tmp/mrilabgraz/openneuro/data/<dataset_id>

# 4. setup
./scripts/submit_bids_cohort.sh setup -c configs/megastudy_openneuro_mriqc.json

# 5. pilot (one subject per dataset)
./scripts/submit_bids_cohort.sh submit -c configs/megastudy_openneuro_mriqc.json --pilot

# 6. full submit
./scripts/submit_bids_cohort.sh submit -c configs/megastudy_openneuro_mriqc.json

# 7. monitor
./scripts/submit_bids_cohort.sh status
```

---

## Related files

| File | Role |
|---|---|
| `configs/megastudy_openneuro_mriqc.json` | mriqc cohort config |
| `configs/megastudy_openneuro_fmriprep.json` | fmriprep cohort config |
| `scripts/sync_openneuro_datasets.py` | keeps `datasets[]` current from the server's folder structure |
| `scripts/check_derivatives_conflicts.py` | detects + generates a fix for pre-existing plain derivatives content |
| `scripts/check_fieldmaps.py` | advisory per-subject fieldmap presence report |
| `scripts/submit_bids_cohort.sh` | setup/submit/status orchestration (SLURM + DataLad) |
| `scripts/hpc_datalad_runner.py` | generates the per-dataset SLURM array script, applies `options_extra` |
| `scripts/prism_datalad.py` | shared SSH connection helpers (`list_remote_directory_names`, `run_remote_script`) — same connection the GUI's remote-dataset browsing uses |

Note: `docs/hpc_fmriprep_workflow_hpc.md` describes an earlier, similar
39-dataset fmriprep-only plan referencing files that were never actually
committed (`configs/fmriprep_openneuro_hpc.json`,
`scripts/submit_fmriprep_cohort.sh`) — this document describes the
version that's actually implemented and in use.
