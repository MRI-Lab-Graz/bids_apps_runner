import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import hpc_datalad_runner


def _base_config():
    return {
        "common": {"work_dir": "/tmp/bids work"},
        "datalad": {
            "input_repo": "ria+file:///tmp/repo name",
            "output_repos": ["derivatives/fmriprep"],
            "clone_method": "clone",
        },
        "hpc": {
            "partition": "standard",
            "time": "24:00:00",
            "mem": "32G",
            "cpus": 8,
            "job_name": "bids_app",
            "output_log": "slurm-%j.out",
            "error_log": "slurm-%j.err",
            "modules": ["apptainer/1.3.0"],
            "environment": {"TEMPLATEFLOW_HOME": "/tmp/template flow"},
        },
        "container": {
            "image": "docker://nipreps/fmriprep:latest",
            "name": "fmriprep",
            "outputs": ["derivatives/fmriprep"],
            "inputs": ["inputs/raw data"],
            "bids_args": {
                "bids_folder": "sourcedata",
                "output_folder": "derivatives/out folder",
                "analysis_level": "participant",
                "fs-license-file": "/tmp/license file.txt",
                "skip-bids-validation": True,
            },
        },
    }


def test_hpc_generator_rejects_unsafe_subject():
    with pytest.raises(ValueError):
        hpc_datalad_runner.DataLadHPCScriptGenerator(
            _base_config(), "sub-01; touch /tmp/pwned"
        )


def test_hpc_generator_quotes_shell_values():
    script = hpc_datalad_runner.DataLadHPCScriptGenerator(
        _base_config(), "sub-01"
    ).generate_script()

    assert "export TEMPLATEFLOW_HOME='/tmp/template flow'" in script
    assert "flock --verbose \"$DS_LOCKFILE\" datalad clone 'ria+file:///tmp/repo name' \"$DS_DIR\"" in script
    assert "-i 'inputs/raw data'" in script
    assert "--fs-license-file '/tmp/license file.txt'" in script


def test_hpc_generator_rejects_unsafe_sbatch_value():
    config = _base_config()
    config["hpc"]["sbatch_qos"] = "normal; touch /tmp/pwned"

    with pytest.raises(ValueError):
        hpc_datalad_runner.DataLadHPCScriptGenerator(config, "sub-01").generate_script()