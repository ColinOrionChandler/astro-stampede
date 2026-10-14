# RCC compatibility

RCC keeps its existing `rcc-thumbnail-review` command and deployment configuration;
it delegates to this package. Its optional `review` dependency installs the engine.
Until a distribution is published, install this checkout into the same environment
first. `astro-stampede legacy --help` exposes the compatibility layout CLI independently
of RCC. It accepts explicit root, label DB, snapshot, and export destinations, with local
`state/` defaults for omitted output paths.
The compatibility API also supports Active Asteroids queues and explicit
Rubin/Ponder comparison manifests. These parsers contain no deployment inventories.

Existing RCC and Active Asteroids score/event rows are preserved through the
identity registry and explicit [compatibility preflight](identity.md). All
launchers use the same filename identity policy by default. Pass the canonical
SQLite, snapshot and export locations together, with a fresh rebuildable index.
RCC remains responsible for filename generation; this package carries the parser
needed to recognize its observation products.
