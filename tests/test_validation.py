import csv
import json
from pathlib import Path

import pytest

from astro_stampede import cli, review
from astro_stampede.demo import generate
from astro_stampede.validation import ValidationError


@pytest.fixture
def dataset(tmp_path):
    root = tmp_path / "synthetic"
    generate(root)
    return root


def rewrite(root, mutate):
    path = root / "manifest.csv"
    with path.open() as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    mutate(rows)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def failure(root, capsys, *extra):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--root", str(root), "--validate", *extra])
    assert exc.value.code == 2
    output = capsys.readouterr().err
    assert "problem(s) found:" in output
    assert "Traceback" not in output
    assert not (root / "state").exists()
    return output


def test_combined_errors_and_order(dataset, capsys, tmp_path):
    def mutate(rows):
        rows[0].update(
            object_id="", order="nan", relative_path="absent.png", metadata="[]", comparison_id="missing"
        )
        rows[1]["image_id"] = rows[0]["image_id"]

    rewrite(dataset, mutate)
    settings = tmp_path / "config.json"
    settings.write_text(json.dumps({"mystery": True, "quiet": "yes", "port": -1}))
    output = failure(dataset, capsys, "--config", str(settings), "--score-min", "8", "--score-max", "2")
    for text in (
        "mystery",
        "quiet",
        "--port",
        "--score-min/--score-max",
        "row 2, object_id",
        "row 2, relative_path",
        "absent.png",
        "row 2, order",
        "row 2, metadata",
        "row 2, comparison_id",
        "row 3, image_id",
    ):
        assert text in output
    assert output.index("mystery") < output.index("row 2, object_id")
    assert output == failure(
        dataset, capsys, "--config", str(settings), "--score-min", "8", "--score-max", "2"
    )


def test_header_errors(dataset, capsys):
    (dataset / "manifest.csv").write_text("image_id,image_id,extra\na,b,c\n")
    output = failure(dataset, capsys)
    assert "missing columns: object_id, relative_path" in output
    assert "unknown columns: extra" in output
    assert "duplicate columns: image_id" in output
    assert "row 2" not in output


def test_json_location(dataset, capsys, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{\n nope}")
    output = failure(dataset, capsys, "--config", str(path))
    assert f"Config {path}, line 2, column 2" in output


def test_invalid_config_prerequisite(dataset, capsys, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"manifest": 5, "tags": 5}))
    output = failure(dataset, capsys, "--config", str(path))
    assert "2 problem(s)" in output
    assert "manifest: Expected a nonempty path string" in output
    assert "tags: Expected a nonempty list" in output
    assert "Read manifest" not in output


def test_png_errors_include_comparisons(dataset, capsys):
    with (dataset / "manifest.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    for row in rows[:2]:
        (dataset / row["relative_path"]).write_bytes(b"broken")
    output = failure(dataset, capsys)
    assert "row 2, relative_path" in output
    assert "row 3, relative_path" in output
    assert output.count("not a valid PNG") == 2


def test_permissions_and_missing_manifest(dataset, capsys, monkeypatch):
    original = Path.open

    def denied(path, *args, **kwargs):
        if path == dataset / "manifest.csv":
            raise PermissionError(13, "Permission denied", str(path))
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", denied)
        output = failure(dataset, capsys)
    assert "Read manifest" in output and "Permission denied" in output
    (dataset / "manifest.csv").unlink()
    assert "No such file" in failure(dataset, capsys)


def test_database_collision_and_existing_state_preserved(dataset, capsys):
    labels = dataset / "existing.sqlite"
    labels.write_bytes(b"existing state")
    output = failure(dataset, capsys, "--db", str(labels), "--index-db", str(labels))
    assert "separate files" in output and str(labels) in output
    assert labels.read_bytes() == b"existing state"


def test_success_does_not_open_stores(dataset, capsys, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Validation opened classification stores")

    monkeypatch.setattr(review, "serve", unexpected)
    cli.main(["--root", str(dataset), "--validate"])
    assert "classification state untouched" in capsys.readouterr().out
    assert not (dataset / "state").exists()


def test_expected_startup_failure(dataset, capsys, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(dataset / "state" / "labels.sqlite"))

    monkeypatch.setattr(review, "serve", denied)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--root", str(dataset)])
    assert exc.value.code == 2
    output = capsys.readouterr().err
    assert "Startup failed" in output and "labels.sqlite" in output and "Permission denied" in output
    assert "Traceback" not in output


def test_exception_contract():
    error = ValidationError(["first", "second"])
    assert isinstance(error, ValueError)
    assert error.problems == ("first", "second")


def test_malformed_row_skips_dependent_reference(dataset, capsys):
    (dataset / "manifest.csv").write_text(
        "image_id,object_id,relative_path,comparison_id\nbroken\nprimary,object,missing.png,broken\n"
    )
    output = failure(dataset, capsys)
    assert "row 2, columns" in output
    assert "row 3, relative_path" in output
    assert "comparison_id:" not in output


def test_duplicate_path_and_invalid_reference(dataset, capsys):
    def mutate(rows):
        rows[1]["relative_path"] = rows[0]["relative_path"]
        rows[0]["comparison_id"] = rows[0]["image_id"]

    rewrite(dataset, mutate)
    output = failure(dataset, capsys)
    assert "row 3, relative_path: Duplicate image path" in output
    assert "row 2, comparison_id:" in output
