# Changelog

## v1.1.0
- Added system dependency checker for Docker, Apptainer/Singularity, and DataLad.
- New standalone `check_system_deps.py` script for command-line validation.
- Integrated system status badges in GUI navbar.
- Added host-side validation for container engines before starting processing jobs.
- Improved Docker check to verify if the daemon/service is actually running (vital for macOS/Windows).

## v1.0.0
- First official release of BIDS App Runner.
- Added Docker Hub engine support alongside Apptainer, including tag discovery and image pull from the GUI.
- Improved GUI responsiveness and container option parsing with clearer error messages and live log streaming.
- Added background Docker pull logging to the in-app console.
- Introduced GitHub Actions workflow to build Windows (.exe) and macOS (.dmg) bundles via PyInstaller.
