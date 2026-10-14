"""Synthetic identity, migration and persistence contracts."""
import csv
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from astro_stampede import cli, compatibility as compat, identities as ids, manifest, review, settings
from astro_stampede.demo import png_bytes


def config_at(tmp_path, names=('Synthetic_20880101-12345_9001.png',), **options):
    root = tmp_path / 'images'
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, name in enumerate(names):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png_bytes())
        rows.append(dict(image_id=f'row-{index}', object_id='Synthetic', relative_path=name, object_class='Class-A'))
    file = tmp_path / 'manifest.csv'
    with file.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    _, args = cli.parse_args(['--root', str(root), '--manifest', str(file), '--dataset', 'synthetic', '--reviewer', 'reader'])
    return replace(cli.make_config(args), **options)


def legacy_db(path, rows, events=(), objects=()):
    with review.connect_db(path) as conn:
        review.initialize_label_db(conn)
        conn.executemany('INSERT INTO image_scores VALUES (?,?,?,?,?,?,?,?)', rows)
        conn.executemany('INSERT INTO review_events VALUES (?,?,?,?,?,?,?,?)', events)
        conn.executemany('INSERT INTO object_reviews VALUES (?,?,?,?,?,?,?,?)', objects)
        conn.commit()


def score(old, value=2, reviewer='reader', date='2088-01-01T00:00:00+00:00', **kw):
    return (old, reviewer, value, kw.get('status', 'scored'), kw.get('tags', ''), kw.get('comment', ''), kw.get('session', 's'), date)


def evidence(*pairs):
    return [(dict(image_id=old, filename=name, product='synthetic', object_name='Synthetic', class_name='Class-A'), 'synthetic-evidence') for old, name in pairs]


def test_filename_equivalences_and_distinctions():
    rcc = 'Synthetic_2088-01-01T00-00-00_Camera_9001_4+3.png'
    same = 'Synthetic_2088-01-02T00-00-00.123_Camera_9001_3,4_withVar__r_dm_p01.png.gz'
    assert ids.thumbnail(rcc) == ids.thumbnail(same)
    for different in ('9001_3.png', '9002_3+4.png', '9001_3+5.png'):
        assert ids.thumbnail(rcc) != ids.thumbnail('Synthetic_2088-01-01T00-00-00_Camera_' + different)
    aa = 'Synthetic_20880101-12345_9001.png'
    assert ids.thumbnail(aa) == ids.thumbnail(aa[:-4] + '__r_q123.png.gz')
    assert ids.thumbnail(aa) != ids.thumbnail(aa.replace('12345', '12346'))
    coadd = 'Synthetic_2088010100000_4_r_deep_coadd_n3_100x100.png'
    assert ids.thumbnail(coadd) == ids.thumbnail(coadd[:-4] + '__q01.png.gz')
    assert ids.thumbnail(coadd) != ids.thumbnail(coadd.replace('n3', 'n4'))
    assert ids.thumbnail('Unknown__r.png') != ids.thumbnail('Unknown.png')
    assert ids.thumbnail('Unknown.png') != ids.thumbnail('unknown.png')
    assert ids.thumbnail('Unknown.png.gz') == ids.thumbnail('/elsewhere/Unknown.png')


def test_explicit_overrides_and_unambiguous_keys():
    name = 'Synthetic.png'
    default = ids.thumbnail(name)[0]
    assert default == ids.thumbnail(name, object_class='Class-A')[0]
    assert default != ids.thumbnail(name, ids.Policy(namespace='scope'))[0]
    assert ids.thumbnail(name, ids.Policy(include_class=True), object_class='A')[0] != ids.thumbnail(name, ids.Policy(include_class=True), object_class='B')[0]
    with pytest.raises(ValueError, match='explicitly supplied'):
        ids.thumbnail(name, ids.Policy(include_class=True))
    with pytest.raises(ValueError, match='manifest'):
        ids.thumbnail(name, ids.Policy(key='manifest-id'))
    assert ids.thumbnail(name, ids.Policy(key='manifest-id'), manifest_id='row')[0] == ids.thumbnail('renamed.png', ids.Policy(key='manifest-id'), manifest_id='row')[0]
    assert ids.thumbnail('a/b.png', ids.Policy(namespace='x/y'))[0] != ids.thumbnail('y/b.png', ids.Policy(namespace='x'))[0]


def test_manifest_directory_moves_classes_and_datasets_share_score(tmp_path):
    cfg = config_at(tmp_path)
    review.index_thumbnails(cfg)
    iid = manifest.records(cfg)[0]['image_id']
    with review.connect_review_db(cfg) as conn:
        review.upsert_image_score(conn, cfg, dict(image_id=iid, score=8, tags='trail', comment='persist'))
        conn.commit()
    newroot = tmp_path / 'moved'
    path = newroot / 'other-product' / 'Class-B' / 'OtherGroup' / 'Synthetic_20880101-12345_9001.png'
    path.parent.mkdir(parents=True); path.write_bytes(png_bytes())
    other = replace(cfg, root=newroot, dataset='other', products=('other-product',), layout='rcc', manifest_path=None, index_db_path=tmp_path/'other-index.sqlite')
    review.index_thumbnails(other)
    with review.connect_review_db(other) as conn:
        obj = review.query_objects(conn, cfg.reviewer, {})[0]
        image = review.query_object_images(conn, obj['object_id'], cfg.reviewer)[0]
        assert (image['image_id'], image['score'], image['score_tags'], image['score_comment']) == (iid, 8, 'trail', 'persist')
        assert review.query_summary(conn, cfg.reviewer)['scored'] == 1
    assert review.object_id_for('dataset', 'A', 'group') == review.object_id_for('dataset', 'B', 'group')
    assert review.object_id_for('dataset', 'A', 'group') != review.object_id_for('other', 'A', 'group')


def test_alias_latest_payload_reviewers_and_historical_preservation(tmp_path):
    cfg = config_at(tmp_path)
    name = 'Synthetic_20880101-12345_9001.png'
    legacy_db(cfg.db_path, [score('old-a'), score('old-b', 7, date='2088-01-02T00:00:00Z', tags='tail', comment='new'), score('old-a', 4, reviewer='other')])
    before = cfg.db_path.read_bytes()
    report = compat.preflight(cfg.db_path, evidence(('old-a', name), ('old-b', name)))
    assert not report['applied'] and not report['unmapped']['image']
    assert cfg.db_path.read_bytes() == before
    result = compat.preflight(cfg.db_path, evidence(('old-a', name), ('old-b', name)), apply=True)
    assert result['effective_images'] == 2
    assert result['historical']['image_scores']['rows'] == 3
    again = compat.preflight(cfg.db_path, evidence(('old-a', name), ('old-b', name)), apply=True)
    assert result['historical'] == again['historical']
    review.index_thumbnails(cfg)
    with review.connect_review_db(cfg) as conn:
        selected = conn.execute("SELECT * FROM effective_image_scores WHERE reviewer='reader'").fetchone()
        assert (selected['score'], selected['tags'], selected['comment']) == (7, 'tail', 'new')
        assert conn.execute("SELECT COUNT(*) FROM identity_aliases WHERE entity_kind='image'").fetchone()[0] == 2
        assert conn.execute('SELECT COUNT(*) FROM image_scores').fetchone()[0] == 3
        assert conn.execute('SELECT image_count FROM objects').fetchone()[0] == 1


@pytest.mark.parametrize('ordered', [False, True])
def test_equal_timestamp_conflicts_and_attributable_event_order(tmp_path, ordered):
    cfg = config_at(tmp_path)
    name = 'Synthetic_20880101-12345_9001.png'
    rows = [score('old-a', 2), score('old-b', 7)]
    events = [(i + 1, row[-1], row[1], row[-2], 'image_score', None, row[0], json.dumps(dict(score=row[2], status=row[3], tags=row[4], comment=row[5]))) for i, row in enumerate(rows)] if ordered else []
    legacy_db(cfg.db_path, rows, events)
    report = compat.preflight(cfg.db_path, evidence(('old-a', name), ('old-b', name)), apply=True)
    assert bool(report['conflicts']) is not ordered
    with review.connect_db(cfg.db_path) as conn:
        ids.refresh(conn)
        row = conn.execute('SELECT score FROM effective_image_scores').fetchone()
        assert (row[0] if row else None) == (7 if ordered else None)


def test_unmapped_blank_and_contradictory_evidence(tmp_path):
    cfg = config_at(tmp_path)
    legacy_db(cfg.db_path, [score('old'), score('unmapped')])
    good = evidence(('old', 'Synthetic.png'), ('empty', ''))
    report = compat.preflight(cfg.db_path, good)
    assert report['unmapped']['image'] == ['unmapped']
    assert report['ignored_empty_names'] == 1
    bad = good + evidence(('old', 'Different.png'))
    assert compat.preflight(cfg.db_path, bad)['contradictions']
    with pytest.raises(ValueError, match='Contradictory'):
        compat.preflight(cfg.db_path, bad, apply=True)
    with sqlite3.connect(cfg.db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM identity_aliases').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM image_scores').fetchone()[0] == 2


def test_transaction_rollback_after_partial_registration(tmp_path, monkeypatch):
    cfg = config_at(tmp_path)
    legacy_db(cfg.db_path, [score('old-a'), score('old-b')])
    original = ids.register
    def fail(conn, kind, canonical, key, policy, filename='', legacy_id=None, source=''):
        if legacy_id == 'old-b':
            raise RuntimeError('synthetic failure')
        return original(conn, kind, canonical, key, policy, filename, legacy_id, source)
    monkeypatch.setattr(ids, 'register', fail)
    with pytest.raises(RuntimeError, match='synthetic failure'):
        compat.preflight(cfg.db_path, evidence(('old-a', 'A.png'), ('old-b', 'B.png')), apply=True)
    with sqlite3.connect(cfg.db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM identity_aliases').fetchone()[0] == 0


def test_v2_snapshot_full_history_roundtrip_and_v1_preflight(tmp_path):
    cfg = config_at(tmp_path)
    name = 'Synthetic_20880101-12345_9001.png'
    legacy_db(cfg.db_path, [score('old')])
    compat.preflight(cfg.db_path, evidence(('old', name)), apply=True)
    out = tmp_path / 'snapshot.parquet'
    review.write_classification_parquet(cfg.db_path, out)
    target = tmp_path / 'restored.sqlite'
    assert review.import_classification_parquet(target, out)['imported']
    for path in (cfg.db_path, target):
        with review.connect_db(path) as conn:
            ids.refresh(conn)
            assert conn.execute('SELECT image_id FROM image_scores').fetchone()[0] == 'old'
            assert conn.execute('SELECT image_id FROM effective_image_scores').fetchone()[0] == ids.thumbnail(name)[0]
    frame = pd.read_parquet(out)
    old = frame[frame.record_type.isin(['image_score', 'object_review', 'review_event'])].copy()
    old['snapshot_version'] = 1
    v1 = tmp_path / 'v1.parquet'; old.to_parquet(v1)
    v1db = tmp_path / 'v1.sqlite'
    assert review.import_classification_parquet(v1db, v1)['imported']
    with review.connect_db(v1db) as conn:
        with pytest.raises(ValueError, match='preflight'):
            ids.require_policy(conn, ids.Policy())
    assert compat.preflight(v1db, evidence(('old', name)), apply=True)['effective_images'] == 1
    invalid = frame.copy(); invalid.loc[invalid.index[0], 'record_type'] = 'unknown'
    bad = tmp_path / 'bad.parquet'; invalid.to_parquet(bad)
    with sqlite3.connect(target) as conn:
        before = compat.fingerprint(conn)
    with pytest.raises(ValueError):
        review.import_classification_parquet(target, bad, force=True)
    with sqlite3.connect(target) as conn:
        assert compat.fingerprint(conn) == before


def test_duplicate_sources_logical_counts_and_export(tmp_path):
    cfg = config_at(tmp_path, names=('a/Synthetic.png', 'b/Synthetic.png'))
    report = review.index_thumbnails(cfg)
    assert (report['records'], report['review_products'], report['duplicates_suppressed']) == (2, 1, 1)
    with review.connect_review_db(cfg) as conn:
        summary = review.query_summary(conn, cfg.reviewer)
        assert (summary['images'], summary['source_images']) == (1, 2)
        row = conn.execute('SELECT * FROM images').fetchone()
        assert row['relative_path'] == 'a/Synthetic.png'
        review.upsert_image_score(conn, cfg, dict(image_id=row['image_id'], score=4)); conn.commit()
    output = review.export_reviews(cfg)
    with Path(output['image_labels_csv']).open() as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1 and rows[0]['score'] == '4'


def test_policy_changes_require_explicit_preflight_and_saved_settings(tmp_path):
    cfg = config_at(tmp_path)
    review.index_thumbnails(cfg)
    changed = replace(cfg, identity_namespace='chosen', index_db_path=tmp_path/'new.sqlite')
    with pytest.raises(ValueError, match='policy changed'):
        review.index_thumbnails(changed)
    policy = ids.Policy.from_config(changed)
    assert compat.preflight(cfg.db_path, [], policy)['policy_changed']
    with pytest.raises(ValueError, match='accept-policy-change'):
        compat.preflight(cfg.db_path, [], policy, apply=True)
    compat.preflight(cfg.db_path, [], policy, apply=True, accept_policy_change=True)
    review.index_thumbnails(changed)
    doc = settings.describe(changed)['document']
    assert doc['identity_namespace'] == 'chosen' and doc['identity_key'] == 'parsed-name'
    file = tmp_path / 'config.json'; file.write_text(json.dumps(doc))
    _, args = cli.parse_args(['--config', str(file)])
    assert cli.make_config(args).identity_namespace == 'chosen'


def test_legacy_manifest_and_object_reviews(tmp_path):
    cfg = config_at(tmp_path)
    old = manifest.identity(cfg.dataset, 'image', 'row-0')
    old_object = manifest.identity(cfg.dataset, 'object', 'Synthetic')
    legacy_db(cfg.db_path, [score(old)], objects=[(old_object, 'reader', 1, 'reviewed', 'tag', 'note', 's', '2088-01-01T00:00:00Z')])
    report = compat.preflight(cfg.db_path, compat.read_evidence(manifests=[cfg.manifest_path], legacy_dataset=cfg.dataset), apply=True)
    assert report['unmapped'] == {'image': [], 'object': []}
    review.index_thumbnails(cfg)
    with review.connect_review_db(cfg) as conn:
        assert conn.execute('SELECT object_id FROM effective_object_reviews').fetchone()[0] == ids.object_identity(cfg.dataset, 'Synthetic')[0]
        assert review.query_summary(conn, cfg.reviewer)['active_objects'] == 1


def test_clear_canonical_does_not_resurrect_legacy(tmp_path):
    cfg = config_at(tmp_path)
    name = 'Synthetic_20880101-12345_9001.png'
    legacy_db(cfg.db_path, [score('old', date='2000-01-01T00:00:00Z')])
    compat.preflight(cfg.db_path, evidence(('old', name)), apply=True)
    review.index_thumbnails(cfg)
    with review.connect_review_db(cfg) as conn:
        review.clear_image_score(conn, cfg, {'image_id': ids.thumbnail(name)[0]})
        conn.commit()
    with review.connect_review_db(cfg) as conn:
        assert conn.execute('SELECT COUNT(*) FROM effective_image_scores').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM image_scores').fetchone()[0] == 2


def test_aa_class_optional_and_explicit_class_required(tmp_path):
    root = tmp_path / 'aa'; root.mkdir()
    (root / 'Synthetic_20880101-12345_9001.png').write_bytes(png_bytes())
    file = tmp_path / 'objects.csv'; file.write_text('object_id\nSynthetic\n')
    selection = review.read_active_asteroids_object_list(file, root=root)
    assert selection.queue_objects[0].class_name == ''
    cfg = config_at(tmp_path / 'config', layout='active-asteroids', root=root, queue_objects=selection.queue_objects, products=('active_asteroids',))
    review.index_thumbnails(cfg)
    with pytest.raises(ValueError, match='explicitly supplied'):
        review.index_thumbnails(replace(cfg, identity_include_class=True))


def test_directory_saved_configuration_and_cli_overrides(tmp_path, monkeypatch):
    cfg = config_at(tmp_path, layout='rcc', identity_namespace='explicit-scope', identity_include_class=True)
    document = settings.describe(cfg)['document']
    path = tmp_path / 'directory-settings.json'
    path.write_text(json.dumps(document))
    captured = []
    monkeypatch.setattr(review, 'serve', lambda config, **kwargs: captured.append(config))
    cli.main(['--config', str(path), '--identity-namespace', 'override'])
    assert captured[0].identity_namespace == 'override'
    assert captured[0].identity_include_class is True
    assert captured[0].root == cfg.root


def test_name_and_policy_changes_do_not_merge_explicit_manifest_ids(tmp_path):
    cfg = config_at(tmp_path, names=('a/Synthetic.png', 'b/Synthetic.png'), identity_key='manifest-id')
    records = manifest.records(cfg)
    assert len({r['image_id'] for r in records}) == 2
    review.index_thumbnails(cfg)
    with review.connect_review_db(cfg) as conn:
        assert review.query_summary(conn, cfg.reviewer)['images'] == 2


def test_readonly_preflight_reports_conflicts(tmp_path):
    cfg = config_at(tmp_path)
    legacy_db(cfg.db_path, [score('old-a', 1), score('old-b', 2)])
    before = cfg.db_path.read_bytes()
    report = compat.preflight(cfg.db_path, evidence(('old-a', 'Synthetic.png'), ('old-b', 'Synthetic.png')))
    assert len(report['conflicts']) == 1
    assert cfg.db_path.read_bytes() == before


def test_alias_score_write_uses_canonical_id(tmp_path):
    cfg = config_at(tmp_path)
    name = 'Synthetic_20880101-12345_9001.png'
    legacy_db(cfg.db_path, [score('old', date='2000-01-01T00:00:00Z')])
    compat.preflight(cfg.db_path, evidence(('old', name)), apply=True)
    review.index_thumbnails(cfg)
    with review.connect_review_db(cfg) as conn:
        result = review.upsert_image_score(conn, cfg, {'image_id': 'old', 'score': 9})
        assert result['image_id'] == ids.thumbnail(name)[0]
        assert conn.execute("SELECT score FROM image_scores WHERE image_id='old'").fetchone()[0] == 2
        assert conn.execute('SELECT score FROM effective_image_scores').fetchone()[0] == 9


def test_directory_unknown_annotations_remain_distinct(tmp_path):
    cfg = config_at(tmp_path, layout='rcc', products=('product',), dataset='')
    for name in ('Unknown__r.png', 'Unknown__g.png'):
        path = cfg.root / 'product' / 'Class' / 'Object' / name
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(png_bytes())
    review.index_thumbnails(cfg)
    with review.connect_review_db(cfg) as conn:
        assert review.query_summary(conn, cfg.reviewer)['images'] == 2


def test_rcc_compatibility_launcher_accepts_manifest_policy(tmp_path, monkeypatch):
    cfg = config_at(tmp_path)
    captured = []
    monkeypatch.setattr(review, 'serve', lambda config, **kwargs: captured.append(config))
    review.main(['--root', str(cfg.root), '--manifest', str(cfg.manifest_path),
                 '--identity-key', 'manifest-id', '--identity-namespace', 'chosen',
                 '--identity-include-class', '--db', str(cfg.db_path)])
    assert captured[0].identity_key == 'manifest-id'
    assert captured[0].identity_namespace == 'chosen'
    assert captured[0].identity_include_class


@pytest.mark.parametrize('override,field,value', [('identity_include_class', 'object_class', 'Class-B'), ('identity_key', 'image_id', 'new-row')])
def test_changed_explicit_identity_reuses_path_without_rekeying_labels(tmp_path, override, field, value):
    cfg = config_at(tmp_path, **{override: True if override == 'identity_include_class' else 'manifest-id'})
    review.index_thumbnails(cfg)
    old_id = manifest.records(cfg)[0]['image_id']
    with review.connect_review_db(cfg) as conn:
        review.upsert_image_score(conn, cfg, dict(image_id=old_id, score=4)); conn.commit()
    with cfg.manifest_path.open() as handle:
        reader = csv.DictReader(handle); fields = reader.fieldnames; rows = list(reader)
    rows[0][field] = value
    with cfg.manifest_path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    new_id = manifest.records(cfg)[0]['image_id']
    assert new_id != old_id
    review.index_thumbnails(cfg)
    with review.connect_review_db(cfg) as conn:
        assert conn.execute('SELECT image_id FROM images WHERE missing=0').fetchone()[0] == new_id
        assert conn.execute('SELECT image_id FROM image_scores').fetchone()[0] == old_id
        assert review.query_summary(conn, cfg.reviewer)['scored'] == 0


def test_launch_reuses_validated_manifest_but_rescan_rechecks_files(tmp_path, monkeypatch):
    cfg = config_at(tmp_path)
    calls = []
    original = review.read_png_size
    def count(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(review, 'read_png_size', count)
    _, args = cli.parse_args(['--root', str(cfg.root), '--manifest', str(cfg.manifest_path)])
    prepared = {}
    cfg = cli.make_config(args, prepared=prepared)
    assert len(calls) == 1
    review.index_thumbnails(cfg, manifest_rows=prepared['rows'])
    server = review.build_server(cfg, '127.0.0.1', 0, manifest_rows=prepared['rows'])
    server.server_close()
    assert len(calls) == 1
    review.index_thumbnails(cfg)
    assert len(calls) == 2
