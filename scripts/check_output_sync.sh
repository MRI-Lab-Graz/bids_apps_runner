#!/usr/bin/env bash
# check_output_sync.sh
#
# Scans this repo's project output clones (the annex-slurm-managed
# derivative datasets under shared HPC storage) for state that hasn't
# actually made it back to the datalad server: files left uncommitted by
# an interrupted finish job (annex_cohort_finish.sh), or commits sitting
# only in the local clone that were never pushed. Both have caused real
# silent data loss here.
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
# suitable for wiring into cron/a monitor job with alerting on failure --
# cron's own built-in behavior (mail whatever a job prints to stdout) is
# enough, no extra notification plumbing needed, but this script prints
# its "OK" line on every clean run too, so a plain `script.sh` crontab
# entry would mail hourly regardless of outcome. Only surface output on
# an actual failure:
#   MAILTO=you@example.org
#   0 * * * *  /path/to/repo/scripts/check_output_sync.sh >/tmp/cos.out 2>&1 || cat /tmp/cos.out
# (put MAILTO above the entry in the crontab; `cat` only runs -- and only
# then produces the stdout cron mails -- in the `||` branch, i.e. when the
# script itself exited nonzero).
#
# Before reporting a dirty clone as a problem, cross-checks it against
# SLURM's own job queue (_open_job_status below) -- a
# cohort simply still mid-run always has local-only state until its finish
# job completes, and reporting that as a "problem" would make this useless
# to actually run unattended (every real cohort would trip it constantly).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECTS_DIR="${REPO_ROOT}/projects"

# shellcheck source=lib_clone_check.sh
source "${REPO_ROOT}/scripts/lib_clone_check.sh"

declare -A SEEN
problems=0
checked=0

# Is a SLURM job for this clone still live? annex-slurm keeps no bookkeeping
# DB -- squeue is the only job state (the old `slurm-finish --list-open-jobs`
# also hangs on repos with git-annex keys-DB drift). The array job is named
# <app>_<ds>, the finish job finish_<ds>[_batchNN|_subregions...], so a live
# job whose name carries the clone's directory name (the dataset id) as a
# whole `_`-delimited token means "still mid-run". Prints "RUNNING" then, and
# nothing (returns 1) otherwise. Callers must treat "couldn't tell" as
# "report it anyway" -- silence here is exactly the failure mode this script
# exists to catch, so an inconclusive check must never suppress a real
# problem. Purely a local scheduler query: no network I/O, bounded by timeout.
_open_job_status() {
    local ds name state out
    ds=$(basename "$1")
    out=$(timeout 20 squeue -h -u "${USER:-$(id -un)}" -o '%j %T' 2>/dev/null) || return 1
    while read -r name state; do
        [[ -n "$name" ]] || continue
        if [[ "_${name}_" == *"_${ds}_"* ]]; then
            echo "RUNNING"
            return 0
        fi
    done <<< "$out"
    return 1
}

check_clone() {
    local path="$1"
    [[ -d "$path/.git" ]] || return 0
    [[ -n "${SEEN[$path]:-}" ]] && return 0
    SEEN["$path"]=1
    checked=$((checked + 1))

    # clone_assess (lib_clone_check.sh) does the actual git-status/ahead-count
    # work, shared with cleanup_output_data.sh's "safe to drop?" precondition.
    clone_assess "$path"
    if [[ "$CLONE_BROKEN" -eq 1 ]]; then
        problems=$((problems + 1))
        echo "PROBLEM: $path"
        echo "  broken or unreadable git repository: $CLONE_BROKEN_MSG"
        echo
        return 0
    fi

    local uncommitted="$CLONE_UNCOMMITTED" branch="$CLONE_BRANCH" unpushed="$CLONE_UNPUSHED"
    local status_out="$CLONE_STATUS_OUT"

    if [[ "$uncommitted" -gt 0 || "$unpushed" -gt 0 ]]; then
        local job_status=""
        job_status=$(_open_job_status "$path") || job_status=""
        if [[ "$job_status" == "RUNNING" ]]; then
            # A cohort still mid-run genuinely has local-only state until
            # its finish job completes -- not a problem, don't report it.
            return 0
        fi

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
