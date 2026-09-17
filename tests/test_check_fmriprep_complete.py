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
