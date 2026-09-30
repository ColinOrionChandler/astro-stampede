"""Read-only comparison images linked by Ponder's explicit target-key mapping."""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def bundle_root(path: Path) -> Path:
    path = path.expanduser().resolve()
    if not (path / "manifest.csv").is_file() and (path / path.name / "manifest.csv").is_file():
        path = path / path.name
    if not (path / "manifest.csv").is_file():
        raise ValueError(f"No manifest.csv in comparison bundle: {path}")
    return path


def read_rows(path: Path, required: set[str]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(f"{path} requires columns: {', '.join(sorted(required))}")
        return list(reader)


def cutout_path(root: Path, value: str) -> Path | None:
    """Rebase remote manifest paths using their exact cutouts-relative suffix."""
    parts = Path(value).parts
    if "cutouts" not in parts:
        return None
    suffix = Path(*parts[parts.index("cutouts"):])
    path = (root / suffix).resolve()
    if not path.is_relative_to((root / "cutouts").resolve()) or path.suffix != ".png":
        return None
    return path


@dataclass
class ComparisonCatalog:
    root: Path
    images: dict[str, dict[str, Any]] = field(default_factory=dict)
    paths: dict[str, Path] = field(default_factory=dict)

    def annotate(self, images: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for image in images:
            image["comparison"] = self.images.get(
                image["relative_path"], {"status": "No mapped comparison"}
            )
        return images


def load_comparisons(primary_root: Path, comparison_root: Path) -> ComparisonCatalog:
    root = bundle_root(comparison_root)
    catalog = ComparisonCatalog(root)
    required = {"target_key", "png_path"}
    # Manifests are appendable; the latest outcome for each target is authoritative.
    primary = {r["target_key"]: r for r in read_rows(primary_root / "manifest.csv", required)}
    comparisons = {r["target_key"]: r for r in read_rows(root / "manifest.csv", required)}
    mappings = read_rows(root / "comparison_mapping.csv", {
        "primary_target_key", "comparison_target_key",
    })
    targets: dict[str, set[str]] = {}
    for row in mappings:
        original = primary.get(row["primary_target_key"])
        if not original:
            continue
        path = cutout_path(primary_root, original["png_path"])
        if path is not None:
            relative = path.relative_to(primary_root.resolve()).as_posix()
            targets.setdefault(relative, set()).add(row["comparison_target_key"])
    for relative, keys in targets.items():
        candidates: dict[str, tuple[Path, dict[str, str]]] = {}
        missing = []
        for key in sorted(keys):
            row = comparisons.get(key)
            path = cutout_path(root, row["png_path"]) if row else None
            if path is None or not path.is_file():
                missing.append(
                    "file missing locally" if path is not None
                    else (row or {}).get("status") or "not generated locally"
                )
            else:
                candidates[str(path)] = (path, row)
        if len(candidates) > 1 or (candidates and missing):
            catalog.images[relative] = {"status": "Ambiguous comparison mapping"}
        elif not candidates:
            catalog.images[relative] = {
                "status": "Comparison unavailable: " + ", ".join(sorted(set(missing))),
            }
        else:
            path, row = next(iter(candidates.values()))
            key = hashlib.sha256(path.relative_to(root).as_posix().encode()).hexdigest()
            catalog.paths[key] = path
            catalog.images[relative] = {
                "status": "available",
                "url": f"/api/comparisons/{key}",
                "filename": path.name,
                "band": row.get("band", ""),
                "visit": row.get("visit", ""),
                "datetime": row.get("datetime", ""),
            }
    return catalog
