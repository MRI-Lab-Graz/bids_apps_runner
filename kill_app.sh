#!/bin/bash

# kill_qsirecon_by_phrase.sh
# Description:
#   Kills qsirecon processes matching a given phrase and their appinit parent processes.
# Usage:
#   ./kill_qsirecon_by_phrase.sh "<unique phrase>"
# Examples:
#   ./kill_qsirecon_by_phrase.sh "mrtrix_multishell_msmt_ACT-hsvs"
#   ./kill_qsirecon_by_phrase.sh "--recon-spec noddi"
#   ./kill_qsirecon_by_phrase.sh "sub-1292037"

# Show help if no arguments or help flag is passed
if [[ -z "$1" || "$1" == "-h" || "$1" == "--help" ]]; then
    echo ""
    echo "🔧 kill_qsirecon_by_phrase.sh — Selectively kill qsirecon jobs and their appinit parents"
    echo ""
    echo "Usage:"
    echo "  $0 \"<search phrase>\""
    echo ""
    echo "Examples:"
    echo "  $0 \"mrtrix_multishell_msmt_ACT-hsvs\""
    echo "  $0 \"--recon-spec noddi\""
    echo "  $0 \"sub-1292037\""
    echo ""
    echo "This script:"
    echo "  - Finds qsirecon jobs containing the given phrase"
    echo "  - Kills them and their associated 'appinit' parent processes"
    echo ""
    exit 0
fi

phrase="$1"

echo "🔍 Searching for qsirecon processes containing phrase: \"$phrase\"..."

# Get matching qsirecon PIDs
qsirecon_pids=$(ps -eo pid,ppid,cmd | grep "qsirecon" | grep "$phrase" | grep -v grep | awk '{print $1}')
appinit_pids=()

if [[ -z "$qsirecon_pids" ]]; then
    echo "✅ No matching qsirecon processes found for phrase: \"$phrase\""
    exit 0
fi

echo "Found qsirecon PIDs: $qsirecon_pids"

# Collect appinit parent PIDs
for pid in $qsirecon_pids; do
    appinit_pid=$(ps -o ppid= -p "$pid" | tr -d ' ')
    cmd=$(ps -o cmd= -p "$appinit_pid")
    if echo "$cmd" | grep -q "appinit"; then
        appinit_pids+=("$appinit_pid")
    fi
done

# Kill qsirecon jobs
echo "🛑 Killing matching qsirecon jobs..."
for pid in $qsirecon_pids; do
    kill -9 "$pid" 2>/dev/null && echo "✅ Killed qsirecon PID $pid"
done

# Kill appinit parents
if [[ ${#appinit_pids[@]} -gt 0 ]]; then
    echo "🛑 Killing appinit parent processes..."
    for pid in "${appinit_pids[@]}"; do
        kill -9 "$pid" 2>/dev/null && echo "✅ Killed appinit PID $pid"
    done
else
    echo "ℹ️ No appinit parents found or needed."
fi

echo "🎯 Done. All matching jobs for phrase \"$phrase\" have been terminated."
