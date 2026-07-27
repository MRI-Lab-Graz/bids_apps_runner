#!/bin/bash
# Install the GitHub CLI (gh) into a repo-local, gitignored .gh-runtime/
# directory -- no root/sudo needed, same spirit as .node-runtime/ (see
# tests_js/README.md).
#
# Usage:
#   scripts/install_gh.sh
#   export PATH="$(pwd)/.gh-runtime/bin:$PATH"   # once per shell, to use it

set -e
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="$PROJECT_ROOT/.gh-runtime"

case "$(uname -s)" in
    Linux) OS="linux" ;;
    Darwin) OS="macOS" ;;
    *) echo "Unsupported OS: $(uname -s)" >&2; exit 1 ;;
esac

case "$(uname -m)" in
    x86_64|amd64) ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    *) echo "Unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac

GH_VERSION=$(curl -fsSL https://api.github.com/repos/cli/cli/releases/latest \
    | grep -Po '"tag_name": "v\K[^"]*')
ASSET="gh_${GH_VERSION}_${OS}_${ARCH}"
TARBALL="/tmp/${ASSET}.tar.gz"

echo "Installing gh ${GH_VERSION} (${OS}/${ARCH}) into ${INSTALL_DIR}..."
curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/${ASSET}.tar.gz" -o "$TARBALL"

rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
tar -xzf "$TARBALL" -C "$INSTALL_DIR" --strip-components=1
rm -f "$TARBALL"

echo "Installed: $("$INSTALL_DIR/bin/gh" --version | head -1)"
echo "Add to PATH with: export PATH=\"$INSTALL_DIR/bin:\$PATH\""
