#!/usr/bin/env bash
# Smoke test: exercises annex-slurm-finish end to end against a throwaway
# repo pair. No HPC/134 dependency -- fails loudly if the plumbing chain
# breaks. Does not test annex-slurm-schedule's sbatch dispatch (needs a
# real scheduler); does exercise its unlock-if-exists logic.
set -euo pipefail
d=$(mktemp -d); trap 'chmod -R u+w "$d" 2>/dev/null; rm -rf "$d"' EXIT
BIN="$(cd "$(dirname "${BASH_SOURCE[0]}")/../bin" && pwd)"

git init -q "$d/remote" >/dev/null
git -C "$d/remote" config receive.denyCurrentBranch updateInstead
git -C "$d/remote" annex init -q remote >/dev/null

git clone -q "$d/remote" "$d/work" >/dev/null
cd "$d/work"
git annex init -q work >/dev/null
branch=$(git symbolic-ref --short HEAD)
echo init > README
git add README
git commit -q -m init
git push -q origin "$branch"

mkdir sub1
echo "segment output" > sub1/result.txt
"$BIN/annex-slurm-finish" -m "test commit" sub1/result.txt

[[ -L sub1/result.txt ]] || { echo "FAIL: not a symlink after finish" >&2; exit 1; }
git -C "$d/remote" log -1 --format=%s "$branch" | grep -q "test commit" \
    || { echo "FAIL: commit not pushed" >&2; exit 1; }
key=$(basename "$(readlink sub1/result.txt)")
git annex whereis --key "$key" | grep -q '\[origin\]' \
    || { echo "FAIL: content not recorded present on remote" >&2; exit 1; }

# The commit must actually CONTAIN the path -- every other check above
# passes on an EMPTY commit. Confirmed real incident (2026-09-22, study
# 134): `git annex fromkey` no-ops silently whenever the working-tree
# symlink already matches the key, which is always true right after setkey,
# so staging into the temp index did nothing, write-tree returned HEAD's own
# tree, and 132 consecutive commits were empty -- while 7721 real output
# files sat staged in the index, unreachable from any clone, and this tool
# reported "OK: ... content-verified" for every one of them.
git ls-tree -r --name-only HEAD | grep -qx 'sub1/result.txt' \
    || { echo "FAIL: committed tree does not contain the path" >&2; exit 1; }
git diff-tree -r --name-only HEAD^ HEAD | grep -qx 'sub1/result.txt' \
    || { echo "FAIL: commit is EMPTY -- the path was not added by it" >&2; exit 1; }

# Re-run finish on a path that is ALREADY a symlink into the annex store
# (idempotent re-run, or a batch mixing already-tracked + new paths).
# Confirmed real incident (2026-09-10): `stat` without -L reports a
# symlink's own size (the target-path string length), not the real content
# size, so this used to fail the size check against contentlocation's real
# size on every already-annexed path.
"$BIN/annex-slurm-finish" -m "re-run on already-symlinked path" sub1/result.txt
[[ -L sub1/result.txt ]] || { echo "FAIL: re-run left path not a symlink" >&2; exit 1; }

# schedule: unlock-if-exists on an already-annexed path, no-op on a new one.
# Shim sbatch -- real dispatch is SLURM's job, not this test's.
mkdir sub2; touch sub2/new.txt  # never annexed -- should be silently skipped
fakebin="$d/fakebin"; mkdir -p "$fakebin"
printf '#!/bin/sh\nexit 0\n' > "$fakebin/sbatch"; chmod +x "$fakebin/sbatch"
PATH="$fakebin:$PATH" "$BIN/annex-slurm-schedule" -o sub1/result.txt -o sub2/new.txt -- ignored
[[ ! -L sub1/result.txt ]] || { echo "FAIL: schedule did not unlock existing path" >&2; exit 1; }

echo "OK: annex-slurm smoke test passed"
