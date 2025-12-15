# Configuration file for the Sphinx documentation builder.

from __future__ import annotations

import os

project = "BIDS Apps Runner"
author = "Karl Koschutnig, MRI-Lab Graz"
copyright = f"{os.environ.get('SOURCE_DATE_EPOCH', '2025')}, {author}"

extensions: list[str] = [
    "sphinx.ext.duration",
    "sphinx.ext.viewcode",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]

# Keep headings consistent with GitHub rendering
pygments_style = "sphinx"
