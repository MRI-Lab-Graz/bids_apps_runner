# HPC GUI Implementation (Current)

## Overview

The HPC tab is an Advanced editor for SLURM settings stored in project.json.
Job submission is not performed from this tab.

## Frontend

- Advanced panel for SLURM settings
- Save settings to project.json via /save_project
- Client-side SLURM script preview
- Environment check via /check_hpc_environment

## Backend Endpoints

- GET /check_hpc_environment
- POST /save_project/<project_id>

## Notes

- Power users only.
- Execution happens in Run App or via CLI.
