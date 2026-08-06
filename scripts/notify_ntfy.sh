#!/usr/bin/env bash
# notify_ntfy.sh -- best-effort push notification via ntfy.sh (or a
# self-hosted ntfy server). Never fails the caller: a missing/broken config,
# missing curl, or an unreachable server is logged to stderr and swallowed,
# since a failed notification must not take down the SLURM job that
# triggered it (or the login-node script calling it).
#
# Usage: notify_ntfy.sh <title> <message> [priority] [tags]
#   priority: ntfy priority (min/low/default/high/urgent) -- default "default"
#   tags:     comma-separated ntfy emoji tags, e.g. "white_check_mark" or "x"
#
# Reads NTFY_SERVER / NTFY_TOPIC from the environment if already set (lets a
# caller override without touching the shared config, and lets tests inject
# a fake server); otherwise sources configs/ntfy.conf next to this script's
# repo root. Set up your own topic with:
#   cat > configs/ntfy.conf <<'EOF'
#   NTFY_SERVER="https://ntfy.sh"
#   NTFY_TOPIC="your-random-unguessable-topic"
#   EOF
set -uo pipefail  # deliberately no -e: a notification failure must never propagate

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="${NTFY_CONFIG:-${REPO_DIR}/configs/ntfy.conf}"

TITLE="${1:-}"
MESSAGE="${2:-}"
PRIORITY="${3:-default}"
TAGS="${4:-}"

if [[ -z "$TITLE" || -z "$MESSAGE" ]]; then
    echo "notify_ntfy.sh: title and message are required" >&2
    exit 0
fi

if [[ -z "${NTFY_SERVER:-}" || -z "${NTFY_TOPIC:-}" ]] && [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$CONFIG_FILE"
fi

if [[ -z "${NTFY_SERVER:-}" || -z "${NTFY_TOPIC:-}" ]]; then
    echo "notify_ntfy.sh: no NTFY_SERVER/NTFY_TOPIC configured (see configs/ntfy.conf) -- skipping" >&2
    exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "notify_ntfy.sh: curl not available -- skipping" >&2
    exit 0
fi

curl -fsS --max-time 10 \
    -H "Title: ${TITLE}" \
    -H "Priority: ${PRIORITY}" \
    ${TAGS:+-H "Tags: ${TAGS}"} \
    -d "${MESSAGE}" \
    "${NTFY_SERVER%/}/${NTFY_TOPIC}" >/dev/null 2>&1 \
    || echo "notify_ntfy.sh: failed to reach ${NTFY_SERVER%/}/${NTFY_TOPIC} (best-effort, ignoring)" >&2

exit 0
