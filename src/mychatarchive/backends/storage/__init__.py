"""Storage backend protocol.

Any storage backend must implement these functions as module-level callables.
The default is 'sqlite' which uses SQLite + sqlite-vec.

Sensitivity scope: every content-returning function takes a keyword-only
``scope`` tuple of sensitivity levels and must return only rows whose
sensitivity is in that scope. The default is ``("public",)`` — a caller that
omits scope fails closed to public. Valid levels: public, private, sealed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pathlib import Path

SENSITIVITY_LEVELS = ("public", "private", "sealed")
DEFAULT_SCOPE = ("public",)


@runtime_checkable
class StorageBackend(Protocol):
    """Defines the interface every storage backend must satisfy."""

    def get_connection(self, db_path: Path): ...

    def ensure_schema(self, con) -> None: ...

    # Ingestion
    def insert_message(
        self, con, message_id: str, canonical_thread_id: str,
        platform: str, account_id: str, ts: str, role: str,
        text: str, title: str, source_id: str,
        *, sensitivity: str = "public",
    ) -> bool: ...

    # Counts
    def message_count(self, con) -> int: ...
    def chunk_count(self, con) -> int: ...
    def thought_count(self, con) -> int: ...
    def thread_count(self, con) -> int: ...
    def platform_counts(self, con) -> list[tuple[str, int]]: ...

    # Iterators
    def iter_messages(
        self, con, batch_size: int = 1000, *, scope: tuple = DEFAULT_SCOPE,
    ) -> Iterator[dict]: ...
    def embedded_message_ids(self, con) -> set[str]: ...
    def iter_threads(self, con, *, scope: tuple = DEFAULT_SCOPE) -> Iterator[dict]: ...
    def get_thread_messages(
        self, con, canonical_thread_id: str, *, scope: tuple = DEFAULT_SCOPE,
    ) -> list[dict]: ...

    # Chunks & thoughts
    def insert_chunk(
        self, con, chunk_id: str, message_id: Optional[str],
        thread_id: str, chunk_index: int, text: str,
        ts_start: str, ts_end: str, embedding: list[float],
        meta: Optional[dict] = None, *, sensitivity: str = "public",
    ) -> None: ...

    def insert_thought(
        self, con, thought_id: str, text: str, created_at: str,
        embedding: list[float], meta: Optional[dict] = None,
        *, sensitivity: str = "public",
    ) -> None: ...

    # Search
    def search_chunks(
        self, con, embedding: list[float], limit: int = 10,
        platform: str | list[str] | None = None,
        cutoff_iso: str | None = None,
        sort_by_time: bool = False,
        group_thread_ids: set[str] | None = None,
        *, scope: tuple = DEFAULT_SCOPE,
    ) -> list: ...
    def search_thoughts(
        self, con, embedding: list[float], limit: int = 10,
        *, scope: tuple = DEFAULT_SCOPE,
    ) -> list: ...
    def fts_search(
        self, con, query: str, limit: int = 20,
        platform: str | list[str] | None = None,
        cutoff_iso: str | None = None,
        sort_by_time: bool = False,
        group_thread_ids: set[str] | None = None,
        *, scope: tuple = DEFAULT_SCOPE,
    ) -> list: ...
    def search_thread_summaries(
        self, con, embedding: list[float], limit: int = 10,
        *, scope: tuple = DEFAULT_SCOPE,
    ) -> list: ...

    # Retrieval
    def get_recent_chunks(
        self, con, cutoff_iso: str, limit: int = 20,
        platform: str | list[str] | None = None,
        *, scope: tuple = DEFAULT_SCOPE,
    ) -> list: ...
    def get_recent_thoughts(
        self, con, cutoff_iso: str, limit: int = 20,
        *, scope: tuple = DEFAULT_SCOPE,
    ) -> list: ...
    def get_chunk_by_id(self, con, chunk_id: str, *, scope: tuple = DEFAULT_SCOPE): ...
    def get_thought_by_id(self, con, thought_id: str, *, scope: tuple = DEFAULT_SCOPE): ...

    # Export
    def export_messages(
        self, con, platform: Optional[str] = None, limit: Optional[int] = None,
        *, scope: tuple = DEFAULT_SCOPE,
    ) -> list[dict]: ...
    def export_thoughts(self, con, *, scope: tuple = DEFAULT_SCOPE) -> list[dict]: ...

    # Thread summaries
    def get_thread_summary(
        self, con, canonical_thread_id: str, *, scope: tuple = DEFAULT_SCOPE,
    ): ...
    def get_thread_summaries(
        self, con, canonical_thread_id: str, *, scope: tuple = DEFAULT_SCOPE,
    ) -> list: ...
    def get_summary_by_id(self, con, summary_id: str, *, scope: tuple = DEFAULT_SCOPE): ...
    def list_thread_summaries(
        self, con, limit: int = 100,
        platform: str | list[str] | None = None,
        since_iso: Optional[str] = None,
        *, scope: tuple = DEFAULT_SCOPE,
    ) -> list: ...

    # Groups (thread metadata reads honor scope; membership admin does not)
    def get_threads_in_group(
        self, con, group_id: str, *, scope: tuple = DEFAULT_SCOPE,
    ) -> list[dict]: ...

    # Sensitivity classification
    def set_thread_sensitivity(self, con, thread_ids: list[str], level: str) -> dict: ...
    def get_thread_sensitivity(self, con, canonical_thread_id: str): ...
    def sensitivity_counts(self, con) -> dict: ...
    def sealed_exists(self, con) -> bool: ...
    def threads_before(self, con, cutoff_iso: str) -> list[str]: ...
    def reconcile_thread_sensitivity(self, con) -> int: ...
