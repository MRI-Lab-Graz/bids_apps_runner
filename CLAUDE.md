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
