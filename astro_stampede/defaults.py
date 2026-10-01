"""Predictable local defaults, shared by the launcher and Settings."""
import hashlib
import json
from pathlib import Path


def paths(root, dataset=None, output=None):
    root = Path(root).expanduser().resolve()
    dataset = dataset or root.name or 'dataset'
    output = Path(output).expanduser().resolve() if output else root / 'state'
    binding = json.dumps([str(root), dataset]).encode()
    index = 'index-' + hashlib.sha256(binding).hexdigest()[:16] + '.sqlite'
    return dict(root=str(root), manifest=str(root / 'manifest.csv'), dataset=dataset,
                output=str(output), db=str(output / 'labels.sqlite'),
                index_db=str(output / index),
                classification_parquet=str(output / 'classifications.parquet'),
                export_dir=str(output / 'exports'))
