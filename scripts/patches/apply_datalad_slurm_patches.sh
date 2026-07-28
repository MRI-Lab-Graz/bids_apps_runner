#!/usr/bin/env bash
# apply_datalad_slurm_patches.sh
#
# Reapply local patches to the `datalad-slurm` package installed in
# .datalad-slurm-venv. That venv is a plain (non-editable) pip/uv install
# gitignored from this repo, so any hand-fix made directly inside its
# site-packages is lost the next time the venv is rebuilt from scratch.
# Run this script after any such rebuild.
#
# Usage: scripts/patches/apply_datalad_slurm_patches.sh [path-to-venv]
#   (defaults to .datalad-slurm-venv next to this repo's root)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VENV_DIR="${1:-${REPO_ROOT}/.datalad-slurm-venv}"
PKG_DIR="${VENV_DIR}/lib/python3.10/site-packages/datalad_slurm"

if [[ ! -d "$PKG_DIR" ]]; then
    echo "error: datalad_slurm package not found at ${PKG_DIR}" >&2
    echo "       pass the venv path explicitly if it lives elsewhere." >&2
    exit 1
fi

for patch in "${SCRIPT_DIR}"/*.patch; do
    echo "Applying $(basename "$patch") to ${PKG_DIR} ..."
    patch --forward --strip=1 --directory="${VENV_DIR}/lib/python3.10/site-packages" < "$patch"
done

echo "Done."
