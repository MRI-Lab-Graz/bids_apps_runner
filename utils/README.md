# BIDS Utilities

Optional utilities for BIDS dataset manipulation and workflow management. These are project-specific tools that may be useful but are not required for core BIDS Apps Runner functionality.

## Event File Utilities

### `copy_events_to_bids.py`
Copy event TSV files into a BIDS dataset structure with validation.

```bash
python utils/copy_events_to_bids.py \
  --events-dir /path/to/flat/events \
  --bids-root /path/to/bids \
  --dry-run
```

**Features:**
- Validates subject/session/task/run matching
- Checks for existing BOLD files
- Prevents overwriting existing events
- Dry-run mode for testing

**Use case:** When you have event files in a flat directory that need to be copied into the proper BIDS func/ folders.

---

### `rename_al_events.py`
Rename legacy event logs (nf_* format) to BIDS-compliant naming.

```bash
python utils/rename_al_events.py \
  --directory /path/to/events \
  --legacy-task AL \
  --bids-task task-rest \
  --subject-prefix "" \
  --include-run \
  --session-override 1
```

**Features:**
- Converts old naming conventions to BIDS format
- Handles subject/session extraction
- Optional run numbers
- Session override capability

**Use case:** Migrating legacy data with non-BIDS naming to proper BIDS format.

---

## BIDS Metadata Utilities

### `fix_bids_intendedfor.py`
Fix IntendedFor fields in fieldmap JSON sidecars by removing BIDS URI prefixes.

```bash
python utils/fix_bids_intendedfor.py /path/to/bids sub-001
```

**Features:**
- Removes `bids::` prefixes from IntendedFor paths
- Removes subject directory from paths
- Updates JSON files in-place

**Use case:** Fixing fieldmap metadata after conversion from other formats.

---

## Processing Utilities

### `fmriprep2conn.sh`
Standardize fMRIPrep output structure for consistency across single and multi-session subjects.

```bash
bash utils/fmriprep2conn.sh
```

**Features:**
- Moves single-session anatomy to subject level
- Updates internal references in JSON files
- Fixes HTML report links
- Preserves session-specific transforms

**Use case:** Standardizing fMRIPrep outputs for downstream connectivity analysis.

**Note:** Run from within the fMRIPrep derivatives directory.

---

### `kill_app.sh`
Kill specific BIDS app processes by search phrase (useful for stuck jobs).

```bash
# Kill specific jobs
bash utils/kill_app.sh "qsirecon.*sub-001"

# Continuous monitoring mode
bash utils/kill_app.sh "qsirecon" --loop --timeout 30
```

**Features:**
- Process search by pattern
- Kills child and parent processes
- Loop mode for continuous monitoring
- Timeout for auto-exit

**Use case:** Cleaning up stuck or problematic BIDS app processes.

---

## Important Notes

⚠️ **These utilities are optional and project-specific**. They are provided as examples and may need modification for your specific use case.

⚠️ **Always test with `--dry-run` when available** before running operations that modify your data.

⚠️ **Backup your data** before running utilities that modify BIDS datasets or metadata.

## Contributing

If you create useful BIDS utilities, consider contributing them here. Ensure they:
- Have clear documentation
- Include usage examples
- Handle errors gracefully
- Support dry-run mode when modifying data
