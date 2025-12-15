Overview
========

This repository provides two main workflows:

1. **Build Apptainer containers from Docker images**

   Use :file:`build_apptainer.sh` to convert a Docker image (from Docker Hub or a local Dockerfile) into an Apptainer image (``.sif``).

2. **Run BIDS Apps based on JSON configuration files**

   Use :file:`run_bids_apps.py` to execute a BIDS App container against a BIDS dataset with all paths/options specified in a JSON config.

Key ideas
---------

- **Reproducible runs**: everything needed to run a container is encoded in the config JSON.
- **Consistent mounting**: common mounts map your BIDS folder, derivatives output folder, TemplateFlow, and a per-subject work directory.
- **Dry-run safety**: use ``--dry-run`` to print the exact ``apptainer`` command and perform a fast validation.

What’s in the repo
------------------

- :file:`build_apptainer.sh` – build ``.sif`` images from Docker.
- :file:`run_bids_apps.py` – local runner (multiprocessing) driven by a JSON config.
- :file:`run_bids_apps_hpc.py` – SLURM/DataLad-oriented runner for HPC.
- :file:`check_app_output.py` – validate derivatives and generate "missing subject" reports.

