# Versions and release checks

`setuptools-scm` derives the version from Git tags and writes the ignored
`src/astro_stampede/_version.py` during installation/build. The package exports
that value as `astro_stampede.__version__`; it must match distribution metadata.
Do not edit generated versions or add a hardcoded project version.

Use `vMAJOR.MINOR.PATCH` tags. Untagged commits produce development versions;
dirty trees can include a date suffix. They are not published releases. At adoption,
the repository had no release tags, so an untagged version need not read `0.1.0`.
The planned first release after migration is `v0.1.1`.

## Rehearse without publishing

From a clean, committed migration checkout with the development environment active:

```sh
release_check=$(mktemp -d)
git clone --no-local . "$release_check/repo"
git -C "$release_check/repo" tag v0.1.1
cd "$release_check/repo"
python -m build --outdir dist/release-check
python tools/audit_distributions.py dist/release-check --history
python tools/check_distributions.py dist/release-check --expected-version 0.1.1
```

The tag exists only in the disposable clone. If that tag already exists, choose
the next intended version rather than moving or replacing a release tag.
The source archive is installed without `.git`, proving its embedded version
metadata survives. CI fetches full history and tags for version-sensitive jobs.

## Publishing is separate

Before an actual release, require green CI, a clean reviewed commit, passing
public-data audits, and installation checks for both distributions. Create the
release tag on that exact commit only as part of an authorized release.
This repository has no automatic PyPI upload workflow. PyPI credentials/trusted
publishing, GitHub release publication, and Read the Docs hosting are separate
setup and publication tasks. The provided Read the Docs configuration does not
create a hosted project.
