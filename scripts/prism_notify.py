#!/usr/bin/env python3
"""Push notifications for the GUI, over the same transport as the cluster.

Cluster jobs already notify through scripts/notify_ntfy.sh, which
hpc_datalad_runner.py bakes into every generated sbatch script. The GUI
used to carry a second, unrelated notification path over SMTP; this
module replaces it so there is one channel to configure and one to debug.

Everything here is best-effort. A notification that fails must never take
down, stall, or alter the run it was reporting on -- failures come back
as a quiet False. That mirrors notify_ntfy.sh's own contract, which
already swallows curl failures rather than failing a job over them.
"""

import subprocess
from pathlib import Path

# Seconds before a push is abandoned. notify_ntfy.sh does its own
# best-effort curl, but a wedged network stack must not be able to hold a
# run's monitor thread open indefinitely.
NOTIFY_TIMEOUT_SECONDS = 20


def notify_script_path():
    """Absolute path to the shared notify_ntfy.sh."""
    return str(Path(__file__).resolve().parent / "notify_ntfy.sh")


def notify(title, message, priority=None, tags=None):
    """Send a push notification. Returns True only on a clean exit.

    Signature mirrors notify_ntfy.sh: <title> <message> [priority] [tags].
    """
    cmd = [notify_script_path(), str(title), str(message)]
    if priority:
        cmd.append(str(priority))
        if tags:
            cmd.append(str(tags))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=NOTIFY_TIMEOUT_SECONDS,
        )
    except Exception:
        # Missing script, timeout, permissions -- never propagate.
        return False
    return result.returncode == 0


def run_completion_message(run_label, success, detail=""):
    """Build the title/message/priority for a finished GUI run."""
    if success:
        return {
            "title": f"Run finished: {run_label}",
            "message": detail or "Completed successfully.",
            "priority": "default",
            "tags": "white_check_mark",
        }
    return {
        "title": f"Run FAILED: {run_label}",
        "message": detail or "Run failed -- check the GUI log for details.",
        "priority": "high",
        "tags": "rotating_light",
    }


def notify_run_completion(run_label, success, detail=""):
    """Convenience wrapper: build and send a run-completion notification."""
    payload = run_completion_message(run_label, success, detail)
    return notify(
        payload["title"], payload["message"], payload["priority"], payload["tags"]
    )
