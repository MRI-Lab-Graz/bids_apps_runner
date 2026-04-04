#!/usr/bin/env python3
"""
run_bids_apps.py - Backward compatibility wrapper

This is a compatibility shim that forwards arguments to prism_runner.py.
The actual execution logic has been refactored into the PRISM unified runner system.

Usage:
    run_bids_apps.py -c config.json                    # Auto-detect mode
    run_bids_apps.py -c config.json --hpc             # Force HPC mode
    run_bids_apps.py -c config.json --local           # Force local mode
    run_bids_apps.py -c config.json --dry-run         # Test configuration
    run_bids_apps.py -c config.json --subjects sub-01 # Specific subjects

Author: BIDS Apps Runner Team (PRISM Edition)
Version: 3.0.0
"""

import sys
import os

# Add the scripts directory to the path to import prism_runner
scripts_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, scripts_dir)

# Import and run prism_runner with the same arguments
from prism_runner import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
