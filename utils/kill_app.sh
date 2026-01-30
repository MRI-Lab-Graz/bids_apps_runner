#!/bin/bash

usage() {
  echo "Usage: $0 <search-phrase> [--loop] [--timeout <seconds>]"
  echo "  <search-phrase>    Phrase to match qsirecon processes (supports wildcards)"
  echo "  --loop             Run continuously, checking and killing every 5 seconds"
  echo "  --timeout <sec>    Stop loop after this many seconds without any new matching jobs (default: 10)"
  exit 1
}

if [[ $# -lt 1 ]]; then
  usage
fi

PHRASE="$1"
LOOP_MODE=false
TIMEOUT=10  # default timeout in seconds

# Parse optional arguments
shift
while [[ $# -gt 0 ]]; do
  case "$1" in
    --loop) LOOP_MODE=true; shift ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    *) echo "Unknown argument: $1"; usage ;;
  esac
done

kill_jobs() {
  echo "🔍 Searching for qsirecon processes containing phrase: \"$PHRASE\"..."

  PIDS=$(pgrep -f "qsirecon.*$PHRASE")

  if [[ -z "$PIDS" ]]; then
    echo "⚠️ No matching qsirecon processes found."
    return 1
  else
    echo "Found qsirecon PIDs:"
    echo "$PIDS"

    echo "🛑 Killing matching qsirecon jobs..."
    for pid in $PIDS; do
      if kill "$pid" 2>/dev/null; then
        echo "✅ Killed qsirecon PID $pid"
      else
        echo "❌ Failed to kill qsirecon PID $pid"
      fi
    done

    echo "🛑 Killing appinit parent processes..."
    # Get parent PIDs of killed qsirecon processes
    PARENT_PIDS=$(ps -o ppid= -p $(echo "$PIDS" | tr '\n' ',' | sed 's/,$//'))

    # Filter only appinit processes and kill
    for ppid in $PARENT_PIDS; do
      if ps -p "$ppid" -o comm= | grep -q '^appinit$'; then
        if kill -9 "$ppid" 2>/dev/null; then
          echo "✅ Killed appinit PID $ppid"
        else
          echo "❌ Failed to kill appinit PID $ppid"
        fi
      fi
    done

    echo "🎯 Done. All matching jobs for phrase \"$PHRASE\" have been terminated."
    return 0
  fi
}

if [[ "$LOOP_MODE" == true ]]; then
  echo "Starting continuous monitoring with phrase \"$PHRASE\"."
  echo "Will stop if no matching jobs found for $TIMEOUT seconds."
  LAST_FOUND=$(date +%s)

  while true; do
    kill_jobs
    FOUND=$?

    NOW=$(date +%s)

    if [[ $FOUND -eq 0 ]]; then
      LAST_FOUND=$NOW
    fi

    DIFF=$((NOW - LAST_FOUND))

    if (( DIFF >= TIMEOUT )); then
      echo "⏰ No matching jobs found for $TIMEOUT seconds. Exiting loop."
      break
    fi

    sleep 5
  done
else
  kill_jobs
fi
