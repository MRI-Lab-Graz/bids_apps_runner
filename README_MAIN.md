# BIDS App Runner

**Version 2.0.0** - Production-ready BIDS App execution with advanced features

Ein flexibles Python-Tool zur Ausführung von BIDS Apps mit JSON-Konfiguration, DataLad-Unterstützung und erweiterten Features.

## 🎯 Features

- **🔄 Automatische DataLad-Erkennung** - Unterstützt sowohl Standard-BIDS-Ordner als auch DataLad-Datasets
- **⚡ Parallele Verarbeitung** - Multithreading für effiziente Batch-Verarbeitung  
- **🐛 Debug-Modus** - Detaillierte Container-Logs für Troubleshooting
- **🎛️ HPC-Integration** - SLURM-Job-Scheduling für Cluster-Umgebungen
- **✅ Robuste Validierung** - Umfassende Fehlerbehandlung und Konfigurationsprüfung
- **📊 Ausführliche Berichte** - Zusammenfassungen mit Timing und Erfolgsraten
- **🔍 Integrierte Output-Validierung** - Automatische Überprüfung und Reprocessing fehlender Outputs
- **📅 Session-Awareness** - Intelligente Erkennung longitudinaler Datasets mit Multi-Session Support

## 🚀 Schnellstart

### Installation

```bash
# Automatische Installation mit UV
./install.sh

# Oder manuell
python -m venv .appsrunner
source .appsrunner/bin/activate
pip install -r requirements.txt
```

### Beispielkonfiguration

```bash
# Beispielkonfiguration kopieren und anpassen
cp config_example.json my_config.json
```

## 📚 Dokumentation

### Standard-Script (Lokale Ausführung)

**Datei:** `run_bids_apps.py`  
**Dokumentation:** [README_STANDARD.md](README_STANDARD.md)

```bash
# Einfache Ausführung
./run_bids_apps.py -x config.json

# Mit spezifischen Subjects
./run_bids_apps.py -x config.json --subjects sub-001 sub-002

# Debug-Modus für Troubleshooting  
./run_bids_apps.py -x config.json --debug

# Integrierte Validierung und Reprocessing
./run_bids_apps.py -x config.json --validate                    # Verarbeitung + Validierung
./run_bids_apps.py -x config.json --validate-only               # Nur Validierung
./run_bids_apps.py -x config.json --reprocess-missing           # Auto-Reprocessing fehlender Outputs
```

### HPC-Script (SLURM-Cluster)

**Datei:** `run_bids_apps_hpc.py`  
**Dokumentation:** [README_HPC.md](README_HPC.md)

```bash
# SLURM-Jobs für alle Subjects einreichen
./run_bids_apps_hpc.py -x config_hpc.json

# Mit Debug-Logs in SLURM-Jobs
./run_bids_apps_hpc.py -x config_hpc.json --debug

# Nur Job-Scripts erstellen (ohne Einreichung)
./run_bids_apps_hpc.py -x config_hpc.json --slurm-only
```

## 🔧 Wichtige Features

### Integrierte Output-Validierung

Nahtlose Integration von Verarbeitung und Validierung:

- **Automatische Pipeline-Erkennung** für fMRIPrep, QSIPrep, FreeSurfer, QSIRecon
- **Session-aware Validierung** für longitudinale Datasets
- **Intelligentes Reprocessing** nur für fehlende Sessions/Subjects
- **App-spezifische Session-Unterstützung** (QSIPrep: `--session-id`, fMRIPrep: Subject-level)

### DataLad Integration

Beide Scripts erkennen automatisch DataLad-Datasets und bieten:

- **Automatische Datenabfrage** mit `datalad get`
- **Ergebnis-Versionierung** mit `datalad save`
- **Nahtloser Fallback** auf Standard-BIDS-Ordner
- **Keine Konfigurationsänderungen erforderlich**

### Debug-Modus

Erweiterte Debugging-Funktionen:

- **Echtzeit-Container-Output** Streaming
- **Detaillierte Log-Dateien** pro Subject
- **Fehler-Kontext** mit letzten 20 Zeilen von stderr
- **Performance-Timing** Informationen

### Production-Ready Features

- **Umfassende Fehlerbehandlung** mit detaillierten Nachrichten
- **Signal-Handling** für graceful shutdown
- **Konfigurationsvalidierung** mit hilfreichen Fehlermeldungen
- **Strukturiertes Logging** mit Timestamps und Levels
- **Performance-Monitoring** und Statistiken

## ⚙️ Konfiguration

### Basis-Konfiguration

```json
{
  "common": {
    "bids_folder": "/path/to/bids/dataset",
    "output_folder": "/path/to/output",
    "tmp_folder": "/tmp/bids_processing",
    "container": "/path/to/app.sif",
    "templateflow_dir": "/path/to/templateflow"
  },
  "app": {
    "analysis_level": "participant",
    "options": ["--skip-bids-validation"]
  }
}
```

### HPC-Konfiguration (zusätzliche Abschnitte)

```json
{
  "hpc": {
    "job_name": "bids_app",
    "partition": "compute",
    "time": "24:00:00",
    "mem": "32GB",
    "cpus": 8
  },
  "datalad": {
    "input_url": "https://example.com/dataset.git",
    "output_url": "https://example.com/results.git"
  }
}
```

## 🔄 Workflow-Auswahl

**Wählen Sie das passende Script für Ihre Umgebung:**

- **Local/Workstation**: Verwenden Sie `run_bids_apps.py` (siehe [README_STANDARD.md](README_STANDARD.md))
- **HPC/Cluster**: Verwenden Sie `run_bids_apps_hpc.py` (siehe [README_HPC.md](README_HPC.md))

## 📋 Systemanforderungen

- **Python 3.8+**
- **Apptainer/Singularity**
- **DataLad** (optional, für erweiterte Features)
- **SLURM** (für HPC-Script)

## 📁 Dateien

- `run_bids_apps.py` - Standard-Script mit DataLad Auto-Erkennung
- `run_bids_apps_hpc.py` - HPC-Script für SLURM-Cluster
- `config.json` - Beispielkonfiguration
- `install.sh` - Automatische Installation
- `README_STANDARD.md` - Vollständige Dokumentation für Standard-Script
- `README_HPC.md` - Vollständige Dokumentation für HPC-Script

## 🆘 Support

Bei Problemen:

1. Verwenden Sie den **Debug-Modus** (`--debug`)
2. Prüfen Sie die **Log-Dateien** im `logs/` Verzeichnis
3. Konsultieren Sie die entsprechende Dokumentation
4. Prüfen Sie die **Konfigurationsvalidierung**

---

**Entwickelt für robuste, production-ready BIDS App-Ausführung mit erweiterten Features.**
