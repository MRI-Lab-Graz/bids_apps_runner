#!/bin/bash
# BIDS App Runner Installation Script
#
# This script sets up a Python virtual environment using UV and installs
# all necessary dependencies for the BIDS App Runner scripts.
#
# Requirements:
# - UV (Ultra-fast Python package installer)
# - Python 3.8+
#
# Usage:
#   ./install.sh         # Install core dependencies only
#   ./install.sh --full  # Install all dependencies including development tools
#   
# To activate the environment after installation:
#   source .appsrunner/bin/activate
#
# Author: BIDS Apps Runner Team
# Version: 2.0.0

set -e  # Exit on any error
set -u  # Exit on undefined variables

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
VENV_NAME=".appsrunner"
PYTHON_VERSION="3.8"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Function to print colored output
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Function to check UV installation
check_uv() {
    if ! command_exists uv; then
        print_error "UV is not installed. Please install UV first."
        echo ""
        echo "Installation instructions:"
        echo "  macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh"
        echo "  Or visit: https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    fi
    
    print_success "UV is installed: $(uv --version)"
}

# Function to check Python version
check_python() {
    local python_cmd
    
    # Try to find a suitable Python version
    for cmd in python3 python python3.8 python3.9 python3.10 python3.11 python3.12; do
        if command_exists "$cmd"; then
            local version
            version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null)
            if [ $? -eq 0 ] && [ "$(printf '%s\n' "$PYTHON_VERSION" "$version" | sort -V | head -n1)" = "$PYTHON_VERSION" ]; then
                python_cmd="$cmd"
                # Print to stderr to avoid mixing with return value
                print_success "Found suitable Python: $cmd (version $version)" >&2
                break
            fi
        fi
    done
    
    if [ -z "${python_cmd:-}" ]; then
        print_error "Python $PYTHON_VERSION or higher is required but not found."
        exit 1
    fi
    
    # Return only the command name
    echo "$python_cmd"
}

# Function to create virtual environment
create_venv() {
    local python_cmd="$1"
    
    print_info "Creating virtual environment: $VENV_NAME"
    
    # Remove existing environment if it exists
    if [ -d "$VENV_NAME" ]; then
        print_warning "Removing existing virtual environment..."
        rm -rf "$VENV_NAME"
    fi
    
    # Get full path to Python executable
    local python_path
    python_path=$(which "$python_cmd")
    
    if [ -z "$python_path" ]; then
        print_error "Could not find path for Python command: $python_cmd"
        exit 1
    fi
    
    print_info "Using Python at: $python_path"
    
    # Create new virtual environment with UV using full path
    uv venv "$VENV_NAME" --python "$python_path"
    
    if [ $? -eq 0 ]; then
        print_success "Virtual environment created successfully"
    else
        print_error "Failed to create virtual environment"
        exit 1
    fi
}

# Function to install packages
install_packages() {
    print_info "Installing Python packages..."
    
    # Use core requirements by default
    local req_file="requirements-core.txt"
    
    # Check if user wants full requirements
    if [[ "${1:-}" == "--full" ]]; then
        req_file="requirements.txt"
        print_info "Installing full requirements (including development tools)..."
    fi
    
    # Check if requirements file exists
    if [ ! -f "$req_file" ]; then
        print_error "Requirements file not found: $req_file"
        exit 1
    fi
    
    # Install packages using UV with the virtual environment
    print_info "Installing packages from $req_file..."
    
    # Use UV to install directly into the virtual environment
    uv pip install --python "$VENV_NAME/bin/python" -r "$req_file"
    
    if [ $? -eq 0 ]; then
        print_success "All packages installed successfully"
    else
        print_error "Failed to install packages"
        exit 1
    fi
    
    # Print system dependencies note
    print_warning "Note: Some dependencies require system installation:"
    echo "  - apptainer/singularity (for container execution)"
    echo "  - SLURM tools (for HPC version): sbatch, squeue, scancel"
    echo "  - DataLad (for HPC version): conda install -c conda-forge datalad"
    echo "  - git and git-annex (for DataLad)"
}

# Function to create activation script
create_activation_script() {
    print_info "Creating activation script..."
    
    cat > activate_appsrunner.sh << 'EOF'
#!/bin/bash
# Activation script for BIDS App Runner environment

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="$SCRIPT_DIR/.appsrunner"

if [ ! -d "$VENV_PATH" ]; then
    echo "Error: Virtual environment not found at $VENV_PATH"
    echo "Please run ./install.sh first"
    exit 1
fi

source "$VENV_PATH/bin/activate"

echo "BIDS App Runner environment activated!"
echo "Python: $(which python)"
echo "Python version: $(python --version)"
echo ""
echo "Available scripts:"
echo "  - run_bids_apps.py       (Local/cluster processing)"
echo "  - run_bids_apps_hpc.py   (HPC/SLURM processing)"
echo ""
echo "To deactivate, run: deactivate"
EOF
    
    chmod +x activate_appsrunner.sh
    print_success "Activation script created: activate_appsrunner.sh"
}

# Function to verify installation
verify_installation() {
    print_info "Verifying installation..."
    
    # Check if virtual environment exists
    if [ ! -d "$VENV_NAME" ]; then
        print_error "Virtual environment not found: $VENV_NAME"
        exit 1
    fi
    
    # Test basic Python functionality using the venv Python directly
    local venv_python="$VENV_NAME/bin/python"
    
    if [ ! -f "$venv_python" ]; then
        print_error "Python executable not found in virtual environment"
        exit 1
    fi
    
    print_info "Testing Python installation..."
    "$venv_python" -c "import sys; print(f'Python version: {sys.version}')"
    
    if [ $? -ne 0 ]; then
        print_error "Python test failed"
        exit 1
    fi
    
    # Test script syntax
    print_info "Testing script syntax..."
    "$venv_python" -m py_compile run_bids_apps.py
    "$venv_python" -m py_compile run_bids_apps_hpc.py
    
    if [ $? -eq 0 ]; then
        print_success "Installation verification completed"
    else
        print_error "Script syntax check failed"
        exit 1
    fi
}

# Function to print usage instructions
print_usage() {
    echo ""
    echo "============================================"
    echo "BIDS App Runner Installation Complete!"
    echo "============================================"
    echo ""
    echo "Installation includes:"
    echo "  - Python virtual environment (.appsrunner)"
    echo "  - Core Python dependencies"
    echo "  - BIDS App Runner scripts"
    echo ""
    echo "To get started:"
    echo ""
    echo "1. Activate the environment:"
    echo "   source .appsrunner/bin/activate"
    echo "   # OR"
    echo "   source activate_appsrunner.sh"
    echo ""
    echo "2. Test the installation:"
    echo "   python run_bids_apps.py --help"
    echo "   python run_bids_apps_hpc.py --help"
    echo ""
    echo "3. Configure your BIDS app:"
    echo "   cp config_example.json config.json"
    echo "   # Edit config.json for your setup"
    echo ""
    echo "4. Run your BIDS app:"
    echo "   python run_bids_apps.py -x config.json"
    echo ""
    echo "For HPC usage:"
    echo "   cp config_hpc.json config_hpc_local.json"
    echo "   # Edit config_hpc_local.json for your setup"
    echo "   python run_bids_apps_hpc.py -x config_hpc_local.json"
    echo ""
    echo "Documentation:"
    echo "   - README.md (general usage)"
    echo "   - README_HPC.md (HPC-specific usage)"
    echo ""
    echo "To deactivate the environment:"
    echo "   deactivate"
    echo ""
}

# Main installation function
main() {
    print_info "Starting BIDS App Runner installation..."
    print_info "Script directory: $SCRIPT_DIR"
    
    # Change to script directory
    cd "$SCRIPT_DIR"
    
    # Check prerequisites
    check_uv
    local python_cmd
    python_cmd=$(check_python)
    
    # Create virtual environment
    create_venv "$python_cmd"
    
    # Install packages
    install_packages "$@"
    
    # Create activation script
    create_activation_script
    
    # Verify installation
    verify_installation
    
    # Print usage instructions
    print_usage
    
    print_success "Installation completed successfully!"
}

# Run main function
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
