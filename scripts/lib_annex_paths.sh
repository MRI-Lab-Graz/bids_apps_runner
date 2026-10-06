#!/usr/bin/env bash
# Shared by submit_bids_cohort.sh (what to unlock before an array job) and
# annex_cohort_finish.sh (what to commit after it). One definition of "the
# output that belongs to this subject", so the two ends can't drift apart --
# the same producer/consumer-must-agree rule as the pipeline handoffs in
# CLAUDE.md. Filesystem listing only: no git, no git-annex, nothing that
# compares index against worktree (see the annex-slurm design spec).

# annex_entries_for_names DATASET NAME...
# Prints, one per line, the dataset-root entries (relative to DATASET) that
# exist for each NAME: NAME, NAME.* and NAME_*. Never NAME* -- sub-01* would
# also swallow sub-010. That covers FreeSurfer-longitudinal siblings
# (sub-1, sub-1_ses-A, sub-1_ses-A.long.sub-1) and fMRIPrep/MRIQC's
# sub-1.html next to sub-1/.
annex_entries_for_names() {
    local dataset="$1"; shift
    local name entry
    (
        cd "$dataset" || exit 1
        shopt -s nullglob
        for name in "$@"; do
            [[ -n "$name" ]] || continue
            for entry in "$name" "$name".* "$name"_*; do
                [[ -e "$entry" || -L "$entry" ]] && printf '%s\n' "$entry"
            done
        done
    )
}

# annex_root_entries DATASET
# Non-hidden regular files and ANNEXED symlinks (target inside
# .git/annex/objects) directly in the dataset root -- dataset_description.json,
# group reports, ... BIDS apps write these next to the per-subject directories,
# so a per-subject scope alone misses them. Symlinks that point elsewhere are
# deliberately excluded: 134's root holds `fsaverage -> /usr/local/freesurfer/...`,
# a link out of the dataset, which is neither something to unlock nor to commit.
# Assumes output datasets annex everything but dotfiles (no text2git /
# annex.largefiles rule), as every dataset this pipeline creates does.
annex_root_entries() {
    find "$1" -mindepth 1 -maxdepth 1 ! -name '.*' \
        \( -type f -o \( -type l -lname '*.git/annex/objects/*' \) \) -printf '%f\n'
}

# annex_schedule_batch DATASET NAMES_FILE SBATCH_SCRIPT [--root-files] [--extra PATH]...
# The schedule half of the cohort flow: unlock the EXISTING output this
# batch's array will overwrite (the NAMES' entries, optionally the loose
# dataset-root files, plus any --extra paths), then sbatch the script via
# annex-slurm-schedule. Explicit paths only -- never a whole-tree unlock, which
# was the multi-hour vulnerable window in the 134 incident (2026-09-02).
# Prints the SLURM job id on stdout; diagnostics go to stderr; returns 1 on
# any failure. git-annex prints its unlock progress to stdout ahead of
# sbatch's own output, so the job id is the LAST line (sbatch --parsable
# prints "jobid" or "jobid;cluster").
# ANNEX_SLURM_SCHEDULE overrides the tool (tests).
annex_schedule_batch() {
    local dataset="$1" names_file="$2" sbatch_script="$3"; shift 3
    local root_files=false
    local -a extras=() names=() flags=()
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --root-files) root_files=true; shift ;;
            --extra)      extras+=("$2"); shift 2 ;;
            *) echo "annex_schedule_batch: unknown option $1" >&2; return 2 ;;
        esac
    done

    local here tool entry out err_file rc=0 job_id
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    tool="${ANNEX_SLURM_SCHEDULE:-${here%/*}/annex-slurm/bin/annex-slurm-schedule}"

    mapfile -t names < <(sed 's/[[:space:]]//g' "$names_file" | grep -v '^$')
    while IFS= read -r entry; do
        [[ -n "$entry" ]] && flags+=(-o "$entry")
    done < <(
        (( ${#names[@]} )) && annex_entries_for_names "$dataset" "${names[@]}"
        $root_files && annex_root_entries "$dataset"
        true
    )
    for entry in "${extras[@]}"; do flags+=(-o "$entry"); done

    err_file=$(mktemp)
    # The venv's git-annex is the one that works on login AND compute nodes.
    out=$( (
        [[ -d "${here%/*}/.datalad-slurm-venv/bin" ]] && export PATH="${here%/*}/.datalad-slurm-venv/bin:$PATH"
        cd "$dataset" && "$tool" "${flags[@]}" -- --parsable "$sbatch_script"
    ) 2>"$err_file") || rc=$?
    if (( rc != 0 )); then
        echo "annex-slurm-schedule failed (exit ${rc}): $(printf '%s\n%s\n' "$out" "$(cat "$err_file")" | tail -3 | tr '\n' ' ')" >&2
        rm -f "$err_file"
        return 1
    fi
    rm -f "$err_file"

    job_id=$(printf '%s\n' "$out" | tail -n1)
    job_id="${job_id%%;*}"
    if [[ ! "$job_id" =~ ^[0-9]+$ ]]; then
        echo "annex-slurm-schedule: could not read a job id from its output: '${job_id}'" >&2
        return 1
    fi
    printf '%s\n' "$job_id"
}
