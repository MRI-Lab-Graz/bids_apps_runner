"""Real incident (2026-09-28): clone_is_clean's `git status --porcelain`
hangs for hours on a repo with heavy unlocked (materialized, non-symlink)
annexed file counts -- confirmed on ds003849/ds005901/ds000256's raw data
clones (60-67% of a 500-file func/ sample unlocked). clone_is_clean_fast
is a deliberately narrower alternative for exactly that class of repo: it
never calls `git status`, only `git diff-index --cached` (index vs HEAD --
no working-tree read) and a ref/commit-graph comparison for unpushed
commits. It will NOT detect an unstaged modification to an already-
unlocked file (that would require re-hashing content, the exact operation
being avoided) -- an accepted, documented gap, not a bug; see the "does
not catch" test below.

Runs real git commands against real temporary repos -- no mocking, same
policy as this repo's other bash-behavior tests.
"""
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parent.parent / "scripts" / "lib_clone_check.sh"


def _run(func: str, path: Path) -> dict:
    script = f'''
set -u
source "{LIB}"
{func} "{path}"
echo "BROKEN=$CLONE_BROKEN"
echo "BROKEN_MSG=$CLONE_BROKEN_MSG"
echo "UNCOMMITTED=$CLONE_UNCOMMITTED"
echo "UNPUSHED=$CLONE_UNPUSHED"
echo "BRANCH=$CLONE_BRANCH"
'''
    result = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    out = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k] = v
    return out


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t.com")
    _git(path, "config", "user.name", "t")
    (path / "f.txt").write_text("hi\n")
    _git(path, "add", "f.txt")
    _git(path, "commit", "-qm", "init")
    return path


def test_fast_clean_repo_reports_clean(repo):
    out = _run("clone_assess_fast", repo)
    assert out["BROKEN"] == "0"
    assert out["UNCOMMITTED"] == "0"
    assert out["UNPUSHED"] == "0"


def test_fast_detects_staged_uncommitted_changes(repo):
    (repo / "f.txt").write_text("bye\n")
    _git(repo, "add", "f.txt")

    out = _run("clone_assess_fast", repo)

    assert out["UNCOMMITTED"] != "0"


def test_fast_detects_unpushed_commits_against_origin(tmp_path, repo):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "HEAD")

    (repo / "g.txt").write_text("new\n")
    _git(repo, "add", "g.txt")
    _git(repo, "commit", "-qm", "unpushed")

    out = _run("clone_assess_fast", repo)

    assert out["UNPUSHED"] == "1"


def test_fast_reports_clean_when_pushed_and_committed(tmp_path, repo):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "HEAD")

    out = _run("clone_assess_fast", repo)

    assert out["BROKEN"] == "0"
    assert out["UNCOMMITTED"] == "0"
    assert out["UNPUSHED"] == "0"


def test_fast_does_not_catch_unstaged_modification_to_an_unlocked_file(repo):
    """Documents the accepted gap: an unstaged (not `git add`ed) change to a
    working-tree file is exactly what git-annex's unlocked-file smudge/clean
    check would need to re-hash content to detect -- the operation this
    function exists to avoid. clone_is_clean_fast is only safe to use where
    that gap is acceptable (generated-once pipeline output, never hand-
    edited afterward), never as a general clone_is_clean replacement.
    """
    (repo / "f.txt").write_text("modified but never staged\n")

    out = _run("clone_assess_fast", repo)

    assert out["UNCOMMITTED"] == "0", (
        "this is the documented gap, not a regression -- if this starts "
        "failing because UNCOMMITTED become non-zero, clone_assess_fast "
        "started reading the working tree, which reintroduces the exact "
        "hang this function exists to avoid"
    )


def test_fast_flags_detached_or_broken_repo(tmp_path):
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()

    out = _run("clone_assess_fast", not_a_repo)

    assert out["BROKEN"] == "1"


def test_is_clean_fast_wrapper_returns_success_for_clean_repo(repo):
    result = subprocess.run(
        ["bash", "-c", f'source "{LIB}"; clone_is_clean_fast "{repo}"'],
        capture_output=True,
    )
    assert result.returncode == 0


def test_is_clean_fast_wrapper_returns_failure_for_dirty_repo(repo):
    (repo / "f.txt").write_text("bye\n")
    _git(repo, "add", "f.txt")

    result = subprocess.run(
        ["bash", "-c", f'source "{LIB}"; clone_is_clean_fast "{repo}"'],
        capture_output=True,
    )
    assert result.returncode == 1
