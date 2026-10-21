"""Exercise an installed distribution outside the source checkout using synthetic pixels.

Run this script with the Python interpreter of a fresh wheel or sdist environment.
The package must be installed normally, not in editable mode.
"""

import hashlib
import json
import threading
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen

import astro_stampede
from astro_stampede import cli, review
from astro_stampede.demo import generate


@contextmanager
def running_server(config):
    server = review.build_server(config, "127.0.0.1", 0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"

    def request(path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        # A fresh installation may need time for its first pandas/Arrow import.
        timeout = 60 if path == "/api/export" else 10
        with urlopen(Request(base + path, data=data), timeout=timeout) as response:
            return response.read()

    try:
        yield request
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def main():
    repository = Path(__file__).resolve().parents[1]
    assert not Path(astro_stampede.__file__).resolve().is_relative_to(repository), (
        "Use a non-editable install"
    )
    assert astro_stampede.__version__ == version("astro-stampede")
    with TemporaryDirectory(prefix="stampede-smoke-") as directory:
        root = Path(directory) / "synthetic"
        generate(root)
        hashes = {p: hashlib.sha256(p.read_bytes()).digest() for p in root.rglob("*.png")}
        _, args = cli.parse_args(["--root", str(root), "--reviewer", "synthetic-reviewer"])
        config = cli.make_config(args)
        review.index_thumbnails(config)
        with running_server(config) as request:
            assert b"Astro Stampede" in request("/")
            for name in ("app.js", "settings.js", "trails.js", "points.js", "styles.css"):
                assert request("/static/" + name)
            objects = json.loads(request("/api/objects"))
            object_id = objects[0]["object_id"]
            images = json.loads(request(f"/api/objects/{object_id}/images"))
            image_id = images[0]["image_id"]
            primary = request("/api/images/" + image_id)
            comparison = request(images[0]["comparison"]["url"])
            assert primary.startswith(review.PNG_SIGNATURE)
            assert comparison.startswith(review.PNG_SIGNATURE) and comparison != primary
            request("/api/image-score", {"image_id": image_id, "score": 6})
            for frame in (images[0], images[0]["comparison"]):
                target = frame["point_target"]
                request(
                    "/api/image-point",
                    {
                        "target_id": target["target_id"],
                        "source_revision": target["source_revision"],
                        "x": 15,
                        "y": 16,
                    },
                )
        review.index_thumbnails(config)
        with running_server(config) as request:
            images = json.loads(request(f"/api/objects/{object_id}/images"))
            restored = next(row for row in images if row["image_id"] == image_id)
            assert restored["score"] == 6
            assert restored["point"]["x"] == 15
            assert restored["comparison"]["point"]["y"] == 16
            exports = json.loads(request("/api/export", {}))
            assert (config.export_dir / exports["image_points_csv"]).is_file()
        assert all(hashlib.sha256(path.read_bytes()).digest() == digest for path, digest in hashes.items())
    print(f"Installed distribution smoke test passed: {astro_stampede.__version__}")


if __name__ == "__main__":
    main()
