"""Synthetic compatibility fixtures for old and current comparison bundles."""
import csv

import pytest

from astro_stampede.adapters.comparisons import load_comparisons
from astro_stampede.demo import png_bytes


def write_rows(path, rows):
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@pytest.mark.parametrize('modern', [False, True])
def test_comparison_manifest_formats_and_final_fallback(tmp_path, modern):
    primary = tmp_path / 'primary'
    comparison = tmp_path / 'comparison'
    for root in (primary, comparison):
        (root / 'cutouts').mkdir(parents=True)
    (primary / 'cutouts/original.png').write_bytes(png_bytes())
    (comparison / 'cutouts/final.png').write_bytes(png_bytes(comparison=True))
    write_rows(primary / 'manifest.csv', [dict(target_key='primary-key', png_path='cutouts/original.png')])
    mapping = dict(primary_target_key='primary-key', comparison_target_key='comparison-key')
    if modern:
        write_rows(comparison / 'manifest.csv', [dict(primary_target_key='primary-key', comparison_target_key='comparison-key',
            png_path='', status='unavailable', band='r')])
        mapping.update(png_path='cutouts/final.png', status='created_fallback', band='r', comparison_band='i')
    else:
        write_rows(comparison / 'manifest.csv', [dict(target_key='comparison-key',
            png_path='cutouts/final.png', status='created', band='i')])
    write_rows(comparison / 'comparison_mapping.csv', [mapping])
    catalog = load_comparisons(primary, comparison)
    record = catalog.images['cutouts/original.png']
    assert record['status'] == 'available'
    assert record['band'] == 'i'
    assert list(catalog.paths.values()) == [comparison / 'cutouts/final.png']

    # A final unavailable result must not resurrect an older successful image.
    if modern:
        write_rows(comparison / 'manifest.csv', [dict(primary_target_key='primary-key', comparison_target_key='comparison-key',
            png_path='cutouts/final.png', status='created')])
        mapping.update(png_path='', status='no_coverage')
        write_rows(comparison / 'comparison_mapping.csv', [mapping])
        catalog = load_comparisons(primary, comparison)
        assert catalog.images['cutouts/original.png']['status'] == 'Comparison unavailable: no_coverage'
        assert not catalog.paths


def test_blank_fallback_keys_remain_bound_to_each_primary(tmp_path):
    primary, comparison = tmp_path / 'primary', tmp_path / 'comparison'
    for root in (primary, comparison):
        (root / 'cutouts').mkdir(parents=True)
    originals, mappings = [], []
    for number in range(3):
        relative = f'cutouts/synthetic-{number}.png'
        (primary / relative).write_bytes(png_bytes(number))
        originals.append(dict(target_key=str(number), png_path=relative))
        if number < 2:
            (comparison / relative).write_bytes(png_bytes(number, comparison=True))
        mappings.append(dict(primary_target_key=str(number), comparison_target_key='',
            png_path=relative if number < 2 else '', status='created' if number < 2 else 'no_coverage'))
    write_rows(primary / 'manifest.csv', originals)
    write_rows(comparison / 'manifest.csv', mappings)
    write_rows(comparison / 'comparison_mapping.csv', mappings)
    catalog = load_comparisons(primary, comparison)
    assert len(catalog.paths) == 2
    for number in range(2):
        record = catalog.images[f'cutouts/synthetic-{number}.png']
        assert catalog.paths[record['url'].rsplit('/', 1)[-1]] == comparison / f'cutouts/synthetic-{number}.png'
    assert catalog.images['cutouts/synthetic-2.png']['status'] == 'Comparison unavailable: no_coverage'
