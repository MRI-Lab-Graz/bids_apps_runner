#!/usr/bin/env python3
"""Scan a cohort config's datasets for a pre-existing plain (non-DataLad
-subdataset) ``derivatives/<app>`` directory that would make
``submit_bids_cohort.sh setup`` fail to register/clone the output dataset.

Real incidents (2026-08-03): ds000256 and ds007328 both already had
pre-existing mriqc report files tracked as plain content on the dataset's
current/default branch (most likely OpenNeuro's own shipped derivatives,
not something this pipeline produced) -- when setup created the
'derivatives' branch fresh off that same commit, it inherited the
conflict immediately: ``datalad create`` and the subsequent
``git submodule add`` both failed with
``'derivatives/mriqc' already exists in the index``, and the output clone
was skipped for that dataset. This scans every dataset in a config up
front, in one read-only batched SSH round-trip, so you know the full
scope before fixing them one at a time.

Read-only: never checks out a branch or modifies anything on the server.

Usage:
    scripts/check_derivatives_conflicts.py -c configs/megastudy_openneuro_mriqc.json
"""
import argparse
import json
import shlex
from pathlib import Path
from typing import List, Tuple

import prism_datalad


def dataset_ids(datasets: list) -> List[str]:
    return [entry.get("id") if isinstance(entry, dict) else entry for entry in datasets]


def parse_ssh_host_and_path(url_template: str, dataset_id: str) -> Tuple[str, str]:
    """Split a resolved ``ssh_host:/path`` or ``ssh://ssh_host/path`` URL.

    Mirrors the two URL forms already used across ``configs/*.json``
    (see ``configs/cohort_hpc_example.json``'s ``input_url_template`` vs.
    ``output_url_template``).
    """
    resolved = url_template.replace("{dataset_id}", dataset_id)
    if resolved.startswith("ssh://"):
        rest = resolved[len("ssh://") :]
        host, _, path = rest.partition("/")
        return host, "/" + path
    host, _, path = resolved.partition(":")
    return host, path


def build_remote_check_script(app_name: str, repo_paths: List[str]) -> str:
    """Build one bash script checking all ``repo_paths`` for a conflict.

    submit_bids_cohort.sh's setup creates the 'derivatives' branch fresh
    (via `git checkout -b derivatives`) off whatever's currently checked
    out if it doesn't exist yet -- so a plain (non-subdataset)
    derivatives/<app> tracked on the CURRENT/default branch is inherited
    immediately and conflicts the same way as if it were already on an
    existing 'derivatives' branch. This checks whichever ref is actually
    relevant (the existing 'derivatives' branch if there is one, else
    current HEAD) so it predicts the real failure instead of only
    catching it after the fact.

    For each dataset's input repo path, reports on stdout one of:
      MISSING_REPO  -- the path doesn't exist on the server at all
      CLEAN         -- no derivatives/<app> tracked on the relevant ref
      OK            -- derivatives/<app> is already a proper subdataset (gitlink)
      CONFLICT      -- derivatives/<app> exists as plain (non-subdataset) content
    """
    lines = ["set -u"]
    for path in repo_paths:
        quoted = shlex.quote(path)
        app = shlex.quote(f"derivatives/{app_name}")
        lines.append(
            f"""
if [ ! -d {quoted} ]; then
  echo "{path}: MISSING_REPO"
else
  if git -C {quoted} show-ref --verify --quiet refs/heads/derivatives; then
    ref=derivatives
  else
    ref=HEAD
  fi
  entry=$(git -C {quoted} ls-tree "$ref" -- {app} 2>/dev/null)
  if [ -z "$entry" ]; then
    echo "{path}: CLEAN (no derivatives/{app_name} on $ref)"
  elif echo "$entry" | grep -q '^160000'; then
    echo "{path}: OK (already a proper subdataset on $ref)"
  else
    echo "{path}: CONFLICT (plain content at derivatives/{app_name} on $ref)"
  fi
fi
""".rstrip()
        )
    return "\n".join(lines)


def build_removal_script(app_name: str, conflict_paths: List[str]) -> str:
    """Build a bash script that removes the stale plain derivatives/<app>
    content for each of ``conflict_paths`` (as reported CONFLICT by
    build_remote_check_script) and commits the removal via ``datalad save``.

    Handles both cases build_remote_check_script distinguishes:
      - No 'derivatives' branch yet: the conflict is on whatever's
        currently checked out (typically the default branch, e.g. master)
        -- remove and save directly, no branch switching needed.
      - A 'derivatives' branch already exists: switch to it, remove and
        save there, then switch back to whatever was checked out before.

    This is a WRITE operation against real, shared, already-committed
    content -- intentionally emitted as a script to review, not executed
    by this tool. Run it yourself, e.g.:
        scripts/check_derivatives_conflicts.py -c <config> --emit-removal-script \\
            | ssh datalad-server bash -s
    """
    lines = ["set -eu"]
    for path in conflict_paths:
        quoted = shlex.quote(path)
        app_path = shlex.quote(f"derivatives/{app_name}")
        message = shlex.quote(
            f"Remove outdated pre-existing {app_name} derivatives "
            "(superseded by mega-study pipeline)"
        )
        lines.append(
            f"""
echo "=== {path} ==="
if git -C {quoted} show-ref --verify --quiet refs/heads/derivatives; then
  orig_branch=$(git -C {quoted} symbolic-ref --short HEAD)
  git -C {quoted} checkout -q derivatives
  git -C {quoted} rm -rq {app_path}
  (cd {quoted} && datalad save -m {message})
  git -C {quoted} checkout -q "$orig_branch"
else
  git -C {quoted} rm -rq {app_path}
  (cd {quoted} && datalad save -m {message})
fi
""".rstrip()
        )
    return "\n".join(lines)


_STATUSES = ("MISSING_REPO", "CLEAN", "OK", "CONFLICT")


def categorize_output(output: str) -> dict:
    """Bucket each ``path: STATUS ...`` line from build_remote_check_script's
    output by status. Returns {status: [line, ...]} for all of _STATUSES.
    """
    results = {status: [] for status in _STATUSES}
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        for status in _STATUSES:
            if f": {status}" in line:
                results[status].append(line)
                break
    return results


def _path_from_status_line(line: str) -> str:
    return line.split(": ", 1)[0]


def check_config(config_path: str, quiet: bool = False) -> dict:
    config = json.loads(Path(config_path).read_text())
    ids = dataset_ids(config.get("datasets", []))
    app_name = config.get("bids_app", {}).get("app_name", "bids_app")
    input_url_template = config.get("datalad", {}).get("input_url_template", "")

    by_host: dict = {}
    for ds_id in ids:
        host, path = parse_ssh_host_and_path(input_url_template, ds_id)
        by_host.setdefault(host, []).append(path)

    results = {status: [] for status in _STATUSES}
    for host, paths in by_host.items():
        script = build_remote_check_script(app_name, paths)
        output = prism_datalad.run_remote_script(host, script)
        for status, lines in categorize_output(output).items():
            results[status].extend(lines)

    if not quiet:
        print(f"{config_path} ({app_name}, {len(ids)} datasets):")
        for status in ("CONFLICT", "MISSING_REPO", "OK", "CLEAN"):
            entries = results[status]
            print(f"  {status}: {len(entries)}")
            for entry in entries:
                print(f"    {entry}")

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan a cohort config's datasets for a pre-existing plain "
        "derivatives/<app> directory that would break setup's subdataset "
        "registration."
    )
    parser.add_argument(
        "-c", "--config", required=True, help="Path to a cohort config JSON file"
    )
    parser.add_argument(
        "--emit-removal-script",
        action="store_true",
        help="After scanning, print a bash script (to stdout, for you to "
        "review and run yourself, e.g. piped into 'ssh datalad-server "
        "bash -s') that removes the stale plain derivatives/<app> content "
        "for every CONFLICT dataset found. This is a WRITE operation "
        "against real committed content -- it is only ever printed here, "
        "never executed by this tool.",
    )
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text())
    app_name = config.get("bids_app", {}).get("app_name", "bids_app")

    results = check_config(args.config, quiet=args.emit_removal_script)

    if args.emit_removal_script:
        conflict_paths = [_path_from_status_line(line) for line in results["CONFLICT"]]
        if not conflict_paths:
            print("# No CONFLICT datasets found -- nothing to remove.")
            return
        print(f"# Review before running -- removes derivatives/{app_name} for "
              f"{len(conflict_paths)} dataset(s):")
        for path in conflict_paths:
            print(f"#   {path}")
        print(build_removal_script(app_name, conflict_paths))


if __name__ == "__main__":
    main()
