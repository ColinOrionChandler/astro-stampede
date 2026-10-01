"""Public CLI with explicit dataset and storage selection."""
from __future__ import annotations
import argparse
from dataclasses import replace
import getpass
import json
from pathlib import Path
import time
import sys
from . import review, defaults


def parser():
    result = argparse.ArgumentParser(description='Review astronomical PNG sequences locally.')
    result.add_argument('--config', type=Path, help='Local JSON configuration; CLI overrides its values.')
    result.add_argument('--demo', type=Path, help='Generate a synthetic dataset in a new directory and launch it.')
    result.add_argument('--root', type=Path, help='Image folder (default: current directory).')
    result.add_argument('--manifest', type=Path, help='Manifest CSV (default: manifest.csv in the image folder).')
    result.add_argument('--dataset', help='Stable namespace (default: image folder name); retain it when relocating data.')
    result.add_argument('--state-dir', type=Path, help='Local output directory (default: state/ in the image folder).')
    result.add_argument('--db', type=Path, help='Existing canonical labels DB; never copied implicitly.')
    result.add_argument('--index-db', type=Path)
    result.add_argument('--classification-parquet', type=Path)
    result.add_argument('--export-dir', type=Path)
    result.add_argument('--title', default='Astro Stampede')
    result.add_argument('--reviewer', default=getpass.getuser())
    result.add_argument('--score-min', type=int, default=0)
    result.add_argument('--score-max', type=int, default=9)
    result.add_argument('--tags', nargs='+')
    result.add_argument('--hide-metadata', action='store_true')
    result.add_argument('--import-snapshot', action='store_true', help='Explicitly replace local labels and events from a newer snapshot; this is not a merge.')
    result.add_argument('--host', choices=['127.0.0.1', 'localhost'], default='127.0.0.1')
    result.add_argument('--port', type=int, default=8765)
    result.add_argument('--no-open', action='store_true')
    result.add_argument('--quiet', action='store_true')
    result.add_argument('--validate', action='store_true', help='Validate inputs without opening or changing classification stores.')
    return result


def parse_args(argv=None):
    preliminary = argparse.ArgumentParser(add_help=False)
    preliminary.add_argument('--config', type=Path)
    known, _ = preliminary.parse_known_args(argv)
    result = parser()
    if known.config:
        try:
            data = json.loads(known.config.read_text())
            actions = {a.dest: a for a in result._actions if a.dest not in {'help', 'config', 'demo'}}
            if not isinstance(data, dict) or set(data) - set(actions):
                raise ValueError('Unknown configuration keys.')
            for key, value in data.items():
                action = actions[key]
                if action.type is Path:
                    value = Path(value).expanduser()
                    if not value.is_absolute():
                        value = known.config.resolve().parent / value
                elif action.type is int:
                    if type(value) is not int:
                        raise ValueError('Numeric settings require integers.')
                elif isinstance(action, argparse._StoreTrueAction):
                    if type(value) is not bool:
                        raise ValueError('Boolean settings require booleans.')
                elif key == 'tags':
                    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                        raise ValueError('Tags require a list of strings.')
                elif not isinstance(value, str):
                    raise ValueError('Text settings require strings.')
                if action.choices and value not in action.choices:
                    raise ValueError('Unsupported configuration option value.')
                data[key] = value
            result.set_defaults(**data)
        except (ValueError, OSError, TypeError):
            result.error('Invalid local configuration; check keys, types, and file readability.')
    return result, result.parse_args(argv)


def make_config(args):
    suggested = defaults.paths(args.root or Path.cwd(), args.dataset, args.state_dir)
    args.root = Path(suggested['root'])
    args.manifest = args.manifest or Path(suggested['manifest'])
    args.dataset = suggested['dataset']
    args.state_dir = Path(suggested['output'])
    if not 0 <= args.score_min <= args.score_max <= 9:
        raise ValueError('Score scale must be a subset of the keyboard digits 0 through 9.')
    state = args.state_dir.expanduser().resolve()
    config = review.ReviewConfig(
        root=args.root.expanduser().resolve(), manifest_path=args.manifest.expanduser().resolve(),
        dataset=args.dataset, layout='manifest', products=(args.dataset,), classes=None,
        object_scope=None, object_list_path=None, db_path=(args.db or state / 'labels.sqlite').expanduser().resolve(),
        index_db_path=(args.index_db or Path(suggested['index_db'])).expanduser().resolve(),
        classification_parquet_path=(args.classification_parquet or state / 'classifications.parquet').expanduser().resolve(),
        export_dir=(args.export_dir or state / 'exports').expanduser().resolve(), reviewer=args.reviewer,
        session_id=f'{args.reviewer}-{time.time_ns()}', title=args.title,
        score_min=args.score_min, score_max=args.score_max, tags=tuple(args.tags) if args.tags else None,
        show_metadata=not args.hide_metadata, import_snapshot=args.import_snapshot,
    )
    if config.db_path == config.index_db_path:
        raise ValueError('Label and index databases must be separate files.')
    from .manifest import records, reveal_scores
    records(config)  # Validate paths and image headers before any database writes.
    return replace(config, reveal_scores=reveal_scores(config))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "legacy":
        legacy_args = argv[1:]
        if not any(arg in {"-h", "--help"} for arg in legacy_args):
            supplied = {arg.split("=", 1)[0] for arg in legacy_args}
            if not {"--root", "--db", "--classification-parquet", "--export-dir"} <= supplied:
                raise SystemExit("Legacy layouts require explicit --root, --db, --classification-parquet and --export-dir.")
        review.main(legacy_args)
        return
    result, args = parse_args(argv)
    try:
        if args.demo:
            from .demo import generate
            generate(args.demo)
            args.root = args.demo
            args.manifest = args.demo / 'manifest.csv'
            args.dataset = 'synthetic-demo'
            args.state_dir = args.demo / 'state'
        config = make_config(args)
        if args.validate:
            print('Manifest and PNG inputs validated; classification state untouched.')
            return
        review.serve(config, host=args.host, port=args.port, open_browser=not args.no_open, quiet=args.quiet)
    except (ValueError, OSError) as exc:
        # Exceptions from filesystem libraries can include deployment paths.
        result.error('Input or storage validation failed. Check configuration, manifest schema, and local file access.')

if __name__ == '__main__':
    main()
