"""Local review engine with explicit compatibility layout adapters."""

from __future__ import annotations

import argparse
import csv
import getpass
import gzip
import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
import sqlite3
import struct
import sys
import threading
import time
import webbrowser
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from itertools import chain
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from astro_stampede.adapters.rcc_names import (
    THUMBNAIL_ANNOTATION_SEPARATOR,
    encode_tisserand_jupiter,
    get_thumbnail_identity,
    get_thumbnail_product_identity,
)
from astro_stampede.adapters.comparisons import ComparisonCatalog, load_comparisons
from astro_stampede import manifest, trails


DEFAULT_ROOT = Path(".")
DEFAULT_PRODUCTS = ("preliminary_visit_image",)
DEFAULT_CLASSIFICATION_DIR = Path.home() / ".local" / "share" / "astro-stampede"
DEFAULT_DB = DEFAULT_CLASSIFICATION_DIR / "labels.sqlite"
DEFAULT_CLASSIFICATION_PARQUET = DEFAULT_CLASSIFICATION_DIR / "classifications.parquet"
DEFAULT_EXPORT_DIR = DEFAULT_CLASSIFICATION_DIR / "exports"
ACTIVE_ASTEROIDS_PRODUCT = "active_asteroids"
ACTIVE_ASTEROIDS_OUTPUT_DIR = DEFAULT_CLASSIFICATION_DIR / "active-asteroids"
ACTIVE_ASTEROIDS_DB = ACTIVE_ASTEROIDS_OUTPUT_DIR / "labels.sqlite"
ACTIVE_ASTEROIDS_CLASSIFICATION_PARQUET = ACTIVE_ASTEROIDS_OUTPUT_DIR / "classifications.parquet"
ACTIVE_ASTEROIDS_EXPORT_DIR = ACTIVE_ASTEROIDS_OUTPUT_DIR / "exports"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
IDENTITY_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")
EDP2_DEEP_COADD_RE = re.compile(
    r"^(?P<object>.+)_(?P<visit>\d{13})_(?P<detector>\d+)_"
    r"(?P<filter>[ugrizy])_deep_coadd_n(?P<coadd_visits>\d+)_"
    r"(?P<width>\d+)x(?P<height>\d+)(?:_\d+(?:\.\d+)?dPix)?$"
)
ACTIVE_ASTEROIDS_FILENAME_RE = re.compile(
    r"^(?P<object_id>[^_]+)_(?P<observation>\d{8}-\d{5})_(?P<visit>[^_]+)"
)
ORBITAL_CACHE_RELATIVE_PATH = Path("data/object_info/orbits/orbital_elements.parquet")
DEFAULT_INDEX_CACHE_ROOT = Path.home() / ".cache" / "astro-stampede"
LABEL_SCHEMA = "labels"
DEFAULT_MIN_IMAGES = 2
DEFAULT_IMAGE_CONTEXT = 10
DEFAULT_BATCH_SIZE = 100
IMAGE_PATH_COLUMNS = ("relative_path", "png_path", "input_path", "path")
REVEAL_SCORE_COLUMNS = ("model_score_r3", "model_score_operational")


@dataclass(frozen=True)
class ReviewQueueObject:
    product: str
    class_name: str
    object_name: str
    queue_rank: int
    batch_number: int


@dataclass(frozen=True)
class RevealScore:
    image_id: str
    model_score_r3: float
    model_score_operational: float


@dataclass(frozen=True)
class RevealSelection:
    scores: tuple[RevealScore, ...]
    summary: dict[str, int]


@dataclass(frozen=True)
class ActiveAsteroidsImageSource:
    path: Path
    source_root: Path
    source_index: int
    relative_path: str
    logical_filename: str


@dataclass(frozen=True)
class RCCImageCandidate:
    path: Path
    image_id: str
    product_image_id: str
    timestamp: str | None
    annotation: str


@dataclass(frozen=True)
class ReviewConfig:
    root: Path
    products: tuple[str, ...]
    classes: tuple[str, ...] | None
    object_scope: tuple[tuple[str, str], ...] | None
    object_list_path: Path | None
    db_path: Path
    index_db_path: Path
    reviewer: str
    session_id: str
    export_dir: Path
    orbital_cache_path: Path | None = None
    classification_parquet_path: Path | None = None
    image_scope: tuple[str, ...] | None = None
    image_paths: tuple[str, ...] | None = None
    image_list_path: Path | None = None
    image_context: int = DEFAULT_IMAGE_CONTEXT
    image_list_summary: dict[str, int] | None = None
    layout: str = "rcc"
    batch_size: int = DEFAULT_BATCH_SIZE
    queue_objects: tuple[ReviewQueueObject, ...] | None = None
    queue_summary: dict[str, int] | None = None
    additional_roots: tuple[Path, ...] = ()
    reveal_list_path: Path | None = None
    reveal_scores: tuple[RevealScore, ...] = ()
    reveal_summary: dict[str, int] | None = None
    reveal_auto_detected: bool = False
    comparison_root: Path | None = None
    manifest_path: Path | None = None
    dataset: str = ""
    title: str = "Astro Stampede"
    score_min: int = 0
    score_max: int = 9
    tags: tuple[str, ...] | None = None
    show_metadata: bool = True
    import_snapshot: bool = False


@dataclass(frozen=True)
class ImageListSelection:
    relative_paths: tuple[str, ...]
    image_ids: tuple[str, ...]
    object_scope: tuple[tuple[str, str], ...]
    summary: dict[str, int]


@dataclass(frozen=True)
class ActiveAsteroidsSelection:
    relative_paths: tuple[str, ...]
    image_ids: tuple[str, ...]
    object_scope: tuple[tuple[str, str], ...]
    queue_objects: tuple[ReviewQueueObject, ...]
    summary: dict[str, int]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def object_id_for(product: str, class_name: str, object_name: str) -> str:
    return stable_id(f"{product}/{class_name}/{object_name}")


def image_id_for(product: str, class_name: str, object_name: str, identity: str) -> str:
    return stable_id(f"{product}/{class_name}/{object_name}/{identity}")


def product_image_id_for(
    product: str,
    class_name: str,
    object_name: str,
    filename: str,
    *,
    fallback_image_id: str,
) -> str:
    """Return a timestamp-independent RCC PNG product identity."""

    identity = get_thumbnail_product_identity(
        filename,
        dataset_type=product,
        class_name=class_name,
    )
    if identity is None:
        return fallback_image_id
    fields = (
        product,
        class_name,
        object_name,
        identity.instrument,
        identity.visit,
        identity.detector_key,
        identity.product_kind,
    )
    return stable_id("/".join(fields))


def _timestamp_has_fractional_seconds(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    fraction = value.rsplit(".", 1)[1]
    return bool(fraction and fraction.strip("0"))


def _candidate_preference_key(
    candidate: RCCImageCandidate,
    preferred_image_ids: set[str],
) -> tuple[int, int, int, str]:
    return (
        0 if candidate.image_id in preferred_image_ids else 1,
        0 if _timestamp_has_fractional_seconds(candidate.timestamp) else 1,
        0 if candidate.annotation.strip() else 1,
        candidate.path.as_posix(),
    )


def object_scope_key(class_name: str, object_name: str) -> tuple[str, str]:
    return (str(class_name).strip(), str(object_name).strip())


def active_asteroids_metadata(filename: str) -> dict[str, Any]:
    """Parse ordering metadata from a flat Active Asteroids cutout name."""

    logical_filename = active_asteroids_logical_filename(filename) or filename
    parsed = parse_filename_metadata(logical_filename)
    matched = ACTIVE_ASTEROIDS_FILENAME_RE.match(parsed["identity"])
    if matched is None:
        return parsed
    parsed.update(
        {
            "object_from_filename": matched.group("object_id"),
            "timestamp": matched.group("observation"),
            "instrument": "Active Asteroids",
            "visit": matched.group("visit"),
        }
    )
    return parsed


def active_asteroids_logical_filename(filename_or_path: str | Path) -> str | None:
    """Return the logical .png name represented by .png or .png.gz."""

    name = Path(filename_or_path).name
    lowered = name.lower()
    if lowered.endswith(".png.gz"):
        return name[:-3]
    if lowered.endswith(".png"):
        return name
    return None


def _is_rsync_partial_path(relative: Path) -> bool:
    return any(
        part in {".rcc-aa-fetch-partial", ".rsync-partial"} for part in relative.parts
    )


def discover_active_asteroids_pngs(
    root: Path,
    additional_roots: tuple[Path, ...] = (),
) -> dict[str, list[ActiveAsteroidsImageSource]]:
    """Index AA PNGs by filename object prefix across primary and extra roots."""

    sources_by_object: dict[str, list[ActiveAsteroidsImageSource]] = {}
    seen_roots: set[Path] = set()
    for source_index, source_root in enumerate((root, *additional_roots)):
        source_root = Path(source_root).expanduser().resolve()
        if source_root in seen_roots:
            continue
        seen_roots.add(source_root)
        if not source_root.is_dir():
            continue
        candidates = (
            source_root.glob("*.png")
            if source_index == 0
            else chain(
                source_root.rglob("*.png"),
                source_root.rglob("*.png.gz"),
            )
        )
        for candidate in candidates:
            logical_filename = active_asteroids_logical_filename(candidate)
            if (
                not candidate.is_file()
                or logical_filename is None
                or "_" not in logical_filename
            ):
                continue
            relative_path = candidate.relative_to(source_root)
            if _is_rsync_partial_path(relative_path):
                continue
            object_name = logical_filename.split("_", 1)[0]
            relative = relative_path.as_posix()
            logical_relative = (
                relative
                if source_index == 0
                else f"additional-{source_index:03d}/{relative}"
            )
            sources_by_object.setdefault(object_name, []).append(
                ActiveAsteroidsImageSource(
                    path=candidate,
                    source_root=source_root,
                    source_index=source_index,
                    relative_path=logical_relative,
                    logical_filename=logical_filename,
                )
            )
    return sources_by_object


def read_active_asteroids_object_list(
    path: str | Path,
    *,
    root: Path,
    additional_roots: tuple[Path, ...] = (),
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> ActiveAsteroidsSelection:
    """Resolve an ordered object CSV against primary and additional AA PNG roots."""

    if batch_size <= 0:
        raise ValueError("batch size must be greater than zero")
    csv_path = Path(path).expanduser()
    root = Path(root).expanduser().resolve()
    additional_roots = tuple(
        Path(candidate).expanduser().resolve() for candidate in additional_roots
    )
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or ())
        if not fieldnames & {"object_id", "object_name", "object"}:
            raise ValueError(
                "Active Asteroids object CSV must contain object_id, object_name, or object."
            )
        if not fieldnames & {"object_class", "class_name"}:
            raise ValueError(
                "Active Asteroids object CSV must contain object_class or class_name."
            )
        rows = list(reader)

    pngs_by_object = discover_active_asteroids_pngs(root, additional_roots)

    requested_classes: dict[str, str] = {}
    queue_objects: list[ReviewQueueObject] = []
    selected_paths: dict[str, str] = {}
    selected_sources: dict[str, ActiveAsteroidsImageSource] = {}
    selected_order: dict[str, tuple[int, str, str, str]] = {}
    missing_objects = 0
    duplicate_rows = 0
    duplicate_images = 0
    invalid_images = 0
    malformed_rows = 0
    for row in rows:
        object_name = str(
            row.get("object_id") or row.get("object_name") or row.get("object") or ""
        ).strip()
        class_name = str(row.get("object_class") or row.get("class_name") or "").strip()
        if not object_name or not class_name:
            malformed_rows += 1
            continue
        previous_class = requested_classes.get(object_name)
        if previous_class is not None:
            if previous_class != class_name:
                raise ValueError(
                    "Active Asteroids object list assigns conflicting classes to "
                    f"{object_name}: {previous_class!r} and {class_name!r}."
                )
            duplicate_rows += 1
            continue
        requested_classes[object_name] = class_name
        candidates = []
        for candidate in pngs_by_object.get(object_name, []):
            try:
                read_png_size(candidate.path)
            except (OSError, ValueError):
                invalid_images += 1
                continue
            candidates.append(candidate)
        if not candidates:
            missing_objects += 1
            continue
        queue_rank = len(queue_objects) + 1
        queue_object = ReviewQueueObject(
            product=ACTIVE_ASTEROIDS_PRODUCT,
            class_name=class_name,
            object_name=object_name,
            queue_rank=queue_rank,
            batch_number=((queue_rank - 1) // batch_size) + 1,
        )
        queue_objects.append(queue_object)
        for candidate in sorted(
            candidates,
            key=lambda item: (
                str(
                    active_asteroids_metadata(item.logical_filename).get("timestamp")
                    or ""
                ),
                str(
                    active_asteroids_metadata(item.logical_filename).get("visit") or ""
                ),
                item.source_index,
                item.relative_path,
            ),
        ):
            metadata = active_asteroids_metadata(candidate.logical_filename)
            image_id = image_id_for(
                ACTIVE_ASTEROIDS_PRODUCT,
                class_name,
                object_name,
                metadata["identity"],
            )
            if image_id in selected_paths:
                duplicate_images += 1
                continue
            selected_paths[image_id] = candidate.relative_path
            selected_sources[image_id] = candidate
            selected_order[image_id] = (
                queue_rank,
                str(metadata.get("timestamp") or ""),
                str(metadata.get("visit") or ""),
                candidate.relative_path,
            )

    if not queue_objects:
        raise ValueError(
            f"No objects from {csv_path} matched PNGs under the configured roots."
        )
    ordered_ids = tuple(
        image_id
        for image_id, _ in sorted(
            selected_order.items(),
            key=lambda item: item[1],
        )
    )
    return ActiveAsteroidsSelection(
        relative_paths=tuple(selected_paths[image_id] for image_id in ordered_ids),
        image_ids=ordered_ids,
        object_scope=tuple(
            (queue.class_name, queue.object_name) for queue in queue_objects
        ),
        queue_objects=tuple(queue_objects),
        summary={
            "requested_objects": len(rows),
            "selected_objects": len(queue_objects),
            "missing_objects": missing_objects,
            "duplicate_rows": duplicate_rows,
            "duplicate_images": duplicate_images,
            "invalid_images": invalid_images,
            "malformed_rows": malformed_rows,
            "selected_images": len(selected_paths),
            "primary_images": sum(
                selected_sources[image_id].source_index == 0 for image_id in ordered_ids
            ),
            "additional_images": sum(
                selected_sources[image_id].source_index > 0 for image_id in ordered_ids
            ),
            "additional_roots": len(additional_roots),
            "missing_additional_roots": sum(
                not candidate.is_dir() for candidate in additional_roots
            ),
            "batches": queue_objects[-1].batch_number,
        },
    )


def discover_object_dirs(
    root: Path, product: str = "preliminary_visit_image"
) -> set[tuple[str, str]]:
    """Return object directories found at root/product/class/object."""

    product_root = Path(root).expanduser() / product
    if not product_root.is_dir():
        return set()
    found: set[tuple[str, str]] = set()
    for class_root in product_root.iterdir():
        if not class_root.is_dir():
            continue
        for object_root in class_root.iterdir():
            if object_root.is_dir():
                found.add(object_scope_key(class_root.name, object_root.name))
    return found


def _object_scope_from_path(
    value: str,
    products: tuple[str, ...],
) -> tuple[str, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    parts = Path(text).parts
    for product in products:
        try:
            product_index = parts.index(product)
        except ValueError:
            continue
        if len(parts) > product_index + 2:
            return object_scope_key(parts[product_index + 1], parts[product_index + 2])
    return None


def read_object_list_scope(
    path: str | Path,
    *,
    root: Path,
    product: str = "preliminary_visit_image",
    products: tuple[str, ...] | None = None,
) -> tuple[tuple[str, str], ...]:
    """Read object rows and keep matching local class/object directories.

    Rows with a class select that exact class/object pair. Rows with only an
    object name select every matching object directory across local classes.
    """

    selected_products = products or (product,)
    csv_path = Path(path).expanduser()
    requested: set[tuple[str, str]] = set()
    requested_object_names: set[str] = set()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            class_name = (
                row.get("object_class") or row.get("class_name") or ""
            ).strip()
            object_name = (
                row.get("object_id")
                or row.get("object_name")
                or row.get("object")
                or ""
            ).strip()
            if object_name:
                object_names = {
                    object_name,
                    object_name.replace(" ", "_"),
                    object_name.replace("_", " "),
                }
                if class_name:
                    requested.update(
                        object_scope_key(class_name, candidate)
                        for candidate in object_names
                    )
                else:
                    requested_object_names.update(object_names)
            for column in ("relative_path", "input_path", "path"):
                key = _object_scope_from_path(row.get(column, ""), selected_products)
                if key is not None:
                    requested.add(key)
                    break

    local_objects = set().union(
        *(
            discover_object_dirs(root, selected_product)
            for selected_product in selected_products
        )
    )
    scoped = requested & local_objects
    scoped.update(key for key in local_objects if key[1] in requested_object_names)
    return tuple(sorted(scoped))


def _image_location_from_value(
    value: str,
    *,
    products: tuple[str, ...],
) -> tuple[str, str, str, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    parts = Path(text).parts
    allowed_products = set(products)
    for index, part in enumerate(parts):
        if part not in allowed_products or len(parts) <= index + 3:
            continue
        return part, parts[index + 1], parts[index + 2], parts[index + 3]
    return None


def _image_location_from_row(
    row: dict[str, str],
    *,
    products: tuple[str, ...],
) -> tuple[str, str, str, str] | None:
    for column in IMAGE_PATH_COLUMNS:
        location = _image_location_from_value(row.get(column, ""), products=products)
        if location is not None:
            return location

    product = str(row.get("rubin_product") or row.get("product") or "").strip()
    class_name = str(row.get("object_class") or row.get("class_name") or "").strip()
    object_name = str(
        row.get("object_id") or row.get("object_name") or row.get("object") or ""
    ).strip()
    filename = str(row.get("filename") or "").strip()
    stable_image_id = str(row.get("rubin_stable_review_image_id") or "").strip()
    if (
        product in set(products)
        and class_name
        and object_name
        and (filename or stable_image_id)
    ):
        return product, class_name, object_name, filename
    return None


def csv_has_reveal_scores(path: str | Path) -> bool:
    """Return whether a CSV header contains both supported reveal scores."""

    csv_path = Path(path).expanduser()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        fieldnames = set(csv.DictReader(handle).fieldnames or ())
    return set(REVEAL_SCORE_COLUMNS).issubset(fieldnames)


def resolve_reveal_list_path(
    explicit_path: str | Path | None,
    object_list_path: str | Path | None,
    *,
    layout: str,
) -> tuple[Path | None, bool]:
    """Resolve explicit or auto-detected reveal input and its source mode."""

    explicit = Path(explicit_path).expanduser() if explicit_path is not None else None
    object_list = (
        Path(object_list_path).expanduser() if object_list_path is not None else None
    )
    if layout == "active-asteroids":
        if explicit is not None:
            raise SystemExit(
                "--layout active-asteroids does not support --reveal-list."
            )
        return None, False
    if explicit is not None:
        return explicit, False
    if object_list is not None and csv_has_reveal_scores(object_list):
        return object_list, True
    return None, False


def _reveal_probability(row: dict[str, str], column: str, row_number: int) -> float:
    value = str(row.get(column) or "").strip()
    try:
        score = float(value)
    except ValueError as exc:
        raise ValueError(
            f"Reveal row {row_number} has invalid {column}: {value!r}."
        ) from exc
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        raise ValueError(
            f"Reveal row {row_number} {column} must be a finite value from 0 to 1."
        )
    return score


def read_reveal_scores(
    path: str | Path,
    *,
    products: tuple[str, ...] = DEFAULT_PRODUCTS,
) -> RevealSelection:
    """Read transient image-level model scores for on-demand reveal mode."""

    csv_path = Path(path).expanduser()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or ())
        missing_columns = set(REVEAL_SCORE_COLUMNS) - fieldnames
        if missing_columns:
            raise ValueError(
                "Reveal CSV is missing required score column(s): "
                f"{', '.join(sorted(missing_columns))}."
            )
        rows = list(reader)

    by_image_id: dict[str, RevealScore] = {}
    duplicate_rows = 0
    unscored_rows = 0
    for row_number, row in enumerate(rows, start=2):
        r3_value = str(row.get("model_score_r3") or "").strip()
        operational_value = str(row.get("model_score_operational") or "").strip()
        if not r3_value and not operational_value:
            unscored_rows += 1
            continue
        if not r3_value or not operational_value:
            raise ValueError(
                f"Reveal row {row_number} must provide both model scores or leave both blank."
            )

        image_id = str(row.get("rubin_stable_review_image_id") or "").strip()
        if not image_id:
            location = _image_location_from_row(row, products=products)
            if location is None:
                raise ValueError(
                    f"Reveal row {row_number} has no usable stable image ID or image path."
                )
            product, class_name, object_name, filename = location
            if not filename:
                raise ValueError(
                    f"Reveal row {row_number} has no filename for stable-ID fallback."
                )
            metadata = parse_filename_metadata(filename)
            image_id = image_id_for(
                product,
                class_name,
                object_name,
                metadata["identity"],
            )

        reveal_score = RevealScore(
            image_id=image_id,
            model_score_r3=_reveal_probability(row, "model_score_r3", row_number),
            model_score_operational=_reveal_probability(
                row,
                "model_score_operational",
                row_number,
            ),
        )
        existing = by_image_id.get(image_id)
        if existing is not None:
            if existing != reveal_score:
                raise ValueError(
                    f"Reveal row {row_number} conflicts with an earlier row for image {image_id}."
                )
            duplicate_rows += 1
            continue
        by_image_id[image_id] = reveal_score

    if not by_image_id:
        raise ValueError(f"Reveal CSV {csv_path} contains no image scores.")
    return RevealSelection(
        scores=tuple(by_image_id[key] for key in sorted(by_image_id)),
        summary={
            "requested_rows": len(rows),
            "unique_scores": len(by_image_id),
            "duplicate_rows": duplicate_rows,
            "unscored_rows": unscored_rows,
        },
    )


def _image_order_key(path: Path) -> tuple[bool, str, str, str, str]:
    metadata = parse_filename_metadata(path.name)
    return (
        metadata["timestamp"] is None,
        str(metadata["timestamp"] or ""),
        str(metadata["visit"] or ""),
        str(metadata["detector"] or ""),
        path.name,
    )


def read_image_list_scope(
    path: str | Path,
    *,
    root: Path,
    products: tuple[str, ...] = DEFAULT_PRODUCTS,
    classes: tuple[str, ...] | None = None,
    context: int = DEFAULT_IMAGE_CONTEXT,
    preferred_image_ids: set[str] | None = None,
) -> ImageListSelection:
    """Resolve image rows and expand context over unique RCC products."""

    if context < 0:
        raise ValueError("image context must be zero or greater")

    csv_path = Path(path).expanduser()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or ())
        supported = set(IMAGE_PATH_COLUMNS) | {
            "filename",
            "rubin_stable_review_image_id",
        }
        if not fieldnames & supported:
            raise ValueError(
                "Image-list CSV must contain a path column "
                f"({', '.join(IMAGE_PATH_COLUMNS)}), filename, or "
                "rubin_stable_review_image_id."
            )
        rows = list(reader)

    allowed_classes = set(classes or ())
    preferred_ids = set(preferred_image_ids or ())
    candidates_by_object: dict[tuple[str, str, str], list[RCCImageCandidate]] = {}
    groups_by_object: dict[
        tuple[str, str, str],
        list[tuple[RCCImageCandidate, tuple[RCCImageCandidate, ...]]],
    ] = {}
    target_indices: dict[tuple[str, str, str], set[int]] = {}
    exact_matches = 0
    stable_matches = 0
    unresolved_rows = 0
    resolved_product_ids: set[str] = set()

    for row in rows:
        location = _image_location_from_row(row, products=products)
        if location is None:
            unresolved_rows += 1
            continue
        product, class_name, object_name, filename = location
        if allowed_classes and class_name not in allowed_classes:
            unresolved_rows += 1
            continue

        object_key = (product, class_name, object_name)
        if object_key not in candidates_by_object:
            object_root = root / product / class_name / object_name
            candidates: list[RCCImageCandidate] = []
            if object_root.is_dir():
                for candidate in sorted(
                    object_root.glob("*.png"), key=_image_order_key
                ):
                    if not candidate.is_file():
                        continue
                    metadata = parse_filename_metadata(candidate.name)
                    candidate_image_id = image_id_for(
                        product,
                        class_name,
                        object_name,
                        metadata["identity"],
                    )
                    candidates.append(
                        RCCImageCandidate(
                            path=candidate,
                            image_id=candidate_image_id,
                            product_image_id=product_image_id_for(
                                product,
                                class_name,
                                object_name,
                                candidate.name,
                                fallback_image_id=candidate_image_id,
                            ),
                            timestamp=metadata["timestamp"],
                            annotation=str(metadata["annotation"] or ""),
                        )
                    )
            candidates_by_object[object_key] = candidates

            members_by_product: dict[str, list[RCCImageCandidate]] = {}
            for candidate in candidates:
                members_by_product.setdefault(candidate.product_image_id, []).append(
                    candidate
                )
            product_groups = []
            for members in members_by_product.values():
                representative = min(
                    members,
                    key=lambda item: _candidate_preference_key(item, preferred_ids),
                )
                product_groups.append((representative, tuple(members)))
            groups_by_object[object_key] = sorted(
                product_groups,
                key=lambda item: _image_order_key(item[0].path),
            )

        candidates = candidates_by_object[object_key]
        product_groups = groups_by_object[object_key]
        group_index_by_product = {
            representative.product_image_id: index
            for index, (representative, _members) in enumerate(product_groups)
        }
        product_by_filename = {
            candidate.path.name: candidate.product_image_id for candidate in candidates
        }
        products_by_image_id: dict[str, set[str]] = {}
        for candidate in candidates:
            products_by_image_id.setdefault(candidate.image_id, set()).add(
                candidate.product_image_id
            )

        matched_product_id = product_by_filename.get(filename)
        matched_kind = "exact"
        if matched_product_id is None:
            stable_id_value = str(row.get("rubin_stable_review_image_id") or "").strip()
            if not stable_id_value:
                metadata = parse_filename_metadata(filename)
                stable_id_value = image_id_for(
                    product,
                    class_name,
                    object_name,
                    metadata["identity"],
                )
            stable_products = products_by_image_id.get(stable_id_value, set())
            if len(stable_products) == 1:
                matched_product_id = next(iter(stable_products))
                matched_kind = "stable"

        if matched_product_id is None:
            unresolved_rows += 1
            continue
        if matched_product_id not in resolved_product_ids:
            if matched_kind == "exact":
                exact_matches += 1
            else:
                stable_matches += 1
            resolved_product_ids.add(matched_product_id)
        target_indices.setdefault(object_key, set()).add(
            group_index_by_product[matched_product_id]
        )

    if not resolved_product_ids:
        raise ValueError(f"No images from {csv_path} matched local PNGs under {root}.")

    selected_paths: dict[str, str] = {}
    selected_product_ids: set[str] = set()
    object_scope: set[tuple[str, str]] = set()
    for object_key, indices in target_indices.items():
        _product, class_name, object_name = object_key
        product_groups = groups_by_object[object_key]
        object_scope.add((class_name, object_name))
        for index in indices:
            start = max(0, index - context)
            stop = min(len(product_groups), index + context + 1)
            for representative, members in product_groups[start:stop]:
                selected_product_ids.add(representative.product_image_id)
                for candidate in members:
                    relative_path = candidate.path.relative_to(root).as_posix()
                    selected_paths[candidate.image_id] = relative_path

    ordered_ids = tuple(
        image_id
        for image_id, _ in sorted(
            selected_paths.items(),
            key=lambda item: item[1],
        )
    )
    selection_summary = {
        "requested_rows": len(rows),
        "unique_targets": len(resolved_product_ids),
        "exact_matches": exact_matches,
        "stable_matches": stable_matches,
        "unresolved_rows": unresolved_rows,
        "selected_images": len(selected_product_ids),
        "selected_objects": len(target_indices),
    }
    if len(selected_paths) != len(selected_product_ids):
        selection_summary["selected_source_images"] = len(selected_paths)

    return ImageListSelection(
        relative_paths=tuple(selected_paths[image_id] for image_id in ordered_ids),
        image_ids=ordered_ids,
        object_scope=tuple(sorted(object_scope)),
        summary=selection_summary,
    )


def default_index_db_path(root: Path) -> Path:
    """Return the local rebuildable thumbnail-index cache path for a root."""

    root_key = stable_id(str(Path(root).expanduser().absolute()))[:16]
    return DEFAULT_INDEX_CACHE_ROOT / root_key / "thumbnail_index.sqlite"


def default_orbital_cache_path(root: Path) -> Path:
    """Return the expected orbital-elements cache path for an RCC thumbnail root."""

    root = Path(root).expanduser()
    root_candidate = root / ORBITAL_CACHE_RELATIVE_PATH
    if root_candidate.exists():
        return root_candidate
    data_root = (
        os.environ.get("RCC_DATA_ROOT")
        or os.environ.get("RCC_DATA_DIR")
        or os.environ.get("RUBIN_COMET_CATCHERS_DATA_DIR")
        or "~/rubin-user/rcc/data"
    )
    return Path(data_root).expanduser() / "object_info/orbits/orbital_elements.parquet"


def normalize_lookup_name(name: object) -> str:
    """Normalize object names using the RCC orbital-cache convention."""

    if name is None:
        return ""
    try:
        if isinstance(name, float) and math.isnan(name):
            return ""
    except TypeError:
        return ""

    text = str(name).strip()
    if not text:
        return ""
    text = text.replace("_", " ")
    text = text.replace("/", " ")
    text = text.replace("−", "-")
    text = re.sub(r"\s+", " ", text)
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    text = re.sub(r"^\((\d+)\)\s*", r"\1 ", text)
    text = re.sub(r"\b(\d{4})\s*([A-Z]{1,2}\d+[A-Z]?)\b", r"\1 \2", text, flags=re.I)
    return text.upper().strip()


def object_lookup_aliases(record: dict[str, Any]) -> set[str]:
    aliases = {
        str(record.get("object_name") or ""),
        str(record.get("object_from_filename") or ""),
    }
    expanded: set[str] = set()
    for alias in aliases:
        text = alias.strip()
        if not text:
            continue
        expanded.add(text)
        expanded.add(text.replace("_", " "))
        expanded.add(text.replace("-", " "))
        expanded.add(text.replace("/", " "))
    return {
        normalize_lookup_name(alias)
        for alias in expanded
        if normalize_lookup_name(alias)
    }


def _float_or_none(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def load_tisserand_lookup(
    cache_path: str | Path | None,
    lookup_keys: set[str],
) -> dict[str, str]:
    """Load Tisserand-Jupiter annotation tokens for requested object keys."""

    if not cache_path or not lookup_keys:
        return {}
    path = Path(cache_path).expanduser()
    if not path.exists():
        return {}
    try:
        import pandas as pd
    except ImportError:
        return {}

    try:
        cache = pd.read_parquet(path, columns=["name", "quaero_name", "tJ"])
    except Exception:
        return {}

    lookup: dict[str, str] = {}
    for row in cache.itertuples(index=False):
        value = _float_or_none(getattr(row, "tJ", None))
        token = encode_tisserand_jupiter(value)
        if token is None:
            continue
        for alias in (getattr(row, "name", None), getattr(row, "quaero_name", None)):
            key = normalize_lookup_name(alias)
            if key in lookup_keys and key not in lookup:
                lookup[key] = token
    return lookup


def add_tisserand_tokens_from_cache(
    records: list[dict[str, Any]],
    cache_path: str | Path | None,
) -> int:
    """Fill missing image tisserand tokens from the orbital-elements cache."""

    missing_records = [
        record for record in records if not record.get("tisserand_token")
    ]
    lookup_keys: set[str] = set()
    aliases_by_id: dict[str, set[str]] = {}
    for record in missing_records:
        aliases = object_lookup_aliases(record)
        aliases_by_id[record["image_id"]] = aliases
        lookup_keys.update(aliases)

    lookup = load_tisserand_lookup(cache_path, lookup_keys)
    if not lookup:
        return 0

    filled = 0
    for record in missing_records:
        for alias in aliases_by_id.get(record["image_id"], set()):
            token = lookup.get(alias)
            if token:
                record["tisserand_token"] = token
                filled += 1
                break
    return filled


def read_png_size(path: Path) -> tuple[int, int]:
    """Return PNG width and height using only the PNG header."""

    opener = gzip.open if path.name.lower().endswith(".png.gz") else Path.open
    with opener(path, "rb") as handle:
        header = handle.read(24)
    if (
        len(header) < 24
        or not header.startswith(PNG_SIGNATURE)
        or header[12:16] != b"IHDR"
    ):
        raise ValueError(f"{path} is not a valid PNG with an IHDR header")
    width, height = struct.unpack(">II", header[16:24])
    return int(width), int(height)


def _base_identity(filename: str) -> str:
    identity = get_thumbnail_identity(filename)
    for suffix in ("_warp", "_withVar"):
        if identity.endswith(suffix):
            return identity[: -len(suffix)]
    return identity


def parse_filename_metadata(filename: str) -> dict[str, Any]:
    """Parse RCC PNG metadata from the filename without requiring sidecars."""

    stem = Path(filename).stem
    annotation = ""
    if THUMBNAIL_ANNOTATION_SEPARATOR in stem:
        annotation = stem.split(THUMBNAIL_ANNOTATION_SEPARATOR, 1)[1]
    identity = _base_identity(filename)
    tokens = identity.split("_")

    timestamp_index = next(
        (
            index
            for index, token in enumerate(tokens)
            if IDENTITY_TIMESTAMP_RE.match(token)
        ),
        None,
    )
    parsed: dict[str, Any] = {
        "identity": identity,
        "pair_key": identity,
        "object_from_filename": None,
        "timestamp": None,
        "instrument": None,
        "visit": None,
        "detector": None,
        "annotation": annotation,
        "filter_token": None,
        "delta_mag_token": None,
        "q_token": None,
        "tisserand_token": None,
    }
    edp2_match = EDP2_DEEP_COADD_RE.match(identity)
    if edp2_match is not None:
        parsed.update(
            {
                "object_from_filename": edp2_match.group("object"),
                "instrument": "LSSTCam",
                "visit": edp2_match.group("visit"),
                "detector": edp2_match.group("detector"),
                "filter_token": edp2_match.group("filter"),
                "annotation": f"deep_coadd_n{edp2_match.group('coadd_visits')}",
            }
        )
    if timestamp_index is not None:
        parsed["object_from_filename"] = "_".join(tokens[:timestamp_index]) or None
        parsed["timestamp"] = tokens[timestamp_index]
        if len(tokens) > timestamp_index + 1:
            parsed["instrument"] = tokens[timestamp_index + 1]
        if len(tokens) > timestamp_index + 2:
            parsed["visit"] = tokens[timestamp_index + 2]
        if len(tokens) > timestamp_index + 3:
            parsed["detector"] = "_".join(tokens[timestamp_index + 3 :]) or None

    annotation_tokens = annotation.split("_") if annotation else []
    for index, token in enumerate(annotation_tokens):
        if token == "dm" and index + 1 < len(annotation_tokens):
            parsed["delta_mag_token"] = f"dm_{annotation_tokens[index + 1]}"
        elif token.startswith("dm_"):
            parsed["delta_mag_token"] = token
        elif token.startswith("q"):
            parsed["q_token"] = token
        elif token == "tj" and index + 1 < len(annotation_tokens):
            parsed["tisserand_token"] = f"tj_{annotation_tokens[index + 1]}"
        elif token.startswith("tj_"):
            parsed["tisserand_token"] = token

    for token in annotation_tokens:
        if (
            token
            and not token.startswith(("dm", "q", "tj"))
            and token not in {"p00", "m00"}
        ):
            parsed["filter_token"] = token
            break
    return parsed


def review_text_is_blank(value: Any) -> int:
    """Return one when a saved review field contains no non-whitespace text."""

    return int(not str(value or "").strip())


def connect_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.create_function(
        "review_text_is_blank",
        1,
        review_text_is_blank,
        deterministic=True,
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def initialize_index_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS images (
            image_id TEXT PRIMARY KEY,
            product_image_id TEXT,
            object_id TEXT NOT NULL,
            relative_path TEXT NOT NULL UNIQUE,
            source_path TEXT,
            product TEXT NOT NULL,
            class_name TEXT NOT NULL,
            object_name TEXT NOT NULL,
            filename TEXT NOT NULL,
            identity TEXT NOT NULL,
            pair_key TEXT NOT NULL,
            timestamp TEXT,
            instrument TEXT,
            visit TEXT,
            detector TEXT,
            annotation TEXT,
            filter_token TEXT,
            delta_mag_token TEXT,
            q_token TEXT,
            tisserand_token TEXT,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            file_size INTEGER NOT NULL,
            mtime REAL NOT NULL,
            discovered_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            stale INTEGER NOT NULL DEFAULT 0,
            missing INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS objects (
            object_id TEXT PRIMARY KEY,
            product TEXT NOT NULL,
            class_name TEXT NOT NULL,
            object_name TEXT NOT NULL,
            image_count INTEGER NOT NULL DEFAULT 0,
            missing_count INTEGER NOT NULL DEFAULT 0,
            stale_count INTEGER NOT NULL DEFAULT 0,
            scored_count INTEGER NOT NULL DEFAULT 0,
            active_flag INTEGER,
            reviewed_at TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS review_queue_objects (
            object_id TEXT PRIMARY KEY,
            queue_rank INTEGER NOT NULL UNIQUE,
            batch_number INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_images_object ON images(object_id, timestamp, visit);
        CREATE INDEX IF NOT EXISTS idx_images_product_class
            ON images(product, class_name, object_name);
        CREATE INDEX IF NOT EXISTS idx_review_queue_batch
            ON review_queue_objects(batch_number, queue_rank);
        """
    )
    image_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(images)").fetchall()
    }
    if "source_path" not in image_columns:
        conn.execute("ALTER TABLE images ADD COLUMN source_path TEXT")
    if "product_image_id" not in image_columns:
        conn.execute("ALTER TABLE images ADD COLUMN product_image_id TEXT")
    if "order_key" not in image_columns:
        conn.execute("ALTER TABLE images ADD COLUMN order_key REAL")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_images_product_image "
        "ON images(product_image_id) "
        "WHERE product_image_id IS NOT NULL"
    )
    conn.commit()


def initialize_label_db(conn: sqlite3.Connection, schema: str = "main") -> None:
    prefix = "" if schema == "main" else f"{schema}."
    index_prefix = "" if schema == "main" else f"{schema}."
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS {prefix}image_scores (
            image_id TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            score INTEGER,
            status TEXT NOT NULL DEFAULT 'scored',
            tags TEXT NOT NULL DEFAULT '',
            comment TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL,
            scored_at TEXT NOT NULL,
            PRIMARY KEY (image_id, reviewer)
        );

        CREATE TABLE IF NOT EXISTS {prefix}object_reviews (
            object_id TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            active_flag INTEGER,
            status TEXT NOT NULL DEFAULT 'reviewed',
            tags TEXT NOT NULL DEFAULT '',
            comment TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            PRIMARY KEY (object_id, reviewer)
        );

        CREATE TABLE IF NOT EXISTS {prefix}review_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            session_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            object_id TEXT,
            image_id TEXT,
            payload TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS {prefix}review_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS {index_prefix}idx_scores_reviewer
            ON image_scores(reviewer, scored_at);
        CREATE INDEX IF NOT EXISTS {index_prefix}idx_events_time
            ON review_events(event_time);
        """
    )
    conn.commit()


CLASSIFICATION_SNAPSHOT_COLUMNS = (
    "snapshot_version",
    "snapshot_exported_at",
    "record_type",
    "image_id",
    "object_id",
    "reviewer",
    "score",
    "active_flag",
    "status",
    "tags",
    "comment",
    "session_id",
    "recorded_at",
    "event_id",
    "event_type",
    "payload",
)


def classification_parquet_path(config: ReviewConfig) -> Path:
    """Return the portable classification snapshot path for a review config."""

    if config.classification_parquet_path is not None:
        return Path(config.classification_parquet_path).expanduser()
    return config.db_path.with_suffix(".parquet")


def _snapshot_source_db(db_path: Path) -> Path | None:
    """Return an existing local label DB, including the one-time legacy default."""

    path = Path(db_path).expanduser()
    if path.exists():
        return path
    return None


def write_classification_parquet(
    db_path: str | Path,
    parquet_path: str | Path,
) -> dict[str, Any]:
    """Write one atomic, portable snapshot of all durable review tables."""

    source = _snapshot_source_db(Path(db_path))
    target = Path(parquet_path).expanduser()
    if source is None:
        return {"written": False, "reason": "missing_db", "parquet": str(target)}

    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError(
            "pandas and pyarrow are required for classification Parquet export"
        ) from exc

    exported_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    uri = f"file:{source.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    records: list[dict[str, Any]] = []
    try:
        for row in conn.execute(
            """
            SELECT image_id, reviewer, score, status, tags, comment, session_id, scored_at
            FROM image_scores
            """
        ).fetchall():
            records.append(
                {
                    "record_type": "image_score",
                    "image_id": row["image_id"],
                    "reviewer": row["reviewer"],
                    "score": row["score"],
                    "status": row["status"],
                    "tags": row["tags"],
                    "comment": row["comment"],
                    "session_id": row["session_id"],
                    "recorded_at": row["scored_at"],
                }
            )
        for row in conn.execute(
            """
            SELECT object_id, reviewer, active_flag, status, tags, comment,
                   session_id, reviewed_at
            FROM object_reviews
            """
        ).fetchall():
            records.append(
                {
                    "record_type": "object_review",
                    "object_id": row["object_id"],
                    "reviewer": row["reviewer"],
                    "active_flag": row["active_flag"],
                    "status": row["status"],
                    "tags": row["tags"],
                    "comment": row["comment"],
                    "session_id": row["session_id"],
                    "recorded_at": row["reviewed_at"],
                }
            )
        for row in conn.execute(
            """
            SELECT event_id, event_time, reviewer, session_id, event_type,
                   object_id, image_id, payload
            FROM review_events
            ORDER BY event_id
            """
        ).fetchall():
            records.append(
                {
                    "record_type": "review_event",
                    "event_id": row["event_id"],
                    "recorded_at": row["event_time"],
                    "reviewer": row["reviewer"],
                    "session_id": row["session_id"],
                    "event_type": row["event_type"],
                    "object_id": row["object_id"],
                    "image_id": row["image_id"],
                    "payload": row["payload"],
                }
            )
    finally:
        conn.close()

    if not records:
        records.append({"record_type": "snapshot"})
    for record in records:
        record["snapshot_version"] = 1
        record["snapshot_exported_at"] = exported_at

    frame = pd.DataFrame(records, columns=CLASSIFICATION_SNAPSHOT_COLUMNS)
    for column in ("snapshot_version", "score", "active_flag", "event_id"):
        frame[column] = pd.array(frame[column], dtype="Int64")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {
        "written": True,
        "source_db": str(source),
        "parquet": str(target),
        "records": len(records),
        "snapshot_exported_at": exported_at,
    }


def _parquet_value(value: Any) -> Any:
    try:
        import pandas as pd

        if pd.isna(value):
            return None
    except (ImportError, TypeError, ValueError):
        pass
    return value


def latest_label_state_time(conn: sqlite3.Connection) -> str:
    """Return the newest local mutation/import time represented by the label DB."""

    values = [
        conn.execute("SELECT MAX(scored_at) FROM image_scores").fetchone()[0],
        conn.execute("SELECT MAX(reviewed_at) FROM object_reviews").fetchone()[0],
        conn.execute("SELECT MAX(event_time) FROM review_events").fetchone()[0],
    ]
    metadata = conn.execute(
        "SELECT value FROM review_metadata WHERE key = 'classification_snapshot_at'"
    ).fetchone()
    if metadata is not None:
        values.append(metadata[0])
    return max((str(value) for value in values if value), default="")


def import_classification_parquet(
    db_path: str | Path,
    parquet_path: str | Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Replace local label state from a newer portable Parquet snapshot."""

    target = Path(db_path).expanduser()
    source = Path(parquet_path).expanduser()
    if not source.exists():
        return {"imported": False, "reason": "missing_parquet", "parquet": str(source)}
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError(
            "pandas and pyarrow are required for classification Parquet import"
        ) from exc

    frame = pd.read_parquet(source)
    missing_columns = set(CLASSIFICATION_SNAPSHOT_COLUMNS) - set(frame.columns)
    if missing_columns:
        raise ValueError(
            f"Unsupported classification snapshot; missing columns: {sorted(missing_columns)}"
        )
    versions = {_parquet_value(value) for value in frame["snapshot_version"].unique()}
    if versions != {1}:
        raise ValueError(
            f"Unsupported classification snapshot version(s): {versions!r}"
        )
    exported_values = [
        str(value) for value in frame["snapshot_exported_at"].dropna().unique().tolist()
    ]
    if len(exported_values) != 1:
        raise ValueError(
            "Classification snapshot must contain exactly one export timestamp"
        )
    exported_at = exported_values[0]

    with closing(connect_db(target)) as conn:
        initialize_label_db(conn)
        local_state_at = latest_label_state_time(conn)
        if not force and local_state_at and exported_at <= local_state_at:
            return {
                "imported": False,
                "reason": "local_state_is_newer",
                "parquet": str(source),
                "snapshot_exported_at": exported_at,
                "local_state_at": local_state_at,
            }

        rows = [
            {key: _parquet_value(value) for key, value in row.items()}
            for row in frame.to_dict(orient="records")
        ]
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM image_scores")
        conn.execute("DELETE FROM object_reviews")
        conn.execute("DELETE FROM review_events")
        for row in rows:
            if row["record_type"] == "image_score":
                conn.execute(
                    """
                    INSERT INTO image_scores
                        (image_id, reviewer, score, status, tags, comment, session_id, scored_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["image_id"],
                        row["reviewer"],
                        row["score"],
                        row["status"],
                        row["tags"],
                        row["comment"],
                        row["session_id"],
                        row["recorded_at"],
                    ),
                )
            elif row["record_type"] == "object_review":
                conn.execute(
                    """
                    INSERT INTO object_reviews
                        (object_id, reviewer, active_flag, status, tags, comment,
                         session_id, reviewed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["object_id"],
                        row["reviewer"],
                        row["active_flag"],
                        row["status"],
                        row["tags"],
                        row["comment"],
                        row["session_id"],
                        row["recorded_at"],
                    ),
                )
            elif row["record_type"] == "review_event":
                conn.execute(
                    """
                    INSERT INTO review_events
                        (event_id, event_time, reviewer, session_id, event_type,
                         object_id, image_id, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["event_id"],
                        row["recorded_at"],
                        row["reviewer"],
                        row["session_id"],
                        row["event_type"],
                        row["object_id"],
                        row["image_id"],
                        row["payload"],
                    ),
                )
        conn.execute(
            """
            INSERT OR REPLACE INTO review_metadata(key, value)
            VALUES ('classification_snapshot_at', ?)
            """,
            (exported_at,),
        )
        conn.commit()
    return {
        "imported": True,
        "parquet": str(source),
        "db": str(target),
        "records": len(rows),
        "snapshot_exported_at": exported_at,
    }


def prepare_classification_state(config: ReviewConfig) -> dict[str, Any]:
    """Import only when the caller explicitly approves snapshot replacement."""
    if not config.import_snapshot:
        return {"imported": False, "reason": "explicit_import_required"}
    return import_classification_parquet(config.db_path, classification_parquet_path(config))


def reconcile_classification_parquet(
    db_path: str | Path,
    parquet_path: str | Path,
    incoming_paths: tuple[str | Path, ...] = (),
) -> dict[str, Any]:
    """Merge newer portable snapshots into the local DB, then rewrite one canonical file."""

    target_db = Path(db_path).expanduser()
    canonical = Path(parquet_path).expanduser()
    imported: list[dict[str, Any]] = []
    for candidate in (canonical, *(Path(path).expanduser() for path in incoming_paths)):
        if candidate.exists():
            imported.append(import_classification_parquet(target_db, candidate))
    written = write_classification_parquet(target_db, canonical)
    return {**written, "imports": imported}


def initialize_db(conn: sqlite3.Connection) -> None:
    """Initialize the legacy monolithic schema used by older DBs and fixtures."""

    initialize_index_db(conn)
    initialize_label_db(conn)


def sqlite_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def attach_label_db(conn: sqlite3.Connection, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn.execute(f"ATTACH DATABASE {sqlite_quote(str(db_path))} AS {LABEL_SCHEMA}")
    initialize_label_db(conn, LABEL_SCHEMA)


def install_object_scope(
    conn: sqlite3.Connection,
    object_scope: tuple[tuple[str, str], ...] | None,
) -> None:
    conn.executescript(
        """
        CREATE TEMP TABLE IF NOT EXISTS review_object_scope (
            class_name TEXT NOT NULL,
            object_name TEXT NOT NULL,
            PRIMARY KEY (class_name, object_name)
        );
        CREATE TEMP TABLE IF NOT EXISTS review_object_scope_state (
            active INTEGER NOT NULL
        );
        DELETE FROM review_object_scope;
        DELETE FROM review_object_scope_state;
        """
    )
    conn.execute(
        "INSERT INTO review_object_scope_state(active) VALUES (?)",
        (1 if object_scope is not None else 0,),
    )
    if object_scope:
        conn.executemany(
            "INSERT OR IGNORE INTO review_object_scope(class_name, object_name) VALUES (?, ?)",
            object_scope,
        )


def install_image_scope(
    conn: sqlite3.Connection,
    image_scope: tuple[str, ...] | None,
    *,
    reviewer: str,
    deduplicate: bool,
) -> None:
    conn.executescript(
        """
        CREATE TEMP TABLE IF NOT EXISTS review_image_scope (
            image_id TEXT PRIMARY KEY
        );
        CREATE TEMP TABLE IF NOT EXISTS review_selection_state (
            scope_active INTEGER NOT NULL,
            deduplicate INTEGER NOT NULL,
            reviewer TEXT NOT NULL
        );
        DELETE FROM review_image_scope;
        DELETE FROM review_selection_state;
        DROP VIEW IF EXISTS review_images;
        DROP VIEW IF EXISTS review_eligible_images;
        """
    )
    conn.execute(
        """
        INSERT INTO review_selection_state(scope_active, deduplicate, reviewer)
        VALUES (?, ?, ?)
        """,
        (1 if image_scope is not None else 0, 1 if deduplicate else 0, reviewer),
    )
    if image_scope:
        conn.executemany(
            "INSERT OR IGNORE INTO review_image_scope(image_id) VALUES (?)",
            ((image_id,) for image_id in image_scope),
        )
    conn.execute(
        """
        CREATE TEMP VIEW review_eligible_images AS
        SELECT i.*
        FROM main.images i
        WHERE i.missing = 0
          AND (
              (SELECT active FROM review_object_scope_state LIMIT 1) = 0
              OR EXISTS (
                  SELECT 1
                  FROM review_object_scope object_scope
                  WHERE object_scope.class_name = i.class_name
                    AND object_scope.object_name = i.object_name
              )
          )
          AND (
              (SELECT scope_active FROM review_selection_state LIMIT 1) = 0
              OR EXISTS (
                  SELECT 1
                  FROM review_image_scope scope
                  WHERE scope.image_id = i.image_id
              )
          )
        """
    )
    conn.execute(
        """
        CREATE TEMP VIEW review_images AS
        SELECT i.*
        FROM review_eligible_images i
        WHERE (SELECT deduplicate FROM review_selection_state LIMIT 1) = 0
           OR i.image_id = (
                SELECT candidate.image_id
                FROM review_eligible_images candidate
                WHERE (
                    candidate.product_image_id = i.product_image_id
                    OR (
                        i.product_image_id IS NULL
                        AND candidate.image_id = i.image_id
                    )
                )
                ORDER BY
                    CASE WHEN EXISTS (
                        SELECT 1
                        FROM review_reveal_scope reveal
                        WHERE reveal.image_id = candidate.image_id
                    ) THEN 0 ELSE 1 END,
                    CASE WHEN EXISTS (
                        SELECT 1
                        FROM labels.image_scores scored
                        WHERE scored.image_id = candidate.image_id
                          AND scored.reviewer = (
                              SELECT reviewer
                              FROM review_selection_state
                              LIMIT 1
                          )
                    ) THEN 0 ELSE 1 END,
                    CASE
                        WHEN candidate.timestamp IS NOT NULL
                         AND INSTR(candidate.timestamp, '.') > 0
                         AND TRIM(
                             SUBSTR(
                                 candidate.timestamp,
                                 INSTR(candidate.timestamp, '.') + 1
                             ),
                             '0'
                         ) <> ''
                        THEN 0 ELSE 1
                    END,
                    CASE
                        WHEN TRIM(COALESCE(candidate.annotation, '')) <> ''
                        THEN 0 ELSE 1
                    END,
                    candidate.relative_path
                LIMIT 1
           )
        """
    )


def install_reveal_scope(
    conn: sqlite3.Connection,
    reveal_scores: tuple[RevealScore, ...],
) -> None:
    """Install transient reveal image identities for this DB connection."""

    conn.executescript(
        """
        CREATE TEMP TABLE IF NOT EXISTS review_reveal_scope (
            image_id TEXT PRIMARY KEY
        );
        DELETE FROM review_reveal_scope;
        """
    )
    if reveal_scores:
        conn.executemany(
            "INSERT OR IGNORE INTO review_reveal_scope(image_id) VALUES (?)",
            ((score.image_id,) for score in reveal_scores),
        )


def table_exists(conn: sqlite3.Connection, name: str, schema: str = "main") -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def split_backup_path(db_path: Path) -> Path:
    return db_path.with_name(f"{db_path.stem}.pre_split_backup{db_path.suffix}")


def migrate_label_db_if_needed(db_path: Path) -> dict[str, Any]:
    """Move a legacy monolithic review DB to a compact label-only DB."""

    db_path = Path(db_path).expanduser()
    if not db_path.exists():
        return {"migrated": False, "reason": "missing"}

    with connect_db(db_path) as conn:
        has_index_tables = table_exists(conn, "images") or table_exists(conn, "objects")
        has_label_tables = any(
            table_exists(conn, name)
            for name in ("image_scores", "object_reviews", "review_events")
        )
        if not has_index_tables:
            initialize_label_db(conn)
            conn.commit()
            return {"migrated": False, "reason": "already_split"}
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    backup_path = split_backup_path(db_path)
    if not backup_path.exists():
        shutil.copy2(db_path, backup_path)

    temp_path = db_path.with_name(f"{db_path.stem}.label_only_tmp{db_path.suffix}")
    if temp_path.exists():
        temp_path.unlink()
    with connect_db(temp_path) as conn:
        initialize_label_db(conn)
        conn.execute(f"ATTACH DATABASE {sqlite_quote(str(backup_path))} AS source")
        if has_label_tables:
            for table_name in ("image_scores", "object_reviews", "review_events"):
                if table_exists(conn, table_name, "source"):
                    conn.execute(
                        f"INSERT OR IGNORE INTO {table_name} SELECT * FROM source.{table_name}"
                    )
        conn.execute(
            """
            INSERT OR REPLACE INTO review_metadata(key, value)
            VALUES
                ('split_from_backup', ?),
                ('split_migrated_at', ?)
            """,
            (str(backup_path), utc_now()),
        )
        conn.commit()
        conn.execute("DETACH DATABASE source")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    os.replace(temp_path, db_path)
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()
    return {"migrated": True, "backup_path": str(backup_path)}


@contextmanager
def connect_review_db(config: ReviewConfig):
    """Open the split review DB: main=index cache, attached labels=durable labels."""

    if config.db_path.resolve() == config.index_db_path.resolve():
        raise ValueError("Label and index databases must be separate files.")
    conn = connect_db(config.index_db_path)
    try:
        initialize_index_db(conn)
        conn.execute("CREATE TABLE IF NOT EXISTS index_metadata (key TEXT PRIMARY KEY, value TEXT)")
        binding = json.dumps([config.layout, config.dataset, str(config.root.resolve())])
        existing = conn.execute("SELECT value FROM index_metadata WHERE key='binding'").fetchone()
        if existing and existing[0] != binding:
            raise ValueError("Index belongs to a different dataset or root; select a fresh index.")
        conn.execute("INSERT OR IGNORE INTO index_metadata VALUES ('binding', ?)", (binding,))
        conn.commit()
        attach_label_db(conn, config.db_path)
        install_object_scope(conn, config.object_scope)
        install_reveal_scope(conn, config.reveal_scores)
        install_image_scope(
            conn,
            config.image_scope,
            reviewer=config.reviewer,
            deduplicate=config.layout == "rcc",
        )
        yield conn
    finally:
        conn.close()


def install_review_queue(
    conn: sqlite3.Connection,
    queue_objects: tuple[ReviewQueueObject, ...] | None,
) -> None:
    """Replace rebuildable queue ranks for the current launch scope."""

    conn.execute("DELETE FROM review_queue_objects")
    if queue_objects:
        conn.executemany(
            """
            INSERT INTO review_queue_objects(object_id, queue_rank, batch_number)
            VALUES (?, ?, ?)
            """,
            (
                (
                    object_id_for(
                        queue.product,
                        queue.class_name,
                        queue.object_name,
                    ),
                    queue.queue_rank,
                    queue.batch_number,
                )
                for queue in queue_objects
            ),
        )


def iter_active_asteroids_png_records(
    root: Path,
    queue_objects: tuple[ReviewQueueObject, ...],
    additional_roots: tuple[Path, ...] = (),
) -> list[dict[str, Any]]:
    """Build deduplicated review records from primary and additional AA roots."""

    sources_by_object = discover_active_asteroids_pngs(root, additional_roots)
    records: list[dict[str, Any]] = []
    seen_image_ids: set[str] = set()
    for queue in queue_objects:
        candidates = sorted(
            sources_by_object.get(queue.object_name, ()),
            key=lambda item: (
                str(
                    active_asteroids_metadata(item.logical_filename).get("timestamp")
                    or ""
                ),
                str(
                    active_asteroids_metadata(item.logical_filename).get("visit") or ""
                ),
                item.source_index,
                item.relative_path,
            ),
        )
        for candidate in candidates:
            path = candidate.path
            if not path.is_file():
                continue
            try:
                stat = path.stat()
                width, height = read_png_size(path)
            except (OSError, ValueError):
                continue
            metadata = active_asteroids_metadata(candidate.logical_filename)
            image_id = image_id_for(
                queue.product,
                queue.class_name,
                queue.object_name,
                metadata["identity"],
            )
            if image_id in seen_image_ids:
                continue
            seen_image_ids.add(image_id)
            records.append(
                {
                    "image_id": image_id,
                    "product_image_id": image_id,
                    "legacy_image_id": stable_id(candidate.relative_path),
                    "object_id": object_id_for(
                        queue.product,
                        queue.class_name,
                        queue.object_name,
                    ),
                    "relative_path": candidate.relative_path,
                    "source_path": str(path.resolve()),
                    "product": queue.product,
                    "class_name": queue.class_name,
                    "object_name": queue.object_name,
                    "filename": candidate.logical_filename,
                    "width": width,
                    "height": height,
                    "file_size": stat.st_size,
                    "mtime": stat.st_mtime,
                    **metadata,
                }
            )
    return records


def iter_png_records(
    root: Path,
    products: tuple[str, ...],
    classes: tuple[str, ...] | None = None,
    object_scope: tuple[tuple[str, str], ...] | None = None,
    image_paths: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    allowed_objects = set(object_scope or ())
    allowed_classes = set(classes or ())
    allowed_image_paths = set(image_paths or ())
    for product in products:
        product_root = root / product
        if not product_root.exists():
            continue
        if allowed_objects:
            object_roots = [
                product_root / class_name / object_name
                for class_name, object_name in sorted(allowed_objects)
                if not allowed_classes or class_name in allowed_classes
            ]
        else:
            class_roots = (
                [product_root / class_name for class_name in classes]
                if classes
                else None
            )
            roots = class_roots or sorted(
                path for path in product_root.iterdir() if path.is_dir()
            )
            object_roots = [
                object_root
                for class_root in roots
                if class_root.exists() and class_root.is_dir()
                for object_root in sorted(class_root.iterdir())
                if object_root.is_dir()
            ]
        for object_root in object_roots:
            if not object_root.exists() or not object_root.is_dir():
                continue
            for path in sorted(object_root.glob("*.png")):
                if not path.is_file():
                    continue
                try:
                    relative_path = path.relative_to(root).as_posix()
                    if (
                        image_paths is not None
                        and relative_path not in allowed_image_paths
                    ):
                        continue
                    rel_parts = Path(relative_path).parts
                    if len(rel_parts) != 4:
                        continue
                    _, class_name, object_name, filename = rel_parts
                    stat = path.stat()
                    width, height = read_png_size(path)
                except (OSError, ValueError):
                    continue
                meta = parse_filename_metadata(filename)
                image_id = image_id_for(
                    product, class_name, object_name, meta["identity"]
                )
                product_image_id = product_image_id_for(
                    product,
                    class_name,
                    object_name,
                    filename,
                    fallback_image_id=image_id,
                )
                oid = object_id_for(product, class_name, object_name)
                records.append(
                    {
                        "image_id": image_id,
                        "product_image_id": product_image_id,
                        "legacy_image_id": stable_id(relative_path),
                        "object_id": oid,
                        "relative_path": relative_path,
                        "product": product,
                        "class_name": class_name,
                        "object_name": object_name,
                        "filename": filename,
                        "width": width,
                        "height": height,
                        "file_size": stat.st_size,
                        "mtime": stat.st_mtime,
                        **meta,
                    }
                )
    return records


def mark_scan_scope_missing(
    conn: sqlite3.Connection,
    *,
    products: tuple[str, ...],
    classes: tuple[str, ...] | None,
    object_scope: tuple[tuple[str, str], ...] | None,
    image_scope: tuple[str, ...] | None,
    updated_at: str,
) -> None:
    if image_scope is not None:
        conn.executemany(
            "UPDATE images SET missing = 1, updated_at = ? WHERE image_id = ?",
            ((updated_at, image_id) for image_id in image_scope),
        )
        return
    if not products:
        return
    product_placeholders = ",".join("?" for _ in products)
    values: list[Any] = [updated_at, *products]
    where = [f"product IN ({product_placeholders})"]
    if classes:
        class_placeholders = ",".join("?" for _ in classes)
        where.append(f"class_name IN ({class_placeholders})")
        values.extend(classes)
    if object_scope:
        object_clauses = []
        for class_name, object_name in object_scope:
            object_clauses.append("(class_name = ? AND object_name = ?)")
            values.extend([class_name, object_name])
        where.append(f"({' OR '.join(object_clauses)})")
    conn.execute(
        f"UPDATE images SET missing = 1, updated_at = ? WHERE {' AND '.join(where)}",
        values,
    )


def migrate_legacy_image_identity(
    conn: sqlite3.Connection, record: dict[str, Any]
) -> str | None:
    """Collapse old full-path image rows into the stable thumbnail identity row."""

    rows = conn.execute(
        """
        SELECT image_id, discovered_at
        FROM images
        WHERE product = ?
            AND class_name = ?
            AND object_name = ?
            AND identity = ?
            AND image_id != ?
        """,
        (
            record["product"],
            record["class_name"],
            record["object_name"],
            record["identity"],
            record["image_id"],
        ),
    ).fetchall()
    relative_conflict = conn.execute(
        """
        SELECT image_id, discovered_at
        FROM images
        WHERE relative_path = ? AND image_id != ?
        """,
        (record["relative_path"], record["image_id"]),
    ).fetchone()
    if relative_conflict is not None and all(
        row["image_id"] != relative_conflict["image_id"] for row in rows
    ):
        rows.append(relative_conflict)

    discovered_values = [
        row["discovered_at"] for row in rows if row["discovered_at"] is not None
    ]
    for row in rows:
        old_image_id = row["image_id"]
        conn.execute(
            """
            INSERT OR IGNORE INTO image_scores (
                image_id, reviewer, score, status, tags, comment, session_id, scored_at
            )
            SELECT ?, reviewer, score, status, tags, comment, session_id, scored_at
            FROM image_scores
            WHERE image_id = ?
            """,
            (record["image_id"], old_image_id),
        )
        conn.execute("DELETE FROM image_scores WHERE image_id = ?", (old_image_id,))
        conn.execute("DELETE FROM images WHERE image_id = ?", (old_image_id,))
    return min(discovered_values) if discovered_values else None


def summarize_record_duplicates(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        product_image_id = str(record.get("product_image_id") or record["image_id"])
        counts[product_image_id] = counts.get(product_image_id, 0) + 1
    duplicate_counts = [count for count in counts.values() if count > 1]
    return {
        "review_products": len(counts),
        "duplicate_groups": len(duplicate_counts),
        "duplicates_suppressed": sum(count - 1 for count in duplicate_counts),
    }


def index_thumbnails(config: ReviewConfig) -> dict[str, int | float]:
    started = time.perf_counter()
    manifest_records = manifest.records(config) if config.layout == "manifest" else None
    with connect_review_db(config) as conn:
        now = utc_now()
        install_review_queue(conn, config.queue_objects)
        mark_scan_scope_missing(
            conn,
            products=config.products,
            classes=config.classes,
            object_scope=config.object_scope,
            image_scope=config.image_scope,
            updated_at=now,
        )
        records = (
            manifest_records
            if config.layout == "manifest" else
            iter_active_asteroids_png_records(
                config.root,
                config.queue_objects or (),
                config.additional_roots,
            )
            if config.layout == "active-asteroids"
            else iter_png_records(
                config.root,
                config.products,
                config.classes,
                config.object_scope,
                config.image_paths,
            )
        )
        duplicate_summary = summarize_record_duplicates(records)
        if config.layout in {"active-asteroids", "manifest"}:
            tisserand_filled = 0
        else:
            orbital_cache_path = (
                config.orbital_cache_path or default_orbital_cache_path(config.root)
            )
            tisserand_filled = add_tisserand_tokens_from_cache(
                records,
                orbital_cache_path,
            )
        inserted = updated = unchanged = changed = 0
        for record in records:
            record.setdefault("order_key", None)
            record.setdefault("source_path", None)
            record.setdefault("product_image_id", record["image_id"])
            legacy_discovered_at = (None if config.layout == "manifest"
                                    else migrate_legacy_image_identity(conn, record))
            existing = conn.execute(
                "SELECT file_size, mtime, stale FROM images WHERE image_id = ?",
                (record["image_id"],),
            ).fetchone()
            stale = 0
            if existing is None:
                inserted += 1
                discovered_at = legacy_discovered_at or now
            else:
                discovered_at = conn.execute(
                    "SELECT discovered_at FROM images WHERE image_id = ?",
                    (record["image_id"],),
                ).fetchone()["discovered_at"]
                if (
                    existing["file_size"] != record["file_size"]
                    or existing["mtime"] != record["mtime"]
                ):
                    stale = 1
                    changed += 1
                else:
                    stale = int(existing["stale"])
                    unchanged += 1
                updated += 1
            conn.execute(
                """
                INSERT INTO images (
                    image_id, product_image_id, object_id, relative_path, source_path,
                    product, class_name, object_name,
                    filename, identity, pair_key, timestamp, instrument, visit, detector,
                    annotation, filter_token, delta_mag_token, q_token, tisserand_token,
                    width, height, file_size, mtime, discovered_at, updated_at, stale, missing, order_key
                )
                VALUES (
                    :image_id, :product_image_id, :object_id, :relative_path, :source_path,
                    :product, :class_name, :object_name,
                    :filename, :identity, :pair_key, :timestamp, :instrument, :visit, :detector,
                    :annotation, :filter_token, :delta_mag_token, :q_token, :tisserand_token,
                    :width, :height, :file_size, :mtime, :discovered_at, :updated_at, :stale, 0, :order_key
                )
                ON CONFLICT(image_id) DO UPDATE SET
                    order_key = excluded.order_key,
                    product_image_id = excluded.product_image_id,
                    object_id = excluded.object_id,
                    relative_path = excluded.relative_path,
                    source_path = excluded.source_path,
                    product = excluded.product,
                    class_name = excluded.class_name,
                    object_name = excluded.object_name,
                    filename = excluded.filename,
                    identity = excluded.identity,
                    pair_key = excluded.pair_key,
                    timestamp = excluded.timestamp,
                    instrument = excluded.instrument,
                    visit = excluded.visit,
                    detector = excluded.detector,
                    annotation = excluded.annotation,
                    filter_token = excluded.filter_token,
                    delta_mag_token = excluded.delta_mag_token,
                    q_token = excluded.q_token,
                    tisserand_token = excluded.tisserand_token,
                    width = excluded.width,
                    height = excluded.height,
                    file_size = excluded.file_size,
                    mtime = excluded.mtime,
                    updated_at = excluded.updated_at,
                    stale = excluded.stale,
                    missing = 0
                """,
                {
                    **record,
                    "discovered_at": discovered_at,
                    "updated_at": now,
                    "stale": stale,
                },
            )
        sync_objects(conn)
        conn.commit()
    elapsed_seconds = time.perf_counter() - started
    return {
        "records": len(records),
        **duplicate_summary,
        "inserted": inserted,
        "updated": updated,
        "unchanged": unchanged,
        "changed": changed,
        "tisserand_filled": tisserand_filled,
        "elapsed_seconds": round(elapsed_seconds, 3),
    }


def sync_objects(conn: sqlite3.Connection) -> None:
    now = utc_now()
    conn.execute(
        """
        INSERT INTO objects (
            object_id, product, class_name, object_name, image_count, missing_count,
            stale_count, scored_count, active_flag, reviewed_at, updated_at
        )
        SELECT
            i.object_id,
            i.product,
            i.class_name,
            i.object_name,
            SUM(CASE WHEN i.missing = 0 THEN 1 ELSE 0 END) AS image_count,
            SUM(i.missing) AS missing_count,
            SUM(CASE WHEN i.missing = 0 THEN i.stale ELSE 0 END) AS stale_count,
            COUNT(DISTINCT CASE WHEN i.missing = 0 THEN s.image_id END) AS scored_count,
            r.active_flag,
            r.reviewed_at,
            ? AS updated_at
        FROM images i
        LEFT JOIN image_scores s ON s.image_id = i.image_id
        LEFT JOIN object_reviews r ON r.object_id = i.object_id
        GROUP BY i.object_id
        ON CONFLICT(object_id) DO UPDATE SET
            product = excluded.product,
            class_name = excluded.class_name,
            object_name = excluded.object_name,
            image_count = excluded.image_count,
            missing_count = excluded.missing_count,
            stale_count = excluded.stale_count,
            scored_count = excluded.scored_count,
            active_flag = excluded.active_flag,
            reviewed_at = excluded.reviewed_at,
            updated_at = excluded.updated_at
        """,
        (now,),
    )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def optional_float_param(params: dict[str, list[str]], name: str) -> float | None:
    value = params.get(name, [""])[0].strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def optional_int_param(params: dict[str, list[str]], name: str) -> int | None:
    value = params.get(name, [""])[0].strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def query_summary(
    conn: sqlite3.Connection,
    reviewer: str,
    layout: str = "rcc",
) -> dict[str, Any]:
    source_image_total = conn.execute(
        "SELECT COUNT(*) AS n FROM review_eligible_images"
    ).fetchone()["n"]
    image_total = conn.execute("SELECT COUNT(*) AS n FROM review_images").fetchone()[
        "n"
    ]
    object_total = conn.execute(
        "SELECT COUNT(DISTINCT object_id) AS n FROM review_images"
    ).fetchone()["n"]
    scored_total = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM image_scores s
        JOIN review_images i ON i.image_id = s.image_id
        WHERE s.reviewer = ?
        """,
        (reviewer,),
    ).fetchone()["n"]
    active_total = conn.execute(
        """
        SELECT COUNT(DISTINCT r.object_id) AS n
        FROM object_reviews r
        JOIN review_images i ON i.object_id = r.object_id
        WHERE r.reviewer = ? AND r.active_flag = 1
        """,
        (reviewer,),
    ).fetchone()["n"]
    products = [
        row_to_dict(row)
        for row in conn.execute(
            """
            SELECT product, COUNT(*) AS images, COUNT(DISTINCT object_id) AS objects
            FROM review_images
            GROUP BY product
            ORDER BY product
            """
        )
    ]
    return {
        "images": image_total,
        "source_images": source_image_total,
        "duplicates_suppressed": source_image_total - image_total,
        "objects": object_total,
        "scored": scored_total,
        "active_objects": active_total,
        "products": products,
        "reviewer": reviewer,
        "layout": layout,
    }


def query_reveal_review_summary(
    conn: sqlite3.Connection,
    reviewer: str,
) -> dict[str, int]:
    """Count locally matched reveal images and those without saved details."""

    rows = conn.execute(
        """
        SELECT s.tags, s.comment
        FROM review_reveal_scope reveal
        JOIN review_images i ON i.image_id = reveal.image_id
        LEFT JOIN image_scores s
          ON s.image_id = i.image_id
         AND s.reviewer = ?
        """,
        (reviewer,),
    ).fetchall()
    return {
        "matched_images": len(rows),
        "without_notes_or_tags": sum(
            review_text_is_blank(row["tags"]) and review_text_is_blank(row["comment"])
            for row in rows
        ),
    }


def query_batches(conn: sqlite3.Connection, reviewer: str) -> list[dict[str, Any]]:
    """Return reviewer-specific progress for each virtual object batch."""

    rows = conn.execute(
        """
        WITH per_object AS (
            SELECT
                q.batch_number,
                q.queue_rank,
                q.object_id,
                COUNT(i.image_id) AS image_count,
                COUNT(s.image_id) AS scored_count
            FROM review_queue_objects q
            LEFT JOIN review_images i ON i.object_id = q.object_id
            LEFT JOIN image_scores s
                ON s.image_id = i.image_id
               AND s.reviewer = ?
            GROUP BY q.batch_number, q.queue_rank, q.object_id
        )
        SELECT
            batch_number,
            COUNT(*) AS objects,
            SUM(image_count) AS images,
            SUM(scored_count) AS scored_images,
            SUM(
                CASE
                    WHEN image_count > 0 AND scored_count >= image_count THEN 1
                    ELSE 0
                END
            ) AS completed_objects,
            MIN(queue_rank) AS first_rank,
            MAX(queue_rank) AS last_rank
        FROM per_object
        GROUP BY batch_number
        ORDER BY batch_number
        """,
        (reviewer,),
    ).fetchall()
    batches: list[dict[str, Any]] = []
    for row in rows:
        batch = row_to_dict(row)
        batch["label"] = f"batch{int(batch['batch_number']):03d}"
        batch["complete"] = int(batch["objects"] or 0) > 0 and int(
            batch["completed_objects"] or 0
        ) >= int(batch["objects"] or 0)
        batches.append(batch)
    return batches


def object_filter_parts(
    params: dict[str, list[str]],
) -> tuple[list[str], list[Any], bool]:
    where = ["soc.image_count > 0"]
    values: list[Any] = []
    for param, column in (("product", "o.product"), ("class", "o.class_name")):
        requested_values = [
            value.strip() for value in params.get(param, []) if value.strip()
        ]
        if not requested_values:
            continue
        if len(requested_values) == 1:
            where.append(f"{column} = ?")
            values.append(requested_values[0])
        else:
            placeholders = ",".join("?" for _ in requested_values)
            where.append(f"{column} IN ({placeholders})")
            values.extend(requested_values)
    search = params.get("q", [""])[0].strip()
    if search:
        where.append("o.object_name LIKE ?")
        values.append(f"%{search}%")
    if params.get("object_scope_active", ["0"])[0] in {"1", "true", "yes"}:
        where.append(
            """
            EXISTS (
                SELECT 1
                FROM review_object_scope scope
                WHERE scope.class_name = o.class_name
                    AND scope.object_name = o.object_name
            )
            """
        )
    if params.get("queue_active", ["0"])[0] in {"1", "true", "yes"}:
        where.append("q.object_id IS NOT NULL")
    batch_number = optional_int_param(params, "batch")
    if batch_number is not None:
        where.append("q.batch_number = ?")
        values.append(max(1, batch_number))
    state = params.get("state", [""])[0].strip()
    if state == "active":
        where.append("COALESCE(r.active_flag, 0) = 1")
    elif state == "unreviewed":
        where.append("r.object_id IS NULL")
    score_state = params.get("score_state", [""])[0].strip()
    if score_state == "unscored":
        where.append("COALESCE(rs.scored_count, 0) < soc.image_count")
    elif score_state == "scored":
        where.append(
            "COALESCE(rs.scored_count, 0) >= soc.image_count AND soc.image_count > 0"
        )
    if params.get("reveal_missing_details", ["0"])[0] in {"1", "true", "yes"}:
        where.append("COALESCE(rrm.missing_details_count, 0) > 0")
    preset = params.get("preset", [""])[0].strip()
    if preset == "comets-centaurs":
        where.append(
            "(LOWER(o.class_name) LIKE '%comet%' OR LOWER(o.class_name) LIKE '%centaur%')"
        )
    elif preset == "active-unscored":
        where.append(
            "COALESCE(r.active_flag, 0) = 1 "
            "AND COALESCE(rs.scored_count, 0) < soc.image_count"
        )
    elif preset == "artifact-audit":
        where.append("COALESCE(rb.has_bad, 0) = 1")
    min_images = optional_int_param(params, "min_images")
    if min_images is not None:
        where.append("soc.image_count >= ?")
        values.append(max(1, min_images))
    min_pct_peri = optional_float_param(params, "min_pct_peri")
    if min_pct_peri is not None:
        where.append("COALESCE(om.max_pct_peri, -1) >= ?")
        values.append(min_pct_peri)
    max_delta_mag = optional_float_param(params, "max_delta_mag")
    include_missing_delta_mag = params.get("include_missing_delta_mag", ["1"])[0] in {
        "1",
        "true",
        "yes",
    }
    if max_delta_mag is not None:
        delta_mag_filter = "om.min_delta_mag <= ?"
        if include_missing_delta_mag:
            delta_mag_filter = (
                f"({delta_mag_filter} OR COALESCE(om.known_delta_mag_count, 0) = 0)"
            )
        where.append(delta_mag_filter)
        values.append(max_delta_mag)
    max_tj = optional_float_param(params, "max_tj")
    include_missing_tj = params.get("include_missing_tj", ["1"])[0] in {
        "1",
        "true",
        "yes",
    }
    if max_tj is not None:
        tj_filter = "om.min_tj <= ?"
        if include_missing_tj:
            tj_filter = f"({tj_filter} OR COALESCE(om.known_tj_count, 0) = 0)"
        where.append(tj_filter)
        values.append(max_tj)
    unscored_first = params.get("unscored_first", ["0"])[0] in {
        "1",
        "true",
        "yes",
    }
    return where, values, unscored_first


OBJECT_FILTER_CTES = """
WITH scoped_object_counts AS (
    SELECT
        object_id,
        COUNT(*) AS image_count,
        SUM(stale) AS stale_count
    FROM review_images
    GROUP BY object_id
),
reviewer_scores AS (
    SELECT i.object_id, COUNT(*) AS scored_count
    FROM image_scores s
    JOIN review_images i ON i.image_id = s.image_id
    WHERE s.reviewer = ?
    GROUP BY i.object_id
),
reviewer_bad AS (
    SELECT i.object_id, 1 AS has_bad
    FROM image_scores s
    JOIN review_images i ON i.image_id = s.image_id
    WHERE s.reviewer = ? AND s.status = 'bad'
    GROUP BY i.object_id
),
reviewer_reveal_missing AS (
    SELECT i.object_id, COUNT(*) AS missing_details_count
    FROM review_reveal_scope reveal
    JOIN review_images i ON i.image_id = reveal.image_id
    LEFT JOIN image_scores s
      ON s.image_id = i.image_id
     AND s.reviewer = ?
    WHERE review_text_is_blank(s.tags) = 1
      AND review_text_is_blank(s.comment) = 1
    GROUP BY i.object_id
),
object_metrics AS (
    SELECT
        object_id,
        MAX(
            CASE
                WHEN q_token GLOB 'q[0-9][0-9][0-9]'
                THEN CAST(SUBSTR(q_token, 2) AS REAL)
            END
        ) AS max_pct_peri,
        MIN(
            CASE
                WHEN delta_mag_token GLOB 'dm_p[0-9]*'
                THEN CAST(SUBSTR(delta_mag_token, 5) AS REAL) / 10.0
                WHEN delta_mag_token GLOB 'dm_m[0-9]*'
                THEN -CAST(SUBSTR(delta_mag_token, 5) AS REAL) / 10.0
            END
        ) AS min_delta_mag,
        COUNT(
            CASE
                WHEN delta_mag_token GLOB 'dm_[pm][0-9]*'
                THEN 1
            END
        ) AS known_delta_mag_count,
        MIN(
            CASE
                WHEN tisserand_token GLOB 'tj_p[0-9][0-9][0-9]'
                THEN CAST(SUBSTR(tisserand_token, 5) AS REAL) / 100.0
                WHEN tisserand_token GLOB 'tj_m[0-9][0-9][0-9]'
                THEN -CAST(SUBSTR(tisserand_token, 5) AS REAL) / 100.0
            END
        ) AS min_tj,
        COUNT(
            CASE
                WHEN tisserand_token GLOB 'tj_[pm][0-9][0-9][0-9]'
                THEN 1
            END
        ) AS known_tj_count
    FROM review_images
    GROUP BY object_id
)
"""


OBJECT_FILTER_JOINS = """
FROM objects o
JOIN scoped_object_counts soc ON soc.object_id = o.object_id
LEFT JOIN object_reviews r ON r.object_id = o.object_id AND r.reviewer = ?
LEFT JOIN reviewer_scores rs ON rs.object_id = o.object_id
LEFT JOIN reviewer_bad rb ON rb.object_id = o.object_id
LEFT JOIN reviewer_reveal_missing rrm ON rrm.object_id = o.object_id
LEFT JOIN object_metrics om ON om.object_id = o.object_id
LEFT JOIN review_queue_objects q ON q.object_id = o.object_id
"""


def query_filtered_object_count(
    conn: sqlite3.Connection,
    reviewer: str,
    params: dict[str, list[str]],
) -> int:
    where, values, _unscored_first = object_filter_parts(params)
    row = conn.execute(
        f"""
        {OBJECT_FILTER_CTES}
        SELECT COUNT(*) AS n
        {OBJECT_FILTER_JOINS}
        WHERE {' AND '.join(where)}
        """,
        [reviewer, reviewer, reviewer, reviewer, *values],
    ).fetchone()
    return int(row["n"])


def params_with_launch_scope(
    params: dict[str, list[str]],
    config: ReviewConfig,
) -> dict[str, list[str]]:
    """Apply launch-time class scope to object queue requests by default."""

    scoped = {key: list(value) for key, value in params.items()}
    if config.classes and not scoped.get("class"):
        scoped["class"] = list(config.classes)
    if config.object_scope:
        scoped["object_scope_active"] = ["1"]
    if config.queue_objects:
        scoped["queue_active"] = ["1"]
    if not scoped.get("min_images"):
        scoped["min_images"] = [
            (
                "1"
                if config.layout in {"active-asteroids", "manifest"} or config.image_scope is not None
                else str(DEFAULT_MIN_IMAGES)
            )
        ]
    return scoped


def query_objects(
    conn: sqlite3.Connection,
    reviewer: str,
    params: dict[str, list[str]],
) -> list[dict[str, Any]]:
    where, values, unscored_first = object_filter_parts(params)
    reverse_sort = params.get("reverse_sort", ["0"])[0] in {"1", "true", "yes"}
    limit = min(max(int(params.get("limit", ["200"])[0] or 200), 1), 1000)
    queue_active = params.get("queue_active", ["0"])[0] in {"1", "true", "yes"}
    order_parts = []
    if unscored_first:
        order_parts.append(
            "CASE WHEN COALESCE(rs.scored_count, 0) < soc.image_count THEN 0 ELSE 1 END"
        )
    direction = "DESC" if reverse_sort else "ASC"
    if queue_active:
        order_parts.append(f"q.queue_rank {direction}")
    else:
        order_parts.extend(
            [
                f"o.product {direction}",
                f"o.class_name {direction}",
                f"o.object_name {direction}",
            ]
        )
    rows = conn.execute(
        f"""
        {OBJECT_FILTER_CTES}
        SELECT
            o.object_id,
            o.product,
            o.class_name,
            o.object_name,
            soc.image_count,
            0 AS missing_count,
            soc.stale_count,
            COALESCE(rs.scored_count, 0) AS scored_count,
            o.active_flag,
            o.reviewed_at,
            o.updated_at,
            r.active_flag AS reviewer_active_flag,
            r.status AS reviewer_status,
            r.tags AS object_tags,
            r.comment AS object_comment,
            q.queue_rank,
            q.batch_number
        {OBJECT_FILTER_JOINS}
        WHERE {' AND '.join(where)}
        ORDER BY {', '.join(order_parts)}
        LIMIT ?
        """,
        [reviewer, reviewer, reviewer, reviewer, *values, limit],
    ).fetchall()
    return [row_to_dict(row) for row in rows if row is not None]


def query_object_images(
    conn: sqlite3.Connection,
    object_id: str,
    reviewer: str,
    *,
    reveal_only: bool = False,
    reveal_context: bool = False,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            i.*,
            s.score,
            s.status AS score_status,
            s.tags AS score_tags,
            s.comment AS score_comment,
            s.scored_at
        FROM review_images i
        LEFT JOIN image_scores s ON s.image_id = i.image_id AND s.reviewer = ?
        WHERE i.object_id = ?
          AND (
              ? = 0
              OR EXISTS (
                  SELECT 1
                  FROM review_reveal_scope reveal
                  WHERE reveal.image_id = i.image_id
              )
          )
        ORDER BY i.order_key, i.timestamp IS NULL, i.timestamp, i.visit, i.detector, i.filename
        """,
        (reviewer, object_id, 1 if reveal_only and not reveal_context else 0),
    ).fetchall()
    images = [row_to_dict(row) for row in rows if row is not None]
    if reveal_context:
        reveal_ids = {
            row[0] for row in conn.execute(
                "SELECT image_id FROM review_reveal_scope"
            )
        }
        included = set()
        for index, image in enumerate(images):
            if image["image_id"] in reveal_ids:
                included.update(range(max(0, index - 10), min(len(images), index + 11)))
        images = [image for index, image in enumerate(images) if index in included]
    visible_ids = {image["image_id"] for image in images}
    for image in images:
        pair_rows = conn.execute(
            """
            SELECT paired.image_id, paired.product, paired.relative_path,
                   paired.filename, paired.annotation
            FROM review_images paired
            WHERE paired.object_name = ?
              AND paired.class_name = ?
              AND paired.pair_key = ?
              AND paired.image_id != ?
              AND (
                  ? = 0
                  OR EXISTS (
                      SELECT 1
                      FROM review_reveal_scope reveal
                      WHERE reveal.image_id = paired.image_id
                  )
              )
            ORDER BY paired.product, paired.relative_path
            """,
            (
                image["object_name"],
                image["class_name"],
                image["pair_key"],
                image["image_id"],
                1 if reveal_only and not reveal_context else 0,
            ),
        ).fetchall()
        image["pairs"] = [
            row_to_dict(row) for row in pair_rows
            if row is not None and (not reveal_context or row["image_id"] in visible_ids)
        ]
    return images


def _rounded_probability_percent(value: float) -> int:
    return int(math.floor(value * 100.0 + 0.5))


def query_object_reveal(
    conn: sqlite3.Connection,
    object_id: str,
    reveal_scores: tuple[RevealScore, ...],
) -> dict[str, Any]:
    """Return rounded reveal scores only for images belonging to one object."""

    scores_by_image = {score.image_id: score for score in reveal_scores}
    rows = conn.execute(
        """
        SELECT image_id
        FROM review_images
        WHERE object_id = ?
        ORDER BY timestamp IS NULL, timestamp, visit, detector, filename
        """,
        (object_id,),
    ).fetchall()
    images = []
    for row in rows:
        score = scores_by_image.get(str(row["image_id"]))
        if score is None:
            continue
        images.append(
            {
                "image_id": score.image_id,
                "model_score_r3_percent": _rounded_probability_percent(
                    score.model_score_r3
                ),
                "model_score_operational_percent": _rounded_probability_percent(
                    score.model_score_operational
                ),
            }
        )
    return {"object_id": object_id, "images": images}


def record_event(
    conn: sqlite3.Connection,
    *,
    config: ReviewConfig,
    event_type: str,
    object_id: str | None = None,
    image_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO review_events (
            event_time, reviewer, session_id, event_type, object_id, image_id, payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now(),
            config.reviewer,
            config.session_id,
            event_type,
            object_id,
            image_id,
            json.dumps(payload or {}, sort_keys=True),
        ),
    )


def upsert_object_review(
    conn: sqlite3.Connection,
    config: ReviewConfig,
    payload: dict[str, Any],
) -> dict[str, Any]:
    object_id = str(payload["object_id"])
    active_flag = payload.get("active_flag")
    if active_flag is not None:
        active_flag = 1 if bool(active_flag) else 0
    status = str(payload.get("status") or "reviewed")
    tags = str(payload.get("tags") or "")
    comment = str(payload.get("comment") or "")
    now = utc_now()
    conn.execute(
        """
        INSERT INTO object_reviews (
            object_id, reviewer, active_flag, status, tags, comment, session_id, reviewed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(object_id, reviewer) DO UPDATE SET
            active_flag = excluded.active_flag,
            status = excluded.status,
            tags = excluded.tags,
            comment = excluded.comment,
            session_id = excluded.session_id,
            reviewed_at = excluded.reviewed_at
        """,
        (
            object_id,
            config.reviewer,
            active_flag,
            status,
            tags,
            comment,
            config.session_id,
            now,
        ),
    )
    record_event(
        conn,
        config=config,
        event_type="object_review",
        object_id=object_id,
        payload={
            "active_flag": active_flag,
            "status": status,
            "tags": tags,
            "comment": comment,
        },
    )
    sync_objects(conn)
    return {
        "object_id": object_id,
        "active_flag": active_flag,
        "status": status,
        "reviewed_at": now,
    }


def upsert_image_score(
    conn: sqlite3.Connection,
    config: ReviewConfig,
    payload: dict[str, Any],
    *,
    sync: bool = True,
) -> dict[str, Any]:
    image_id = str(payload["image_id"])
    if config.layout == "manifest" and not conn.execute(
        "SELECT 1 FROM review_images WHERE image_id = ?", (image_id,)
    ).fetchone():
        raise ValueError("Only primary images in this dataset may be scored.")
    score = payload.get("score")
    status = str(payload.get("status") or "scored")
    if score is not None:
        score = int(score)
        if not config.score_min <= score <= config.score_max:
            raise ValueError("score is outside the configured scale")
        status = "scored"
    tags = str(payload.get("tags") or "")
    comment = str(payload.get("comment") or "")
    now = utc_now()
    conn.execute("UPDATE images SET stale = 0 WHERE image_id = ?", (image_id,))
    conn.execute(
        """
        INSERT INTO image_scores (
            image_id, reviewer, score, status, tags, comment, session_id, scored_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(image_id, reviewer) DO UPDATE SET
            score = excluded.score,
            status = excluded.status,
            tags = excluded.tags,
            comment = excluded.comment,
            session_id = excluded.session_id,
            scored_at = excluded.scored_at
        """,
        (
            image_id,
            config.reviewer,
            score,
            status,
            tags,
            comment,
            config.session_id,
            now,
        ),
    )
    object_row = conn.execute(
        "SELECT object_id FROM images WHERE image_id = ?",
        (image_id,),
    ).fetchone()
    object_id = object_row["object_id"] if object_row else None
    record_event(
        conn,
        config=config,
        event_type="image_score",
        object_id=object_id,
        image_id=image_id,
        payload={"score": score, "status": status, "tags": tags, "comment": comment},
    )
    if sync:
        sync_objects(conn)
    return {"image_id": image_id, "score": score, "status": status, "scored_at": now}


def upsert_image_scores(
    conn: sqlite3.Connection,
    config: ReviewConfig,
    payloads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    results = [
        upsert_image_score(conn, config, payload, sync=False) for payload in payloads
    ]
    sync_objects(conn)
    return results


def clear_image_score(
    conn: sqlite3.Connection,
    config: ReviewConfig,
    payload: dict[str, Any],
) -> dict[str, Any]:
    image_id = str(payload["image_id"])
    object_row = conn.execute(
        "SELECT object_id FROM images WHERE image_id = ?",
        (image_id,),
    ).fetchone()
    object_id = object_row["object_id"] if object_row else None
    conn.execute(
        "DELETE FROM image_scores WHERE image_id = ? AND reviewer = ?",
        (image_id, config.reviewer),
    )
    record_event(
        conn,
        config=config,
        event_type="clear_score",
        object_id=object_id,
        image_id=image_id,
        payload={},
    )
    sync_objects(conn)
    return {"image_id": image_id, "status": "cleared", "object_id": object_id}


def undo_last_score(conn: sqlite3.Connection, config: ReviewConfig) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT event_id, image_id, object_id
        FROM review_events
        WHERE reviewer = ? AND event_type = 'image_score'
        ORDER BY event_id DESC
        LIMIT 1
        """,
        (config.reviewer,),
    ).fetchone()
    if row is None or row["image_id"] is None:
        return {"undone": False}
    conn.execute(
        "DELETE FROM image_scores WHERE image_id = ? AND reviewer = ?",
        (row["image_id"], config.reviewer),
    )
    record_event(
        conn,
        config=config,
        event_type="undo_score",
        object_id=row["object_id"],
        image_id=row["image_id"],
        payload={"undone_event_id": row["event_id"]},
    )
    sync_objects(conn)
    return {"undone": True, "image_id": row["image_id"], "object_id": row["object_id"]}


def export_reviews(config: ReviewConfig) -> dict[str, str]:
    export_dir = config.export_dir
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    image_csv = export_dir / f"thumbnail_activity_image_labels_{stamp}.csv"
    object_csv = export_dir / f"thumbnail_activity_object_summary_{stamp}.csv"
    events_jsonl = export_dir / f"thumbnail_activity_events_{stamp}.jsonl"
    expert_csv = (
        export_dir / f"active_asteroids_expert_scores_{stamp}.csv"
        if config.layout == "active-asteroids"
        else None
    )

    with connect_review_db(config) as conn:
        image_rows = conn.execute(
            """
            SELECT
                i.image_id, i.relative_path, i.source_path,
                i.product, i.class_name, i.object_name,
                i.filename, i.identity, i.timestamp, i.instrument, i.visit, i.detector,
                i.annotation, i.filter_token, i.delta_mag_token, i.q_token, i.tisserand_token,
                i.width, i.height, i.file_size, i.mtime, i.stale, i.missing,
                s.reviewer, s.score, s.status, s.tags, s.comment, s.scored_at,
                r.active_flag AS object_active_flag, r.status AS object_status,
                r.tags AS object_tags, r.comment AS object_comment
            FROM images i
            LEFT JOIN image_scores s ON s.image_id = i.image_id
            LEFT JOIN object_reviews r ON r.object_id = i.object_id AND r.reviewer = s.reviewer
            UNION ALL
            SELECT
                s.image_id, NULL AS relative_path, NULL AS source_path,
                NULL AS product, NULL AS class_name,
                NULL AS object_name, NULL AS filename, NULL AS identity, NULL AS timestamp,
                NULL AS instrument, NULL AS visit, NULL AS detector, NULL AS annotation,
                NULL AS filter_token, NULL AS delta_mag_token, NULL AS q_token,
                NULL AS tisserand_token, NULL AS width, NULL AS height, NULL AS file_size,
                NULL AS mtime, NULL AS stale, NULL AS missing,
                s.reviewer, s.score, s.status, s.tags, s.comment, s.scored_at,
                NULL AS object_active_flag, NULL AS object_status,
                NULL AS object_tags, NULL AS object_comment
            FROM image_scores s
            LEFT JOIN images i ON i.image_id = s.image_id
            WHERE i.image_id IS NULL
            ORDER BY product, class_name, object_name, timestamp, visit, detector
            """
        ).fetchall()
        object_rows = conn.execute(
            """
            SELECT
                o.*, r.reviewer, r.active_flag AS reviewer_active_flag,
                r.status AS reviewer_status,
                r.tags,
                r.comment,
                r.reviewed_at AS reviewer_reviewed_at
            FROM objects o
            LEFT JOIN object_reviews r ON r.object_id = o.object_id
            ORDER BY o.product, o.class_name, o.object_name
            """
        ).fetchall()
        event_rows = conn.execute(
            "SELECT * FROM review_events ORDER BY event_id"
        ).fetchall()
        expert_rows = (
            conn.execute(
                """
                SELECT
                    i.identity AS thumbnail_basename,
                    s.score
                FROM review_queue_objects q
                JOIN review_images i ON i.object_id = q.object_id
                JOIN image_scores s
                    ON s.image_id = i.image_id
                   AND s.reviewer = ?
                WHERE s.status = 'scored'
                  AND s.score IS NOT NULL
                ORDER BY q.queue_rank, i.timestamp, i.visit, i.filename
                """,
                (config.reviewer,),
            ).fetchall()
            if expert_csv is not None
            else []
        )

    def write_csv(path: Path, rows: list[sqlite3.Row]) -> None:
        fieldnames = [k for k in rows[0].keys() if k != "source_path"] if rows else ["empty"]
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row_to_dict(row))

    write_csv(image_csv, image_rows)
    write_csv(object_csv, object_rows)
    if expert_csv is not None:
        with expert_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["thumbnail_basename", "score"],
            )
            writer.writeheader()
            for row in expert_rows:
                writer.writerow(row_to_dict(row))
    with events_jsonl.open("w", encoding="utf-8") as handle:
        for row in event_rows:
            handle.write(json.dumps(row_to_dict(row), sort_keys=True) + "\n")

    classification_snapshot = write_classification_parquet(
        config.db_path,
        classification_parquet_path(config),
    )

    exported = {
        "image_labels_csv": str(image_csv),
        "object_summary_csv": str(object_csv),
        "events_jsonl": str(events_jsonl),
        "classification_parquet": str(classification_snapshot["parquet"]),
    }
    if expert_csv is not None:
        exported["active_asteroids_expert_scores_csv"] = str(expert_csv)
    return exported


class ThumbnailReviewHandler(BaseHTTPRequestHandler):
    server_version = "RCCThumbnailReview/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        if getattr(self.server, "quiet", False):
            return
        super().log_message(format, *args)

    @property
    def config(self) -> ReviewConfig:
        return self.server.config  # type: ignore[attr-defined]

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        def public_value(value):
            if isinstance(value, dict):
                return {k: public_value(v) for k, v in value.items() if k != "source_path"}
            if isinstance(value, list):
                return [public_value(v) for v in value]
            if isinstance(value, str) and not value.startswith("/api/") and Path(value).is_absolute():
                return Path(value).name
            return value
        data = json.dumps(public_value(payload), sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_error_json(
        self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST
    ) -> None:
        self._send_json({"error": message}, status=status)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > 1048576:
            raise ValueError("Request body is too large.")
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)
        try:
            if path == "/":
                self._serve_static("index.html")
            elif path.startswith("/static/"):
                self._serve_static(path.removeprefix("/static/"))
            elif path == "/api/summary":
                with connect_review_db(self.config) as conn:
                    summary = query_summary(
                        conn,
                        self.config.reviewer,
                        self.config.layout,
                    )
                    summary.update(title=self.config.title, score_min=self.config.score_min,
                                   score_max=self.config.score_max, tags=self.config.tags,
                                   show_metadata=self.config.show_metadata)
                    summary["reveal_available"] = bool(self.config.reveal_scores)
                    summary["comparison_mode"] = self.server.comparisons is not None
                    summary["reveal_images"] = len(self.config.reveal_scores)
                    reveal_review = query_reveal_review_summary(
                        conn,
                        self.config.reviewer,
                    )
                    summary["reveal_matched_images"] = reveal_review["matched_images"]
                    summary["reveal_without_notes_or_tags"] = reveal_review[
                        "without_notes_or_tags"
                    ]
                    self._send_json(summary)
            elif path == "/api/batches":
                with connect_review_db(self.config) as conn:
                    self._send_json(query_batches(conn, self.config.reviewer))
            elif path == "/api/objects":
                scoped_params = params_with_launch_scope(params, self.config)
                with connect_review_db(self.config) as conn:
                    self._send_json(
                        query_objects(conn, self.config.reviewer, scoped_params)
                    )
            elif path == "/api/objects/count":
                scoped_params = params_with_launch_scope(params, self.config)
                with connect_review_db(self.config) as conn:
                    self._send_json(
                        {
                            "objects": query_filtered_object_count(
                                conn,
                                self.config.reviewer,
                                scoped_params,
                            )
                        }
                    )
            elif path.startswith("/api/objects/") and path.endswith("/images"):
                object_id = unquote(
                    path.removeprefix("/api/objects/").removesuffix("/images")
                )
                reveal_only = params.get("reveal_only", ["0"])[0].lower() in {
                    "1",
                    "true",
                    "yes",
                }
                with connect_review_db(self.config) as conn:
                    images = query_object_images(
                        conn,
                        object_id,
                        self.config.reviewer,
                        reveal_only=reveal_only,
                        reveal_context=params.get("reveal_context", ["0"])[0].lower()
                        in {"1", "true", "yes"},
                    )
                    if self.server.comparisons is not None:
                        self.server.comparisons.annotate(images)
                    trails.annotate(images, legacy=self.config.layout != "manifest")
                    self._send_json(images)
            elif path.startswith("/api/objects/") and path.endswith("/reveal"):
                object_id = unquote(
                    path.removeprefix("/api/objects/").removesuffix("/reveal")
                )
                with connect_review_db(self.config) as conn:
                    self._send_json(
                        query_object_reveal(
                            conn,
                            object_id,
                            self.config.reveal_scores,
                        )
                    )
            elif path.startswith("/api/comparisons/"):
                self._serve_comparison(path.removeprefix("/api/comparisons/"))
            elif path.startswith("/api/images/"):
                image_id = unquote(path.removeprefix("/api/images/"))
                self._serve_image(image_id)
            else:
                self._send_error_json("not found", HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_error_json("Request failed; check local configuration and inputs.", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            self._send_error_json("Cross-origin requests are not allowed.", HTTPStatus.FORBIDDEN)
            return
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._send_error_json("Cross-site requests are not allowed.", HTTPStatus.FORBIDDEN)
            return
        try:
            payload = self._read_json()
            if path == "/api/rescan":
                comparisons = (
                    manifest.comparisons(self.config) if self.config.layout == "manifest" else
                    load_comparisons(self.config.root, self.config.comparison_root)
                    if self.config.comparison_root else None
                )
                result = index_thumbnails(self.config)
                self.server.comparisons = comparisons
                self._send_json(result)
            elif path == "/api/export":
                self._send_json(export_reviews(self.config))
            elif path == "/api/shutdown":
                result = write_classification_parquet(
                    self.config.db_path,
                    classification_parquet_path(self.config),
                )
                if not result.get("written"):
                    raise RuntimeError(
                        f"Classification export failed: {result.get('reason')}"
                    )
                self.server.classification_exported_on_shutdown = True  # type: ignore[attr-defined]
                self._send_json(result)
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            elif path == "/api/object-review":
                with connect_review_db(self.config) as conn:
                    result = upsert_object_review(conn, self.config, payload)
                    conn.commit()
                self._send_json(result)
            elif path == "/api/image-score":
                with connect_review_db(self.config) as conn:
                    result = upsert_image_score(conn, self.config, payload)
                    conn.commit()
                self._send_json(result)
            elif path == "/api/image-scores":
                scores = payload.get("scores") or []
                if not isinstance(scores, list):
                    raise ValueError("scores must be a list")
                with connect_review_db(self.config) as conn:
                    result = upsert_image_scores(conn, self.config, scores)
                    conn.commit()
                self._send_json({"results": result})
            elif path == "/api/image-score/clear":
                with connect_review_db(self.config) as conn:
                    result = clear_image_score(conn, self.config, payload)
                    conn.commit()
                self._send_json(result)
            elif path == "/api/undo-score":
                with connect_review_db(self.config) as conn:
                    result = undo_last_score(conn, self.config)
                    conn.commit()
                self._send_json(result)
            else:
                self._send_error_json("not found", HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_error_json("Invalid request or unavailable local input.", HTTPStatus.BAD_REQUEST)

    def _serve_static(self, relative: str) -> None:
        static_root = resources.files("astro_stampede").joinpath("review_static")
        if relative not in {"app.js", "trails.js", "index.html", "styles.css"}:
            self._send_error_json("static file not found", HTTPStatus.NOT_FOUND)
            return
        target = static_root.joinpath(relative)
        if not target.is_file():
            self._send_error_json("static file not found", HTTPStatus.NOT_FOUND)
            return
        data = target.read_bytes()
        mime = mimetypes.guess_type(relative)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_comparison(self, image_id: str) -> None:
        catalog = self.server.comparisons
        path = catalog.paths.get(image_id) if catalog else None
        if path is None or not path.is_file():
            self._send_error_json("comparison not found", HTTPStatus.NOT_FOUND)
            return
        if not path.resolve().is_relative_to(catalog.root.resolve()):
            self._send_error_json("comparison path escaped root", HTTPStatus.FORBIDDEN)
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_image(self, image_id: str) -> None:
        with connect_review_db(self.config) as conn:
            row = conn.execute(
                "SELECT relative_path, source_path FROM review_images WHERE image_id = ?",
                (image_id,),
            ).fetchone()
        if row is None:
            self._send_error_json("image not found", HTTPStatus.NOT_FOUND)
            return
        path = (
            Path(row["source_path"]).resolve()
            if row["source_path"]
            else (self.config.root / row["relative_path"]).resolve()
        )
        allowed_roots = (
            (self.config.root, *self.config.additional_roots)
            if self.config.layout == "active-asteroids"
            else (self.config.root,)
        )
        if not any(
            path.is_relative_to(candidate.resolve()) for candidate in allowed_roots
        ):
            self._send_error_json("image path escaped root", HTTPStatus.FORBIDDEN)
            return
        if not path.is_file():
            self._send_error_json("image file missing", HTTPStatus.NOT_FOUND)
            return
        if path.name.lower().endswith(".png.gz"):
            with gzip.open(path, "rb") as handle:
                data = handle.read()
        else:
            data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ReviewServer(ThreadingHTTPServer):
    config: ReviewConfig
    comparisons: ComparisonCatalog | None
    quiet: bool
    daemon_threads = True
    classification_exported_on_shutdown: bool


def build_server(
    config: ReviewConfig,
    host: str,
    port: int,
    *,
    quiet: bool = False,
) -> ReviewServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Local review must bind to a loopback host.")
    comparisons = (
        manifest.comparisons(config) if config.layout == "manifest" else
        load_comparisons(config.root, config.comparison_root)
        if config.comparison_root else None
    )
    server = ReviewServer((host, port), ThumbnailReviewHandler)
    server.config = config
    server.comparisons = comparisons
    server.quiet = quiet
    server.classification_exported_on_shutdown = False
    return server


def serve(
    config: ReviewConfig,
    *,
    host: str,
    port: int,
    open_browser: bool,
    quiet: bool = False,
) -> None:
    prepare_classification_state(config)
    summary = index_thumbnails(config)
    server = build_server(config, host, port, quiet=quiet)
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"[astro-stampede] Indexed {summary['records']} images; serving {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[thumbnail-review] Stopping.", flush=True)
    finally:
        if not server.classification_exported_on_shutdown:
            exported = write_classification_parquet(
                config.db_path,
                classification_parquet_path(config),
            )
            if exported.get("written"):
                print(
                    "[astro-stampede] Saved classification snapshot",
                    flush=True,
                )
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch the local RCC thumbnail activity review interface."
    )
    parser.add_argument(
        "--comparison-root",
        type=Path,
        help=(
            "Ponder comparison bundle with manifest.csv and comparison_mapping.csv. "
            "Original --root must contain its manifest.csv; comparison images are "
            "display-only and never added to the scoring queue."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help=f"RCC root. Default: {DEFAULT_ROOT}",
    )
    parser.add_argument(
        "--layout",
        choices=("rcc", "active-asteroids"),
        default="rcc",
        help=(
            "Filesystem layout to review. 'rcc' expects product/class/object "
            "directories; 'active-asteroids' expects a flat PNG directory and "
            "requires --object-list. Default: rcc."
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=lambda value: _positive_int(value, "--batch-size"),
        default=DEFAULT_BATCH_SIZE,
        help=(
            "Objects per virtual batch in active-asteroids mode. "
            f"Default: {DEFAULT_BATCH_SIZE}."
        ),
    )
    parser.add_argument(
        "--additional-root",
        "--additional-images-dir",
        dest="additional_roots",
        action="append",
        type=Path,
        default=[],
        help=(
            "Additional directory to search recursively for matching "
            "<object_id>_*.png files in active-asteroids mode. Repeatable."
        ),
    )
    parser.add_argument(
        "--products",
        nargs="+",
        default=list(DEFAULT_PRODUCTS),
        help="Product directory names to index. Default: preliminary_visit_image.",
    )
    parser.add_argument(
        "--classes",
        "--class",
        dest="classes",
        nargs="+",
        default=None,
        help="Optional class directory names to index, such as Comet or Centaur.",
    )
    scope_group = parser.add_mutually_exclusive_group()
    scope_group.add_argument(
        "--object-list",
        "--active-learning-objects",
        dest="object_list",
        type=Path,
        default=None,
        help=(
            "CSV limiting the queue to listed objects. Reads object_id/object_name "
            "across all local classes; optional object_class/class_name or "
            "relative_path/input_path/path values select a specific class. Intersects with "
            "<root>/preliminary_visit_image/<class>/<object> directories, and scans "
            "only those object folders."
        ),
    )
    scope_group.add_argument(
        "--image-list",
        type=Path,
        default=None,
        help=(
            "CSV limiting review to exact images plus chronological context. "
            "Reads relative_path/png_path/input_path/path and falls back to "
            "rubin_stable_review_image_id."
        ),
    )
    parser.add_argument(
        "--reveal-list",
        type=Path,
        default=None,
        help=(
            "Optional image-level CSV for on-demand Reveal mode. Requires "
            "rubin_stable_review_image_id or usable image paths plus "
            "model_score_r3 and model_score_operational. When omitted, an "
            "--object-list containing both score columns is auto-detected."
        ),
    )
    parser.add_argument(
        "--image-context",
        type=lambda value: _nonnegative_int(value, "--image-context"),
        default=DEFAULT_IMAGE_CONTEXT,
        help=(
            "Preceding and following images to include for each --image-list target. "
            f"Default: {DEFAULT_IMAGE_CONTEXT}."
        ),
    )
    parser.add_argument(
        "--no-image-context",
        action="store_const",
        const=0,
        dest="image_context",
        help="Disable neighboring context images (equivalent to --image-context 0).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Local runtime SQLite label DB (never synced). Default: {DEFAULT_DB}",
    )
    parser.add_argument(
        "--classification-parquet",
        type=Path,
        default=DEFAULT_CLASSIFICATION_PARQUET,
        help=(
            "Portable classification snapshot imported at startup and atomically rewritten "
            f"on export/shutdown. Default: {DEFAULT_CLASSIFICATION_PARQUET}"
        ),
    )
    parser.add_argument(
        "--write-classification-parquet",
        action="store_true",
        help="Write the portable classification Parquet from --db and exit without serving.",
    )
    parser.add_argument(
        "--merge-classification-parquet",
        action="append",
        type=Path,
        default=[],
        help=(
            "Before --write-classification-parquet, merge this portable snapshot into the "
            "local DB when it is newer. Repeatable."
        ),
    )
    parser.add_argument(
        "--index-db",
        type=Path,
        default=None,
        help=(
            "Rebuildable SQLite thumbnail index cache. Default: "
            "a per-root cache beneath the user cache directory."
        ),
    )
    parser.add_argument(
        "--export-dir",
        type=Path,
        default=DEFAULT_EXPORT_DIR,
        help=f"Export directory. Default: {DEFAULT_EXPORT_DIR}",
    )
    parser.add_argument(
        "--orbital-cache",
        type=Path,
        default=None,
        help=(
            "Optional orbital-elements parquet used to fill missing TJ tokens. "
            "Default: <root>/data/object_info/orbits/orbital_elements.parquet "
            "or RCC_DATA_ROOT/object_info/orbits/orbital_elements.parquet."
        ),
    )
    parser.add_argument(
        "--reviewer",
        default=getpass.getuser(),
        help="Reviewer name for scores.",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host. Default: 127.0.0.1"
    )
    parser.add_argument(
        "--port", type=int, default=8765, help="Bind port. Default: 8765"
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the browser automatically.",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress HTTP request logs."
    )
    parser.add_argument("--import-snapshot", action="store_true",
                        help="Explicitly replace labels/events from a newer snapshot.")
    return parser


def _nonnegative_int(value: str, option: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{option} must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"{option} must be zero or greater")
    return parsed


def _positive_int(value: str, option: str) -> int:
    parsed = _nonnegative_int(value, option)
    if parsed == 0:
        raise argparse.ArgumentTypeError(f"{option} must be greater than zero")
    return parsed


def main(argv: list[str] | None = None, *, defaults: dict | None = None) -> None:
    parser = build_parser()
    parser.set_defaults(**(defaults or {}))
    args = parser.parse_args(argv)
    active_asteroids = args.layout == "active-asteroids"
    active_asteroids_db_override = os.environ.get("RCC_ACTIVE_ASTEROIDS_DB")
    if active_asteroids and args.db == DEFAULT_DB:
        db_path = (
            Path(active_asteroids_db_override).expanduser()
            if active_asteroids_db_override
            else ACTIVE_ASTEROIDS_DB
        )
    else:
        db_path = args.db.expanduser()
    classification_parquet = (
        ACTIVE_ASTEROIDS_CLASSIFICATION_PARQUET
        if active_asteroids
        and args.classification_parquet == DEFAULT_CLASSIFICATION_PARQUET
        else args.classification_parquet.expanduser()
    )
    export_dir = (
        ACTIVE_ASTEROIDS_EXPORT_DIR
        if active_asteroids and args.export_dir == DEFAULT_EXPORT_DIR
        else args.export_dir.expanduser()
    )
    if args.write_classification_parquet:
        result = reconcile_classification_parquet(
            db_path,
            classification_parquet,
            tuple(path.expanduser() for path in args.merge_classification_parquet),
        )
        if result.get("written"):
            print(
                "[thumbnail-review] Wrote "
                f"{result['records']} classification record(s) to {result['parquet']}"
            )
        else:
            print(
                f"[thumbnail-review] No local label DB found; preserving {result['parquet']}"
            )
        return
    root = args.root.expanduser().resolve()
    additional_roots = tuple(
        candidate.expanduser().resolve() for candidate in args.additional_roots
    )
    object_list_path = args.object_list.expanduser() if args.object_list else None
    image_list_path = args.image_list.expanduser() if args.image_list else None
    reveal_list_path, reveal_auto_detected = resolve_reveal_list_path(
        args.reveal_list,
        object_list_path,
        layout=args.layout,
    )
    if active_asteroids and object_list_path is None:
        raise SystemExit("--layout active-asteroids requires --object-list.")
    if active_asteroids and image_list_path is not None:
        raise SystemExit("--layout active-asteroids does not support --image-list.")
    if additional_roots and not active_asteroids:
        raise SystemExit("--additional-root requires --layout active-asteroids.")
    active_selection = (
        read_active_asteroids_object_list(
            object_list_path,
            root=root,
            additional_roots=additional_roots,
            batch_size=args.batch_size,
        )
        if active_asteroids and object_list_path is not None
        else None
    )
    reveal_selection = (
        read_reveal_scores(
            reveal_list_path,
            products=tuple(args.products),
        )
        if reveal_list_path is not None and not active_asteroids
        else None
    )
    image_selection = (
        read_image_list_scope(
            image_list_path,
            root=root,
            products=tuple(args.products),
            classes=tuple(args.classes) if args.classes else None,
            context=args.image_context,
            preferred_image_ids=(
                {score.image_id for score in reveal_selection.scores}
                if reveal_selection is not None
                else None
            ),
        )
        if image_list_path is not None and not active_asteroids
        else None
    )
    if active_selection is not None:
        object_scope = active_selection.object_scope
    elif image_selection is not None:
        object_scope = image_selection.object_scope
    else:
        object_scope = (
            read_object_list_scope(
                object_list_path,
                root=root,
                products=tuple(args.products),
            )
            if object_list_path is not None
            else None
        )
    if object_list_path is not None and not object_scope:
        expected_root = (
            root
            if active_asteroids
            else ", ".join(str(root / product) for product in args.products)
        )
        raise SystemExit(
            "No objects from "
            f"{object_list_path} matched local directories under "
            f"{expected_root}."
        )
    products = (ACTIVE_ASTEROIDS_PRODUCT,) if active_asteroids else tuple(args.products)
    classes = (
        None if active_asteroids else tuple(args.classes) if args.classes else None
    )
    config = ReviewConfig(
        root=root,
        products=products,
        classes=classes,
        object_scope=object_scope,
        object_list_path=object_list_path,
        db_path=db_path,
        index_db_path=(
            args.index_db.expanduser() if args.index_db else default_index_db_path(root)
        ),
        reviewer=args.reviewer,
        session_id=f"{args.reviewer}-{int(time.time())}-{os.getpid()}",
        export_dir=export_dir,
        orbital_cache_path=(
            args.orbital_cache.expanduser() if args.orbital_cache else None
        ),
        classification_parquet_path=classification_parquet,
        image_scope=(
            image_selection.image_ids if image_selection is not None else None
        ),
        image_paths=(
            image_selection.relative_paths if image_selection is not None else None
        ),
        image_list_path=image_list_path,
        image_context=args.image_context,
        image_list_summary=(
            image_selection.summary if image_selection is not None else None
        ),
        layout=args.layout,
        batch_size=args.batch_size,
        queue_objects=(
            active_selection.queue_objects if active_selection is not None else None
        ),
        queue_summary=(
            active_selection.summary if active_selection is not None else None
        ),
        additional_roots=additional_roots,
        reveal_list_path=reveal_list_path,
        reveal_scores=(reveal_selection.scores if reveal_selection is not None else ()),
        reveal_summary=(
            reveal_selection.summary if reveal_selection is not None else None
        ),
        reveal_auto_detected=reveal_auto_detected,
        comparison_root=args.comparison_root,
        import_snapshot=args.import_snapshot,
    )
    serve(
        config,
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
        quiet=args.quiet,
    )


if __name__ == "__main__":
    main()
