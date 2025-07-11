# BIDS App Runner Documentation - Version 2.0.0

## Table of Contents

- [Prerequisites](#prerequisites)
- [New Features in Version 2.0.0](#new-features-in-version-200)
- [Configuration File](#configuration-file)
- [Usage](#usage)
- [Advanced Features](#advanced-features)
- [What Happens When You Run the Script](#what-happens-when-you-run-the-script)
- [Example Scenario](#example-scenario)
- [Troubleshooting](#troubleshooting)

## General

This Python script allows you to run a BIDS App container (such as fmriprep, mriqc, qsiprep, etc.) using a single JSON configuration file. The script is designed to be production-ready with comprehensive error handling, logging, and user-friendly features.

**Key Features:**
- **Robust Configuration**: Comprehensive validation of all settings
- **Flexible Processing**: Support for participant and group-level analysis
- **Smart Output Checking**: Automatic detection of completed subjects
- **Comprehensive Logging**: Detailed logs with customizable levels
- **Dry-Run Mode**: Test configurations without running containers
- **Parallel Processing**: Multi-core support for faster processing
- **Production Ready**: Bullet-proof error handling and recovery

------

## New Features in Version 2.0.0

### 🚀 Enhanced User Experience
- **Comprehensive Help**: Detailed command-line help with examples
- **Better Error Messages**: Clear, actionable error messages
- **Processing Summary**: Detailed summary of completed and failed subjects
- **Progress Tracking**: Real-time logging of processing status

### 🔧 Improved Configuration
- **Validation**: Thorough validation of all configuration parameters
- **Flexible Paths**: Support for both absolute and relative paths
- **Smart Defaults**: Intelligent default values for optional parameters
- **Custom Mounts**: Flexible container mount configuration

### 📊 Advanced Processing Features
- **Force Reprocessing**: `--force` flag to reprocess existing subjects
- **Subject Selection**: `--subjects` flag to process specific subjects
- **Dry-Run Mode**: `--dry-run` to preview commands without execution
- **Pilot Mode**: Test with a single random subject

### 🛡️ Production-Ready Features
- **Signal Handling**: Graceful shutdown on interrupts
- **Resource Management**: Automatic cleanup of temporary directories
- **Error Recovery**: Preserve debugging information on failures
- **Comprehensive Logging**: Structured logs with timestamps

------

## Prerequisites

Before running the script, ensure that:

- You have **Python 3** installed.
- [Apptainer](https://apptainer.org/) (or Singularity, if applicable) is installed on your system.
- The container image specified in your configuration file exists. If not, see [Create apptainer container](#Create apptainer container)
- Your BIDS dataset is organized correctly in the specified bids folder.

### Create apptainer container

#### Download apptainer script

```bash
sudo bash build_apptainer.sh -o /data/local/container/mriqc/ -t /data/local/container/tmp/
```

Options:

-o: output for container
-t:  TMP folder

**_NOTE:_** if version == latest => container_name = latest (CAVE!)

Enter Docker image repository (e.g., 'pennbbl/qsiprep'):

- Go to Docker Hub
- Search for version
- Select for example "nipreps/mriqc"
- Choose version (not latest and not latest release candidate-25.0.0rc0)

"Wait until done"

------

## Recommended: Using a Dedicated Python Environment

It is advisable to run this script in a dedicated Python environment. This practice isolates your project's dependencies, preventing potential conflicts with other Python packages on your system. Here are some steps to set up a dedicated environment using `venv`:

1. **Create a virtual environment:**

```bash
   python3 -m venv bidsapp_env
```

2. **Activate the virtual environment:**

- On Unix or MacOS:

```bash
source bidsapp_env/bin/activate
```

- On Windows:

     ```bash
     bidsapp_env\Scripts\activate
     ```

3. **Install required dependencies:**

If your script requires additional packages, install them using `pip`. For example:

```bash
pip install argparse concurrent.futures
```

*(Note: Many of the modules used in this script are part of Python's standard library, so you might not need extra packages unless you extend the script.)*

1. **Run the script within the virtual environment:**

   ```bash
   python run_bidsapp.py -x config.json
   ```

2. **Deactivate the environment when done:**

   ```bash
   deactivate
   ```

Using a dedicated environment ensures that your dependencies are managed and consistent across different systems or development stages.

------

## Configuration File

Create a JSON configuration file (e.g., `config.json`) with the following structure:

```json
{
"common": {
    "bids_folder": "/data/local/study_01/derivatives/qsiprep/",
    "output_folder": "/data/local/study_01/derivatives/qsirecon",
    "tmp_folder": "/data/local/study_01/derivative/sqsirecon_temp",
    "templateflow_dir": "/data/local/templateflow",
    "container": "/data/local/container/qsirecon/qsirecon_1.0.0.sif",
    "optional_folder": "/data/local/study_01/", 
    "jobs": 4,
    "pilottest": false
},
"app": {
    "analysis_level": "participant",
	"apptainer_args": [
		"--containall",
		"--writable-tmpfs"
	],
 "options": [
      "--fs-license-file", "/fs/license.txt",
      "--fs-subjects-dir", "/base/derivatives/freesurfer",
      "--recon-spec", "mrtrix_multishell_msmt_ACT-hsvs",
      "--nprocs", "6",
      "--atlases", "Brainnetome246Ext"
    ],
"mounts": [
	{ "source": "/usr/local/freesurfer", "target": "/fs" }
	],
"output_check": {
	"directory": "derivatives",
	"pattern": "sub-{subject}.html"
	}
	}
}

```

>Note: It is important to prepare each argument in quotes. It is also true for multiple arguments like `--output-spaces`. You can see that each output space is in quotes and comma-separated. **Do not use** "MNI152NLin2009cAsym:res-2 fsaverage:den-10k"!

### Configuration Details

- **common**
  - `bids_folder`: Path to your BIDS-formatted dataset.
  - `output_folder`: Directory where the container should write its output.
  - `tmp_folder`: Temporary folder used during processing.
  - `container`: Path to the container image (e.g., Singularity/Apptainer image).
  - `optional_folder`: An optional directory (if needed by your container) that will be mounted.
  - `jobs`: Number of parallel jobs to run (default is the number of CPUs if not specified).
  - `pilottest`: If set to `true`, the script will process only one randomly chosen subject.
- **app**
  - `analysis_level`: Analysis type. For subject-level analysis use `"participant"`; for group-level, set it to another value.
  - `options`: A list of additional command-line options to pass to the container.
  - `mounts`: A list of additional directory mounts. Each mount should specify a `source` (local folder) and a `target` (mount point inside the container).
  - `output_check`: Used to verify successful processing. Define the `directory` (relative to the output folder) and a filename `pattern` that includes a `{subject}` placeholder.
  - `participant_labels`: Specify single (or singel-picked) subjects. "sub-" prefixed should be skipped. sub-001 should be stated as "001

------

## Usage

### Basic Usage

```bash
# Run with default settings
python run_bids_apps.py -x config.json

# Run with debug logging
python run_bids_apps.py -x config.json --log-level DEBUG

# Preview commands without execution
python run_bids_apps.py -x config.json --dry-run

# Process specific subjects only
python run_bids_apps.py -x config.json --subjects sub-001 sub-002

# Force reprocessing of existing subjects
python run_bids_apps.py -x config.json --force

# Show help
python run_bids_apps.py --help
```

### Advanced Usage

```bash
# Pilot mode (set in config.json: "pilottest": true)
python run_bids_apps.py -x config.json

# Group-level analysis (set in config.json: "analysis_level": "group")
python run_bids_apps.py -x config.json

# Combination of flags
python run_bids_apps.py -x config.json --subjects sub-001 sub-002 --force --log-level DEBUG
```

### Command-Line Options

- `-x, --config`: Path to JSON configuration file (required)
- `--log-level`: Set logging level (DEBUG, INFO, WARNING, ERROR)
- `--dry-run`: Show commands without executing them
- `--subjects`: Process only specified subjects
- `--force`: Force reprocessing even if output exists
- `--version`: Show version information
- `--help`: Show help message

## Advanced Features

### 1. Output Checking
The script can automatically detect if a subject has already been processed:

```json
{
  "app": {
    "output_check": {
      "directory": "derivatives",
      "pattern": "sub-{subject}.html"
    }
  }
}
```

### 2. Custom Container Arguments
Configure Apptainer/Singularity arguments:

```json
{
  "app": {
    "apptainer_args": [
      "--containall",
      "--writable-tmpfs",
      "--cleanenv"
    ]
  }
}
```

### 3. Custom Mount Points
Add additional bind mounts:

```json
{
  "app": {
    "mounts": [
      {
        "source": "/usr/local/freesurfer",
        "target": "/fs"
      },
      {
        "source": "/data/shared/atlases",
        "target": "/atlases"
      }
    ]
  }
}
```

### 4. Parallel Processing
Configure the number of parallel jobs:

```json
{
  "common": {
    "jobs": 8
  }
}
```

### 5. Logging and Monitoring
- All runs create timestamped log files in the `logs/` directory
- Use `--log-level DEBUG` for detailed troubleshooting
- Processing summaries show success/failure counts
- Failed subjects preserve temp directories for debugging

------

## What Happens When You Run the Script

1. **Configuration Parsing and Validation:**
   The script reads your JSON configuration file and validates that all required paths and parameters are provided.
2. **Subject Discovery:**
   For participant-level analysis, the script will look for subject directories (folders starting with `sub-`) in your `bids_folder`.
3. **Processing:**
   - If in **pilot test** mode (i.e., `"pilottest": true`), a single subject is randomly selected and processed.
   - Otherwise, subjects are processed in parallel (using the number of jobs specified).
   - For each subject, the script builds an Apptainer command with the specified options and mounts, then runs the container.
   - After processing, the script checks for the expected output file. If found, the temporary directory is removed; otherwise, it is preserved for inspection.
4. **Group-Level Analysis:**
   If `analysis_level` is set to something other than `participant`, the container will be run once for a group-level analysis.

------

## Example Scenario

Suppose you have a dataset located at `/data/local/theo/Theo_fmriprep/rawdata`, and you want to run `fmriprep` using a container image located at `/data/local/container/fmriprep/fmriprep-24.0.0.sif`. You want the outputs to be stored in `/data/local/theo/Theo_fmriprep/derivatives` and you also have an optional folder at `/data/local/theo/Theo_fmriprep/`. Your configuration file (`config.json`) would look like the JSON example provided above.

Run the script with:

```bash
python run_bidsapp.py -x config.json
```

If the script finds directories like `sub-01`, `sub-02`, etc., in your bids folder, it will process each one (or a randomly selected one if in pilot mode) by running the container with all the required mounts and options.

------

## Troubleshooting

- **Missing Paths:**
  Ensure that all paths specified in the configuration file (BIDS folder, output folder, tmp folder, container image, etc.) exist.
- **Output Not Found:**
  If the expected output file (as defined by the `output_check` section) isn’t produced, check the container logs and verify that the options and mounts in the configuration file are correct.
- **Parallel Processing Issues:**
  If running multiple jobs causes problems, try setting `"jobs": 1` in the configuration file to run subjects sequentially.

------

This documentation should help you get started with running your BIDS App container using the provided Python script within a dedicated Python environment. If you have further questions or issues, please consult the script’s comments or reach out for support.
