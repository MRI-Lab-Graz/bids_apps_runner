# Pipeline-spezifische JSON Beispiele

## Beispiel 1: Multi-Pipeline JSON (von check_app_output.py --output-json)

```json
{
  "metadata": {
    "generated_by": "BIDS App Output Checker",
    "timestamp": "2025-08-04T15:30:45.123456",
    "command": "python check_app_output.py /data/bids /data/derivatives --output-json all_missing.json",
    "pipeline_filter": null
  },
  "missing_data_by_pipeline": {
    "qsiprep": {
      "missing_items": [
        "QSIPrep subject directory missing: sub-001",
        "QSIPrep subject directory missing: sub-005"
      ],
      "total_missing": 2,
      "subjects_with_missing_data": ["sub-001", "sub-005"]
    },
    "fmriprep": {
      "missing_items": [
        "fMRIPrep subject directory missing: sub-002",
        "fMRIPrep subject directory missing: sub-007"
      ],
      "total_missing": 2,
      "subjects_with_missing_data": ["sub-002", "sub-007"]
    }
  },
  "summary": {
    "total_pipelines_checked": 2,
    "pipelines_with_missing_data": 2,
    "all_missing_subjects": ["sub-001", "sub-002", "sub-005", "sub-007"]
  }
}
```

**Verwendung:**
```bash
# Alle missing subjects reprocessieren
python run_bids_apps.py -x universal_config.json --from-json all_missing.json
# Resultado: sub-001, sub-002, sub-005, sub-007

# Nur QSIPrep missing subjects
python run_bids_apps.py -x qsiprep_config.json --from-json all_missing.json --pipeline qsiprep
# Resultado: sub-001, sub-005

# Nur fMRIPrep missing subjects  
python run_bids_apps.py -x fmriprep_config.json --from-json all_missing.json --pipeline fmriprep
# Resultado: sub-002, sub-007
```

## Beispiel 2: External Tool JSON Format

```json
{
  "pipelines": {
    "qsiprep": {
      "subjects": ["sub-001", "sub-003", "sub-008"],
      "status": "missing_outputs",
      "checked_date": "2025-08-04"
    },
    "qsirecon": {
      "subjects": ["sub-001", "sub-002", "sub-008"],
      "status": "missing_reconstructions",
      "checked_date": "2025-08-04"
    }
  },
  "metadata": {
    "tool": "External BIDS Checker",
    "version": "1.0"
  }
}
```

**Verwendung:**
```bash
# Alle missing subjects von allen pipelines
python run_bids_apps.py -x config.json --from-json external_report.json
# Resultado: sub-001, sub-002, sub-003, sub-008 (combined from all pipelines)

# Nur QSIPrep missing subjects
python run_bids_apps.py -x qsiprep_config.json --from-json external_report.json --pipeline qsiprep
# Resultado: sub-001, sub-003, sub-008

# Nur QSIRecon missing subjects
python run_bids_apps.py -x qsirecon_config.json --from-json external_report.json --pipeline qsirecon
# Resultado: sub-001, sub-002, sub-008
```

## Beispiel 3: Praktischer Workflow

### Step 1: Generate comprehensive report
```bash
python check_app_output.py /data/bids /data/derivatives --output-json comprehensive_report.json
```

### Step 2: Check what's in the report
```bash
# Quick overview
python -c "import json; data=json.load(open('comprehensive_report.json')); print('Pipelines:', list(data['missing_data_by_pipeline'].keys())); [print(f'{p}: {len(data[\"missing_data_by_pipeline\"][p][\"subjects_with_missing_data\"])} subjects') for p in data['missing_data_by_pipeline']]"
```

### Step 3: Targeted reprocessing
```bash
# Check if specific pipeline has missing subjects before reprocessing
python -c "import json; data=json.load(open('comprehensive_report.json')); qsi_missing=data['missing_data_by_pipeline'].get('qsiprep', {}).get('subjects_with_missing_data', []); print(f'QSIPrep missing: {len(qsi_missing)} subjects') if qsi_missing else print('QSIPrep: All subjects processed')"

# If missing subjects found, reprocess only those
if [ $(python -c "import json; data=json.load(open('comprehensive_report.json')); print(len(data['missing_data_by_pipeline'].get('qsiprep', {}).get('subjects_with_missing_data', [])))") -gt 0 ]; then
    echo "Reprocessing QSIPrep missing subjects..."
    python run_bids_apps.py -x qsiprep_config.json --from-json comprehensive_report.json --pipeline qsiprep
else
    echo "QSIPrep: No missing subjects, skipping reprocessing"
fi
```

## Error Scenarios and Handling

### Scenario 1: Pipeline not found
```bash
python run_bids_apps.py -x config.json --from-json report.json --pipeline nonexistent
```
**Output:**
```
ERROR: Pipeline 'nonexistent' not found in JSON. Available: ['qsiprep', 'fmriprep']
```

### Scenario 2: Empty pipeline
```bash
# JSON contains pipeline but no missing subjects
python run_bids_apps.py -x config.json --from-json report.json --pipeline qsiprep
```
**Output:**
```
INFO: No missing subjects found in JSON file
INFO: Found 0 subjects to process
ERROR: No subjects found.
```

### Scenario 3: Invalid JSON format
```bash
python run_bids_apps.py -x config.json --from-json invalid.json
```
**Output:**
```
ERROR: Unsupported JSON format. Expected 'pipelines', 'missing_data_by_pipeline', or 'all_missing_subjects' fields.
```

## Best Practices

1. **Always check pipeline availability first:**
```bash
python -c "import json; print('Available pipelines:', list(json.load(open('report.json'))['missing_data_by_pipeline'].keys()))"
```

2. **Use pipeline-specific configs:**
```bash
# Don't do this (wrong config for pipeline)
python run_bids_apps.py -x fmriprep_config.json --from-json report.json --pipeline qsiprep

# Do this (matching config and pipeline)
python run_bids_apps.py -x qsiprep_config.json --from-json report.json --pipeline qsiprep
```

3. **Combine with dry-run for testing:**
```bash
python run_bids_apps.py -x config.json --from-json report.json --pipeline qsiprep --dry-run
```

4. **Use validation after reprocessing:**
```bash
python run_bids_apps.py -x config.json --from-json report.json --pipeline qsiprep --validate
```
