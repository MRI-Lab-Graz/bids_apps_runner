"""Regression test for check_fmriprep_complete.py's core logic.

Real incident (2026-09-17): ds003849's sub-BPD0113B/sub-BPD0119A had an
`.html` report on disk but fMRIPrep never produced desc-preproc_bold.nii.gz
for them -- html-report-existence was being used as a completeness proxy
and it isn't reliable. This checks the real output file + volume count
instead. nibabel is optional at import time so this file can be collected
without it; the tests themselves are skipped if it's unavailable.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

nib = pytest.importorskip("nibabel")
import numpy as np  # noqa: E402

from check_fmriprep_complete import fmriprep_complete_report  # noqa: E402


def _write_nifti(path: Path, nvols: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.zeros((4, 4, 4, nvols), dtype="float32")
    nib.save(nib.Nifti1Image(data, affine=np.eye(4)), str(path))


def _write_json(path: Path) -> None:
    path.write_text(json.dumps({"TaskName": "rest"}))


@pytest.fixture
def bids_and_fmriprep(tmp_path):
    bids_dir = tmp_path / "bids"
    fmriprep_dir = tmp_path / "fmriprep"

    # sub-01: complete -- raw and processed volume counts match
    raw = bids_dir / "sub-01" / "func" / "sub-01_task-rest_run-01_bold.nii.gz"
    _write_nifti(raw, nvols=100)
    _write_json(raw.with_name(raw.name.replace(".nii.gz", ".json")))
    proc = fmriprep_dir / "sub-01" / "func" / "sub-01_task-rest_run-01_space-MNI152NLin2009cAsym_desc-preproc_bold.nii.gz"
    _write_nifti(proc, nvols=100)

    # sub-02: fMRIPrep output missing entirely (the real incident's shape)
    raw2 = bids_dir / "sub-02" / "func" / "sub-02_task-rest_run-01_bold.nii.gz"
    _write_nifti(raw2, nvols=100)

    # sub-03: fMRIPrep output present but truncated
    raw3 = bids_dir / "sub-03" / "func" / "sub-03_task-rest_run-01_bold.nii.gz"
    _write_nifti(raw3, nvols=100)
    proc3 = fmriprep_dir / "sub-03" / "func" / "sub-03_task-rest_run-01_space-MNI152NLin2009cAsym_desc-preproc_bold.nii.gz"
    _write_nifti(proc3, nvols=12)

    return bids_dir, fmriprep_dir


def test_complete_subject_passes(bids_and_fmriprep):
    bids_dir, fmriprep_dir = bids_and_fmriprep
    report = fmriprep_complete_report(str(fmriprep_dir), str(bids_dir), "ds_fake")
    assert report["sub-01"]["complete"] is True
    assert report["sub-01"]["runs"][0]["nvols"] == 100


def test_missing_output_is_incomplete(bids_and_fmriprep):
    bids_dir, fmriprep_dir = bids_and_fmriprep
    report = fmriprep_complete_report(str(fmriprep_dir), str(bids_dir), "ds_fake")
    assert report["sub-02"]["complete"] is False
    assert report["sub-02"]["runs"][0]["reason"] == "missing"


def test_truncated_output_is_incomplete(bids_and_fmriprep):
    bids_dir, fmriprep_dir = bids_and_fmriprep
    report = fmriprep_complete_report(str(fmriprep_dir), str(bids_dir), "ds_fake")
    assert report["sub-03"]["complete"] is False
    assert "truncated" in report["sub-03"]["runs"][0]["reason"]


def test_multi_echo_output_drops_echo_entity(tmp_path):
    """fMRIPrep COMBINES multi-echo runs, so its output carries no `echo-` entity.

    Deriving the expected filename straight from the raw name kept `echo-1` in
    it, so every multi-echo subject was reported missing. Confirmed against
    ds006707 (2026-09-25): expected
    sub-101_ses-01_task-rest_run-1_echo-1_part-mag_space-..._desc-preproc_bold.nii.gz
    while the real output is
    sub-101_ses-03_task-rest_run-1_part-mag_space-..._desc-preproc_bold.nii.gz
    -- all 184 runs flagged while the data was there.
    """
    bids_dir = tmp_path / "bids"
    fmriprep_dir = tmp_path / "fmriprep"
    for echo in (1, 2):
        raw = (bids_dir / "sub-04" / "ses-01" / "func"
               / f"sub-04_ses-01_task-rest_echo-{echo}_part-mag_bold.nii.gz")
        _write_nifti(raw, nvols=50)
    proc = (fmriprep_dir / "sub-04" / "ses-01" / "func"
            / "sub-04_ses-01_task-rest_part-mag_space-MNI152NLin2009cAsym_desc-preproc_bold.nii.gz")
    _write_nifti(proc, nvols=50)

    report = fmriprep_complete_report(str(fmriprep_dir), str(bids_dir), "ds_fake")
    assert report["sub-04"]["complete"] is True
    # The echoes collapse onto one combined output: report it once, not per echo.
    assert len(report["sub-04"]["runs"]) == 1


def test_one_session_processed_is_complete_under_cross_sectional_design(tmp_path):
    """Since 3c7d25a (2026-09-21) each subject contributes exactly ONE scan.

    The checker predates that and required every raw run in every session to
    have an output, so it reported by-design behaviour as missing data -- e.g.
    ds004592 came back "ok=1/2" for all 27 subjects. A subject is complete when
    at least one resting-state run is verified; an unprocessed sibling session
    is a deliberate choice, not a failure.
    """
    bids_dir = tmp_path / "bids"
    fmriprep_dir = tmp_path / "fmriprep"
    for ses in ("01", "02"):
        raw = bids_dir / "sub-05" / f"ses-{ses}" / "func" / f"sub-05_ses-{ses}_task-rest_bold.nii.gz"
        _write_nifti(raw, nvols=80)
    proc = (fmriprep_dir / "sub-05" / "ses-02" / "func"
            / "sub-05_ses-02_task-rest_space-MNI152NLin2009cAsym_desc-preproc_bold.nii.gz")
    _write_nifti(proc, nvols=80)

    report = fmriprep_complete_report(str(fmriprep_dir), str(bids_dir), "ds_fake")
    assert report["sub-05"]["complete"] is True
    assert report["sub-05"]["verified_runs"] == 1


def test_present_but_corrupt_output_still_fails_under_any_semantics(tmp_path):
    """Relaxing to "at least one run" must NOT excuse a broken output.

    Catching present-but-invalid output is the reason this tool exists, so a
    run whose output EXISTS and is truncated keeps the subject incomplete even
    when a sibling run is fine.
    """
    bids_dir = tmp_path / "bids"
    fmriprep_dir = tmp_path / "fmriprep"
    for ses, nvols in (("01", 80), ("02", 80)):
        raw = bids_dir / "sub-06" / f"ses-{ses}" / "func" / f"sub-06_ses-{ses}_task-rest_bold.nii.gz"
        _write_nifti(raw, nvols=nvols)
    good = (fmriprep_dir / "sub-06" / "ses-01" / "func"
            / "sub-06_ses-01_task-rest_space-MNI152NLin2009cAsym_desc-preproc_bold.nii.gz")
    _write_nifti(good, nvols=80)
    truncated = (fmriprep_dir / "sub-06" / "ses-02" / "func"
                 / "sub-06_ses-02_task-rest_space-MNI152NLin2009cAsym_desc-preproc_bold.nii.gz")
    _write_nifti(truncated, nvols=9)

    report = fmriprep_complete_report(str(fmriprep_dir), str(bids_dir), "ds_fake")
    assert report["sub-06"]["complete"] is False
    assert "truncated" in report["sub-06"]["reason"]
