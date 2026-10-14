"""Sphinx configuration adapted from LINCC Python Project Template v2.3.0."""

from importlib.metadata import version as package_version
from pathlib import Path

project = "Astro Stampede"
author = "Colin Chandler"
copyright = "2026, Colin Chandler"
release = package_version("astro-stampede")
version = release
extensions = ["myst_parser", "sphinx.ext.napoleon", "autoapi.extension"]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
exclude_patterns = ["_build"]
html_theme = "sphinx_rtd_theme"
html_show_sourcelink = False
autoapi_type = "python"
autoapi_dirs = [str(Path(__file__).resolve().parents[1] / "src")]
autoapi_ignore = []
autoapi_add_toctree_entry = False
autoapi_keep_files = False
autoapi_member_order = "bysource"
autoapi_options = ["members", "undoc-members", "show-inheritance", "show-module-summary"]


def skip_generated_version(app, what, name, obj, skip, options):
    """Resolve generated version imports without documenting their private module."""
    if name == "astro_stampede._version" or name.startswith("astro_stampede._version."):
        return True
    return None


def setup(app):
    app.connect("autoapi-skip-member", skip_generated_version)
