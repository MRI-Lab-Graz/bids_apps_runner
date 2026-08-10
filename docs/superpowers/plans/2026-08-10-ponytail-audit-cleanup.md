# Ponytail Audit Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the dead weight identified by the `ponytail:ponytail-audit` pass on 2026-08-10: nine unused pip dependencies and 1775 lines of unreferenced archived scripts.

**Architecture:** Pure deletion — no behavior changes. Two tasks: (1) strip unused packages from `requirements.txt` / `requirements-core.txt`, (2) delete `scripts/archive/` and the stale doc entries in `scripts/README.md` that describe it.

**Tech Stack:** Python (pip requirements files), no new libraries.

## Global Constraints

- No source file may import any of the removed packages — verified by AST scan before each deletion, not just grep (regex misses re-exports/aliasing edge cases the audit already ruled out here, but the verification step must be re-run rather than trusted from memory).
- `scripts/run_bids_apps.py` (the live back-compat shim in `scripts/`, not `scripts/archive/`) is **not** part of this cleanup — it has live callers (`gui/gui_run_routes.py`, `prism_app_runner.py`) and must be left untouched.
- Full backend suite (`python3 -m pytest tests/`) must stay green after each task — deletions are expected to be behavior-neutral, and a red suite means something depended on the removed code after all.
- Per `CLAUDE.md`, this is scoped strictly to what the audit flagged — do not use this pass as cover to also "clean up" `scripts/README.md` sections unrelated to the archived-file entries (e.g. the `mri_synthseg_original.py` / examples-dir recommendations further down the file stay as-is).

---

### Task 1: Remove unused pip dependencies

**Files:**
- Modify: `requirements.txt`
- Modify: `requirements-core.txt`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing consumed by Task 2 — independent.

- [ ] **Step 1: Re-verify the packages are genuinely unused**

Run this from the repo root (excludes gitignored venvs/node_modules, matches real `import`/`from` statements via AST rather than substring grep):

```bash
python3 - <<'EOF'
import ast, pathlib
targets = {"jsonschema","colorlog","psutil","tqdm","click","yaml","rich","send2trash","pytest_mock"}
found = {t: [] for t in targets}
for p in pathlib.Path('.').rglob('*.py'):
    s = str(p)
    if any(x in s for x in ['.appsrunner','.datalad-slurm-venv','node_modules']):
        continue
    try:
        tree = ast.parse(p.read_text(errors='ignore'))
    except Exception:
        continue
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [n.name.split('.')[0] for n in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module.split('.')[0]]
        for m in mods:
            if m in targets:
                found[m].append(str(p))
for t, v in found.items():
    print(t, v)
EOF
```

Expected: every target prints an empty list (`pkgname []`). Also confirm no `mocker` fixture usage: `grep -rl 'mocker' tests/*.py` should print nothing.

If any package shows a non-empty list, stop and drop that package from the removal list below — the audit finding was wrong for that one.

- [ ] **Step 2: Edit `requirements.txt`**

Remove these lines (and their preceding comment lines) — `jsonschema`, `psutil`, `colorlog`, `tqdm`, `click`, `PyYAML`, `rich`, `send2trash`, `pytest-mock`:

```diff
-# For JSON schema validation
-jsonschema>=4.0.0
-
-# For system monitoring and process management
-psutil>=5.8.0
-
 # For DataLad integration (HPC version)
 # Note: DataLad requires system-level git and git-annex
 datalad>=0.16.0; sys_platform!="win32"
 # datalad-slurm extension (not on PyPI — install via):
 #   pip install git+https://github.com/knuedd/datalad-slurm.git
 # Already bundled in the venv; see scripts/install.sh for setup notes.

-# For enhanced logging with colors
-colorlog>=6.0.0
-
-# For better CLI experience and progress indication
-tqdm>=4.60.0
-
 # GUI Dependencies
 flask>=2.3.0
 waitress>=3.0.0
-click>=8.0.0
 requests>=2.31.0

-# For YAML configuration support (optional enhancement)
-PyYAML>=6.0
-
-# For better error handling and debugging
-rich>=12.0.0
-
-# For file operations with better cross-platform support
-send2trash>=1.8.0
-
 # Development and testing dependencies (optional)
 pytest>=7.0.0
 pytest-cov>=4.0.0
-pytest-mock>=3.0.0
```

Use the Edit tool with each hunk as a separate old_string/new_string pair (the file has no line numbers to anchor on, so match the surrounding comment text shown above).

- [ ] **Step 3: Edit `requirements-core.txt`**

```diff
-# For enhanced logging and user experience
-colorlog>=6.0.0
-tqdm>=4.60.0
-
-# For system process monitoring
-psutil>=5.8.0
-
-# For JSON configuration validation
-jsonschema>=4.0.0
-
 # GUI dependencies
 flask>=2.3.0
 waitress>=3.0.0
 requests>=2.31.0
```

- [ ] **Step 4: Confirm the remaining files still parse as valid requirements**

```bash
pip install --dry-run -r requirements.txt 2>&1 | tail -5
pip install --dry-run -r requirements-core.txt 2>&1 | tail -5
```

Expected: no `ERROR: Invalid requirement` lines (network/resolution errors from `--dry-run` in a sandboxed environment are fine — we're only checking the file's syntax survived the edit, not resolving real installs).

- [ ] **Step 5: Run the backend test suite**

```bash
python3 -m pytest tests/
```

Expected: same pass/fail counts as before this task (no test imports any of the removed packages, per Step 1's scan, so nothing should change).

- [ ] **Step 6: Commit**

```bash
git add requirements.txt requirements-core.txt
git commit -m "chore: drop unused pip dependencies

jsonschema, colorlog, psutil, tqdm, click, PyYAML, rich, send2trash,
and pytest-mock are never imported anywhere in the codebase (verified
via AST scan). Flagged by ponytail-audit."
```

---

### Task 2: Delete `scripts/archive/` and its stale README entries

**Files:**
- Delete: `scripts/archive/run_bids_apps.py`
- Delete: `scripts/archive/run_bids_apps_hpc.py`
- Delete: `scripts/archive/bids_validation_integration.py`
- Delete: `scripts/archive/hpc_batch_submit.py`
- Delete: `scripts/archive/check_system_deps.py`
- Delete: `scripts/archive/manage_datalad_repos.sh`
- Modify: `scripts/README.md`

**Interfaces:**
- Consumes: nothing from Task 1 — independent, can run in either order.
- Produces: nothing consumed elsewhere.

- [ ] **Step 1: Re-verify nothing references these files**

```bash
for f in run_bids_apps.py run_bids_apps_hpc.py bids_validation_integration.py \
         hpc_batch_submit.py check_system_deps.py manage_datalad_repos.sh; do
  echo "--- $f ---"
  grep -rn "$f" --include='*.py' --include='*.sh' --include='*.html' --include='*.js' . \
    2>/dev/null | grep -v '^./scripts/archive/' | grep -v '^./.git/'
done
```

Expected: the only hits are the `scripts/README.md` lines this task is about to edit (and, for `run_bids_apps.py`, hits inside the *live* `scripts/run_bids_apps.py` shim itself and its callers — those are expected and must stay; only the `scripts/archive/run_bids_apps.py` copy is being deleted).

If a hit turns up outside `scripts/README.md` and the live shim, stop — that file has a real caller and shouldn't be deleted.

- [ ] **Step 2: Delete the archive directory**

```bash
git rm -r scripts/archive/
```

- [ ] **Step 3: Update `scripts/README.md`**

Remove the entries describing the now-deleted files. In the "Main Runners" section:

```diff
-- **`run_bids_apps_hpc.py`** (32KB) - HPC runner with SLURM integration
-  - DataLad-aware batch submission
-  - Used for HPC environments
-
-- **`hpc_batch_submit.py`** (7.3KB) - SLURM job submission wrapper
-  - Required by HPC workflows
-
 - **`hpc_datalad_runner.py`** (16KB) - DataLad integration for HPC
   - Script generator for DataLad workflows
   - Required by prism_app_runner.py (HPC mode)
```

In the "Validation & Analysis" section:

```diff
-- **`bids_validation_integration.py`** (17KB) - BIDS validator integration
-  - Not currently imported by main app
-  - **ASSESSMENT**: Useful utility but could be standalone
-
 ### System Management
-- **`check_system_deps.py`** (3.5KB) - Dependency checker
-  - Validates Apptainer, containers, system requirements
-  - **ASSESSMENT**: Good utility, but not imported by main app
```

(Leave the `### System Management` heading in place — only its bullet is removed. If that leaves the heading with no content, delete the empty heading too.)

In the "Build & Container Scripts" section:

```diff
-## Build & Container Scripts (Keep)
-
-- **`manage_datalad_repos.sh`** (9.0KB) - DataLad repository management
-  - Clone, unlock, commit operations
-  - Required for HPC DataLad workflows
-
 ## BIDS Utility Scripts (Evaluate - May be project-specific)
```

In the "Proposed New Structure" tree near the end of the file:

```diff
 scripts/
 ├── README.md                          # This file
 ├── run_bids_apps.py                   # Core runners
-├── run_bids_apps_hpc.py
-├── hpc_batch_submit.py
 ├── hpc_datalad_runner.py
 ├── check_app_output.py                # Validation
-├── bids_validation_integration.py
-├── check_system_deps.py               # System checks
 ├── install.sh                         # Installation
-├── activate_appsrunner.sh
-└── manage_datalad_repos.sh
+└── activate_appsrunner.sh
```

- [ ] **Step 4: Confirm the deletion didn't break anything**

```bash
python3 -m pytest tests/
```

Expected: same pass/fail counts as before this task (Step 1 already confirmed nothing imports these files).

- [ ] **Step 5: Commit**

```bash
git add -A scripts/archive scripts/README.md
git commit -m "chore: delete scripts/archive/

1775 lines across 6 files with zero references anywhere else in the
repo (confirmed via grep across .py/.sh/.html/.js). Flagged by
ponytail-audit."
```

---

## Self-Review Notes

- **Spec coverage:** all three ponytail-audit findings map 1:1 to a task — unused deps → Task 1, dead archive dir → Task 2 (README cleanup folded in since it's the only place documenting the deleted files).
- **Scope guard:** Task 2 explicitly calls out that the live `scripts/run_bids_apps.py` shim and the rest of `scripts/README.md` (the `mri_synthseg_original.py` section, the `examples/` recommendations) are out of scope, since they share filenames/proximity with what's being deleted but weren't flagged by the audit.
- **No placeholders:** every step has literal commands or diff hunks; nothing deferred to "handle appropriately."
