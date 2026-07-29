import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_app_output import BIDSOutputValidator, FreeSurferChecker


def _make_t1w(anat_dir):
    anat_dir.mkdir(parents=True, exist_ok=True)
    (anat_dir / "sub-01_T1w.nii.gz").write_text("fake")


def _make_fs_dir(fs_root, name):
    d = fs_root / name
    (d / "scripts").mkdir(parents=True, exist_ok=True)
    (d / "scripts" / "recon-all.done").write_text("done")
    return d


def _has_count_mismatch(missing_items):
    return any("folder count mismatch" in m for m in missing_items)


class TestSessionFiltering:
    """Reproduces the study-129 scenario: a subject has anat data in
    ses-1/ses-2/ses-3, but FreeSurfer was only ever run on ses-1/ses-2 by
    design -- `expected_sessions` should stop the checker from treating
    ses-3 as a gap."""

    def _make_three_session_subject(self, tmp_path):
        bids = tmp_path / "bids"
        fs = tmp_path / "derivatives" / "freesurfer"
        for ses in ("ses-1", "ses-2", "ses-3"):
            _make_t1w(bids / "sub-01" / ses / "anat")
        for name in (
            "sub-01",
            "sub-01_ses-1",
            "sub-01_ses-1.long.sub-01",
            "sub-01_ses-2",
            "sub-01_ses-2.long.sub-01",
        ):
            _make_fs_dir(fs, name)
        return bids, fs

    def test_unscoped_flags_missing_third_session(self, tmp_path):
        bids, fs = self._make_three_session_subject(tmp_path)

        checker = FreeSurferChecker(bids, fs)
        checker.check_pipeline(fs)

        assert _has_count_mismatch(checker.missing_items)

    def test_scoped_to_processed_sessions_does_not_flag(self, tmp_path):
        bids, fs = self._make_three_session_subject(tmp_path)

        checker = FreeSurferChecker(bids, fs, expected_sessions={"ses-1", "ses-2"})
        checker.check_pipeline(fs)

        assert not _has_count_mismatch(checker.missing_items)

    def test_expected_sessions_reported_in_stats_sorted(self, tmp_path):
        checker = FreeSurferChecker(tmp_path, tmp_path, expected_sessions={"ses-2", "ses-1"})
        assert checker.stats["expected_sessions"] == ["ses-1", "ses-2"]

    def test_no_filter_defaults_to_none(self, tmp_path):
        checker = FreeSurferChecker(tmp_path, tmp_path)
        assert checker.expected_sessions is None
        assert checker.stats["expected_sessions"] is None

    def test_empty_set_treated_as_no_filter(self, tmp_path):
        checker = FreeSurferChecker(tmp_path, tmp_path, expected_sessions=set())
        assert checker.expected_sessions is None


class TestSingleTimepointLongitudinal:
    """A subject with only one BIDS session can still legitimately get a
    full base+cross+long FreeSurfer output set if the pipeline always runs
    the longitudinal stream -- that shouldn't be flagged as an unexpected
    'extra' folder."""

    def test_base_cross_long_for_single_session_is_not_flagged(self, tmp_path):
        bids = tmp_path / "bids"
        fs = tmp_path / "derivatives" / "freesurfer"
        _make_t1w(bids / "sub-02" / "ses-1" / "anat")
        for name in ("sub-02", "sub-02_ses-1", "sub-02_ses-1.long.sub-02"):
            _make_fs_dir(fs, name)

        checker = FreeSurferChecker(bids, fs)
        checker.check_pipeline(fs)

        assert not _has_count_mismatch(checker.missing_items)

    def test_plain_cross_sectional_single_session_still_expects_one_folder(self, tmp_path):
        bids = tmp_path / "bids"
        fs = tmp_path / "derivatives" / "freesurfer"
        _make_t1w(bids / "sub-03" / "ses-1" / "anat")
        _make_fs_dir(fs, "sub-03_ses-1")

        checker = FreeSurferChecker(bids, fs)
        checker.check_pipeline(fs)

        assert not _has_count_mismatch(checker.missing_items)

    def test_still_flags_genuinely_missing_recon(self, tmp_path):
        bids = tmp_path / "bids"
        fs = tmp_path / "derivatives" / "freesurfer"
        _make_t1w(bids / "sub-04" / "ses-1" / "anat")
        (fs / "sub-04_ses-1" / "scripts").mkdir(parents=True)  # no recon-all.done

        checker = FreeSurferChecker(bids, fs)
        checker.check_pipeline(fs)

        assert any("recon-all.done missing" in m for m in checker.missing_items)


class TestBIDSOutputValidatorSessions:
    """End-to-end (still in-process, no subprocess) check that
    BIDSOutputValidator forwards expected_sessions all the way down to the
    checker it instantiates -- reproduces the actual study-129 report going
    from 'failed' to 'passed' once the pipeline's session scope is known."""

    def test_validator_forwards_expected_sessions_to_checker(self, tmp_path):
        bids = tmp_path / "bids"
        fs = tmp_path / "derivatives"
        for ses in ("ses-1", "ses-2", "ses-3"):
            _make_t1w(bids / "sub-01" / ses / "anat")
        fs_dir = fs / "freesurfer"
        for name in (
            "sub-01",
            "sub-01_ses-1",
            "sub-01_ses-1.long.sub-01",
            "sub-01_ses-2",
            "sub-01_ses-2.long.sub-01",
        ):
            _make_fs_dir(fs_dir, name)
        for long_name in ("sub-01_ses-1.long.sub-01", "sub-01_ses-2.long.sub-01"):
            mri_dir = fs_dir / long_name / "mri"
            mri_dir.mkdir(parents=True, exist_ok=True)
            (mri_dir / "lh.hippoSfVolumes.long.sub-01.txt").write_text("fake")
            (mri_dir / "lh.amygNucVolumes.long.sub-01.txt").write_text("fake")

        unscoped = BIDSOutputValidator(bids, fs)
        assert unscoped.validate_pipeline("freesurfer")["status"] == "failed"

        scoped = BIDSOutputValidator(bids, fs, expected_sessions={"ses-1", "ses-2"})
        result = scoped.validate_pipeline("freesurfer")
        assert result["status"] == "passed"
        assert result["total_missing"] == 0
