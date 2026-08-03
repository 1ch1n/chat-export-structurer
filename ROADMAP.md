# MyChatArchive -- Roadmap

## Vision

A personal AI memory system that compounds from daily usage. Start with searchable chat archives, build toward a full personal context engine -- your own Jarvis.

---

## Phase 1: Core Archive (current)

**Status:** Complete (validated 2026-03-03)

- [x] Multi-platform import (ChatGPT, Claude, Grok, Claude Code, Cursor) with auto-detection
- [x] SHA1 dedup -- re-import without duplicates
- [x] Local vector embeddings (sentence-transformers, all-MiniLM-L6-v2)
- [x] sqlite-vec for vector storage (single file, no external services)
- [x] FTS5 keyword search
- [x] MCP server: search_brain, search_recent, get_context, capture_thought, get_profile, get_current_datetime
- [x] CLI: init, import, export, embed, serve, search, info, mcp-config, summarize, groups, sources, sync --embed
- [x] Named import sources (`mychatarchive sources add/list/remove/rename`)
- [x] Batch/folder import (`mychatarchive import ./folder/` recursive scan)
- [x] `--from` flag to pull from configured sources (`--from nas`, `--from all`)
- [x] `sync` command -- one-command import from auto-discovery + drop folder + named sources
- [x] Drop folder (`~/.mychatarchive/imports/`) for drag-and-drop exports
- [x] Auto-discovery sources (Claude Code, Cursor) enabled by default
- [x] pip-installable package
- [x] End-to-end validation (12 messages, 3 platforms, 18 tests passing)
- [ ] PyPI publish

---

## Phase 2: Daily Driver + Remote Access

**Goal:** Use MyChatArchive daily across all devices -- desktop, laptop, phone -- without friction.

### Local MCP (stdio)
- [x] Connect MCP to Claude Desktop (desktop)
- [x] Connect MCP to Cursor (coding)
- [x] Weekly re-import workflow (`mychatarchive sync --embed` -- drop exports in folder, run one command)

### Remote MCP (SSE/HTTP)
- [x] Add SSE transport option to MCP server (`mychatarchive serve --transport sse --port 8420`)
- [ ] NAS deployment: run MCP server as persistent service on home NAS
- [ ] Tailscale/WireGuard tunnel for secure remote access from anywhere
- [ ] Mobile access: Claude mobile (phone) connects to NAS-hosted MCP over tunnel
- [ ] Auth layer for remote connections (API key or token-based)

### Parsers + Retrieval
- [ ] Gemini parser
- [ ] Perplexity parser
- [ ] Thread-level chunking for better context retrieval

---

## Phase 3: Analysis Engine

**Goal:** Run deep research prompts against your own archive. Not a chat -- a batch analysis tool.

- [ ] `mychatarchive analyze` CLI command
- [ ] Direct API integration (Claude API) for structured analysis
- [ ] Prompt templates: life threads, decision tracking, abandoned ideas, recurring patterns
- [ ] Cross-reference decisions vs outcomes
- [ ] Structured report output (markdown)
- [ ] Configurable scope (time range, platform, topic)

---

## Phase 4: Auto-Sync

**Goal:** Archive stays current without manual exports.

- [ ] Programmatic ChatGPT sync (reverse-engineered or official API when available)
- [ ] Programmatic Claude sync
- [ ] Background daemon / scheduled task
- [ ] Incremental import (only new conversations)
- [ ] Conflict resolution for edited conversations

---

## Phase 5: Personal OS / Jarvis

**Goal:** The AI has your full context -- memory, calendar, projects, patterns -- and acts on it.

- [ ] Read/write loop: MCP-connected conversations auto-capture back to archive
- [x] Sensitivity levels: public / private / sealed, fail-closed retrieval (`mychatarchive classify`, v0.4.0)
- [ ] Calendar integration (MCP server or direct)
- [ ] Project context linking (connect archive threads to repos/projects)
- [ ] Proactive context surfacing (AI suggests relevant history before you ask)
- [ ] Web dashboard at mychatarchive.com (browse, search, analyze -- not another chat UI)

### Cloud / Self-Hosted Option
- [ ] Docker image: `docker run mychatarchive` -- one command to spin up your own server
- [ ] mychatarchive.com hosted option for users who don't want to self-host
- [ ] End-to-end encryption: data encrypted at rest, decrypted only client-side
- [ ] Zero-knowledge architecture: hosted version can't read your data
- [ ] User-controlled encryption keys

---

## Additional Parsers (ongoing)

- [x] Claude Code (local ~/.claude/projects/ JSONL sessions)
- [x] Cursor IDE (local %APPDATA%/Cursor/ SQLite databases)
- [ ] Gemini / NotebookLM
- [ ] Perplexity
- [ ] Copilot
- [ ] Slack (AI bot conversations)
- [ ] Custom JSON schema

---

## Principles

1. **Local-first** -- no data leaves the machine unless you deploy it
2. **Single file** -- one SQLite DB, portable, backup-friendly
3. **Plug and play** -- pip install, three commands, done
4. **Compound from usage** -- the more you use AI, the smarter the archive gets
5. **Open source** -- AGPL-3.0, anyone can build on it
