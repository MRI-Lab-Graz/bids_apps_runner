# BIDS App Runner - HPC Version with DataLad

Diese erweiterte Version des BIDS App Runners ist speziell für High Performance Computing (HPC) Umgebungen mit SLURM und DataLad-Integration entwickelt.

## Neue Features für HPC

### 🚀 SLURM Integration
- Automatische Erstellung von SLURM Job-Scripts
- Parallele Verarbeitung über SLURM statt multiprocessing
- Flexible SLURM-Parameter-Konfiguration
- Job-Monitoring und Status-Verfolgung

### 📦 DataLad Integration
- Automatisches Klonen von BIDS-Repositories
- Branch-basierte Verarbeitung pro Subject
- Versionskontrolle für Input- und Output-Daten
- Git-Annex für große Dateien

### 🌿 Branch-Management
- Separate Branches für jeden Subject (`processing-sub-{ID}`)
- Automatisches Merging der Ergebnisse
- Saubere Trennung von Verarbeitungszuständen

## Installation und Setup

### Voraussetzungen
- SLURM Workload Manager
- DataLad (>= 0.18.0)
- Apptainer/Singularity
- Git und Git-Annex

### HPC-Module (Beispiel)
```bash
module load apptainer/1.2.0
module load datalad/0.19.0
module load git-annex/10.20230408
```

## Konfiguration

### HPC-spezifische Konfiguration (`config_hpc.json`)

```json
{
  "common": {
    "templateflow_dir": "/data/shared/templateflow",
    "container": "/data/containers/qsirecon/qsirecon_1.0.0.sif",
    "work_dir": "/scratch/$USER/bids_work",
    "log_dir": "/scratch/$USER/bids_logs"
  },
  "hpc": {
    "partition": "compute",
    "time": "24:00:00",
    "mem": "32G",
    "cpus": 8,
    "job_name": "qsirecon",
    "modules": ["apptainer/1.2.0", "datalad/0.19.0"],
    "monitor_jobs": true
  },
  "datalad": {
    "input_repo": "git@github.com:your-lab/bids-dataset.git",
    "output_repo": "git@github.com:your-lab/qsirecon-outputs.git",
    "branch_per_subject": true,
    "output_branch": "results"
  }
}
```

### Wichtige Konfigurationsoptionen

#### HPC-Sektion
- `partition`: SLURM-Partition
- `time`: Maximale Laufzeit
- `mem`: Speicher pro Job
- `cpus`: CPU-Kerne pro Job
- `modules`: Zu ladende Module
- `environment`: Umgebungsvariablen

#### DataLad-Sektion
- `input_repo`: BIDS-Datenrepository
- `output_repo`: Ergebnis-Repository
- `branch_per_subject`: Separate Branches pro Subject
- `clone_method`: "clone" oder "install"
- `auto_push`: Automatisches Pushen der Ergebnisse

## Verwendung

### 1. Repository-Setup
```bash
# Input-Repository initialisieren
./manage_datalad_repos.sh init-input -r /data/bids/my_study

# Output-Repository initialisieren  
./manage_datalad_repos.sh init-output -r /data/outputs/qsirecon_results

# Remote-Repositories einrichten
./manage_datalad_repos.sh setup-sibling -r /data/bids/my_study -s git@github.com:lab/study.git
```

### 2. BIDS App ausführen
```bash
# Normale Ausführung
python run_bids_apps_hpc.py -x config_hpc.json

# Dry-Run (zeigt nur die Befehle)
python run_bids_apps_hpc.py -x config_hpc.json --dry-run

# Nur Job-Scripts erstellen ohne Submission
python run_bids_apps_hpc.py -x config_hpc.json --slurm-only

# Spezifische Subjects verarbeiten
python run_bids_apps_hpc.py -x config_hpc.json --subjects sub-001 sub-002

# Pilot-Modus (ein zufälliger Subject)
# Setzen Sie "pilottest": true in der Konfiguration
```

### 3. Job-Monitoring
```bash
# Job-Status überprüfen
squeue -u $USER

# Logs ansehen
tail -f logs/slurm-*.out

# Repository-Status
./manage_datalad_repos.sh status -r /data/outputs/qsirecon_results
```

### 4. Ergebnisse zusammenführen
```bash
# Alle Verarbeitungs-Branches mergen
./manage_datalad_repos.sh merge-results -r /data/outputs/qsirecon_results

# Temporäre Branches aufräumen
./manage_datalad_repos.sh cleanup -r /data/outputs/qsirecon_results
```

## Workflow-Übersicht

### Typischer HPC-Workflow:

1. **Setup**: Repository klonen und konfigurieren
2. **Submission**: Jobs für alle Subjects einreichen
3. **Monitoring**: Job-Status überwachen
4. **Merge**: Ergebnisse zusammenführen
5. **Cleanup**: Temporäre Daten bereinigen

### DataLad-Workflow pro Subject:

1. **Branch**: Neuen processing-Branch erstellen
2. **Get**: Subject-Daten mit `datalad get` laden
3. **Process**: BIDS App über SLURM ausführen
4. **Save**: Ergebnisse mit `datalad save` sichern
5. **Merge**: In Haupt-Branch zusammenführen

## Verzeichnisstruktur

```
/scratch/$USER/bids_work/
├── input_data/           # Geklonte BIDS-Daten
│   ├── sub-001/
│   ├── sub-002/
│   └── derivatives/
├── output_data/          # Ergebnis-Repository
│   ├── derivatives/
│   └── logs/
└── tmp/                  # Temporäre Verarbeitung
    ├── sub-001/
    └── sub-002/
```

## Fehlerbehandlung

### Häufige Probleme:

1. **Job-Fehler**: Log-Dateien in `logs/slurm-*.err` überprüfen
2. **DataLad-Fehler**: Repository-Status mit `datalad status` prüfen
3. **Speicher-Probleme**: Temporäre Verzeichnisse bereinigen

### Debugging:
```bash
# Verbose Logging
python run_bids_apps_hpc.py -x config_hpc.json --log-level DEBUG

# Job-Script ansehen
cat job_sub-001.sh

# SLURM-Job-Details
sacct -j <job_id> --format=JobID,JobName,State,ExitCode
```

## Best Practices

### 📊 Ressourcen-Management
- Angemessene Speicher- und CPU-Anforderungen
- Temporäre Verzeichnisse auf schnellem Storage (/scratch)
- Regelmäßige Bereinigung alter Jobs

### 🔄 Daten-Management
- Regelmäßige Backups der Repositories
- Branch-Strategien für verschiedene Verarbeitungsversionen
- Dokumentation der Verarbeitungsparameter

### 🚨 Monitoring
- Job-Logs regelmäßig überprüfen
- Disk-Usage überwachen
- Failed Jobs analysieren und neu starten

## Erweiterte Features

### Custom SLURM-Templates
```bash
# Eigenes Job-Template verwenden
python run_bids_apps_hpc.py -x config_hpc.json --job-template custom_template.sh
```

### Batch-Processing verschiedener Pipelines
```bash
# Mehrere Konfigurationen nacheinander
for config in config_qsiprep.json config_qsirecon.json; do
    python run_bids_apps_hpc.py -x $config
done
```

### Integration mit anderen HPC-Systemen
- Anpassung für andere Scheduler (PBS, LSF)
- Integration mit Container-Orchestration
- Automatisierung mit Workflow-Managern

## Support und Weiterentwicklung

Dieses Tool ist für wissenschaftliche HPC-Umgebungen optimiert und wird aktiv weiterentwickelt. Feature-Requests und Bug-Reports sind willkommen!

### Bekannte Limitationen
- SLURM-spezifisch (andere Scheduler benötigen Anpassung)
- Erfordert DataLad-Kenntnisse für erweiterte Features
- Git-Annex-Setup kann komplex sein

### Geplante Features
- Integration mit Workflow-Managern (Nextflow, Snakemake)
- Web-basiertes Monitoring-Dashboard
- Automatische QC-Report-Generierung
- Multi-Site-Verarbeitung
