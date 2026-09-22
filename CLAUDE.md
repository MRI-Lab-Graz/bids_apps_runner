# Working conventions for this repo

## ⚠️ Data storage — bulk data goes under /cl_tmp/mrilabgraz, never /usr/people

**All BIDS input data, derivatives, per-run scratch, logs, and subject
lists must live under `/cl_tmp/mrilabgraz`** (a large shared scratch
filesystem). `/usr/people/mrilabgraz` (the home/user-folder filesystem)
does **not** have the quota for this — do not point `shared_input_base`,
`shared_output_base`, `scratch_dir`, `log_dir`, or `subject_lists_dir` at
anything under `/usr/people` in any HPC cohort config
(`configs/*.json`, `scripts/submit_bids_cohort.sh` /
`scripts/hpc_datalad_runner.py` inputs).

Container images (`.sif`) and small shared files like the FreeSurfer
license (e.g. `/usr/people/mrilabgraz/container/...`) are the one
exception — they're small, shared infrastructure, not per-run data, and
that's where the existing containers already live on disk.

When generating or editing any config that sets these path keys, default
to `/cl_tmp/mrilabgraz/<something>` — never invent a new path under
`/usr/people/mrilabgraz` for bulk data, even as a placeholder.

## ⚠️ HPC login node policy — never run real compute there

The admin's instruction is explicit: **do not use the login node** for
anything beyond submitting jobs and checking status. Violating this risks
losing cluster access entirely.

Real incident (2026-07-29): a `datalad push` for a FreeSurfer longitudinal
cohort (study 129) was run interactively on `IT010128` -- confirmed via
`sinfo -N` not listing it as a cluster member at all (every real compute
node, `IT010130` upward, was in the pool). The push itself was arguably
low-risk network I/O, but it was one step away from someone instead
clicking "Run" in the GUI and launching a full FreeSurfer/fMRIPrep
container directly on shared login-node hardware -- which is exactly what
"local/cluster execution mode" (`--local`, used by the GUI's `/run_app` and
`/run_pilot_estimator` routes, and any direct CLI use of
`run_bids_apps.py --local`) does: it runs the real container and
`datalad get`/`push` calls **synchronously in the calling process, on
whatever host that process happens to be running on.**

**This is now enforced in code, not just policy**: `execute_local()` in
`scripts/prism_local.py` refuses to proceed (raises
`LoginNodeExecutionError`, loud multi-line message) whenever it detects
`sbatch` on PATH (a SLURM cluster) with no active allocation
(`SLURM_JOB_ID`/`SLURM_JOBID` unset) -- i.e. a bare login node. This is a
single chokepoint, so it covers the GUI's "Run" button, the pilot resource
estimator, and direct CLI invocations alike. `--dry-run` is exempt (it
never touches a container or the network, so it's safe and useful for
config validation on the login node). See
`tests/test_prism_local_login_node_guard.py` for the exact conditions.

**If you hit this refusal, the correct paths are:**
- Full cohort: `scripts/submit_bids_cohort.sh submit -c <config>` (submits
  via `sbatch`, chains a `datalad slurm-finish` + push job).
- Single ad-hoc subject: `scripts/hpc_datalad_runner.py -c <config>
  -s <subject> -o <script.sh> --submit`.
- Interactive testing that genuinely needs `--local`: get a real
  allocation first (`salloc`/`srun`), which sets `SLURM_JOB_ID`
  automatically -- don't just run it bare on the login node.

If you're adding a *new* code path that shells out to a container engine
(`apptainer`/`singularity`/`docker`) or does real data movement
(`datalad get`/`push`, `git annex get`/`copy`) outside of an already
`sbatch`-dispatched script, it needs to go through this same guard (or an
equivalent one) -- don't reintroduce a login-node execution path by adding
a new entry point that bypasses `execute_local()`.

### ⚠️ A guard only protects processes started *after* it landed

Second incident (2026-09-22, another admin warning). A
`prism_app_runner.py` GUI daemon was found still running on `IT010128`
after **62 days**. It had started 2026-07-22; the login-node guard above
landed 2026-07-29 in `c5edee6`. A running Python process keeps the code it
loaded at startup, so **that daemon never had the guard at all** -- for its
whole life its "Run" button would have executed containers straight onto
the login node. The policy was fixed in git and still violated in memory.

Alongside it: orphaned `tail -f`/`grep` watchers from Claude Code sessions
that had exited 48 days earlier, and VS Code server stacks 41 and 91 days
old. None of this is *compute*, so none of it was the sort of thing
`execute_local()` looks for.

Two rules follow, and both are enforced in code:

- **Long-lived processes get a bounded lifetime.** The GUI now shuts
  itself down after 30 min idle on a bare login node
  (`_should_shut_down_now()` in `prism_app_runner.py`, decided by
  `login_node_hygiene.should_exit_idle()`). In-flight `datalad clone`s and
  container pulls always win -- see `_job_registries()`. Timer-driven
  status polls deliberately do **not** count as activity, or a forgotten
  browser tab would keep it alive forever. The cap is inert off a login
  node, so `install_macos.sh` collaborators are unaffected.
- **Residue gets reaped, not just reported.** `scripts/login_node_hygiene.py`
  runs hourly from cron. It only ever touches this user's own UID, and
  `is_protected()` vetoes anything with living children, a `SLURM_JOB_ID`,
  or in the reaper's own lineage. Age alone is never sufficient: in the
  real incident one 20-day-old VS Code stack was the live session and two
  older ones were abandoned, and only the child check told them apart.

When you add a new daemon, background thread, or watcher that can outlive
a single request, give it a lifetime bound or a reap rule. "It's only
running while someone's using it" is exactly what was believed about the
62-day GUI.

**Before debugging a guard that "should" have fired, check how long the
process has been up** (`ps -o lstart= -p <pid>`) against when the guard
landed (`git log -S <symbol>`). A stale process is not a broken guard.

### ⚠️ "Read-only" git/git-annex checks are not exempt either -- there is no guard for this yet

Third incident (2026-09-22), same day as the one above, different cause: an
interactive investigation (Claude, diagnosing the study-134 subregion-
segmentation data-loss incident) ran plain `git status`, `git diff-tree`, and
`find` directly on `IT010128` against
`/cl_tmp/mrilab/134/derivatives/freesurfer` -- a git-annex dataset with
~300k files -- to check whether specific commits actually changed any
content. These are read-only and touch no container and no network, so they
are **not** the kind of thing `execute_local()` or
`incremental_datalad_save.sh`'s own login-node guard look for (both only
gate container runs and `datalad`/`git annex` get/push/hashing). Nothing in
code stopped this.

Result: `git status` alone ran at ~17% CPU for 5+ minutes before being
killed, and spawned several `git-annex cat-file --batch` / `diff` helper
subprocesses that **outlived the killed parent** and had to be killed
individually by PID -- the same orphaned-process pattern as the 62-day-GUI
incident above, just produced by an interactive investigation instead of a
stale daemon. Separately, `incremental_datalad_save.sh --dry-run` -- which
is explicitly exempted from that script's own guard on the theory that a
dry run is cheap -- still timed out after 100s across only 112 subjects on
this filesystem. "Read-only" and "explicitly marked cheap" both turned out
not to mean "actually cheap" here; NFS latency and git-annex's per-object
bookkeeping dominate regardless of whether anything is being written.

**Lesson, until this gets an actual code guard**: any git/git-annex
operation that walks history or diffs against a large annex working tree --
unscoped `git status`, `git diff-tree`, `git log -p`, `find` over an annex
checkout, etc. -- must be treated as real work and run via `sbatch` or
under `salloc`/`srun`, never bare on the login node, *even when it's
read-only and even when a tool says its dry-run/status-check mode is
exempt*. If you're investigating a dataset this size, scope every git
command to the narrowest path you can (a single subject's subtree, a single
commit) and prefer submitting a small diagnostic script over running the
check interactively -- see `scripts/audit_subregion_commits.sh` for the
pattern this incident led to. Flagging as a first-order problem: a proper
fix would extend the `execute_local()`-style chokepoint to cover heavy
read-only git-annex operations too, not just container/data-movement calls,
since right now this category is policy-only with no enforcement.

## Chip away at the monoliths

`templates/index.html` (~7100 lines, most of it one inline `<script>` block)
and `prism_app_runner.py` are legacy monoliths. Whenever a fix or feature
touches logic that lives in either of them, prefer extracting that logic into
its own module rather than adding more code to the monolith:

- **Frontend**: new or modified JS functions belong in `static/js/*.js`
  (see `project_loader.js`, `readiness_panel.js` for the existing pattern),
  not appended to the inline `<script>` block in `templates/index.html`.
  If a function you need to touch is still inline there, moving just that
  function out to a module is preferred over editing it in place.
- **Backend**: new or modified routes/logic belong in `gui/gui_*_routes.py`
  (already split by concern: run, project, cohort, utility, misc, system,
  auth) or `scripts/*.py`, not added to `prism_app_runner.py` itself.

This is opportunistic, scoped to whatever you're already touching for the
issue at hand -- not a mandate to refactor unrelated code nearby just
because it's in the same file.

## Testing

- **Backend**: `pytest` (`tests/*.py`). Run with `python3 -m pytest tests/`.
  A coverage gate (`pytest.ini`, currently 80%) fails the run if new
  untested code drags the total below it -- add tests alongside new
  `scripts/*.py`/`gui/*.py` code, not just the happy path.
- **Frontend**: `static/js/*.js` modules extracted per the rule above get
  tests under `tests_js/*.test.js` (Vitest + jsdom). These are plain
  classic `<script src>` files, not ES modules, so tests load them via
  `tests_js/helpers/loadScript.js` (`window.eval()`, same effective
  semantics as the browser). See `tests_js/README.md` for one-time setup
  (this machine has no system Node.js/sudo, so a portable build is
  downloaded into gitignored `.node-runtime/`) and the pattern for stubbing
  the handful of helpers that still live inline in `templates/index.html`
  vs. loading real `static/js/*.js` dependencies. Run with:
  ```
  export PATH="$(pwd)/.node-runtime/bin:$PATH"
  npm test
  ```
  When extracting a function out of the inline `<script>` per the rule
  above, add a `tests_js/*.test.js` case for it in the same change --
  that's the point of extracting it.
