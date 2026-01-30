#!/usr/bin/env python3
"""
PRISM Runner - Unified BIDS App Execution Engine

Supports both local/cluster execution and HPC/SLURM environments
with automatic mode detection.

Usage:
    prism_runner.py -c config.json                    # Auto-detect mode
    prism_runner.py -c config.json --hpc             # Force HPC mode
    prism_runner.py -c config.json --local           # Force local mode
    prism_runner.py -c config.json --dry-run         # Test configuration
    prism_runner.py -c config.json --subjects sub-01 # Specific subjects

Author: BIDS Apps Runner Team (PRISM Edition)
Version: 3.0.0
"""

import sys
import os
import argparse
import logging
from pathlib import Path

# Import PRISM modules
try:
    from prism_core import (
        setup_logging,
        read_config,
        detect_execution_mode,
        validate_common_config,
        validate_app_config,
        validate_hpc_config,
        fix_system_path,
        print_summary,
    )
except ImportError:
    print("ERROR: Could not import prism_core module")
    print("Make sure all prism_*.py files are in the same directory")
    sys.exit(1)


def parse_arguments():
    """Parse command line arguments.

    Returns:
        Parsed arguments namespace
    """
    parser = argparse.ArgumentParser(
        description="PRISM Runner - Unified BIDS App Execution Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect mode from config
  %(prog)s -c config.json
  
  # Force specific mode
  %(prog)s -c config.json --hpc
  %(prog)s -c config.json --local
  
  # Common operations
  %(prog)s -c config.json --dry-run
  %(prog)s -c config.json --subjects sub-001 sub-002
  %(prog)s -c config.json --force
  %(prog)s -c config.json --debug
  
  # Local-specific
  %(prog)s -c config.json --pilot
  %(prog)s -c config.json --validate
  %(prog)s -c config.json --jobs 4
  
  # HPC-specific  
  %(prog)s -c config.json --hpc --slurm-only
  %(prog)s -c config.json --hpc --monitor

For detailed documentation, see README.md
        """,
    )

    # Required arguments
    parser.add_argument(
        "-c",
        "--config",
        required=True,
        help="Path to JSON configuration file (required)",
    )

    # Execution mode (optional - auto-detect if not specified)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--local", action="store_true", help="Force local/cluster execution mode"
    )
    mode_group.add_argument(
        "--hpc", action="store_true", help="Force HPC/SLURM execution mode"
    )

    # Common arguments (work in both modes)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level (default: INFO)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be executed without running it",
    )
    parser.add_argument(
        "--subjects",
        nargs="+",
        help="Process only specified subjects (e.g., sub-001 sub-002)",
    )
    parser.add_argument(
        "--force", action="store_true", help="Force reprocessing even if output exists"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode with detailed container logs",
    )
    parser.add_argument("--version", action="version", version="PRISM Runner 3.0.0")

    # Local-mode specific arguments
    local_group = parser.add_argument_group("local mode options")
    local_group.add_argument(
        "--jobs", type=int, help="Number of parallel jobs (local mode only)"
    )
    local_group.add_argument(
        "--pilot",
        action="store_true",
        help="Run pilot mode - process one random subject (local mode only)",
    )
    local_group.add_argument(
        "--validate",
        action="store_true",
        help="Validate outputs after processing (local mode only)",
    )
    local_group.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate, don't process (local mode only)",
    )
    local_group.add_argument(
        "--reprocess-missing",
        action="store_true",
        help="Automatically reprocess subjects with missing outputs (local mode only)",
    )
    local_group.add_argument(
        "--reprocess-from-json",
        type=Path,
        help="Reprocess subjects from validation JSON report (local mode only)",
    )
    local_group.add_argument(
        "--pipeline",
        help="Specify pipeline when using --reprocess-from-json (local mode only)",
    )

    # HPC-mode specific arguments
    hpc_group = parser.add_argument_group("HPC mode options")
    hpc_group.add_argument(
        "--slurm-only",
        action="store_true",
        help="Generate SLURM scripts without submitting (HPC mode only)",
    )
    hpc_group.add_argument(
        "--monitor",
        action="store_true",
        help="Monitor submitted jobs until completion (HPC mode only)",
    )
    hpc_group.add_argument(
        "--no-datalad",
        action="store_true",
        help="Disable DataLad operations (HPC mode only)",
    )

    # Background execution
    parser.add_argument(
        "--nohup",
        action="store_true",
        help="Run in background (nohup mode) to survive connection drops",
    )

    # Show help if no arguments
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    return parser.parse_args()


def main():
    """Main entry point for PRISM Runner."""

    # Fix system PATH
    fix_system_path()

    # Parse arguments
    args = parse_arguments()

    # Handle nohup mode - relaunch in background
    if args.nohup:
        import subprocess
        from datetime import datetime

        # Remove --nohup from arguments to prevent infinite recursion
        cmd_args = [arg for arg in sys.argv if arg != "--nohup"]

        # Create a log file for the background process
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nohup_log = f"nohup_prism_runner_{timestamp}.log"

        print("🚀 Launching in background (nohup mode)...")
        print(f"📄 Output will be redirected to: {nohup_log}")

        with open(nohup_log, "w") as log_f:
            subprocess.Popen(
                [sys.executable] + cmd_args,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setpgrp,  # Detach from terminal group
            )

        print("✅ Process started in background. You can disconnect now.")
        print(f"👀 Monitor progress with: tail -f {nohup_log}")
        sys.exit(0)

    # Setup logging
    log_file = setup_logging(args.log_level)

    try:
        # Load configuration
        config = read_config(args.config)

        # Detect or force execution mode
        force_mode = None
        if args.local:
            force_mode = "local"
        elif args.hpc:
            force_mode = "hpc"

        execution_mode = detect_execution_mode(config, force_mode)

        # Validate configuration
        logging.info("Validating configuration...")
        validate_common_config(config)
        validate_app_config(config)

        if execution_mode == "hpc":
            validate_hpc_config(config)

        # Import and execute appropriate mode
        if execution_mode == "local":
            logging.info("=== EXECUTING IN LOCAL MODE ===")
            from prism_local import execute_local

            result = execute_local(config, args)
        else:
            logging.info("=== EXECUTING IN HPC MODE ===")
            from prism_hpc import execute_hpc

            result = execute_hpc(config, args)

        # Print summary
        if result:
            logging.info("Execution completed successfully")
            return 0
        else:
            logging.error("Execution completed with errors")
            return 1

    except KeyboardInterrupt:
        logging.warning("\nExecution interrupted by user")
        return 130
    except Exception as e:
        logging.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
