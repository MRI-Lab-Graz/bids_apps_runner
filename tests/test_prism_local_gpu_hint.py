import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import prism_local


GLIBC_ERROR = (
    "ImportError: /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_2.34' not found"
)


def test_nv_glibc_hint_logged_when_nv_and_error_present(caplog):
    cmd = ["apptainer", "run", "--nv", "image.sif"]
    with caplog.at_level(logging.ERROR):
        prism_local._maybe_log_nv_glibc_hint(cmd, GLIBC_ERROR)
    assert any("--nvccli" in record.message for record in caplog.records)


def test_nv_glibc_hint_silent_without_nv_flag(caplog):
    cmd = ["apptainer", "run", "image.sif"]
    with caplog.at_level(logging.ERROR):
        prism_local._maybe_log_nv_glibc_hint(cmd, GLIBC_ERROR)
    assert not any("--nvccli" in record.message for record in caplog.records)


def test_nv_glibc_hint_silent_on_unrelated_error(caplog):
    cmd = ["apptainer", "run", "--nv", "image.sif"]
    with caplog.at_level(logging.ERROR):
        prism_local._maybe_log_nv_glibc_hint(cmd, "some other failure")
    assert not any("--nvccli" in record.message for record in caplog.records)
