#!/usr/bin/env bash
# install_macos.sh — BIDS App Runner installer for Apple Silicon Macs (M1/M2/M3/M4)
#
# Share this single file with collaborators. Running it once sets up everything.
#
# Usage:
#   bash install_macos.sh                         # installs to ~/bids_apps_runner
#   bash install_macos.sh --dir ~/Desktop/runner  # custom location
#
# What this script does:
#   1. Verifies Apple Silicon + macOS
#   2. Checks Homebrew (guides install if missing)
#   3. Checks Xcode CLI tools (provides git)
#   4. Installs git-annex via Homebrew (required for DataLad / OpenNeuro)
#   5. Clones the repository (or updates it if already present)
#   6. Downloads UV locally and creates a Python 3.11+ virtual environment
#   7. Installs all Python dependencies including DataLad
#   8. Checks Docker Desktop
#   9. Writes activate_appsrunner.sh for daily use

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REPO_URL="https://github.com/MRI-Lab-Graz/bids_apps_runner.git"
DEFAULT_INSTALL_DIR="${HOME}/bids_apps_runner"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
INSTALL_DIR="$DEFAULT_INSTALL_DIR"
FULL_INSTALL=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --dir)   INSTALL_DIR="$2"; shift 2 ;;
        --full)  FULL_INSTALL=true; shift ;;
        -h|--help)
            echo "Usage: bash $(basename "$0") [--dir <path>] [--full]"
            echo "  --dir <path>   Installation directory (default: ~/bids_apps_runner)"
            echo "  --full         Also install dev tools and docs dependencies"
            exit 0 ;;
        *) echo "Unknown option: $1  (use --help for usage)"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }
step()    { echo -e "\n${BOLD}── $* ──${NC}"; }

# ---------------------------------------------------------------------------
# Step 1 — Apple Silicon guard
# ---------------------------------------------------------------------------
step "1/9  Checking hardware"

[[ "$(uname -s)" == "Darwin" ]] || error "This script is for macOS only. On Linux use scripts/install.sh."
[[ "$(uname -m)" == "arm64"  ]] || error "Apple Silicon (arm64) required. Detected: $(uname -m)"

success "Apple Silicon Mac · macOS $(sw_vers -productVersion)"

# ---------------------------------------------------------------------------
# Step 2 — Homebrew
# ---------------------------------------------------------------------------
step "2/9  Checking Homebrew"

if ! command -v brew &>/dev/null; then
    echo ""
    echo "  Homebrew is not installed. Install it with:"
    echo ""
    echo '    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    echo ""
    echo "  After installation follow the printed instructions to add brew to"
    echo "  your PATH, then rerun this script."
    exit 1
fi

# Ensure Homebrew binaries are on PATH for this session
eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || true)"
success "Homebrew at $(brew --prefix)"

# ---------------------------------------------------------------------------
# Step 3 — Xcode CLI tools (provides git)
# ---------------------------------------------------------------------------
step "3/9  Checking Xcode CLI tools"

if ! xcode-select -p &>/dev/null; then
    warn "Xcode CLI tools not found — installing (a system dialog will appear)..."
    xcode-select --install
    echo ""
    echo "  Wait for the installation to finish, then rerun this script."
    exit 0
fi
success "Xcode CLI tools present"
command -v git &>/dev/null || error "git not found — reopen your terminal and try again."
success "git $(git --version | awk '{print $3}')"

# ---------------------------------------------------------------------------
# Step 4 — git-annex (required for DataLad / OpenNeuro datasets)
# ---------------------------------------------------------------------------
step "4/9  Checking git-annex"

if command -v git-annex &>/dev/null; then
    success "git-annex $(git-annex version | head -1 | awk '{print $3}')"
else
    info "Installing git-annex via Homebrew..."
    brew install git-annex
    command -v git-annex &>/dev/null \
        || error "git-annex installation failed — run 'brew install git-annex' manually."
    success "git-annex $(git-annex version | head -1 | awk '{print $3}')"
fi

# ---------------------------------------------------------------------------
# Step 5 — Clone / update repository
# ---------------------------------------------------------------------------
step "5/9  Setting up repository at ${INSTALL_DIR}"

if [[ -d "${INSTALL_DIR}/.git" ]]; then
    warn "Repository already exists — pulling latest changes..."
    git -C "$INSTALL_DIR" pull --ff-only
else
    git clone "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"
success "Repository ready at ${INSTALL_DIR}"

# ---------------------------------------------------------------------------
# Step 6 — UV (downloaded locally) + Python virtual environment
# ---------------------------------------------------------------------------
step "6/9  Installing UV and creating Python environment"

UV_BIN="./build/uv"

if [[ -f "$UV_BIN" ]]; then
    warn "UV already present ($("$UV_BIN" --version)) — skipping download."
else
    mkdir -p build
    UV_TARBALL="uv-aarch64-apple-darwin.tar.gz"
    info "Downloading UV for Apple Silicon..."
    curl -LsSf --retry 3 \
        "https://github.com/astral-sh/uv/releases/latest/download/${UV_TARBALL}" \
        -o "build/${UV_TARBALL}" \
        || error "UV download failed — check your internet connection."
    tar -xzf "build/${UV_TARBALL}" -C build --strip-components=1
    rm -f "build/${UV_TARBALL}"
    chmod +x "$UV_BIN"
    success "UV $("$UV_BIN" --version) installed"
fi

# Find Python 3.11+
PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        if "$candidate" -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" 2>/dev/null; then
            PYTHON_BIN="$(command -v "$candidate")"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    info "Python 3.11+ not found — installing via Homebrew..."
    brew install python@3.11
    PYTHON_BIN="/opt/homebrew/bin/python3.11"
    "$PYTHON_BIN" --version &>/dev/null \
        || error "Python 3.11 install failed — run 'brew install python@3.11' manually."
fi
info "Python $("$PYTHON_BIN" --version 2>&1 | awk '{print $2}') at ${PYTHON_BIN}"

# Create virtual environment
if [[ -d ".appsrunner" ]]; then
    warn "Existing .appsrunner environment found — recreating."
    rm -rf .appsrunner
fi
"$UV_BIN" venv .appsrunner --python "$PYTHON_BIN"
success "Virtual environment created"

# ---------------------------------------------------------------------------
# Step 7 — Python dependencies + DataLad
# ---------------------------------------------------------------------------
step "7/9  Installing Python dependencies"

REQ_FILE="requirements-core.txt"
[[ "$FULL_INSTALL" == true ]] && REQ_FILE="requirements.txt"
[[ -f "$REQ_FILE" ]] || error "$REQ_FILE not found in ${INSTALL_DIR}"

"$UV_BIN" pip install --python ".appsrunner/bin/python" -r "$REQ_FILE"
success "Core packages installed"

info "Installing DataLad..."
"$UV_BIN" pip install --python ".appsrunner/bin/python" "datalad>=0.19.0"
success "DataLad installed"

# ---------------------------------------------------------------------------
# Step 8 — Docker Desktop
# ---------------------------------------------------------------------------
step "8/9  Checking Docker Desktop"

if ! command -v docker &>/dev/null; then
    echo ""
    warn "Docker Desktop is not installed."
    echo "  Download it here (choose 'Apple Silicon / M-chip'):"
    echo "    https://www.docker.com/products/docker-desktop/"
    echo ""
    echo "  After installing and starting Docker Desktop, rerun this script"
    echo "  or just run the app — Docker is only needed at analysis time."
elif ! docker info &>/dev/null 2>&1; then
    warn "Docker is installed but not running."
    echo "  Open Docker Desktop, wait for it to start, then you are ready to go."
else
    success "Docker Desktop is running ($(docker --version | awk '{print $3}' | tr -d ','))"
fi

# ---------------------------------------------------------------------------
# Step 9 — Activation script
# ---------------------------------------------------------------------------
step "9/9  Writing activation script"

cat > activate_appsrunner.sh << HEREDOC
#!/usr/bin/env bash
# Activate the BIDS App Runner environment (macOS Apple Silicon).
# Usage:  source activate_appsrunner.sh

SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -d "\$SCRIPT_DIR/.appsrunner" ]]; then
    echo "ERROR: Virtual environment not found."
    echo "       Run scripts/install_macos.sh first."
    return 1 2>/dev/null || exit 1
fi

source "\$SCRIPT_DIR/.appsrunner/bin/activate"

# Ensure Homebrew binaries (git-annex, datalad) are on PATH
eval "\$(/opt/homebrew/bin/brew shellenv 2>/dev/null || true)"

export BIDS_RUNNER_ROOT="\$SCRIPT_DIR"

echo ""
echo "BIDS App Runner environment activated"
echo "  Project  : \$SCRIPT_DIR"
echo "  Python   : \$(which python)"
echo "  DataLad  : \$(datalad --version 2>/dev/null || echo 'not found on PATH')"
echo "  Docker   : \$(docker --version 2>/dev/null | head -1 || echo 'not found')"
echo ""
echo "  Start web GUI   :  python prism_app_runner.py"
echo "  CLI runner      :  python scripts/prism_runner.py --help"
echo "  Deactivate      :  deactivate"
echo ""
HEREDOC

chmod +x activate_appsrunner.sh
success "activate_appsrunner.sh written"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo -e "${BOLD}${GREEN}──────────────────────────────────────────────────────${NC}"
echo -e "${BOLD}${GREEN}  BIDS App Runner setup complete!${NC}"
echo -e "${BOLD}${GREEN}──────────────────────────────────────────────────────${NC}"
echo ""
echo -e "  Installation path : ${BOLD}${INSTALL_DIR}${NC}"
echo ""
echo "  To get started:"
echo "    cd ${INSTALL_DIR}"
echo "    source activate_appsrunner.sh"
echo "    python prism_app_runner.py          # start the web GUI"
echo ""
echo "  Using OpenNeuro datasets:"
echo "    In the GUI: Dataset → enter 'ds005239' and click Clone"
echo "    Only data for subjects you process will be downloaded."
echo ""
if ! (command -v docker &>/dev/null && docker info &>/dev/null 2>&1); then
    echo -e "  ${YELLOW}Reminder:${NC} Install/start Docker Desktop before running analyses."
    echo ""
fi
