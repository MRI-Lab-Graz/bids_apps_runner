import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_fieldmaps


def _make_subject(root, label, with_fmap, with_session=False):
    subject_dir = root / f"sub-{label}"
    fmap_dir = (
        subject_dir / "ses-01" / "fmap" if with_session else subject_dir / "fmap"
    )
    anat_dir = (
        subject_dir / "ses-01" / "anat" if with_session else subject_dir / "anat"
    )
    anat_dir.mkdir(parents=True)
    (anat_dir / f"sub-{label}_T1w.nii.gz").write_text("x")
    if with_fmap:
        fmap_dir.mkdir(parents=True)
        (fmap_dir / f"sub-{label}_magnitude1.nii.gz").write_text("x")


def test_check_fieldmaps_reports_presence_and_absence(tmp_path):
    _make_subject(tmp_path, "01", with_fmap=True)
    _make_subject(tmp_path, "02", with_fmap=False)

    results = check_fieldmaps.check_fieldmaps(str(tmp_path))

    assert results["sub-01"]["has_fieldmaps"] is True
    assert results["sub-02"]["has_fieldmaps"] is False
    assert results["sub-02"]["fmap_files"] == []


def test_check_fieldmaps_finds_session_level_fmap(tmp_path):
    _make_subject(tmp_path, "01", with_fmap=True, with_session=True)

    results = check_fieldmaps.check_fieldmaps(str(tmp_path))

    assert results["sub-01"]["has_fieldmaps"] is True
    assert "ses-01/fmap" in results["sub-01"]["fmap_files"][0]


def test_summarize_flags_dataset_with_no_fieldmaps_anywhere(tmp_path):
    _make_subject(tmp_path, "01", with_fmap=False)
    _make_subject(tmp_path, "02", with_fmap=False)

    results = check_fieldmaps.check_fieldmaps(str(tmp_path))
    summary = check_fieldmaps.summarize(results)

    assert "without fieldmaps: 2" in summary
    assert "options_extra override" in summary


def test_summarize_flags_mixed_coverage(tmp_path):
    _make_subject(tmp_path, "01", with_fmap=True)
    _make_subject(tmp_path, "02", with_fmap=False)

    results = check_fieldmaps.check_fieldmaps(str(tmp_path))
    summary = check_fieldmaps.summarize(results)

    assert "MIXED fieldmap coverage" in summary
    assert "sub-02" in summary
