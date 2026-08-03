import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_derivatives_conflicts as check_mod


def _run_git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_repo(repo):
    repo.mkdir()
    _run_git(repo, "init", "-q", "-b", "master")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")


def _run_check_script(app_name, repo_path):
    """Execute the generated check-script body directly (no SSH) against a
    real local repo, so the actual git logic is exercised end to end."""
    script = check_mod.build_remote_check_script(app_name, [str(repo_path)])
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def test_parse_ssh_host_and_path_scp_style():
    host, path = check_mod.parse_ssh_host_and_path(
        "datalad-server:/datalad/mri/openneuro/{dataset_id}", "ds000256"
    )
    assert host == "datalad-server"
    assert path == "/datalad/mri/openneuro/ds000256"


def test_parse_ssh_host_and_path_ssh_uri_style():
    host, path = check_mod.parse_ssh_host_and_path(
        "ssh://datalad-server/datalad/mri/openneuro/{dataset_id}/derivatives/mriqc",
        "ds000256",
    )
    assert host == "datalad-server"
    assert path == "/datalad/mri/openneuro/ds000256/derivatives/mriqc"


def test_dataset_ids_handles_plain_and_override_entries():
    datasets = ["ds000031", {"id": "ds000256", "options_extra": ["--use-syn-sdc"]}]
    assert check_mod.dataset_ids(datasets) == ["ds000031", "ds000256"]


def test_build_remote_check_script_references_app_and_path():
    script = check_mod.build_remote_check_script(
        "mriqc", ["/datalad/mri/openneuro/ds000256"]
    )
    assert "/datalad/mri/openneuro/ds000256" in script
    assert "derivatives/mriqc" in script
    assert "git -C" in script  # sanity: quoting didn't collapse the flag


def test_categorize_output_buckets_each_status_line():
    output = (
        "/path/ds1: OK (already a proper subdataset)\n"
        "/path/ds2: CONFLICT (plain content at derivatives/mriqc)\n"
        "/path/ds3: CLEAN (no derivatives/mriqc yet)\n"
        "/path/ds4: MISSING_REPO\n"
    )
    results = check_mod.categorize_output(output)
    assert len(results["OK"]) == 1
    assert len(results["CONFLICT"]) == 1
    assert len(results["CLEAN"]) == 1
    assert len(results["MISSING_REPO"]) == 1
    assert "ds2" in results["CONFLICT"][0]


def test_detects_conflict_on_default_branch_with_no_derivatives_branch_yet(tmp_path):
    """Regression test for the real ds000256/ds007328 incident: plain
    derivatives/mriqc content tracked on master, with no 'derivatives'
    branch created yet, must be reported as CONFLICT (predicted) -- not
    CLEAN just because the 'derivatives' branch itself doesn't exist."""
    repo = tmp_path / "ds000256"
    _init_repo(repo)
    (repo / "derivatives" / "mriqc").mkdir(parents=True)
    (repo / "derivatives" / "mriqc" / "report.html").write_text("<html></html>")
    _run_git(repo, "add", "derivatives/mriqc/report.html")
    _run_git(repo, "commit", "-q", "-m", "OpenNeuro-shipped mriqc derivatives")

    output = _run_check_script("mriqc", repo)

    assert "CONFLICT" in output
    assert "ref=HEAD" not in output  # the $ref var is substituted, not literal
    assert str(repo) in output


def test_reports_clean_when_no_derivatives_content_anywhere(tmp_path):
    repo = tmp_path / "ds999999"
    _init_repo(repo)
    (repo / "README").write_text("just a readme")
    _run_git(repo, "add", "README")
    _run_git(repo, "commit", "-q", "-m", "init")

    output = _run_check_script("mriqc", repo)

    assert "CLEAN" in output


def test_reports_ok_when_already_a_proper_subdataset_on_default_branch(tmp_path):
    outer = tmp_path / "outer"
    inner = tmp_path / "inner_mriqc"
    _init_repo(inner)
    (inner / "report.html").write_text("<html></html>")
    _run_git(inner, "add", "report.html")
    _run_git(inner, "commit", "-q", "-m", "inner commit")

    _init_repo(outer)
    _run_git(
        outer,
        "-c", "protocol.file.allow=always",
        "submodule", "add", str(inner), "derivatives/mriqc",
    )
    _run_git(outer, "commit", "-q", "-m", "register mriqc submodule")

    output = _run_check_script("mriqc", outer)

    assert "OK" in output


def test_uses_derivatives_branch_when_it_already_exists(tmp_path):
    """If a 'derivatives' branch already exists, it -- not HEAD/master --
    is the one that matters, since that's what setup would reuse."""
    repo = tmp_path / "ds_with_branch"
    _init_repo(repo)
    (repo / "README").write_text("raw bids data only")
    _run_git(repo, "add", "README")
    _run_git(repo, "commit", "-q", "-m", "init")

    _run_git(repo, "checkout", "-q", "-b", "derivatives")
    (repo / "derivatives" / "mriqc").mkdir(parents=True)
    (repo / "derivatives" / "mriqc" / "report.html").write_text("<html></html>")
    _run_git(repo, "add", "derivatives/mriqc/report.html")
    _run_git(repo, "commit", "-q", "-m", "plain mriqc content on derivatives branch")
    _run_git(repo, "checkout", "-q", "master")

    output = _run_check_script("mriqc", repo)

    assert "CONFLICT" in output


def _run_removal_script(app_name, repo_path):
    script = check_mod.build_removal_script(app_name, [str(repo_path)])
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, check=True
    )
    return result


def test_path_from_status_line_extracts_path():
    line = "/datalad/mri/openneuro/ds000256: CONFLICT (plain content at derivatives/mriqc on HEAD)"
    assert check_mod._path_from_status_line(line) == "/datalad/mri/openneuro/ds000256"


def test_removal_script_removes_content_with_no_derivatives_branch(tmp_path):
    repo = tmp_path / "ds000256"
    _init_repo(repo)
    (repo / "derivatives" / "mriqc").mkdir(parents=True)
    (repo / "derivatives" / "mriqc" / "report.html").write_text("<html></html>")
    _run_git(repo, "add", "derivatives/mriqc/report.html")
    _run_git(repo, "commit", "-q", "-m", "OpenNeuro-shipped mriqc derivatives")

    _run_removal_script("mriqc", repo)

    assert not (repo / "derivatives" / "mriqc").exists()
    # confirmed by re-running the check script: now CLEAN, not CONFLICT
    assert "CLEAN" in _run_check_script("mriqc", repo)
    # stayed on master -- no branch switch needed in this case
    branch = subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert branch == "master"


def test_removal_script_restores_original_branch_when_derivatives_branch_exists(tmp_path):
    repo = tmp_path / "ds_with_branch"
    _init_repo(repo)
    (repo / "README").write_text("raw bids data only")
    _run_git(repo, "add", "README")
    _run_git(repo, "commit", "-q", "-m", "init")

    _run_git(repo, "checkout", "-q", "-b", "derivatives")
    (repo / "derivatives" / "mriqc").mkdir(parents=True)
    (repo / "derivatives" / "mriqc" / "report.html").write_text("<html></html>")
    _run_git(repo, "add", "derivatives/mriqc/report.html")
    _run_git(repo, "commit", "-q", "-m", "plain mriqc content on derivatives branch")
    _run_git(repo, "checkout", "-q", "master")

    _run_removal_script("mriqc", repo)

    branch = subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert branch == "master"  # restored, not left on 'derivatives'

    entry = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "derivatives", "--", "derivatives/mriqc"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert entry == ""  # actually removed from the derivatives branch


def test_check_config_reports_conflicts(tmp_path, monkeypatch, capsys):
    config = {
        "datasets": ["ds000256", "ds007328", "ds000031"],
        "bids_app": {"app_name": "mriqc"},
        "datalad": {
            "input_url_template": "datalad-server:/datalad/mri/openneuro/{dataset_id}"
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))

    fake_output = (
        "/datalad/mri/openneuro/ds000256: CONFLICT (plain content at derivatives/mriqc)\n"
        "/datalad/mri/openneuro/ds007328: CONFLICT (plain content at derivatives/mriqc)\n"
        "/datalad/mri/openneuro/ds000031: OK (already a proper subdataset)\n"
    )

    def fake_run_remote_script(ssh_host, script, timeout=30):
        assert ssh_host == "datalad-server"
        assert "ds000256" in script
        return fake_output

    monkeypatch.setattr(
        check_mod.prism_datalad, "run_remote_script", fake_run_remote_script
    )

    results = check_mod.check_config(str(config_path))

    assert len(results["CONFLICT"]) == 2
    assert len(results["OK"]) == 1

    captured = capsys.readouterr()
    assert "CONFLICT: 2" in captured.out
    assert "ds000256" in captured.out
