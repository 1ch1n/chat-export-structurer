# CLAUDE.md

Local-first AI memory archive. Import AI chat history, embed locally, serve via MCP.

## Commands

```bash
pip install -e ".[dev]"    # install (editable)
pytest                     # run tests
ruff check src/ tests/     # lint (py310, line-length 100)
```

## Privacy

Never include real archive statistics (sizes, counts, date spans), real
category keywords, personal workflow details, or machine paths in code,
comments, tests, docs, or PR text. All examples synthetic (project-x style).
Audit PR text against this before opening.

## Agent delegation

Workflows/subagents default to sonnet-class models; reserve the top model for
planning, ranking, and final review.

## Conventions

- Conventional-commit prefixes (`feat(storage):`, `fix:`, `docs:`); release
  branches `release/vX.Y.Z`; squash-merge only.
- HISTORY.md is the changelog — narrative prose, one bold-dated paragraph per
  release, honest about behavior changes.
- Version lives in `src/mychatarchive/__init__.py` and `pyproject.toml`
  (bump both together). Schema version in `backends/storage/sqlite.py`.
- All schema DDL lives in `backends/storage/sqlite.py`; migrations are
  detection-based (PRAGMA table_info / sqlite_master), idempotent, and run
  inside `ensure_schema`. Destructive migrations back up first.
- Every content-returning storage function takes keyword-only
  `scope: tuple = ("public",)` — fail-closed sensitivity filtering. New
  retrieval paths must enforce scope; sealed must remain unreachable via MCP.
- CLI handlers: heavy imports function-local, errors to stderr + exit 1,
  results to stdout, `_add_db_arg` on every db-touching subparser leaf.
- `_cmd_init` rebuilds config.json from a whitelist dict — new top-level
  config keys must be added there or `init` silently deletes them.
