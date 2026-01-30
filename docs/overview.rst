Overview
========

BIDS Apps Runner is a GUI-first workflow with a CLI fallback for running BIDS Apps locally or on HPC.
Project state is stored in project.json and is edited from the GUI.

Core workflows
--------------

1. **Build containers** (Apptainer from Docker)
2. **Configure and run BIDS Apps** using project.json
3. **Validate outputs** and reprocess missing subjects
4. **Manage HPC settings** in project.json (Advanced section in GUI)

Key behaviors
-------------

- **Project-centric**: each project is a folder containing project.json
- **Container option locking**: options are auto-discovered the first time; once saved, they stay fixed unless the container path changes
- **HPC settings are explicit**: SLURM parameters live under the hpc section and are edited by power users

Repo components (high-level)
----------------------------

- build_apptainer.sh – build .sif images from Docker
- scripts/prism_runner.py – CLI runner
- scripts/check_app_output.py – output validation
- prism_app_runner.py – GUI server
- templates/index.html – GUI front-end

