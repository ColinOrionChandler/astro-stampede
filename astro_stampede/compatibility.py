"""Explicit read-only compatibility preflight and transactional alias registration.

Run ``python -m astro_stampede.compatibility --help``. Evidence files are runtime
inputs and must never be checked into the public repository.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
from . import identities as ids


def read_evidence(indexes=(), exports=(), manifests=(), legacy_dataset=None):
    for path in indexes:
        with sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            for row in conn.execute('SELECT * FROM images'):
                yield dict(row), f'index:{Path(path).resolve()}'
    for path in (*exports, *manifests):
        with Path(path).open(newline='', encoding='utf-8-sig') as handle:
            for row in csv.DictReader(handle):
                if path in manifests:
                    row['filename'] = Path(row.get('relative_path') or '').name
                    row['manifest_id'] = row.get('image_id')
                    row['object_name'] = row.get('object_id')
                    row['product'] = legacy_dataset or ''
                    if row.get('role') == 'comparison':
                        continue
                    if not legacy_dataset:
                        raise ValueError('--legacy-dataset is required for manifest evidence.')
                    # Version 1 generic manifest identity, including its original JSON spacing.
                    row['image_id'] = hashlib.sha256(json.dumps([legacy_dataset, 'image', row['manifest_id']], ensure_ascii=True).encode()).hexdigest()
                    row['legacy_object_id'] = hashlib.sha256(json.dumps([legacy_dataset, 'object', row['object_name']], ensure_ascii=True).encode()).hexdigest()
                yield row, f'file:{Path(path).resolve()}'


def mappings(evidence, policy=ids.Policy()):
    result = {}
    problems = []
    ignored = 0
    for row, source in evidence:
        name = row.get('filename')
        if not name:
            ignored += 1
            continue
        old = row.get('image_id') or row.get('legacy_id')
        if not old:
            problems.append(f'{source}: missing legacy image ID')
            continue
        try:
            canonical, key = ids.thumbnail(name, policy, manifest_id=row.get('manifest_id'), object_class=row.get('object_class') or row.get('class_name'))
        except ValueError as exc:
            problems.append(f'{source}: {exc}')
            continue
        entries = [('image', old, canonical, key, name)]
        dataset = row.get('product') or row.get('dataset')
        obj = row.get('object_name')
        if dataset and obj:
            oid, okey = ids.object_identity(dataset, obj)
            old_object = row.get('legacy_object_id') or row.get('object_id')
            if not old_object and row.get('class_name'):
                old_object = hashlib.sha256(f"{dataset}/{row['class_name']}/{obj}".encode()).hexdigest()
            if old_object:
                entries.append(('object', old_object, oid, okey, ''))
        for kind, legacy, canonical, key, name in entries:
            entry = (kind, legacy)
            value = result.setdefault(entry, {'targets': {}, 'sources': set()})
            value['targets'][canonical] = (key, name)
            value['sources'].add(source)
    contradictions = [f'{kind}:{old}' for (kind, old), v in result.items() if len(v['targets']) != 1]
    return result, problems, ignored, contradictions


def fingerprint(conn):
    return {table: {'rows': conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],
                    'sha256': hashlib.sha256(ids.encoded([list(r) for r in conn.execute(f'SELECT * FROM {table} ORDER BY 1,2')]).encode()).hexdigest()}
            for table in ('image_scores', 'object_reviews', 'review_events')}


def preflight(db, evidence, policy=ids.Policy(), *, apply=False, accept_policy_change=False):
    mapping, problems, ignored, contradictions = mappings(evidence, policy)
    path = Path(db).resolve()
    conn = sqlite3.connect(f'{path.as_uri()}?mode={"rw" if apply else "ro"}', uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
        before = fingerprint(conn)
        existing_tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        registry = set()
        aliases = {}
        if 'identity_registry' in existing_tables:
            registry = {(r[0], r[1]) for r in conn.execute('SELECT entity_kind, canonical_id FROM identity_registry')}
            aliases = {(r[0], r[1]): r[2] for r in conn.execute('SELECT * FROM identity_aliases')}
        for entry, value in mapping.items():
            target = next(iter(value['targets']))
            if (entry in aliases and aliases[entry] != target) or (entry in registry and entry[1] != target):
                contradictions.append(f'{entry[0]}:{entry[1]} conflicts with registered identity')
        unmapped = {}
        for kind, table in [('image', 'image_scores'), ('object', 'object_reviews')]:
            unmapped[kind] = [r[0] for r in conn.execute(f'SELECT DISTINCT {kind}_id FROM {table}')
                              if (kind, r[0]) not in mapping and (kind, r[0]) not in registry and (kind, r[0]) not in aliases]
        prior = conn.execute("SELECT value FROM review_metadata WHERE key='identity_policy'").fetchone()
        policy_changed = bool(prior and prior[0] != ids.encoded(policy.document()))
        result = dict(db=str(path), policy=policy.document(), historical=before,
                      mappings={kind: sum(k[0] == kind for k in mapping) for kind in ('image', 'object')},
                      unmapped=unmapped, contradictions=contradictions, problems=problems,
                      ignored_empty_names=ignored, policy_changed=policy_changed, applied=False)
        if not apply and not contradictions and not problems:
            overrides = {entry: next(iter(value['targets'])) for entry, value in mapping.items()}
            ids.refresh(conn, alias_overrides=overrides, readonly=True)
            result['conflicts'] = [dict(r) for r in conn.execute('SELECT * FROM identity_conflicts')]
            result['effective_images'] = conn.execute('SELECT COUNT(*) FROM effective_image_scores').fetchone()[0]
        if apply:
            if contradictions or problems:
                raise ValueError('Contradictory or invalid evidence; no aliases registered: ' + ids.encoded(result))
            if policy_changed and not accept_policy_change:
                raise ValueError('Policy change requires --accept-policy-change after reviewing the preflight.')
            ids.initialize(conn)
            for (kind, old), entry in mapping.items():
                canonical = next(iter(entry['targets']))
                key, name = entry['targets'][canonical]
                for source in sorted(entry['sources']):
                    ids.register(conn, kind, canonical, key, policy, name, old, source)
            conn.execute("INSERT OR REPLACE INTO review_metadata VALUES ('identity_policy', ?)", (ids.encoded(policy.document()),))
            ids.refresh(conn)
            result['conflicts'] = [dict(r) for r in conn.execute('SELECT * FROM identity_conflicts')]
            result['effective_images'] = conn.execute('SELECT COUNT(*) FROM effective_image_scores').fetchone()[0]
            if fingerprint(conn) != before:
                raise RuntimeError('Historical records changed during alias registration.')
            conn.commit()
            result['applied'] = True
        return result
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--index', type=Path, action='append', default=[])
    parser.add_argument('--export', type=Path, action='append', default=[])
    parser.add_argument('--manifest', type=Path, action='append', default=[])
    parser.add_argument('--legacy-dataset')
    parser.add_argument('--identity-key', choices=['parsed-name', 'manifest-id'], default='parsed-name')
    parser.add_argument('--identity-namespace')
    parser.add_argument('--identity-include-class', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--accept-policy-change', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    result = preflight(args.db, read_evidence(args.index, args.export, args.manifest, args.legacy_dataset),
                       ids.Policy(args.identity_key, args.identity_namespace, args.identity_include_class),
                       apply=args.apply, accept_policy_change=args.accept_policy_change)
    text = json.dumps(result, indent=2)
    if args.report:
        args.report.write_text(text + '\n')
    print(text)

if __name__ == '__main__':
    main()
