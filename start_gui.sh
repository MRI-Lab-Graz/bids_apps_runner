#!/bin/bash
# Start script for BIDS App Runner GUI

# Get the project directory (same as this script's location)
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
VENV_PATH="$PROJECT_DIR/.appsrunner"

# Activate virtual environment
if [ ! -d "$VENV_PATH" ]; then
    echo "Error: Virtual environment not found at $VENV_PATH"
    echo "Please run ./scripts/install.sh first"
    exit 1
fi
source "$VENV_PATH/bin/activate"

# Set PYTHONPATH to include the project directory
export PYTHONPATH="$PROJECT_DIR:$PYTHONPATH"

echo "--------------------------------------------------------"
echo "  BIDS App Runner GUI - Starting..."
echo "--------------------------------------------------------"

# Site settings (scratch root, remote DataLad server, data dir) -- see
# site.env.example. Missing files are fine: defaults are per-user.
# shellcheck source=scripts/load_site_env.sh
source "$PROJECT_DIR/scripts/load_site_env.sh" "$PROJECT_DIR"

# Run the app. (No password login; access is gated by the per-run token
# printed below, which is always required.)
PRISM_GUI_DISABLE_LOGIN=1 python "$PROJECT_DIR/prism_app_runner.py"
