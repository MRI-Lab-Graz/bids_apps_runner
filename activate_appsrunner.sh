#!/bin/bash
# Activation script for BIDS App Runner environment

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PATH="$PROJECT_ROOT/.appsrunner"

if [ ! -d "$VENV_PATH" ]; then
    echo "Error: Virtual environment not found at $VENV_PATH"
    echo "Please run ./scripts/install.sh first"
    exit 1
fi

source "$VENV_PATH/bin/activate"

echo "BIDS App Runner environment activated!"
echo "Python: $(which python)"
echo "Python version: $(python --version)"
echo ""
echo "Available scripts:"
echo "  - scripts/run_bids_apps.py       (Local/cluster processing)"
echo "  - scripts/run_bids_apps_hpc.py   (HPC/SLURM processing)"
echo "  - prism_app_runner.py            (Web GUI)"
echo ""
echo "To deactivate, run: deactivate"
