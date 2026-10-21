"""Versioned thumbnail identities and non-destructive compatibility resolution."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .adapters.rcc_names import get_thumbnail_product_identity

VERSION = 2


class LegacyIdentityError(ValueError):
    """Historical classifications lack a registered identity policy."""


def encoded(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class Policy:
    key: str = "parsed-name"
    namespace: str | None = None
    include_class: bool = False

    def __post_init__(self):
        if self.key not in {"parsed-name", "manifest-id"}:
            raise ValueError("Unknown identity key.")
        if self.namespace is not None and not self.namespace.strip():
            raise ValueError("Identity namespace must be nonempty when supplied.")

    def document(self):
        return dict(version=VERSION, key=self.key, namespace=self.namespace, include_class=self.include_class)

    @classmethod
    def from_config(cls, config):
        return cls(config.identity_key, config.identity_namespace, config.identity_include_class)


def logical_name(filename):
    name = Path(filename).name
    return name[:-3] if name.lower().endswith(".png.gz") else name


def components(filename):
    name = logical_name(filename)
    # Adapter recognition is based only on the name, never on directory layout.
    product = get_thumbnail_product_identity(name)
    if product is not None:
        return [
            "rcc-observation",
            product.object_token,
            product.instrument,
            product.visit,
            product.detector_key,
            product.product_kind,
        ]
    stem = name[:-4] if name.lower().endswith(".png") else name
    base = stem.split("__", 1)[0]
    recognized_base = re.sub(r"_(?:warp|withVar)$", "", base)
    # AA observations and Ponder/EDP2 coadds retain all observation tokens.
    if re.fullmatch(r"[^_]+_\d{8}-\d{5}_[^_]+(?:_.*)?", recognized_base):
        return ["aa-observation", recognized_base]
    if re.fullmatch(r".+_\d{13}_[^_]+_[^_]+_deep_coadd_n\d+_\d+x\d+(?:_[0-9.]+dPix)?", base):
        return ["coadd", base]
    return ["basename", name]


def thumbnail(filename, policy=Policy(), *, manifest_id=None, object_class=None):
    if policy.key == "manifest-id":
        if not manifest_id:
            raise ValueError("manifest-id identity requires an explicit manifest row image_id.")
        parts = ["manifest-id", manifest_id]
    else:
        parts = components(filename)
    if policy.include_class and not object_class:
        raise ValueError(
            "--identity-include-class requires an explicitly supplied object class for every primary image."
        )
    value = [VERSION, "thumbnail", parts]
    if policy.namespace is not None:
        value.append(["namespace", policy.namespace])
    if policy.include_class:
        value.append(["class", object_class])
    key = encoded(value)
    return hashlib.sha256(key.encode()).hexdigest(), key


def object_identity(dataset, name):
    key = encoded([VERSION, "object", dataset, name])
    return hashlib.sha256(key.encode()).hexdigest(), key


def schema_name(conn):
    return "labels" if any(r[1] == "labels" for r in conn.execute("PRAGMA database_list")) else "main"


def initialize(conn, schema="main"):
    # Individual statements deliberately do not commit the caller's transaction.
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {schema}.identity_registry (
        entity_kind TEXT NOT NULL, canonical_id TEXT NOT NULL, normalized_key TEXT NOT NULL,
        policy TEXT NOT NULL, filename TEXT NOT NULL, PRIMARY KEY(entity_kind, canonical_id),
        UNIQUE(entity_kind, normalized_key))""")
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {schema}.identity_aliases (
        entity_kind TEXT NOT NULL, legacy_id TEXT NOT NULL, canonical_id TEXT NOT NULL,
        PRIMARY KEY(entity_kind, legacy_id))""")
    conn.execute(f"""CREATE TABLE IF NOT EXISTS {schema}.identity_provenance (
        entity_kind TEXT NOT NULL, legacy_id TEXT NOT NULL, canonical_id TEXT NOT NULL,
        source TEXT NOT NULL, PRIMARY KEY(entity_kind, legacy_id, canonical_id, source))""")


def register(conn, kind, canonical_id, key, policy, filename="", legacy_id=None, source=""):
    schema = schema_name(conn)
    row = conn.execute(
        f"SELECT normalized_key FROM {schema}.identity_registry WHERE entity_kind=? AND canonical_id=?",
        (kind, canonical_id),
    ).fetchone()
    if row and row[0] != key:
        raise ValueError("Contradictory canonical identity key.")
    if conn.execute(
        f"SELECT 1 FROM {schema}.identity_aliases WHERE entity_kind=? AND legacy_id=?", (kind, canonical_id)
    ).fetchone():
        raise ValueError("An alias cannot also be a canonical identity.")
    conn.execute(
        f"INSERT OR IGNORE INTO {schema}.identity_registry VALUES (?,?,?,?,?)",
        (kind, canonical_id, key, encoded(policy.document()), logical_name(filename)),
    )
    if legacy_id and legacy_id != canonical_id:
        prior = conn.execute(
            f"SELECT canonical_id FROM {schema}.identity_aliases WHERE entity_kind=? AND legacy_id=?",
            (kind, legacy_id),
        ).fetchone()
        if prior and prior[0] != canonical_id:
            raise ValueError(f"Contradictory alias for {kind} {legacy_id}.")
        # A canonical key is immutable and can never become an alias to another policy.
        if conn.execute(
            f"SELECT 1 FROM {schema}.identity_registry WHERE entity_kind=? AND canonical_id=?",
            (kind, legacy_id),
        ).fetchone():
            raise ValueError("A canonical identity cannot be remapped; select a new policy explicitly.")
        conn.execute(
            f"INSERT OR IGNORE INTO {schema}.identity_aliases VALUES (?,?,?)", (kind, legacy_id, canonical_id)
        )
        conn.execute(
            f"INSERT OR IGNORE INTO {schema}.identity_provenance VALUES (?,?,?,?)",
            (kind, legacy_id, canonical_id, source),
        )


def require_policy(conn, policy):
    schema = schema_name(conn)
    value = encoded(policy.document())
    row = conn.execute(f"SELECT value FROM {schema}.review_metadata WHERE key='identity_policy'").fetchone()
    if row and row[0] != value:
        raise ValueError(
            "Identity policy changed: run the explicit compatibility preflight before using this store."
        )
    if not row:
        count = sum(
            conn.execute(f"SELECT COUNT(*) FROM {schema}.{table}").fetchone()[0]
            for table in ("image_scores", "object_reviews")
        )
        if count:
            raise LegacyIdentityError(
                "Existing classifications require an identity compatibility preflight; select legacy indexes or rich exports explicitly."
            )
        conn.execute(f"INSERT INTO {schema}.review_metadata VALUES ('identity_policy', ?)", (value,))
    return value


def resolve_id(conn, kind, value):
    schema = schema_name(conn)
    row = conn.execute(
        f"SELECT canonical_id FROM {schema}.identity_aliases WHERE entity_kind=? AND legacy_id=?",
        (kind, value),
    ).fetchone()
    return row[0] if row else value


def instant(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return (
            result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
        )
    except (ValueError, TypeError, AttributeError):
        return None


def refresh(conn, *, alias_overrides=None, readonly=False):
    """Materialize one consistent effective view, preserving every durable source row."""
    schema = schema_name(conn)
    if not readonly:
        initialize(conn, schema)
    exists = conn.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE name='identity_aliases'").fetchone()
    aliases = (
        {(r[0], r[1]): r[2] for r in conn.execute(f"SELECT * FROM {schema}.identity_aliases")}
        if exists
        else {}
    )
    aliases.update(alias_overrides or {})
    events = {}
    for row in conn.execute(f"SELECT * FROM {schema}.review_events ORDER BY event_id"):
        r = dict(row)
        for kind in ("image", "object"):
            if r.get(kind + "_id"):
                events.setdefault((kind, r[kind + "_id"], r["reviewer"]), []).append(r)
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS identity_conflicts (entity_kind TEXT, canonical_id TEXT, reviewer TEXT, reason TEXT)"
    )
    conn.execute("DELETE FROM identity_conflicts")
    for kind, table, timestamp in [
        ("image", "image_scores", "scored_at"),
        ("object", "object_reviews", "reviewed_at"),
    ]:
        target = "effective_" + table
        conn.execute(f"CREATE TEMP TABLE IF NOT EXISTS {target} AS SELECT * FROM {schema}.{table} WHERE 0")
        conn.execute(f"CREATE INDEX IF NOT EXISTS temp.idx_{target} ON {target} ({kind}_id, reviewer)")
        conn.execute(f"DELETE FROM {target}")
        groups = {}
        idfield = kind + "_id"
        fields = (
            ("score", "status", "tags", "comment")
            if kind == "image"
            else ("active_flag", "status", "tags", "comment")
        )
        for row in conn.execute(f"SELECT * FROM {schema}.{table}"):
            r = dict(row)
            canonical = aliases.get((kind, r[idfield]), r[idfield])
            groups.setdefault((canonical, r["reviewer"]), []).append(r)
        for (canonical, reviewer), records in groups.items():
            dates = [instant(r[timestamp]) for r in records]
            reason = None
            if any(d is None for d in dates):
                finalists = records
                reason = "Invalid classification timestamp" if len(records) > 1 else None
            else:
                latest = max(dates)
                finalists = [r for r, d in zip(records, dates) if d == latest]
            payloads = {encoded([r[f] for f in fields]) for r in finalists}
            if len(payloads) > 1 and reason is None:
                ordered = []
                for r in finalists:
                    attributable = []
                    for event in events.get((kind, r[idfield], reviewer), []):
                        if event["session_id"] != r["session_id"] or instant(event["event_time"]) != instant(
                            r[timestamp]
                        ):
                            continue
                        try:
                            payload = json.loads(event["payload"])
                        except (ValueError, TypeError):
                            continue
                        if event["event_type"] == (
                            "image_score" if kind == "image" else "object_review"
                        ) and all(payload.get(f) == r[f] for f in fields):
                            attributable.append(event["event_id"])
                        elif r["status"] == "cleared" and event["event_type"] in {
                            "clear_score",
                            "undo_score",
                        }:
                            attributable.append(event["event_id"])
                    ordered.append((max(attributable) if attributable else None, r))
                if all(order is not None for order, _ in ordered) and len(
                    {order for order, _ in ordered}
                ) == len(ordered):
                    finalists = [max(ordered, key=lambda item: item[0])[1]]
                else:
                    reason = "Conflicting equal timestamps without attributable audit ordering"
            if reason:
                conn.execute(
                    "INSERT INTO identity_conflicts VALUES (?,?,?,?)", (kind, canonical, reviewer, reason)
                )
                continue
            selected = sorted(finalists, key=lambda r: r[idfield])[0].copy()
            if selected["status"] == "cleared":
                continue
            selected[idfield] = canonical
            conn.execute(
                f"INSERT INTO {target} ({','.join(selected)}) VALUES ({','.join('?' for _ in selected)})",
                tuple(selected.values()),
            )


def prepare_records(records, config, conn):
    """Keep source occurrences and deterministically select one display representative."""
    policy = Policy.from_config(config)
    _schema = schema_name(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS image_sources (image_id TEXT NOT NULL, source_path TEXT NOT NULL, relative_path TEXT NOT NULL, object_id TEXT NOT NULL, filename TEXT NOT NULL, PRIMARY KEY(image_id, source_path))"
    )
    conn.execute("DELETE FROM image_sources")
    conn.execute("DELETE FROM source_identity_keys")
    selected = {}
    for record in sorted(
        records, key=lambda r: (r["relative_path"].startswith("additional-"), r["relative_path"])
    ):
        iid, key = thumbnail(
            record["filename"],
            policy,
            manifest_id=record.get("manifest_id"),
            object_class=record.get("explicit_class", record.get("class_name")),
        )
        oid, okey = object_identity(config.dataset or record["product"], record["object_name"])
        register(conn, "image", iid, key, policy, record["filename"])
        register(conn, "object", oid, okey, policy)
        source = record.get("source_path") or str(config.root / record["relative_path"])
        conn.execute(
            "INSERT OR IGNORE INTO image_sources VALUES (?,?,?,?,?)",
            (iid, source, record["relative_path"], oid, record["filename"]),
        )
        for source_id in {
            record["image_id"],
            record.get("legacy_image_id"),
            thumbnail(record["filename"])[0],
        } - {None}:
            conn.execute("INSERT OR IGNORE INTO source_identity_keys VALUES (?,?)", (source_id, iid))
        record.update(image_id=iid, product_image_id=iid, object_id=oid, identity=key, normalized_key=key)
        selected.setdefault(iid, record)
    return list(selected.values())


def selected_id(conn, value):
    rows = conn.execute(
        "SELECT canonical_id FROM source_identity_keys WHERE source_id=?", (value,)
    ).fetchall()
    if len(rows) > 1:
        raise ValueError("Ambiguous selection identity; use explicit manifest row references.")
    return rows[0][0] if rows else resolve_id(conn, "image", value)
