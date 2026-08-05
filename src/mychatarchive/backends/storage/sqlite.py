"""SQLite + sqlite-vec storage backend (default).

All data lives in a single .sqlite file with FTS5 and vector search via sqlite-vec.
"""

import datetime
import json
import re
import struct
from pathlib import Path
from typing import Optional

try:
    import pysqlite3 as sqlite3  # NAS/embedded: pip install pysqlite3-binary
except ImportError:
    import sqlite3

import sqlite_vec


def _get_embedding_dim() -> int:
    from mychatarchive.config import get_embedding_dim
    return get_embedding_dim()


def serialize_f32(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


# Sensitivity levels, least to most restricted. Rows default to 'public';
# 'private' requires callers to opt in; 'sealed' is never served over MCP.
SENSITIVITY_LEVELS = ("public", "private", "sealed")

_DEFAULT_SCOPE = ("public",)


def _validate_scope(scope) -> tuple:
    """Validate a sensitivity scope, returning it as a tuple.

    Raises ValueError on unknown levels so a typo can never widen access.
    """
    scope = tuple(scope)
    if not scope:
        raise ValueError("sensitivity scope must not be empty")
    unknown = [s for s in scope if s not in SENSITIVITY_LEVELS]
    if unknown:
        raise ValueError(
            f"Unknown sensitivity level(s) {unknown}; valid: {list(SENSITIVITY_LEVELS)}"
        )
    return scope


def _validate_level(level: str) -> str:
    if level not in SENSITIVITY_LEVELS:
        raise ValueError(
            f"Unknown sensitivity level '{level}'; valid: {list(SENSITIVITY_LEVELS)}"
        )
    return level


def _scope_sql(scope, column: str = "sensitivity") -> tuple[str, tuple]:
    """Return an 'IN (?,...)' predicate + params for a validated scope."""
    scope = _validate_scope(scope)
    placeholders = ",".join("?" * len(scope))
    return f"{column} IN ({placeholders})", scope


def get_connection(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def _ensure_thread_summaries_v2(con: sqlite3.Connection, dim: int) -> None:
    """Create or migrate thread_summaries to the multi-segment schema.

    Old schema: canonical_thread_id TEXT PRIMARY KEY  (one row per thread)
    New schema: summary_id TEXT PRIMARY KEY           (one row per segment)

    summary_id format: "{canonical_thread_id}::{segment_index:04d}"

    Migration copies old rows as segment 0 of each thread. Embeddings are
    dropped and must be regenerated with 'mychatarchive summarize'.
    """
    cols = {row[1] for row in con.execute("PRAGMA table_info(thread_summaries)").fetchall()}

    if "summary_id" not in cols:
        if cols:
            # Old single-segment schema — migrate data, keep summary text
            con.executescript("""
                CREATE TABLE thread_summaries_new (
                    summary_id TEXT PRIMARY KEY,
                    canonical_thread_id TEXT NOT NULL,
                    segment_index INTEGER NOT NULL DEFAULT 0,
                    title TEXT,
                    platform TEXT,
                    message_count INTEGER,
                    segment_chars INTEGER,
                    ts_start TEXT,
                    ts_end TEXT,
                    summary TEXT NOT NULL,
                    key_topics TEXT,
                    summary_model TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    sensitivity TEXT NOT NULL DEFAULT 'public'
                        CHECK(sensitivity IN ('public','private','sealed'))
                );
                INSERT INTO thread_summaries_new
                    (summary_id, canonical_thread_id, segment_index, title, platform,
                     message_count, segment_chars, ts_start, ts_end, summary, key_topics,
                     summary_model, created_at, updated_at)
                SELECT
                    canonical_thread_id || '::0000',
                    canonical_thread_id, 0, title, platform,
                    message_count, NULL, ts_start, ts_end, summary, key_topics,
                    summary_model, created_at, updated_at
                FROM thread_summaries;
                DROP TABLE thread_summaries;
                ALTER TABLE thread_summaries_new RENAME TO thread_summaries;
            """)
        else:
            # Fresh install — create new schema directly
            con.execute("""
                CREATE TABLE thread_summaries (
                    summary_id TEXT PRIMARY KEY,
                    canonical_thread_id TEXT NOT NULL,
                    segment_index INTEGER NOT NULL DEFAULT 0,
                    title TEXT,
                    platform TEXT,
                    message_count INTEGER,
                    segment_chars INTEGER,
                    ts_start TEXT,
                    ts_end TEXT,
                    summary TEXT NOT NULL,
                    key_topics TEXT,
                    summary_model TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    sensitivity TEXT NOT NULL DEFAULT 'public'
                        CHECK(sensitivity IN ('public','private','sealed'))
                )
            """)
        # Drop old vec table (wrong PK: canonical_thread_id) and recreate with summary_id
        con.execute("DROP TABLE IF EXISTS vec_thread_summaries")
        con.execute(f"""
            CREATE VIRTUAL TABLE vec_thread_summaries
            USING vec0(summary_id TEXT PRIMARY KEY, embedding float[{dim}] distance_metric=cosine)
        """)
        con.commit()

    # Idempotent: ensure index and vec exist for already-migrated DBs
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_thread_summaries_thread
        ON thread_summaries(canonical_thread_id)
    """)
    con.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_thread_summaries
        USING vec0(summary_id TEXT PRIMARY KEY, embedding float[{dim}] distance_metric=cosine)
    """)
    con.commit()


SCHEMA_VERSION = "3"


def _detect_existing_vec_dim(con: sqlite3.Connection) -> Optional[int]:
    """Read the embedding dim baked into an existing vec_chunks table, if any.

    vec0 freezes the dimension into its DDL (``embedding float[384]``); parsing
    it back tells us the archive's real dim regardless of current config.
    """
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'vec_chunks'"
    ).fetchone()
    if not row or not row[0]:
        return None
    m = re.search(r"float\[(\d+)\]", row[0])
    return int(m.group(1)) if m else None


def _ensure_archive_meta(con: sqlite3.Connection) -> int:
    """Create the self-describing archive_meta table and return the archive's
    authoritative embedding dimension.

    archive_meta records schema_version, embedding model/dim, chunk params, and
    the writing tool version. The dimension recorded here (or, for pre-0.3.0
    archives, the dim already frozen into vec_chunks) is authoritative: if the
    currently-configured embedding dim disagrees with an existing archive, we
    raise rather than create mismatched vec tables — the exact failure mode that
    silently produced unusable vectors before this table existed.
    """
    con.execute(
        "CREATE TABLE IF NOT EXISTS archive_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    existing = dict(con.execute("SELECT key, value FROM archive_meta").fetchall())
    config_dim = _get_embedding_dim()
    frozen_dim = _detect_existing_vec_dim(con)

    # Authoritative dim: what the archive was actually built with wins over config.
    if existing.get("embedding_dim"):
        true_dim = int(existing["embedding_dim"])
    elif frozen_dim is not None:
        true_dim = frozen_dim
    else:
        true_dim = config_dim  # fresh archive — config decides

    # Guard: config points at a different-dimensioned model than this archive.
    if true_dim != config_dim:
        from mychatarchive import config as _cfg
        raise RuntimeError(
            f"Embedding dimension mismatch: this archive was built with "
            f"dim={true_dim} but the current config resolves to dim={config_dim} "
            f"(model={_cfg.get_embedding_model()}). Re-embedding with a different "
            f"model needs a fresh archive (or a future 'reindex'). Refusing to "
            f"create mismatched vector tables."
        )

    if not existing:
        from mychatarchive import config as _cfg
        try:
            from mychatarchive import __version__ as _ver
        except Exception:
            _ver = "unknown"
        meta = {
            "schema_version": SCHEMA_VERSION,
            "mychatarchive_version": str(_ver),
            "embedding_model": _cfg.get_embedding_model(),
            "embedding_dim": str(true_dim),
            "chunk_size": str(_cfg.get_chunk_size()),
            "chunk_overlap": str(_cfg.get_chunk_overlap()),
        }
        con.executemany(
            "INSERT OR IGNORE INTO archive_meta (key, value) VALUES (?, ?)",
            list(meta.items()),
        )
        con.commit()

    return true_dim


def _create_messages_fts(con: sqlite3.Connection) -> None:
    """Create the external-content FTS5 table + sync triggers.

    External content (content='messages') means the FTS index stores only the
    inverted index and reads original text from messages via rowid — enabling
    bm25() ranking, snippet()/highlight(), and native deletes, with no separate
    docid map to keep in sync. Triggers mirror INSERT/UPDATE/DELETE on messages.
    """
    con.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
            USING fts5(text, content='messages', content_rowid='rowid');

        CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, text) VALUES (new.rowid, new.text);
        END;
        CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, text)
                VALUES('delete', old.rowid, old.text);
        END;
        CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, text)
                VALUES('delete', old.rowid, old.text);
            INSERT INTO messages_fts(rowid, text) VALUES (new.rowid, new.text);
        END;
    """)


def _ensure_messages_fts_v2(con: sqlite3.Connection) -> None:
    """Create or migrate messages_fts to external-content FTS5.

    Pre-0.3.0 archives used ``fts5(text, content='')`` (contentless) plus a
    hand-maintained messages_fts_docids map, with no relevance ordering. Detect
    that by the stored CREATE SQL, drop it and the docid map, recreate as
    external-content, and rebuild the index from the messages table in place.
    Fresh archives just get the new table. Idempotent.
    """
    row = con.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'messages_fts'"
    ).fetchone()
    existing_sql = row[0] if row else None
    is_external = existing_sql is not None and "content='messages'" in existing_sql

    if existing_sql is not None and not is_external:
        # Old contentless FTS — drop it, the docid map, and any stale triggers.
        con.executescript("""
            DROP TRIGGER IF EXISTS messages_fts_ai;
            DROP TRIGGER IF EXISTS messages_fts_ad;
            DROP TRIGGER IF EXISTS messages_fts_au;
            DROP TABLE IF EXISTS messages_fts;
            DROP TABLE IF EXISTS messages_fts_docids;
        """)
        existing_sql = None

    if existing_sql is None:
        _create_messages_fts(con)
        # Populate from existing messages (no-op on a fresh empty table).
        con.execute("INSERT INTO messages_fts(messages_fts) VALUES('rebuild')")
    else:
        # Already external-content — ensure triggers exist (idempotent).
        _create_messages_fts(con)

    con.commit()


_SENSITIVITY_TABLES = ("messages", "chunks", "thoughts", "thread_summaries")


def _ensure_sensitivity_v3(con: sqlite3.Connection) -> None:
    """Add the sensitivity column to all content tables (schema v3). Idempotent.

    On a populated archive this is the only migration that ALTERs in place, so
    it backs the database file up first (sqlite backup API — a plain file copy
    would drop uncommitted WAL frames) and refuses to migrate if the backup
    cannot be verified. Fresh archives get the column from the CREATE DDL and
    never reach the backup path.
    """
    missing = []
    for table in _SENSITIVITY_TABLES:
        cols = {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
        if cols and "sensitivity" not in cols:
            missing.append(table)

    if missing:
        db_file = None
        for _, name, path in con.execute("PRAGMA database_list").fetchall():
            if name == "main" and path:
                db_file = Path(path)
                break

        if db_file is not None:
            stamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            backup_path = db_file.with_name(f"{db_file.stem}.pre-v3-{stamp}.backup.sqlite")
            dest = sqlite3.connect(str(backup_path))
            try:
                con.backup(dest)
            finally:
                dest.close()
            if not backup_path.exists() or backup_path.stat().st_size == 0:
                raise RuntimeError(
                    f"Pre-migration backup verification failed at {backup_path}; "
                    f"refusing to migrate. Free disk space and retry."
                )

        con.commit()  # close any implicit transaction before BEGIN IMMEDIATE
        con.execute("BEGIN IMMEDIATE")  # serialize concurrent openers (serve + CLI)
        for table in missing:
            cols = {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
            if "sensitivity" in cols:
                continue  # another process migrated while we waited on the lock
            con.execute(f"""
                ALTER TABLE {table} ADD COLUMN sensitivity TEXT NOT NULL DEFAULT 'public'
                    CHECK(sensitivity IN ('public','private','sealed'))
            """)
        con.execute(
            "INSERT OR REPLACE INTO archive_meta (key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        con.commit()

    # Idempotent index maintenance (all paths, including fresh installs).
    # Partial indexes: archives are overwhelmingly public, so index only the
    # exceptions — powers sealed_exists() and classify --list cheaply.
    for table in _SENSITIVITY_TABLES:
        con.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{table}_sensitivity
            ON {table}(sensitivity) WHERE sensitivity != 'public'
        """)
    # Thread-level classify UPDATEs chunks by thread; chunks had no thread index.
    con.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_thread ON chunks(canonical_thread_id)"
    )
    con.commit()


def ensure_schema(con: sqlite3.Connection):
    """Create all tables (ingestion + brain). Idempotent.

    The embedding dimension for vec0 tables is resolved from archive_meta
    (the self-describing record), not directly from config, so an archive
    always keeps the dimension it was created with.
    """
    cur = con.cursor()

    # thread_summaries is handled separately below (needs migration logic).
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            message_id TEXT PRIMARY KEY,
            canonical_thread_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            account_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            role TEXT NOT NULL,
            text TEXT NOT NULL,
            title TEXT,
            source_id TEXT NOT NULL,
            sensitivity TEXT NOT NULL DEFAULT 'public'
                CHECK(sensitivity IN ('public','private','sealed'))
        );

        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            message_id TEXT,
            canonical_thread_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            ts_start TEXT,
            ts_end TEXT,
            meta TEXT,
            sensitivity TEXT NOT NULL DEFAULT 'public'
                CHECK(sensitivity IN ('public','private','sealed'))
        );

        CREATE TABLE IF NOT EXISTS thoughts (
            thought_id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            meta TEXT,
            sensitivity TEXT NOT NULL DEFAULT 'public'
                CHECK(sensitivity IN ('public','private','sealed'))
        );

        -- User-curated thread groups (e.g. "jarvis", "coding", "projects").
        CREATE TABLE IF NOT EXISTS thread_groups (
            group_id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            created_at TEXT NOT NULL
        );

        -- Many-to-many: threads belong to one or more groups.
        CREATE TABLE IF NOT EXISTS thread_group_members (
            canonical_thread_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (canonical_thread_id, group_id)
        );

        -- Secondary indexes: the summarizer and thread iteration scan by
        -- (thread, ts); recent-chunk queries scan chunks by time. Without
        -- these, summarize is O(threads x messages) on a large archive.
        CREATE INDEX IF NOT EXISTS idx_messages_thread_ts
            ON messages(canonical_thread_id, ts);
        CREATE INDEX IF NOT EXISTS idx_chunks_ts_start
            ON chunks(ts_start);
    """)

    con.commit()

    # Self-describing metadata + embedding-dim authority. MUST run before any
    # vec0 table is created: the archive's true dim comes from here, not from
    # mutable config, so flipping the configured model can never silently build
    # wrong-dim vector tables (it raises instead).
    dim = _ensure_archive_meta(con)

    cur.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks
        USING vec0(chunk_id TEXT PRIMARY KEY, embedding float[{dim}] distance_metric=cosine)
    """)

    cur.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_thoughts
        USING vec0(thought_id TEXT PRIMARY KEY, embedding float[{dim}] distance_metric=cosine)
    """)

    con.commit()

    # Full-text search: create or migrate to external-content FTS5 (bm25
    # ranking, native deletes) kept in sync by triggers.
    _ensure_messages_fts_v2(con)

    # Thread summaries: create or migrate to multi-segment schema
    _ensure_thread_summaries_v2(con, dim)

    # Sensitivity column (schema v3). MUST run last: _ensure_thread_summaries_v2
    # table-rewrites thread_summaries and would drop a column added earlier.
    _ensure_sensitivity_v3(con)


# --- Ingestion ---

def insert_message(con: sqlite3.Connection, message_id: str, canonical_thread_id: str,
                   platform: str, account_id: str, ts: str, role: str, text: str,
                   title: str, source_id: str, *, sensitivity: str = "public") -> bool:
    """Insert a message. Returns True if inserted, False if duplicate.

    The external-content FTS index is maintained by the messages_fts_ai/au/ad
    triggers (see _create_messages_fts), so there is no manual FTS bookkeeping
    here. INSERT OR IGNORE that skips a duplicate does not fire the trigger.

    sensitivity is set at insert time so a row entering an already-classified
    thread is never briefly (or, after an interrupted import, permanently)
    readable at a wider scope than its thread.
    """
    _validate_level(sensitivity)
    cur = con.execute(
        "INSERT OR IGNORE INTO messages "
        "(message_id, canonical_thread_id, platform, account_id, ts, role, text, title, source_id, "
        "sensitivity) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (message_id, canonical_thread_id, platform, account_id, ts, role, text, title, source_id,
         sensitivity),
    )
    return cur.rowcount > 0


# --- Counts ---

def message_count(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(*) FROM messages").fetchone()[0]


def chunk_count(con: sqlite3.Connection) -> int:
    try:
        return con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def thought_count(con: sqlite3.Connection) -> int:
    try:
        return con.execute("SELECT count(*) FROM thoughts").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def thread_count(con: sqlite3.Connection) -> int:
    return con.execute("SELECT count(DISTINCT canonical_thread_id) FROM messages").fetchone()[0]


def platform_counts(con: sqlite3.Connection) -> list[tuple[str, int]]:
    return con.execute(
        "SELECT platform, count(*) FROM messages GROUP BY platform ORDER BY count(*) DESC"
    ).fetchall()


# --- Iterators ---

def iter_messages(con: sqlite3.Connection, batch_size: int = 1000, *,
                  scope: tuple = _DEFAULT_SCOPE):
    scope_sql, scope_params = _scope_sql(scope)
    cur = con.cursor()
    cur.execute(f"""
        SELECT message_id, canonical_thread_id, ts, role, text, title, sensitivity
        FROM messages WHERE {scope_sql} ORDER BY canonical_thread_id, ts
    """, scope_params)
    while True:
        rows = cur.fetchmany(batch_size)
        if not rows:
            break
        for row in rows:
            yield {
                "message_id": row[0],
                "canonical_thread_id": row[1],
                "ts": row[2],
                "role": row[3],
                "text": row[4],
                "title": row[5],
                "sensitivity": row[6],
            }


def embedded_message_ids(con: sqlite3.Connection) -> set[str]:
    try:
        return {
            row[0]
            for row in con.execute(
                "SELECT message_id FROM chunks WHERE message_id IS NOT NULL"
            ).fetchall()
        }
    except sqlite3.OperationalError:
        return set()


# --- Vector operations ---

def clear_chunks(con: sqlite3.Connection) -> None:
    """Delete all chunks and their vectors. Used by embed --force."""
    con.execute("DELETE FROM vec_chunks")
    con.execute("DELETE FROM chunks")
    con.commit()


def insert_chunk(con: sqlite3.Connection, chunk_id: str, message_id: Optional[str],
                 thread_id: str, chunk_index: int, text: str,
                 ts_start: str, ts_end: str, embedding: list[float],
                 meta: Optional[dict] = None, *, sensitivity: str = "public"):
    _validate_level(sensitivity)
    con.execute(
        "INSERT OR IGNORE INTO chunks "
        "(chunk_id, message_id, canonical_thread_id, chunk_index, text, ts_start, ts_end, meta, "
        "sensitivity) VALUES (?,?,?,?,?,?,?,?,?)",
        (chunk_id, message_id, thread_id, chunk_index, text, ts_start, ts_end,
         json.dumps(meta) if meta else None, sensitivity),
    )
    con.execute(
        "INSERT OR IGNORE INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)",
        (chunk_id, serialize_f32(embedding)),
    )


def insert_thought(con: sqlite3.Connection, thought_id: str, text: str,
                   created_at: str, embedding: list[float], meta: Optional[dict] = None,
                   *, sensitivity: str = "public"):
    _validate_level(sensitivity)
    con.execute(
        "INSERT OR IGNORE INTO thoughts (thought_id, text, created_at, meta, sensitivity) "
        "VALUES (?,?,?,?,?)",
        (thought_id, text, created_at, json.dumps(meta) if meta else None, sensitivity),
    )
    con.execute(
        "INSERT OR IGNORE INTO vec_thoughts (thought_id, embedding) VALUES (?, ?)",
        (thought_id, serialize_f32(embedding)),
    )


def search_chunks(
    con: sqlite3.Connection,
    embedding: list[float],
    limit: int = 10,
    platform: str | list[str] | None = None,
    cutoff_iso: str | None = None,
    sort_by_time: bool = False,
    group_thread_ids: set[str] | None = None,
    *,
    scope: tuple = _DEFAULT_SCOPE,
):
    # An explicit empty set means "filter to zero threads" → nothing to return.
    # Without this, bool(set()) is False, needs_filter ignores it, and we'd
    # silently return global results instead of empty — wrong semantics.
    if group_thread_ids is not None and not group_thread_ids:
        return []

    scope = _validate_scope(scope)
    scope_active = set(scope) != set(SENSITIVITY_LEVELS)
    needs_filter = bool(platform or cutoff_iso or group_thread_ids) or scope_active
    # When scoping to a small set of threads, the target chunks are unlikely to appear
    # in the global top (limit * 5) results across 90k+ chunks. Use a much larger
    # candidate pool so filtering actually finds matching chunks.
    if group_thread_ids and len(group_thread_ids) <= 3:
        fetch_limit = max(limit * 15, 100)
    elif needs_filter:
        fetch_limit = limit * 5
    else:
        fetch_limit = limit
    raw = con.execute(
        "SELECT chunk_id, distance FROM vec_chunks "
        "WHERE embedding MATCH ? AND k = ?",
        (serialize_f32(embedding), fetch_limit),
    ).fetchall()

    if not needs_filter and not sort_by_time:
        return raw[:limit]

    chunk_ids = [r[0] for r in raw]
    if not chunk_ids:
        return []

    conditions = [f"c.chunk_id IN ({','.join('?' * len(chunk_ids))})"]
    params: list = list(chunk_ids)

    need_message_join = bool(platform)

    if platform:
        platforms = [platform] if isinstance(platform, str) else platform
        placeholders = ",".join("?" * len(platforms))
        conditions.append(f"m.platform IN ({placeholders})")
        params.extend(platforms)

    if cutoff_iso:
        conditions.append("c.ts_start >= ?")
        params.append(cutoff_iso)

    if group_thread_ids:
        placeholders = ",".join("?" * len(group_thread_ids))
        conditions.append(f"c.canonical_thread_id IN ({placeholders})")
        params.extend(group_thread_ids)

    if scope_active:
        scope_sql, scope_params = _scope_sql(scope, "c.sensitivity")
        conditions.append(scope_sql)
        params.extend(scope_params)

    join_clause = " JOIN messages m ON c.message_id = m.message_id" if need_message_join else ""
    where_sql = " AND ".join(conditions)

    matching_rows = con.execute(
        f"SELECT c.chunk_id, c.ts_start FROM chunks c {join_clause} WHERE {where_sql}",
        params,
    ).fetchall()

    raw_by_id = {c: d for c, d in raw}
    matched = [(r[0], r[1], raw_by_id.get(r[0], 0)) for r in matching_rows]

    if sort_by_time:
        matched.sort(key=lambda x: x[1] or "", reverse=True)
    else:
        # The re-query above returns rows in table order, not KNN order, so
        # relevance ranking must be restored before truncating to `limit` —
        # otherwise the best match can be cut or buried. Every default search
        # takes this path (scope=("public",) makes needs_filter true).
        matched.sort(key=lambda x: x[2])

    return [(c, d) for c, ts, d in matched[:limit]]


def search_thoughts(con: sqlite3.Connection, embedding: list[float], limit: int = 10, *,
                    scope: tuple = _DEFAULT_SCOPE):
    scope = _validate_scope(scope)
    scope_active = set(scope) != set(SENSITIVITY_LEVELS)
    # vec0 can't carry filter columns: over-fetch, filter against thoughts,
    # keep KNN distance order, truncate.
    fetch_limit = limit * 5 if scope_active else limit
    raw = con.execute(
        "SELECT thought_id, distance FROM vec_thoughts "
        "WHERE embedding MATCH ? AND k = ?",
        (serialize_f32(embedding), fetch_limit),
    ).fetchall()
    if not scope_active or not raw:
        return raw[:limit]
    ids = [r[0] for r in raw]
    scope_sql, scope_params = _scope_sql(scope)
    placeholders = ",".join("?" * len(ids))
    allowed = {
        r[0] for r in con.execute(
            f"SELECT thought_id FROM thoughts WHERE thought_id IN ({placeholders}) "
            f"AND {scope_sql}",
            (*ids, *scope_params),
        ).fetchall()
    }
    return [(tid, dist) for tid, dist in raw if tid in allowed][:limit]


def _build_fts_match(query: str) -> str:
    """Turn arbitrary user text into a safe FTS5 MATCH expression.

    Each whitespace-separated token becomes a double-quoted string literal, so
    hyphens, colons, apostrophes, quotes, and FTS5 operators (AND/OR/NOT/NEAR,
    parentheses, '*', '^') are matched literally instead of raising a syntax
    error. Tokens are implicitly ANDed — the FTS5 default. Returns "" for empty
    input (caller treats that as "no results").
    """
    if not query or not isinstance(query, str):
        return ""
    tokens = query.split()
    if not tokens:
        return ""
    # FTS5 string-literal escaping: wrap in double quotes, double any internal ".
    return " ".join('"' + tok.replace('"', '""') + '"' for tok in tokens)


def fts_search(
    con: sqlite3.Connection,
    query: str,
    limit: int = 20,
    platform: str | list[str] | None = None,
    cutoff_iso: str | None = None,
    sort_by_time: bool = False,
    group_thread_ids: set[str] | None = None,
    *,
    scope: tuple = _DEFAULT_SCOPE,
):
    """Full-text search via external-content FTS5, ranked by bm25 relevance.

    Results are ordered best-match-first (bm25) unless sort_by_time is set, in
    which case the caller wants reverse-chronological. The messages row is
    joined directly on the shared rowid (no docid map).
    """
    # Explicit empty thread set means "filter to zero threads" (same guard as
    # search_chunks — without it an unknown group silently searches everything).
    if group_thread_ids is not None and not group_thread_ids:
        return []
    scope_sql, scope_params = _scope_sql(scope, "m.sensitivity")
    match = _build_fts_match(query)
    if not match:
        return []
    sql = f"""
        SELECT m.message_id, m.text, m.canonical_thread_id, m.ts, m.role, m.title
        FROM messages_fts f
        JOIN messages m ON m.rowid = f.rowid
        WHERE messages_fts MATCH ? AND {scope_sql}
    """
    params: list = [match, *scope_params]
    if platform:
        platforms = [platform] if isinstance(platform, str) else platform
        placeholders = ",".join("?" * len(platforms))
        sql += f" AND m.platform IN ({placeholders})"
        params.extend(platforms)
    if cutoff_iso:
        sql += " AND m.ts >= ?"
        params.append(cutoff_iso)
    if group_thread_ids:
        placeholders = ",".join("?" * len(group_thread_ids))
        sql += f" AND m.canonical_thread_id IN ({placeholders})"
        params.extend(group_thread_ids)
    # bm25() returns more-negative for better matches, so ascending = best first.
    sql += " ORDER BY m.ts DESC" if sort_by_time else " ORDER BY bm25(messages_fts)"
    sql += " LIMIT ?"
    params.append(limit)
    return con.execute(sql, params).fetchall()


def fts_search_thread_ids(
    con: sqlite3.Connection,
    query: str,
    platform: str | list[str] | None = None,
    cutoff_iso: str | None = None,
    group_thread_ids: set[str] | None = None,
    *,
    scope: tuple = _DEFAULT_SCOPE,
) -> list[str]:
    """Distinct thread ids with at least one FTS match, uncapped.

    Bulk classification needs every matching thread, not just the top-N
    matching messages — thread count is far smaller than message count, so
    selecting distinct thread ids with no row LIMIT is cheap and bounded
    (bounded by thread count, not match count).
    """
    if group_thread_ids is not None and not group_thread_ids:
        return []
    scope_sql, scope_params = _scope_sql(scope, "m.sensitivity")
    match = _build_fts_match(query)
    if not match:
        return []
    sql = f"""
        SELECT DISTINCT m.canonical_thread_id
        FROM messages_fts f
        JOIN messages m ON m.rowid = f.rowid
        WHERE messages_fts MATCH ? AND {scope_sql}
    """
    params: list = [match, *scope_params]
    if platform:
        platforms = [platform] if isinstance(platform, str) else platform
        placeholders = ",".join("?" * len(platforms))
        sql += f" AND m.platform IN ({placeholders})"
        params.extend(platforms)
    if cutoff_iso:
        sql += " AND m.ts >= ?"
        params.append(cutoff_iso)
    if group_thread_ids:
        placeholders = ",".join("?" * len(group_thread_ids))
        sql += f" AND m.canonical_thread_id IN ({placeholders})"
        params.extend(group_thread_ids)
    return [r[0] for r in con.execute(sql, params).fetchall()]


def get_recent_chunks(
    con: sqlite3.Connection,
    cutoff_iso: str,
    limit: int = 20,
    platform: str | list[str] | None = None,
    *,
    scope: tuple = _DEFAULT_SCOPE,
):
    # Filter on the chunk's own denormalized column in BOTH branches — the
    # no-platform branch never joins messages, so a message-side filter would
    # silently not apply there.
    scope_sql, scope_params = _scope_sql(scope, "sensitivity")
    if not platform:
        return con.execute(
            f"SELECT chunk_id, text, canonical_thread_id, ts_start, meta "
            f"FROM chunks WHERE ts_start >= ? AND {scope_sql} "
            f"ORDER BY ts_start DESC LIMIT ?",
            (cutoff_iso, *scope_params, limit),
        ).fetchall()

    platforms = [platform] if isinstance(platform, str) else platform
    placeholders = ",".join("?" * len(platforms))
    return con.execute(
        f"""
        SELECT c.chunk_id, c.text, c.canonical_thread_id, c.ts_start, c.meta
        FROM chunks c
        JOIN messages m ON c.message_id = m.message_id
        WHERE c.ts_start >= ? AND m.platform IN ({placeholders}) AND c.{scope_sql}
        ORDER BY c.ts_start DESC LIMIT ?
        """,
        (cutoff_iso, *platforms, *scope_params, limit),
    ).fetchall()


def get_recent_thoughts(con: sqlite3.Connection, cutoff_iso: str, limit: int = 20, *,
                        scope: tuple = _DEFAULT_SCOPE):
    scope_sql, scope_params = _scope_sql(scope)
    return con.execute(
        f"SELECT thought_id, text, created_at, meta "
        f"FROM thoughts WHERE created_at >= ? AND {scope_sql} "
        f"ORDER BY created_at DESC LIMIT ?",
        (cutoff_iso, *scope_params, limit),
    ).fetchall()


def get_chunk_by_id(con: sqlite3.Connection, chunk_id: str, *,
                    scope: tuple = _DEFAULT_SCOPE):
    # Direct-ID lookups enforce scope too: they are reachable with no search
    # step in front (e.g. provenance tools holding a chunk_id).
    scope_sql, scope_params = _scope_sql(scope)
    return con.execute(
        f"SELECT text, canonical_thread_id, ts_start, ts_end, meta FROM chunks "
        f"WHERE chunk_id = ? AND {scope_sql}",
        (chunk_id, *scope_params),
    ).fetchone()


def get_thought_by_id(con: sqlite3.Connection, thought_id: str, *,
                      scope: tuple = _DEFAULT_SCOPE):
    scope_sql, scope_params = _scope_sql(scope)
    return con.execute(
        f"SELECT text, created_at, meta FROM thoughts WHERE thought_id = ? AND {scope_sql}",
        (thought_id, *scope_params),
    ).fetchone()


# ── Export helpers ────────────────────────────────────────────────────────────

def export_messages(con: sqlite3.Connection, platform: Optional[str] = None,
                    limit: Optional[int] = None, *, scope: tuple = _DEFAULT_SCOPE):
    scope_sql, scope_params = _scope_sql(scope)
    conditions = [scope_sql]
    params: list = list(scope_params)
    if platform:
        conditions.append("platform = ?")
        params.append(platform)
    query = f"""
        SELECT message_id, canonical_thread_id, platform, account_id,
               ts, role, text, title, source_id, sensitivity
        FROM messages WHERE {" AND ".join(conditions)}
        ORDER BY platform, canonical_thread_id, ts
    """
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    rows = con.execute(query, params).fetchall()
    return [
        {"message_id": r[0], "thread_id": r[1], "platform": r[2], "account_id": r[3],
         "timestamp": r[4], "role": r[5], "content": r[6], "title": r[7], "source_id": r[8],
         "sensitivity": r[9]}
        for r in rows
    ]


def export_thoughts(con: sqlite3.Connection, *, scope: tuple = _DEFAULT_SCOPE):
    scope_sql, scope_params = _scope_sql(scope)
    rows = con.execute(
        f"SELECT thought_id, text, created_at, meta FROM thoughts "
        f"WHERE {scope_sql} ORDER BY created_at",
        scope_params,
    ).fetchall()
    return [{"thought_id": r[0], "content": r[1], "created_at": r[2], "metadata": r[3]}
            for r in rows]


# ── Thread iteration + summaries ─────────────────────────────────────────────

# Numeric ranks for computing a thread's effective (max) sensitivity in SQL.
_LEVEL_RANK_SQL = "CASE sensitivity WHEN 'sealed' THEN 2 WHEN 'private' THEN 1 ELSE 0 END"


def iter_threads(con: sqlite3.Connection, *, scope: tuple = _DEFAULT_SCOPE):
    """Yield one dict per unique thread with metadata aggregated from messages.

    A thread is excluded if ANY of its messages is outside scope — titles and
    aggregates leak topics, so mixed threads fail closed.
    """
    scope = _validate_scope(scope)
    max_allowed_rank = max(
        {"public": 0, "private": 1, "sealed": 2}[s] for s in scope
    )
    cur = con.execute(f"""
        SELECT canonical_thread_id,
               MAX(platform) AS platform,
               MAX(title) AS title,
               COUNT(*) AS message_count,
               MIN(ts) AS ts_start,
               MAX(ts) AS ts_end,
               MAX({_LEVEL_RANK_SQL}) AS max_rank
        FROM messages
        GROUP BY canonical_thread_id
        HAVING max_rank <= ?
        ORDER BY ts_start ASC
    """, (max_allowed_rank,))
    rank_to_level = {0: "public", 1: "private", 2: "sealed"}
    for row in cur:
        yield {
            "canonical_thread_id": row[0],
            "platform": row[1],
            "title": row[2],
            "message_count": row[3],
            "ts_start": row[4],
            "ts_end": row[5],
            "sensitivity": rank_to_level[row[6]],
        }


def get_thread_messages(con: sqlite3.Connection, canonical_thread_id: str, *,
                        scope: tuple = _DEFAULT_SCOPE) -> list[dict]:
    """Return all in-scope messages for a thread, ordered chronologically."""
    scope_sql, scope_params = _scope_sql(scope)
    rows = con.execute(
        f"SELECT role, text, ts FROM messages "
        f"WHERE canonical_thread_id = ? AND {scope_sql} ORDER BY ts",
        (canonical_thread_id, *scope_params),
    ).fetchall()
    return [{"role": r[0], "text": r[1], "ts": r[2]} for r in rows]


_SUMMARY_SELECT = """
    SELECT summary_id, canonical_thread_id, segment_index, title, platform,
           message_count, ts_start, ts_end, summary, key_topics
    FROM thread_summaries
"""
# Column indices for the fixed 10-col layout above:
#   summary_id[0], canonical_thread_id[1], segment_index[2], title[3],
#   platform[4], message_count[5], ts_start[6], ts_end[7], summary[8], key_topics[9]


def has_thread_summary(con: sqlite3.Connection, canonical_thread_id: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM thread_summaries WHERE canonical_thread_id = ?",
        (canonical_thread_id,),
    ).fetchone()
    return row is not None


def insert_thread_summary(
    con: sqlite3.Connection,
    summary_id: str,
    canonical_thread_id: str,
    segment_index: int,
    title: Optional[str],
    platform: Optional[str],
    message_count: int,
    segment_chars: int,
    ts_start: Optional[str],
    ts_end: Optional[str],
    summary: str,
    key_topics: list[str],
    summary_model: str,
    now: str,
    *,
    sensitivity: str = "public",
):
    """Insert or replace a single summary segment."""
    _validate_level(sensitivity)
    con.execute(
        """
        INSERT OR REPLACE INTO thread_summaries
            (summary_id, canonical_thread_id, segment_index, title, platform,
             message_count, segment_chars, ts_start, ts_end, summary,
             key_topics, summary_model, created_at, updated_at, sensitivity)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,
            COALESCE((SELECT created_at FROM thread_summaries WHERE summary_id=?), ?),
            ?, ?)
        """,
        (summary_id, canonical_thread_id, segment_index, title, platform,
         message_count, segment_chars, ts_start, ts_end, summary,
         json.dumps(key_topics), summary_model,
         summary_id, now, now, sensitivity),
    )


def insert_thread_summary_embedding(
    con: sqlite3.Connection, summary_id: str, embedding: list[float]
):
    """Insert or replace the embedding for a summary segment."""
    con.execute(
        "INSERT OR REPLACE INTO vec_thread_summaries (summary_id, embedding) VALUES (?,?)",
        (summary_id, serialize_f32(embedding)),
    )


def delete_thread_summaries(con: sqlite3.Connection, canonical_thread_id: str) -> int:
    """Delete all segments and embeddings for a thread. Returns number of segments deleted."""
    summary_ids = [
        r[0] for r in con.execute(
            "SELECT summary_id FROM thread_summaries WHERE canonical_thread_id = ?",
            (canonical_thread_id,),
        ).fetchall()
    ]
    if summary_ids:
        placeholders = ",".join("?" * len(summary_ids))
        con.execute(f"DELETE FROM vec_thread_summaries WHERE summary_id IN ({placeholders})", summary_ids)
    cur = con.execute(
        "DELETE FROM thread_summaries WHERE canonical_thread_id = ?",
        (canonical_thread_id,),
    )
    return cur.rowcount


def get_thread_summary(con: sqlite3.Connection, canonical_thread_id: str, *,
                       scope: tuple = _DEFAULT_SCOPE):
    """Returns the first segment (segment_index=0) for a thread using the 10-col layout, or None."""
    scope_sql, scope_params = _scope_sql(scope)
    return con.execute(
        _SUMMARY_SELECT
        + f" WHERE canonical_thread_id = ? AND {scope_sql} ORDER BY segment_index LIMIT 1",
        (canonical_thread_id, *scope_params),
    ).fetchone()


def get_thread_summaries(con: sqlite3.Connection, canonical_thread_id: str, *,
                         scope: tuple = _DEFAULT_SCOPE) -> list:
    """Return all in-scope segments for a thread in segment_index order (10-col layout).

    Returns an empty list if the thread has no summary yet.
    Use this when you need the full picture of a thread (e.g. for display),
    rather than get_thread_summary which returns only the first segment.
    """
    scope_sql, scope_params = _scope_sql(scope)
    return con.execute(
        _SUMMARY_SELECT
        + f" WHERE canonical_thread_id = ? AND {scope_sql} ORDER BY segment_index",
        (canonical_thread_id, *scope_params),
    ).fetchall()


def get_summary_by_id(con: sqlite3.Connection, summary_id: str, *,
                      scope: tuple = _DEFAULT_SCOPE):
    """Fetch a single segment by summary_id using the 10-col layout."""
    scope_sql, scope_params = _scope_sql(scope)
    return con.execute(
        _SUMMARY_SELECT + f" WHERE summary_id = ? AND {scope_sql}",
        (summary_id, *scope_params),
    ).fetchone()


def list_thread_summaries(
    con: sqlite3.Connection,
    limit: int = 100,
    platform: str | list[str] | None = None,
    since_iso: Optional[str] = None,
    *,
    scope: tuple = _DEFAULT_SCOPE,
):
    """Returns rows in the 10-col layout, one row per segment, ordered newest first.

    10-col layout: summary_id[0], canonical_thread_id[1], segment_index[2], title[3],
    platform[4], message_count[5], ts_start[6], ts_end[7], summary[8], key_topics[9].
    """
    scope_sql, scope_params = _scope_sql(scope)
    sql = _SUMMARY_SELECT
    params: list = []
    conditions = [scope_sql]
    params.extend(scope_params)
    if platform:
        # Accept a list like the other search functions (callers pass parsed
        # platform lists; a single string still works).
        platforms = [platform] if isinstance(platform, str) else list(platform)
        placeholders = ",".join("?" * len(platforms))
        conditions.append(f"platform IN ({placeholders})")
        params.extend(platforms)
    if since_iso:
        conditions.append("ts_start >= ?")
        params.append(since_iso)
    sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY ts_start DESC, segment_index ASC LIMIT ?"
    params.append(limit)
    return con.execute(sql, params).fetchall()


def search_thread_summaries(
    con: sqlite3.Connection, embedding: list[float], limit: int = 10, *,
    scope: tuple = _DEFAULT_SCOPE,
):
    """Vector KNN search on thread summaries. Returns [(summary_id, distance)]."""
    scope = _validate_scope(scope)
    scope_active = set(scope) != set(SENSITIVITY_LEVELS)
    fetch_limit = limit * 5 if scope_active else limit
    raw = con.execute(
        "SELECT summary_id, distance FROM vec_thread_summaries "
        "WHERE embedding MATCH ? AND k = ?",
        (serialize_f32(embedding), fetch_limit),
    ).fetchall()
    if not scope_active or not raw:
        return raw[:limit]
    ids = [r[0] for r in raw]
    scope_sql, scope_params = _scope_sql(scope)
    placeholders = ",".join("?" * len(ids))
    allowed = {
        r[0] for r in con.execute(
            f"SELECT summary_id FROM thread_summaries "
            f"WHERE summary_id IN ({placeholders}) AND {scope_sql}",
            (*ids, *scope_params),
        ).fetchall()
    }
    return [(sid, dist) for sid, dist in raw if sid in allowed][:limit]


def summary_count(con: sqlite3.Connection) -> int:
    """Number of summary segments (not threads)."""
    try:
        return con.execute("SELECT count(*) FROM thread_summaries").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def summarized_thread_count(con: sqlite3.Connection) -> int:
    """Number of distinct threads that have at least one summary segment."""
    try:
        return con.execute(
            "SELECT count(DISTINCT canonical_thread_id) FROM thread_summaries"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def unsummarized_thread_count(con: sqlite3.Connection) -> int:
    """Threads that have messages but no summary yet."""
    row = con.execute("""
        SELECT count(DISTINCT canonical_thread_id) FROM messages
        WHERE canonical_thread_id NOT IN (SELECT canonical_thread_id FROM thread_summaries)
    """).fetchone()
    return row[0] if row else 0


# ── Thread groups ─────────────────────────────────────────────────────────────

def create_group(
    con: sqlite3.Connection,
    group_id: str,
    name: str,
    description: Optional[str],
    now: str,
) -> bool:
    """Create a new group. Returns False if name already exists."""
    try:
        con.execute(
            "INSERT INTO thread_groups (group_id, name, description, created_at) VALUES (?,?,?,?)",
            (group_id, name, description, now),
        )
        con.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def list_groups(con: sqlite3.Connection) -> list[tuple]:
    """Returns (group_id, name, description, created_at, member_count) per group."""
    return con.execute("""
        SELECT g.group_id, g.name, g.description, g.created_at,
               COUNT(m.canonical_thread_id) AS member_count
        FROM thread_groups g
        LEFT JOIN thread_group_members m ON g.group_id = m.group_id
        GROUP BY g.group_id
        ORDER BY g.name
    """).fetchall()


def get_group_by_name(con: sqlite3.Connection, name: str):
    """Returns (group_id, name, description, created_at) or None."""
    return con.execute(
        "SELECT group_id, name, description, created_at FROM thread_groups WHERE name = ?",
        (name,),
    ).fetchone()


def add_to_group(
    con: sqlite3.Connection,
    canonical_thread_id: str,
    group_id: str,
    now: str,
) -> bool:
    """Add a thread to a group. Returns True if inserted, False if already a member."""
    cur = con.execute(
        "INSERT OR IGNORE INTO thread_group_members (canonical_thread_id, group_id, added_at) "
        "VALUES (?,?,?)",
        (canonical_thread_id, group_id, now),
    )
    return cur.rowcount > 0


def remove_from_group(con: sqlite3.Connection, canonical_thread_id: str, group_id: str) -> bool:
    cur = con.execute(
        "DELETE FROM thread_group_members WHERE canonical_thread_id = ? AND group_id = ?",
        (canonical_thread_id, group_id),
    )
    return cur.rowcount > 0


def delete_group(con: sqlite3.Connection, group_id: str) -> bool:
    """Delete a group and all its memberships."""
    con.execute("DELETE FROM thread_group_members WHERE group_id = ?", (group_id,))
    cur = con.execute("DELETE FROM thread_groups WHERE group_id = ?", (group_id,))
    con.commit()
    return cur.rowcount > 0


def get_threads_in_group(con: sqlite3.Connection, group_id: str, *,
                         scope: tuple = _DEFAULT_SCOPE) -> list[dict]:
    """Return thread metadata for in-scope members of a group.

    Mixed threads fail closed: any out-of-scope message hides the whole thread
    (titles and aggregates leak topics).
    """
    scope = _validate_scope(scope)
    max_allowed_rank = max(
        {"public": 0, "private": 1, "sealed": 2}[s] for s in scope
    )
    rows = con.execute("""
        SELECT m.canonical_thread_id,
               MAX(msgs.platform) AS platform,
               MAX(msgs.title) AS title,
               COUNT(msgs.message_id) AS message_count,
               MIN(msgs.ts) AS ts_start,
               MAX(msgs.ts) AS ts_end
        FROM thread_group_members m
        JOIN messages msgs ON msgs.canonical_thread_id = m.canonical_thread_id
        WHERE m.group_id = ?
        GROUP BY m.canonical_thread_id
        HAVING MAX(CASE msgs.sensitivity WHEN 'sealed' THEN 2 WHEN 'private' THEN 1 ELSE 0 END) <= ?
        ORDER BY ts_start DESC
    """, (group_id, max_allowed_rank)).fetchall()
    return [
        {"canonical_thread_id": r[0], "platform": r[1], "title": r[2],
         "message_count": r[3], "ts_start": r[4], "ts_end": r[5]}
        for r in rows
    ]


def get_group_thread_ids(con: sqlite3.Connection, group_id: str) -> set[str]:
    """Return the set of canonical_thread_ids belonging to a group."""
    rows = con.execute(
        "SELECT canonical_thread_id FROM thread_group_members WHERE group_id = ?",
        (group_id,),
    ).fetchall()
    return {r[0] for r in rows}


def group_count(con: sqlite3.Connection) -> int:
    try:
        return con.execute("SELECT count(*) FROM thread_groups").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


# ── Sensitivity classification ────────────────────────────────────────────────

_LEVEL_RANK = {"public": 0, "private": 1, "sealed": 2}


def set_thread_sensitivity(con: sqlite3.Connection, thread_ids: list[str],
                           level: str) -> dict:
    """Set the sensitivity level for whole threads across all content tables.

    Returns per-table update counts. Commits.
    """
    _validate_level(level)
    counts = {"messages": 0, "chunks": 0, "thread_summaries": 0}
    batch = 500  # keep IN() placeholder lists well under SQLite's variable cap
    for start in range(0, len(thread_ids), batch):
        ids = list(thread_ids[start:start + batch])
        placeholders = ",".join("?" * len(ids))
        for table in ("messages", "chunks", "thread_summaries"):
            cur = con.execute(
                f"UPDATE {table} SET sensitivity = ? "
                f"WHERE canonical_thread_id IN ({placeholders})",
                (level, *ids),
            )
            counts[table] += cur.rowcount
    con.commit()
    return counts


def get_thread_sensitivity(con: sqlite3.Connection, canonical_thread_id: str):
    """Return (effective_level, message_count) for a thread, or None if unknown.

    The effective level is the max over the thread's messages.
    """
    row = con.execute(f"""
        SELECT MAX({_LEVEL_RANK_SQL}), COUNT(*)
        FROM messages WHERE canonical_thread_id = ?
    """, (canonical_thread_id,)).fetchone()
    if not row or not row[1]:
        return None
    rank_to_level = {v: k for k, v in _LEVEL_RANK.items()}
    return (rank_to_level[row[0]], row[1])


def sensitivity_counts(con: sqlite3.Connection) -> dict:
    """Per-level row counts for messages, threads, thoughts, and summaries."""
    out: dict = {"messages": {}, "threads": {}, "thoughts": {}, "thread_summaries": {}}
    for table in ("messages", "thoughts", "thread_summaries"):
        for level, n in con.execute(
            f"SELECT sensitivity, COUNT(*) FROM {table} GROUP BY sensitivity"
        ).fetchall():
            out[table][level] = n
    rank_to_level = {v: k for k, v in _LEVEL_RANK.items()}
    for rank, n in con.execute(f"""
        SELECT max_rank, COUNT(*) FROM (
            SELECT MAX({_LEVEL_RANK_SQL}) AS max_rank
            FROM messages GROUP BY canonical_thread_id
        ) GROUP BY max_rank
    """).fetchall():
        out["threads"][rank_to_level[rank]] = n
    return out


def sealed_exists(con: sqlite3.Connection) -> bool:
    """True if any row in any content table is sealed (hits the partial indexes)."""
    for table in _SENSITIVITY_TABLES:
        try:
            if con.execute(
                f"SELECT 1 FROM {table} WHERE sensitivity = 'sealed' LIMIT 1"
            ).fetchone():
                return True
        except sqlite3.OperationalError:
            return False  # pre-v3 archive: no column → nothing sealed
    return False


def threads_before(con: sqlite3.Connection, cutoff_iso: str) -> list[str]:
    """Thread ids whose last message predates cutoff_iso (thread-scoped --before)."""
    return [
        r[0] for r in con.execute(
            "SELECT canonical_thread_id FROM messages "
            "GROUP BY canonical_thread_id HAVING MAX(ts) < ? ORDER BY MAX(ts)",
            (cutoff_iso,),
        ).fetchall()
    ]


def reconcile_thread_sensitivity(con: sqlite3.Connection) -> int:
    """Re-apply thread-level classification to rows added after the fact.

    For every thread containing at least one non-public message, raise all of
    the thread's messages/chunks/summaries to the thread's max level. Never
    lowers a level and never touches all-public threads, so it re-applies
    explicit prior classification without silently reclassifying anything.
    Returns the number of rows raised. Commits.
    """
    raised = 0
    rank_to_level = {v: k for k, v in _LEVEL_RANK.items()}
    classified = con.execute(f"""
        SELECT canonical_thread_id, MAX({_LEVEL_RANK_SQL}) AS max_rank
        FROM messages GROUP BY canonical_thread_id HAVING max_rank > 0
    """).fetchall()
    for thread_id, rank in classified:
        level = rank_to_level[rank]
        for table in ("messages", "chunks", "thread_summaries"):
            cur = con.execute(f"""
                UPDATE {table} SET sensitivity = ?
                WHERE canonical_thread_id = ?
                  AND {_LEVEL_RANK_SQL} < ?
            """, (level, thread_id, rank))
            raised += cur.rowcount
    con.commit()
    return raised
