"""Public CLI with explicit dataset and storage selection."""

from __future__ import annotations

import argparse
import getpass
import json
import sqlite3
import sys
import time
from dataclasses import replace
from pathlib import Path

from . import defaults, review
from .validation import ValidationError, report


def parser():
    result = argparse.ArgumentParser(description="Review astronomical PNG sequences locally.")
    result.add_argument("--config", type=Path, help="Local JSON configuration; CLI overrides its values.")
    result.add_argument(
        "--demo", type=Path, help="Generate a synthetic dataset in a new directory and launch it."
    )
    result.add_argument("--root", type=Path, help="Image folder (default: current directory).")
    result.add_argument(
        "--manifest", type=Path, help="Manifest CSV (default: manifest.csv in the image folder)."
    )
    result.add_argument(
        "--dataset", help="Stable namespace (default: image folder name); retain it when relocating data."
    )
    result.add_argument(
        "--state-dir", type=Path, help="Local output directory (default: state/ in the image folder)."
    )
    result.add_argument("--db", type=Path, help="Existing canonical labels DB; never copied implicitly.")
    result.add_argument("--index-db", type=Path)
    result.add_argument("--classification-parquet", type=Path)
    result.add_argument("--export-dir", type=Path)
    result.add_argument("--title", default="Astro Stampede")
    result.add_argument("--reviewer", default=getpass.getuser())
    result.add_argument("--score-min", type=int, default=0)
    result.add_argument("--score-max", type=int, default=9)
    result.add_argument("--tags", nargs="+")
    result.add_argument("--hide-metadata", action="store_true")
    result.add_argument("--identity-key", choices=["parsed-name", "manifest-id"], default="parsed-name")
    result.add_argument("--identity-namespace")
    result.add_argument("--identity-include-class", action="store_true")
    result.add_argument(
        "--import-snapshot",
        action="store_true",
        help="Explicitly replace local labels and events from a newer snapshot; this is not a merge.",
    )
    result.add_argument("--host", choices=["127.0.0.1", "localhost"], default="127.0.0.1")
    result.add_argument("--port", type=int, default=8765)
    result.add_argument("--no-open", action="store_true")
    result.add_argument("--quiet", action="store_true")
    result.add_argument(
        "--validate",
        action="store_true",
        help="Validate inputs without opening or changing classification stores.",
    )
    return result


def parse_args(argv=None):
    preliminary = argparse.ArgumentParser(add_help=False)
    preliminary.add_argument("--config", type=Path)
    known, _ = preliminary.parse_known_args(argv)
    result = parser()
    problems = []
    if known.config:
        try:
            data = json.loads(known.config.read_text())
        except json.JSONDecodeError as exc:
            result.error(
                report(
                    ValidationError(
                        [f"Config {known.config}, line {exc.lineno}, column {exc.colno}: {exc.msg}"]
                    )
                )
            )
        except (OSError, UnicodeError) as exc:
            result.error(report(ValidationError([f"Read config {known.config}: {exc}"])))
        if not isinstance(data, dict):
            result.error(report(ValidationError([f"Config {known.config}: expected a JSON object."])))
        actions = {a.dest: a for a in result._actions if a.dest not in {"help", "config", "demo"}}
        converted = {}
        for key, value in data.items():
            try:
                if key not in actions:
                    raise ValueError("Unknown configuration key.")
                action = actions[key]
                if action.type is Path:
                    if not isinstance(value, str) or not value.strip():
                        raise ValueError("Expected a nonempty path string.")
                    value = Path(value).expanduser()
                    if not value.is_absolute():
                        value = known.config.resolve().parent / value
                elif action.type is int:
                    if type(value) is not int:
                        raise ValueError("Expected an integer.")
                elif isinstance(action, argparse._StoreTrueAction):
                    if type(value) is not bool:
                        raise ValueError("Expected a boolean.")
                elif key == "tags":
                    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
                        raise ValueError("Expected a nonempty list of strings.")
                elif not isinstance(value, str):
                    raise ValueError("Expected a string.")
                if action.choices and value not in action.choices:
                    raise ValueError(f"Expected one of {action.choices!r}; got {value!r}.")
                converted[key] = value
            except (ValueError, TypeError, OSError) as exc:
                problems.append(f"Config {known.config}, {key}: {exc}")
        result.set_defaults(**converted)
    args = result.parse_args(argv)
    # Carry config errors into preflight so independent manifest issues can join them.
    args.validation_problems = problems
    args.invalid_config_fields = set(data) - set(converted) if known.config else set()
    return result, args


def make_config(args, *, prepared=None):
    suggested = defaults.paths(args.root or Path.cwd(), args.dataset, args.state_dir)
    args.root = Path(suggested["root"])
    args.manifest = args.manifest or Path(suggested["manifest"])
    args.dataset = suggested["dataset"]
    args.state_dir = Path(suggested["output"])
    problems = list(getattr(args, "validation_problems", ()))
    if not 0 <= args.score_min <= args.score_max <= 9:
        problems.append(
            f"--score-min/--score-max: require 0 <= min <= max <= 9; got {args.score_min}, {args.score_max}."
        )
    if not 0 <= args.port <= 65535:
        problems.append(f"--port: expected an integer from 0 through 65535; got {args.port}.")
    state = args.state_dir.expanduser().resolve()
    config = review.ReviewConfig(
        root=args.root.expanduser().resolve(),
        manifest_path=args.manifest.expanduser().resolve(),
        dataset=args.dataset,
        layout="manifest",
        products=(args.dataset,),
        classes=None,
        object_scope=None,
        object_list_path=None,
        db_path=(args.db or state / "labels.sqlite").expanduser().resolve(),
        index_db_path=(args.index_db or Path(suggested["index_db"])).expanduser().resolve(),
        classification_parquet_path=(args.classification_parquet or state / "classifications.parquet")
        .expanduser()
        .resolve(),
        export_dir=(args.export_dir or state / "exports").expanduser().resolve(),
        reviewer=args.reviewer,
        session_id=f"{args.reviewer}-{time.time_ns()}",
        title=args.title,
        score_min=args.score_min,
        score_max=args.score_max,
        tags=tuple(args.tags) if args.tags else None,
        show_metadata=not args.hide_metadata,
        import_snapshot=args.import_snapshot,
        identity_key=args.identity_key,
        identity_namespace=args.identity_namespace,
        identity_include_class=args.identity_include_class,
    )
    if config.db_path == config.index_db_path:
        problems.append(
            f"--db/--index-db: Label and index databases must be separate files; both resolve to {config.db_path}."
        )
    from .manifest import read, records, reveal_scores

    validated_rows = None
    if not getattr(args, "invalid_config_fields", set()) & {"root", "manifest", "dataset"}:
        try:
            validated_rows = read(config, progress=prepared is not None and not args.quiet)
            records(config, rows=validated_rows)  # Validate policy before any database writes.
        except ValidationError as exc:
            problems.extend(exc.problems)
        except (ValueError, OSError) as exc:
            problems.append(f"Validate manifest {config.manifest_path}: {exc}")
    if problems:
        raise ValidationError(problems)
    if prepared is not None:
        prepared["rows"] = validated_rows
    return replace(config, reveal_scores=reveal_scores(config, rows=validated_rows))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    from .launcher import dispatch

    try:
        public_options = {option for action in parser()._actions for option in action.option_strings}
        if dispatch(argv, public_options):
            return
    except (ValueError, OSError, sqlite3.Error, RuntimeError) as exc:
        parser().error(str(exc))
    result, args = parse_args(argv)
    operation = "Validate inputs"
    try:
        if args.demo and args.validation_problems:
            raise ValidationError(args.validation_problems)
        if args.demo:
            from .demo import generate

            operation = f"Generate demo at {args.demo}"
            generate(args.demo)
            args.root = args.demo
            args.manifest = args.demo / "manifest.csv"
            args.dataset = "synthetic-demo"
            args.state_dir = args.demo / "state"
        operation = "Validate inputs"
        prepared = {}
        config = make_config(args, prepared=prepared)
        if args.validate:
            print("Manifest and PNG inputs validated; classification state untouched.")
            return
        operation = (
            f"Start review using label DB {config.db_path}, index DB {config.index_db_path}, "
            f"snapshot {config.classification_parquet_path}, and export directory {config.export_dir}"
        )
        review.serve(
            config,
            host=args.host,
            port=args.port,
            open_browser=not args.no_open,
            quiet=args.quiet,
            manifest_rows=prepared["rows"],
        )
    except (ValueError, OSError, sqlite3.Error, RuntimeError) as exc:
        if not isinstance(exc, ValidationError):
            exc = ValidationError([f"Startup failed ({operation}): {exc}"])
        result.error(report(exc))


if __name__ == "__main__":
    main()
