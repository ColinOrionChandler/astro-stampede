"""Validated generic inputs. Inventories are always supplied at runtime."""
from __future__ import annotations
import csv
import hashlib
import json
import math
import stat
from pathlib import Path, PurePosixPath, PureWindowsPath
from .adapters.comparisons import ComparisonCatalog
from .trails import validate_metadata
from . import identities
from .validation import ValidationError

REQUIRED = {'image_id', 'object_id', 'relative_path'}
OPTIONAL = {'order', 'time', 'filter', 'comparison_id', 'role', 'metadata',
            'model_score_r3', 'model_score_operational', 'object_class'}


def identity(dataset: str, kind: str, value: str) -> str:
    """Length-unambiguous namespacing; independent of image filename and location."""
    return hashlib.sha256(json.dumps([dataset, kind, value], ensure_ascii=True).encode()).hexdigest()


def safe_path(root: Path, value: str) -> Path:
    relative = PurePosixPath(value)
    if (not value or relative.is_absolute() or PureWindowsPath(value).drive
            or '\\' in value or '..' in relative.parts or not value.lower().endswith(('.png', '.png.gz'))):
        raise ValueError('Image paths must be relative PNG paths without parent traversal.')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('Image resolves outside the dataset root.')
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('Image is not a regular file.')
    return path


def read(config, *, progress=False):
    from .review import read_png_size
    problems = []
    if not config.dataset.strip():
        problems.append('dataset: A nonempty dataset namespace is required.')
    try:
        with config.manifest_path.open(newline='', encoding='utf-8-sig') as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            header_problems = []
            for label, values in (
                ('missing columns', REQUIRED - set(columns)),
                ('unknown columns', set(columns) - REQUIRED - OPTIONAL),
                ('duplicate columns', {c for c in columns if columns.count(c) > 1}),
            ):
                if values:
                    header_problems.append(f'Manifest {config.manifest_path}: {label}: {", ".join(sorted(values))}.')
            if header_problems:
                raise ValidationError(problems + header_problems)
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValidationError(problems + [f'Read manifest {config.manifest_path}: {exc}']) from None
    if not rows:
        problems.append('Manifest contains no images.')
    if progress:
        print(f"[astro-stampede] Checking {len(rows):,} manifest rows and PNG headers...", flush=True)
    ids, paths = {}, {}
    valid_rows = []
    for number, row in enumerate(rows, 2):
        if progress and (number - 1) % 5000 == 0:
            print(f"[astro-stampede] Checked {number - 1:,}/{len(rows):,} manifest rows", flush=True)
        def issue(field, message):
            problems.append(f'Invalid manifest row {number}, {field}: {message}')
        if None in row or any(value is None for value in row.values()):
            issue('columns', 'Row has the wrong number of columns.')
            continue
        valid_rows.append((number, row))
        for field in ('image_id', 'object_id'):
            if not row[field].strip():
                issue(field, 'ID cannot be empty.')
        if row['image_id'].strip():
            if row['image_id'] in ids:
                issue('image_id', f'Duplicate image ID {row["image_id"]!r}; first appears in row {ids[row["image_id"]]}.')
            else:
                ids[row['image_id']] = number
        try:
            path = row['_path'] = safe_path(config.root, row['relative_path'])
            if path in paths:
                issue('relative_path', f'Duplicate image path {row["relative_path"]!r}; first appears in row {paths[path]}.')
            else:
                paths[path] = number
            row['_dimensions'] = read_png_size(path)
            row['_stat'] = path.stat()
        except (ValueError, OSError) as exc:
            issue('relative_path', f'Read PNG {row["relative_path"]!r}: {exc}')
        if row.get('role', '') not in {'', 'primary', 'comparison'}:
            issue('role', f'{row["role"]!r}: Role must be primary or comparison.')
        try:
            row['_order'] = float(row.get('order') or number)
            if not math.isfinite(row['_order']):
                raise ValueError()
        except ValueError:
            issue('order', f'{row.get("order")!r}: Order must be a finite number.')
        try:
            row['_metadata'] = json.loads(row.get('metadata') or '{}')
            if not isinstance(row['_metadata'], dict):
                raise ValueError('Metadata must be a JSON object.')
            validate_metadata(row['_metadata'])
        except (ValueError, TypeError) as exc:
            issue('metadata', str(exc))
        score_fields = ('model_score_r3', 'model_score_operational')
        if any(row.get(k) for k in score_fields):
            for field in score_fields:
                try:
                    value = float(row.get(field) or '')
                    if not math.isfinite(value) or not 0 <= value <= 1:
                        raise ValueError()
                except ValueError:
                    issue(field, 'Reveal scores require two finite probabilities in [0, 1].')
    by_id = {}
    for number, row in valid_rows:
        by_id.setdefault(row['image_id'], []).append(row)
    for number, row in valid_rows:
        comparison = row.get('comparison_id')
        if comparison:
            targets = by_id.get(comparison, [])
            if (not targets and len(valid_rows) == len(rows)) or (len(targets) == 1 and targets[0].get('role', '') in {'', 'primary'}):
                problems.append(f'Invalid manifest row {number}, comparison_id: {comparison!r} must reference an explicit comparison row.')
    if rows and len(valid_rows) == len(rows) and all(r.get('role') == 'comparison' for _, r in valid_rows):
        problems.append('Manifest contains no primary images.')
    if problems:
        raise ValidationError(problems)
    return sorted(rows, key=lambda r: (r['object_id'], r['_order'], r.get('time', ''), r['image_id']))


def records(config, *, rows=None):
    from .review import read_png_size
    result = []
    for position, row in enumerate(read(config) if rows is None else rows):
        if row.get('role') == 'comparison':
            continue
        path = row['_path']; stat = row['_stat']; width, height = row['_dimensions']
        iid, normalized = identities.thumbnail(path.name, identities.Policy.from_config(config), manifest_id=row['image_id'], object_class=row.get('object_class'))
        result.append(dict(
            image_id=iid, product_image_id=iid, legacy_image_id=identity(config.dataset, 'image', row['image_id']),
            manifest_id=row['image_id'], explicit_class=row.get('object_class'),
            object_id=identities.object_identity(config.dataset, row['object_id'])[0],
            relative_path=row['relative_path'], source_path=None,
            product=config.dataset, class_name=row.get('object_class') or 'generic', object_name=row['object_id'],
            filename=path.name, identity=normalized, pair_key=iid,
            timestamp=row.get('time') or None, order_key=row['_order'], instrument='', visit='', detector='',
            annotation=json.dumps(row['_metadata'], ensure_ascii=True) if row['_metadata'] else '',
            filter_token=row.get('filter', ''), delta_mag_token=None, q_token=None, tisserand_token=None,
            width=width, height=height, file_size=stat.st_size, mtime=stat.st_mtime,
        ))
    return result


def comparisons(config, *, rows=None):
    rows = read(config) if rows is None else rows; by_id = {r['image_id']: r for r in rows}
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


def reveal_scores(config, *, rows=None):
    from .review import RevealScore
    return tuple(RevealScore(identities.thumbnail(r['_path'].name, identities.Policy.from_config(config), manifest_id=r['image_id'], object_class=r.get('object_class'))[0],
                            float(r['model_score_r3']), float(r['model_score_operational']))
                 for r in (read(config) if rows is None else rows) if r.get('role') != 'comparison' and r.get('model_score_r3'))
