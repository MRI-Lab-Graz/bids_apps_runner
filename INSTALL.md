# Installation and Setup Guide

## Quick Start

1. **Install UV** (if not already installed):
   ```bash
   # macOS/Linux
   curl -LsSf https://astral.sh/uv/install.sh | sh
   
   # Or visit: https://docs.astral.sh/uv/getting-started/installation/
   ```

2. **Run the installation script**:
   ```bash
   # Core installation (recommended)
   ./install.sh
   
   # Full installation (includes development tools)
   ./install.sh --full
   ```

3. **Activate the environment**:
   ```bash
   source .appsrunner/bin/activate
   # OR
   source activate_appsrunner.sh
   ```

4. **Test the installation**:
   ```bash
   python run_bids_apps.py --help
   python run_bids_apps_hpc.py --help
   ```

## Files Created

### `.bidsignore`
Excludes common system files and development artifacts from BIDS validation:
- System files (`.DS_Store`, `__pycache__`, etc.)
- Version control files (`.git/`, `.gitignore`)
- Virtual environments and build artifacts
- Log files and temporary files
- Container files and scripts

### `install.sh`
Automated installation script that:
- Checks for UV and Python 3.8+ requirements
- Creates a virtual environment (`.appsrunner`)
- Installs Python dependencies
- Creates an activation script
- Verifies the installation

### `requirements-core.txt`
Minimal dependencies for core functionality:
- `colorlog` - Enhanced logging with colors
- `tqdm` - Progress bars
- `psutil` - System process monitoring
- `jsonschema` - Configuration validation

### `requirements.txt`
Full dependencies including development tools:
- All core dependencies
- Testing tools (pytest, coverage)
- Code quality tools (black, flake8, mypy)
- Documentation tools (sphinx)

## System Dependencies

Some dependencies require system-level installation:

### Required for All Functionality
- **Apptainer/Singularity**: Container execution
  ```bash
  # Installation varies by system
  # See: https://apptainer.org/docs/user/main/quick_start.html
  ```

### Required for HPC Version
- **SLURM Tools**: Job scheduling
  ```bash
  # Usually installed on HPC systems
  # Commands: sbatch, squeue, scancel
  ```

- **DataLad**: Data management
  ```bash
  # Recommended installation via conda
  conda install -c conda-forge datalad
  ```

- **Git/Git-annex**: Version control
  ```bash
  # Most systems have git, git-annex may need installation
  # macOS: brew install git-annex
  # Linux: apt-get install git-annex (Ubuntu/Debian)
  ```

## Environment Management

### Activation
```bash
# Method 1: Direct activation
source .appsrunner/bin/activate

# Method 2: Using provided script
source activate_appsrunner.sh
```

### Deactivation
```bash
deactivate
```

### Updating Dependencies
```bash
# Activate environment first
source .appsrunner/bin/activate

# Update packages
uv pip install --upgrade -r requirements-core.txt

# Or for full requirements
uv pip install --upgrade -r requirements.txt
```

## Troubleshooting

### UV Not Found
```bash
# Install UV
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc  # or ~/.zshrc
```

### Python Version Issues
```bash
# Check available Python versions
python3 --version
python3.8 --version  # etc.

# Install specific version (example for Ubuntu)
sudo apt-get install python3.8 python3.8-venv
```

### Permission Issues
```bash
# Make install script executable
chmod +x install.sh
```

### Environment Conflicts
```bash
# Remove existing environment
rm -rf .appsrunner activate_appsrunner.sh

# Reinstall
./install.sh
```
