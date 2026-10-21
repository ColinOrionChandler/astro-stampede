"""Root-only launches exercise directory workflows without generic manifests."""

import csv
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from astro_stampede import cli, compatibility, points, review
from astro_stampede.adapters.comparisons import load_comparisons
from astro_stampede.demo import png_bytes


def rows(path, data):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(data[0]))
        writer.writeheader()
        writer.writerows(data)


def bundle(parent, name="synthetic-bundle", nested=False):
    outer = parent / name
    root = outer / name if nested else outer
    image = root / "cutouts" / "synthetic-bin" / "SyntheticObject" / "synthetic.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(png_bytes())
    return outer, root, image


@pytest.mark.parametrize("nested", [False, True])
def test_root_only_parent_selects_originals_and_explicit_comparisons(tmp_path, monkeypatch, nested):
    outer, root, image = bundle(tmp_path, nested=nested)
    comparison = tmp_path / (outer.name + "_comparisons")
    counterpart = comparison / "cutouts" / "comparison.png"
    counterpart.parent.mkdir(parents=True)
    counterpart.write_bytes(png_bytes(comparison=True))
    rows(
        root / "manifest.csv",
        [dict(target_key="synthetic-primary", png_path=image.relative_to(root).as_posix())],
    )
    rows(
        comparison / "manifest.csv",
        [dict(target_key="synthetic-comparison", png_path="cutouts/comparison.png")],
    )
    rows(
        comparison / "comparison_mapping.csv",
        [dict(primary_target_key="synthetic-primary", comparison_target_key="synthetic-comparison")],
    )
    captured = []
    monkeypatch.setattr(review, "serve", lambda config, **kwargs: captured.append(config))
    monkeypatch.chdir(tmp_path)
    cli.main(["--root", "."])
    config = captured[0]
    assert config.root == root
    assert config.products == ("cutouts",)
    assert config.db_path == root / "state" / "labels.sqlite"
    records = review.iter_png_records(config.root, config.products)
    assert len(records) == 1
    catalog = load_comparisons(config.root, config.comparison_root)
    assert catalog.images[records[0]["relative_path"]]["status"] == "available"
    assert not config.db_path.exists()


def test_root_only_without_any_manifest(tmp_path, capsys):
    _, root, _ = bundle(tmp_path)
    cli.main(["--root", str(tmp_path), "--validate"])
    assert "1 PNGs" in capsys.readouterr().out
    assert not (root / "state").exists()


def test_object_list_without_legacy_prefix_and_explicit_storage(tmp_path, monkeypatch):
    root = tmp_path / "images"
    image = root / "preliminary_visit_image" / "synthetic-class" / "SyntheticObject" / "synthetic.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(png_bytes())
    queue = tmp_path / "queue.csv"
    rows(queue, [dict(object_id="SyntheticObject")])
    captured = []
    monkeypatch.setattr(review, "serve", lambda config, **kwargs: captured.append(config))
    db = tmp_path / "canonical.sqlite"
    cli.main(["--root", str(root), "--object-list", str(queue), "--db", str(db)])
    assert captured[0].db_path == db
    assert captured[0].object_scope == (("synthetic-class", "SyntheticObject"),)


def test_ambiguous_bundle_parent_requires_selection(tmp_path):
    bundle(tmp_path, "synthetic-one")
    bundle(tmp_path, "synthetic-two")
    with pytest.raises(SystemExit):
        cli.main(["--root", str(tmp_path), "--validate"])
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("explicit", [False, True])
def test_only_default_index_enables_recovery(tmp_path, monkeypatch, explicit):
    bundle(tmp_path, nested=True)
    captured = []
    monkeypatch.setattr(review, "serve", lambda config, **kwargs: captured.append(kwargs))
    args = ["--root", str(tmp_path)]
    if explicit:
        args += ["--index-db", str(tmp_path / "explicit.sqlite")]
    cli.main(args)
    assert captured[0]["recover_index"] is (not explicit)


@pytest.mark.parametrize("legacy", [False, True])
def test_root_dot_rebuilds_nested_bundle_index(tmp_path, monkeypatch, legacy):
    _, root, _ = bundle(tmp_path, nested=True)
    launched = []

    def build(config, *args, **kwargs):
        launched.append(config)
        return SimpleNamespace(
            config=config,
            server_address=("127.0.0.1", 0),
            serve_forever=lambda: None,
            server_close=lambda: None,
            classification_exported_on_shutdown=False,
        )

    monkeypatch.setattr(review, "build_server", build)
    monkeypatch.chdir(tmp_path)
    cli.main(["--root", ".", "--no-open"])
    config = launched[-1]
    with review.connect_review_db(config) as conn:
        oid = conn.execute("SELECT object_id FROM images").fetchone()[0]
        images = review.query_object_images(conn, oid, config.reviewer)
        points.annotate(conn, config, None, images)
        target = images[0]["point_target"]
        points.save(
            conn,
            config,
            None,
            dict(target_id=target["target_id"], source_revision=target["source_revision"], x=5, y=6),
        )
        review.upsert_image_score(conn, config, dict(image_id=images[0]["image_id"], score=8))
        if legacy:
            conn.execute("UPDATE main.images SET image_id='legacy-image', product_image_id='legacy-image'")
            conn.execute("UPDATE image_scores SET image_id='legacy-image'")
            conn.execute("UPDATE review_events SET image_id='legacy-image' WHERE image_id IS NOT NULL")
            for table in ("identity_registry", "identity_aliases", "identity_provenance", "review_metadata"):
                conn.execute(f"DELETE FROM labels.{table}")
        conn.execute("UPDATE index_metadata SET value=? WHERE key='binding'", (json.dumps(["old-root"]),))
        conn.commit()
    with closing(sqlite3.connect(config.db_path)) as conn:
        before = list(conn.iterdump())
        historical = compatibility.fingerprint(conn)
    cli.main(["--root", ".", "--no-open"])
    recovered = launched[-1]
    assert recovered.root == root
    assert recovered.db_path == config.db_path
    assert recovered.index_db_path != config.index_db_path
    with closing(sqlite3.connect(config.db_path)) as conn:
        if not legacy:
            assert list(conn.iterdump()) == before
        assert compatibility.fingerprint(conn) == historical
    with review.connect_review_db(recovered) as conn:
        images = review.query_object_images(conn, oid, config.reviewer)
        points.annotate(conn, recovered, None, images)
        assert images[0]["score"] == 8
        assert (images[0]["point"]["x"], images[0]["point"]["y"]) == (5, 6)
