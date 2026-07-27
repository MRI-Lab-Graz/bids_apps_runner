import sys
import types
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT_DIR / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import fix_dwi_fov_headers as fdh


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def test_normalize_subject_label():
    assert fdh.normalize_subject_label("006") == "sub-006"
    assert fdh.normalize_subject_label("sub-006") == "sub-006"


def test_find_dwi_run_groups_groups_multirun_sessions(tmp_path):
    base = tmp_path / "sub-006" / "ses-1" / "dwi"
    _touch(base / "sub-006_ses-1_acq-multi_run-1_dwi.nii.gz")
    _touch(base / "sub-006_ses-1_acq-multi_run-2_dwi.nii.gz")
    _touch(base / "sub-006_ses-1_acq-multi_run-3_dwi.nii.gz")

    groups = fdh.find_dwi_run_groups(tmp_path)

    assert len(groups) == 1
    (runs,) = groups.values()
    assert [r for r, _ in runs] == [1, 2, 3]


def test_find_dwi_run_groups_skips_single_run(tmp_path):
    base = tmp_path / "sub-053" / "ses-1" / "dwi"
    _touch(base / "sub-053_ses-1_acq-multi_run-1_dwi.nii.gz")

    groups = fdh.find_dwi_run_groups(tmp_path)

    assert groups == {}


def test_find_dwi_run_groups_keeps_sessions_separate(tmp_path):
    for ses in ("ses-1", "ses-2"):
        base = tmp_path / "sub-063" / ses / "dwi"
        _touch(base / f"sub-063_{ses}_acq-multi_run-1_dwi.nii.gz")
        _touch(base / f"sub-063_{ses}_acq-multi_run-2_dwi.nii.gz")

    groups = fdh.find_dwi_run_groups(tmp_path)

    assert len(groups) == 2


def test_find_dwi_run_groups_no_session_level(tmp_path):
    base = tmp_path / "sub-100" / "dwi"
    _touch(base / "sub-100_acq-multi_run-1_dwi.nii.gz")
    _touch(base / "sub-100_acq-multi_run-2_dwi.nii.gz")

    groups = fdh.find_dwi_run_groups(tmp_path)

    assert len(groups) == 1


def test_find_dwi_run_groups_ignores_backup_files(tmp_path):
    # A prior --apply run leaves "<name>.orig" backups next to the fixed
    # files. Those must never be treated as DWI runs themselves -- their
    # run-number-stripped names collide with each other (not with the
    # real group), which previously crashed nib.load() on the ".orig"
    # extension.
    base = tmp_path / "sub-006" / "ses-1" / "dwi"
    _touch(base / "sub-006_ses-1_acq-multi_run-1_dwi.nii.gz")
    _touch(base / "sub-006_ses-1_acq-multi_run-2_dwi.nii.gz")
    _touch(base / "sub-006_ses-1_acq-multi_run-2_dwi.nii.gz.orig")
    _touch(base / "sub-006_ses-1_acq-multi_run-3_dwi.nii.gz")
    _touch(base / "sub-006_ses-1_acq-multi_run-3_dwi.nii.gz.orig")

    groups = fdh.find_dwi_run_groups(tmp_path)

    assert len(groups) == 1
    (runs,) = groups.values()
    assert [r for r, _ in runs] == [1, 2, 3]
    assert all(not str(p).endswith(".orig") for _, p in runs)


def test_find_dwi_run_groups_subject_filter(tmp_path):
    for subj in ("sub-006", "sub-053"):
        base = tmp_path / subj / "ses-1" / "dwi"
        _touch(base / f"{subj}_ses-1_acq-multi_run-1_dwi.nii.gz")
        _touch(base / f"{subj}_ses-1_acq-multi_run-2_dwi.nii.gz")

    groups = fdh.find_dwi_run_groups(tmp_path, subjects=["006"])

    assert len(groups) == 1
    ((path_key, _),) = [(k, v) for k, v in groups.items()]
    assert "sub-006" in path_key


def test_parse_args_defaults():
    args = fdh.parse_args(["--bids-dir", "/some/path"])

    assert args.bids_dir == "/some/path"
    assert args.apply is False
    assert args.tolerance_mm == 0.05
    assert args.backup_suffix == ".orig"
    assert args.subjects is None


def test_main_reports_missing_nibabel_gracefully(monkeypatch, tmp_path, capsys):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name in ("nibabel", "numpy"):
            raise ImportError(f"No module named '{name}'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    rc = fdh.main(["--bids-dir", str(tmp_path)])

    assert rc == 1
    assert "nibabel/numpy not available" in capsys.readouterr().err


def test_main_no_groups_found(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "nibabel", types.ModuleType("nibabel"))
    monkeypatch.setitem(sys.modules, "numpy", types.ModuleType("numpy"))

    rc = fdh.main(["--bids-dir", str(tmp_path)])

    assert rc == 0
    assert "No multi-run DWI groups found" in capsys.readouterr().out
