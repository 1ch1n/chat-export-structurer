"""Tests for the v0.4.0 sensitivity layer: the schema-v3 migration on a
populated database, fail-closed scope defaults across the data access layer,
and thread-level classification helpers."""

from pathlib import Path

import pytest

from mychatarchive.backends.storage import sqlite as store
from mychatarchive.config import get_embedding_dim

DIM = get_embedding_dim()


def _vec(seed: float) -> list[float]:
    return [seed] * DIM


@pytest.fixture
def env(tmp_path):
    """Fresh archive in an isolated directory (backups land next to the db)."""
    db_path = tmp_path / "archive.sqlite"
    con = store.get_connection(db_path)
    store.ensure_schema(con)
    yield con, db_path, tmp_path
    con.close()


def _msg(con, mid, thread, text, ts="2024-01-01T00:00:00", platform="chatgpt"):
    store.insert_message(con, mid, thread, platform, "main", ts, "user", text,
                         f"Title {thread}", "src")
    con.commit()


def _seed_three_levels(con):
    """One thread per level, each with a message + chunk + summary, plus one
    thought per level. Sealed/private set via the real classification path."""
    for tid, seed in (("pub", 0.1), ("priv", 0.2), ("seal", 0.9)):
        _msg(con, f"m-{tid}", tid, f"{tid} SECRET-{tid.upper()} content")
        store.insert_chunk(con, f"c-{tid}", f"m-{tid}", tid, 0,
                           f"{tid} SECRET-{tid.upper()} content",
                           "2024-01-01T00:00:00", "2024-01-01T00:00:00",
                           _vec(seed), {"role": "user", "title": f"Title {tid}"})
        store.insert_thread_summary(
            con, f"{tid}::0000", tid, 0, f"Title {tid}", "chatgpt", 1, 100,
            "2024-01-01T00:00:00", "2024-01-01T00:00:00",
            f"summary of {tid} SECRET-{tid.upper()}", ["topic"], "test-model",
            "2024-01-01T00:00:00",
        )
        store.insert_thread_summary_embedding(con, f"{tid}::0000", _vec(seed))
        store.insert_thought(con, f"th-{tid}", f"thought SECRET-{tid.upper()}",
                             "2024-01-01T00:00:00", _vec(seed))
    con.commit()
    store.set_thread_sensitivity(con, ["priv"], "private")
    store.set_thread_sensitivity(con, ["seal"], "sealed")
    con.execute("UPDATE thoughts SET sensitivity='private' WHERE thought_id='th-priv'")
    con.execute("UPDATE thoughts SET sensitivity='sealed' WHERE thought_id='th-seal'")
    con.commit()


# ── Migration on a populated database ─────────────────────────────────────────

_V2_SCHEMA = """
CREATE TABLE messages (
    message_id TEXT PRIMARY KEY, canonical_thread_id TEXT NOT NULL,
    platform TEXT NOT NULL, account_id TEXT NOT NULL, ts TEXT NOT NULL,
    role TEXT NOT NULL, text TEXT NOT NULL, title TEXT, source_id TEXT NOT NULL
);
CREATE TABLE chunks (
    chunk_id TEXT PRIMARY KEY, message_id TEXT, canonical_thread_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL, text TEXT NOT NULL, ts_start TEXT, ts_end TEXT, meta TEXT
);
CREATE TABLE thoughts (
    thought_id TEXT PRIMARY KEY, text TEXT NOT NULL, created_at TEXT NOT NULL, meta TEXT
);
CREATE TABLE thread_summaries (
    summary_id TEXT PRIMARY KEY, canonical_thread_id TEXT NOT NULL,
    segment_index INTEGER NOT NULL DEFAULT 0, title TEXT, platform TEXT,
    message_count INTEGER, segment_chars INTEGER, ts_start TEXT, ts_end TEXT,
    summary TEXT NOT NULL, key_topics TEXT, summary_model TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
INSERT INTO messages VALUES ('m1','t1','chatgpt','main','2024-01-01','user','hello','T','s');
INSERT INTO chunks VALUES ('c1','m1','t1',0,'hello','2024-01-01','2024-01-01','{}');
INSERT INTO thoughts VALUES ('th1','a thought','2024-01-01','{}');
INSERT INTO thread_summaries VALUES
    ('t1::0000','t1',0,'T','chatgpt',1,5,'2024-01-01','2024-01-01','sum','[]','m','2024-01-01','2024-01-01');
"""


def _build_v2_db(tmp_path: Path):
    db_path = tmp_path / "old.sqlite"
    con = store.get_connection(db_path)
    con.executescript(_V2_SCHEMA)
    con.commit()
    return con, db_path


def test_migration_adds_column_and_defaults_public(tmp_path):
    con, db_path = _build_v2_db(tmp_path)
    store.ensure_schema(con)

    for table in ("messages", "chunks", "thoughts", "thread_summaries"):
        cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
        assert "sensitivity" in cols, table
        rows = con.execute(f"SELECT sensitivity FROM {table}").fetchall()
        assert rows and all(r[0] == "public" for r in rows), table

    # No data loss
    assert con.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM chunks").fetchone()[0] == 1

    version = con.execute(
        "SELECT value FROM archive_meta WHERE key='schema_version'"
    ).fetchone()[0]
    assert version == "3"
    con.close()


def test_migration_creates_verified_backup_and_is_idempotent(tmp_path):
    con, db_path = _build_v2_db(tmp_path)
    store.ensure_schema(con)

    backups = list(tmp_path.glob("old.pre-v3-*.backup.sqlite"))
    assert len(backups) == 1
    assert backups[0].stat().st_size > 0

    # Second run: no new backup, no error
    store.ensure_schema(con)
    assert len(list(tmp_path.glob("old.pre-v3-*.backup.sqlite"))) == 1
    con.close()


def test_fresh_db_never_creates_backup(env):
    con, db_path, tmp_path = env
    assert list(tmp_path.glob("*.backup.sqlite")) == []


def test_backup_failure_refuses_migration(tmp_path, monkeypatch):
    con, db_path = _build_v2_db(tmp_path)

    def _no_connect(*a, **kw):
        raise OSError("disk full")

    # The backup destination is opened via the backend module's sqlite3.connect.
    monkeypatch.setattr(store.sqlite3, "connect", _no_connect)
    with pytest.raises(Exception):
        store.ensure_schema(con)

    cols = {r[1] for r in con.execute("PRAGMA table_info(messages)").fetchall()}
    assert "sensitivity" not in cols  # migration must not proceed unbacked-up
    con.close()


# ── Fail-closed scope defaults ────────────────────────────────────────────────

def test_search_chunks_defaults_to_public_only(env):
    con, *_ = env
    _seed_three_levels(con)
    ids = {c for c, _ in store.search_chunks(con, _vec(0.9), limit=10)}
    assert ids == {"c-pub"}
    ids = {c for c, _ in store.search_chunks(
        con, _vec(0.9), limit=10, scope=("public", "private"))}
    assert ids == {"c-pub", "c-priv"}
    ids = {c for c, _ in store.search_chunks(
        con, _vec(0.9), limit=10, scope=store.SENSITIVITY_LEVELS)}
    assert "c-seal" in ids


def test_fts_search_defaults_to_public_only(env):
    con, *_ = env
    _seed_three_levels(con)
    assert all(r[2] == "pub" for r in store.fts_search(con, "SECRET"))
    threads = {r[2] for r in store.fts_search(con, "SECRET", scope=("public", "private"))}
    assert threads == {"pub", "priv"}
    threads = {r[2] for r in store.fts_search(con, "SECRET", scope=store.SENSITIVITY_LEVELS)}
    assert threads == {"pub", "priv", "seal"}


def test_fts_search_empty_group_set_returns_nothing(env):
    con, *_ = env
    _seed_three_levels(con)
    assert store.fts_search(con, "SECRET", group_thread_ids=set(),
                            scope=store.SENSITIVITY_LEVELS) == []


def test_recent_chunks_scoped_in_both_branches(env):
    con, *_ = env
    _seed_three_levels(con)
    # no-platform branch (no JOIN)
    ids = {r[0] for r in store.get_recent_chunks(con, "2020-01-01")}
    assert ids == {"c-pub"}
    # platform branch (JOIN messages)
    ids = {r[0] for r in store.get_recent_chunks(con, "2020-01-01", platform="chatgpt")}
    assert ids == {"c-pub"}
    ids = {r[0] for r in store.get_recent_chunks(
        con, "2020-01-01", platform="chatgpt", scope=store.SENSITIVITY_LEVELS)}
    assert ids == {"c-pub", "c-priv", "c-seal"}


def test_direct_id_lookups_enforce_scope(env):
    con, *_ = env
    _seed_three_levels(con)
    assert store.get_chunk_by_id(con, "c-seal") is None
    assert store.get_thought_by_id(con, "th-seal") is None
    assert store.get_summary_by_id(con, "seal::0000") is None
    assert store.get_thread_summary(con, "seal") is None
    assert store.get_thread_summaries(con, "seal") == []
    # Explicit full scope reaches them (CLI-only path)
    assert store.get_chunk_by_id(con, "c-seal", scope=store.SENSITIVITY_LEVELS) is not None
    assert store.get_chunk_by_id(con, "c-priv", scope=("public", "private")) is not None


def test_thoughts_and_summary_search_scoped(env):
    con, *_ = env
    _seed_three_levels(con)
    ids = {t for t, _ in store.search_thoughts(con, _vec(0.9), limit=10)}
    assert ids == {"th-pub"}
    ids = {s for s, _ in store.search_thread_summaries(con, _vec(0.9), limit=10)}
    assert ids == {"pub::0000"}
    ids = {r[0] for r in store.get_recent_thoughts(con, "2020-01-01")}
    assert ids == {"th-pub"}


def test_exports_scoped(env):
    con, *_ = env
    _seed_three_levels(con)
    assert {m["thread_id"] for m in store.export_messages(con)} == {"pub"}
    assert {t["thought_id"] for t in store.export_thoughts(con)} == {"th-pub"}
    all_msgs = store.export_messages(con, scope=store.SENSITIVITY_LEVELS)
    assert {m["thread_id"] for m in all_msgs} == {"pub", "priv", "seal"}
    assert all("sensitivity" in m for m in all_msgs)


def test_iter_threads_hides_mixed_threads_entirely(env):
    con, *_ = env
    _seed_three_levels(con)
    # Add a second, public message to the private thread → mixed thread
    _msg(con, "m-priv-2", "priv", "innocuous followup")
    assert {t["canonical_thread_id"] for t in store.iter_threads(con)} == {"pub"}
    in_scope = {t["canonical_thread_id"]: t["sensitivity"]
                for t in store.iter_threads(con, scope=("public", "private"))}
    assert set(in_scope) == {"pub", "priv"}
    assert in_scope["priv"] == "private"


def test_get_thread_messages_scoped(env):
    con, *_ = env
    _seed_three_levels(con)
    assert store.get_thread_messages(con, "seal") == []
    assert len(store.get_thread_messages(con, "seal",
                                         scope=store.SENSITIVITY_LEVELS)) == 1


def test_invalid_scope_raises(env):
    con, *_ = env
    _seed_three_levels(con)
    with pytest.raises(ValueError):
        store.search_chunks(con, _vec(0.1), scope=("public", "secret"))
    with pytest.raises(ValueError):
        store.fts_search(con, "SECRET", scope=())
    with pytest.raises(ValueError):
        store.insert_thought(con, "x", "t", "2024-01-01", _vec(0.1),
                             sensitivity="classified")


def test_list_thread_summaries_accepts_platform_list(env):
    con, *_ = env
    _seed_three_levels(con)
    # Regression: get_profile passes a parsed list; single-equality broke this.
    rows = store.list_thread_summaries(con, platform=["chatgpt", "anthropic"],
                                       scope=store.SENSITIVITY_LEVELS)
    assert {r[1] for r in rows} == {"pub", "priv", "seal"}


# ── Classification helpers ────────────────────────────────────────────────────

def test_set_thread_sensitivity_cascades_and_counts(env):
    con, *_ = env
    _seed_three_levels(con)
    counts = store.set_thread_sensitivity(con, ["pub"], "private")
    assert counts == {"messages": 1, "chunks": 1, "thread_summaries": 1}
    assert store.get_thread_sensitivity(con, "pub") == ("private", 1)


def test_sensitivity_counts_and_sealed_exists(env):
    con, *_ = env
    assert store.sealed_exists(con) is False
    _seed_three_levels(con)
    assert store.sealed_exists(con) is True
    counts = store.sensitivity_counts(con)
    assert counts["threads"] == {"public": 1, "private": 1, "sealed": 1}
    assert counts["messages"]["sealed"] == 1


def test_threads_before_uses_last_message(env):
    con, *_ = env
    _msg(con, "a1", "old", "x", ts="2020-01-01T00:00:00")
    _msg(con, "b1", "spanning", "x", ts="2020-01-01T00:00:00")
    _msg(con, "b2", "spanning", "x", ts="2025-01-01T00:00:00")
    assert store.threads_before(con, "2024-01-01") == ["old"]


def test_reconcile_raises_new_rows_to_thread_level(env):
    con, *_ = env
    _seed_three_levels(con)
    # Re-sync adds a new message to the sealed thread — lands public by default
    _msg(con, "m-seal-2", "seal", "newly imported into sealed thread")
    row = con.execute(
        "SELECT sensitivity FROM messages WHERE message_id='m-seal-2'"
    ).fetchone()
    assert row[0] == "public"

    raised = store.reconcile_thread_sensitivity(con)
    assert raised == 1
    row = con.execute(
        "SELECT sensitivity FROM messages WHERE message_id='m-seal-2'"
    ).fetchone()
    assert row[0] == "sealed"

    # All-public threads are never touched, and reconcile never lowers levels
    assert store.reconcile_thread_sensitivity(con) == 0
    assert store.get_thread_sensitivity(con, "pub") == ("public", 1)


# ── Uncapped thread-id lookup for bulk classification (bug: 10k row cap) ──────

def test_fts_search_thread_ids_scoped(env):
    con, *_ = env
    _seed_three_levels(con)
    ids = set(store.fts_search_thread_ids(con, "SECRET"))
    assert ids == {"pub"}
    ids = set(store.fts_search_thread_ids(con, "SECRET", scope=("public", "private")))
    assert ids == {"pub", "priv"}
    ids = set(store.fts_search_thread_ids(con, "SECRET", scope=store.SENSITIVITY_LEVELS))
    assert ids == {"pub", "priv", "seal"}


def test_fts_search_thread_ids_empty_group_set_returns_nothing(env):
    con, *_ = env
    _seed_three_levels(con)
    assert store.fts_search_thread_ids(con, "SECRET", group_thread_ids=set(),
                                       scope=store.SENSITIVITY_LEVELS) == []


def test_fts_search_thread_ids_covers_every_matching_thread_beyond_any_row_cap(env):
    """Regression for the classify --query row cap: a message-row-limited
    search (the old ``fts_search(..., limit=N)`` approach) can silently miss
    threads whose matches fall outside the cap, even though thread count is
    far smaller than message count. The dedicated thread-id lookup must
    return every matching thread regardless of how many messages match.

    N=25 threads is a synthetic stand-in for "more threads than any row cap"
    -- the assertion pattern (row-limited search misses threads that the
    uncapped lookup finds) is what would have failed against the real
    limit=10000 cap on a large archive; testing at N=10000 would just make
    this test slow without proving anything more.
    """
    con, *_ = env
    n = 25
    cap = 10
    for i in range(n):
        _msg(con, f"m-{i}", f"thread-{i}", "WIDGET rollout notes")

    # Old approach: derive thread ids from a row-limited message search.
    capped_rows = store.fts_search(con, "WIDGET", limit=cap, scope=store.SENSITIVITY_LEVELS)
    capped_thread_ids = {row[2] for row in capped_rows}
    assert len(capped_rows) == cap
    assert len(capped_thread_ids) < n  # some threads silently dropped by the cap

    # New approach: dedicated uncapped thread-id lookup finds all of them.
    all_thread_ids = set(store.fts_search_thread_ids(con, "WIDGET", scope=store.SENSITIVITY_LEVELS))
    assert all_thread_ids == {f"thread-{i}" for i in range(n)}
