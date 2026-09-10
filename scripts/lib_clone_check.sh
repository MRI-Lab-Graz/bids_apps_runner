# lib_clone_check.sh -- shared "is this datalad/git-annex clone safe to
# touch" logic for check_output_sync.sh and cleanup_output_data.sh.
#
# Deliberately does no network I/O (no `git fetch`, no `datalad`/`ssh`
# calls): it only compares HEAD against each repo's last-known
# refs/remotes/origin/<branch> ref, which datalad/git already update
# locally as a side effect of the last successful push. Source this file,
# then call clone_assess/clone_is_clean -- do not execute it directly.
#
# Prepend the repo's pinned, known-good git-annex to PATH before the user's
# own PATH is consulted. `git status` on an annexed repo can shell out to
# `git-annex` internally (e.g. to re-smudge a pointer file when its cached
# stat info looks stale -- observed to trigger inconsistently between a
# login-node shell and a fresh sbatch/srun job environment, likely due to
# differing NFS attribute-cache state). On this host `~/.local/bin/git-annex`
# is a broken shim from an errant `uv tool install git-annex` (a same-named
# but unrelated PyPI package, not the real tool) that crashes with
# `ModuleNotFoundError: No module named 'git_annex'` -- and `~/.local/bin`
# sorts ahead of the real git-annex in a bare job PATH, so `git status`
# silently reports every repo it touches as "broken" from inside a batch
# job. Without this, clone_assess() fails closed (safe -- it never causes
# an incorrect drop) but also never finds anything to report or drop,
# making the whole tool a no-op precisely in the sbatch/salloc context
# CLAUDE.md's login-node policy requires --live to run from.
_LIB_CLONE_CHECK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -x "${_LIB_CLONE_CHECK_DIR}/.datalad-slurm-venv/bin/git-annex" ]]; then
    PATH="${_LIB_CLONE_CHECK_DIR}/.datalad-slurm-venv/bin:${PATH}"
fi

# jq lives at ~/.local/bin/jq (a user-local install), which is absent from
# cron's minimal PATH -- confirmed real incident (2026-09-08): both
# check_output_sync.sh and cleanup_output_data.sh's `jq` calls silently
# resolved to nothing under cron for a full month (Aug 8 - Sep 8), so every
# discovery loop that depends on jq's output found zero clones and every
# run logged a false "OK: 0 output clone(s) checked" / "nothing to do"
# instead of erroring -- the exact same failure shape as the git-annex shim
# above (fails closed, silently, rather than loudly). check_output_sync.sh
# exists specifically to catch silent data-loss risk; this bug made it
# blind to that risk for a month while still reporting green.
if [[ -x "${HOME}/.local/bin/jq" ]]; then
    PATH="${HOME}/.local/bin:${PATH}"
fi

# `git status` on a git-annex repo can end up doing a full annexed-object
# diff scan (via `git-annex filter-process`) when it decides a pointer
# file's cached stat info looks stale. On a repo with a very large file
# count (observed on a FreeSurfer derivatives clone here) that scan can run
# for hours over a networked filesystem -- with no way to tell "still
# working" apart from "hung" from the outside. Bound it: a clone that can't
# be assessed within this many seconds is treated as CLONE_BROKEN (fail
# closed, same as an actually-broken repo) rather than stalling every other
# clone behind it. Override via CLONE_CHECK_TIMEOUT_SECS in the environment.
: "${CLONE_CHECK_TIMEOUT_SECS:=300}"

# Sets on every call, even when the repo is broken:
#   CLONE_BROKEN       1 if `git status` failed or timed out (bad/incomplete
#                       repo, or exceeded CLONE_CHECK_TIMEOUT_SECS), else 0
#   CLONE_BROKEN_MSG   first line of git's stderr, only set if CLONE_BROKEN=1
#   CLONE_UNCOMMITTED  count of `git status --porcelain` lines
#   CLONE_UNPUSHED     commits ahead of origin/<branch> (0 if no such ref)
#   CLONE_BRANCH       current branch name, or "(detached)"
#   CLONE_STATUS_OUT   raw `git status --porcelain` output
#
# Always returns 0 -- "dirty" is a data condition for callers to inspect
# via the vars above, not a shell-error condition.
clone_assess() {
    local path="$1" rc
    CLONE_BROKEN=0
    CLONE_BROKEN_MSG=""
    CLONE_UNCOMMITTED=0
    CLONE_UNPUSHED=0
    CLONE_BRANCH=""
    CLONE_STATUS_OUT=""

    # Repos managed by annex-slurm (see docs/superpowers/specs/2026-09-09-
    # annex-slurm-design.md) are known to hang `git status` indefinitely due
    # to git-annex keys-DB reconciliation drift -- confirmed real incident
    # (2026-09-10): dataset 134's derivatives repo, hit by this exact cron
    # call, left an orphaned `git-annex filter-process` behind almost every
    # hourly run (the timeout below kills the `git status` it started, but
    # not that detached helper -- git-annex intentionally runs it in its own
    # session so it survives parent death). Orphans accumulated for over a
    # day and their held lock then blocked unrelated annex-slurm-finish
    # calls on the same repo. Skip the hang-prone call entirely rather than
    # trying to out-signal a subprocess that's designed to detach; fails
    # closed exactly like a timeout would (never causes an incorrect drop).
    if [[ -f "$path/.annex-slurm-managed" ]]; then
        CLONE_BROKEN=1
        CLONE_BROKEN_MSG="skipped: annex-slurm-managed repo, git status known to hang (see .annex-slurm-managed)"
        return 0
    fi

    # Capture git's stderr rather than discarding it: a `.git` dir that
    # exists but is broken/incomplete (a stub with no refs/objects) makes
    # `git status` itself fail, and silently discarding that failure reads
    # as "0 uncommitted changes" -- the worst possible false negative for
    # code deciding whether it's safe to drop local content.
    CLONE_STATUS_OUT=$(timeout --kill-after=10 "$CLONE_CHECK_TIMEOUT_SECS" git -C "$path" status --porcelain 2>&1)
    rc=$?
    if [[ "$rc" -eq 124 || "$rc" -eq 137 ]]; then
        CLONE_BROKEN=1
        CLONE_BROKEN_MSG="git status timed out after ${CLONE_CHECK_TIMEOUT_SECS}s (large/slow repo?)"
        return 0
    elif [[ "$rc" -ne 0 ]]; then
        CLONE_BROKEN=1
        CLONE_BROKEN_MSG=$(printf '%s' "$CLONE_STATUS_OUT" | head -1)
        return 0
    fi

    CLONE_UNCOMMITTED=$(printf '%s\n' "$CLONE_STATUS_OUT" | grep -c '.' || true)
    CLONE_BRANCH=$(git -C "$path" symbolic-ref --short -q HEAD 2>/dev/null || echo "(detached)")

    if git -C "$path" rev-parse --verify -q "refs/remotes/origin/${CLONE_BRANCH}" >/dev/null 2>&1; then
        CLONE_UNPUSHED=$(git -C "$path" rev-list --count "origin/${CLONE_BRANCH}..HEAD" 2>/dev/null || echo 0)
    fi
    return 0
}

# Convenience wrapper: 0 (clean, safe to touch) if not broken and no
# uncommitted/unpushed state; 1 otherwise. Populates the same CLONE_* vars
# as clone_assess for callers that want to log why.
clone_is_clean() {
    local path="$1"
    clone_assess "$path"
    [[ "$CLONE_BROKEN" -eq 0 && "$CLONE_UNCOMMITTED" -eq 0 && "$CLONE_UNPUSHED" -eq 0 ]]
}
