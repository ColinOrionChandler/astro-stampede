# Thumbnail identity and existing stores

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

## Existing classification stores

Default-index launches such as `astro-stampede --root .` can reconnect older
classifications automatically using the retained original index. Startup checks
every scored/reviewed ID, audit-only ID, and primary point target, and requires
unambiguous mappings with no unresolved classification conflicts. Alias
registration is transactional in the same label database; fingerprints verify
that scores, object reviews, audit events, and image points remain unchanged.
Recovery does not scan unrelated indexes or change an existing identity policy.
If evidence is incomplete, it stops and prints the paths needed for the report
below. Explicit index launches retain the manual preflight workflow.

Before first use of a legacy store, stop its writers and run a read-only audit
with explicitly selected sources that contain both old IDs and filenames:

```sh
python -m astro_stampede.compatibility --db labels.sqlite \
  --index old-index.sqlite --export rich-labels.csv --require-complete \
  --report compatibility.json
```

`--index` and `--export` are repeatable. Legacy generic manifests use
`--manifest manifest.csv --legacy-dataset original-dataset`. Empty-name export
rows are ignored; unmapped IDs and contradictory evidence are reported without
guessing. The report includes raw-table fingerprints, mapping coverage, effective
classification counts, and unresolved conflicts. Review those results before
repeating the command with `--apply`. The apply transaction rechecks evidence,
registers aliases in the **same SQLite database**, and verifies unchanged raw
score, object-review, point and audit-event rows. Repeating registration is idempotent.
`--require-complete` includes audit-only references and point targets, and refuses
to apply incomplete mappings or unresolved classification conflicts.
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

Snapshot **versions 2 and 3** preserve all historical score rows and audit events
plus the registry, aliases, provenance and policy; version 3 also includes image
points. Version 1 remains importable via `--import-snapshot`; it needs compatibility
evidence before review because it contains no filename mappings. Imports remain explicit replacements. Effective
CSV exports expose canonical IDs and recoverable names; snapshots retain the
original historical IDs. Exports also include a separate identity-conflict CSV.
