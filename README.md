# Astro Stampede

A local browser application for reviewing astronomical PNG cutout sequences.
Review object queues, animate or blink images, compare explicitly matched images
side by side, adjust the display, score with the keyboard, add tags and notes,
undo scores, and export labels and audit events. Model scores remain hidden until
**Reveal** is selected.

## Install and try it

Python 3.10 or later is required. From this checkout:

```sh
python -m venv .venv
source .venv/bin/activate
pip install .
astro-stampede --demo local/demo --reviewer demo
```

The demo creates new synthetic pixels and a runtime manifest in a new directory.
It refuses to overwrite an existing directory. It uses no RCC installation or
research data. To reopen the same demo and its labels:

```sh
astro-stampede --root local/demo --manifest local/demo/manifest.csv \
  --dataset synthetic-demo --state-dir local/demo/state --reviewer demo
```

The application binds to loopback. It is designed for one local review process,
not shared hosted sessions. Source images are read only. Labels are saved to
SQLite; pending browser changes flush periodically and before navigation/export.
Use **Export** or **Shutdown** to write the portable snapshot before closing a session.

## Your own dataset

Supply `--root`, `--manifest`, `--dataset`, and `--state-dir` as in the command above.
Inventories and configurations stay outside version control; `local/` is ignored.
The repository contains no image-name tables, research images, or deployment paths.
The only manifest example is the runtime demo generator.

The manifest is a UTF-8 CSV with unique headers:

- Required: `image_id`, `object_id`, `relative_path`.
- Optional: `order` (finite number), `time` (display timestamp), `filter`,
  `metadata` (JSON object), `role`, `comparison_id`.
- Optional reveal inputs: `model_score_r3` and `model_score_operational`, both
  probabilities between zero and one. Supply both together or leave both blank.

Image IDs are nonempty and unique within the manifest. Paths must point to
existing PNG images beneath the root. Absolute paths, parent traversal, escaping
symlinks, repeated image paths, malformed rows, and unknown columns are rejected.
Images are ordered by `order`, then time and ID; row order is the default.

`role` defaults to `primary`. To compare an image, reference another row's
`image_id` in `comparison_id`; that target row must explicitly have role
`comparison`. Comparison rows are display-only and never enter the scoring queue.
No filename or date proximity matching occurs. Unreferenced comparison rows are
not displayed.

Choose a stable dataset namespace. Stored IDs are SHA-256 hashes of a JSON tuple
containing the namespace, entity kind, and manifest ID. Moving or renaming an
image while retaining its manifest ID preserves its labels. Changing the namespace
creates different identities. Keep the manifest IDs alongside exported labels.
A cache is bound to one dataset and root; use a fresh index after relocating data.

Validate without opening classification stores:

```sh
astro-stampede --root local/demo --manifest local/demo/manifest.csv \
  --dataset synthetic-demo --state-dir local/demo/state --validate
```

## Local configuration and persistence

`--config` accepts a local JSON object whose keys correspond to CLI option names
with underscores. Relative paths in that file resolve relative to the configuration
file. CLI arguments override configured values. Supported settings include `title`,
`reviewer`, `tags` (a list), `score_min`, `score_max`, and `hide_metadata`. The default
activity preset uses scores 0–9 and astronomy review tags. A configured scale can
use any contiguous subset of the keyboard digits 0–9.

By default, the state directory contains a durable label SQLite database, a
rebuildable dataset index, a portable classification Parquet, and an export folder.
`--db`, `--index-db`, `--classification-parquet`, and `--export-dir` can select
existing local stores explicitly. Label and index paths must differ.

**SQLite labels are authoritative by default.** Startup does not import or merge
snapshots, relocate databases, or split old databases. Only `--import-snapshot`
explicitly imports a newer snapshot. That operation **replaces** scores, object
reviews, and audit events; it is not a multi-reviewer merge. Choose the canonical
source before using it. A new installation alone never reconciles live data.

CSV exports include stable IDs, relative image paths, labels and provenance.
JSONL contains audit events; Parquet preserves the durable tables. Automatically
recorded absolute source paths are omitted from CSV exports and browser responses.
User-entered comments, tags and metadata are local content and may themselves
contain private information: review exports before sharing them.

## RCC compatibility

RCC keeps its existing `rcc-thumbnail-review` command and deployment configuration;
it delegates to this package. Its optional `review` dependency installs the engine.
Until a distribution is published, install this checkout into the same environment
first. `astro-stampede legacy --help` exposes the compatibility layout CLI independently
of RCC. It requires explicit root, label DB, snapshot, and export destinations.
The compatibility API also supports Active Asteroids queues and explicit
Rubin/Ponder comparison manifests. These parsers contain no deployment inventories.

Existing RCC and Active Asteroids IDs are preserved. Their product context,
class, object and filename identity rules are not replaced with generic manifest
namespaces. The same canonical SQLite and Parquet locations can be passed through
the compatibility launcher. RCC remains responsible for filename generation;
this package carries the minimal identity parser needed to read existing products.

## Development and public-data boundary

```sh
pip install '.[dev]'
pytest
python -m build
python tools/audit_public.py --history \
  --artifact dist/astro_stampede-0.1.0-py3-none-any.whl \
  --artifact dist/astro_stampede-0.1.0.tar.gz
```

The audit checks filenames and contents in the working tree, index, all local Git
refs, and specified distributions. It rejects image-name tables, database/image
assets, binary files, and private path patterns. CI runs it on every change.
For local release checks, `--private-values` accepts an external newline-delimited
denylist; results report counts without printing matching private values. Never
add that denylist, a real manifest, a screenshot of research data, or a deployment
configuration to this repository. Pattern scans supplement manual review.

The code was extracted from a reviewed working version of Rubin Comet Catchers;
its history and research data were not imported. See [VALIDATION.md](VALIDATION.md)
for the verification scope.

## License and acknowledgement

STAMPEDE is available under the [MIT License](LICENSE), copyright 2026 Colin
Chandler.

If you use STAMPEDE in research, please acknowledge the software. Once a STAMPEDE
paper is published, please cite it as well. This scholarly request is separate
from the MIT license conditions.

For the first STAMPEDE publication, add the verified reference and citation
instructions here and in `CITATION.cff`, and include the acknowledgement and
citation request in the publication and release materials.
