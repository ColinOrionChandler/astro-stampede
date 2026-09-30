# Extraction verification

Verified locally on September 30, 2026. These checks validate the implementation;
code publication is recorded separately in Git history. They do not constitute a
package release or a live classification migration.

- 34 standalone tests pass in a fresh Python 3.12 environment. Coverage includes
  runtime synthetic generation, manifest validation, escaping symlinks, duplicate
  IDs, ordering inputs, namespace separation, stable identity after renaming,
  SQLite restart persistence, explicit Parquet round trips, tags, notes, audit
  events, undo, path-redacted exports, RCC product identity, Active Asteroids
  compressed duplicate identity, score bounds, and comparison scoring exclusion.
- 80 existing RCC reviewer, browser-source, reveal-context, and comparison tests
  pass against the compatibility launcher. Fixtures remain in RCC; research
  inventories were not copied into this repository.
- The exact RCC extraction staged for publication passes 78 review tests in an
  isolated checkout. Unrelated pre-existing test changes remain in the original
  working tree and are excluded from this commit.
- A built wheel was installed into a fresh environment and launched outside the
  source checkout without RCC. Its packaged HTML, JavaScript, styles, primary
  images, and explicitly mapped comparison images were served successfully.
- Browser inspection of generated synthetic pixels verified side-by-side display,
  blinking, animation, inversion, keyboard scoring, tags, notes, undo, on-demand
  reveal, export, shutdown, and a saved score after a server restart. Reveal reset
  to hidden after restart. No research images were used in the browser checks.
- Source-image hashes remain unchanged during persistence, export, and restart
  tests. Canonical research databases and snapshots were not opened or reconciled.
- Public audits cover names and contents in the nonignored working tree, staged
  versions, all local Git refs, wheel, and source archive. Both pattern checks and
  an external local private-value denylist report zero failures. That denylist and
  all generated test/demo data remain outside distributable source.
- Manual source review removed inherited deployment defaults and startup path
  logging, retained only needed identity helpers, and checked browser assets.
  RCC owns deployment defaults and retains filename-generation code. No RCC Git
  history was imported into this repository.

The local RCC compatibility command was tested after installing astro-stampede
into the existing review environment. Unrelated RCC working changes were retained;
the original reviewer source and browser assets were backed up outside both repos.

The owner confirmed sole authorship and selected the MIT license. The license is
included in the repository and package metadata.
The test scope is local single-process review; hosted collaboration, concurrent
writers, and reconciliation of live canonical stores are outside this extraction.
