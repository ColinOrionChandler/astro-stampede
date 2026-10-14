# Development

## Install and check

Use Python 3.10 or later, and Node.js 22 for browser unit tests. CI tests Python
3.10–3.14 on Linux and 3.12 on macOS. Documentation builds use Python 3.12.

```sh
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,docs]'
pre-commit install
python -m pytest --cov=astro_stampede --cov-report=term-missing
node --test tests/*.cjs
pre-commit run --all-files
python -m sphinx -W --keep-going -b html docs docs/_build/html
```

Open `docs/_build/html/index.html` to inspect the rendered documentation.
The editable installation is required: the package is under `src/`, and tests
do not add the repository root to Python's import path. Following a pull or a
new tag, reinstall to refresh generated version metadata. When migrating an
older development checkout, remove its stale root-level `astro_stampede.egg-info`
and `build/` directories before reinstalling.

Ruff checks correctness and imports with `E4`, `E7`, `E9`, `F`, and `I`, and
formats Python at 110 columns. Use `ruff check --fix .` and `ruff format .` for
intentional cleanup. Keep mechanical changes separate from behavior changes.
The pre-commit hooks validate configuration, lint, and format; the full Python
and JavaScript suites run explicitly and in CI. Coverage is reported without
a minimum-percentage gate or external coverage service.

## Distribution checks

Use a new output directory for every build so old artifacts cannot mask failures.

```sh
build_dir=$(mktemp -d)
python -m build --outdir "$build_dir"
python tools/audit_distributions.py "$build_dir" --history
python tools/check_distributions.py "$build_dir"
```

The last command installs the wheel and source archive in separate fresh
environments. It checks the CLI, matching versions, browser assets, synthetic
primary/comparison images, saved scores after a server restart, and unchanged
source pixels. Source archives must build without Git metadata.

## Public-data boundary

Use runtime-generated synthetic fixtures. Never commit research manifests,
image-name tables, databases, PNGs, screenshots of research data, deployment
configuration, or private paths. Local configuration and generated data belong
under ignored `local/` or outside the checkout.

The audit checks filenames and contents in the working tree, index, all local
Git refs, and built distributions. Pattern checks supplement manual review.
For a local release audit, `tools/audit_public.py --private-values` accepts an
external denylist and suppresses matched values. Do not commit that denylist.

The API reference describes current internals; it is not a promise that every
documented helper is a stable public API. Preserve the existing CLI, import
names used by integrations, storage formats, and classification identity rules.
