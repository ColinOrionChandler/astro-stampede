"""Local settings and filesystem selection for the review UI."""
from dataclasses import replace
from pathlib import Path
import json
import sqlite3
from tempfile import TemporaryFile

from . import manifest, review, defaults


class SettingsError(ValueError):
    pass


PATHS = {'root': 'root', 'manifest': 'manifest_path', 'db': 'db_path',
         'index_db': 'index_db_path', 'classification_parquet': 'classification_parquet_path',
         'export_dir': 'export_dir'}


def describe(config):
    values = {key: str(getattr(config, attr) or '') for key, attr in PATHS.items()}
    values['classification_parquet'] = str(review.classification_parquet_path(config))
    values['dataset'] = config.dataset
    document = None
    if config.layout == 'manifest':
        document = dict(values, state_dir=str(config.db_path.parent), reviewer=config.reviewer,
                        title=config.title, score_min=config.score_min, score_max=config.score_max,
                        hide_metadata=not config.show_metadata)
        if config.tags is not None:
            document['tags'] = list(config.tags)
    return {'output': str(config.db_path.parent), 'values': values, 'source_editable': config.layout == 'manifest', 'document': document}


def suggestions(payload):
    root = payload.get('root')
    if not isinstance(root, str) or not root.strip() or not Path(root).expanduser().is_absolute():
        raise SettingsError('Choose an absolute image folder first.')
    return defaults.paths(root, payload.get('dataset'), payload.get('output'))


def browse(payload):
    path = Path(payload.get('path') or Path.home()).expanduser()
    if not path.is_absolute():
        raise SettingsError('Enter an absolute folder path or a path beginning with ~.')
    path = path.resolve()
    if path.is_file():
        path = path.parent
    if not path.is_dir():
        raise SettingsError('Folder does not exist or is unavailable.')
    entries = []
    for child in path.iterdir():
        if child.name.startswith('.'):
            continue
        if child.is_dir() or child.suffix.lower() in {'.csv', '.sqlite', '.db', '.parquet'}:
            entries.append({'name': child.name, 'path': str(child), 'directory': child.is_dir()})
    entries.sort(key=lambda item: (not item['directory'], item['name'].casefold()))
    return {'path': str(path), 'parent': str(path.parent), 'entries': entries}


def candidate(config, payload):
    if not isinstance(payload, dict) or set(payload) != set(PATHS) | {'dataset'}:
        raise SettingsError('Settings fields are missing or unsupported.')
    current = describe(config)['values']
    if config.layout != 'manifest' and any(payload[k] != current[k] for k in ('root', 'manifest', 'dataset')):
        raise SettingsError('Compatibility input selections must be changed in the launcher.')
    changes = {}
    for key, attr in PATHS.items():
        value = payload[key]
        if not isinstance(value, str) or (not value.strip() and key != 'manifest'):
            raise SettingsError('Fill in all required paths.')
        if not value.strip():
            changes[attr] = None
            continue
        path = Path(value.strip()).expanduser()
        if not path.is_absolute():
            raise SettingsError('Use absolute paths or paths beginning with ~.')
        changes[attr] = path.resolve()
    dataset = payload['dataset']
    if not isinstance(dataset, str) or (config.layout == 'manifest' and not dataset.strip()):
        raise SettingsError('Enter a stable dataset name.')
    new = replace(config, **changes, dataset=dataset.strip(), import_snapshot=False)
    files = [new.db_path, new.index_db_path, new.classification_parquet_path]
    if len(set(files)) != len(files) or new.manifest_path in files:
        raise SettingsError('Database, index, snapshot and manifest paths must be different.')
    if new.db_path == config.index_db_path or new.index_db_path == config.db_path:
        raise SettingsError('Do not exchange the label database and index paths.')
    for path, suffixes in zip(files, ({'.sqlite', '.db'}, {'.sqlite', '.db'}, {'.parquet'})):
        if path.suffix.lower() not in suffixes or path.is_dir():
            raise SettingsError('Use .sqlite or .db for databases and .parquet for the snapshot.')
    if new.export_dir.exists() and not new.export_dir.is_dir():
        raise SettingsError('Export destination must be a directory.')
    if new.db_path != config.db_path and new.classification_parquet_path == review.classification_parquet_path(config):
        raise SettingsError('Choose a separate snapshot when switching label databases.')
    if new.layout == 'manifest':
        if new.manifest_path is None:
            raise SettingsError('Select a manifest CSV.')
        manifest.records(new)
        new = replace(new, products=(new.dataset,), reveal_scores=manifest.reveal_scores(new))
    if new.index_db_path.exists():
        with sqlite3.connect(f'{new.index_db_path.as_uri()}?mode=ro', uri=True) as conn:
            row = conn.execute("SELECT value FROM index_metadata WHERE key='binding'").fetchone()
            binding = json.dumps([new.layout, new.dataset, str(new.root)])
            if not row or row[0] != binding:
                raise SettingsError('This index belongs to another dataset or folder. Choose a new index filename.')
    return new


def apply(server, payload):
    old = server.config
    new = candidate(old, payload)
    comparisons = (manifest.comparisons(new) if new.layout == 'manifest' else server.comparisons)
    result = review.write_classification_parquet(old.db_path, review.classification_parquet_path(old))
    if not result.get('written'):
        raise SettingsError('Could not save the current snapshot. Settings were not applied.')
    # Check every destination before activating it, including deferred exports.
    for directory in {new.db_path.parent, new.index_db_path.parent,
                      new.classification_parquet_path.parent, new.export_dir}:
        directory.mkdir(parents=True, exist_ok=True)
        with TemporaryFile(dir=directory):
            pass
    review.index_thumbnails(new)
    server.config = new
    server.comparisons = comparisons
    server.settings_revision += 1
    return {'applied': True}
