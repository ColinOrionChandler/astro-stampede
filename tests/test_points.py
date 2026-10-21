"""Pixel marks use synthetic images and isolated label stores only."""

import csv
import hashlib
import json
import os
import threading
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pandas as pd
import pytest

from astro_stampede import manifest, points, review


@pytest.fixture
def point_session(config):
    review.index_thumbnails(config)
    server = review.build_server(config, "127.0.0.1", 0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, payload=None, revision=0, origin=None):
        headers = {"X-Settings-Revision": str(revision)}
        if origin:
            headers["Origin"] = origin
        data = None if payload is None else json.dumps(payload).encode()
        with urlopen(
            Request(f"http://127.0.0.1:{server.server_port}/api/{path}", data=data, headers=headers),
            timeout=10,
        ) as response:
            return json.load(response)

    try:
        yield server, request
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def frames(request):
    obj = request("objects")[0]
    return request(f"objects/{obj['object_id']}/images")


def payload(frame, **extra):
    target = frame["point_target"]
    return {
        "target_id": target["target_id"],
        "source_revision": target["source_revision"],
        "x": 0,
        "y": target["height"] - 1,
        **extra,
    }


def test_http_point_lifecycle_and_independent_comparison(point_session):
    server, request = point_session
    config = server.config
    hashes = {p: hashlib.sha256(p.read_bytes()).digest() for p in config.root.rglob("*.png")}
    image = frames(request)[0]
    primary = payload(image)
    comparison = payload(image["comparison"], x=3, y=5)
    assert primary["target_id"] != comparison["target_id"]
    for data in (primary, comparison, dict(primary, x=9, y=8)):
        saved = request("image-point", data)["point"]
        assert (saved["x"], saved["y"]) == (data["x"], data["y"])
    image = frames(request)[0]
    assert (image["point"]["x"], image["comparison"]["point"]["x"]) == (9, 3)
    assert image["point"]["stale"] is False
    assert request("summary")["scored"] == 0
    request("image-score", {"image_id": image["image_id"], "score": 7})
    request("image-score/clear", {"image_id": image["image_id"]})
    assert frames(request)[0]["point"]["x"] == 9
    request("image-point/clear", primary)
    assert frames(request)[0]["point"] is None
    assert frames(request)[0]["comparison"]["point"]["x"] == 3
    with review.connect_review_db(config) as conn:
        events = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM review_events WHERE event_type LIKE 'image_point%' ORDER BY event_id"
            )
        ]
        assert len(events) == 4
        assert json.loads(events[2]["payload"])["previous"]["x"] == 0
        assert events[-1]["event_type"] == "image_point_clear"
    assert all(hashlib.sha256(p.read_bytes()).digest() == digest for p, digest in hashes.items())


@pytest.mark.parametrize(
    "extra",
    [
        {"x": -1},
        {"y": 100000},
        {"x": 1.2},
        {"x": True},
        {"x": "3"},
        {"x": None},
        {"y": float("nan")},
        {"x": float("inf")},
        {"target_id": "not-an-image"},
        {"target_id": "comparison:unknown"},
    ],
)
def test_point_validation(point_session, extra):
    _, request = point_session
    with pytest.raises(HTTPError) as exc:
        request("image-point", payload(frames(request)[0], **extra))
    assert exc.value.code == 400
    assert frames(request)[0]["point"] is None


def test_stale_sources_settings_and_origin(point_session):
    server, request = point_session
    image = frames(request)[0]
    data = payload(image)
    request("image-point", data)
    path = server.config.root / image["relative_path"]
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert frames(request)[0]["point"]["stale"]
    for route in ("image-point", "image-point/clear"):
        with pytest.raises(HTTPError) as exc:
            request(route, data)
        assert exc.value.code == 409
    current = payload(frames(request)[0], x=2)
    with pytest.raises(HTTPError) as exc:
        request("image-point", current, revision=1)
    assert exc.value.code == 409
    with pytest.raises(HTTPError) as exc:
        request("image-point", current, origin="https://example.invalid")
    assert exc.value.code == 403
    request("image-point", current)
    assert not frames(request)[0]["point"]["stale"]


def test_restart_reviewers_exports_and_snapshots(point_session, tmp_path):
    server, request = point_session
    config = server.config
    image = frames(request)[0]
    request("image-point", payload(image, x=12, y=13))
    request("image-point", payload(image["comparison"], x=21, y=22))
    other = replace(config, reviewer="another-reviewer")
    with review.connect_review_db(other) as conn:
        points.save(conn, other, server.comparisons, payload(image, x=34, y=35))
        conn.commit()
        images = review.query_object_images(conn, image["object_id"], other.reviewer)
        points.annotate(conn, other, server.comparisons, images)
        assert images[0]["point"]["x"] == 34
    review.index_thumbnails(config)
    assert frames(request)[0]["point"]["x"] == 12
    exported = request("export", {})
    with (config.export_dir / exported["image_points_csv"]).open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3
    assert all(not Path(row["relative_path"]).is_absolute() for row in rows)
    snapshot = review.classification_parquet_path(config)
    frame = pd.read_parquet(snapshot)
    assert set(frame.snapshot_version) == {3}
    assert sum(frame.record_type == "image_points") == 3
    restored = replace(config, db_path=tmp_path / "restored.sqlite")
    assert review.import_classification_parquet(restored.db_path, snapshot)["imported"]
    with review.connect_review_db(restored) as conn:
        assert conn.execute("SELECT COUNT(*) FROM image_points").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM review_events").fetchone()[0] == 3
    for version in (1, 2):
        old = frame[frame.record_type != "image_points"].copy()
        if version == 1:
            old = old[old.record_type.isin(["image_score", "object_review", "review_event", "snapshot"])]
        old["snapshot_version"] = version
        legacy = tmp_path / f"v{version}.parquet"
        old.to_parquet(legacy)
        assert review.import_classification_parquet(restored.db_path, legacy, force=True)["imported"]
        with review.connect_review_db(restored) as conn:
            assert conn.execute("SELECT COUNT(*) FROM image_points").fetchone()[0] == 0


def test_invalid_snapshot_is_atomic_and_legacy_db_is_additive(point_session, tmp_path):
    server, request = point_session
    image = frames(request)[0]
    request("image-score", {"image_id": image["image_id"], "score": 6})
    request("image-point", payload(image))
    request("export", {})
    frame = pd.read_parquet(review.classification_parquet_path(server.config))
    index = frame.index[frame.record_type == "image_points"][0]
    value = json.loads(frame.loc[index, "payload"])
    value["x"] = -5
    frame.loc[index, "payload"] = json.dumps(value)
    bad = tmp_path / "invalid.parquet"
    frame.to_parquet(bad)
    with pytest.raises(points.PointError):
        review.import_classification_parquet(server.config.db_path, bad, force=True)
    assert frames(request)[0]["point"]["x"] == 0
    with review.connect_review_db(server.config) as conn:
        conn.execute("DROP TABLE labels.image_points")
        conn.commit()
    with review.connect_review_db(server.config) as conn:
        assert conn.execute("SELECT COUNT(*) FROM image_points").fetchone()[0] == 0
        assert conn.execute("SELECT score FROM image_scores").fetchone()[0] == 6


def test_comparison_namespace_sharing_and_ordinary_pair_identity(config):
    review.index_thumbnails(config)
    catalog = manifest.comparisons(config)
    with review.connect_review_db(config) as conn:
        obj = review.query_objects(conn, config.reviewer, {})[0]
        images = review.query_object_images(conn, obj["object_id"], config.reviewer)
        catalog.images[images[1]["relative_path"]] = catalog.images[images[0]["relative_path"]]
        catalog.annotate(images)
        points.annotate(conn, config, catalog, images)
        assert images[0]["comparison"]["point_target"] == images[1]["comparison"]["point_target"]
        key = next(iter(catalog.paths))
        assert points.comparison_target(config, key) != points.comparison_target(
            replace(config, dataset="other"), key
        )
        images[0]["pairs"] = [{"image_id": images[1]["image_id"]}]
        points.save(conn, config, catalog, payload(images[1], x=17))
        points.annotate(conn, config, catalog, images)
        assert images[0]["pairs"][0]["point"]["x"] == 17
        assert images[0]["pairs"][0]["point_target"] == images[1]["point_target"]
