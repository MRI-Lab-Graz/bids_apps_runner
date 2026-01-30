# Implementation Summary (Current)

## Core Workflow

- GUI edits project.json
- Run App executes using saved settings
- Container options are auto-loaded once and then locked
- HPC settings are edited in HPC -> Advanced and stored in project.json

## Entry Points

- GUI: prism_app_runner.py
- CLI: scripts/prism_runner.py

## Configuration

Top-level sections:

- common
- app
- hpc (optional)
- datalad (optional)

## Notes

Legacy HPC script generation endpoints are no longer used in the GUI.
