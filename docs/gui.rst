GUI Interface
=============

The `app_gui.py` script brings the BIDS App Runner workflows to a browser-based interface. It serves `templates/index.html` via Flask/Waitress and talks to `run_bids_apps.py` in the background, so you can assemble configs, load container help, and kick off jobs without typing a long command line.

Launch the GUI
--------------

Use the helper script to bring the GUI online:

.. code-block:: bash

   bash gui/start_gui.sh

That command exports the project root into `PYTHONPATH`, starts `app_gui.py`, and prints the URL that Wasitress listens on (by default `http://localhost:8080`). Point your browser there from the workstation that started the server or, when working remotely, forward the port via SSH.

Building and saving configurations
----------------------------------

The form in the UI is divided into four stages:

1. **Data locations** – browse for the BIDS root, derivatives folder, temporary workspace, and optional TemplateFlow cache. Clicking `...` opens a filesystem browser backed by `app_gui.py`'s `/list_dirs` endpoint.
2. **Container settings** – provide the folder holding `.sif`/`.simg` images, click `Scan` to list them, and select one. `check_container_version` compares the chosen image with Docker Hub versions and renders a badge when newer tags are available.
3. **App-specific arguments** – the `Load Options` button posts to `/get_app_help`, which runs `apptainer run --containall <container> --help`, parses the sections, and displays them as grouped cards. It removes the common `--help`/`--version` flags, sorts alphabetically, and builds dropdowns, checkboxes, and input fields so you can declare pipeline-specific arguments.
4. **Runner overrides** – toggle log level, subject filters, force reprocessing, validation-only mode, dry-run, pilot mode, reprocess missing, and more. Each control maps to the corresponding `run_bids_apps.py` flag, and the form remembers the values when you load or save a config.

Saving the form hits `/save_config`, which writes the JSON into `configs/` (default `config.json`). After saving you can reuse the config via `Load Config` or run it immediately with `Save & Start Runner`.

Monitoring execution and logs
-----------------------------

When you press `Save & Start Runner`, the GUI launches `run_bids_apps.py` with `--nohup` so the process continues after the browser closes. The console panel polls `/get_log` every 3 seconds and shows the last 150 lines of the most recent `nohup_bids_runner_*.log` file after stripping ANSI color sequences. The `Stop Execution` button posts to `/kill_job`, terminating the runner and related Apptainer processes via `pkill`.

Documented REST helpers
------------------------

- `/list_containers` – list `.sif` and `.simg` files inside a folder so the dropdown stays in sync with the filesystem.
- `/check_container_version` – deduce the app name from the filename, map it to Docker Hub (or nipreps), and tell you if a newer tag exists plus a changelog URL.
- `/get_app_help` – run `apptainer run --help`, split the output by capitalized headings, extract flags, guesses, and choice lists, and deliver JSON sections plus upstream documentation URLs.
- `/list_configs` and `/get_config` – expose saved JSON configs so the browser can reload them and pre-populate the fields again.
- `/run_app` – validates the paths, injects helper flags (e.g., `--nohup`), and respawns `run_bids_apps.py` in a background process.
- `/kill_job` – fires `pkill -f apptainer` and stops the runner processes the same way as `kill_app.sh`.
- `/get_log` – tails the latest log, removes ANSI escape codes, and sends the text back to the live console widget.

Troubleshooting and tips
------------------------

- If the UI cannot find any containers, verify that the folder path is correct and contains `.sif` or `.simg` files.
- When the `Load Options` step shows no sections, the container either hides help flags or the parsing heuristics cannot find `--` prefixes. In that case, click the `Show Raw Help Output` button to inspect the raw `apptainer --help` text and adjust the command-line manually.
- The GUI keeps a link to the pipeline documentation (fMRIPrep, QSIPrep, MRIQC) so you can jump straight to the official help.
- You can reuse saved configs with `Load Config`, which will rerun `scanContainers()` and `Load Options` to recreate the displayed arguments.

Read the GUI guide on Read the Docs for screenshots and step-by-step help with the new interface.
