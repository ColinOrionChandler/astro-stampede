"""RCC compatibility identity rules; no deployment configuration."""

from __future__ import annotations

import math

import os

import re

from dataclasses import dataclass

from pathlib import Path

from typing import Any, Iterable, Mapping

THUMBNAIL_ANNOTATION_SEPARATOR = "__"

THUMBNAIL_IDENTITY_RE = re.compile(
    r"^(?P<object>.+?)_"
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}(?:\.\d+)?)_"
    r"(?P<instrument>[^_]+)_"
    r"(?P<visit>\d+)_"
    r"(?P<detector>[^_]+)$"
)

@dataclass(frozen=True)
class ThumbnailProductIdentity:
    """Timestamp-independent identity for one on-disk RCC image product."""

    object_token: str
    instrument: str
    visit: str
    detector_key: str
    product_kind: str
    dataset_type: str = ""
    class_name: str = ""

def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        result = value != value
    except Exception:
        result = False
    if isinstance(result, bool) and result:
        return True
    text = str(value).strip()
    return text == "" or text.lower() in {"nan", "none", "<na>", "null"}

def _to_float(value: Any) -> float | None:
    if _is_missing(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number

def sanitize_filename_token(value: Any, *, slash_replacement: str = "-") -> str:
    """Return a conservative filename-safe token for RCC thumbnail components."""

    if _is_missing(value):
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+", "_", text)
    text = text.replace("/", slash_replacement).replace("\\", slash_replacement)
    text = text.replace(":", "-")
    text = text.replace("'", "")
    text = re.sub(r"[^A-Za-z0-9_.+-]+", "_", text)
    text = re.sub(r"_+", "_", text)
    text = text.strip("._-")
    return text

def canonicalize_comet_fragment_name(value: Any) -> str:
    """Return canonical uppercase spelling for recognized comet fragments.

    Only full object-name spellings are changed. Ordinary object names retain
    their original case.
    """

    if _is_missing(value):
        return ""
    text = str(value).strip()

    numbered_parenthetical = re.fullmatch(
        r"(?P<number>\d+)(?P<kind>[PDI])\s*\(\s*(?P<fragment>[A-Z]+)\s*\)",
        text,
        flags=re.IGNORECASE,
    )
    if numbered_parenthetical:
        return (
            f"{numbered_parenthetical.group('number')}"
            f"{numbered_parenthetical.group('kind').upper()}-"
            f"{numbered_parenthetical.group('fragment').upper()}"
        )

    numbered_hyphen = re.fullmatch(
        r"(?P<number>\d+)(?P<kind>[PDI])\s*-\s*(?P<fragment>[A-Z]+)",
        text,
        flags=re.IGNORECASE,
    )
    if numbered_hyphen:
        return (
            f"{numbered_hyphen.group('number')}"
            f"{numbered_hyphen.group('kind').upper()}-"
            f"{numbered_hyphen.group('fragment').upper()}"
        )

    prefixed_hyphen = re.fullmatch(
        r"(?P<prefix>[CPDXAI])\s*/\s*"
        r"(?P<year>\d{4})\s+(?P<designation>[A-Z]{1,2}\d+[A-Z0-9]*)"
        r"\s*-\s*(?P<fragment>[A-Z]+)",
        text,
        flags=re.IGNORECASE,
    )
    if prefixed_hyphen:
        return (
            f"{prefixed_hyphen.group('prefix').upper()}/"
            f"{prefixed_hyphen.group('year')} "
            f"{prefixed_hyphen.group('designation').upper()}-"
            f"{prefixed_hyphen.group('fragment').upper()}"
        )

    filesystem_prefixed = re.fullmatch(
        r"(?P<prefix>[CPDXAI])(?P<slash>[+-])(?P<year>\d{4})_"
        r"(?P<designation>[A-Z]{1,2}\d+[A-Z0-9]*)-"
        r"(?P<fragment>[A-Z]+)",
        text,
        flags=re.IGNORECASE,
    )
    if filesystem_prefixed:
        return (
            f"{filesystem_prefixed.group('prefix').upper()}"
            f"{filesystem_prefixed.group('slash')}"
            f"{filesystem_prefixed.group('year')}_"
            f"{filesystem_prefixed.group('designation').upper()}-"
            f"{filesystem_prefixed.group('fragment').upper()}"
        )

    return text

def sanitize_object_token(value: Any, *, slash_replacement: str = "-") -> str:
    """Return an RCC object token with canonical comet-fragment case."""

    return sanitize_filename_token(
        canonicalize_comet_fragment_name(value),
        slash_replacement=slash_replacement,
    )

def encode_tisserand_jupiter(value: Any) -> str | None:
    """Encode the Tisserand parameter with respect to Jupiter for annotations."""

    number = _to_float(value)
    if number is None:
        return None
    hundredths = int(round(abs(number) * 100.0))
    sign = "m" if number < 0 and hundredths != 0 else "p"
    return f"tj_{sign}{hundredths:03d}"

def get_thumbnail_extension(filename_or_path: str | os.PathLike[str]) -> str:
    """Return the RCC image extension, preserving compound ``.fits.fz`` suffixes."""

    name = os.path.basename(os.fspath(filename_or_path)).lower()
    if name.endswith(".fits.fz"):
        return ".fits.fz"
    return Path(name).suffix

def strip_thumbnail_extension(filename_or_path: str | os.PathLike[str]) -> str:
    """Return a thumbnail basename without its RCC image extension."""

    name = os.path.basename(os.fspath(filename_or_path))
    if name.lower().endswith(".fits.fz"):
        return name[:-8]
    return os.path.splitext(name)[0]

def get_thumbnail_identity(filename_or_path: str | os.PathLike[str]) -> str:
    """Return the stable identity portion of a thumbnail filename or path."""

    stem = strip_thumbnail_extension(filename_or_path)
    return stem.split(THUMBNAIL_ANNOTATION_SEPARATOR, 1)[0]

def normalize_detector_key(value: Any) -> str:
    """Return a stable detector-set token such as ``72`` or ``72+75``."""

    text = sanitize_filename_token(value)
    if not text:
        return ""
    pieces = [piece for piece in re.split(r"[+,;\s]+", text) if piece]
    if not pieces:
        return text

    def sort_key(piece: str) -> tuple[int, int | str]:
        return (0, int(piece)) if piece.isdigit() else (1, piece)

    return "+".join(sorted(dict.fromkeys(pieces), key=sort_key))

def thumbnail_product_kind(filename_or_path: str | os.PathLike[str]) -> str:
    """Return the coarse product kind used for duplicate grouping."""

    ext = get_thumbnail_extension(filename_or_path).lower()
    if ext in {".fits", ".fits.fz"}:
        return "fits"
    if ext == ".png":
        return "png"
    return ext.lstrip(".")

def parse_thumbnail_identity_fields(
    filename_or_path: str | os.PathLike[str],
) -> dict[str, str] | None:
    """Parse the stable identity tokens from an RCC thumbnail filename."""

    identity = get_thumbnail_identity(filename_or_path)
    for suffix in ("_warp", "_withVar"):
        if identity.endswith(suffix):
            identity = identity[: -len(suffix)]
            break
    match = THUMBNAIL_IDENTITY_RE.match(identity)
    if not match:
        return None
    fields = match.groupdict()
    fields["detector"] = normalize_detector_key(fields["detector"])
    return fields

def build_thumbnail_product_identity(
    *,
    object_token: Any,
    instrument: Any,
    visit: Any,
    detector: Any,
    product_kind: str,
    dataset_type: Any = "",
    class_name: Any = "",
) -> ThumbnailProductIdentity:
    """Build a timestamp-independent RCC product identity."""

    return ThumbnailProductIdentity(
        object_token=sanitize_object_token(object_token),
        instrument=sanitize_filename_token(instrument),
        visit=sanitize_filename_token(visit),
        detector_key=normalize_detector_key(detector),
        product_kind=sanitize_filename_token(product_kind).lower(),
        dataset_type=sanitize_filename_token(dataset_type),
        class_name=sanitize_filename_token(class_name),
    )

def get_thumbnail_product_identity(
    filename_or_path: str | os.PathLike[str],
    *,
    dataset_type: Any = "",
    class_name: Any = "",
) -> ThumbnailProductIdentity | None:
    """Return the timestamp-independent product identity for a thumbnail path."""

    fields = parse_thumbnail_identity_fields(filename_or_path)
    if fields is None:
        return None
    return build_thumbnail_product_identity(
        object_token=fields["object"],
        instrument=fields["instrument"],
        visit=fields["visit"],
        detector=fields["detector"],
        product_kind=thumbnail_product_kind(filename_or_path),
        dataset_type=dataset_type,
        class_name=class_name,
    )
