Installation
============

System prerequisites
-------------------

- Python 3.8+
- Apptainer (or Singularity) available as the apptainer command
- SLURM available on HPC (optional for local runs)

For container building:

- curl and jq (Docker Hub tag discovery)
- docker (only if building from a Dockerfile)

Install via helper script (recommended)
--------------------------------------

From the repo root:

.. code-block:: bash

   ./scripts/install.sh
   source .appsrunner/bin/activate

Full GUI + CLI install:

.. code-block:: bash

   ./scripts/install.sh --full
   source .appsrunner/bin/activate

Notes
-----

- Core install is CLI-only; GUI dependencies require --full.
- If UV is not installed, the installer will prompt for it.

