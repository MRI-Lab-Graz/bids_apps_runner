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
# datalad-slurm's own open-job bookkeeping (_open_job_status below) -- a
# cohort simply still mid-run always has local-only state until its finish
# job completes, and reporting that as a "problem" would make this useless
# to actually run unattended (every real cohort would trip it constantly).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECTS_DIR="${REPO_ROOT}/projects"

# shellcheck source=lib_clone_check.sh
source "${REPO_ROOT}/scripts/lib_clone_check.sh"

# Same venv-pinned datalad binary the finish-job scripts use (see
# submit_bids_cohort.sh) -- a plain `datalad` on PATH may be a different
# install without the datalad-slurm extension enabled at all.
DATALAD_SLURM_BIN="${REPO_ROOT}/.datalad-slurm-venv/bin/datalad"
[[ -x "$DATALAD_SLURM_BIN" ]] || DATALAD_SLURM_BIN="datalad"

declare -A SEEN
problems=0
checked=0

# Reads datalad-slurm's own "is there an open job for this dataset" table
# (`--list-open-jobs`, local job-database read -- no network I/O, same
# no-hang guarantee as the rest of this script; 20s timeout is generation
# headroom only, matching the same call's timeout in gui/gui_cohort_routes.py).
# Prints "RUNNING" if a still-open, non-FAILED job exists for this clone,
# "FAILED:<job_id>" if the only open job(s) are dead, or nothing at all if
# the check itself couldn't run. Callers must treat "couldn't tell" as
# "report it anyway" -- silence here is exactly the failure mode this
# script exists to catch, so an inconclusive check must never suppress a
# real problem.
_open_job_status() {
    local path="$1" out
    out=$(cd "$path" 2>/dev/null && timeout 20 "$DATALAD_SLURM_BIN" slurm-finish --list-open-jobs 2>/dev/null) || return 1

    local -a jobs
    mapfile -t jobs < <(printf '%s\n' "$out" | awk 'NF==2 && tolower($1)!="slurm-job-id" {print $1, $2}')
    [[ ${#jobs[@]} -eq 0 ]] && return 1

    local job status
    for job in "${jobs[@]}"; do
        status="${job#* }"
        if [[ "$status" != "FAILED" ]]; then
            echo "RUNNING"
            return 0
        fi
    done
    echo "FAILED:${jobs[0]%% *}"
    return 0
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
        if [[ "$job_status" == FAILED:* ]]; then
            echo "  datalad-slurm job ${job_status#FAILED:} is FAILED -- this is state left behind by a dead job, not one still running"
        fi
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
