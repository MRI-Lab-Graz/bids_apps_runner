# BIDS App Runner mit integrierter Output-Validierung

## 🎯 Neue Validation Features

Die BIDS App Runner Scripts haben jetzt **integrierte Output-Validierung** mit automatischer Reprocessing-Funktionalität.

### ✨ Neue Command-Line Optionen

```bash
# Nach Processing validieren und Reports erstellen
./run_bids_apps.py -x config.json --validate

# Nur validieren, kein Processing
./run_bids_apps.py -x config.json --validate-only

# Automatisch fehlende Subjects reprocessieren
./run_bids_apps.py -x config.json --reprocess-missing

# Reports in custom Verzeichnis speichern
./run_bids_apps.py -x config.json --validate --validation-output-dir reports
```

## 🔄 Intelligenter Workflow

### Scenario 1: Standard Processing mit Validation
```bash
# 1. Normales Processing mit automatischer Validation
./run_bids_apps.py -x fmriprep_config.json --validate

# Outputs:
# - Normal processing logs
# - validation_report_fmriprep_20250728_143522.json
# - reprocess_config_fmriprep_20250728_143522.json (wenn fehlende Subjects)
```

### Scenario 2: Nur Validation (Check existing outputs)
```bash
# Validiere bereits existierende Outputs
./run_bids_apps.py -x fmriprep_config.json --validate-only

# Output zeigt:
# ✅ All subjects processed successfully!
# oder
# ⚠️  Found 3 subjects requiring reprocessing
# 📋 Missing subjects: sub-001, sub-003, sub-005
# 🔄 Reprocess config: reprocess_config_fmriprep_20250728_143522.json
```

### Scenario 3: Vollautomatischer Workflow
```bash
# Processing + Validation + Auto-Reprocessing in einem Schritt
./run_bids_apps.py -x fmriprep_config.json --reprocess-missing

# 1. Verarbeitet alle Subjects
# 2. Validiert Outputs automatisch  
# 3. Erstellt Reprocess-Config für fehlende Subjects
# 4. Startet automatisch Reprocessing für fehlende Subjects
# 5. Wiederholt bis alle Subjects erfolgreich verarbeitet
```

## 📊 Generated Reports

### Validation Report (JSON)
```json
{
  "metadata": {
    "generated_by": "BIDS App Runner Integrated Validator",
    "timestamp": "2025-07-28T14:35:22.123456",
    "bids_directory": "/data/bids/study",
    "output_directory": "/data/derivatives/fmriprep",
    "pipeline_type": "fmriprep"
  },
  "validation_results": {
    "pipelines": {
      "fmriprep": {
        "passed": true,
        "missing_items": [
          "[ERROR] sub-001: fMRIPrep file missing",
          "[ERROR] sub-003: fMRIPrep file missing"
        ]
      }
    },
    "summary": {
      "total": 1,
      "passed": 0,
      "failed": 1
    }
  },
  "missing_subjects": ["sub-001", "sub-003"]
}
```

### Reprocess Config (JSON)
```json
{
  "common": {
    "bids_folder": "/data/bids/study",
    "output_folder": "/data/derivatives/fmriprep",
    "tmp_folder": "/tmp/fmriprep_work",
    "container": "/containers/fmriprep_24.0.1.sif",
    "templateflow_dir": "/data/templateflow",
    "jobs": 4
  },
  "app": {
    "analysis_level": "participant",
    "options": ["--skip-bids-validation"],
    "participant_labels": ["sub-001", "sub-003"]
  },
  "_metadata": {
    "generated_by": "BIDS App Runner Integrated Validator",
    "timestamp": "2025-07-28T14:35:22.123456",
    "original_subjects": 2,
    "reprocess_reason": "Missing or incomplete outputs detected"
  }
}
```

## 🎛️ Unterstützte Pipelines

Die Validation erkennt automatisch diese BIDS Apps:

- **fMRIPrep** - Functional MRI preprocessing 
- **QSIPrep** - Diffusion MRI preprocessing
- **FreeSurfer** - Structural MRI processing
- **QSIRecon** - Diffusion MRI reconstruction

Pipeline-Erkennung erfolgt über:
1. Container-Namen (`fmriprep.sif` → fmriprep)
2. Output-Verzeichnisse (`derivatives/fmriprep` → fmriprep)
3. App-Optionen (qsirecon flags → qsirecon)

## 🔧 Session-Aware Validation

Funktioniert perfekt mit der neuen **Session-Awareness**:

```bash
# Multi-Session Datasets
./run_bids_apps.py -x config.json --validate-only

# Output:
# Subject 'sub-001' partially processed: 2/3 sessions complete. Missing sessions: ['ses-03']
# 📋 Missing subjects: sub-001
# 🔄 Reprocess config enthält nur sub-001 für fehlende Session
```

## 💡 Best Practices

### Development/Testing
```bash
# 1. Pilot run mit Validation
./run_bids_apps.py -x config.json --pilot --validate

# 2. Check ob alles funktioniert  
./run_bids_apps.py -x config.json --validate-only

# 3. Full run mit Auto-Reprocessing
./run_bids_apps.py -x config.json --reprocess-missing
```

### Production Workflows
```bash
# Single command für complete workflow
./run_bids_apps.py -x production_config.json --reprocess-missing --validation-output-dir production_reports

# Monitoring mit external validation
./run_bids_apps.py -x config.json --validate-only --validation-output-dir daily_checks
```

## 🚨 Error Handling

```bash
# Exit codes:
# 0: All subjects successfully processed and validated
# 1: Processing failures OR validation found missing subjects (without --reprocess-missing)

# Logs alle Actions:
tail -f logs/bids_app_runner_*.log

# Validation Reports für debugging:
ls -la validation_reports/
```

## 🔗 Integration Benefits

✅ **Keine separaten Scripts** - Alles in einem Workflow  
✅ **Automatische Pipeline-Erkennung** - Keine manuelle Konfiguration  
✅ **Session-Aware** - Perfekte Integration mit longitudinalen Datasets  
✅ **JSON-basierte Reports** - Maschinenlesbar für weitere Verarbeitung  
✅ **Auto-Reprocessing** - Vollautomatischer Workflow  
✅ **Backward Compatible** - Alle existierenden Configs funktionieren  

Der nervige Workflow:
```bash
# Vorher:
./run_bids_apps.py -x config.json
python check_app_output.py /data/bids /data/derivatives
# Manuelle Analyse der Outputs...
# Manuelle Erstellung neuer Config...
./run_bids_apps.py -x reprocess_config.json
```

Wird zu:
```bash
# Jetzt:
./run_bids_apps.py -x config.json --reprocess-missing
# ✨ DONE! ✨
```
