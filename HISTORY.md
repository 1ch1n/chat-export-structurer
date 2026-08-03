# Project history

A short honest record of the turns this project has taken, because version
numbers and license fields tell that story badly on their own.

**November 2025 — v0.1.0, MIT.** First public release: a straightforward
chat-export archiver. Useful, narrow, and structured around downloading
export files by hand.

**March 2026 — the "v2" rewrite.** The project was rebuilt from scratch as a
local-first AI memory archive: one SQLite file as the unit of ownership,
FTS5 + sqlite-vec search, auto-discovery of Claude Code and Cursor history,
and an MCP server so agents can read (and write to) the archive. The rewrite
landed with the version still reading 0.1.0, then was released as **v0.2.0**
— so there is no v1.x lineage; "v2" described the rewrite, not a version
number. If you cloned before March 2026 you were using a different, simpler
tool that happened to share the name.

**March 2026 — MIT → AGPL-3.0.** The license changed with the rewrite. The
reasoning, plainly: the archive engine is the core of a product I intend to
build a business around (a hosted version and paid tooling on top). AGPL
keeps the engine genuinely free for people running it on their own machines
— which is the whole point of a local-first tool — while requiring anyone
who offers it as a network service to share their changes. Code released
under MIT before the change (up to e7af8c9^) remains MIT; everything after
is AGPL-3.0. I hold sole copyright, which is what makes a future commercial
exception for hosting possible without relicensing anyone else's work.

**v0.3.0 — search that ranks, and a lean install.** Full-text search moved
to external-content FTS5 with bm25 relevance ranking (previously results
came back in ingestion order — honest bug, now fixed and tested), hostile
query input no longer errors, archives became self-describing
(`archive_meta` records the schema version and embedding model/dimension so
a config change can never silently corrupt vectors), and the ~2GB
torch/sentence-transformers stack moved to an optional `[local]` extra —
the core install (import, full-text search, MCP) is now light enough for
`uvx mychatarchive`. Existing v0.2.0 archives migrate in place on first
open.

**v0.4.0 — the archive learns to keep secrets.** An archive that holds six
years of everything is only safe to connect to agents if it can withhold
things. This release adds three sensitivity levels — public, private,
sealed — enforced in the data access layer with a fail-closed default: any
retrieval call that doesn't name a scope gets public content only, and
sealed content cannot be expressed or returned through the MCP server at
all. Behavior change to know about: `search` and `export` now return
public content only unless you pass `--include-private`/`--include-sealed`
(before this release there was no distinction, so nothing is hidden that
you didn't classify yourself). The migration ALTERs a populated database in
place, so it takes a verified backup first (`*.pre-v3-*.backup.sqlite`
beside the archive) and refuses to run without one. Deliberately not in
this release: model-assisted auto-classification and encryption at rest —
both worth doing, neither belongs in the same change as a schema migration.
