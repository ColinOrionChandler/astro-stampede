# Release notes

## v0.1.1 — prepared, not published

This is the first planned release after adopting the LINCC Python Project
Template. No earlier version is implied to have been published to PyPI.

### Included functionality

- Local browser review of astronomical PNG cutout sequences from directories,
  manifests, or object lists, with a synthetic demo for evaluation.
- Animation, blinking, explicitly matched comparison images, keyboard scoring,
  tags, notes, undo, and export of classifications and audit history.
- Persistent classification identity, reviewer settings, compatibility support
  for RCC workflows, image preloading, and read-only validation diagnostics.

### Packaging and development

- Installable `astro-stampede` command and `astro_stampede` Python package with
  packaged browser assets, now using a `src/` layout.
- Versions derived from Git tags through `setuptools-scm`; the intended release
  tag is `v0.1.1` and the installed distribution version is `0.1.1`.
- LINCC template v2.3.0 tracked with Copier, Ruff and pre-commit checks, Python
  and JavaScript tests, coverage collection, distribution checks, public-data
  audits, and Sphinx/AutoAPI documentation.
- CI covers Python 3.10–3.14 on Linux and Python 3.12 on macOS. Strict Twine
  validation checks distribution descriptions before upload; Markdown layout
  is reviewed separately in a browser.

### Compatibility and limits

Python 3.10 or later is required. Runtime dependencies remain pandas and pyarrow.
The CLI arguments, import name, configuration formats, classification identities,
and storage schemas are preserved; no research-data migration is required.
Development now requires an editable installation:

```sh
python -m pip install -e '.[dev,docs,release]'
```

The application serves one local review process on loopback. Source images
remain read-only. The template migration changes development tooling and
packaging; it does not establish improved classification accuracy or image
loading performance. Windows is not covered by CI.

See the [publication checklist](releases.md) before creating the release.
