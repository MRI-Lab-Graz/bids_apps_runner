# BIDS Apps Runner

A comprehensive tool for running BIDS Apps with automatic output validation and reprocessing capabilities.

## Documentation (Read the Docs)

This repository includes a Sphinx documentation site in `docs/` and a Read the Docs build config in `.readthedocs.yaml`.

- **Local build**:

   ```bash
   pip install -r docs/requirements.txt
   sphinx-build -b html docs docs/_build/html
   ```

- **Enable GitHub ↔ RTD integration** (one-time setup in the Read the Docs UI):
   1. Create a project on https://readthedocs.org
   2. Import `MRI-Lab-Graz/bids_apps_runner`
   3. Ensure the project uses the included `.readthedocs.yaml`

After you create the RTD project, you can add a badge by replacing `<rtd-project-slug>`:

```markdown
[![Documentation Status](https://readthedocs.org/projects/<rtd-project-slug>/badge/?version=latest)](https://<rtd-project-slug>.readthedocs.io/en/latest/?badge=latest)
```

## Overview

This tool provides a seamless workflow for:

1. **Running BIDS Apps** with robust configuration management
2. **Validating pipeline outputs** to identify missing or incomplete data
3. **Automatic reprocessing** of missing subjects without manual intervention

## Key Features

- 🚀 **Automated Pipeline Execution**: Run fMRIPrep, QSIPrep, FreeSurfer, and other BIDS Apps
- 🔍 **Smart Output Validation**: Automatically detect missing or incomplete pipeline outputs
- 🔄 **Seamless Reprocessing**: Identify and reprocess missing subjects in one command
- ⚡ **Parallel Processing**: Efficient multi-subject processing with configurable parallelization
- 📊 **Comprehensive Logging**: Detailed logs and validation reports
- 🐳 **Container Support**: Full Apptainer/Singularity container integration

## Quick Start

### 1. Basic BIDS App Execution

```bash
# Run a BIDS App with a configuration file
python run_bids_apps.py -x config.json

# Process specific subjects
python run_bids_apps.py -x config.json --subjects sub-001 sub-002

# Dry run to test configuration
python run_bids_apps.py -x config.json --dry-run
```

### 2. Automated Validation and Reprocessing Workflow

```bash
# Step 1: Validate pipeline outputs and generate missing subjects report
python check_app_output.py /data/bids /data/derivatives --output-json missing_subjects.json

# Step 2: Automatically reprocess missing subjects (--force is auto-enabled)
python run_bids_apps.py -x config.json --from-json missing_subjects.json
```

## Configuration

Create a `config.json` file with your pipeline settings. You can use `config_example.json` as a template:

```bash
cp config_example.json config.json
# Edit config.json with your specific paths and settings
```

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
python check_app_output.py /data/bids /data/derivatives \
    --output-json validation_report.json --verbose

# 2. Reprocess only fMRIPrep missing subjects
python run_bids_apps.py -x fmriprep_config.json \
    --from-json validation_report.json --pipeline fmriprep

# 3. Monitor progress
tail -f logs/bids_app_runner_*.log
```

### Quick Pipeline Testing

```bash
# Test configuration with one subject
python run_bids_apps.py -x config.json --pilot --dry-run

# Run actual pilot test
python run_bids_apps.py -x config.json --pilot --debug
```

## Browser-based GUI

The project ships with a lightweight Flask/Waitress application (`app_gui.py` plus `templates/index.html`) so you can build configurations and drive `run_bids_apps.py` from a browser. Launch the GUI with `bash gui/start_gui.sh` (or `python app_gui.py`) and point your browser at `http://localhost:8080` to:

- scan a directory for Apptainer/Singularity images, check for newer releases, and load container-specific help automatically
- assemble BIDS, derivatives, and temp folders along with runner overrides (subjects, pilot, dry-run, validation, etc.)
- peek at live runner logs, start/stop the background job, and reuse previously saved configs

The interface fetches the container's `--help` output to surface pipeline-specific arguments, links directly to the upstream documentation, and runs `run_bids_apps.py` in the background via `--nohup`. Read the GUI reference on Read the Docs to see how the REST endpoints, log tailing, and help parsing work.

## Installation

1. **Clone the repository**

   ```bash
   git clone https://github.com/MRI-Lab-Graz/bids_apps_runner.git
   cd bids_apps_runner
   ```

2. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

3. **Set up containers**
   - Download your BIDS App containers (Apptainer/Singularity format)
   - Update container paths in your configuration files

## Requirements

- Python 3.8+
- Apptainer/Singularity for container execution
- Sufficient disk space for BIDS datasets and derivatives
- Optional: Slurm for HPC environments (see `run_bids_apps_hpc.py`)

## Logging and Monitoring

- **Execution logs**: `logs/bids_app_runner_YYYYMMDD_HHMMSS.log`
- **Validation reports**: `validation_reports/validation_report_YYYYMMDD_HHMMSS.json`
- **Real-time monitoring**: Use `tail -f` on log files for live progress tracking

## Troubleshooting

### Common Issues

1. **"All subjects already processed"**
   - Use `--force` flag or `--from-json` (which auto-enables force mode)
   - Check output detection logic with `--debug`

2. **Container execution fails**
   - Verify container path and permissions
   - Check bind mounts and directory access
   - Review container logs with `--debug`

3. **Validation reports empty results**
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
