# Working conventions for this repo

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
