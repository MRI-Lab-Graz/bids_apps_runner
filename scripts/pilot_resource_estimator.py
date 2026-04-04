#!/usr/bin/env python3
"""Pilot resource estimator for BIDS Apps Runner.

Runs one subject repeatedly with different CPU settings and produces
recommendations for HPC resources (cpus, mem, time, optional gpu).
"""

import argparse
import copy
import csv
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

CPU_FLAGS = {"--nprocs", "--nthreads", "--n_cpus", "--n-cpus"}
OMP_FLAG = "--omp-nthreads"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run a pilot sweep and estimate CPU/GPU/RAM settings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python scripts/pilot_resource_estimator.py "
            "--config configs/134_qsiprep.json "
            "--subject sub-134001 "
            "--nprocs-min 2 --nprocs-max 32 --nprocs-step 2"
        ),
    )

    parser.add_argument(
        "--config",
        "-c",
        required=True,
        help="Path to JSON config (config.json or project.json)",
    )
    parser.add_argument(
        "--subject",
        help="Subject ID (sub-001 or 001). If omitted, first subject in BIDS folder is used.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory for logs/reports (default: logs/pilot_resource_estimator_<timestamp>)",
    )
    parser.add_argument(
        "--nprocs",
        default=None,
        help=(
            "Comma-separated nprocs sweep, e.g. 4,8,16,24. "
            "If omitted, auto-sweep is used from --nprocs-min to detected max cores."
        ),
    )
    parser.add_argument(
        "--nprocs-min",
        type=int,
        default=2,
        help="Lower bound for auto nprocs sweep (default: 2)",
    )
    parser.add_argument(
        "--nprocs-max",
        type=int,
        default=None,
        help="Upper bound for auto nprocs sweep (default: detected max cores)",
    )
    parser.add_argument(
        "--nprocs-step",
        type=int,
        default=1,
        help="Step for auto nprocs sweep (default: 1)",
    )
    parser.add_argument(
        "--omp-nthreads",
        type=int,
        default=None,
        help="Optional --omp-nthreads override for each test run",
    )
    parser.add_argument(
        "--gpu-sample-sec",
        type=int,
        default=5,
        help="GPU monitor sampling interval in seconds",
    )
    parser.add_argument(
        "--min-speedup-gain",
        type=float,
        default=0.10,
        help="If speedup between CPU steps is below this, stop scaling (default: 0.10)",
    )
    parser.add_argument(
        "--mem-safety-factor",
        type=float,
        default=1.30,
        help="Safety multiplier for recommended memory (default: 1.30)",
    )
    parser.add_argument(
        "--time-safety-factor",
        type=float,
        default=1.70,
        help="Safety multiplier for recommended SLURM time (default: 1.70)",
    )
    parser.add_argument(
        "--gpu-util-threshold",
        type=float,
        default=20.0,
        help="Recommend GPU when max utilization reaches this percent (default: 20)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Pass --force to prism_runner for each pilot test",
    )
    parser.add_argument(
        "--keep-temp-configs",
        action="store_true",
        help="Keep generated temporary config files",
    )

    return parser.parse_args()


def load_config(config_path):
    config_file = Path(config_path).expanduser().resolve()
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    with open(config_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "config" in data and isinstance(data["config"], dict):
        data = data["config"]

    return data, config_file


def normalize_subject(subject):
    if not subject:
        return None
    return subject if str(subject).startswith("sub-") else f"sub-{subject}"


def discover_subject(config):
    bids_folder = config.get("common", {}).get("bids_folder")
    if not bids_folder:
        raise ValueError(
            "common.bids_folder is required when --subject is not provided"
        )

    bids_path = Path(bids_folder).expanduser()
    if not bids_path.exists():
        raise FileNotFoundError(f"BIDS folder not found: {bids_path}")

    subjects = sorted([p.name for p in bids_path.glob("sub-*") if p.is_dir()])
    if not subjects:
        raise ValueError(f"No subjects found in BIDS folder: {bids_path}")

    return subjects[0]


def parse_nprocs_list(raw):
    values = []
    for token in str(raw).split(","):
        token = token.strip()
        if not token:
            continue
        value = int(token)
        if value <= 0:
            raise ValueError("nprocs values must be > 0")
        values.append(value)

    if not values:
        raise ValueError("No valid nprocs values provided")

    return sorted(set(values))


def build_auto_nprocs_list(nprocs_min=2, nprocs_max=None, nprocs_step=1):
    detected_max = os.cpu_count() or 1

    if nprocs_step <= 0:
        raise ValueError("--nprocs-step must be > 0")

    start = max(1, int(nprocs_min))
    stop = detected_max if nprocs_max is None else int(nprocs_max)
    stop = min(stop, detected_max)

    if start > stop:
        raise ValueError(
            f"Invalid nprocs bounds: min={start}, max={stop} (detected max cores={detected_max})"
        )

    values = list(range(start, stop + 1, nprocs_step))
    if values[-1] != stop:
        values.append(stop)

    return values, detected_max


def drop_flag(options, flag):
    cleaned = []
    i = 0
    while i < len(options):
        token = str(options[i])
        if token == flag:
            i += 2
            continue
        if token.startswith(flag + "="):
            i += 1
            continue
        cleaned.append(token)
        i += 1
    return cleaned


def set_app_resource_options(config, nprocs, omp_nthreads=None):
    app = config.setdefault("app", {})
    options = [str(x) for x in app.get("options", [])]

    for flag in CPU_FLAGS:
        options = drop_flag(options, flag)
    options.extend(["--nprocs", str(nprocs)])

    if omp_nthreads is not None:
        options = drop_flag(options, OMP_FLAG)
        options.extend([OMP_FLAG, str(omp_nthreads)])

    app["options"] = options


def start_gpu_monitor(output_file, sample_seconds):
    if shutil.which("nvidia-smi") is None:
        return None

    cmd = [
        "nvidia-smi",
        "--query-gpu=timestamp,index,name,utilization.gpu,utilization.memory,memory.used,memory.total",
        "--format=csv,noheader,nounits",
        "-l",
        str(sample_seconds),
    ]

    output_handle = open(output_file, "w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        stdout=output_handle,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )
    return proc, output_handle


def stop_gpu_monitor(monitor):
    if monitor is None:
        return

    proc, output_handle = monitor
    try:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            proc.wait(timeout=5)
    except Exception:
        try:
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass
    finally:
        output_handle.close()


def parse_elapsed_to_seconds(raw):
    raw = raw.strip()
    parts = raw.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(raw)


def parse_time_metrics(stderr_file):
    max_rss_kb = None
    elapsed_seconds = None

    rss_re = re.compile(r"Maximum resident set size \(kbytes\):\s*(\d+)")
    elapsed_re = re.compile(r"Elapsed \(wall clock\) time .*:\s*([0-9:.]+)")

    with open(stderr_file, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            rss_match = rss_re.search(line)
            if rss_match:
                max_rss_kb = int(rss_match.group(1))

            elapsed_match = elapsed_re.search(line)
            if elapsed_match:
                elapsed_seconds = parse_elapsed_to_seconds(elapsed_match.group(1))

    return max_rss_kb, elapsed_seconds


def parse_gpu_metrics(gpu_csv):
    if not Path(gpu_csv).exists():
        return {
            "samples": 0,
            "gpu_util_max": 0.0,
            "gpu_util_avg": 0.0,
            "gpu_mem_used_max_mb": 0.0,
        }

    gpu_utils = []
    gpu_mem_used = []

    with open(gpu_csv, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 7:
                continue

            try:
                util_gpu = float(str(row[3]).strip())
                mem_used = float(str(row[5]).strip())
            except ValueError:
                continue

            gpu_utils.append(util_gpu)
            gpu_mem_used.append(mem_used)

    if not gpu_utils:
        return {
            "samples": 0,
            "gpu_util_max": 0.0,
            "gpu_util_avg": 0.0,
            "gpu_mem_used_max_mb": 0.0,
        }

    return {
        "samples": len(gpu_utils),
        "gpu_util_max": max(gpu_utils),
        "gpu_util_avg": sum(gpu_utils) / len(gpu_utils),
        "gpu_mem_used_max_mb": max(gpu_mem_used) if gpu_mem_used else 0.0,
    }


def format_hms(seconds):
    total = int(math.ceil(max(0, seconds)))
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def pick_recommended_cpu(results, min_gain):
    sorted_results = sorted(results, key=lambda x: x["nprocs"])
    if len(sorted_results) == 1:
        return sorted_results[0]

    prev = sorted_results[0]
    for current in sorted_results[1:]:
        prev_t = prev["wall_seconds"]
        curr_t = current["wall_seconds"]
        if prev_t <= 0 or curr_t <= 0:
            prev = current
            continue

        gain = (prev_t - curr_t) / prev_t
        if gain < min_gain:
            return prev
        prev = current

    return min(sorted_results, key=lambda x: x["wall_seconds"])


def write_report(report_path, args, subject, config_path, results, recommendation):
    lines = []
    lines.append("# Pilot Resource Estimation Report")
    lines.append("")
    lines.append(f"- Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- Config: {config_path}")
    lines.append(f"- Subject: {subject}")
    lines.append(f"- Sweep: {', '.join(str(r['nprocs']) for r in results)}")
    lines.append("")

    lines.append("## Pilot Results")
    lines.append("")
    lines.append(
        "| nprocs | exit | wall_s | max_rss_gib | gpu_util_max_pct | gpu_mem_max_mb |"
    )
    lines.append("|---:|---:|---:|---:|---:|---:|")

    for row in results:
        max_rss_gib = (
            row["max_rss_kb"] / (1024 * 1024) if row["max_rss_kb"] is not None else 0.0
        )
        lines.append(
            "| {n} | {code} | {wall:.1f} | {rss:.2f} | {gpu:.1f} | {gmem:.0f} |".format(
                n=row["nprocs"],
                code=row["returncode"],
                wall=row["wall_seconds"],
                rss=max_rss_gib,
                gpu=row["gpu_util_max"],
                gmem=row["gpu_mem_used_max_mb"],
            )
        )

    lines.append("")
    lines.append("## Recommendation (Current Hardware)")
    lines.append("")
    lines.append(f"- Recommended cpus: {recommendation['cpus']}")
    lines.append(f"- Recommended mem: {recommendation['mem']}")
    lines.append(f"- Recommended time: {recommendation['time']}")
    lines.append(
        "- Recommend GPU request: " + ("yes" if recommendation["use_gpu"] else "no")
    )
    lines.append(
        f"- Based on pilot nprocs={recommendation['source_nprocs']}, "
        f"wall={recommendation['source_wall_seconds']:.1f}s, "
        f"max_rss={recommendation['source_max_rss_gib']:.2f}GiB"
    )
    lines.append("")

    lines.append("## Suggested hpc block")
    lines.append("")
    hpc_block = {
        "partition": "compute",
        "time": recommendation["time"],
        "mem": recommendation["mem"],
        "cpus": recommendation["cpus"],
    }
    if recommendation["use_gpu"]:
        hpc_block["sbatch_gres"] = "gpu:1"

    lines.append("```json")
    lines.append(json.dumps({"hpc": hpc_block}, indent=2))
    lines.append("```")
    lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append(
        "- This estimate is hardware-specific. Re-run one pilot on the target HPC partition before full production."
    )
    lines.append(
        "- Memory recommendation uses --mem-safety-factor and should be increased for multi-subject concurrency."
    )
    lines.append(
        "- If your scheduler requires additional GPU flags, add them as sbatch_* keys in hpc."
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    args = parse_args()

    config, config_path = load_config(args.config)

    if args.nprocs:
        nprocs_values = parse_nprocs_list(args.nprocs)
        detected_max_cores = os.cpu_count() or 1
        sweep_mode = "manual"
    else:
        nprocs_values, detected_max_cores = build_auto_nprocs_list(
            nprocs_min=args.nprocs_min,
            nprocs_max=args.nprocs_max,
            nprocs_step=args.nprocs_step,
        )
        sweep_mode = "auto"

    subject = normalize_subject(args.subject) or discover_subject(config)

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    runner = repo_root / "scripts" / "prism_runner.py"

    if not runner.exists():
        raise FileNotFoundError(f"Runner not found: {runner}")

    time_bin = Path("/usr/bin/time")
    if not time_bin.exists():
        raise FileNotFoundError("/usr/bin/time not found. Please install GNU time.")

    if args.output_dir:
        run_dir = Path(args.output_dir).expanduser()
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = repo_root / "logs" / f"pilot_resource_estimator_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    print(f"Config: {config_path}")
    print(f"Subject: {subject}")
    print(f"Detected max CPU cores: {detected_max_cores}")
    print(f"Sweep mode: {sweep_mode}")
    print(f"nprocs sweep: {nprocs_values}")
    print(f"Output dir: {run_dir}")
    print("")

    results = []

    for nprocs in nprocs_values:
        run_label = f"n{nprocs}"
        print(f"[RUN] nprocs={nprocs}")

        run_config = copy.deepcopy(config)
        run_config.setdefault("common", {})["jobs"] = 1
        set_app_resource_options(run_config, nprocs, args.omp_nthreads)

        temp_config_file = run_dir / f"pilot_config_{run_label}.json"
        with open(temp_config_file, "w", encoding="utf-8") as f:
            json.dump(run_config, f, indent=2)

        stdout_log = run_dir / f"stdout_{run_label}.log"
        stderr_log = run_dir / f"stderr_{run_label}.log"
        gpu_log = run_dir / f"gpu_{run_label}.csv"

        monitor = start_gpu_monitor(gpu_log, args.gpu_sample_sec)

        cmd = [
            str(time_bin),
            "-v",
            sys.executable,
            str(runner),
            "-c",
            str(temp_config_file),
            "--local",
            "--subjects",
            subject,
        ]
        if args.force:
            cmd.append("--force")

        t0 = time.time()
        with (
            open(stdout_log, "w", encoding="utf-8") as out_f,
            open(stderr_log, "w", encoding="utf-8") as err_f,
        ):
            proc = subprocess.run(cmd, cwd=repo_root, stdout=out_f, stderr=err_f)
        wall_fallback = time.time() - t0

        stop_gpu_monitor(monitor)

        max_rss_kb, wall_seconds = parse_time_metrics(stderr_log)
        if wall_seconds is None:
            wall_seconds = wall_fallback

        gpu_metrics = parse_gpu_metrics(gpu_log)

        result = {
            "nprocs": nprocs,
            "returncode": proc.returncode,
            "wall_seconds": float(wall_seconds),
            "max_rss_kb": max_rss_kb,
            "gpu_util_max": gpu_metrics["gpu_util_max"],
            "gpu_util_avg": gpu_metrics["gpu_util_avg"],
            "gpu_mem_used_max_mb": gpu_metrics["gpu_mem_used_max_mb"],
            "gpu_samples": gpu_metrics["samples"],
            "stdout_log": str(stdout_log),
            "stderr_log": str(stderr_log),
            "gpu_log": str(gpu_log),
            "config_file": str(temp_config_file),
        }
        results.append(result)

        print(
            "  exit={code} wall={wall:.1f}s max_rss={rss}KB gpu_max={gpu:.1f}%".format(
                code=result["returncode"],
                wall=result["wall_seconds"],
                rss=result["max_rss_kb"] if result["max_rss_kb"] is not None else 0,
                gpu=result["gpu_util_max"],
            )
        )

        if not args.keep_temp_configs:
            try:
                temp_config_file.unlink(missing_ok=True)
            except Exception:
                pass

    successful = [r for r in results if r["returncode"] == 0]
    if not successful:
        report_json = run_dir / "pilot_results.json"
        with open(report_json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
        raise RuntimeError(
            "All pilot runs failed. Inspect stderr logs in "
            f"{run_dir} and adjust app options before estimating resources."
        )

    recommended_run = pick_recommended_cpu(successful, args.min_speedup_gain)

    source_rss_kb = recommended_run["max_rss_kb"] or 0
    source_rss_gib = source_rss_kb / (1024 * 1024)
    recommended_mem_gib = max(2, math.ceil(source_rss_gib * args.mem_safety_factor))
    recommended_time = format_hms(
        recommended_run["wall_seconds"] * args.time_safety_factor
    )
    use_gpu = recommended_run["gpu_util_max"] >= args.gpu_util_threshold

    recommendation = {
        "cpus": recommended_run["nprocs"],
        "mem": f"{recommended_mem_gib}G",
        "time": recommended_time,
        "use_gpu": use_gpu,
        "source_nprocs": recommended_run["nprocs"],
        "source_wall_seconds": recommended_run["wall_seconds"],
        "source_max_rss_gib": source_rss_gib,
    }

    report_md = run_dir / "pilot_resource_report.md"
    report_json = run_dir / "pilot_results.json"

    write_report(report_md, args, subject, config_path, results, recommendation)

    payload = {
        "subject": subject,
        "config": str(config_path),
        "results": results,
        "recommendation": recommendation,
    }
    with open(report_json, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print("\nRecommendation:")
    print(f"  cpus: {recommendation['cpus']}")
    print(f"  mem:  {recommendation['mem']}")
    print(f"  time: {recommendation['time']}")
    print("  gpu:  " + ("request" if recommendation["use_gpu"] else "not required"))
    print(f"\nReport: {report_md}")
    print(f"Data:   {report_json}")


if __name__ == "__main__":
    main()
