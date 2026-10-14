# Directory, manifest, and comparison workflows

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

## Generic manifests

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
