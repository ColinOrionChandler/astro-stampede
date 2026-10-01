"""Validated generic inputs. Inventories are always supplied at runtime."""
from __future__ import annotations
import csv
import hashlib
import json
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
from .adapters.comparisons import ComparisonCatalog
from .trails import validate_metadata

REQUIRED = {'image_id', 'object_id', 'relative_path'}
OPTIONAL = {'order', 'time', 'filter', 'comparison_id', 'role', 'metadata',
            'model_score_r3', 'model_score_operational'}


def identity(dataset: str, kind: str, value: str) -> str:
    """Length-unambiguous namespacing; independent of image filename and location."""
    return hashlib.sha256(json.dumps([dataset, kind, value], ensure_ascii=True).encode()).hexdigest()


def safe_path(root: Path, value: str) -> Path:
    relative = PurePosixPath(value)
    if (not value or relative.is_absolute() or PureWindowsPath(value).drive
            or '\\' in value or '..' in relative.parts or relative.suffix.lower() != '.png'):
        raise ValueError('Image paths must be relative PNG paths without parent traversal.')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Manifest image is missing or outside the dataset root.')
    return path


def read(config):
    if not config.dataset.strip():
        raise ValueError('A nonempty dataset namespace is required.')
    with config.manifest_path.open(newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        if (not REQUIRED <= set(columns) or set(columns) - REQUIRED - OPTIONAL
                or len(set(columns)) != len(columns)):
            raise ValueError('Manifest requires unique image_id, object_id, relative_path columns; unknown columns are rejected.')
        rows = list(reader)
    if not rows:
        raise ValueError('Manifest contains no images.')
    ids, paths = set(), set()
    for number, row in enumerate(rows, 2):
        try:
            if None in row or any(value is None for value in row.values()):
                raise ValueError('Row has the wrong number of columns.')
            if not row['image_id'].strip() or not row['object_id'].strip():
                raise ValueError('Image and object IDs cannot be empty.')
            if row['image_id'] in ids:
                raise ValueError('Duplicate image ID.')
            row['_path'] = safe_path(config.root, row['relative_path'])
            if row['_path'] in paths:
                raise ValueError('Duplicate image path.')
            ids.add(row['image_id']); paths.add(row['_path'])
            if row.get('role', '') not in {'', 'primary', 'comparison'}:
                raise ValueError('Role must be primary or comparison.')
            row['_order'] = float(row.get('order') or number)
            if not math.isfinite(row['_order']):
                raise ValueError('Order must be finite.')
            row['_metadata'] = json.loads(row.get('metadata') or '{}')
            if not isinstance(row['_metadata'], dict):
                raise ValueError('Metadata must be a JSON object.')
            validate_metadata(row['_metadata'])
            probabilities = [row.get(k, '') for k in ('model_score_r3', 'model_score_operational')]
            if any(probabilities):
                if not all(probabilities) or not all(math.isfinite(float(v)) and 0 <= float(v) <= 1 for v in probabilities):
                    raise ValueError('Reveal scores require two finite probabilities in [0, 1].')
        except (ValueError, TypeError, OSError) as exc:
            raise ValueError(f'Invalid manifest row {number}: {exc}') from None
    by_id = {r['image_id']: r for r in rows}
    for row in rows:
        comparison = row.get('comparison_id')
        if comparison and (comparison not in by_id or by_id[comparison].get('role') != 'comparison'):
            raise ValueError('comparison_id must reference an explicit comparison row.')
    if not any(r.get('role') != 'comparison' for r in rows):
        raise ValueError('Manifest contains no primary images.')
    return sorted(rows, key=lambda r: (r['object_id'], r['_order'], r.get('time', ''), r['image_id']))


def records(config):
    from .review import read_png_size
    result = []
    for position, row in enumerate(read(config)):
        if row.get('role') == 'comparison':
            continue
        path = row['_path']; stat = path.stat(); width, height = read_png_size(path)
        iid = identity(config.dataset, 'image', row['image_id'])
        result.append(dict(
            image_id=iid, product_image_id=iid, legacy_image_id=iid,
            object_id=identity(config.dataset, 'object', row['object_id']),
            relative_path=row['relative_path'], source_path=None,
            product=config.dataset, class_name='generic', object_name=row['object_id'],
            filename=path.name, identity=row['image_id'], pair_key=iid,
            timestamp=row.get('time') or None, order_key=row['_order'], instrument='', visit='', detector='',
            annotation=json.dumps(row['_metadata'], ensure_ascii=True) if row['_metadata'] else '',
            filter_token=row.get('filter', ''), delta_mag_token=None, q_token=None, tisserand_token=None,
            width=width, height=height, file_size=stat.st_size, mtime=stat.st_mtime,
        ))
    return result


def comparisons(config):
    rows = read(config); by_id = {r['image_id']: r for r in rows}
    catalog = ComparisonCatalog(config.root)
    for row in rows:
        if row.get('comparison_id'):
            other = by_id[row['comparison_id']]
            key = identity(config.dataset, 'comparison', other['image_id'])
            catalog.paths[key] = other['_path']
            catalog.images[row['relative_path']] = dict(status='available', url=f'/api/comparisons/{key}',
                expected_trail_pixels=other['_metadata'].get('expected_trail_pixels'),
                filename=other['_path'].name, band=other.get('filter', ''), visit='', datetime=other.get('time', ''))
    return catalog if catalog.paths else None


def reveal_scores(config):
    from .review import RevealScore
    return tuple(RevealScore(identity(config.dataset, 'image', r['image_id']),
                            float(r['model_score_r3']), float(r['model_score_operational']))
                 for r in read(config) if r.get('role') != 'comparison' and r.get('model_score_r3'))
