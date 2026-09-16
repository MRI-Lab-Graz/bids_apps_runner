import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_qa_subject_list as qa


def _write_iqm(root, subject, task, run, fd_mean):
    func_dir = root / f"sub-{subject}" / "func"
    func_dir.mkdir(parents=True, exist_ok=True)
    name = f"sub-{subject}_task-{task}_run-{run}_bold.json"
    (func_dir / name).write_text(json.dumps({"fd_mean": fd_mean}))


def test_subject_passes_with_single_low_motion_rest_run(tmp_path):
    _write_iqm(tmp_path, "01", "rest", 1, fd_mean=0.1)

    results = qa.build_qa_report(str(tmp_path), "ds999999", fd_threshold=0.5)

    assert results["sub-01"]["qa_valid"] is True
    assert results["sub-01"]["runs"][0]["passed"] is True


def test_subject_fails_when_fd_mean_exceeds_threshold(tmp_path):
    _write_iqm(tmp_path, "01", "rest", 1, fd_mean=0.8)

    results = qa.build_qa_report(str(tmp_path), "ds999999", fd_threshold=0.5)

    assert results["sub-01"]["qa_valid"] is False
    assert "fd_mean 0.800" in results["sub-01"]["runs"][0]["reason"]


def test_subject_fails_if_any_of_multiple_runs_fails(tmp_path):
    _write_iqm(tmp_path, "01", "rest", 1, fd_mean=0.1)
    _write_iqm(tmp_path, "01", "rest", 2, fd_mean=0.9)

    results = qa.build_qa_report(str(tmp_path), "ds999999", fd_threshold=0.5)

    assert results["sub-01"]["qa_valid"] is False
    assert len(results["sub-01"]["runs"]) == 2


def test_subject_with_no_matching_resting_state_run_is_excluded(tmp_path):
    _write_iqm(tmp_path, "01", "nback", 1, fd_mean=0.1)

    results = qa.build_qa_report(str(tmp_path), "ds999999", fd_threshold=0.5)

    assert results["sub-01"]["qa_valid"] is False
    assert results["sub-01"]["runs"] == []
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
