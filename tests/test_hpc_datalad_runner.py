import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import hpc_datalad_runner


def _base_config():
    return {
        "paths": {
            "shared_input_base": "/tmp/input base",
            "shared_output_base": "/tmp/output base",
            "scratch_dir": "/tmp/scratch base",
            "container": "/containers/fmriprep 24.0.0.sif",
            "templateflow_dir": "/tmp/template flow",
            "fs_license": "/tmp/license file.txt",
            "log_dir": "/tmp/log dir",
        },
        "hpc": {
            "partition": "standard",
            "time": "24:00:00",
            "mem": "32G",
            "cpus": 8,
            "max_concurrent": 10,
            "modules": ["apptainer/1.3.0"],
            "environment": {"TEMPLATEFLOW_HOME": "/tmp/template flow"},
        },
        "bids_app": {
            "app_name": "fmriprep",
            "analysis_level": "participant",
            "output_dir_name": "fmriprep",
            "options": ["--skip-bids-validation", "--output-spaces", "MNI152NLin6Asym"],
        },
    }


def _write_subject_list(tmp_path, subjects):
    list_path = tmp_path / "subjects.txt"
    list_path.write_text("\n".join(subjects) + "\n")
    return str(list_path)


def test_array_generator_rejects_unsafe_dataset_id(tmp_path):
    subj_list = _write_subject_list(tmp_path, ["sub-01"])
    with pytest.raises(ValueError):
        hpc_datalad_runner.BidsAppComputeScriptGenerator(
            _base_config(), "ds; touch /tmp/pwned", subj_list, 1
        )


def test_array_generator_has_no_datalad_or_git_calls(tmp_path):
    subj_list = _write_subject_list(tmp_path, ["sub-01", "sub-02"])
    script = hpc_datalad_runner.BidsAppComputeScriptGenerator(
        _base_config(), "ds001", subj_list, 2
    ).generate_script()

    assert "datalad" not in script
    assert "git " not in script
    assert "git-annex" not in script
    assert "flock" not in script


def test_array_generator_uses_persistent_clones_and_per_task_scratch():
    script = hpc_datalad_runner.BidsAppComputeScriptGenerator(
        _base_config(), "ds001", "/tmp/subjects.txt", 2
    ).generate_script()

    assert "BIDS_DIR='/tmp/input base/ds001'" in script
    assert "OUT_DIR='/tmp/output base/ds001/fmriprep'" in script
    assert "WORK_DIR='/tmp/scratch base'/ds001/${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}" in script
    assert '#SBATCH --array=0-1%10' in script


def test_array_generator_quotes_shell_values():
    script = hpc_datalad_runner.BidsAppComputeScriptGenerator(
        _base_config(), "ds001", "/tmp/subjects.txt", 1
    ).generate_script()

    assert "export TEMPLATEFLOW_HOME='/tmp/template flow'" in script
    assert "-B '/tmp/template flow':/templateflow:ro" in script
    assert "-B '/tmp/license file.txt':/fs/license.txt:ro" in script
    assert "'/containers/fmriprep 24.0.0.sif'" in script
    assert "--output-spaces \\\n    MNI152NLin6Asym" in script


def test_array_generator_rejects_unsafe_sbatch_value():
    config = _base_config()
    config["hpc"]["sbatch_qos"] = "normal; touch /tmp/pwned"

    with pytest.raises(ValueError):
        hpc_datalad_runner.BidsAppComputeScriptGenerator(
            config, "ds001", "/tmp/subjects.txt", 1
        ).generate_script()


def test_generate_script_writes_single_line_subject_list(tmp_path):
    config_path = tmp_path / "config.json"
    import json

    config_path.write_text(json.dumps(_base_config()))
    output_path = tmp_path / "job.sh"

    script = hpc_datalad_runner.generate_script(
        str(config_path), "sub-07", str(output_path)
    )

    subjects_file = tmp_path / "job.sh.subjects.txt"
    assert subjects_file.read_text().strip() == "sub-07"
    assert "#SBATCH --array=0-0" in script
    assert "datalad" not in script


def test_generate_script_rejects_unsafe_subject(tmp_path):
    config_path = tmp_path / "config.json"
    import json

    config_path.write_text(json.dumps(_base_config()))

    with pytest.raises(SystemExit):
        hpc_datalad_runner.generate_script(
            str(config_path), "sub-01; touch /tmp/pwned", str(tmp_path / "job.sh")
        )
