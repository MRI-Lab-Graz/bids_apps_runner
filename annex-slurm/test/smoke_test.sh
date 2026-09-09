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

# schedule: unlock-if-exists on an already-annexed path, no-op on a new one.
# Shim sbatch -- real dispatch is SLURM's job, not this test's.
mkdir sub2; touch sub2/new.txt  # never annexed -- should be silently skipped
fakebin="$d/fakebin"; mkdir -p "$fakebin"
printf '#!/bin/sh\nexit 0\n' > "$fakebin/sbatch"; chmod +x "$fakebin/sbatch"
PATH="$fakebin:$PATH" "$BIN/annex-slurm-schedule" -o sub1/result.txt -o sub2/new.txt -- ignored
[[ ! -L sub1/result.txt ]] || { echo "FAIL: schedule did not unlock existing path" >&2; exit 1; }

echo "OK: annex-slurm smoke test passed"
