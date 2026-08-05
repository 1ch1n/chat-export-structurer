"""Rows entering a classified thread must be classified at insert time.

Regression guard: reconcile_thread_sensitivity runs once at the end of an
import, but _flush_thread commits per thread. Relying on reconcile alone meant
new rows in a sealed thread were committed as 'public' and readable by a
concurrent MCP server for the rest of the import — permanently if the import
was interrupted before the reconcile ran.
"""

import json
import tempfile
from pathlib import Path

import pytest

from mychatarchive import db, ingest
from mychatarchive.backends.storage import sqlite as store

SENTINEL = "project-x rollout decision"


def _chatgpt_export(messages):
    """Minimal ChatGPT-format export: one conversation, given messages."""
    mapping = {}
    for i, (role, text, ts) in enumerate(messages):
        mapping[f"n_{i}"] = {
            "message": {
                "author": {"role": role},
                "content": {"content_type": "text", "parts": [text]},
                "create_time": ts,
            }
        }
    return [{"id": "conv1", "title": "Planning", "mapping": mapping}]


def _write_json(data) -> Path:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                    encoding="utf-8")
    json.dump(data, f)
    f.close()
    return Path(f.name)


FIRST_PASS = [("user", "kickoff notes", 1700000000.0),
              ("assistant", "understood", 1700000060.0)]
# Same first message => same canonical_thread_id => a genuine re-import.
SECOND_PASS = FIRST_PASS + [("user", SENTINEL, 1700000120.0)]


@pytest.fixture
def sealed_archive(tmp_path):
    """Archive with one thread, imported then sealed."""
    db_path = tmp_path / "archive.sqlite"
    ingest.run(_write_json(_chatgpt_export(FIRST_PASS)), db_path, format_name="chatgpt")

    con = db.get_connection(db_path)
    thread_id = con.execute(
        "SELECT canonical_thread_id FROM messages LIMIT 1"
    ).fetchone()[0]
    db.set_thread_sensitivity(con, [thread_id], "sealed")
    con.close()
    return db_path, thread_id


def _levels(db_path):
    con = db.get_connection(db_path)
    rows = con.execute("SELECT text, sensitivity FROM messages").fetchall()
    con.close()
    return dict(rows)


def test_reimport_into_sealed_thread_inserts_sealed(sealed_archive):
    db_path, _ = sealed_archive
    ingest.run(_write_json(_chatgpt_export(SECOND_PASS)), db_path,
               format_name="chatgpt")

    levels = _levels(db_path)
    assert SENTINEL in levels, "the new message should have been imported"
    assert levels[SENTINEL] == "sealed"
    assert set(levels.values()) == {"sealed"}


def test_interrupted_import_leaves_no_public_rows(sealed_archive, monkeypatch):
    """Crash between the per-thread commit and the end-of-run reconcile."""
    db_path, _ = sealed_archive

    def _boom(con):
        raise KeyboardInterrupt("simulated interrupt after flush")

    monkeypatch.setattr(db, "reconcile_thread_sensitivity", _boom)

    with pytest.raises(KeyboardInterrupt):
        ingest.run(_write_json(_chatgpt_export(SECOND_PASS)), db_path,
                   format_name="chatgpt")

    # The row is committed (per-thread flush) but must already be sealed —
    # reconcile never ran, so insert-time classification is the only guard.
    levels = _levels(db_path)
    assert levels.get(SENTINEL) == "sealed"
    assert "public" not in levels.values()


def test_interrupted_import_content_not_searchable(sealed_archive, monkeypatch):
    """The real consequence: an interrupted sync must not expose the content."""
    db_path, _ = sealed_archive
    monkeypatch.setattr(db, "reconcile_thread_sensitivity",
                        lambda con: (_ for _ in ()).throw(KeyboardInterrupt()))

    with pytest.raises(KeyboardInterrupt):
        ingest.run(_write_json(_chatgpt_export(SECOND_PASS)), db_path,
                   format_name="chatgpt")

    con = db.get_connection(db_path)
    assert store.fts_search(con, "project-x") == []          # default scope
    assert store.fts_search(con, "project-x",
                            scope=store.SENSITIVITY_LEVELS)   # exists when asked
    con.close()


def test_private_thread_inheritance(tmp_path):
    db_path = tmp_path / "archive.sqlite"
    ingest.run(_write_json(_chatgpt_export(FIRST_PASS)), db_path, format_name="chatgpt")
    con = db.get_connection(db_path)
    thread_id = con.execute("SELECT canonical_thread_id FROM messages LIMIT 1").fetchone()[0]
    db.set_thread_sensitivity(con, [thread_id], "private")
    con.close()

    ingest.run(_write_json(_chatgpt_export(SECOND_PASS)), db_path, format_name="chatgpt")
    assert _levels(db_path)[SENTINEL] == "private"


def test_unclassified_threads_still_import_public(tmp_path):
    db_path = tmp_path / "archive.sqlite"
    ingest.run(_write_json(_chatgpt_export(SECOND_PASS)), db_path, format_name="chatgpt")
    assert set(_levels(db_path).values()) == {"public"}
