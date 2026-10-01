"""Settings changes exercise real HTTP requests and synthetic classification stores."""
import json
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from astro_stampede import cli, review, settings
from astro_stampede.demo import generate


@pytest.fixture
def session(tmp_path):
    root = tmp_path / 'images'
    generate(root)
    _, args = cli.parse_args(['--root', str(root), '--manifest', str(root / 'manifest.csv'),
                             '--dataset', 'synthetic', '--state-dir', str(tmp_path / 'state')])
    config = cli.make_config(args)
    review.index_thumbnails(config)
    server = review.build_server(config, '127.0.0.1', 0, quiet=True)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    yield server
    server.shutdown()
    thread.join()
    server.server_close()


def post(server, path, payload=None, revision=0, origin=None):
    headers = {'Content-Type': 'application/json', 'X-Settings-Revision': str(revision)}
    if origin:
        headers['Origin'] = origin
    request = Request(f'http://127.0.0.1:{server.server_port}/api/{path}',
                      data=json.dumps(payload or {}).encode(), headers=headers)
    with urlopen(request) as response:
        return json.load(response)


def test_settings_apply_preserves_labels_and_rejects_stale_writes(session, tmp_path):
    old = session.config
    with review.connect_review_db(old) as conn:
        obj = review.query_objects(conn, old.reviewer, {})[0]
        image = review.query_object_images(conn, obj['object_id'], old.reviewer)[0]
        score = {'image_id': image['image_id'], 'score': 8}
        review.upsert_image_score(conn, old, score)
        conn.commit()
    values = post(session, 'settings')['values']
    for key, name in [('db', 'labels.sqlite'), ('index_db', 'index.sqlite'),
                      ('classification_parquet', 'classifications.parquet'), ('export_dir', 'exports')]:
        values[key] = str(tmp_path / 'new-output' / name)
    assert post(session, 'settings/apply', values)['applied']
    assert old.classification_parquet_path.exists()
    with review.connect_review_db(old) as conn:
        assert conn.execute('SELECT score FROM labels.image_scores').fetchone()[0] == 8
    with review.connect_review_db(session.config) as conn:
        assert conn.execute('SELECT COUNT(*) FROM labels.image_scores').fetchone()[0] == 0
    with pytest.raises(HTTPError) as exc:
        post(session, 'image-score', score)
    assert exc.value.code == 409
    post(session, 'image-score', score, revision=1)
    document = post(session, 'settings', revision=1)['document']
    config_path = tmp_path / 'saved.json'
    config_path.write_text(json.dumps(document))
    _, args = cli.parse_args(['--config', str(config_path)])
    restored = cli.make_config(args)
    assert restored.db_path == session.config.db_path
    assert restored.root == session.config.root
    assert restored.import_snapshot is False


def test_invalid_paths_leave_current_session_usable(session, tmp_path):
    old = session.config
    values = settings.describe(old)['values']
    values['manifest'] = str(tmp_path / 'missing.csv')
    with pytest.raises(HTTPError):
        post(session, 'settings/apply', values)
    assert session.config is old
    assert session.settings_revision == 0
    assert post(session, 'export')
    values = settings.describe(old)['values']
    values['dataset'] = 'different'
    with pytest.raises(HTTPError) as exc:
        post(session, 'settings/apply', values)
    assert 'new index filename' in exc.value.read().decode()
    assert session.config is old


def test_browse_and_origin_protection(session):
    root = session.config.root
    result = post(session, 'settings/browse', {'path': str(root)})
    assert result['path'] == str(root)
    assert any(e['name'] == 'manifest.csv' for e in result['entries'])
    with pytest.raises(HTTPError) as exc:
        post(session, 'settings', origin='https://example.com')
    assert exc.value.code == 403


def test_settings_reject_overlapping_storage(session):
    values = settings.describe(session.config)['values']
    values['classification_parquet'] = values['db']
    with pytest.raises(settings.SettingsError):
        settings.candidate(session.config, values)
    values = settings.describe(session.config)['values']
    values['db'] = values['db'].replace('labels.sqlite', 'other.sqlite')
    with pytest.raises(settings.SettingsError, match='separate snapshot'):
        settings.candidate(session.config, values)


def test_switch_image_folder_with_new_index(session, tmp_path):
    root = tmp_path / 'second-images'
    generate(root)
    values = settings.describe(session.config)['values']
    values.update(root=str(root), manifest=str(root / 'manifest.csv'),
                  index_db=str(tmp_path / 'second-index.sqlite'))
    post(session, 'settings/apply', values)
    assert session.config.root == root
    with review.connect_review_db(session.config) as conn:
        assert review.query_summary(conn, session.config.reviewer, 'manifest')['images'] == 6


def test_unavailable_output_keeps_active_configuration(session, tmp_path):
    old = session.config
    blocked = tmp_path / 'not-a-directory'
    blocked.write_text('synthetic obstruction')
    values = settings.describe(old)['values']
    values['export_dir'] = str(blocked / 'exports')
    with pytest.raises(HTTPError):
        post(session, 'settings/apply', values)
    assert session.config is old
    assert session.settings_revision == 0


def test_convenient_launcher_defaults(tmp_path, monkeypatch):
    root = tmp_path / 'synthetic-input'
    generate(root)
    monkeypatch.chdir(root)
    _, args = cli.parse_args([])
    config = cli.make_config(args)
    assert config.root == root
    assert config.manifest_path == root / 'manifest.csv'
    assert config.dataset == root.name
    assert config.db_path == root / 'state' / 'labels.sqlite'
    assert config.export_dir == root / 'state' / 'exports'
    assert not (root / 'state').exists()


def test_suggested_defaults_preserve_namespace_and_separate_indexes(session, tmp_path):
    old = session.config
    suggested = post(session, 'settings/defaults', {'root': str(tmp_path / 'new-images'), 'dataset': old.dataset})
    assert suggested['dataset'] == old.dataset
    assert suggested['manifest'] == str(tmp_path / 'new-images' / 'manifest.csv')
    assert suggested['db'] == str(tmp_path / 'new-images' / 'state' / 'labels.sqlite')
    first = post(session, 'settings/defaults', {'root': str(old.root), 'dataset': old.dataset, 'output': str(old.db_path.parent)})
    second = post(session, 'settings/defaults', {'root': str(tmp_path / 'new-images'), 'dataset': old.dataset, 'output': str(old.db_path.parent)})
    assert first['db'] == second['db']
    assert first['index_db'] != second['index_db']
    assert session.config is old
    assert not (tmp_path / 'new-images').exists()
