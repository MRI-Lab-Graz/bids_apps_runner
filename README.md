# BIDS App Runner Documentation

## Table of Contents

- [Prerequisites](#prerequisites)
- [Recommended: Using a Dedicated Python Environment](#recommended-using-a-dedicated-python-environment)
- [Configuration File](#configuration-file)
	- [Configuration Details](#configuration-details)
- [Usage](#usage)
- [What Happens When You Run the Script](#what-happens-when-you-run-the-script)
- [Example Scenario](#example-scenario)
- [Troubleshooting](#troubleshooting)

## General

This Python script allows you to run a BIDS App container (such as fmriprep, mriqc, etc.) using a single JSON configuration file. The configuration file contains two main sections:

- **common**: General settings (paths to data, output directories, container image, parallel processing options, etc.)
- **app**: Application-specific settings (analysis level, extra container options, additional mounts, and output-checking)

The script automatically discovers subjects in your BIDS directory (folders starting with `sub-`) and will run the container for each subject (or a single subject in pilot mode). Alternatively, if you set the analysis level to something other than `participant`, the container will run once for a group-level analysis.

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
    "bids_folder": "/data/local/theo/Theo_fmriprep/rawdata",
    "output_folder": "/data/local/theo/Theo_fmriprep/derivatives",
    "tmp_folder": "/data/local/theo/Theo_fmriprep/code",
    "container": "/data/local/container/fmriprep/fmriprep-24.0.0.sif",
    "optional_folder": "/data/local/theo/Theo_fmriprep/",
    "jobs": 1,
    "pilottest": false
  },
  "app": {
    "analysis_level": "participant",
    "participant_labels": ["001"],
    "options": [
"--fs-license-file", "/fs/license.txt",
"--output-spaces", "MNI152NLin2009cAsym:res-2", "fsaverage:den-10k",
"--skip_bids_validation"
    ],
    "mounts": [
{ "source": "/usr/local/freesurfer", "target": "/fs" }
    ],
    "output_check": {
"directory": "derivatives",
"pattern": "sub-{subject}_report.html"
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

Run the script from the command line by passing the path to your configuration file. For example:

```bash
python run_bidsapp.py -x config.json
```

### What Happens When You Run the Script

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
