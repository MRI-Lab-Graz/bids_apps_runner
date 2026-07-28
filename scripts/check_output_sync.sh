#!/usr/bin/env bash
# check_output_sync.sh
#
# Scans this repo's project output clones (the datalad-slurm-managed
# derivative datasets under shared HPC storage) for state that hasn't
# actually made it back to the datalad server: files left uncommitted by
# an interrupted `datalad slurm-finish`, or commits sitting only in the
# local clone that were never pushed. Both have caused real silent data
# loss here -- see scripts/patches/README.md for the finish_cmd bug this
# is a safety net for.
#
# Deliberately does no network I/O (no `git fetch`, no `datalad push`):
# it only compares HEAD against each repo's last-known
# refs/remotes/origin/<branch> ref, which datalad/git already update
# locally as a side effect of the last successful push. A monitoring tool
# that can itself hang on the network is not a monitoring tool -- see the
# stale-SSH-multiplex-socket incident this script's sibling fixes are for.
#
# Usage: scripts/check_output_sync.sh
# Exit code is nonzero if any clone has pending state, so this is
# suitable for wiring into cron/a monitor job with alerting on failure.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECTS_DIR="${REPO_ROOT}/projects"

declare -A SEEN
problems=0
checked=0

check_clone() {
    local path="$1"
    [[ -d "$path/.git" ]] || return 0
    [[ -n "${SEEN[$path]:-}" ]] && return 0
    SEEN["$path"]=1
    checked=$((checked + 1))

    # Capture git's stderr rather than discarding it: a `.git` dir that
    # exists but is broken/incomplete (seen in the wild -- a stub with no
    # refs/objects) makes `git status` itself fail, and silently
    # discarding that failure reads as "0 uncommitted changes", the worst
    # possible false negative for a script whose whole job is catching
    # desync.
    local status_out
    if ! status_out=$(git -C "$path" status --porcelain 2>&1); then
        problems=$((problems + 1))
        echo "PROBLEM: $path"
        echo "  broken or unreadable git repository: $(printf '%s' "$status_out" | head -1)"
        echo
        return 0
    fi

    local uncommitted branch unpushed=0
    uncommitted=$(printf '%s\n' "$status_out" | grep -c '.' || true)
    branch=$(git -C "$path" symbolic-ref --short -q HEAD 2>/dev/null || echo "(detached)")

    if git -C "$path" rev-parse --verify -q "refs/remotes/origin/${branch}" >/dev/null 2>&1; then
        unpushed=$(git -C "$path" rev-list --count "origin/${branch}..HEAD" 2>/dev/null || echo 0)
    fi

    if [[ "$uncommitted" -gt 0 || "$unpushed" -gt 0 ]]; then
        problems=$((problems + 1))
        echo "PROBLEM: $path"
        echo "  branch: $branch"
        [[ "$uncommitted" -gt 0 ]] && echo "  uncommitted paths: $uncommitted"
        [[ "$unpushed" -gt 0 ]] && echo "  commits not yet pushed to origin/${branch}: $unpushed"
        if [[ "$uncommitted" -gt 0 ]]; then
            printf '%s\n' "$status_out" | head -5 | sed 's/^/    /'
        fi
        echo
    fi
}

# 1. Every configured project's primary output folder
for pj in "$PROJECTS_DIR"/*/project.json; do
    [[ -s "$pj" ]] || continue
    out_folder=$(jq -r '.config.common.output_folder // empty' "$pj" 2>/dev/null)
    out_root=$(jq -r '.config.common.pipeline_output_root // empty' "$pj" 2>/dev/null)
    [[ -n "$out_folder" ]] && check_clone "$out_folder"
    # 2. Sibling output clones under the same pipeline_output_root -- e.g. a
    #    FreeSurfer/subregion follow-up that shares a project's derivatives/
    #    dir but isn't itself that project's own configured output_folder.
    if [[ -n "$out_root" && -d "$out_root" ]]; then
        for sub in "$out_root"/*/; do
            [[ -d "${sub}.git" ]] && check_clone "${sub%/}"
        done
    fi
done

if [[ "$problems" -eq 0 ]]; then
    echo "OK: no pending uncommitted/unpushed state found in ${checked} output clone(s) checked."
    exit 0
else
    echo "Found pending state in ${problems} output clone(s) out of ${checked} checked."
    exit 1
fi
