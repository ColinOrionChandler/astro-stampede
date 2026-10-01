import csv
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest
from astro_stampede import cli, manifest, review, trails
from astro_stampede.demo import generate, png_bytes
from tools.audit_public import check


@pytest.fixture
def config(tmp_path):
    root = tmp_path / 'synthetic'
    generate(root)
    _, args = cli.parse_args(['--root', str(root), '--manifest', str(root / 'manifest.csv'),
        '--dataset', 'test-universe', '--state-dir', str(tmp_path / 'state'), '--reviewer', 'synthetic-reviewer'])
    return cli.make_config(args)


def modify(config, callback):
    with config.manifest_path.open(newline='') as handle:
        reader = csv.DictReader(handle); fields = reader.fieldnames; rows = list(reader)
    callback(rows)
    with config.manifest_path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def test_persistence_restart_undo_and_export(config):
    hashes = {p: hashlib.sha256(p.read_bytes()).digest() for p in config.root.rglob('*.png')}
    assert review.index_thumbnails(config)['records'] == 6
    with review.connect_review_db(config) as conn:
        objects = review.query_objects(conn, config.reviewer, {})
        images = review.query_object_images(conn, objects[0]['object_id'], config.reviewer)
        iid = images[0]['image_id']
        review.upsert_image_score(conn, config, {'image_id': iid, 'score': 7, 'tags': 'trail', 'comment': 'synthetic note'})
        conn.commit()
    assert review.index_thumbnails(config)['unchanged'] == 6
    with review.connect_review_db(config) as conn:
        saved = conn.execute('SELECT * FROM labels.image_scores').fetchone()
        assert (saved['image_id'], saved['score'], saved['tags'], saved['comment']) == (iid, 7, 'trail', 'synthetic note')
        assert conn.execute('SELECT COUNT(*) FROM labels.review_events').fetchone()[0] == 1
    exports = review.export_reviews(config)
    assert 'source_path' not in Path(exports['image_labels_csv']).read_text()
    assert str(config.root) not in Path(exports['image_labels_csv']).read_text()
    fresh = replace(config, db_path=config.db_path.with_name('restored.sqlite'))
    assert review.prepare_classification_state(fresh)['imported'] is False
    assert not fresh.db_path.exists()
    assert review.prepare_classification_state(replace(fresh, import_snapshot=True))['imported']
    with review.connect_review_db(fresh) as conn:
        assert conn.execute('SELECT image_id FROM labels.image_scores').fetchone()[0] == iid
        assert conn.execute('SELECT COUNT(*) FROM labels.review_events').fetchone()[0] == 1
        review.undo_last_score(conn, fresh); conn.commit()
        assert conn.execute('SELECT COUNT(*) FROM labels.image_scores').fetchone()[0] == 0
    assert all(hashlib.sha256(p.read_bytes()).digest() == digest for p, digest in hashes.items())


def test_dataset_namespaces_and_stable_ids(config):
    original = manifest.records(config)
    other = manifest.records(replace(config, dataset='another-universe'))
    assert {r['image_id'] for r in original}.isdisjoint(r['image_id'] for r in other)
    assert {r['object_id'] for r in original}.isdisjoint(r['object_id'] for r in other)
    row = original[0]; path = config.root / row['relative_path']; new = path.with_name('renamed.png'); path.rename(new)
    modify(config, lambda rows: rows[0].update(relative_path=new.relative_to(config.root).as_posix()))
    assert manifest.records(config)[0]['image_id'] == row['image_id']


@pytest.mark.parametrize('change', ['duplicate', 'parent', 'absolute', 'missing', 'comparison', 'role', 'probability', 'nan'])
def test_manifest_validation_before_state_changes(config, change):
    def mutate(rows):
        if change == 'duplicate': rows[2]['image_id'] = rows[0]['image_id']
        if change == 'parent': rows[0]['relative_path'] = '../escape.png'
        if change == 'absolute': rows[0]['relative_path'] = str(config.root / rows[0]['relative_path'])
        if change == 'missing': rows[0]['relative_path'] = 'absent.png'
        if change == 'comparison': rows[0]['comparison_id'] = rows[2]['image_id']
        if change == 'role': rows[0]['role'] = 'unknown'
        if change == 'probability': rows[0]['model_score_r3'] = 'nan'
        if change == 'nan': rows[0]['order'] = 'nan'
    modify(config, mutate)
    with pytest.raises(ValueError): review.index_thumbnails(config)
    assert not config.db_path.exists()
    assert not config.index_db_path.exists()


def test_symlink_escape(config, tmp_path):
    path = tmp_path / 'outside.png'; path.write_bytes(png_bytes())
    (config.root / 'linked.png').symlink_to(path)
    modify(config, lambda rows: rows[0].update(relative_path='linked.png'))
    with pytest.raises(ValueError): manifest.records(config)


def test_index_cannot_mix_datasets(config):
    review.index_thumbnails(config)
    with pytest.raises(ValueError, match='different dataset'):
        review.index_thumbnails(replace(config, dataset='another', products=('another',)))


def test_rcc_identity_compatibility():
    first = 'Synthetic_2088-01-01T00-00-00_Camera_9001_4+3.png'
    second = 'Synthetic_2088-01-02T00-00-00.123_Camera_9001_3+4__r.png'
    args = ('synthetic-product', 'synthetic-class', 'Synthetic')
    expected = review.stable_id('/'.join((*args, 'Camera', '9001', '3+4', 'png')))
    assert review.product_image_id_for(*args, first, fallback_image_id='unused') == expected
    assert review.product_image_id_for(*args, second, fallback_image_id='unused') == expected


def test_local_config_relative_paths_and_override(config, tmp_path):
    file = tmp_path / 'settings.local.json'
    file.write_text(json.dumps({'root': 'synthetic', 'manifest': 'synthetic/manifest.csv', 'dataset': 'demo', 'state_dir': 'state', 'title': 'Configured', 'score_max': 5, 'tags': ['tail']}))
    _, args = cli.parse_args(['--config', str(file), '--title', 'Override'])
    result = cli.make_config(args)
    assert result.root == config.root
    assert result.title == 'Override' and result.score_max == 5 and result.tags == ('tail',)


def test_http_assets_comparisons_blind_reveal_and_persistence(config):
    review.index_thumbnails(config)
    server = review.build_server(config, '127.0.0.1', 0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    def request(path, payload=None, headers=None):
        data = None if payload is None else json.dumps(payload).encode()
        with urlopen(Request(base + path, data=data, headers=headers or {}), timeout=10) as response:
            return response.read()
    try:
        assert b'Astro Stampede' in request('/')
        assert b'configureLayout' in request('/static/app.js')
        for asset in ('/', '/static/app.js', '/static/styles.css', '/static/trails.js'):
            with urlopen(base + asset, timeout=10) as response:
                assert response.headers['Cache-Control'] == 'no-store'
        objects = json.loads(request('/api/objects'))
        oid = objects[0]['object_id']
        data = request(f'/api/objects/{oid}/images'); images = json.loads(data)
        assert len(images) == 6 and b'model_score_r3' not in data and str(config.root).encode() not in data
        assert images[0]['expected_trail_pixels'] == 24
        assert images[0]['comparison']['expected_trail_pixels'] == 8
        assert 'source_path' not in images[0]
        assert request('/api/images/' + images[0]['image_id']).startswith(review.PNG_SIGNATURE)
        assert request('/api/images/' + images[0]['image_id']) == (config.root / images[0]['relative_path']).read_bytes()
        assert b'trailBarGeometry' in request('/static/trails.js')
        comparison = images[0]['comparison']
        assert comparison['status'] == 'available'
        assert request(comparison['url']) != request('/api/images/' + images[0]['image_id'])
        assert b'model_score_r3' in request(f'/api/objects/{oid}/reveal')
        request('/api/image-score', {'image_id': images[0]['image_id'], 'score': 6})
        assert json.loads(request(f'/api/objects/{oid}/images'))[0]['score'] == 6
        with pytest.raises(HTTPError) as error:
            request('/api/image-score', {}, {'Origin': 'https://invalid.example'})
        assert error.value.code == 403
        assert str(config.root).encode() not in request('/api/export', {})
        request('/api/rescan', {})
        modify(config, lambda rows: rows[0].update(metadata=json.dumps({'expected_trail_pixels': 32.5})))
        request('/api/rescan', {})
        refreshed = json.loads(request(f'/api/objects/{oid}/images'))[0]
        assert refreshed['expected_trail_pixels'] == 32.5
        assert refreshed['score'] == 6 and refreshed['image_id'] == images[0]['image_id']
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_public_boundary_detects_data_and_private_values():
    assert check('inventory.csv', b'image_id')
    assert check('example.py', ('/' + 'Users' + '/synthetic/private').encode())
    assert check('example.py', b'private-marker', [b'private-marker'])
    assert not check('example.py', b'Path.home() / "local"')


def test_active_asteroids_identity_and_compressed_duplicate(tmp_path):
    import gzip
    root = tmp_path / 'synthetic-aa'; root.mkdir()
    name = 'SyntheticAA_20880101-12345_9001.png'
    (root / name).write_bytes(png_bytes())
    extra = tmp_path / 'extra'; extra.mkdir()
    (extra / (name + '.gz')).write_bytes(gzip.compress(png_bytes()))
    queue = review.ReviewQueueObject('active_asteroids', 'synthetic-class', 'SyntheticAA', 1, 1)
    records = review.iter_active_asteroids_png_records(root, (queue,), (extra,))
    assert len(records) == 1
    expected = review.image_id_for(queue.product, queue.class_name, queue.object_name, name[:-4])
    assert records[0]['image_id'] == expected
    assert records[0]['product_image_id'] == expected


def test_configured_scale_and_comparison_scoring_rejection(config):
    config = replace(config, score_min=2, score_max=5)
    review.index_thumbnails(config)
    primary = manifest.records(config)[0]['image_id']
    comparison = next(iter(manifest.comparisons(config).paths))
    with review.connect_review_db(config) as conn:
        with pytest.raises(ValueError, match='configured scale'):
            review.upsert_image_score(conn, config, {'image_id': primary, 'score': 9})
        with pytest.raises(ValueError, match='Only primary'):
            review.upsert_image_score(conn, config, {'image_id': comparison, 'score': 4})
        assert conn.execute('SELECT COUNT(*) FROM labels.image_scores').fetchone()[0] == 0


def test_legacy_cli_requires_explicit_destinations():
    with pytest.raises(SystemExit, match='explicit'):
        cli.main(['legacy'])


def test_label_store_cannot_be_used_as_index(config):
    with pytest.raises(ValueError, match='separate files'):
        with review.connect_review_db(replace(config, index_db_path=config.db_path)):
            pass
    assert not config.db_path.exists()


@pytest.mark.parametrize('value', [-1, True, '24', float('nan'), float('inf'), [], {}])
def test_invalid_trail_length_is_rejected_before_state_changes(config, value):
    modify(config, lambda rows: rows[0].update(metadata=json.dumps({'expected_trail_pixels': value})))
    with pytest.raises(ValueError, match='expected_trail_pixels'):
        review.index_thumbnails(config)
    assert not config.db_path.exists()
    assert not config.index_db_path.exists()


def test_legacy_trail_lengths_and_comparisons_are_independent():
    images = [dict(filename='synthetic_235dPix.png', pairs=[dict(relative_path='synthetic_8.5dPix.png')],
                   comparison=dict(filename='synthetic-comparison.png'))]
    trails.annotate(images, legacy=True)
    assert images[0]['expected_trail_pixels'] == 235
    assert images[0]['pairs'][0]['expected_trail_pixels'] == 8.5
    assert images[0]['comparison']['expected_trail_pixels'] is None
    for filename in ['synthetic.png', 'synthetic_-8dPix.png', 'synthetic_5dPix_other.png']:
        assert trails.annotate([dict(filename=filename)], legacy=True)[0]['expected_trail_pixels'] is None


def test_manifest_trail_lengths_are_explicit():
    images = [dict(filename='synthetic_235dPix.png', annotation='{}'),
              dict(annotation='{"expected_trail_pixels": 0}'),
              dict(annotation='{"expected_trail_pixels": 12.5}')]
    trails.annotate(images)
    assert [i['expected_trail_pixels'] for i in images] == [None, 0, 12.5]
