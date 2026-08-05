"""Data access layer -- thin shim that delegates to the active storage backend.

All public functions maintain the exact same signatures as before the refactor.
Callers (ingest.py, cli.py, mcp/server.py) don't need to change.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from mychatarchive.backends import get_storage

# Sensitivity levels, least to most restricted. Duplicated here (not imported
# from a backend) so callers can reference them without eagerly importing a
# storage backend — backends load lazily by design.
SENSITIVITY_LEVELS = ("public", "private", "sealed")

# Fail-closed default: callers that don't name a scope see public rows only.
DEFAULT_SCOPE = ("public",)


def _b():
    return get_storage()


def serialize_f32(vec: list[float]) -> bytes:
    return _b().serialize_f32(vec)


def get_connection(db_path: Path):
    return _b().get_connection(db_path)


def ensure_schema(con) -> None:
    return _b().ensure_schema(con)


def insert_message(con, message_id: str, canonical_thread_id: str,
                   platform: str, account_id: str, ts: str, role: str,
                   text: str, title: str, source_id: str,
                   *, sensitivity: str = "public") -> bool:
    return _b().insert_message(
        con, message_id, canonical_thread_id, platform, account_id,
        ts, role, text, title, source_id, sensitivity=sensitivity,
    )


def message_count(con) -> int:
    return _b().message_count(con)


def chunk_count(con) -> int:
    return _b().chunk_count(con)


def thought_count(con) -> int:
    return _b().thought_count(con)


def thread_count(con) -> int:
    return _b().thread_count(con)


def platform_counts(con) -> list[tuple[str, int]]:
    return _b().platform_counts(con)


def iter_messages(con, batch_size: int = 1000, *, scope: tuple = DEFAULT_SCOPE):
    return _b().iter_messages(con, batch_size, scope=scope)


def embedded_message_ids(con) -> set[str]:
    return _b().embedded_message_ids(con)


def clear_chunks(con) -> None:
    """Delete all chunks and vectors — used by embed --force to allow clean re-embed."""
    return _b().clear_chunks(con)


def insert_chunk(con, chunk_id: str, message_id: Optional[str],
                 thread_id: str, chunk_index: int, text: str,
                 ts_start: str, ts_end: str, embedding: list[float],
                 meta: Optional[dict] = None, *, sensitivity: str = "public"):
    return _b().insert_chunk(
        con, chunk_id, message_id, thread_id, chunk_index,
        text, ts_start, ts_end, embedding, meta, sensitivity=sensitivity,
    )


def insert_thought(con, thought_id: str, text: str, created_at: str,
                   embedding: list[float], meta: Optional[dict] = None,
                   *, sensitivity: str = "public"):
    return _b().insert_thought(con, thought_id, text, created_at, embedding, meta,
                               sensitivity=sensitivity)


def search_chunks(
    con,
    embedding: list[float],
    limit: int = 10,
    platform: str | list[str] | None = None,
    cutoff_iso: str | None = None,
    sort_by_time: bool = False,
    group_thread_ids: set | None = None,
    *,
    scope: tuple = DEFAULT_SCOPE,
):
    return _b().search_chunks(
        con, embedding, limit=limit, platform=platform,
        cutoff_iso=cutoff_iso, sort_by_time=sort_by_time,
        group_thread_ids=group_thread_ids, scope=scope,
    )


def search_thoughts(con, embedding: list[float], limit: int = 10, *,
                    scope: tuple = DEFAULT_SCOPE):
    return _b().search_thoughts(con, embedding, limit, scope=scope)


def fts_search(
    con,
    query: str,
    limit: int = 20,
    platform: str | list[str] | None = None,
    cutoff_iso: str | None = None,
    sort_by_time: bool = False,
    group_thread_ids: set | None = None,
    *,
    scope: tuple = DEFAULT_SCOPE,
):
    return _b().fts_search(
        con, query, limit=limit, platform=platform,
        cutoff_iso=cutoff_iso, sort_by_time=sort_by_time,
        group_thread_ids=group_thread_ids, scope=scope,
    )


def fts_search_thread_ids(
    con,
    query: str,
    platform: str | list[str] | None = None,
    cutoff_iso: str | None = None,
    group_thread_ids: set | None = None,
    *,
    scope: tuple = DEFAULT_SCOPE,
) -> list[str]:
    """Distinct thread ids with at least one FTS match, uncapped.

    Unlike fts_search (which caps rows via `limit` for interactive display),
    this has no row cap — it's for callers (bulk classification) that need
    every matching thread, not just the top-N matching messages.
    """
    return _b().fts_search_thread_ids(
        con, query, platform=platform, cutoff_iso=cutoff_iso,
        group_thread_ids=group_thread_ids, scope=scope,
    )


def get_recent_chunks(
    con,
    cutoff_iso: str,
    limit: int = 20,
    platform: str | list[str] | None = None,
    *,
    scope: tuple = DEFAULT_SCOPE,
):
    return _b().get_recent_chunks(con, cutoff_iso, limit=limit, platform=platform, scope=scope)


def get_recent_thoughts(con, cutoff_iso: str, limit: int = 20, *,
                        scope: tuple = DEFAULT_SCOPE):
    return _b().get_recent_thoughts(con, cutoff_iso, limit, scope=scope)


def get_chunk_by_id(con, chunk_id: str, *, scope: tuple = DEFAULT_SCOPE):
    return _b().get_chunk_by_id(con, chunk_id, scope=scope)


def get_thought_by_id(con, thought_id: str, *, scope: tuple = DEFAULT_SCOPE):
    return _b().get_thought_by_id(con, thought_id, scope=scope)


def export_messages(con, platform: str | None = None, limit: int | None = None,
                    *, scope: tuple = DEFAULT_SCOPE):
    return _b().export_messages(con, platform=platform, limit=limit, scope=scope)


def export_thoughts(con, *, scope: tuple = DEFAULT_SCOPE):
    return _b().export_thoughts(con, scope=scope)


# ── Thread summaries ──────────────────────────────────────────────────────────

def iter_threads(con, *, scope: tuple = DEFAULT_SCOPE):
    return _b().iter_threads(con, scope=scope)


def get_thread_messages(con, canonical_thread_id: str, *,
                        scope: tuple = DEFAULT_SCOPE) -> list[dict]:
    return _b().get_thread_messages(con, canonical_thread_id, scope=scope)


def has_thread_summary(con, canonical_thread_id: str) -> bool:
    return _b().has_thread_summary(con, canonical_thread_id)


def insert_thread_summary(
    con,
    summary_id: str,
    canonical_thread_id: str,
    segment_index: int,
    title,
    platform,
    message_count: int,
    segment_chars: int,
    ts_start,
    ts_end,
    summary: str,
    key_topics: list,
    summary_model: str,
    now: str,
    *,
    sensitivity: str = "public",
):
    return _b().insert_thread_summary(
        con, summary_id, canonical_thread_id, segment_index, title, platform,
        message_count, segment_chars, ts_start, ts_end, summary, key_topics, summary_model, now,
        sensitivity=sensitivity,
    )


def insert_thread_summary_embedding(con, summary_id: str, embedding: list[float]):
    return _b().insert_thread_summary_embedding(con, summary_id, embedding)


def delete_thread_summaries(con, canonical_thread_id: str) -> int:
    """Delete all segments and embeddings for a thread. Returns number of segments deleted."""
    return _b().delete_thread_summaries(con, canonical_thread_id)


def get_thread_summary(con, canonical_thread_id: str, *, scope: tuple = DEFAULT_SCOPE):
    """Returns the first segment (segment_index=0) for a thread using the 10-col layout, or None."""
    return _b().get_thread_summary(con, canonical_thread_id, scope=scope)


def get_thread_summaries(con, canonical_thread_id: str, *,
                         scope: tuple = DEFAULT_SCOPE) -> list:
    """Return all segments for a thread in segment_index order (10-col layout).

    10-col: summary_id[0], canonical_thread_id[1], segment_index[2], title[3],
    platform[4], message_count[5], ts_start[6], ts_end[7], summary[8], key_topics[9].
    """
    return _b().get_thread_summaries(con, canonical_thread_id, scope=scope)


def get_summary_by_id(con, summary_id: str, *, scope: tuple = DEFAULT_SCOPE):
    """Fetch a single segment by summary_id using the 10-col layout."""
    return _b().get_summary_by_id(con, summary_id, scope=scope)


def list_thread_summaries(con, limit: int = 100, platform=None, since_iso=None,
                          *, scope: tuple = DEFAULT_SCOPE):
    """10-col layout: summary_id[0], canonical_thread_id[1], segment_index[2], title[3],
    platform[4], message_count[5], ts_start[6], ts_end[7], summary[8], key_topics[9]."""
    return _b().list_thread_summaries(con, limit=limit, platform=platform,
                                      since_iso=since_iso, scope=scope)


def search_thread_summaries(con, embedding: list[float], limit: int = 10, *,
                            scope: tuple = DEFAULT_SCOPE):
    """Returns [(summary_id, distance)]."""
    return _b().search_thread_summaries(con, embedding, limit, scope=scope)


def summary_count(con) -> int:
    """Number of summary segments."""
    return _b().summary_count(con)


def summarized_thread_count(con) -> int:
    """Number of distinct threads with at least one summary segment."""
    return _b().summarized_thread_count(con)


def unsummarized_thread_count(con) -> int:
    return _b().unsummarized_thread_count(con)


# ── Thread groups ─────────────────────────────────────────────────────────────

def create_group(con, group_id: str, name: str, description, now: str) -> bool:
    return _b().create_group(con, group_id, name, description, now)


def list_groups(con) -> list:
    return _b().list_groups(con)


def get_group_by_name(con, name: str):
    return _b().get_group_by_name(con, name)


def add_to_group(con, canonical_thread_id: str, group_id: str, now: str) -> bool:
    return _b().add_to_group(con, canonical_thread_id, group_id, now)


def remove_from_group(con, canonical_thread_id: str, group_id: str) -> bool:
    return _b().remove_from_group(con, canonical_thread_id, group_id)


def delete_group(con, group_id: str) -> bool:
    return _b().delete_group(con, group_id)


def get_threads_in_group(con, group_id: str, *,
                         scope: tuple = DEFAULT_SCOPE) -> list[dict]:
    return _b().get_threads_in_group(con, group_id, scope=scope)


def get_group_thread_ids(con, group_id: str) -> set:
    return _b().get_group_thread_ids(con, group_id)


def group_count(con) -> int:
    return _b().group_count(con)


# ── Sensitivity classification ────────────────────────────────────────────────

def set_thread_sensitivity(con, thread_ids: list, level: str) -> dict:
    """Set the level for whole threads across messages/chunks/summaries."""
    return _b().set_thread_sensitivity(con, thread_ids, level)


def get_thread_sensitivity(con, canonical_thread_id: str):
    """Return (effective_level, message_count) for a thread, or None."""
    return _b().get_thread_sensitivity(con, canonical_thread_id)


def sensitivity_counts(con) -> dict:
    """Per-level counts for messages, threads, thoughts, and summaries."""
    return _b().sensitivity_counts(con)


def sealed_exists(con) -> bool:
    return _b().sealed_exists(con)


def threads_before(con, cutoff_iso: str) -> list:
    """Thread ids whose last message predates cutoff_iso."""
    return _b().threads_before(con, cutoff_iso)


def reconcile_thread_sensitivity(con) -> int:
    """Raise newly ingested rows to their thread's already-classified level."""
    return _b().reconcile_thread_sensitivity(con)
