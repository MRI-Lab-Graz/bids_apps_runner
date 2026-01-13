# Changelog

## v1.1.1
- Fixed GitHub Actions workflow: Updated retired macOS Intel runner from `macos-13` to `macos-15`.

## v1.1.0
- Added system dependency checker for Docker, Apptainer/Singularity, and DataLad.
- New standalone `check_system_deps.py` script for command-line validation.
- Integrated system status badges in GUI navbar.
- Added host-side validation for container engines before starting processing jobs.
- Improved Docker check to verify if the daemon/service is actually running (vital for macOS/Windows).
- Added a "Quit App" button to the GUI to easily shut down the backend server.
- Improved installation scripts to handle project root discovery and dynamic requirements.
- Added progress feedback for Docker image pulls directly in the GUI console.
- Flexible configuration saving: choosing custom directories for JSON configs.

## v1.0.0
- First official release of BIDS App Runner.
- Added Docker Hub engine support alongside Apptainer, including tag discovery and image pull from the GUI.
- Improved GUI responsiveness and container option parsing with clearer error messages and live log streaming.
- Added background Docker pull logging to the in-app console.
- Introduced GitHub Actions workflow to build Windows (.exe) and macOS (.dmg) bundles via PyInstaller.
