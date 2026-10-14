# LINCC template provenance and updates

This repository adopts [LINCC Frameworks Python Project Template](https://github.com/lincc-frameworks/python-project-template)
v2.3.0, commit `6b22d83030624c9177a0d0933c30f1e340f6b23e`.
Copier 9.19.1 rendered the selected configuration; Copier 9.1 or newer is required.
The generated `.copier-answers.yml` records the upstream URL, release, and choices.
The files were reconciled with the existing application rather than replacing it.

## Intentional customizations

- Preserve the MIT license, CLI, pandas/pyarrow dependencies, and packaged browser assets.
- Keep setuptools at least 77 and use setuptools-scm with a generated `_version.py`.
- Keep focused pytest discovery and importlib mode; do not collect all module docstrings.
- Use a small Ruff rule set, a matched Ruff version in dev dependencies and hooks,
  and explicit test commands rather than running every test on each commit.
- Retain Node tests, public-history/distribution audits, and installed-package smoke tests.
- Use the Linux Python matrix plus macOS, and store coverage as GitHub artifacts.
- Use Markdown user guides with Sphinx/AutoAPI and MyST; build docs in CI.
- Omit example code, sample version CLI, generated agent instructions, notebooks/Jupyter,
  benchmarks, mypy, Codecov, custom notifications, and automatic PyPI publication.
- Keep template-update checks manual; no network version check runs on every commit.

These choices differ from some upstream defaults and must survive future updates.
Source-layout and linting changes were reviewed separately from documentation.

## Updating

Start from a clean feature branch. Install Copier in a separate tool environment,
choose a reviewed upstream release, and run:

```sh
copier update --vcs-ref=<reviewed-release> --skip-tasks
```

Replace the placeholder with the chosen tag. Review the full diff and resolve
any `.rej` files; do not commit rejection files. Retain this customization list,
runtime dependencies, resource packaging, and public-data checks. Check for
regenerated examples, agent instructions, release workflows, and unnecessary
dependencies. Do not run the template's new-project initialization script here.

Reinstall, run all development and distribution checks, and inspect the rendered
docs before merging. Updating a template does not publish a package or website.
Consult LINCC's [update guide](https://lincc-ppt.readthedocs.io/en/latest/source/update_project.html).
