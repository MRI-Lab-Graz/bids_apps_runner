#!/usr/bin/env python3
"""Delete local .sif/.simg images that have not been used for --days.

Run daily from cron. Local images are only a cache of the DataLad server's
container catalog (the GUI's Download button fetches them back), so an image
is deleted only when all of these hold:

  * its access time is older than --days (the filesystem is mounted
    relatime, so every run of the container refreshes it);
  * the server catalog has the same relative path at the same size, so
    the identical build can be fetched again. On 2026-10-06 this kept
    freesurfer_8.2.0.sif (not on the server at all) and
    qsiprep_26.0.0.sif (server copy is a different build);
  * no queued or running SLURM job of this user mentions its path (the
    generated sbatch scripts embed the absolute .sif path).

If the server or the SLURM queue cannot be read, nothing is deleted.
Dry run unless --live.
"""

import argparse
import shlex
import subprocess
import sys
import time
from pathlib import Path

import prism_datalad


def remote_sizes(remote):
    """{relative path: size} of every image in the server catalog ``host:/path``."""
    host, _, root = remote.partition(":")
    script = (
        "find "
        + shlex.quote(root)
        + r" -type f \( -name '*.sif' -o -name '*.simg' \) -printf '%P %s\n'"
    )
    out = prism_datalad.run_remote_script(host, script, timeout=60)
    sizes = {}
    for line in out.splitlines():
        name, _, size = line.rpartition(" ")
        if name:
            sizes[name] = int(size)
    return sizes


def queued_job_scripts():
    """Batch scripts of this user's queued and running jobs."""
    try:
        ids = subprocess.check_output(
            ["squeue", "--me", "-h", "-o", "%A"], text=True, timeout=60
        ).split()
        return [
            subprocess.check_output(
                ["scontrol", "write", "batch_script", job_id, "-"],
                text=True,
                timeout=60,
            )
            for job_id in dict.fromkeys(ids)
        ]
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"cannot read the SLURM queue: {exc}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--folder", required=True, help="local images root")
    parser.add_argument("--remote", required=True, help="server catalog, host:/path")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--live", action="store_true", help="actually delete")
    args = parser.parse_args(argv)

    try:
        server = remote_sizes(args.remote)
        jobs = "\n".join(queued_job_scripts())
    except RuntimeError as exc:
        print(f"[prune_containers] nothing deleted: {exc}", flush=True)
        return 1

    root = Path(args.folder).expanduser()
    cutoff = time.time() - args.days * 86400
    images = sorted(root.rglob("*.sif")) + sorted(root.rglob("*.simg"))
    for path in images:
        rel = path.relative_to(root).as_posix()
        st = path.stat()
        if st.st_atime > cutoff:
            continue
        if rel not in server:
            reason = "not on the server"
        elif server[rel] != st.st_size:
            reason = "differs from the server copy"
        elif str(path) in jobs:
            reason = "used by a queued/running job"
        else:
            if args.live:
                path.unlink()
            print(f"[prune_containers] {'deleted' if args.live else 'would delete'} {rel}")
            continue
        print(f"[prune_containers] keeping unused {rel}: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
