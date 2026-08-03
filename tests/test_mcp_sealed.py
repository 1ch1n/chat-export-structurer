"""Sealed content must be unreachable through every MCP tool, in every
configuration — including include_private=True. Each sealed row carries a
sentinel string; if it ever appears in a tool's JSON output, the wall leaked."""

import json

import pytest

from mychatarchive.backends.storage import sqlite as store
from mychatarchive.config import get_embedding_dim
from mychatarchive.mcp import server

DIM = get_embedding_dim()
SENTINEL = "SEALED_SENTINEL_XYZ"
PRIVATE_MARK = "PRIVATE_MARK_ABC"


def _vec(seed: float) -> list[float]:
    return [seed] * DIM


@pytest.fixture
def mcp_env(tmp_path, monkeypatch):
    db_path = tmp_path / "archive.sqlite"
    con = store.get_connection(db_path)
    store.ensure_schema(con)

    for tid, seed, text in (
        ("pub", 0.5, "ordinary public conversation"),
        ("priv", 0.5, f"quiet {PRIVATE_MARK} conversation"),
        ("seal", 0.5, f"never surface {SENTINEL} anywhere"),
    ):
        store.insert_message(con, f"m-{tid}", tid, "chatgpt", "main",
                             "2024-01-01T00:00:00", "user", text, f"T-{tid}", "s")
        store.insert_chunk(con, f"c-{tid}", f"m-{tid}", tid, 0, text,
                           "2024-01-01T00:00:00", "2024-01-01T00:00:00",
                           _vec(seed), {"role": "user", "title": f"T-{tid}"})
        store.insert_thread_summary(
            con, f"{tid}::0000", tid, 0, f"T-{tid}", "chatgpt", 1, 50,
            "2024-01-01T00:00:00", "2024-01-01T00:00:00",
            f"summary: {text}", ["topic"], "test-model", "2024-01-01T00:00:00")
        store.insert_thread_summary_embedding(con, f"{tid}::0000", _vec(seed))
        store.insert_thought(con, f"th-{tid}", f"thought: {text}",
                             "2024-01-01T00:00:00", _vec(seed))
    con.commit()
    store.set_thread_sensitivity(con, ["priv"], "private")
    store.set_thread_sensitivity(con, ["seal"], "sealed")
    con.execute("UPDATE thoughts SET sensitivity='private' WHERE thought_id='th-priv'")
    con.execute("UPDATE thoughts SET sensitivity='sealed' WHERE thought_id='th-seal'")
    con.commit()

    monkeypatch.setattr(server, "_con", con)
    monkeypatch.setattr(server, "_lazy_embed", lambda text: _vec(0.5))
    yield con
    con.close()


def _all_tool_outputs(include_private: bool) -> list[str]:
    """Run every content-returning MCP tool and collect raw JSON output."""
    return [
        server.search_brain("anything", limit=50, include_private=include_private),
        server.search_recent(hours=24 * 365 * 20, limit=50,
                             include_private=include_private),
        server.get_context("anything", limit=50, include_private=include_private),
        server.get_profile(days_back=365 * 20, include_private=include_private),
    ]


@pytest.mark.parametrize("include_private", [False, True])
def test_sealed_sentinel_never_appears(mcp_env, include_private):
    for output in _all_tool_outputs(include_private):
        assert SENTINEL not in output


def test_private_gated_by_include_private(mcp_env):
    for output in _all_tool_outputs(include_private=False):
        assert PRIVATE_MARK not in output
    combined = "".join(_all_tool_outputs(include_private=True))
    assert PRIVATE_MARK in combined


def test_capture_thought_rejects_sealed(mcp_env):
    con = mcp_env
    before = con.execute("SELECT count(*) FROM thoughts").fetchone()[0]
    out = json.loads(server.capture_thought("secret", sensitivity="sealed"))
    assert "error" in out
    assert con.execute("SELECT count(*) FROM thoughts").fetchone()[0] == before


def test_capture_thought_private_roundtrip(mcp_env):
    con = mcp_env
    out = json.loads(server.capture_thought("a private idea", sensitivity="private"))
    assert out["status"] == "captured"
    row = con.execute(
        "SELECT sensitivity FROM thoughts WHERE thought_id = ?",
        (out["thought_id"],),
    ).fetchone()
    assert row[0] == "private"
    # Invisible by default, visible with include_private
    assert "a private idea" not in server.search_recent(hours=24)
    assert "a private idea" in server.search_recent(hours=24, include_private=True)


def test_scope_helper_cannot_express_sealed(mcp_env):
    assert server._scope(False) == ("public",)
    assert server._scope(True) == ("public", "private")
    assert "sealed" not in server._scope(True)
