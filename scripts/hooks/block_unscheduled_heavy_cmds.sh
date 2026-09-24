#!/usr/bin/env bash
# PreToolUse(Bash) hook: hard-block heavy work against the big annex datasets
# unless it goes through SLURM.
#
# Why this is code and not another line in CLAUDE.md: the login-node policy was
# ALREADY written in CLAUDE.md, and was still violated three times in one
# session (2026-09-22) -- a `git status` on IT010128 that burned 5 minutes of
# CPU and left orphaned git-annex helper processes, a recovery job left hung for
# 4+ hours holding 4 CPUs, and ~10 one-off `srun` probes where one batched job
# would have done. A rule that only gets read is not a rule that gets enforced.
# Same reasoning as the commit gate in annex-slurm/bin/annex-slurm-finish:
# turn the lesson into a refusal.
#
# Contract: PreToolUse JSON payload on stdin; a permission decision on stdout.
# Printing nothing and exiting 0 means "no opinion", and the command proceeds.
#
# KNOWN LIMIT: only the command string is inspected, so a bare `git status`
# whose heaviness comes from an inherited working directory is not caught --
# only commands that name a dataset path (`cd /cl_tmp/... && git status`,
# `git -C /cl_tmp/... status`). That covers how these commands actually get
# written here, but it is a guardrail against carelessness, not a sandbox.
#
# Test: scripts/hooks/test_block_unscheduled_heavy_cmds.sh
set -uo pipefail

payload=$(cat)
cmd=$(printf '%s' "$payload" | jq -r '.tool_input.command // empty' 2>/dev/null)
[[ -z "$cmd" ]] && exit 0

deny() {
    jq -n --arg reason "$1" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: $reason
        }
    }'
    exit 0
}

HOWTO='Submit it instead: `sbatch <script>`, or `srun --partition=hpc --time=... --mem=... <cmd>` for a short check. See the HPC login node policy in CLAUDE.md.'

# Inside a real allocation the entire concern is moot.
[[ -n "${SLURM_JOB_ID:-}${SLURM_JOBID:-}" ]] && exit 0

# Already routed through the scheduler: that IS the sanctioned path. Leading
# env assignments and `cd ... &&` prefixes are tolerated.
if printf '%s' "$cmd" | grep -Eq '(^|[;&|][[:space:]]*)([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*(env[[:space:]]+[^;&|]*)?(srun|sbatch|salloc)[[:space:]]'; then
    exit 0
fi

# Help/version probes read no data.
if printf '%s' "$cmd" | grep -Eq -- '(--help|--version)([[:space:]]|$)'; then
    exit 0
fi

DATASET_PATH='(/cl_tmp/|/datalad/)'

# (1) Container execution is real compute wherever it points.
if printf '%s' "$cmd" | grep -Eq '\b(apptainer|singularity)[[:space:]]+(exec|run|shell)\b|\bdocker[[:space:]]+run\b'; then
    deny "BLOCKED: runs a container, which must never execute on the login node. ${HOWTO}"
fi

# (2) datalad always moves or hashes data.
if printf '%s' "$cmd" | grep -Eq '\bdatalad[[:space:]]'; then
    deny "BLOCKED: \`datalad\` does real data movement (get/push/save all hash or transfer content). ${HOWTO}"
fi

# (3) Heavy git / filesystem traversal aimed at one of the big datasets.
if printf '%s' "$cmd" | grep -Eq "$DATASET_PATH"; then
    # Worktree-comparing and data-moving git subcommands. Object-graph reads
    # (log, show, ls-tree, rev-parse, cat-file, diff-tree, diff-index,
    # ls-files, for-each-ref) are deliberately NOT here: on 2026-09-22 those
    # all returned in seconds on this dataset, while `git status` never
    # finished. The distinction is "does it have to look at the working tree".
    # The subcommand must stand alone as its own word: \b would also match
    # inside a flag, so `git diff-tree --no-commit-id` was read as a `commit`
    # (caught by test_block_unscheduled_heavy_cmds.sh). Requiring whitespace on
    # the left and whitespace-or-end on the right keeps flags out.
    if printf '%s' "$cmd" | grep -Eq '\bgit\b[^;&|]*[[:space:]](status|add|commit|checkout|reset|stash|clean|fsck|gc|annex|push|pull|fetch|clone|worktree)([[:space:]]|$)'; then
        deny "BLOCKED: this git subcommand reads the working tree or moves content on a large annex dataset. ~95k of its files are unlocked, so git must hash tens of GB through the annex clean filter -- a scoped \`git status\` on ONE subject sat at 0% CPU for 4+ hours. ${HOWTO}"
    fi
    # `git diff` (worktree) is heavy; diff-index/diff-tree are not, and \b
    # would match inside them, so require whitespace or end after "diff".
    if printf '%s' "$cmd" | grep -Eq '\bgit\b[^;&|]*[[:space:]]diff([[:space:]]|$)'; then
        deny "BLOCKED: \`git diff\` compares against the working tree on a large annex dataset. Use \`git diff-index --cached\` / \`git diff-tree\` for object-graph comparisons, or ${HOWTO}"
    fi
    if printf '%s' "$cmd" | grep -Eq '\b(find|rsync|du)[[:space:]]'; then
        deny "BLOCKED: filesystem traversal or transfer over a large dataset on shared NFS. ${HOWTO}"
    fi
fi

exit 0
