"""Semantic search must return results best-match-first.

Regression guard for the v0.4.0 filtered-path bug: making scope=("public",)
the default routed every search through the post-KNN re-query, which returned
rows in table order and truncated to `limit` without restoring distance order.

These tests deliberately use hash-style chunk ids (the production format,
sha1(message_id|index)) so chunk_id order is uncorrelated with distance. With
sequential ids like c00/c01 the bug is invisible, because table order happens
to match rank.
"""

import hashlib
import math

import pytest

from mychatarchive.backends.storage import sqlite as store
from mychatarchive.config import get_embedding_dim

DIM = get_embedding_dim()
N = 60


def _chunk_id(rank: int) -> str:
    return hashlib.sha1(f"msg{rank}|0".encode()).hexdigest()[:16]


def _vec_at_angle(degrees: float) -> list[float]:
    """Unit vector `degrees` away from the query axis. Cosine distance grows
    with the angle, so rank 0 (0 degrees) is nearest."""
    theta = math.radians(degrees)
    v = [0.0] * DIM
    v[0] = math.cos(theta)
    v[1] = math.sin(theta)
    return v


@pytest.fixture
def ranked_archive(tmp_path):
    """Archive where semantic rank is known: rank r sits r*1.5 degrees out."""
    con = store.get_connection(tmp_path / "archive.sqlite")
    store.ensure_schema(con)
    for rank in range(N):
        store.insert_message(con, f"m{rank}", f"t{rank}", "chatgpt", "main",
                             "2024-01-01T00:00:00", "user", f"text {rank}", "T", "s")
        store.insert_chunk(con, _chunk_id(rank), f"m{rank}", f"t{rank}", 0,
                           f"text {rank}", "2024-01-01T00:00:00", "2024-01-01T00:00:00",
                           _vec_at_angle(rank * 1.5), {"role": "user", "title": "T"})
    con.commit()
    yield con
    con.close()


def _query():
    return _vec_at_angle(0)


def _ranks(results):
    by_id = {_chunk_id(r): r for r in range(N)}
    return [by_id[cid] for cid, _ in results]


def test_default_scope_returns_best_matches_in_order(ranked_archive):
    # The default scope makes needs_filter true, so this exercises the
    # filtered path — the one that used to lose ranking.
    results = store.search_chunks(ranked_archive, _query(), limit=5)
    assert _ranks(results) == [0, 1, 2, 3, 4]


def test_full_scope_fast_path_returns_best_matches_in_order(ranked_archive):
    results = store.search_chunks(ranked_archive, _query(), limit=5,
                                  scope=store.SENSITIVITY_LEVELS)
    assert _ranks(results) == [0, 1, 2, 3, 4]


def test_distances_ascend_with_rank(ranked_archive):
    results = store.search_chunks(ranked_archive, _query(), limit=10)
    distances = [d for _, d in results]
    assert distances == sorted(distances), "results must be ordered best-first"


def test_ranking_holds_with_other_filters(ranked_archive):
    # platform triggers the messages JOIN; cutoff triggers the ts predicate.
    results = store.search_chunks(ranked_archive, _query(), limit=5,
                                  platform="chatgpt", cutoff_iso="2020-01-01T00:00:00")
    assert _ranks(results) == [0, 1, 2, 3, 4]


def test_sort_by_time_still_sorts_by_time(ranked_archive):
    # sort_by_time is an explicit override of relevance order, so newest wins
    # even when that inverts the distance ranking. Timestamps go on the three
    # nearest chunks so they are inside the KNN candidate pool.
    con = ranked_archive
    for rank, ts in ((0, "2025-01-01T00:00:00"), (1, "2025-02-01T00:00:00"),
                     (2, "2025-03-01T00:00:00")):
        con.execute("UPDATE chunks SET ts_start = ? WHERE chunk_id = ?",
                    (ts, _chunk_id(rank)))
    con.commit()
    results = store.search_chunks(con, _query(), limit=3, sort_by_time=True)
    # Distance order would be [0, 1, 2]; newest-first must invert it.
    assert _ranks(results) == [2, 1, 0]


def test_private_content_excluded_without_widening_scope(ranked_archive):
    # Ranking fix must not weaken the fail-closed scope filter.
    con = ranked_archive
    store.set_thread_sensitivity(con, ["t0", "t1"], "private")
    results = store.search_chunks(con, _query(), limit=5)
    assert _ranks(results) == [2, 3, 4, 5, 6]
