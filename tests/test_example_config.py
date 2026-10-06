"""configs/cohort_hpc_example.json is the file new users copy.

It used to hold one person's /usr/people paths (home filesystem: no quota for
bulk data -- CLAUDE.md) and one lab's server, and `configs/` was gitignored so
it never reached anyone else anyway."""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "configs" / "cohort_hpc_example.json"
BULK_KEYS = ("shared_input_base", "shared_output_base", "scratch_dir", "log_dir", "subject_lists_dir")


def test_the_example_is_not_ignored_by_git():
    out = subprocess.run(["git", "check-ignore", str(EXAMPLE)], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 1, f"still gitignored: {out.stdout}"


def test_bulk_data_paths_are_under_cl_tmp_never_the_home_filesystem():
    paths = json.loads(EXAMPLE.read_text())["paths"]
    for key in BULK_KEYS:
        assert paths[key].startswith("/cl_tmp/"), f"{key}={paths[key]}"
        assert "/usr/people" not in paths[key]


def test_nothing_names_a_particular_lab_or_person():
    text = EXAMPLE.read_text()
    for needle in ("mrilab", "MRI-Lab", "datalad-server", "/usr/people"):
        assert needle not in text, needle


def test_it_must_be_edited_before_it_can_run():
    # submit_bids_cohort.sh refuses any config still containing "TODO...".
    assert '"TODO' in EXAMPLE.read_text()
