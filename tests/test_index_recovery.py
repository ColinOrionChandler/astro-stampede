"""Startup may replace a default cache, never the authoritative review state."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest

from astro_stampede import cli, compatibility, manifest, points, review


def label_rows(config):
    with closing(sqlite3.connect(config.db_path)) as conn:
        tables = [
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        ]
        return {name: sorted(conn.execute(f'SELECT * FROM "{name}"').fetchall()) for name in tables}


def mismatch(config, field=2):
    binding = json.loads(review.index_binding(config))
    binding[field] = {"version": 1} if field == 3 else "previous-input"
    with closing(sqlite3.connect(config.index_db_path)) as conn:
        conn.execute("UPDATE index_metadata SET value=? WHERE key='binding'", (json.dumps(binding),))
        conn.commit()


def seed_reviews(config):
    review.index_thumbnails(config)
    catalog = manifest.comparisons(config)
    for reviewer in (config.reviewer, "second-reviewer"):
        current = replace(config, reviewer=reviewer)
        with review.connect_review_db(current) as conn:
            oid = conn.execute("SELECT object_id FROM images ORDER BY relative_path LIMIT 1").fetchone()[0]
            images = review.query_object_images(conn, oid, reviewer)
            catalog.annotate(images)
            points.annotate(conn, current, catalog, images)
            image = images[0]
            review.upsert_image_score(
                conn, current, dict(image_id=image["image_id"], score=7, comment="keep")
            )
            review.upsert_object_review(conn, current, dict(object_id=oid, active_flag=True, comment="keep"))
            for frame in (image, image["comparison"]):
                target = frame["point_target"]
                points.save(
                    conn,
                    current,
                    catalog,
                    dict(target_id=target["target_id"], source_revision=target["source_revision"], x=3, y=4),
                )
            conn.commit()
    return oid


def stop_after_start(monkeypatch):
    original = review.build_server
    launched = []

    def build(*args, **kwargs):
        server = original(*args, **kwargs)
        server.serve_forever = lambda: None
        launched.append(server.config)
        return server

    monkeypatch.setattr(review, "build_server", build)
    return launched


@pytest.mark.parametrize("field", range(4), ids=["layout", "dataset", "root", "identity-policy"])
def test_recovery_preserves_all_labels_and_points(config, monkeypatch, field):
    oid = seed_reviews(config)
    before = label_rows(config)
    pngs = {path: path.read_bytes() for path in config.root.rglob("*.png")}
    mismatch(config, field)
    old_index = config.index_db_path.read_bytes()
    launched = stop_after_start(monkeypatch)
    for _ in range(2):
        review.serve(config, host="127.0.0.1", port=0, open_browser=False, recover_index=True)
        recovered = launched[-1]
        assert recovered.index_db_path != config.index_db_path
        assert replace(recovered, index_db_path=config.index_db_path) == config
        assert label_rows(config) == before
        assert config.index_db_path.read_bytes() == old_index
        with review.connect_review_db(recovered) as conn:
            images = review.query_object_images(conn, oid, config.reviewer)
            catalog = manifest.comparisons(recovered)
            catalog.annotate(images)
            points.annotate(conn, recovered, catalog, images)
            assert images[0]["score"] == 7
            for frame in (images[0], images[0]["comparison"]):
                assert (frame["point"]["x"], frame["point"]["y"]) == (3, 4)
                assert not frame["point"]["stale"]
    assert launched[0].index_db_path == launched[1].index_db_path
    assert all(path.read_bytes() == data for path, data in pngs.items())


def test_explicit_index_still_rejects_mismatch(config):
    seed_reviews(config)
    before = label_rows(config)
    mismatch(config)
    with pytest.raises(ValueError, match="different dataset or root"):
        review.serve(config, host="127.0.0.1", port=0, open_browser=False)
    assert label_rows(config) == before


def test_recovery_does_not_override_label_identity_policy(config):
    seed_reviews(config)
    before = label_rows(config)
    changed = replace(config, identity_namespace="different-policy")
    with pytest.raises(ValueError, match="policy"):
        review.serve(changed, host="127.0.0.1", port=0, open_browser=False, recover_index=True)
    assert label_rows(config) == before


def test_default_index_reuse_and_collision(config):
    assert review.recover_default_index(config) is config
    assert not config.index_db_path.exists()
    review.index_thumbnails(config)
    assert review.recover_default_index(config) is config
    mismatch(config)
    first = review.recover_default_index(config)
    review.index_thumbnails(first)
    mismatch(first)
    collision_bytes = first.index_db_path.read_bytes()
    second = review.recover_default_index(config)
    assert second.index_db_path not in (first.index_db_path, config.index_db_path)
    review.index_thumbnails(second)
    assert review.recover_default_index(config) == second
    assert first.index_db_path.read_bytes() == collision_bytes


def test_recovery_never_selects_label_database(config):
    with pytest.raises(ValueError, match="separate files"):
        review.recover_default_index(replace(config, index_db_path=config.db_path))


@pytest.mark.parametrize("selection", ["default", "explicit", "saved-config"])
def test_manifest_launch_index_selection(config, monkeypatch, tmp_path, selection):
    captured = []
    monkeypatch.setattr(review, "serve", lambda config, **kwargs: captured.append(kwargs))
    args = ["--root", str(config.root)]
    if selection == "explicit":
        args += ["--index-db", str(config.index_db_path)]
    elif selection == "saved-config":
        path = tmp_path / "settings.json"
        path.write_text(json.dumps(dict(root=str(config.root), index_db=str(config.index_db_path))))
        args = ["--config", str(path)]
    cli.main(args)
    assert captured[0]["recover_index"] is (selection == "default")


def test_validation_does_not_recover_or_write_state(config):
    seed_reviews(config)
    mismatch(config)
    before = {path: path.read_bytes() for path in config.db_path.parent.iterdir() if path.is_file()}
    cli.main(
        [
            "--root",
            str(config.root),
            "--state-dir",
            str(config.db_path.parent),
            "--dataset",
            config.dataset,
            "--validate",
        ]
    )
    assert {path: path.read_bytes() for path in config.db_path.parent.iterdir() if path.is_file()} == before


def make_legacy_store(config):
    """Model an upgrade from old image IDs with no identity-policy metadata."""
    oid = seed_reviews(config)
    with closing(sqlite3.connect(config.index_db_path)) as conn:
        images = [row[0] for row in conn.execute("SELECT image_id FROM images")]
        objects = [row[0] for row in conn.execute("SELECT DISTINCT object_id FROM images")]
        for iid in images:
            conn.execute(
                "UPDATE images SET image_id=?, product_image_id=? WHERE image_id=?",
                ("source-" + iid, "old-" + iid, iid),
            )
        for obj in objects:
            conn.execute("UPDATE images SET object_id=? WHERE object_id=?", ("old-" + obj, obj))
        conn.commit()
    with closing(sqlite3.connect(config.db_path)) as conn:
        for kind, ids, table in (("image", images, "image_scores"), ("object", objects, "object_reviews")):
            for value in ids:
                conn.execute(f"UPDATE {table} SET {kind}_id=? WHERE {kind}_id=?", ("old-" + value, value))
                conn.execute(
                    f"UPDATE review_events SET {kind}_id=? WHERE {kind}_id=?", ("old-" + value, value)
                )
        for table in ("identity_registry", "identity_aliases", "identity_provenance", "review_metadata"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    mismatch(config)
    return oid


@pytest.mark.parametrize("unbound", [False, True])
def test_legacy_upgrade_reconnects_scores_and_preserves_raw_history(config, monkeypatch, unbound):
    oid = make_legacy_store(config)
    if unbound:
        with closing(sqlite3.connect(config.index_db_path)) as conn:
            conn.execute("DROP TABLE index_metadata")
            conn.commit()
    with closing(sqlite3.connect(config.db_path)) as conn:
        before = compatibility.fingerprint(conn)
    original_index = config.index_db_path.read_bytes()
    launched = stop_after_start(monkeypatch)
    for _ in range(2):
        review.serve(config, host="127.0.0.1", port=0, open_browser=False, recover_index=True)
        recovered = launched[-1]
        with review.connect_review_db(recovered) as conn:
            for reviewer in (config.reviewer, "second-reviewer"):
                current = replace(recovered, reviewer=reviewer)
                images = review.query_object_images(conn, oid, reviewer)
                catalog = manifest.comparisons(current)
                catalog.annotate(images)
                points.annotate(conn, current, catalog, images)
                assert images[0]["score"] == 7
                assert images[0]["point"]["x"] == images[0]["comparison"]["point"]["x"] == 3
            assert conn.execute("SELECT COUNT(*) FROM effective_object_reviews").fetchone()[0] == 2
        with closing(sqlite3.connect(config.db_path)) as conn:
            assert compatibility.fingerprint(conn) == before
            assert conn.execute("SELECT COUNT(*) FROM identity_aliases").fetchone()[0] > 0
        assert config.index_db_path.read_bytes() == original_index


@pytest.mark.parametrize("missing", ["score", "object", "event", "point"])
def test_incomplete_legacy_recovery_preserves_store_and_explains_evidence(config, missing):
    make_legacy_store(config)
    with closing(sqlite3.connect(config.db_path)) as conn:
        table, field = {
            "score": ("image_scores", "image_id"),
            "object": ("object_reviews", "object_id"),
            "event": ("review_events", "image_id"),
            "point": ("image_points", "target_id"),
        }[missing]
        conn.execute(f"UPDATE {table} SET {field}='unmapped' WHERE rowid=(SELECT MIN(rowid) FROM {table})")
        conn.commit()
    before = label_rows(config)
    with pytest.raises(ValueError, match="unmapped historical") as exc:
        review.serve(config, host="127.0.0.1", port=0, open_browser=False, recover_index=True)
    assert str(config.index_db_path) in str(exc.value)
    assert "read-only report" in str(exc.value)
    assert label_rows(config) == before


def test_legacy_conflict_rolls_back_registration(config):
    make_legacy_store(config)
    with closing(sqlite3.connect(config.index_db_path)) as conn:
        source, old = conn.execute(
            "SELECT image_id, product_image_id FROM images ORDER BY relative_path LIMIT 1"
        ).fetchone()
    with closing(sqlite3.connect(config.db_path)) as conn:
        row = list(conn.execute("SELECT * FROM image_scores WHERE image_id=? LIMIT 1", (old,)).fetchone())
        row[0], row[2], row[-2] = source, 1, "unattributed-session"
        conn.execute("INSERT INTO image_scores VALUES (?,?,?,?,?,?,?,?)", row)
        conn.commit()
    before = label_rows(config)
    with pytest.raises(ValueError, match="unresolved classification conflicts"):
        review.serve(config, host="127.0.0.1", port=0, open_browser=False, recover_index=True)
    assert label_rows(config) == before


def test_contradictory_retained_index_does_not_register_aliases(config):
    make_legacy_store(config)
    with closing(sqlite3.connect(config.index_db_path)) as conn:
        rows = conn.execute("SELECT image_id, product_image_id FROM images ORDER BY relative_path").fetchall()
        conn.execute("UPDATE images SET product_image_id=? WHERE image_id=?", (rows[0][1], rows[1][0]))
        conn.commit()
    before = label_rows(config)
    with pytest.raises(ValueError, match="contradictory mappings"):
        review.serve(config, host="127.0.0.1", port=0, open_browser=False, recover_index=True)
    assert label_rows(config) == before


def test_explicit_legacy_index_does_not_register_aliases(config):
    make_legacy_store(config)
    with closing(sqlite3.connect(config.index_db_path)) as conn:
        conn.execute("UPDATE index_metadata SET value=? WHERE key='binding'", (review.index_binding(config),))
        conn.commit()
    before = label_rows(config)
    with pytest.raises(ValueError, match="identity compatibility preflight"):
        review.serve(config, host="127.0.0.1", port=0, open_browser=False)
    assert label_rows(config) == before
