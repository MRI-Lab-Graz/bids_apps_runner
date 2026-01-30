#!/usr/bin/env python3
"""
Batch HPC Job Submission Helper

Simplifies submitting multiple subjects to HPC at once from the GUI or CLI.
"""

import os
import sys
import json
import argparse
import subprocess
import logging
from pathlib import Path
from typing import List, Optional
from hpc_datalad_runner import DataLadHPCScriptGenerator, generate_script, submit_job


def setup_logging(log_level="INFO"):
    """Setup logging."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def discover_subjects(bids_dir: str, prefix: str = "sub-") -> List[str]:
    """Discover subjects in a BIDS directory.

    Args:
        bids_dir: Path to BIDS directory
        prefix: Subject prefix (default: "sub-")

    Returns:
        Sorted list of subject IDs
    """
    subjects = []
    try:
        for entry in os.listdir(bids_dir):
            if entry.startswith(prefix) and os.path.isdir(
                os.path.join(bids_dir, entry)
            ):
                subjects.append(entry)
    except Exception as e:
        logging.error(f"Error discovering subjects: {e}")

    return sorted(subjects)


def submit_batch_jobs(
    config_path: str,
    subjects: Optional[List[str]] = None,
    script_dir: str = "/tmp/hpc_scripts",
    logs_dir: str = "logs",
    dry_run: bool = False,
    max_jobs: Optional[int] = None,
    submit: bool = True,
) -> dict:
    """Submit multiple subjects as HPC jobs.

    Args:
        config_path: Path to HPC config JSON
        subjects: List of subjects to process (if None, auto-discover)
        script_dir: Directory to save SLURM scripts
        logs_dir: Directory for SLURM logs
        dry_run: Show what would be done
        max_jobs: Maximum number of jobs to submit
        submit: Actually submit jobs (False = just generate scripts)

    Returns:
        Dictionary with submission results
    """
    # Load config
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except Exception as e:
        logging.error(f"Failed to load config: {e}")
        return {"success": False, "error": str(e)}

    # Create script directory
    script_path = Path(script_dir)
    script_path.mkdir(parents=True, exist_ok=True)

    # Create logs directory
    logs_path = Path(logs_dir)
    logs_path.mkdir(parents=True, exist_ok=True)

    # Update config with log directory
    if "hpc" not in config:
        config["hpc"] = {}
    config["hpc"]["output_log"] = str(logs_path / "slurm-%j.out")
    config["hpc"]["error_log"] = str(logs_path / "slurm-%j.err")

    # Discover subjects if not provided
    if not subjects:
        bids_dir = config.get("common", {}).get("bids_folder")
        if not bids_dir or not os.path.exists(bids_dir):
            return {
                "success": False,
                "error": "No subjects provided and bids_folder not found",
            }

        subjects = discover_subjects(bids_dir)
        if not subjects:
            return {"success": False, "error": "No subjects found"}

        logging.info(f"Auto-discovered {len(subjects)} subjects")

    # Limit number of jobs if specified
    if max_jobs:
        subjects = subjects[:max_jobs]
        logging.info(f"Limited to {max_jobs} subjects")

    # Generate and submit scripts
    results = {
        "success": True,
        "total": len(subjects),
        "submitted": 0,
        "failed": 0,
        "jobs": [],
        "scripts": [],
    }

    for subject in subjects:
        try:
            # Clean subject ID
            subject_id = subject.replace("sub-", "")

            # Generate script
            script_file = script_path / f"job_{subject_id}.sh"
            script = generate_script(config_path, subject_id, str(script_file))

            results["scripts"].append(str(script_file))
            logging.info(f"Generated script: {script_file}")

            # Submit if requested
            if submit and not dry_run:
                job_id = submit_job(str(script_file), dry_run=False)
                if job_id:
                    results["submitted"] += 1
                    results["jobs"].append(
                        {
                            "subject": subject_id,
                            "job_id": job_id,
                            "script": str(script_file),
                        }
                    )
                    logging.info(f"Submitted job {job_id} for {subject_id}")
                else:
                    results["failed"] += 1
                    logging.error(f"Failed to submit job for {subject_id}")
            elif dry_run:
                results["submitted"] += 1
                results["jobs"].append(
                    {
                        "subject": subject_id,
                        "job_id": "DRY_RUN",
                        "script": str(script_file),
                    }
                )
                logging.info(f"[DRY RUN] Would submit: {script_file}")

        except Exception as e:
            results["failed"] += 1
            logging.error(f"Error processing {subject}: {e}")

    return results


def main():
    """CLI interface."""
    parser = argparse.ArgumentParser(
        description="Submit batch HPC jobs for multiple subjects"
    )

    parser.add_argument("-c", "--config", required=True, help="Path to HPC config JSON")

    parser.add_argument(
        "-s",
        "--subjects",
        nargs="+",
        help="Subject IDs to process (if not specified, auto-discover from BIDS)",
    )

    parser.add_argument(
        "--script-dir",
        default="/tmp/hpc_scripts",
        help="Directory to save SLURM scripts",
    )

    parser.add_argument("--logs-dir", default="logs", help="Directory for SLURM logs")

    parser.add_argument("--max-jobs", type=int, help="Maximum number of jobs to submit")

    parser.add_argument(
        "--generate-only", action="store_true", help="Generate scripts but don't submit"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without doing it",
    )

    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )

    args = parser.parse_args()

    setup_logging(args.log_level)

    # Submit jobs
    results = submit_batch_jobs(
        config_path=args.config,
        subjects=args.subjects,
        script_dir=args.script_dir,
        logs_dir=args.logs_dir,
        dry_run=args.dry_run,
        max_jobs=args.max_jobs,
        submit=not args.generate_only,
    )

    # Print summary
    print("\n" + "=" * 60)
    print("BATCH SUBMISSION SUMMARY")
    print("=" * 60)
    print(f"Total subjects: {results['total']}")
    print(f"Submitted: {results['submitted']}")
    print(f"Failed: {results['failed']}")

    if results["jobs"]:
        print("\nSubmitted jobs:")
        for job in results["jobs"]:
            print(f"  {job['subject']}: {job['job_id']}")

    if results["scripts"]:
        print(f"\nScripts saved to: {args.script_dir}")
        print(f"Number of scripts: {len(results['scripts'])}")

    print("=" * 60)

    # Exit with error code if any failed
    if not results["success"] or results["failed"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
