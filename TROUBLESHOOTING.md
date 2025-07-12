# UV Installation Troubleshooting Guide

## Common Issues and Solutions

### 1. "No interpreter found for executable name" Error

**Problem**: UV cannot find the Python interpreter even though Python is installed.

**Error message**:
```
× No interpreter found for executable name `[SUCCESS] Found suitable Python: python3 (version 3.11)
  │ python3` in managed installations or search path
```

**Root Cause**: The install script's output was being mixed, causing UV to receive corrupted Python executable name.

**Solution**: Updated install script with proper output handling:
- Separated success messages from return values
- Used full Python executable paths
- Added error checking for each step

### 2. Updated Install Script Features

The fixed `install.sh` now includes:

#### Better Python Detection
```bash
# Redirects success message to stderr to avoid mixing with return value
print_success "Found suitable Python: $cmd (version $version)" >&2

# Uses full path to Python executable
python_path=$(which "$python_cmd")
uv venv "$VENV_NAME" --python "$python_path"
```

#### Improved UV Integration
```bash
# Direct installation into virtual environment
uv pip install --python "$VENV_NAME/bin/python" -r "$req_file"
```

#### Better Error Handling
```bash
if [ $? -eq 0 ]; then
    print_success "Virtual environment created successfully"
else
    print_error "Failed to create virtual environment"
    exit 1
fi
```

### 3. Manual Installation Steps (if script still fails)

If the automated script continues to have issues, follow these manual steps:

#### Step 1: Check Python Installation
```bash
# Verify Python is available
python3 --version
which python3

# Should output something like:
# Python 3.11.x
# /usr/bin/python3
```

#### Step 2: Create Virtual Environment Manually
```bash
# Create virtual environment with UV
uv venv .appsrunner --python $(which python3)

# Alternative: Use built-in venv if UV fails
python3 -m venv .appsrunner
```

#### Step 3: Activate Environment
```bash
source .appsrunner/bin/activate
```

#### Step 4: Install Dependencies
```bash
# With UV (preferred)
uv pip install -r requirements-core.txt

# Alternative: Use pip
pip install -r requirements-core.txt
```

#### Step 5: Verify Installation
```bash
python --version
python run_bids_apps.py --help
python run_bids_apps_hpc.py --help
```

### 4. System-Specific Solutions

#### Rocky Linux / CentOS / RHEL
```bash
# Install Python 3.8+ if not available
sudo dnf install python3 python3-pip python3-venv

# Install UV
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

#### Ubuntu / Debian
```bash
# Install Python 3.8+
sudo apt-get update
sudo apt-get install python3 python3-pip python3-venv

# Install UV
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

#### HPC Environments
```bash
# Load Python module (if using module system)
module load python/3.11

# Check loaded Python
which python3
python3 --version

# Run install script
./install.sh
```

### 5. Debugging Commands

If you encounter issues, run these commands for debugging:

```bash
# Check UV version and Python detection
uv --version
uv python list

# Check system Python installations
which python
which python3
python3 --version

# Test UV virtual environment creation
uv venv test_env --python $(which python3)
ls -la test_env/
rm -rf test_env
```

### 6. Alternative Installation Methods

#### Using Conda/Mamba
```bash
# Create conda environment
conda create -n bids_apps python=3.11
conda activate bids_apps

# Install dependencies
pip install -r requirements-core.txt

# Test scripts
python run_bids_apps.py --help
```

#### Using System pip
```bash
# Install to user directory (not recommended for production)
pip3 install --user -r requirements-core.txt

# Run scripts with system Python
python3 run_bids_apps.py --help
```

### 7. Environment Variables

For persistent UV configuration:
```bash
# Add to ~/.bashrc or ~/.zshrc
export UV_PYTHON_PREFERENCE=only-system
export UV_PYTHON_DOWNLOADS=never

# Reload shell
source ~/.bashrc
```

### 8. Verification Checklist

After installation, verify:
- [ ] Virtual environment exists: `ls -la .appsrunner/`
- [ ] Python works: `.appsrunner/bin/python --version`
- [ ] Dependencies installed: `.appsrunner/bin/python -c "import colorlog, tqdm, psutil"`
- [ ] Scripts work: `.appsrunner/bin/python run_bids_apps.py --help`
- [ ] HPC script works: `.appsrunner/bin/python run_bids_apps_hpc.py --help`

### 9. Getting Help

If you continue to have issues:

1. Check the installation log for detailed error messages
2. Verify all system dependencies are installed
3. Try manual installation steps
4. Check if your system has any special Python configurations
5. Contact system administrator for HPC-specific issues

### 10. Success Indicators

A successful installation should show:
```
[INFO] Starting BIDS App Runner installation...
[SUCCESS] UV is installed: uv 0.7.20
[SUCCESS] Found suitable Python: python3 (version 3.11)
[INFO] Using Python at: /usr/bin/python3
[SUCCESS] Virtual environment created successfully
[SUCCESS] All packages installed successfully
[SUCCESS] Installation verification completed
============================================
BIDS App Runner Installation Complete!
============================================
```
