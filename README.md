# Astro Stampede

A local browser application for reviewing astronomical PNG cutout sequences.
Review object queues, animate or blink images, compare explicitly matched images,
score with the keyboard, add tags and notes, undo scores, and export labels and
audit events. Model scores remain hidden until **Reveal** is selected.

## Install and try it

Python 3.10 or later is required. To install from source:

```sh
git clone https://github.com/ColinOrionChandler/astro-stampede.git
cd astro-stampede
python -m venv .venv
source .venv/bin/activate
python -m pip install .
astro-stampede --demo local/demo --reviewer demo
```

The demo creates synthetic pixels in a new directory and refuses to overwrite
an existing directory. Source images are read only. Classifications are stored
in SQLite; use **Export** or **Shutdown** to write a portable snapshot. The server
binds to loopback and supports one local review process.

Once v0.1.1 is published to PyPI, it can also be installed with
`python -m pip install astro-stampede==0.1.1` in an active virtual environment.

## Documentation

- [Installation and reopening the demo](https://github.com/ColinOrionChandler/astro-stampede/blob/main/docs/installation.md)
- [Directory, manifest, object-list, and comparison workflows](https://github.com/ColinOrionChandler/astro-stampede/blob/main/docs/workflows.md)
- [Display controls and image-loading performance](https://github.com/ColinOrionChandler/astro-stampede/blob/main/docs/display.md)
- [Classification identity and existing stores](https://github.com/ColinOrionChandler/astro-stampede/blob/main/docs/identity.md)
- [Settings and persistence](https://github.com/ColinOrionChandler/astro-stampede/blob/main/docs/persistence.md)
- [RCC compatibility](https://github.com/ColinOrionChandler/astro-stampede/blob/main/docs/compatibility.md)
- [Read-only validation diagnostics](https://github.com/ColinOrionChandler/astro-stampede/blob/main/docs/validation.md)
- [Development guide](https://github.com/ColinOrionChandler/astro-stampede/blob/main/DEVELOPMENT_GUIDE.md)

The Sphinx documentation can be built locally:

```sh
python -m pip install -e '.[dev,docs]'
python -m sphinx -W --keep-going -b html docs docs/_build/html
```

Open `docs/_build/html/index.html`. Hosted documentation is not enabled by this
configuration alone. Development requires an editable installation because the
package now lives under `src/`.

## Public-data boundary

Keep research images, manifests, classification stores, and deployment settings
outside version control. Tests generate synthetic data at runtime. CI audits
source history and built distributions; see the [development checks](https://github.com/ColinOrionChandler/astro-stampede/blob/main/docs/development.md).
[VALIDATION.md](https://github.com/ColinOrionChandler/astro-stampede/blob/main/VALIDATION.md) records the original extraction checks.

## License and acknowledgements

MIT License, copyright 2026 Colin Chandler. Please acknowledge STAMPEDE when
using it in research; cite its paper once published. See
[license and citation guidance](https://github.com/ColinOrionChandler/astro-stampede/blob/main/docs/acknowledgements.md).

Development tooling uses [LINCC Frameworks Python Project Template](https://github.com/lincc-frameworks/python-project-template)
v2.3.0. Template citation: Oldag et al. (2024), *A Python Project Template for
Healthy Scientific Software*, RNAAS 8, 141,
[doi:10.3847/2515-5172/ad4da1](https://doi.org/10.3847/2515-5172/ad4da1).
See [template customizations and updates](https://github.com/ColinOrionChandler/astro-stampede/blob/main/docs/template.md) and `CITATION.bib`.
