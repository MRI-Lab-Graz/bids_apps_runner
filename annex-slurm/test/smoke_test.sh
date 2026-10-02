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

# The remote must learn WHERE the content is, not just receive the bytes:
# setpresentkey writes the location log to the local git-annex branch, so
# that branch has to be pushed too or the server holds objects it does not
# know it has.
[[ "$(git rev-parse git-annex)" == "$(git -C "$d/remote" rev-parse git-annex)" ]] \
    || { echo "FAIL: git-annex branch not pushed to remote" >&2; exit 1; }
git -C "$d/remote" annex whereis --key "$key" | grep -q 'here' \
    || { echo "FAIL: remote does not know it holds the content" >&2; exit 1; }

# Integrity: a remote object with the right SIZE but wrong CONTENT must be
# rejected. rsync -a skips files whose size+mtime match, so a same-size
# corrupt object (stale partial transfer) can survive the copy, and a
# size-only check would pass it and record it as present. rsync is shimmed
# to skip the copy so this is deterministic (a real skip depends on mtimes).
local_obj=$(readlink -f sub1/result.txt)
robj="$d/remote/$(realpath --relative-to="$PWD" "$local_obj")"
touch -r "$robj" "$d/mtime.ref"
chmod u+w "$(dirname "$robj")" "$robj"
osize=$(stat -c%s "$robj")  # before the redirect below truncates it
head -c "$osize" /dev/zero | tr '\0' 'X' > "$robj"
touch -r "$d/mtime.ref" "$robj"
skipbin="$d/skipbin"; mkdir -p "$skipbin"
printf '#!/bin/sh\nexit 0\n' > "$skipbin/rsync"; chmod +x "$skipbin/rsync"
if PATH="$skipbin:$PATH" "$BIN/annex-slurm-finish" -m "corrupt remote object" sub1/result.txt 2>"$d/err"; then
    echo "FAIL: finish accepted a same-size corrupt remote object" >&2; exit 1
fi
grep -q "CHECKSUM MISMATCH" "$d/err" \
    || { echo "FAIL: corrupt object rejected for the wrong reason:" >&2; cat "$d/err" >&2; exit 1; }
chmod u+w "$(dirname "$robj")" "$robj"  # rsync -a re-applied the read-only mode
cat "$local_obj" > "$robj"; touch -r "$d/mtime.ref" "$robj"  # repair for the rest

# Serialization: finish does read-HEAD -> commit-tree -> update-ref, so two
# at once on one repo would build from the same HEAD and one commit would
# drop the other's paths. A held lock must block (and time out loudly)...
lock="$(cd "$(git rev-parse --git-common-dir)" && pwd)/annex-slurm-finish.lock"
flock "$lock" sleep 4 &
holder=$!
sleep 1
mkdir -p sub3; echo three > sub3/a.txt
if ANNEX_SLURM_LOCK_TIMEOUT=1 "$BIN/annex-slurm-finish" -m "should not run" sub3/a.txt 2>"$d/err"; then
    echo "FAIL: finish ran while the lock was held" >&2; exit 1
fi
grep -q "lock" "$d/err" || { echo "FAIL: lock timeout not reported" >&2; cat "$d/err" >&2; exit 1; }
wait "$holder"
# ...and two concurrent finishes must BOTH land in history.
echo four > sub3/b.txt; echo five > sub3/c.txt
"$BIN/annex-slurm-finish" -m "concurrent b" sub3/b.txt & p1=$!
"$BIN/annex-slurm-finish" -m "concurrent c" sub3/c.txt & p2=$!
wait "$p1" && wait "$p2" || { echo "FAIL: a concurrent finish errored" >&2; exit 1; }
for f in sub3/b.txt sub3/c.txt; do
    git ls-tree -r --name-only HEAD | grep -qx "$f" \
        || { echo "FAIL: concurrent finish lost $f from history" >&2; exit 1; }
done

# Datasets made by `datalad create` -- every dataset on the server -- use
# annex.backend=MD5E, not SHA256E. The integrity check must cover that backend
# too, or it silently degrades to size-only for exactly the datasets this tool
# is used on.
git config annex.backend MD5E
mkdir -p sub4; echo md5content > sub4/m.txt
"$BIN/annex-slurm-finish" -m "md5e file" sub4/m.txt
case "$(basename "$(readlink sub4/m.txt)")" in
    MD5E-*) ;;
    *) echo "FAIL: fixture did not produce an MD5E key" >&2; exit 1 ;;
esac
mobj=$(readlink -f sub4/m.txt)
mrobj="$d/remote/$(realpath --relative-to="$PWD" "$mobj")"
chmod u+w "$(dirname "$mrobj")" "$mrobj"
msize=$(stat -c%s "$mrobj")
head -c "$msize" /dev/zero | tr '\0' 'X' > "$mrobj"
if PATH="$skipbin:$PATH" "$BIN/annex-slurm-finish" -m "corrupt md5e remote object" sub4/m.txt 2>"$d/err"; then
    echo "FAIL: finish accepted a same-size corrupt MD5E remote object" >&2; exit 1
fi
grep -q "CHECKSUM MISMATCH" "$d/err" \
    || { echo "FAIL: MD5E corruption not caught by checksum:" >&2; cat "$d/err" >&2; exit 1; }
grep -q "size only" "$d/err" && { echo "FAIL: MD5E fell back to a size-only check" >&2; exit 1; }
cat "$mobj" > "$mrobj"  # repair
git config --unset annex.backend

# schedule: unlock-if-exists on an already-annexed path, no-op on a new one.
# Shim sbatch -- real dispatch is SLURM's job, not this test's.
mkdir sub2; touch sub2/new.txt  # never annexed -- should be silently skipped
fakebin="$d/fakebin"; mkdir -p "$fakebin"
printf '#!/bin/sh\nexit 0\n' > "$fakebin/sbatch"; chmod +x "$fakebin/sbatch"
PATH="$fakebin:$PATH" "$BIN/annex-slurm-schedule" -o sub1/result.txt -o sub2/new.txt -- ignored
[[ ! -L sub1/result.txt ]] || { echo "FAIL: schedule did not unlock existing path" >&2; exit 1; }

echo "OK: annex-slurm smoke test passed"
