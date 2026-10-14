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

### Image loading performance

The browser preloads the current image, the next three images, the previous image,
and their comparison frames, with one background request at a time. It retains
at most ten preload images; primary frames larger than four megapixels are skipped.
Versioned PNG URLs allow browser caching for one hour. Restarting the server,
rescanning, or changing settings changes those URLs, so use **Rescan** after
replacing source pixels. Source PNGs are never rewritten or copied into a second
classification dataset.

Manifest and Active Asteroids image requests skip rebuilding effective scores;
score-dependent RCC representative selection retains its existing lookup.
Object lists, classification saves, and first uncached reads can still take time.
For sluggish systems, check Activity Monitor's memory pressure and available space
on the startup disk as well as activity on the image drive. Raising CPU scheduling
priority does not reserve RAM or eliminate storage latency.

## Directory and comparison bundles

The existing directory workflow does **not** require a generic review manifest.
`astro-stampede --root .` recognizes a standard `preliminary_visit_image/` tree,
a bundle containing `cutouts/<class-or-bin>/<object>/*.png`, or a parent with one
such primary bundle. Repeated extraction folders are supported. If several
primary bundles are present, select one with `--root`.

A sibling `<bundle>_comparisons` folder is excluded from the scoring queue.
When the bundles contain their generated manifests and `comparison_mapping.csv`,
those explicit mappings enable the comparison panel automatically. Without those
sidecars, originals can still be reviewed; comparison matching is not guessed.
These bundle sidecars are distinct from the generic manifest described below.

`--object-list`, `--image-list`, `--comparison-root`, `--products`, and `--layout`
route directly to the existing directory launcher; the `legacy` prefix is optional.
For example, `astro-stampede --root /path/to/images --object-list /path/to/queue.csv`
uses the existing object-list workflow. Omitted storage paths default to `state/`
inside the selected image root; explicit canonical storage paths remain supported.
Use `--validate` to check inputs without writing classification stores.

## Your own dataset

For a folder containing `manifest.csv`, start with `astro-stampede --root /path/to/images`.
Omitting `--root` uses the current directory. The default dataset name is the folder
name, and outputs go in its `state/` subfolder: `labels.sqlite`,
`classifications.parquet`, `exports/`, and an index named for the dataset and root.
Override any of these with `--manifest`, `--dataset`, `--state-dir`, or the individual
storage options. Retain the dataset name when moving or renaming a dataset folder
to preserve classification identities.
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

### Thumbnail identity

Classification identity defaults to the **parsed thumbnail name** in every
launcher and layout. A versioned, unambiguous JSON key is hashed with SHA-256.
Class, dataset, product directory, object group and filesystem location are
metadata; moving a thumbnail or changing those fields preserves its score in the
same database. Object groups remain local to each dataset and omit class.

The name itself selects supported AA, RCC and Ponder/coadd parsing rules.
Recognized annotations are removed; RCC timestamps and detector ordering do not
split equivalent observation products. Observation, detector and coadd distinctions
remain meaningful. `.png.gz` is equivalent to `.png`. Unrecognized names retain
their logical basename and case, including unrecognized annotation-like tokens.
Renaming an observation can therefore change its identity.

Explicit alternatives, also accepted in saved JSON configuration:

- `--identity-key parsed-name|manifest-id` (default `parsed-name`). Manifest IDs
  otherwise identify rows and comparison references only. `manifest-id` requires
  a manifest launch and preserves identity across filename changes.
- `--identity-namespace VALUE` deliberately separates classifications.
- `--identity-include-class` deliberately includes class, requiring an explicit
  `object_class` on each primary manifest row or a class in the directory input.
  Class is optional in AA object lists when this override is absent.

The selected policy is stored in SQLite and shown in Settings. Downloaded
settings retain the policy; both generic and directory configurations can be
reopened with `--config`. A cache is bound to its root, dataset and identity policy;
use a fresh rebuildable index when changing these. A changed store policy requires
an explicit compatibility preflight.

### Existing classification stores

Before first use of a legacy store, stop its writers and run a read-only audit
with explicitly selected sources that contain both old IDs and filenames:

```sh
python -m astro_stampede.compatibility --db labels.sqlite \
  --index old-index.sqlite --export rich-labels.csv --report compatibility.json
```

`--index` and `--export` are repeatable. Legacy generic manifests use
`--manifest manifest.csv --legacy-dataset original-dataset`. Empty-name export
rows are ignored; unmapped IDs and contradictory evidence are reported without
guessing. The report includes raw-table fingerprints, mapping coverage, effective
classification counts, and unresolved conflicts. Review those results before
repeating the command with `--apply`. The apply transaction rechecks evidence,
registers aliases in the **same SQLite database**, and verifies unchanged raw
score, object-review and audit-event rows. Repeating registration is idempotent.
Unmapped records remain in the store and snapshots. Contradictory mappings abort
the transaction. Use additional explicit evidence to reconnect unmapped records.

Effective classifications resolve aliases per thumbnail and reviewer. The latest
timestamp wins with its complete score, status, tags and comment. Equal timestamps
use attributable audit-event ordering; otherwise conflicting thumbnails are
flagged for review while the rest of the queue remains available. A new
classification resolves a conflict when its timestamp is later. Subsequent writes
use canonical IDs and append audit events; clears leave a tombstone so older
aliased scores cannot reappear. Multiple source locations share one deterministic
display representative and one score. Comparisons remain display-only.

For a deliberate change to an already registered policy, first audit the new
policy, then apply with `--accept-policy-change`. Existing canonical IDs are
immutable: changing namespace or adding class creates separate identities and
does not reassign prior canonical classifications. Legacy alias contradictions
still require resolution and are never silently overwritten.

Snapshot **version 2** preserves all historical score rows and audit events plus
the registry, aliases, provenance and policy. Version 1 remains importable via
`--import-snapshot`; it requires a compatibility preflight before review because
it contains no filename mappings. Imports remain explicit replacements. Effective
CSV exports expose canonical IDs and recoverable names; snapshots retain the
original historical IDs. Exports also include a separate identity-conflict CSV.

### Expected trail bar

The **Expected trail bar** checkbox under Display toggles a lime-green,
black-outlined bar at the lower right of each image, matching the RCC batch
overlay. Its length is the predicted trail length in original PNG pixels; it
does not indicate the trail direction or an angular sky scale. Zoom and Fit scale
the bar with the image. Inversion, brightness and contrast leave it green.
Source PNGs and classifications are unchanged.

For a generic manifest, put `{"expected_trail_pixels": 24}` in that row's
`metadata` JSON (CSV writers handle the required quoting). Values must be finite,
nonnegative numbers; null or an omitted key means unavailable. Comparison rows
need their own value, including during blinking. After editing metadata, select
**Rescan**. The runtime demo supplies synthetic lengths for both panels.

In RCC compatibility mode, the length is read from the terminal `_NdPix.png`
filename suffix, including decimal values. No length is inferred from pixel
scale alone. Missing lengths, lengths below 3 pixels, and bars too large for the
image are reported in Display and omitted instead of being clipped or enlarged.
Turn the overlay off when reviewing copies that already have a bar baked in.

Validate without opening classification stores:

```sh
astro-stampede --root local/demo --manifest local/demo/manifest.csv \
  --dataset synthetic-demo --state-dir local/demo/state --validate
```

## Local configuration and persistence

Open **Settings** beside the app title to choose the image folder, manifest CSV,
stable dataset name, and classification output locations. **Browse** navigates
folders on the computer running the local server. You can also type an absolute
path or a path beginning with `~`, including a new output folder. Choosing a
**Classification output folder** is prefilled with the current label database folder; changing it fills in the label database, index, Parquet
snapshot, and export paths; each can then be adjusted separately.

**Use defaults for this folder** fills in `manifest.csv` and the `state/` output
paths for the selected image folder, retaining the entered dataset name. It does
not apply changes until you choose Apply. Selecting a different image folder
updates a standard manifest path and suggests a separate index while preserving
existing label and export destinations. Manifest and index paths customized during the edit are kept.

**Save pending work & apply** flushes pending scores, saves the current snapshot,
validates the inputs and output locations, indexes the selected dataset, and
reloads the page. Changing the label database opens that store without copying
or importing previous classifications. If you move the image folder or change
the dataset name, choose a fresh index file. Other open tabs must reload before
saving after a settings change.

Settings apply to the running session. After applying, reopen Settings and select
**Download active configuration** to keep a reusable local JSON configuration:

```sh
astro-stampede --config /path/to/astro-stampede-settings.json
```

The download contains local paths; keep it out of version control. Compatibility
layouts support output-path changes here, while their input selections remain
managed by their launchers. Configuration download is available for manifest
sessions. Initial startup still uses the CLI or a saved configuration.

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
recorded absolute source paths are omitted from CSV exports and ordinary review
responses. The local Settings dialog explicitly displays configured paths.
User-entered comments, tags and metadata are local content and may themselves
contain private information: review exports before sharing them.

## RCC compatibility

RCC keeps its existing `rcc-thumbnail-review` command and deployment configuration;
it delegates to this package. Its optional `review` dependency installs the engine.
Until a distribution is published, install this checkout into the same environment
first. `astro-stampede legacy --help` exposes the compatibility layout CLI independently
of RCC. It accepts explicit root, label DB, snapshot, and export destinations, with local
`state/` defaults for omitted output paths.
The compatibility API also supports Active Asteroids queues and explicit
Rubin/Ponder comparison manifests. These parsers contain no deployment inventories.

Existing RCC and Active Asteroids score/event rows are preserved through the
identity registry and explicit compatibility preflight described above. All
launchers use the same filename identity policy by default. Pass the canonical
SQLite, snapshot and export locations together, with a fresh rebuildable index.
RCC remains responsible for filename generation; this package carries the parser
needed to recognize its observation products.

## Development and public-data boundary

```sh
pip install '.[dev]'
pytest
node --test tests/test_trails.cjs
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

### Validation diagnostics

Run your usual launcher command with `--validate` to check the manifest and PNG
inputs without opening or changing classification stores. Omitted paths retain
the launcher's normal defaults. Validation reports a problem count and one
specific diagnostic per line, including configuration keys or manifest row and
field names, for example:

```text
2 problem(s) found:
  - Invalid manifest row 2, object_id: ID cannot be empty.
  - Invalid manifest row 3, order: 'nan': Order must be a finite number.
```

Independent problems are collected in one pass. Checks that depend on invalid
input are skipped; unreadable files and unusable manifest headers prevent further
checks of that manifest. Expected failures exit with code 2. Local terminal
messages can include configured paths and filesystem reasons; review them before
sharing. Runtime storage failures are reported when encountered and stop startup;
`--validate` does not attempt writes to prove that storage is writable.
