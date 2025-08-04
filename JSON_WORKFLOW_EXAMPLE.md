# JSON-basierter Workflow für Missing Subjects

Dieser Workflow ermöglicht es, zuerst die Konsistenz der preprocessed Data zu überprüfen und anschließend missing Subjects über ein JSON-File zu reprocessen.

## 2-Schritt Workflow

### Schritt 1: Konsistenz prüfen und JSON generieren

#### Option A: Mit check_app_output.py
```bash
# Check consistency und save missing subjects to JSON
python check_app_output.py /data/bids /data/derivatives --output-json missing_subjects.json

# Oder für einen spezifischen Pipeline
python check_app_output.py /data/bids /data/derivatives -p qsiprep --output-json missing_subjects.json
```

#### Option B: Mit Ihrem externen Tool
```bash
# Ihr externes Tool sollte JSON in folgendem Format generieren:
{
  "pipelines": {
    "qsiprep": {
      "subjects": ["sub-001", "sub-002", "sub-003"]
    },
    "fmriprep": {
      "subjects": ["sub-001", "sub-005"]
    }
  }
}
```

### Schritt 2: Missing Subjects reprocessen

```bash
# Reprocess alle missing subjects aus dem JSON (alle Pipelines)
python run_bids_apps.py -x config.json --from-json missing_subjects.json

# Reprocess nur missing subjects von einer spezifischen Pipeline
python run_bids_apps.py -x qsiprep_config.json --from-json missing_subjects.json --pipeline qsiprep
python run_bids_apps.py -x fmriprep_config.json --from-json missing_subjects.json --pipeline fmriprep

# Mit additional options
python run_bids_apps.py -x config.json --from-json missing_subjects.json --pipeline qsiprep --dry-run
python run_bids_apps.py -x config.json --from-json missing_subjects.json --pipeline qsiprep --debug
```

## Unterstützte JSON-Formate

### Format 1: Externes Tool Format
```json
{
  "pipelines": {
    "pipeline_name": {
      "subjects": ["sub-001", "sub-002", ...]
    }
  }
}
```

### Format 2: check_app_output.py Format
```json
{
  "metadata": {
    "generated_by": "BIDS App Output Checker",
    "timestamp": "2025-08-04T...",
    "pipeline_filter": null
  },
  "missing_data_by_pipeline": {
    "qsiprep": {
      "missing_items": [...],
      "total_missing": 5,
      "subjects_with_missing_data": ["sub-001", "sub-002"]
    }
  },
  "summary": {
    "all_missing_subjects": ["sub-001", "sub-002", "sub-003"]
  }
}
```

### Format 3: Simple Subject List
```json
{
  "all_missing_subjects": ["sub-001", "sub-002", "sub-003"]
}
```

## Beispiel-Workflow

```bash
# 1. Check QSIPrep outputs und save missing subjects
python check_app_output.py /data/bids /data/derivatives -p qsiprep --output-json qsiprep_missing.json

# 2. Reprocess only the missing QSIPrep subjects
python run_bids_apps.py -x qsiprep_config.json --from-json qsiprep_missing.json --pipeline qsiprep

# 3. Verify that reprocessing worked
python check_app_output.py /data/bids /data/derivatives -p qsiprep --quiet

# 4. If all good, proceed with next pipeline
python check_app_output.py /data/bids /data/derivatives -p qsirecon --output-json qsirecon_missing.json
python run_bids_apps.py -x qsirecon_config.json --from-json qsirecon_missing.json --pipeline qsirecon
```

## Multi-Pipeline Workflow

Wenn Sie ein JSON mit mehreren Pipelines haben:

```bash
# 1. Generate comprehensive report für alle pipelines
python check_app_output.py /data/bids /data/derivatives --output-json all_missing.json

# 2. Reprocess each pipeline separately
python run_bids_apps.py -x qsiprep_config.json --from-json all_missing.json --pipeline qsiprep
python run_bids_apps.py -x fmriprep_config.json --from-json all_missing.json --pipeline fmriprep

# 3. Oder alle missing subjects auf einmal (wenn Sie eine universal config haben)
python run_bids_apps.py -x universal_config.json --from-json all_missing.json  # Alle pipelines
```

## Kombinierte One-Liner

```bash
# Check consistency und sofort reprocessen wenn missing subjects gefunden werden
python check_app_output.py /data/bids /data/derivatives -p qsiprep --list-missing-subjects | \
  (read -r subjects && [ -n "$subjects" ] && echo "$subjects" | tr ' ' '\n' | \
   xargs python run_bids_apps.py -x config.json --subjects)

# Oder mit JSON approach:
python check_app_output.py /data/bids /data/derivatives --output-json missing.json && \
python run_bids_apps.py -x config.json --from-json missing.json
```

## Pipeline-spezifische Verarbeitung

### Option 1: Verwenden Sie --pipeline Flag
```bash
# Multi-pipeline JSON file
python check_app_output.py /data/bids /data/derivatives --output-json all_pipelines.json

# Reprocess nur spezifische Pipeline
python run_bids_apps.py -x qsiprep_config.json --from-json all_pipelines.json --pipeline qsiprep
python run_bids_apps.py -x fmriprep_config.json --from-json all_pipelines.json --pipeline fmriprep
```

### Option 2: Separate JSON files für jede Pipeline
```bash
# Separate JSON files für jede Pipeline
python check_app_output.py /data/bids /data/derivatives -p qsiprep --output-json qsiprep_missing.json
python check_app_output.py /data/bids /data/derivatives -p fmriprep --output-json fmriprep_missing.json

# Dann separate reprocessing (--pipeline nicht nötig da JSON nur eine Pipeline enthält)
python run_bids_apps.py -x qsiprep_config.json --from-json qsiprep_missing.json
python run_bids_apps.py -x fmriprep_config.json --from-json fmriprep_missing.json
```

### Error Handling
```bash
# Was passiert wenn Pipeline nicht im JSON existiert?
python run_bids_apps.py -x config.json --from-json missing.json --pipeline nonexistent
# ERROR: Pipeline 'nonexistent' not found in JSON. Available: ['qsiprep', 'fmriprep']

# Was passiert wenn --pipeline nicht spezifiziert wird?
python run_bids_apps.py -x config.json --from-json multi_pipeline.json
# INFO: Using subjects from all pipelines: qsiprep, fmriprep (combined subject list)
```

## Logging und Debugging

```bash
# Mit verbose logging
python run_bids_apps.py -x config.json --from-json missing.json --log-level DEBUG

# Dry run to test
python run_bids_apps.py -x config.json --from-json missing.json --dry-run

# Debug einzelnen Subject
python run_bids_apps.py -x config.json --subjects sub-001 --debug
```
