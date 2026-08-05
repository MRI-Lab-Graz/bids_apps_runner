#!/usr/bin/env bash
# watch_user_jobs.sh
#
# Quick SLURM overview for one user: live squeue state for every array/plain
# job, plus today's terminal-state counts (COMPLETED/FAILED/...) from sacct
# for any array jobs, since squeue alone drops a task the moment it finishes
# (success or failure) and so is blind to failures once they're done.
#
# This is a general "what's running for this user right now" viewer -- for
# per-cohort progress against a specific submit_bids_cohort.sh submission,
# use `submit_bids_cohort.sh status` instead (reads that cohort's own
# submission_*.log and knows subject counts / which finish job goes with
# which array).
#
# Usage:
#   scripts/watch_user_jobs.sh                  # snapshot for $USER, once
#   scripts/watch_user_jobs.sh -u otheruser      # snapshot for another user
#   scripts/watch_user_jobs.sh -w                # auto-refresh every 30s
#   scripts/watch_user_jobs.sh -w -n 10          # auto-refresh every 10s
#   scripts/watch_user_jobs.sh -j 5560071,5560082  # also track specific job
#                                                   # IDs even if they've
#                                                   # already left squeue
set -euo pipefail

USER_NAME="${USER:-$(whoami)}"
WATCH=false
INTERVAL=30
EXTRA_JOBS=()

usage() {
    grep '^#' "$0" | sed '1d;s/^# \{0,1\}//'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -u|--user)     USER_NAME="$2"; shift 2 ;;
        -w|--watch)    WATCH=true; shift ;;
        -n|--interval) INTERVAL="$2"; shift 2 ;;
        -j|--jobs)     IFS=',' read -ra EXTRA_JOBS <<< "$2"; shift 2 ;;
        -h|--help)     usage ;;
        *) echo "Unknown option: $1" >&2; usage ;;
    esac
done

# Today at midnight, for sacct's -S (default lookback varies by site config,
# so pin it explicitly rather than relying on cluster defaults).
TODAY="$(date '+%Y-%m-%dT00:00:00')"

snapshot() {
    echo "=== $(date '+%Y-%m-%d %H:%M:%S') -- jobs for ${USER_NAME} ==="
    echo ""

    # ── Live queue state ────────────────────────────────────────────────
    local live
    live=$(squeue -u "$USER_NAME" -h -o "%i %j %t %M %R" 2>/dev/null)

    if [[ -z "$live" ]]; then
        echo "No jobs currently in the queue."
    else
        printf "%-16s %-22s %-3s %-10s %s\n" "JOBID" "NAME" "ST" "TIME" "REASON/NODE"
        printf "%-16s %-22s %-3s %-10s %s\n" "-----" "----" "--" "----" "-----------"
        echo "$live" | while read -r jobid name st time reason; do
            printf "%-16s %-22s %-3s %-10s %s\n" "$jobid" "$name" "$st" "$time" "$reason"
        done
    fi
    echo ""

    # ── Array jobs: today's terminal-state breakdown ───────────────────
    # Base array job IDs currently visible in squeue (strip "_taskid"),
    # unioned with any --jobs the caller asked to keep tracking even after
    # they've left the live queue.
    local array_bases
    array_bases=$( {
        echo "$live" | awk '{print $1}' | grep '_' | cut -d_ -f1
        printf '%s\n' "${EXTRA_JOBS[@]:-}"
    } | grep -E '^[0-9]+$' | sort -u )

    if [[ -n "$array_bases" ]]; then
        printf "%-14s %-22s %-8s %-30s\n" "ARRAY_JOB" "NAME" "N_TASKS" "TODAY'S STATES"
        printf "%-14s %-22s %-8s %-30s\n" "---------" "----" "-------" "--------------"
        while read -r job_id; do
            [[ -z "$job_id" ]] && continue
            local name states n_tasks failed_note=""
            # scontrol only knows about recently-active jobs -- an array
            # whose tasks all finished a while ago can already be purged
            # from its in-memory state, so this legitimately returns
            # nothing; `|| true` keeps that from tripping `set -e` via
            # pipefail (scontrol's own nonzero exit propagates through the
            # pipe even though grep/head "succeed" on the empty input).
            name=$(scontrol show job "$job_id" 2>/dev/null | grep -oP 'JobName=\K\S+' | head -1) || true
            [[ -z "$name" ]] && name="?"

            states=$(sacct -u "$USER_NAME" -S "$TODAY" --noheader --format=JobID,State --parsable2 2>/dev/null \
                | awk -F'|' -v jid="$job_id" '$1 ~ ("^" jid "_[0-9]+$") {print $2}' \
                | sort | uniq -c | awk '{printf "%s:%s ", $2, $1}' | sed 's/ $//')
            [[ -z "$states" ]] && states="(none today)"

            n_tasks=$(sacct -u "$USER_NAME" -S "$TODAY" --noheader --format=JobID --parsable2 2>/dev/null \
                | awk -F'|' -v jid="$job_id" '$1 ~ ("^" jid "_[0-9]+$")' | wc -l)

            if echo "$states" | grep -q 'FAILED\|TIMEOUT\|OUT_OF_MEMORY\|CANCELLED'; then
                failed_note="  <-- has failures, worth checking"
            fi

            printf "%-14s %-22s %-8s %-30s%s\n" "$job_id" "$name" "$n_tasks" "$states" "$failed_note"
        done <<< "$array_bases"
        echo ""
    fi

    # ── Explicitly-tracked plain jobs no longer in squeue (e.g. a finish
    #    job that already completed) -- report their final state.
    if [[ ${#EXTRA_JOBS[@]} -gt 0 ]]; then
        local gone=()
        for j in "${EXTRA_JOBS[@]}"; do
            echo "$live" | awk '{print $1}' | grep -qx "$j" || gone+=("$j")
        done
        if [[ ${#gone[@]} -gt 0 ]]; then
            echo "-- previously-tracked jobs no longer in queue --"
            for j in "${gone[@]}"; do
                sacct -j "$j" -X --noheader --format=JobID,JobName%25,State,ExitCode,Elapsed 2>/dev/null
            done
            echo ""
        fi
    fi
}

if $WATCH; then
    while true; do
        clear
        snapshot
        echo "(refreshing every ${INTERVAL}s, Ctrl-C to stop)"
        sleep "$INTERVAL"
    done
else
    snapshot
fi
