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

From a clean, committed checkout with the development environment active,
install the release tools with `python -m pip install -e '.[release]'`.
Check `git status --short` is empty, then:

```sh
release_check=$(mktemp -d)
git clone --no-local . "$release_check/repo"
git -C "$release_check/repo" tag v0.1.1
cd "$release_check/repo"
python -m build --outdir dist/release-check
python -m twine check --strict dist/release-check/*
python tools/audit_distributions.py dist/release-check --history
python tools/check_distributions.py dist/release-check --expected-version 0.1.1
```

The tag exists only in the disposable clone. If that tag already exists, choose
the next intended version rather than moving or replacing a release tag.
The source archive is installed without `.git`, proving its embedded version
metadata survives. CI fetches full history and tags for version-sensitive jobs.

## v0.1.1 publication checklist

The [release notes](release-notes.md) describe the intended release. Preparation
does not create a public tag, GitHub release, TestPyPI upload, or PyPI upload.

1. Merge the reviewed release-preparation changes and require green CI on the
   resulting `main` commit. Rehearse again from that exact clean commit; a
   rehearsal from a PR branch is a candidate, not the final release artifact.
2. Confirm access to the intended PyPI owner account, verified email, two-factor
   authentication, and an upload credential or Trusted Publisher. Do not put
   credentials in this repository or in release reports.
3. Confirm PyPI accepts the normalized project name `astro-stampede`. A missing
   public project page does not establish availability: names can be reserved.
4. Review the rendered README, metadata (MIT license, Python requirement, project
   URLs), release notes, and the wheel/source-archive contents. Require strict
   Twine checks, public-data audits, and fresh wheel and source installations.
   The smoke checks exercise browser assets, synthetic comparison images, and
   score persistence across restart.
5. When publication is authorized, tag that exact reviewed commit `v0.1.1` and
   push the tag. Never move an already published tag. Build from a clean checkout
   of the tag and repeat the checks above with `--expected-version 0.1.1`.
   Record the source commit and SHA-256 hashes of both files.
6. Upload only those two verified artifacts using the selected publication
   method. Verify PyPI metadata, version, and file hashes against the receipt,
   then install `astro-stampede==0.1.1` from PyPI in a fresh environment and run
   the installed-package smoke test. Publish a GitHub release with the notes.

### Publication method

No upload workflow is enabled in this repository. For an initial manual release,
use [Twine](https://twine.readthedocs.io/en/stable/) with a PyPI API token supplied
through a secure local credential prompt or keyring. The first upload creates
the project; a project-scoped token can be used once the project exists.
Do not pass tokens on the command line or commit them in configuration.

If GitHub Actions publication is preferred, first add a reviewed publishing
workflow and a protected `pypi` GitHub environment. Configure a
[pending Trusted Publisher](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
in the PyPI account with project name `astro-stampede`, owner
`ColinOrionChandler`, repository `astro-stampede`, the exact workflow filename,
and environment `pypi`. Require explicit approval for production publication.
A pending publisher does not reserve the project name; the first successful
upload creates the project. This is an alternative to a manual token upload,
not a prerequisite for it.

A TestPyPI rehearsal is optional and needs a separate account/publisher
configuration. TestPyPI acceptance does not establish PyPI name availability.
Read the Docs hosting is also optional; repository documentation links work
without activating a hosted documentation project.

### Preparation observations (2026-10-20)

The public PyPI and TestPyPI JSON endpoints both returned HTTP 404 for
`astro-stampede`. GitHub listed no release tags, releases, or environments.
Account ownership, pending publishers, API tokens, and PyPI name acceptance
were not verified by these public checks. Recheck these observations at release
time; they are not permanent requirements or evidence of a reserved name.
