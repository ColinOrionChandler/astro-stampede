# Settings and persistence

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
