"""Shared synthetic fixtures; never open research datasets."""

import pytest

from astro_stampede import cli
from astro_stampede.demo import generate


@pytest.fixture
def config(tmp_path):
    root = tmp_path / "synthetic"
    generate(root)
    _, args = cli.parse_args(
        [
            "--root",
            str(root),
            "--manifest",
            str(root / "manifest.csv"),
            "--dataset",
            "test-universe",
            "--state-dir",
            str(tmp_path / "state"),
            "--reviewer",
            "synthetic-reviewer",
        ]
    )
    return cli.make_config(args)
