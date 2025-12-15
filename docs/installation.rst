Installation
============

System prerequisites
-------------------

- Python 3.8+
- Apptainer (or Singularity) available as the ``apptainer`` command

For building containers with :file:`build_apptainer.sh`:

- ``curl`` and ``jq`` (Docker Hub tag discovery)
- ``docker`` (only if you build from a Dockerfile via ``-d``)

Python environment
------------------

From the repo root:

.. code-block:: bash

   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt

Minimal installs (core dependencies only) can use :file:`requirements-core.txt`.

