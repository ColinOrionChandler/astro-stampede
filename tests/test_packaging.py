"""Verify the installed package metadata and browser resources."""

from importlib.metadata import distribution, version
from importlib.resources import files

import astro_stampede


def test_installed_version_and_entry_point():
    assert astro_stampede.__version__ == version("astro-stampede")
    scripts = {entry.name: entry.value for entry in distribution("astro-stampede").entry_points}
    assert scripts["astro-stampede"] == "astro_stampede.cli:main"


def test_packaged_browser_resources():
    static = files("astro_stampede").joinpath("review_static")
    for name in ("index.html", "app.js", "settings.js", "trails.js", "points.js", "styles.css"):
        assert static.joinpath(name).read_bytes()
