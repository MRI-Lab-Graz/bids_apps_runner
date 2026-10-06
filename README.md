# BIDS Apps Runner

A tool for running BIDS Apps (fMRIPrep, MRIQC, QSIPrep, FreeSurfer, …) on one machine or across many datasets on a SLURM cluster, with automatic output validation and reprocessing — from the command line or a browser GUI.

## Documentation

The guides live in [docs/](docs/) as plain Markdown (no build step). Start with:

- [docs/README_HPC_DATALAD.md](docs/README_HPC_DATALAD.md) — running a BIDS App across many datasets on SLURM with DataLad
- [docs/EXAMPLES_HPC_DATALAD.md](docs/EXAMPLES_HPC_DATALAD.md) — worked cohort examples
- [HPC_DEVELOPER_GUIDE.md](HPC_DEVELOPER_GUIDE.md) — repository layout and internals
- [docs/briefing_datalad_slurm_annex.md](docs/briefing_datalad_slurm_annex.md) — why and how the commit step works (annex-slurm)

## Overview

This tool gives you, in a browser GUI (or from scripts):

1. **Running BIDS Apps** with robust configuration management
2. **Validating pipeline outputs** to identify missing or incomplete data
3. **Automatic reprocessing** of missing subjects without manual intervention

## Key Features

- 🖥️ **Browser GUI**: build projects, run, monitor and validate — protected by a per-run access token
- 🚀 **Automated Pipeline Execution**: Run fMRIPrep, QSIPrep, FreeSurfer, and other BIDS Apps
- 🔍 **Smart Output Validation**: Automatically detect missing or incomplete pipeline outputs
- 🔄 **Seamless Reprocessing**: Identify and reprocess missing subjects in one command
- ⚡ **Parallel Processing**: Efficient multi-subject processing with configurable parallelization
- 📊 **Comprehensive Logging**: Detailed logs and validation reports
- 🐳 **Container Support**: Full Apptainer/Singularity container integration
- 🏛️ **HPC cohorts**: SLURM array jobs per dataset, results committed to DataLad/git-annex datasets

## Installation

1. **Clone the repository**

   ```bash
   git clone https://github.com/MRI-Lab-Graz/bids_apps_runner.git
   cd bids_apps_runner
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt     # or: ./scripts/install.sh  (--full adds dev tools)
   ```

3. **Set up containers**
   - Download your BIDS App containers (Apptainer/Singularity format)
   - Update container paths in your configuration files

## Requirements

- Python 3.10+
- Apptainer/Singularity for container execution
- Sufficient disk space for BIDS datasets and derivatives
- Optional, for HPC cohorts: SLURM, `git`/`git-annex`, DataLad, `jq`, `rsync` (see *Running on an HPC cluster*)

## Getting started: the browser GUI

The GUI is the main way to use this tool. It is a lightweight Flask/Waitress app (`prism_app_runner.py`) that runs on your machine (or on the cluster login node) and opens in your browser — no web server to set up.

1. **Start it**

   ```bash
   ./start_gui.sh            # or: python prism_app_runner.py
   ```

2. **Open the URL it prints**, e.g. `http://localhost:5000/?token=…` (the first free port from 5000 up). Open that exact URL once; the token is what keeps other users of the same machine out — see *Security defaults* below. On a remote machine, forward the port (in VS Code: the PORTS panel).
3. **Projects tab** — create a project: choose your BIDS folder (a local folder, or a study from your DataLad server once a site is configured), the container, and the app's options. The GUI reads the container's `--help` and offers the app's own arguments. Save.
4. **Run App tab** — check the settings, optionally estimate CPU/RAM with the pilot estimator, then **Save & Start Runner**. Watch the live log, stop a run, reuse saved settings.
5. **Check Output tab** — validate the derivatives against the BIDS input, list missing subjects, and send them straight back for reprocessing.
6. **Many datasets on a cluster?** Use the **BIDS App Cohort** panel (see [Running on an HPC cluster](#running-on-an-hpc-cluster-slurm--datalad)).

Other things the GUI does for you:

- scans a folder for Apptainer/Singularity images and checks for newer releases
- applies an optional per-run CPU cap (max usage %) to leave headroom for other containers on the same host
- creates and browses directories from the interface
- shows Docker pull progress and which container engines/tools are available
- edits SLURM settings per project (**HPC → Advanced**) and machine-wide defaults (**Machine Settings**, below)
- sends a push notification when a run finishes (see below)

Everything the GUI does is also scriptable — see [Command-line usage](#command-line-usage-scripting-and-automation).

### Security defaults

- **Every request needs the per-run access token.** On a shared login node every logged-in user can reach `127.0.0.1`, so loopback is never trusted. At startup the runner prints `http://localhost:<port>/?token=<token>`. Open that URL once and an HttpOnly, `SameSite=Strict` cookie carries the token after that. Each start generates a fresh token; set `PRISM_GUI_AUTH_TOKEN` to pin one. Scripts send it as `X-Prism-Auth: <token>` or `Authorization: Bearer <token>`.
- The GUI binds to `127.0.0.1` by default (`PRISM_GUI_HOST` overrides). Requests whose `Host` header isn't loopback or the bind host are refused, which blocks DNS rebinding; list extra names (e.g. behind a reverse proxy) in `PRISM_GUI_ALLOWED_HOSTS`, comma-separated.
- Cookie-authenticated POST/DELETE requests coming from another origin are refused.
- Optional password login on top: `PRISM_GUI_PASSWORD` or `PRISM_GUI_PASSWORD_HASH`.
- Set `PRISM_SECRET_KEY` if you do not want the runner to generate a per-machine local secret file.

### Setting it up for your site / as another user

Nothing in the code names a lab or a person. Site settings come from environment
variables, read from `site.env` by the app at startup (copy [site.env.example](site.env.example);
put it in the repo folder, or in `~/.config/bids_apps_runner/site.env` for yourself alone —
an exported variable beats both). Plain `KEY=VALUE` lines, never executed.

| Variable | Default | Meaning |
|---|---|---|
| `PRISM_SCRATCH_ROOT` | `/cl_tmp/<you>` | Root for bulk per-run data (never your home folder) |
| `PRISM_REMOTE_SSH_HOST`, `PRISM_REMOTE_BASE_PATH` | *(off)* | DataLad server (an `~/.ssh/config` alias + base path). Without both, "Remote (SSH)" datasets and cohort setup/submit say how to configure it instead of guessing |
| `PRISM_LOCAL_DATASET_BASE_DIR` | `$PRISM_SCRATCH_ROOT/datasets` | Where remote studies are cloned |
| `PRISM_COHORT_LOG_BASE_DIR` | `$PRISM_SCRATCH_ROOT/bids_apps_runner_cohort_logs` | Cohort logs, generated scripts, subject lists |
| `PRISM_DATA_DIR` | `~/.bids_apps_runner` | Projects, settings, secret key — never shared between users |

A **shared install** (one checkout many people run) works: each user gets their own data dir
and token, so projects and the secret key are never shared. A checkout that already holds its
own `projects/` keeps using it.

### Push notifications for long/detached runs

The GUI and the generated cluster jobs report completion and failure through
[ntfy](https://ntfy.sh) push notifications (`scripts/notify_ntfy.sh`), so you hear about a run
even with the browser closed. It is best-effort: a missing config or an unreachable server never
affects the run. Set up your own topic once:

```bash
cat > configs/ntfy.conf <<'EOF'
NTFY_SERVER="https://ntfy.sh"
NTFY_TOPIC="your-random-unguessable-topic"
EOF
chmod 600 configs/ntfy.conf   # the topic name is the only secret
```

Subscribe to that topic in the ntfy phone/desktop app. `configs/ntfy.conf` is gitignored — don't
commit it. (`NTFY_SERVER`/`NTFY_TOPIC` in the environment override the file.)

## Running on an HPC cluster (SLURM + DataLad)

To run a BIDS App over many datasets, the cohort workflow submits one SLURM array job per dataset
and commits the results back to a DataLad/git-annex dataset on your server. The array tasks are
plain compute (no git inside the job); a dependent finish job then commits, pushes and
checksum-verifies the output per subject (**annex-slurm**, see
[docs/README_HPC_DATALAD.md](docs/README_HPC_DATALAD.md)).

**In the GUI:** configure the project as usual (your cluster's partition, time, memory under
**HPC → Advanced**), then open the **BIDS App Cohort** panel:

1. **Setup** — clones the datasets and creates the output datasets (once).
2. Tick **Pilot (1 random subject)** and **Submit** — a real end-to-end run on one subject. Check
   the result before continuing. (**Preview derived config** and **Dry run** show what would happen.)
3. Untick Pilot and **Submit** — the whole cohort. **Status** shows each dataset's progress;
   **Resume** skips what already exists.

The same workflow from the command line:

```bash
cp configs/cohort_hpc_example.json my_cohort.json     # then edit every "TODO…" value
scripts/submit_bids_cohort.sh setup  -c my_cohort.json                # clone datasets, create outputs
scripts/submit_bids_cohort.sh submit -c my_cohort.json --dry-run      # preview
scripts/submit_bids_cohort.sh submit -c my_cohort.json --pilot        # ONE random subject first
scripts/submit_bids_cohort.sh submit -c my_cohort.json
scripts/submit_bids_cohort.sh status -c my_cohort.json
```

Bulk data (input clones, derivatives, scratch, logs) belongs on your cluster's large scratch
filesystem (`/cl_tmp/<you>/…` here), never in your home folder — see `PRISM_SCRATCH_ROOT` above.

**Login-node etiquette.** On a shared SLURM login node, only submit jobs and check status. The
runner enforces this: container runs (`--local`) refuse to start without a SLURM allocation, the
GUI sends anything heavy (output checks, `datalad drop`, cohort setup/submit) to a compute node
via `srun`, and the GUI shuts itself down after 30 minutes idle on a login node. Use
`salloc`/`srun` for interactive work. Details in [CLAUDE.md](CLAUDE.md).

## Global Machine Settings (Cross-Platform)

The GUI Projects tab now includes a **Machine Settings** section for host-dependent defaults.

By default, settings are auto-detected from the host OS and available tools:

- Linux: prefer Apptainer/Singularity
- macOS/Windows: prefer Docker
- Runtime fallback still checks installed tools and picks what is available

Use this when your students run the same project on different infrastructure (for example:
Linux server with Apptainer vs macOS with Docker Desktop).

What it controls:

- preferred container engine (`auto`, `apptainer`, `docker`)
- whether Docker/Apptainer are allowed on this machine
- default Apptainer image folder
- default Apptainer container image path
- default temp/work folder
- default Docker repo/tag
- default parallel jobs for new projects

Where it is stored:

- `DATA_DIR/configs/global_settings.json`
   - your data directory is `~/.bids_apps_runner` unless this checkout already holds your own `projects/` folder, or `PRISM_DATA_DIR` says otherwise (see [Setting it up for your site](#setting-it-up-for-your-site--as-another-user))
   - the file is created the first time you save Machine Settings in the GUI

New projects inherit resolved defaults for the current host. Existing projects remain unchanged unless you explicitly apply settings from the GUI.

## Command-line usage (scripting and automation)

Everything the GUI does is also available from the command line — useful for scripting, cron jobs and batch systems. The GUI saves projects as JSON; the same JSON drives `scripts/run_bids_apps.py`. The sections down to *Pilot Resource Estimation* cover it.

## Command-line Quick Start

### 1. Basic BIDS App Execution

```bash
# Run a BIDS App with a configuration file
python scripts/run_bids_apps.py -x configs/config.json

# Process specific subjects
python scripts/run_bids_apps.py -x configs/config.json --subjects sub-001 sub-002

# Dry run to test configuration
python scripts/run_bids_apps.py -x configs/config.json --dry-run
```

### 2. Automated Validation and Reprocessing Workflow

```bash
# Step 1: Validate pipeline outputs and generate missing subjects report
python scripts/check_app_output.py /data/bids /data/derivatives --output-json missing_subjects.json

# Step 2: Automatically reprocess missing subjects (--force is auto-enabled)
python scripts/run_bids_apps.py -x configs/config.json --from-json missing_subjects.json
```

## Configuration

Create a `configs/config.json` file with your pipeline settings (or build one in the GUI, which saves it as part of a project). A minimal example:

```json
{
  "common": {
    "bids_folder": "/data/bids",
    "output_folder": "/data/derivatives/fmriprep",
    "tmp_folder": "/tmp/fmriprep_work",
    "container": "/containers/fmriprep-24.0.0.sif",
    "jobs": 4
  },
  "app": {
    "analysis_level": "participant",
    "options": [
      "--fs-license-file", "/freesurfer/license.txt",
      "--output-spaces", "MNI152NLin2009cAsym:res-native",
      "--skip_bids_validation"
    ]
  }
}
```

## Core Components

### run_bids_apps.py

Main execution engine for running BIDS Apps with features:

- JSON-based configuration management
- Automatic subject discovery
- Parallel processing with configurable job limits
- Force reprocessing capabilities
- Comprehensive error handling and logging

### check_app_output.py

Output validation tool supporting:

- **fMRIPrep**: Validates preprocessed BOLD data, HTML reports, surface outputs
- **QSIPrep**: Checks DWI preprocessing, session handling, sidecar files
- **FreeSurfer**: Validates recon-all completion, longitudinal processing
- **QSIRecon**: Checks reconstruction pipelines and derivatives structure

## Command Line Options

### run_bids_apps.py

```bash
-x, --config         Configuration JSON file (required)
--subjects           Specific subjects to process
--from-json          Process subjects from validation JSON report
--pipeline           Filter specific pipeline from JSON report
--force              Force reprocessing (auto-enabled with --from-json)
--dry-run            Test configuration without execution
--pilot              Process one random subject for testing
--start-delay-sec    Delay between launching subjects/jobs (seconds)
--debug              Enable detailed container output
--log-level          Set logging verbosity (DEBUG, INFO, WARNING, ERROR)
```

### check_app_output.py

```bash
bids_dir             BIDS source directory
derivatives_dir      BIDS derivatives directory
-p, --pipeline       Check specific pipeline only
--output-json        Save detailed missing subjects report
--verbose            Detailed validation output
--quiet              Minimal output mode
```

## Supported Pipelines

| Pipeline | Container Support | Output Validation | Key Features |
|----------|------------------|-------------------|--------------|
| **fMRIPrep** | ✅ | ✅ | Preprocessed BOLD, HTML reports, surface outputs |
| **QSIPrep** | ✅ | ✅ | DWI preprocessing, multi-session support |
| **FreeSurfer** | ✅ | ✅ | Structural processing, longitudinal analysis |
| **QSIRecon** | ✅ | ✅ | DWI reconstruction, multiple recon pipelines |

## Advanced Features

### Automatic Force Mode

When using `--from-json`, the `--force` flag is automatically enabled to ensure missing subjects are reprocessed regardless of existing partial outputs.

### Smart Output Detection

The validation system uses pipeline-specific completion indicators:

- **fMRIPrep**: HTML reports + preprocessed files
- **QSIPrep**: HTML reports + desc-preproc DWI files
- **FreeSurfer**: recon-all.done markers
- **QSIRecon**: Reconstruction-specific output files

### Session Handling

Full support for multi-session BIDS datasets with automatic session detection and validation.

## Workflow Examples

### Complete Validation and Reprocessing

```bash
# 1. Check all pipelines and save detailed report
python scripts/check_app_output.py /data/bids /data/derivatives \
    --output-json validation_report.json --verbose

# 2. Reprocess only fMRIPrep missing subjects
python scripts/run_bids_apps.py -x configs/fmriprep_config.json \
    --from-json validation_report.json --pipeline fmriprep

# 3. Monitor progress
tail -f logs/bids_app_runner_*.log
```

### Quick Pipeline Testing

```bash
# Test configuration with one subject
python scripts/run_bids_apps.py -x configs/config.json --pilot --dry-run

# Run actual pilot test
python scripts/run_bids_apps.py -x configs/config.json --pilot --debug
```

### Pilot Resource Estimation (CPU/GPU/RAM)

```bash
# Auto-sweep CPU from 2 to detected max cores (default behavior)
python scripts/pilot_resource_estimator.py \
   --config configs/134_qsiprep.json \
   --subject sub-134001

# Use bounded auto-sweep (faster on large machines)
python scripts/pilot_resource_estimator.py \
   --config configs/134_qsiprep.json \
   --subject sub-134001 \
   --nprocs-min 2 --nprocs-max 32 --nprocs-step 2

# Or force explicit sweep points
python scripts/pilot_resource_estimator.py \
   --config configs/134_qsiprep.json \
   --subject sub-134001 \
   --nprocs 4,8,16,24

# Output report is written to logs/pilot_resource_estimator_*/pilot_resource_report.md
```

The estimator runs the same subject with different CPU settings, measures wall time,
peak memory, and GPU activity (if nvidia-smi is available), and writes a suggested
HPC block (`cpus`, `mem`, `time`, and optional `sbatch_gres`).

## Logging and Monitoring

- **Execution logs**: `logs/bids_app_runner_YYYYMMDD_HHMMSS.log`
- **Validation reports**: `validation_reports/validation_report_YYYYMMDD_HHMMSS.json`
- **Real-time monitoring**: Use `tail -f` on log files for live progress tracking

### Watching HPC/Slurm jobs

- **Per-cohort progress**: `scripts/submit_bids_cohort.sh status` -- reads
  the most recent `submission_*.log` for a given cohort config and reports
  each dataset's array/finish job state and subject-level progress.
- **All jobs for a user**: `scripts/watch_user_jobs.sh` -- a general "what's
  running for me right now" view, independent of any specific cohort
  config. Shows live `squeue` state plus today's terminal-state counts
  (COMPLETED/FAILED/TIMEOUT/...) from `sacct` per array job, since a task
  that finishes (success or failure) drops out of `squeue` immediately and
  so is otherwise invisible once done.

  ```bash
  scripts/watch_user_jobs.sh                     # one-shot snapshot for $USER
  scripts/watch_user_jobs.sh -w                   # auto-refresh every 30s (Ctrl-C to stop)
  scripts/watch_user_jobs.sh -w -n 10             # refresh every 10s
  scripts/watch_user_jobs.sh -j 5560071,5560082   # also track specific job IDs after they leave the queue
  scripts/watch_user_jobs.sh -u otheruser         # another user
  ```

## Troubleshooting

### Common Issues

1. **"All subjects already processed"**
   - Use `--force` flag or `--from-json` (which auto-enables force mode)
   - Check output detection logic with `--debug`

2. **Container execution fails**
   - Verify container path and permissions
   - Check bind mounts and directory access
   - Review container logs with `--debug`

3. **GPU run fails with `GLIBC_x.xx' not found` (Apptainer `--nv`)**
   - Apptainer's `--nv` bind-mounts the host's NVIDIA driver libraries into
     the container using a static library list; on some hosts that list
     also pulls in a host `libc.so.6` that shadows the container's own and
     is older than what the container's binaries need, causing an
     `ImportError: ... GLIBC_x.xx' not found`. This is a host/driver-library
     mismatch, not something an OS upgrade is required to fix.
   - Workaround: add `"--nvccli"` to the app's `apptainer_args` in your
     config (e.g. `"apptainer_args": ["--nvccli"]`) to switch Apptainer to
     using `nvidia-container-cli` for GPU setup instead of the static list.
     This requires the `nvidia-container-toolkit` (which provides
     `nvidia-container-cli`) to be installed on the host. The runner logs a
     matching hint automatically when it detects this error.

4. **Validation reports empty results**
   - Ensure correct BIDS directory structure
   - Verify pipeline-specific output formats
   - Use `--verbose` for detailed validation output

### Getting Help

- Check log files for detailed error messages
- Use `--dry-run` to test configurations safely
- Use `--debug` for verbose container output
- Review validation reports for missing data details

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Citation

If you use this tool in your research, please cite:

```
BIDS Apps Runner: Automated Pipeline Execution and Validation for BIDS Datasets
GitHub: https://github.com/MRI-Lab-Graz/bids_apps_runner
```
