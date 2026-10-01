"""Root-only launches exercise directory workflows without generic manifests."""
import csv

import pytest

from astro_stampede import cli, review
from astro_stampede.demo import png_bytes
from astro_stampede.adapters.comparisons import load_comparisons


def rows(path, data):
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(data[0]))
        writer.writeheader()
        writer.writerows(data)


def bundle(parent, name='synthetic-bundle', nested=False):
    outer = parent / name
    root = outer / name if nested else outer
    image = root / 'cutouts' / 'synthetic-bin' / 'SyntheticObject' / 'synthetic.png'
    image.parent.mkdir(parents=True)
    image.write_bytes(png_bytes())
    return outer, root, image


@pytest.mark.parametrize('nested', [False, True])
def test_root_only_parent_selects_originals_and_explicit_comparisons(tmp_path, monkeypatch, nested):
    outer, root, image = bundle(tmp_path, nested=nested)
    comparison = tmp_path / (outer.name + '_comparisons')
    counterpart = comparison / 'cutouts' / 'comparison.png'
    counterpart.parent.mkdir(parents=True)
    counterpart.write_bytes(png_bytes(comparison=True))
    rows(root / 'manifest.csv', [dict(target_key='synthetic-primary', png_path=image.relative_to(root).as_posix())])
    rows(comparison / 'manifest.csv', [dict(target_key='synthetic-comparison', png_path='cutouts/comparison.png')])
    rows(comparison / 'comparison_mapping.csv', [dict(primary_target_key='synthetic-primary', comparison_target_key='synthetic-comparison')])
    captured = []
    monkeypatch.setattr(review, 'serve', lambda config, **kwargs: captured.append(config))
    monkeypatch.chdir(tmp_path)
    cli.main(['--root', '.'])
    config = captured[0]
    assert config.root == root
    assert config.products == ('cutouts',)
    assert config.db_path == root / 'state' / 'labels.sqlite'
    records = review.iter_png_records(config.root, config.products)
    assert len(records) == 1
    catalog = load_comparisons(config.root, config.comparison_root)
    assert catalog.images[records[0]['relative_path']]['status'] == 'available'
    assert not config.db_path.exists()


def test_root_only_without_any_manifest(tmp_path, capsys):
    _, root, _ = bundle(tmp_path)
    cli.main(['--root', str(tmp_path), '--validate'])
    assert '1 PNGs' in capsys.readouterr().out
    assert not (root / 'state').exists()


def test_object_list_without_legacy_prefix_and_explicit_storage(tmp_path, monkeypatch):
    root = tmp_path / 'images'
    image = root / 'preliminary_visit_image' / 'synthetic-class' / 'SyntheticObject' / 'synthetic.png'
    image.parent.mkdir(parents=True)
    image.write_bytes(png_bytes())
    queue = tmp_path / 'queue.csv'
    rows(queue, [dict(object_id='SyntheticObject')])
    captured = []
    monkeypatch.setattr(review, 'serve', lambda config, **kwargs: captured.append(config))
    db = tmp_path / 'canonical.sqlite'
    cli.main(['--root', str(root), '--object-list', str(queue), '--db', str(db)])
    assert captured[0].db_path == db
    assert captured[0].object_scope == (('synthetic-class', 'SyntheticObject'),)


def test_ambiguous_bundle_parent_requires_selection(tmp_path):
    bundle(tmp_path, 'synthetic-one')
    bundle(tmp_path, 'synthetic-two')
    with pytest.raises(SystemExit):
        cli.main(['--root', str(tmp_path), '--validate'])
    assert not (tmp_path / 'state').exists()
