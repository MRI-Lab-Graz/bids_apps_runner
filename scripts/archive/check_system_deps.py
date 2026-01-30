#!/usr/bin/env python3
import shutil
import sys
import subprocess
import os
import platform


def _fix_system_path():
    """Ensure common paths are in PATH, especially on macOS."""
    extra_paths = [
        "/usr/local/bin",
        "/opt/homebrew/bin",
        "/opt/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    current_path = os.environ.get("PATH", "").split(os.pathsep)
    path_changed = False
    for p in extra_paths:
        if p not in current_path and os.path.exists(p):
            current_path.append(p)
            path_changed = True

    if path_changed:
        os.environ["PATH"] = os.pathsep.join(current_path)


_fix_system_path()


def check_command(cmd, name):
    path = shutil.which(cmd)
    if path:
        print(f"[OK] {name} found at: {path}")
        # Try to get version
        try:
            if cmd == "docker":
                result = subprocess.run(
                    [cmd, "--version"], capture_output=True, text=True, timeout=5
                )
            else:
                result = subprocess.run(
                    [cmd, "version" if cmd != "datalad" else "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
            version = result.stdout.strip() or result.stderr.strip()
            print(f"     Version: {version}")
        except Exception as e:
            print(f"     Could not retrieve version: {e}")
        return True
    else:
        print(f"[ERROR] {name} ('{cmd}') NOT found in PATH")
        return False


def main():
    print("=== BIDS App Runner - System Dependency Check ===\n")

    results = {}
    results["docker"] = check_command("docker", "Docker")
    if results["docker"]:
        try:
            subprocess.run(
                ["docker", "info"], capture_output=True, timeout=2, check=True
            )
            print("     [OK] Docker daemon is running.")
            results["docker_running"] = True
        except subprocess.CalledProcessError:
            print("     [ERROR] Docker is installed but the DAEMON IS NOT RUNNING.")
            results["docker_running"] = False
        except subprocess.TimeoutExpired:
            print("     [ERROR] Docker daemon is not responding (timeout).")
            results["docker_running"] = False

    print("-" * 40)
    results["apptainer"] = check_command("apptainer", "Apptainer")
    if not results["apptainer"]:
        results["singularity"] = check_command("singularity", "Singularity")
    print("-" * 40)
    results["datalad"] = check_command("datalad", "DataLad")

    print("\nSummary:")
    docker_ready = results.get("docker") and results.get("docker_running")
    apptainer_ready = results.get("apptainer") or results.get("singularity")

    if docker_ready or apptainer_ready:
        print("✓ At least one container engine is available and ready.")
    elif results.get("docker") and not results.get("docker_running"):
        print(
            "✗ Docker is installed but NOT RUNNING. Please start Docker (Docker Desktop on macOS/Windows)."
        )
    else:
        print(
            "✗ NO container engine found! You need Docker, Apptainer, or Singularity to run BIDS Apps."
        )

    if results["datalad"]:
        print("✓ DataLad is available for automatic data management.")
    else:
        print(
            "! DataLad is NOT available. Automatic retrieval of DataLad datasets will be skipped."
        )

    # Return non-zero if no container engine found
    if not (
        results["docker"] or results.get("apptainer") or results.get("singularity")
    ):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
