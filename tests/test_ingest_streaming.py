"""Tests for the v0.3.0 streaming ingest: per-thread flush must produce the
same database state as the old whole-file grouping, re-imports stay
idempotent, and detection/parsing no longer materializes whole files."""

import json
import tempfile
from pathlib import Path

import pytest

from mychatarchive import db, ingest
from mychatarchive.parsers import detect_format, parse


def _chatgpt_export(conversations):
    """Build a minimal ChatGPT-format export (top-level array)."""
    out = []
    for cid, title, msgs in conversations:
        mapping = {}
        for i, (role, text, ts) in enumerate(msgs):
            mapping[f"n{cid}_{i}"] = {
                "message": {
                    "author": {"role": role},
                    "content": {"content_type": "text", "parts": [text]},
                    "create_time": ts,
                }
            }
        out.append({"id": cid, "title": title, "mapping": mapping})
    return out


@pytest.fixture
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = Path(f.name)
    yield db_path
    db_path.unlink(missing_ok=True)


def _write_json(data) -> Path:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    json.dump(data, f)
    f.close()
    return Path(f.name)


def test_streaming_ingest_basic(tmp_db):
    export = _write_json(_chatgpt_export([
        ("c1", "First", [("user", "hello there", 1700000000), ("assistant", "hi", 1700000030)]),
        ("c2", "Second", [("user", "another thread", 1700010000)]),
    ]))
    try:
        inserted, dupes = ingest.run(export, tmp_db, format_name="chatgpt")
        assert (inserted, dupes) == (3, 0)
        con = db.get_connection(tmp_db)
        assert db.message_count(con) == 3
        assert db.thread_count(con) == 2
        con.close()
    finally:
        export.unlink(missing_ok=True)


def test_reimport_is_idempotent(tmp_db):
    export = _write_json(_chatgpt_export([
        ("c1", "Idempotent", [("user", "same content", 1700000000), ("assistant", "reply", 1700000060)]),
    ]))
    try:
        first = ingest.run(export, tmp_db, format_name="chatgpt")
        second = ingest.run(export, tmp_db, format_name="chatgpt")
        assert first == (2, 0)
        assert second == (0, 2), "re-import must insert nothing and count duplicates"
    finally:
        export.unlink(missing_ok=True)


def test_noncontiguous_duplicate_thread_dedups(tmp_db):
    """The same conversation appearing twice in one export (non-contiguous
    thread_id) flushes twice but derives the same canonical id, so the second
    flush is all duplicates — matching the old grouping's net result."""
    convo = ("cdup", "Dup", [("user", "identical first message", 1700000000)])
    other = ("cother", "Other", [("user", "in between", 1700005000)])
    export = _write_json(_chatgpt_export([convo, other, convo]))
    try:
        inserted, dupes = ingest.run(export, tmp_db, format_name="chatgpt")
        assert inserted == 2, "one row per unique message"
        assert dupes == 1, "the repeated conversation dedups"
        con = db.get_connection(tmp_db)
        assert db.thread_count(con) == 2
        con.close()
    finally:
        export.unlink(missing_ok=True)


def test_empty_export_reports_nothing(tmp_db):
    export = _write_json([])
    try:
        assert ingest.run(export, tmp_db, format_name="chatgpt") == (0, 0)
    finally:
        export.unlink(missing_ok=True)


def test_detect_format_streams_first_element_only():
    """detect_format on an array export must not need valid JSON beyond the
    first element — proving it streams instead of json.load-ing the file."""
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    first = json.dumps({"id": "c1", "title": "T", "mapping": {}})
    # Valid first element, then garbage where element 2 would be.
    f.write(f"[{first}, {{THIS IS NOT JSON")
    f.close()
    p = Path(f.name)
    try:
        assert detect_format(p) == "chatgpt"
    finally:
        p.unlink(missing_ok=True)


def test_anthropic_parser_streams():
    data = [{
        "uuid": "u1", "name": "Claude convo",
        "chat_messages": [
            {"sender": "human", "text": "hi", "content": [], "created_at": "2026-01-01T00:00:00Z"},
            {"sender": "assistant", "text": "hello", "content": [], "created_at": "2026-01-01T00:00:30Z"},
        ],
    }]
    p = _write_json(data)
    try:
        msgs = list(parse(p, "anthropic"))
        assert len(msgs) == 2
        assert msgs[0]["thread_id"] == "u1"
    finally:
        p.unlink(missing_ok=True)


def test_unparseable_timestamp_is_counted_not_silently_dropped(tmp_db, capsys):
    """A message whose source timestamp can't be parsed (parsers yield 0.0
    for these — see anthropic.py / cursor.py) must not vanish from the
    import summary. It should be counted as skipped, and inserted +
    duplicates + skipped must account for every parsed message."""
    data = [{
        "uuid": "u1", "name": "Project-x convo",
        "chat_messages": [
            {"sender": "human", "text": "hello", "content": [],
             "created_at": "2026-01-01T00:00:00Z"},
            {"sender": "assistant", "text": "reply with a bad timestamp", "content": [],
             "created_at": "not-a-real-date"},
        ],
    }]
    export = _write_json(data)
    try:
        capsys.readouterr()  # clear
        inserted, dupes = ingest.run(export, tmp_db, format_name="anthropic")
        err = capsys.readouterr().err
        assert inserted == 1
        assert dupes == 0
        assert "Skipped:    1" in err, "the skipped count must be surfaced in the summary"

        total_parsed = 2
        # No message may vanish: every parsed message is either inserted,
        # a duplicate, or explicitly counted as skipped.
        assert inserted + dupes + 1 == total_parsed
    finally:
        export.unlink(missing_ok=True)


def test_grok_wrapped_conversations_streams():
    data = {"conversations": [{
        "conversation": {"id": "g1", "title": "Grok convo"},
        "responses": [
            {"response": {"message": "hey", "sender": "human",
                          "create_time": {"$date": {"$numberLong": "1737381600000"}}}},
        ],
    }]}
    p = _write_json(data)
    try:
        assert detect_format(p) == "grok"
        msgs = list(parse(p, "grok"))
        assert len(msgs) == 1
        assert msgs[0]["thread_id"] == "g1"
    finally:
        p.unlink(missing_ok=True)
