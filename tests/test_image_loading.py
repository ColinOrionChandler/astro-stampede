"""Image loading must avoid classification replay without weakening selection."""

import json
import threading
from dataclasses import replace
from urllib.request import Request, urlopen

from astro_stampede import review


def test_image_only_selection_preserves_scope_and_skips_score_replay(config, monkeypatch):
    review.index_thumbnails(config)
    with review.connect_review_db(config) as conn:
        rows = conn.execute("SELECT image_id, class_name, object_name FROM review_images").fetchall()

    def unexpected(*args, **kwargs):
        raise AssertionError("PNG lookup must not replay classification history")

    monkeypatch.setattr(review.identities, "refresh", unexpected)
    scoped = replace(config, image_scope=(rows[0]["image_id"],))
    with review.connect_review_db(scoped, image_only=True) as conn:
        assert [r[0] for r in conn.execute("SELECT image_id FROM review_images")] == [rows[0]["image_id"]]
    with review.connect_review_db(replace(config, object_scope=()), image_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM review_images").fetchone()[0] == 0


def test_rcc_image_lookup_retains_score_dependent_selection(config, monkeypatch):
    calls = []
    original = review.identities.refresh

    def refresh(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(review.identities, "refresh", refresh)
    with review.connect_review_db(replace(config, layout="rcc"), image_only=True):
        pass
    assert calls == [True]


def test_versioned_png_cache_changes_after_rescan_and_settings(config, monkeypatch):
    review.index_thumbnails(config)
    server = review.build_server(config, "127.0.0.1", 0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def get(path):
        with urlopen(base + path, timeout=10) as response:
            return response.read(), response.headers

    try:
        token = json.loads(get("/api/summary")[0])["image_cache_token"]
        objects = json.loads(get("/api/objects")[0])
        images = json.loads(get("/api/objects/" + objects[0]["object_id"] + "/images")[0])
        paths = ["/api/images/" + images[0]["image_id"], images[0]["comparison"]["url"]]
        original = review.identities.refresh

        def unexpected(*args, **kwargs):
            raise AssertionError("image HTTP request replayed scores")

        monkeypatch.setattr(review.identities, "refresh", unexpected)
        for path in paths:
            plain, headers = get(path)
            assert headers["Cache-Control"] == "no-store"
            cached, headers = get(path + "?v=" + token)
            assert cached == plain and plain.startswith(review.PNG_SIGNATURE)
            assert headers["Cache-Control"] == "private, max-age=3600"
        monkeypatch.setattr(review.identities, "refresh", original)
        with urlopen(Request(base + "/api/rescan", data=b"{}")) as response:
            assert response.status == 200
        after = json.loads(get("/api/summary")[0])["image_cache_token"]
        assert after != token
        assert get(paths[0] + "?v=" + token)[1]["Cache-Control"] == "no-store"
        server.settings_revision += 1
        assert json.loads(get("/api/summary")[0])["image_cache_token"] != after
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
