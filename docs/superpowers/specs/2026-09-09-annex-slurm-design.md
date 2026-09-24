# annex-slurm: design spec

Status: draft, fully validated against production data (see "Validation
evidence" below) before being written down here — every primitive in this
spec has actually run successfully against dataset `134`'s real FreeSurfer
output on this cluster, not just in theory.

## Why this exists

`datalad-slurm` (the extension this pipeline previously used to orchestrate
SLURM array jobs against datalad-managed git-annex datasets) breaks down on
large, long-lived repos: `datalad unlock`/`datalad status`/`datalad save`/
`datalad slurm-schedule`/`datalad slurm-finish` all depend on datalad's
Python layer running a full index/working-tree/object-graph comparison
before doing anything else. On dataset `134` (~142K annexed objects,
~249K tracked paths, FreeSurfer's very file-dense per-subject output) that
comparison hangs — sometimes for hours, sometimes indefinitely — because
git-annex's own "keys database" (an internal cache mapping annexed keys to
locations) accumulates reconciliation drift every time a write-oriented
operation gets interrupted (a dropped session, a SLURM walltime `TIMEOUT`,
a killed debugging attempt). `134` accumulated an unusual amount of this
drift over several weeks, and every comparison-requiring git/git-annex/
datalad command hit the same wall as a result — proven by testing `git
status`, `git diff`, `git add`, `git fsck`, `git read-tree HEAD`, `datalad
unlock`, and `datalad status` in isolation: all hang, regardless of scope.

Full incident write-up: this pipeline's `submit_bids_cohort.sh` header
comments and this conversation's own investigation trail (2026-09-01
through 2026-09-09).

## Core insight

Every hanging command needed to *compare* something (index vs. working
tree, index vs. object graph, local vs. remote). Every command that only
needed to *read or write specific, already-known state* — never compare —
stayed fast regardless of repo size or accumulated drift:

| Fast (proven, used by this design) | Hangs (proven, never used) |
|---|---|
| `git annex calckey` | `git status` |
| `git annex setkey` (core effect; command itself needs a bounded timeout — see below) | `git diff` |
| `git annex fromkey` | `git add` |
| `git annex contentlocation` / `whereis --key` | `git fsck` |
| `git ls-tree`, `git write-tree`, `git commit-tree`, `git update-ref` | `git read-tree HEAD` (no explicit tree target) |
| `git push` (ref-level) | `datalad unlock` / `status` / `save` / `slurm-schedule` / `slurm-finish` |
| `git annex setpresentkey` | `git annex copy --to <remote>` (its P2P protocol needs to *read* local content for transfer, which re-triggers the same local reconciliation) |
| `rsync` (raw object transfer) | `git annex lock` (added 2026-09-23: it must verify working-tree content against the recorded key to decide whether locking is safe, i.e. compare. Hung 43 min at 0% CPU with four unreaped zombie `git` children, having locked 0 of 500 paths, before being cancelled. Note `git annex unlock` is in the fast column -- it materializes content from a known key and compares nothing.) |

`annex-slurm` is built entirely from the left column. It has no
dependency on datalad's CLI or Python API, and it never asks git or
git-annex to figure out "what changed" — the caller always already knows
exactly which paths changed (they're the array job's own declared output),
so nothing needs to be discovered by comparison.

## Scope

Two commands only. Nothing speculative — every piece below has been
exercised against real production data.

### `annex-slurm schedule <path>... -- <sbatch-args>`

1. For each given path that is an *existing* tracked file that needs to
   become writable: `git annex unlock <path>` (proven fast: 0.18s for a
   single file, 6s for a whole ~2000-file subject directory — never `-o .`,
   never a whole-tree operation). New, not-yet-tracked output paths need no
   unlock step — there's nothing to unlock.
2. Dispatch via plain `sbatch`. No wrapper, no bookkeeping database.
   "What's currently running" is answered by SLURM's own `squeue`/`sacct` —
   proven sufficient because this pipeline's batches are already strictly
   sequential (chained one at a time via the finish step), so the
   open-job-conflict problem datalad-slurm's database exists to prevent
   doesn't arise in practice. (If a future user of this tool genuinely runs
   concurrent overlapping batches, that's a documented limitation, not a
   silent risk — see "Explicitly out of scope.")
3. Every working file this needs (path lists, generated array scripts) is
   required to live under shared HPC storage, never a location local to
   whatever host the orchestrating process happens to run on. This is
   enforced, not just documented — see "Operational lessons" below;
   real jobs failed instantly twice during development because of this
   exact mistake.

### `annex-slurm finish <path>...`

Given the array job's own declared output paths (known by the caller, not
discovered):

1. **Capture expected size for each path before any consuming operation
   runs.** (See "Operational lessons" — this ordering bug cost a full
   ~2.5h re-run during development.)
2. `calckey` → `setkey` (bounded timeout, e.g. 15s; `setkey`'s core
   effect — moving content into the object store — completes even when the
   command itself hangs on some trailing step, confirmed by checking the
   object landed correctly afterward regardless of the command's own exit
   status).
3. Verify every object via `contentlocation`, comparing against the size
   captured in step 1 — a targeted, per-key lookup, never a full-tree scan.
4. Build the new commit in a **temporary `GIT_INDEX_FILE`**, seeded from
   `HEAD`'s tree (`git read-tree <HEAD-tree-sha>` into the fresh temp index
   — not `git read-tree HEAD`, which triggers a working-tree comparison and
   hangs; reading an explicit tree-ish into an *empty* temp index is pure
   object-graph work and stays fast) plus only the batch's explicit new
   paths via `fromkey`. Never touches the repo's real index, which is known
   to carry unrelated drift (confirmed: 33,737 of 248,827 tracked paths
   differed between the real index and `HEAD` on `134`, unrelated to
   anything this tool did).
5. `write-tree` → `commit-tree -p HEAD` → `update-ref refs/heads/<branch>`.
   Verify the commit object and at least one file (both a batch file and a
   file *outside* the batch, to confirm nothing else broke) via direct
   `cat-file`/`show` lookups.
6. `git push`. Verify it landed via `git ls-remote` ref comparison — never
   `git status`/`diff`.
7. **Push the annexed content itself via `rsync`, not `git annex copy`.**
   `git annex copy --to <remote>` was proven to hang even when scoped to a
   single already-known `--key` — its P2P transfer protocol needs to read
   local object content to stream it, which re-enters the same reconciliation
   path that makes everything else on this repo slow. The proven
   alternative: resolve each path's already-correct working-tree symlink to
   its `.git/annex/objects/...` relative path, `rsync -aR` all objects to
   the identical relative path under the remote's repo root in one call
   (git-annex's hash-directory layout is deterministic and identical on
   both ends), verify each remote object's size against the local one, then
   record presence locally via a single batched
   `git annex setpresentkey --batch` call (format: `KEY UUID 1` per line,
   fed via stdin) — never one `setpresentkey` call per file. Verified via
   `git annex whereis --key` (targeted, ~4s even on this repo).

## Explicitly out of scope

Nothing below made it into this design because nothing below has been
proven necessary:

- **Any open-jobs bookkeeping database.** `134`'s batches are strictly
  sequential; SLURM's own state was sufficient in every real test.
- **Any `-o`-style wildcard or whole-tree output declaration.** Every
  `-o .`-equivalent operation this investigation touched (`datalad
  unlock`, the original `datalad-slurm` scheduling call) was directly
  implicated in the incident. Explicit paths only, always.
- **Any dependency on datalad's CLI or Python API**, for scheduling,
  finishing, or anything else.
- **Concurrent/overlapping batch support.** Not tested, not needed by this
  pipeline's actual usage pattern. A future user who needs it should treat
  it as a new, separately-designed feature, not an assumed capability.

## Operational lessons (baked into the design, not just noted)

Two real bugs surfaced during validation and are reflected directly in the
steps above, not left as "remember to..." documentation:

1. **Compute-node-invisible paths.** Twice during development, a working
   file (a timepoint list, then a temp-index/file-manifest path) was placed
   under a location only visible to the orchestrating host, and the
   `sbatch` job failed instantly with "No such file or directory." Every
   path an `annex-slurm`-generated script touches must resolve under
   shared HPC storage — this is a hard requirement of the design, not a
   convention to remember.
2. **Consuming operations must have their "before" state captured in the
   same pass.** `git annex setkey` *moves* a file's content into the object
   store rather than copying it — verifying success in a later, separate
   pass found the original path already gone for every single file and
   wrongly reported every one as a failure, even though every object had
   actually landed correctly. Cost a real ~2.5h of compute to redo. Step 2
   of `finish` above captures size in the same loop as the consuming
   `setkey` call specifically because of this.

## Validation evidence

Every primitive above was run against `134`'s real FreeSurfer output on
this cluster, not a toy repo:
- 3 real subjects, real `segment_subregions` compute (~2.5-2.8h array job).
- 182 real output files: `calckey`/`setkey`/verify all succeeded.
- A real commit built via the temp-index approach, independently confirmed
  correct on the remote server (`derivatives` branch there matches the
  local commit exactly) — not just locally assumed.
- All 182 objects' content independently pushed via `rsync` and confirmed
  present via `git annex whereis` from a completely fresh session.

## Testing plan

- Unit-level: each primitive step (temp-index construction, size-before-setkey
  capture, rsync path resolution, batched setpresentkey formatting) gets a
  test against a small, disposable git-annex repo — fast, no dependency on
  `134` or any HPC resources.
- Integration-level: a small (2-3 file) end-to-end `schedule`/`finish` run
  against a disposable repo, run via actual `sbatch` (not simulated), since
  the two real bugs found during development were both specifically about
  what does and doesn't work under `sbatch`.
- No test should ever invoke `git status`/`diff`/`add`/`fsck`/`read-tree
  HEAD`/`datalad` anything, including in test setup/teardown — a test that
  does would not actually validate this tool's core premise.

## Packaging

Branch `annex-slurm` in this repo for now; the goal is a separate,
standalone public repository once the design is validated further at
production scale. Updated 2026-09-23: `134`'s full cohort HAS now run
through it -- 117 subjects (not 121; `sub-134053`, `-134061`, `-134069` and
`-134081` are gaps in the ID numbering and exist nowhere in the dataset,
and `participants.tsv` has 117 rows), 306 longitudinal timepoints, 7903
subregion files. It surfaced one real bug in `annex-slurm-finish`: staging
into the temp index via `git annex fromkey` silently no-ops when the
working-tree symlink already matches the key, so 132 consecutive commits
were EMPTY while every check in the tool passed. Fixed by staging with
`git update-index --cacheinfo` plus a gate that refuses to commit unless
the tree records exactly the staged paths; regression test in
`annex-slurm/test/smoke_test.sh`.
