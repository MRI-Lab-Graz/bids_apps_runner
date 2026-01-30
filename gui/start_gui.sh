#!/bin/bash
# Start script for BIDS App Runner GUI

# Get the script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( dirname "$SCRIPT_DIR" )"

# Set PYTHONPATH to include the project directory
export PYTHONPATH="$PROJECT_DIR:$PYTHONPATH"

echo "--------------------------------------------------------"
echo "  BIDS App Runner GUI - Starting..."
echo "--------------------------------------------------------"

# Run the app
python3 "$PROJECT_DIR/prism_app_runner.py"
