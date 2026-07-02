import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import app_profiles
from app_profiles import CATALOG, resolve_app_name, resolve_app_profile


def test_mriqc_resolves_with_nprocs_flags_and_no_sub():
    profile = resolve_app_profile({"pipeline_app_name": "mriqc"}, {})

    assert profile["supports_nipreps_resource_flags"] is True
    assert "--no-sub" in profile["auto_options"]


def test_unknown_container_resolves_to_default_profile():
    name = resolve_app_name({}, {}, container_ref="/path/to/my_custom_tool_1.0.sif")
    profile = resolve_app_profile({}, {}, container_ref="/path/to/my_custom_tool_1.0.sif")

    assert name == ""
    assert profile["supports_nipreps_resource_flags"] is False
    assert profile["auto_options"] == []


def test_container_sniffing_uses_precise_matching_not_loose_substring():
    # A container name that merely *contains* "fastsurfer" as a substring in
    # a non-prefix position must NOT match -- only a docker tag/ref or a
    # filename that actually starts with the app name should.
    assert resolve_app_name({}, {}, container_ref="/path/to/notfastsurfer_1.0.sif") == ""
    assert resolve_app_name({}, {}, container_ref="/path/to/fastsurfer_3.0.sif") == "fastsurfer"
    assert resolve_app_name({}, {}, container_ref="somewhere/fastsurfer:latest") == "fastsurfer"


def test_app_profile_overrides_patches_single_field_only():
    profile = resolve_app_profile(
        {"pipeline_app_name": "mriqc"},
        {"app_profile_overrides": {"supports_nipreps_resource_flags": False}},
    )

    assert profile["supports_nipreps_resource_flags"] is False
    # Untouched fields must survive the override.
    assert "--no-sub" in profile["auto_options"]
    assert profile["display_name"] == "MRIQC"


def test_explicit_app_profile_beats_pipeline_app_name_and_sniffing():
    name = resolve_app_name(
        {"pipeline_app_name": "fmriprep"},
        {"app_profile": "mriqc"},
        container_ref="/path/to/qsiprep_1.0.sif",
    )
    assert name == "mriqc"


def test_pipeline_app_name_beats_container_sniffing():
    name = resolve_app_name(
        {"pipeline_app_name": "fmriprep"},
        {},
        container_ref="/path/to/mriqc_24.0.2.sif",
    )
    assert name == "fmriprep"


def test_fastsurfer_execution_adapter_aliases():
    aliases = CATALOG["fastsurfer"]["execution_adapter_aliases"]
    assert aliases["fastsurfer"] == "fastsurfer-cross"
    assert aliases["fastsurfer-cross"] == "fastsurfer-cross"
    assert aliases["bids-fastsurfer"] == "fastsurfer-cross"


def test_qsiprep_resolves_for_all_three_container_ref_shapes():
    for ref in ("pennlinc/qsiprep:1.1.1", "qsiprep:latest", "/x/qsiprep_1.1.1.sif"):
        assert resolve_app_name({}, {}, container_ref=ref) == "qsiprep"


def test_qsirecon_resolves_for_all_three_container_ref_shapes():
    for ref in ("pennlinc/qsirecon:1.1.1", "qsirecon:latest", "/x/qsirecon_1.1.1.sif"):
        assert resolve_app_name({}, {}, container_ref=ref) == "qsirecon"


def test_qsirecon_has_longer_completion_wait_than_default():
    qsirecon_profile = resolve_app_profile({"pipeline_app_name": "qsirecon"}, {})
    default_profile = resolve_app_profile({}, {}, container_ref="/x/custom.sif")

    assert qsirecon_profile["completion_wait_seconds"] == 300
    assert default_profile["completion_wait_seconds"] == 90


def test_fmriprep_cannot_self_fetch_datalad_but_mriqc_can():
    assert CATALOG["mriqc"]["supports_datalad_self_fetch"] is True
    assert CATALOG["fmriprep"]["supports_datalad_self_fetch"] is False


def test_container_matches_app_precise_matching():
    assert app_profiles.container_matches_app("/x/mriqc_24.0.2.sif", "mriqc") is True
    assert app_profiles.container_matches_app("mriqc:latest", "mriqc") is True
    assert app_profiles.container_matches_app("nipreps/mriqc:24.0.2", "mriqc") is True
    assert app_profiles.container_matches_app("/x/notmriqc_1.0.sif", "mriqc") is False
    assert app_profiles.container_matches_app("", "mriqc") is False
