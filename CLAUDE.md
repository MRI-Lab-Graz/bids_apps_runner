# Working conventions for this repo

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
