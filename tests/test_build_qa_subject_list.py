import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_qa_subject_list as qa


def _write_iqm(root, subject, task, run, fd_mean, session=None):
    ses_part = f"ses-{session}_" if session else ""
    func_dir = root / f"sub-{subject}" / (f"ses-{session}" if session else "") / "func"
    func_dir.mkdir(parents=True, exist_ok=True)
    name = f"sub-{subject}_{ses_part}task-{task}_run-{run}_bold.json"
    (func_dir / name).write_text(json.dumps({"fd_mean": fd_mean}))


def _write_anat(root, subject, session, suffix="T1w"):
    anat_dir = root / f"sub-{subject}" / f"ses-{session}" / "anat"
    anat_dir.mkdir(parents=True, exist_ok=True)
    (anat_dir / f"sub-{subject}_ses-{session}_{suffix}.nii.gz").write_bytes(b"")


def test_subject_passes_with_single_low_motion_rest_run(tmp_path):
    _write_iqm(tmp_path, "01", "rest", 1, fd_mean=0.1)

    results = qa.build_qa_report(str(tmp_path), "ds999999", fd_threshold=0.5)

    assert results["sub-01"]["qa_valid"] is True
    assert results["sub-01"]["selected"]["fd_mean"] == 0.1


def test_subject_fails_when_fd_mean_exceeds_threshold(tmp_path):
    _write_iqm(tmp_path, "01", "rest", 1, fd_mean=0.8)

    results = qa.build_qa_report(str(tmp_path), "ds999999", fd_threshold=0.5)

    assert results["sub-01"]["qa_valid"] is False
    assert "0.800" in results["sub-01"]["reason"]


def test_subject_passes_on_cross_sectional_best_of_multiple_runs(tmp_path):
    """2026-09-21 rule change: cross-sectional design keeps one scan per
    subject -- the BEST of several candidate runs decides QA, not
    requiring every run to pass."""
    _write_iqm(tmp_path, "01", "rest", 1, fd_mean=0.1)
    _write_iqm(tmp_path, "01", "rest", 2, fd_mean=0.9)

    results = qa.build_qa_report(str(tmp_path), "ds999999", fd_threshold=0.5)

    assert results["sub-01"]["qa_valid"] is True
    assert results["sub-01"]["n_candidates"] == 2
    assert results["sub-01"]["selected"]["fd_mean"] == 0.1
    assert results["sub-01"]["selected"]["run"] == "1"


def test_subject_fails_when_best_of_multiple_runs_still_exceeds_threshold(tmp_path):
    _write_iqm(tmp_path, "01", "rest", 1, fd_mean=0.6)
    _write_iqm(tmp_path, "01", "rest", 2, fd_mean=0.9)

    results = qa.build_qa_report(str(tmp_path), "ds999999", fd_threshold=0.5)

    assert results["sub-01"]["qa_valid"] is False
    assert results["sub-01"]["n_candidates"] == 2


def test_selected_run_records_session_entity(tmp_path):
    _write_iqm(tmp_path, "01", "rest", 1, fd_mean=0.2, session="A")
    _write_iqm(tmp_path, "01", "rest", 1, fd_mean=0.1, session="B")

    results = qa.build_qa_report(str(tmp_path), "ds999999", fd_threshold=0.5)

    assert results["sub-01"]["selected"]["session"] == "B"


def test_subject_with_no_matching_resting_state_run_is_excluded(tmp_path):
    _write_iqm(tmp_path, "01", "nback", 1, fd_mean=0.1)

    results = qa.build_qa_report(str(tmp_path), "ds999999", fd_threshold=0.5)

    assert results["sub-01"]["qa_valid"] is False
    assert results["sub-01"]["n_candidates"] == 0
    assert "no resting-state IQM found" in results["sub-01"]["reason"]


def test_dataset_specific_task_label_is_used(tmp_path):
    _write_iqm(tmp_path, "01", "restawake", 1, fd_mean=0.1)
    _write_iqm(tmp_path, "02", "restlight", 1, fd_mean=0.1)

    results = qa.build_qa_report(str(tmp_path), "ds003171", fd_threshold=0.5)

    assert results["sub-01"]["qa_valid"] is True
    assert results["sub-02"]["qa_valid"] is False


def test_summarize_lists_excluded_subjects_with_reasons(tmp_path):
    _write_iqm(tmp_path, "01", "rest", 1, fd_mean=0.1)
    _write_iqm(tmp_path, "02", "rest", 1, fd_mean=0.9)

    results = qa.build_qa_report(str(tmp_path), "ds999999", fd_threshold=0.5)
    summary = qa.summarize(results)

    assert "QA-valid:   1" in summary
    assert "excluded:   1" in summary
    assert "sub-02" in summary


def test_main_writes_subject_list_and_report(tmp_path, capsys):
    mriqc_dir = tmp_path / "mriqc"
    _write_iqm(mriqc_dir, "01", "rest", 1, fd_mean=0.1)
    _write_iqm(mriqc_dir, "02", "rest", 1, fd_mean=0.9)
    out_file = tmp_path / "ds999999_subjects.txt"
    report_file = tmp_path / "report.json"

    sys.argv = [
        "build_qa_subject_list.py",
        "ds999999",
        str(mriqc_dir),
        "--out",
        str(out_file),
        "--report-json",
        str(report_file),
    ]
    qa.main()

    assert out_file.read_text().splitlines() == ["sub-01"]
    report = json.loads(report_file.read_text())
    assert report["sub-02"]["qa_valid"] is False


def test_main_writes_bids_filter_only_for_multi_session_subjects(tmp_path):
    mriqc_dir = tmp_path / "mriqc"
    bids_dir = tmp_path / "bids"
    # sub-01: single scan, no session/run entity to pin -> no filter file needed
    _write_iqm(mriqc_dir, "01", "rest", 1, fd_mean=0.1)
    # sub-02: two sessions -> best one (ses-B) must be pinned; anat lives in both
    _write_iqm(mriqc_dir, "02", "rest", 1, fd_mean=0.3, session="A")
    _write_iqm(mriqc_dir, "02", "rest", 1, fd_mean=0.1, session="B")
    _write_anat(bids_dir, "02", session="A")
    _write_anat(bids_dir, "02", session="B")
    filters_dir = tmp_path / "filters"

    sys.argv = [
        "build_qa_subject_list.py",
        "ds999999",
        str(mriqc_dir),
        "--bids-filters-out",
        str(filters_dir),
        "--bids-dir",
        str(bids_dir),
    ]
    qa.main()

    assert not (filters_dir / "sub-01.json").exists()
    filt = json.loads((filters_dir / "sub-02.json").read_text())
    assert filt["bold"]["session"] == "B"
    assert filt["bold"]["task"] == "rest"


def test_main_requires_bids_dir_with_bids_filters_out(tmp_path, capsys):
    mriqc_dir = tmp_path / "mriqc"
    _write_iqm(mriqc_dir, "01", "rest", 1, fd_mean=0.1, session="A")
    _write_iqm(mriqc_dir, "01", "rest", 1, fd_mean=0.2, session="B")

    sys.argv = [
        "build_qa_subject_list.py",
        "ds999999",
        str(mriqc_dir),
        "--bids-filters-out",
        str(tmp_path / "filters"),
    ]
    with pytest.raises(SystemExit):
        qa.main()

    assert "--bids-dir" in capsys.readouterr().err


def test_bids_filter_pins_anat_to_selected_bold_session_when_present(tmp_path):
    """2026-09-21 postmortem regression: anat must be pinned to a session it
    actually has data in -- preferring the selected BOLD session -- or
    fmriprep's collect_data raises "Conflicting entities for session"."""
    mriqc_dir = tmp_path / "mriqc"
    bids_dir = tmp_path / "bids"
    _write_iqm(mriqc_dir, "01", "rest", 1, fd_mean=0.3, session="01")
    _write_iqm(mriqc_dir, "01", "rest", 1, fd_mean=0.1, session="02")
    _write_anat(bids_dir, "01", session="01")
    _write_anat(bids_dir, "01", session="02")

    sys.argv = [
        "build_qa_subject_list.py",
        "ds999999",
        str(mriqc_dir),
        "--bids-filters-out",
        str(tmp_path / "filters"),
        "--bids-dir",
        str(bids_dir),
    ]
    qa.main()

    filt = json.loads((tmp_path / "filters" / "sub-01.json").read_text())
    assert filt["bold"]["session"] == "02"
    assert filt["t1w"]["session"] == "02"


def test_bids_filter_falls_back_to_the_session_anat_actually_has(tmp_path):
    """The exact ds004592/ds005339 failure mode: T1w only exists in ses-01,
    but the QA-selected (lowest fd_mean) BOLD run is in ses-02 -- the anat
    filter must fall back to ses-01 rather than pointing at a session with
    no T1w at all."""
    mriqc_dir = tmp_path / "mriqc"
    bids_dir = tmp_path / "bids"
    _write_iqm(mriqc_dir, "01", "rest", 1, fd_mean=0.3, session="01")
    _write_iqm(mriqc_dir, "01", "rest", 1, fd_mean=0.1, session="02")
    _write_anat(bids_dir, "01", session="01")  # no anat in ses-02

    sys.argv = [
        "build_qa_subject_list.py",
        "ds999999",
        str(mriqc_dir),
        "--bids-filters-out",
        str(tmp_path / "filters"),
        "--bids-dir",
        str(bids_dir),
    ]
    qa.main()

    filt = json.loads((tmp_path / "filters" / "sub-01.json").read_text())
    assert filt["bold"]["session"] == "02"
    assert filt["t1w"]["session"] == "01"


def test_bids_filter_omits_anat_key_when_subject_has_no_anat_at_all(tmp_path):
    mriqc_dir = tmp_path / "mriqc"
    bids_dir = tmp_path / "bids"
    _write_iqm(mriqc_dir, "01", "rest", 1, fd_mean=0.3, session="01")
    _write_iqm(mriqc_dir, "01", "rest", 1, fd_mean=0.1, session="02")
    bids_dir.mkdir()

    sys.argv = [
        "build_qa_subject_list.py",
        "ds999999",
        str(mriqc_dir),
        "--bids-filters-out",
        str(tmp_path / "filters"),
        "--bids-dir",
        str(bids_dir),
    ]
    qa.main()

    filt = json.loads((tmp_path / "filters" / "sub-01.json").read_text())
    assert "t1w" not in filt
    assert "t2w" not in filt
