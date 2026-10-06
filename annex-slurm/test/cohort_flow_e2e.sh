#!/bin/bash
#SBATCH --job-name=annex_cohort_e2e
#SBATCH --partition=hpc
#SBATCH --time=00:20:00
#SBATCH --mem=2G
#SBATCH --cpus-per-task=1
#SBATCH --output=/cl_tmp/mrilabgraz/annex_e2e_logs/e2e-%j.out
#
# End-to-end test of the cohort flow on annex-slurm, under a REAL scheduler:
#
#   previous run's annexed output  ->  annex_schedule_batch (unlock + sbatch)
#   ->  array job overwrites it    ->  dependent finish job
#   ->  annex_cohort_finish.sh     ->  everything committed, pushed, verified
#
# Why this exists as well as the unit tests (tests/test_annex_*.py): both
# real bugs found while building annex-slurm only showed up under sbatch
# (compute-node-invisible paths; consuming operations needing "before" state
# captured in the same pass). The unit tests stub the scheduler and git-annex;
# this one does not.
#
# Disposable: builds a throwaway remote+clone under /cl_tmp/mrilabgraz, never
# touches a real dataset, removes everything on exit (KEEP=1 to inspect).
#
#   mkdir -p /cl_tmp/mrilabgraz/annex_e2e_logs
#   sbatch annex-slurm/test/cohort_flow_e2e.sh
# Exit status 0 + "PASS" on the last line of the log means it all held.
set -euo pipefail

REPO="${REPO_DIR:-/usr/people/mrilabgraz/github/bids_apps_runner}"
BASE="${E2E_BASE:-/cl_tmp/mrilabgraz/annex_e2e}"
W="${BASE}/run_${SLURM_JOB_ID:-manual}"

case "$W" in /cl_tmp/mrilabgraz/*) ;; *) echo "refusing: $W is outside /cl_tmp/mrilabgraz" >&2; exit 1 ;; esac
cleanup() {
    [[ "${KEEP:-0}" == 1 ]] && { echo "KEEP=1: leaving $W"; return; }
    chmod -R u+w "$W" 2>/dev/null || true
    rm -rf "$W"
}
trap cleanup EXIT

# The venv's git-annex is the one that works on compute nodes.
export PATH="${REPO}/.datalad-slurm-venv/bin:$PATH"
# shellcheck source=../../scripts/lib_annex_paths.sh
source "${REPO}/scripts/lib_annex_paths.sh"
FINISH="${REPO}/annex-slurm/bin/annex-slurm-finish"

fail() { echo "FAIL: $*" >&2; exit 1; }
[[ -z "${SLURM_JOB_ID:-}" ]] && fail "run this under sbatch/srun (real scheduler needed)"

mkdir -p "$W"
git init -q "$W/remote"
git -C "$W/remote" config receive.denyCurrentBranch updateInstead
git -C "$W/remote" annex init -q remote >/dev/null
git clone -q "$W/remote" "$W/work" >/dev/null
cd "$W/work"
git annex init -q work >/dev/null
branch=$(git symbolic-ref --short HEAD)
# Like 134's real root: a hidden placeholder (dotfiles stay in git) and a
# symlink OUT of the dataset (fsaverage -> FreeSurfer's own tree), which must
# never be unlocked or committed as annexed content.
echo init > .keep
ln -s /usr/local/freesurfer/8.2.0/subjects/fsaverage fsaverage
git add .keep fsaverage; git commit -q -m init; git push -q origin "$branch"

# ── a previous run's output, already annexed (read-only symlinks) ────────────
mkdir -p sub-01 sub-010
echo "old sub-01" > sub-01/out.txt
echo "old sub-010 (NOT in this batch)" > sub-010/out.txt
echo "old report" > group_report.txt
"$FINISH" -m "previous run" sub-01/out.txt sub-010/out.txt group_report.txt >/dev/null
[[ -L sub-01/out.txt && -L group_report.txt ]] || fail "fixture: previous output not annexed"
external_target=$(readlink fsaverage)

# ── this batch: sub-01 (existing, must be unlocked) and sub-02 (new) ─────────
printf 'sub-01\nsub-02\n' > "$W/subjects.txt"
mkdir -p .slurm_logs/e2e
cat > "$W/array.sh" <<EOF
#!/bin/bash
#SBATCH --job-name=e2e_array
#SBATCH --partition=hpc
#SBATCH --time=00:05:00
#SBATCH --mem=100M
#SBATCH --array=0-1
#SBATCH --output=${W}/work/.slurm_logs/e2e/slurm-%A_%a.out
set -euo pipefail
cd "${W}/work"
n=\$((SLURM_ARRAY_TASK_ID + 1))
mkdir -p "sub-0\$n"
echo "new sub-0\$n" > "sub-0\$n/out.txt"      # overwrites a locked symlink for n=1
if [[ \$n == 1 ]]; then echo "new report" > group_report.txt; fi
exit 0
EOF

# The production schedule step, exactly as submit_bids_cohort.sh calls it.
array_id=$(annex_schedule_batch "$W/work" "$W/subjects.txt" "$W/array.sh" --root-files) \
    || fail "annex_schedule_batch"
echo "array job: ${array_id}"
[[ ! -L sub-01/out.txt ]] || fail "sub-01 was not unlocked before the array ran"
[[ ! -L group_report.txt ]] || fail "root report was not unlocked"
[[ -L sub-010/out.txt ]] || fail "sub-010 is not in the batch and must stay locked"

# The finish job, dependency-chained with afterany exactly like the template.
cat > "$W/finish.sh" <<EOF
#!/bin/bash
#SBATCH --job-name=e2e_finish
#SBATCH --partition=hpc
#SBATCH --dependency=afterany:${array_id}
#SBATCH --time=00:10:00
#SBATCH --mem=1G
#SBATCH --output=${W}/finish-%j.out
set -euo pipefail
export PATH="${REPO}/.datalad-slurm-venv/bin:\$PATH"
"${REPO}/scripts/annex_cohort_finish.sh" -d "${W}/work" -s "${W}/subjects.txt" \\
    -m "e2e array ${array_id}" --root-files --extra ".slurm_logs/e2e"
EOF
finish_id=$(sbatch --parsable "$W/finish.sh"); finish_id=${finish_id%%;*}
echo "finish job: ${finish_id}"

# Wait for the finish job to reach a terminal state.
for _ in $(seq 1 120); do
    state=$(sacct -j "$finish_id" --noheader -X --format=State%20 2>/dev/null | awk 'NR==1{print $1}')
    case "$state" in
        COMPLETED) break ;;
        FAILED|CANCELLED*|TIMEOUT|OUT_OF_MEMORY|NODE_FAIL) echo "--- finish log ---"; cat "$W"/finish-*.out || true; fail "finish job ended $state" ;;
    esac
    sleep 5
done
[[ "${state:-}" == COMPLETED ]] || fail "finish job did not complete (state: ${state:-unknown})"
echo "--- finish log ---"; cat "$W"/finish-*.out

# ── the contract ─────────────────────────────────────────────────────────────
for f in sub-01/out.txt sub-02/out.txt group_report.txt; do
    [[ -L "$f" ]] || fail "$f is not annexed after finish"
done
[[ "$(cat sub-01/out.txt)" == "new sub-01" ]] || fail "sub-01 content is not the NEW output"
[[ "$(cat group_report.txt)" == "new report" ]] || fail "root report is not the NEW output"
[[ "$(cat sub-010/out.txt)" == "old sub-010 (NOT in this batch)" ]] || fail "sub-010 was touched"
n_logs=$(find .slurm_logs/e2e -type l | wc -l)
(( n_logs >= 2 )) || fail "array logs were not committed (found ${n_logs} annexed)"
left=$(find sub-01 sub-02 group_report.txt .slurm_logs/e2e -type f | wc -l)
(( left == 0 )) || fail "${left} regular file(s) left unannexed"

# Committed, not just present in the working tree -- an EMPTY commit passes
# every check above (134, 2026-09-22), so look inside the pushed history.
for f in sub-01/out.txt sub-02/out.txt group_report.txt; do
    git ls-tree -r --name-only HEAD | grep -qx "$f" || fail "$f missing from HEAD's tree"
    git -C "$W/remote" ls-tree -r --name-only "$branch" | grep -qx "$f" || fail "$f missing on the remote"
done
[[ "$(git rev-parse HEAD)" == "$(git -C "$W/remote" rev-parse "$branch")" ]] || fail "remote branch != local HEAD"
key=$(basename "$(readlink sub-01/out.txt)")
git annex whereis --key "$key" | grep -q '\[origin\]' || fail "remote not recorded as holding sub-01's new content"
[[ "$(readlink fsaverage)" == "$external_target" ]] || fail "the external symlink was changed"
git -C "$W/remote" ls-tree "$branch" -- fsaverage | grep -q "^120000" || fail "external symlink lost from the remote tree"
echo "PASS"
