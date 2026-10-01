"""Select existing directory workflows without requiring a generic manifest."""
import argparse
from pathlib import Path

from . import defaults, review


def image_root(path):
    """Recognize extracted bundles, including a repeated outer directory."""
    for candidate in (path, path / path.name):
        if (candidate / 'cutouts').is_dir():
            return candidate
    return None


def dispatch(argv, public_options):
    explicit_legacy = bool(argv and argv[0] == 'legacy')
    argv = argv[1:] if explicit_legacy else list(argv)
    options = {arg.split('=', 1)[0] for arg in argv if arg.startswith('--')}
    legacy_options = {option for action in review.build_parser()._actions for option in action.option_strings}
    requested = explicit_legacy or bool(options & (legacy_options - public_options))
    if not requested and options & {'--config', '--manifest', '--demo', '--help'}:
        return False
    probe = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    probe.add_argument('--root', type=Path, default=Path.cwd())
    probe.add_argument('--state-dir', type=Path)
    args, remaining = probe.parse_known_args(argv)
    root = args.root.expanduser().resolve()
    selected = image_root(root)
    outer = root
    if selected is None and root.is_dir() and not (root / 'manifest.csv').exists():
        bundles = [(child, image_root(child)) for child in sorted(root.iterdir())
                   if child.is_dir() and not child.name.endswith('_comparisons')]
        bundles = [(child, inner) for child, inner in bundles if inner is not None]
        if len(bundles) > 1 and not options & {'--products', '--layout'}:
            raise ValueError('Multiple image bundles found. Set --root to the desired primary bundle.')
        if len(bundles) == 1:
            outer, selected = bundles[0]
    detected = selected is not None or (root / 'preliminary_visit_image').is_dir()
    if not requested and not detected:
        return False
    # Explicit products/layout select a directory interpretation without root rebasing.
    if selected is not None and not options & {'--products', '--layout'}:
        root = selected
        remaining += ['--products', 'cutouts']
        if '--comparison-root' not in options:
            comparison = outer.parent / (outer.name + '_comparisons')
            if not comparison.is_dir() and outer.parent.name == outer.name:
                comparison = outer.parent.parent / (outer.name + '_comparisons')
            if comparison.is_dir():
                try:
                    from .adapters.comparisons import bundle_root
                    resolved = bundle_root(comparison)
                    ready = (root / 'manifest.csv').is_file() and (resolved / 'comparison_mapping.csv').is_file()
                except ValueError:
                    ready = False
                if ready:
                    remaining += ['--comparison-root', str(comparison)]
                else:
                    print('[astro-stampede] Comparison folder found, but bundle manifests/mapping are missing; reviewing originals only.', flush=True)
    values = defaults.paths(root, output=args.state_dir)
    configured = {key: Path(values[key]) for key in ('db', 'index_db', 'classification_parquet', 'export_dir')}
    configured['root'] = root
    review.main(remaining, defaults=configured)
    return True
