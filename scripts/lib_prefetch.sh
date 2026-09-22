#!/usr/bin/env bash
# lib_prefetch.sh -- keep the cohort prefetch off the HPC login node.
#
# submit_bids_cohort.sh prefetches a cohort's subject data with `datalad
# get` before scheduling its array job, because datalad-slurm keeps all
# git/annex operations outside the job itself. That prefetch used to run
# synchronously in the submitting shell, and the documented workflow is
# to submit from the login node.
#
# Measured 2026-09-22: ds004592 holds 40 GB of annexed content, ds005339
# 52 GB, and git-annex checksums every byte it fetches. Across the
# 26-dataset megastudy that is roughly a terabyte of transfer plus
# hashing on shared login-node hardware -- far more load than the 62-day
# GUI daemon that triggered the admin's second warning.
#
# CLAUDE.md's login-node policy is enforced in prism_local.py's
# execute_local(), but that is Python and this path is bash: it never went
# anywhere near the guard. This library is the bash-side equivalent.
#
# Sourced by submit_bids_cohort.sh; kept as its own file (like
# lib_clone_check.sh) so the dispatch logic can be tested directly --
# see tests/test_lib_prefetch.py.

# Resources for the dispatched prefetch job. It is pure I/O: one core is
# right, and a fat allocation would just queue longer for no benefit.
PREFETCH_CPUS="${PREFETCH_CPUS:-1}"
PREFETCH_MEM="${PREFETCH_MEM:-4G}"
PREFETCH_TIME="${PREFETCH_TIME:-04:00:00}"
PREFETCH_PARTITION="${PREFETCH_PARTITION:-}"
PREFETCH_LOG_DIR="${PREFETCH_LOG_DIR:-/tmp}"

# Resolved with parameter expansion, not dirname/pwd: this library must
# work under a minimal PATH.
_LIB_PREFETCH_SELF="${BASH_SOURCE[0]}"
_LIB_PREFETCH_DIR="${_LIB_PREFETCH_SELF%/*}"
[[ "$_LIB_PREFETCH_DIR" == "$_LIB_PREFETCH_SELF" ]] && _LIB_PREFETCH_DIR="."

# Which datalad the dispatched job should run.
#
# sbatch exports the submitting shell's PATH, and on this cluster the
# first `datalad` on it (~/.local/bin/datalad) works on the login node but
# dies on a compute node with "ModuleNotFoundError: No module named
# 'datalad'" -- compute nodes have a different system python3, the same
# hazard submit_bids_cohort.sh's header documents for .appsrunner. The
# repo's .datalad-slurm-venv lives on shared storage and works on both,
# which is why the finish-job templates already pin DATALAD_BIN to it.
#
# The inline prefetch never hit this because it only ever ran on the login
# node; dispatching it to a compute node is what exposed the broken shim.
prefetch_datalad_bin() {
    if [[ -n "${PREFETCH_DATALAD_BIN:-}" ]]; then
        echo "${PREFETCH_DATALAD_BIN}"
        return 0
    fi
    # ${dir%/*} strips the trailing "scripts" component without needing
    # realpath, keeping this builtin-only.
    local venv="${_LIB_PREFETCH_DIR%/*}/.datalad-slurm-venv/bin/datalad"
    if [[ -x "$venv" ]]; then
        echo "$venv"
        return 0
    fi
    # No venv (a collaborator's workstation): fall back to PATH, which is
    # correct off-cluster where there is no compute/login split.
    echo "datalad"
}

# True when this host belongs to a SLURM cluster but the current process
# has no allocation of its own -- i.e. a bare login/edge node. Mirrors
# prism_local._on_bare_slurm_login_node() exactly, including its choice
# not to shell out to sinfo/scontrol (both can hang under scheduler
# load, and a prefetch that hangs before it starts is worse than useless).
on_bare_slurm_login_node() {
    command -v sbatch >/dev/null 2>&1 || return 1
    [[ -n "${SLURM_JOB_ID:-}" || -n "${SLURM_JOBID:-}" ]] && return 1
    return 0
}

# run_prefetch <label> <command string>
#
# Runs the command on a compute node when called from a login node, and
# inline otherwise (a workstation with no SLURM, or an allocation the
# caller already holds). Returns the command's own exit status either
# way: a silently-swallowed prefetch failure would let the array job
# start against missing input.
run_prefetch() {
    local label="${1:-prefetch}"
    shift || true
    local cmd="$*"

    if [[ -z "${cmd// /}" ]]; then
        echo "run_prefetch: refusing to run an empty command" >&2
        return 2
    fi

    if ! on_bare_slurm_login_node; then
        bash -c "$cmd"
        return $?
    fi

    # --wait blocks until the job finishes and exits with the job's own
    # status, so the caller keeps the ordering guarantee it had when this
    # ran inline: content is present before the array job is scheduled.
    local -a sb=(
        sbatch --wait
        "--job-name=prefetch_${label}"
        "--cpus-per-task=${PREFETCH_CPUS}"
        "--mem=${PREFETCH_MEM}"
        "--time=${PREFETCH_TIME}"
        "--output=${PREFETCH_LOG_DIR}/prefetch_${label}_%j.out"
    )
    [[ -n "$PREFETCH_PARTITION" ]] && sb+=("--partition=${PREFETCH_PARTITION}")

    # Put the repo venv first on PATH inside the job, exactly as the
    # finish-job templates in submit_bids_cohort.sh do. Pinning the datalad
    # binary alone is not enough: datalad shells out to git-annex, and the
    # venv holds the only copy that works on a compute node. Without this
    # the job dies with "No working git-annex installation of version >=
    # 10.20230126".
    local venv_bin="${_LIB_PREFETCH_DIR%/*}/.datalad-slurm-venv/bin"
    local wrapped="$cmd"
    if [[ -d "$venv_bin" ]]; then
        wrapped="export PATH=\"${venv_bin}:\$PATH\"; ${cmd}"
    fi
    sb+=(--wrap "$wrapped")

    echo "[prefetch] login node detected -- dispatching '${label}' to a compute node (see CLAUDE.md)" >&2
    "${sb[@]}"
    return $?
}
