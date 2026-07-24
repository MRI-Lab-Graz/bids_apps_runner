import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import concat_subregion_results as concat_mod


def _write_volumes(path, volumes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for label, value in volumes.items():
            f.write(f"{label} {value}\n")


def test_concat_structure_file_builds_wide_csv(tmp_path):
    subjects_dir = tmp_path / "subjects"
    _write_volumes(
        subjects_dir / "sub-001_ses-1" / "mri" / "ThalamicNuclei.volumes.txt",
        {"Left-Whole_thalamus": "6500.123456", "Right-Whole_thalamus": "6600.654321"},
    )
    _write_volumes(
        subjects_dir / "sub-002_ses-1" / "mri" / "ThalamicNuclei.volumes.txt",
        {"Left-Whole_thalamus": "6100.000000", "Right-Whole_thalamus": "6200.000000"},
    )

    out_path = tmp_path / "results" / "ThalamicNuclei_cross_concat.csv"
    n = concat_mod.concat_structure_file(
        subjects_dir,
        ["sub-001_ses-1", "sub-002_ses-1"],
        "ThalamicNuclei.volumes.txt",
        out_path,
    )

    assert n == 2
    with open(out_path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["timepoint"] for r in rows] == ["sub-001_ses-1", "sub-002_ses-1"]
    assert rows[0]["Left-Whole_thalamus"] == "6500.123456"
    assert rows[1]["Right-Whole_thalamus"] == "6200.000000"


def test_concat_structure_file_skips_missing_files(tmp_path):
    subjects_dir = tmp_path / "subjects"
    _write_volumes(
        subjects_dir / "sub-001_ses-1" / "mri" / "ThalamicNuclei.volumes.txt",
        {"Left-Whole_thalamus": "6500.0"},
    )
    # sub-002_ses-1 has no output file at all (e.g. that array task failed).

    out_path = tmp_path / "results" / "ThalamicNuclei_cross_concat.csv"
    n = concat_mod.concat_structure_file(
        subjects_dir,
        ["sub-001_ses-1", "sub-002_ses-1"],
        "ThalamicNuclei.volumes.txt",
        out_path,
    )

    assert n == 1
    with open(out_path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["timepoint"] for r in rows] == ["sub-001_ses-1"]


def test_concat_structure_file_returns_zero_when_nothing_found(tmp_path):
    subjects_dir = tmp_path / "subjects"
    out_path = tmp_path / "results" / "ThalamicNuclei_cross_concat.csv"
    n = concat_mod.concat_structure_file(
        subjects_dir, ["sub-001_ses-1"], "ThalamicNuclei.volumes.txt", out_path
    )
    assert n == 0
    assert not out_path.exists()


def test_main_cross_sectional_end_to_end(tmp_path):
    subjects_dir = tmp_path / "subjects"
    _write_volumes(
        subjects_dir / "sub-001_ses-1" / "mri" / "ThalamicNuclei.volumes.txt",
        {"Left-Whole_thalamus": "6500.0"},
    )
    _write_volumes(
        subjects_dir / "sub-001_ses-1" / "mri" / "lh.hippoSfVolumes.txt",
        {"whole_hippocampus": "3400.0"},
    )
    _write_volumes(
        subjects_dir / "sub-001_ses-1" / "mri" / "rh.hippoSfVolumes.txt",
        {"whole_hippocampus": "3450.0"},
    )
    _write_volumes(
        subjects_dir / "sub-001_ses-1" / "mri" / "lh.amygNucVolumes.txt",
        {"Whole_amygdala": "1500.0"},
    )
    _write_volumes(
        subjects_dir / "sub-001_ses-1" / "mri" / "rh.amygNucVolumes.txt",
        {"Whole_amygdala": "1520.0"},
    )
    _write_volumes(
        subjects_dir / "sub-001_ses-1" / "mri" / "brainstemSsLabels.volumes.txt",
        {"Medulla": "5000.0"},
    )

    timepoint_list = tmp_path / "timepoints.txt"
    timepoint_list.write_text("sub-001_ses-1\n")
    results_dir = tmp_path / "results"

    rc = concat_mod.main_from_args(
        subjects_dir=str(subjects_dir),
        mode="cross",
        structures=["thalamus", "hippo-amygdala", "brainstem"],
        timepoint_list=str(timepoint_list),
        results_dir=str(results_dir),
    )

    assert rc == 0
    assert (results_dir / "ThalamicNuclei_cross_concat.csv").exists()
    assert (results_dir / "lh.hippoSfVolumes_cross_concat.csv").exists()
    assert (results_dir / "rh.amygNucVolumes_cross_concat.csv").exists()
    assert (results_dir / "brainstemSsLabels_cross_concat.csv").exists()


def test_main_longitudinal_expands_base_to_long_timepoints(tmp_path):
    subjects_dir = tmp_path / "subjects"
    _write_volumes(
        subjects_dir / "sub-001_ses-1.long.sub-001" / "mri" / "ThalamicNuclei.long.volumes.txt",
        {"Left-Whole_thalamus": "6500.0"},
    )
    _write_volumes(
        subjects_dir / "sub-001_ses-2.long.sub-001" / "mri" / "ThalamicNuclei.long.volumes.txt",
        {"Left-Whole_thalamus": "6480.0"},
    )

    timepoint_list = tmp_path / "timepoints.txt"
    timepoint_list.write_text("sub-001\n")
    results_dir = tmp_path / "results"

    rc = concat_mod.main_from_args(
        subjects_dir=str(subjects_dir),
        mode="longitudinal",
        structures=["thalamus"],
        timepoint_list=str(timepoint_list),
        results_dir=str(results_dir),
    )

    assert rc == 0
    out_path = results_dir / "ThalamicNuclei_longitudinal_concat.csv"
    with open(out_path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert sorted(r["timepoint"] for r in rows) == [
        "sub-001_ses-1.long.sub-001",
        "sub-001_ses-2.long.sub-001",
    ]


def test_main_returns_nonzero_when_no_output_found(tmp_path):
    subjects_dir = tmp_path / "subjects"
    subjects_dir.mkdir()
    timepoint_list = tmp_path / "timepoints.txt"
    timepoint_list.write_text("sub-001_ses-1\n")

    rc = concat_mod.main_from_args(
        subjects_dir=str(subjects_dir),
        mode="cross",
        structures=["thalamus"],
        timepoint_list=str(timepoint_list),
        results_dir=str(tmp_path / "results"),
    )
    assert rc == 1
