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


def _fastsurfer_bids_config(gpu=True):
    config = _base_config()
    config["paths"]["container"] = "/containers/fastsurfer_bids_cuda-v2.5.4.sif"
    config["bids_app"] = {
        "app_name": "fastsurfer",
        "analysis_level": "participant",
        "output_dir_name": "fastsurfer",
        "execution_adapter": "fastsurfer-bids",
        "options": ["--3T", "--cereb"],
    }
    if gpu:
        config["hpc"]["sbatch_gres"] = "gpu:1"
    return config


def test_fastsurfer_bids_adapter_calls_run_fastsurfer_bids_py(tmp_path):
    subj_list = _write_subject_list(tmp_path, ["sub-01", "sub-02"])
    script = hpc_datalad_runner.BidsAppComputeScriptGenerator(
        _fastsurfer_bids_config(gpu=True), "ds001", subj_list, 2
    ).generate_script()

    # Uses the FastSurfer-bids entrypoint via apptainer exec, not the
    # generic apptainer run /bids /output participant convention -- the
    # container's default ENTRYPOINT is run_fastsurfer.sh, which doesn't
    # understand that convention at all.
    assert "python3 /fastsurfer/run_fastsurfer_bids.py" in script
    assert '"$APPTAINER_BIN" exec' in script
    assert "--participant_label" in script
    assert '"${SUBJECT_LABEL}"' in script
    assert "--nv" in script
    # slurm_nohog kills any job that touches a GPU index other than the one
    # SLURM assigned -- --cleanenv strips CUDA_VISIBLE_DEVICES before the
    # container starts unless explicitly re-passed via --env. _shell_quote
    # wraps the value in single quotes since it contains ${...}.
    assert "--env 'CUDA_VISIBLE_DEVICES" in script
    assert "${CUDA_VISIBLE_DEVICES:-}" in script
    # And not the generic path's flags for this same job.
    assert "--participant-label" not in script
    assert "-w /tmp/wdir" not in script
    # Passthrough options after the BIDS-App's own args, separated by --.
    assert "-- \\\n    --3T \\\n    --cereb" in script


def test_fastsurfer_bids_adapter_cpu_only_forces_empty_cuda_visible_devices(tmp_path):
    subj_list = _write_subject_list(tmp_path, ["sub-01", "sub-02"])
    script = hpc_datalad_runner.BidsAppComputeScriptGenerator(
        _fastsurfer_bids_config(gpu=False), "ds001", subj_list, 2
    ).generate_script()

    # No sbatch_gres requesting a GPU -- --nv must not be added (the node's
    # GPUs, if any, aren't reserved for this job), and CUDA_VISIBLE_DEVICES
    # must be forced empty so any CUDA-probing library (e.g. PyTorch just
    # checking torch.cuda.is_available()) can't touch a device slurm_nohog
    # would flag as misuse against an allocation with zero GPUs.
    assert "--nv" not in script
    assert "--env CUDA_VISIBLE_DEVICES=" in script
    assert "${CUDA_VISIBLE_DEVICES:-}" not in script


def test_generic_bids_app_gpu_request_passes_through_cuda_visible_devices(tmp_path):
    # Same slurm_nohog exposure as fastsurfer-bids: a GPU job whose
    # CUDA_VISIBLE_DEVICES doesn't survive --cleanenv will use physical GPU 0
    # regardless of what SLURM actually assigned, and gets killed as
    # "misuse". Covers the generic _run_bids_app path (qsiprep/fmriprep/etc,
    # not just the FastSurfer-specific one).
    config = _base_config()
    config["hpc"]["sbatch_gres"] = "gpu:1"
    subj_list = _write_subject_list(tmp_path, ["sub-01"])
    script = hpc_datalad_runner.BidsAppComputeScriptGenerator(
        config, "ds001", subj_list, 1
    ).generate_script()

    assert "--env" in script
    assert "CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-}" in script


def test_fastsurfer_bids_adapter_binds_fs_license(tmp_path):
    subj_list = _write_subject_list(tmp_path, ["sub-01"])
    script = hpc_datalad_runner.BidsAppComputeScriptGenerator(
        _fastsurfer_bids_config(), "ds001", subj_list, 1
    ).generate_script()

    assert "-B '/tmp/license file.txt':/fs/license.txt:ro" in script
    assert "--fs_license \\\n    /fs/license.txt" in script


def test_fastsurfer_bids_adapter_script_is_valid_bash(tmp_path):
    import subprocess

    subj_list = _write_subject_list(tmp_path, ["sub-01", "sub-02", "sub-03"])
    script = hpc_datalad_runner.BidsAppComputeScriptGenerator(
        _fastsurfer_bids_config(), "ds001", subj_list, 3
    ).generate_script()

    script_path = subj_list.replace("subjects.txt", "job.sh")
    with open(script_path, "w") as f:
        f.write(script)
    result = subprocess.run(["bash", "-n", script_path], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_fastsurfer_cross_execution_adapter_does_not_use_bids_entrypoint(tmp_path):
    # execution_adapter "fastsurfer-cross" (or unset) must still fall through
    # to the generic apptainer-run path here -- only "fastsurfer-bids" is
    # implemented in this generator so far.
    config = _fastsurfer_bids_config()
    config["bids_app"]["execution_adapter"] = "fastsurfer-cross"
    subj_list = _write_subject_list(tmp_path, ["sub-01"])
    script = hpc_datalad_runner.BidsAppComputeScriptGenerator(
        config, "ds001", subj_list, 1
    ).generate_script()

    assert "run_fastsurfer_bids.py" not in script
    assert '"$APPTAINER_BIN" run' in script


def _freesurfer_bids_config():
    config = _base_config()
    config["paths"]["container"] = "/containers/freesurfer_bids_8.2.0.sif"
    config["bids_app"] = {
        "app_name": "freesurfer",
        "analysis_level": "participant",
        "output_dir_name": "freesurfer",
        "execution_adapter": "freesurfer-bids",
        "options": ["--3T"],
    }
    return config


def test_freesurfer_bids_adapter_calls_run_py(tmp_path):
    subj_list = _write_subject_list(tmp_path, ["sub-01", "sub-02"])
    script = hpc_datalad_runner.BidsAppComputeScriptGenerator(
        _freesurfer_bids_config(), "ds001", subj_list, 2
    ).generate_script()

    # Uses the wrapper's own entrypoint via apptainer exec, not the generic
    # apptainer run convention -- run.py auto-discovers a subject's sessions
    # and dispatches cross-sectional -> base template -> longitudinal
    # internally, so no per-session looping is needed at this layer either.
    assert "python /run.py" in script
    assert '"$APPTAINER_BIN" exec' in script
    assert "--participant_label" in script
    assert '"${SUBJECT_LABEL}"' in script
    # recon-all is CPU-only: no GPU handling at all for this adapter.
    assert "--nv" not in script
    assert "CUDA_VISIBLE_DEVICES" not in script
    # And not the generic path's flags for this same job.
    assert "--participant-label" not in script
    assert "-w /tmp/wdir" not in script
    # Passthrough options after the wrapper's own runtime-managed args.
    assert "--3T" in script


def test_freesurfer_bids_adapter_binds_fs_license(tmp_path):
    subj_list = _write_subject_list(tmp_path, ["sub-01"])
    script = hpc_datalad_runner.BidsAppComputeScriptGenerator(
        _freesurfer_bids_config(), "ds001", subj_list, 1
    ).generate_script()

    assert "-B '/tmp/license file.txt':/fs/license.txt:ro" in script
    assert "--license_file \\\n    /fs/license.txt" in script


def test_freesurfer_bids_group_level_omits_participant_label(tmp_path):
    # Unlike fastsurfer-bids (participant-only), run.py supports group1/
    # group2 analysis levels too, and --participant_label must not be
    # passed for those.
    config = _freesurfer_bids_config()
    config["bids_app"]["analysis_level"] = "group1"
    subj_list = _write_subject_list(tmp_path, ["sub-01"])
    script = hpc_datalad_runner.BidsAppComputeScriptGenerator(
        config, "ds001", subj_list, 1
    ).generate_script()

    assert "--participant_label" not in script
    assert "group1" in script


def test_freesurfer_bids_session_label_passes_through(tmp_path):
    # --session_label restricts a longitudinal run to a subset of a
    # subject's BIDS sessions (default with no flag: every session found).
    # Unlike --participant_label/--license_file, the adapter never sets
    # this itself, so it must survive _prepare_freesurfer_bids_options
    # rather than being silently dropped as a runtime-managed flag.
    config = _freesurfer_bids_config()
    config["bids_app"]["options"] = ["--session_label", "1", "2"]
    subj_list = _write_subject_list(tmp_path, ["sub-01"])
    script = hpc_datalad_runner.BidsAppComputeScriptGenerator(
        config, "ds001", subj_list, 1
    ).generate_script()

    assert "--session_label \\\n    1 \\\n    2" in script


def test_freesurfer_bids_adapter_script_is_valid_bash(tmp_path):
    import subprocess

    subj_list = _write_subject_list(tmp_path, ["sub-01", "sub-02", "sub-03"])
    script = hpc_datalad_runner.BidsAppComputeScriptGenerator(
        _freesurfer_bids_config(), "ds001", subj_list, 3
    ).generate_script()

    script_path = subj_list.replace("subjects.txt", "job.sh")
    with open(script_path, "w") as f:
        f.write(script)
    result = subprocess.run(["bash", "-n", script_path], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


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
