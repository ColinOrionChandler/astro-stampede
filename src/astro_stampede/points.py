"""Durable pixel marks, separate from classifications and source PNGs."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from . import identities


class PointError(ValueError):
    """A safe, user-facing point validation error."""


class StalePointError(PointError):
    """The browser is marking an obsolete source revision."""


def initialize(conn, schema="main"):
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {schema}.image_points (
        target_id TEXT NOT NULL, reviewer TEXT NOT NULL, kind TEXT NOT NULL,
        image_id TEXT, object_id TEXT, dataset TEXT NOT NULL,
        relative_path TEXT NOT NULL, source_revision TEXT NOT NULL,
        width INTEGER NOT NULL, height INTEGER NOT NULL,
        x INTEGER NOT NULL, y INTEGER NOT NULL,
        session_id TEXT NOT NULL, updated_at TEXT NOT NULL,
        PRIMARY KEY(target_id, reviewer))""")


def comparison_target(config, key):
    digest = hashlib.sha256(json.dumps([config.dataset, key]).encode()).hexdigest()
    return f"comparison:{digest}"


@lru_cache(maxsize=2048)
def source_dimensions(path, size, mtime_ns):
    # Cache only header geometry; every lookup still stats the current source.
    from .review import read_png_size

    return read_png_size(Path(path))


def descriptor(config, path, roots, **fields):
    path = path.resolve()
    source = next(
        (
            (index, path.relative_to(root.resolve()).as_posix())
            for index, root in enumerate(roots)
            if path.is_relative_to(root.resolve())
        ),
        None,
    )
    if source is None or not path.is_file():
        raise PointError("Point source is unavailable in this dataset.")
    stat = path.stat()
    width, height = source_dimensions(str(path), stat.st_size, stat.st_mtime_ns)
    if width <= 0 or height <= 0:
        raise PointError("Point source has invalid dimensions.")
    revision = hashlib.sha256(
        json.dumps([source, stat.st_size, stat.st_mtime_ns, width, height]).encode()
    ).hexdigest()
    return dict(
        fields,
        dataset=config.dataset,
        relative_path=source[1],
        source_revision=revision,
        width=width,
        height=height,
    )


def comparison_sources(conn, config, catalog):
    if not catalog:
        return {}
    eligible = {row[0] for row in conn.execute("SELECT relative_path FROM review_images")}
    linked = {
        item["url"].rsplit("/", 1)[-1]
        for relative, item in catalog.images.items()
        if relative in eligible and item.get("url")
    }
    return {comparison_target(config, key): catalog.paths[key] for key in linked if key in catalog.paths}


def resolve(conn, config, catalog, target_id, *, comparisons=None):
    if not isinstance(target_id, str):
        raise PointError("A point target is required.")
    if target_id.startswith("comparison:"):
        sources = comparison_sources(conn, config, catalog) if comparisons is None else comparisons
        path = sources.get(target_id)
        if path is None:
            raise PointError("Comparison point target is unavailable.")
        return descriptor(
            config,
            path,
            (catalog.root,),
            target_id=target_id,
            kind="comparison",
            image_id=None,
            object_id=None,
        )
    image_id = identities.selected_id(conn, target_id)
    row = conn.execute("SELECT * FROM review_images WHERE image_id=?", (image_id,)).fetchone()
    if row is None:
        raise PointError("Image point target is unavailable in this dataset.")
    path = Path(row["source_path"]) if row["source_path"] else config.root / row["relative_path"]
    roots = (config.root, *config.additional_roots) if config.layout == "active-asteroids" else (config.root,)
    return descriptor(
        config, path, roots, target_id=image_id, kind="image", image_id=image_id, object_id=row["object_id"]
    )


def annotate(conn, config, catalog, images):
    cache = {}
    comparisons = comparison_sources(conn, config, catalog)
    for image in images:
        frames = [image, *image.get("pairs", [])]
        comparison = image.get("comparison")
        if comparison and comparison.get("url"):
            frames.append(comparison)
        for frame in frames:
            target_id = (
                comparison_target(config, frame["url"].rsplit("/", 1)[-1])
                if frame is comparison
                else frame["image_id"]
            )
            if target_id not in cache:
                try:
                    target = resolve(conn, config, catalog, target_id, comparisons=comparisons)
                    row = conn.execute(
                        "SELECT * FROM image_points WHERE target_id=? AND reviewer=?",
                        (target["target_id"], config.reviewer),
                    ).fetchone()
                    point = dict(row) if row else None
                    if point:
                        point["stale"] = point["source_revision"] != target["source_revision"]
                    cache[target_id] = {"point_target": target, "point": point}
                except (OSError, ValueError):
                    cache[target_id] = {"point_target": None, "point": None}
            frame.update(cache[target_id])
    return images


def validate_record(value):
    """Validate portable point rows before replacing any existing label state."""
    required = {
        "target_id",
        "reviewer",
        "kind",
        "image_id",
        "object_id",
        "dataset",
        "relative_path",
        "source_revision",
        "width",
        "height",
        "x",
        "y",
        "session_id",
        "updated_at",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise PointError("Invalid snapshot image point fields.")
    if value["kind"] not in {"image", "comparison"}:
        raise PointError("Invalid point kind.")
    for name in ("target_id", "reviewer", "relative_path", "source_revision", "session_id", "updated_at"):
        if not isinstance(value[name], str) or not value[name]:
            raise PointError(f"Invalid point {name}.")
    if Path(value["relative_path"]).is_absolute() or ".." in Path(value["relative_path"]).parts:
        raise PointError("Point paths must be relative.")
    validate_coordinates(value, value)


def validate_coordinates(payload, target):
    if any(type(target[key]) is not int or target[key] <= 0 for key in ("width", "height")):
        raise PointError("Invalid point dimensions.")
    for key, limit in (("x", target["width"]), ("y", target["height"])):
        if type(payload.get(key)) is not int or not 0 <= payload[key] < limit:
            raise PointError(f"Point {key} must be an integer from 0 through {limit - 1}.")


def save(conn, config, catalog, payload, *, clear=False):
    from .review import record_event, utc_now

    target = resolve(conn, config, catalog, payload.get("target_id"))
    if payload.get("source_revision") != target["source_revision"]:
        raise StalePointError("Image changed. Rescan and reload it before recording a point.")
    if not clear:
        validate_coordinates(payload, target)
    previous = conn.execute(
        "SELECT * FROM image_points WHERE target_id=? AND reviewer=?", (target["target_id"], config.reviewer)
    ).fetchone()
    point = None
    if clear:
        conn.execute(
            "DELETE FROM image_points WHERE target_id=? AND reviewer=?",
            (target["target_id"], config.reviewer),
        )
    else:
        point = dict(
            target,
            reviewer=config.reviewer,
            x=payload["x"],
            y=payload["y"],
            session_id=config.session_id,
            updated_at=utc_now(),
        )
        columns = list(point)
        conn.execute(
            f"INSERT INTO image_points ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
            f"ON CONFLICT(target_id,reviewer) DO UPDATE SET "
            + ",".join(f"{key}=excluded.{key}" for key in columns if key not in {"target_id", "reviewer"}),
            tuple(point.values()),
        )
    record_event(
        conn,
        config=config,
        event_type="image_point_clear" if clear else "image_point",
        object_id=target["object_id"],
        image_id=target["image_id"],
        payload={
            "target_id": target["target_id"],
            "previous": dict(previous) if previous else None,
            "point": point,
        },
    )
    return {"target_id": target["target_id"], "point": point}
