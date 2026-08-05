"""Tests for parser auto-detection and parsing."""

import json
from pathlib import Path

import pytest

from mychatarchive import db, ingest
from mychatarchive.parsers import detect_format, parse

EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def test_detect_chatgpt():
    assert detect_format(EXAMPLES_DIR / "sample_chatgpt.json") == "chatgpt"


def test_detect_anthropic():
    assert detect_format(EXAMPLES_DIR / "sample_anthropic.json") == "anthropic"


def test_detect_grok():
    assert detect_format(EXAMPLES_DIR / "sample_grok.json") == "grok"


def test_parse_chatgpt():
    messages = list(parse(EXAMPLES_DIR / "sample_chatgpt.json", "chatgpt"))
    assert len(messages) > 0
    for msg in messages:
        assert "thread_id" in msg
        assert "role" in msg
        assert "content" in msg
        assert "created_at" in msg


def test_parse_anthropic():
    messages = list(parse(EXAMPLES_DIR / "sample_anthropic.json", "anthropic"))
    assert len(messages) > 0


def test_parse_grok():
    messages = list(parse(EXAMPLES_DIR / "sample_grok.json", "grok"))
    assert len(messages) > 0


def test_auto_detect_parse():
    messages = list(parse(EXAMPLES_DIR / "sample_chatgpt.json"))
    assert len(messages) > 0


def _write_jsonl(tmp_path: Path, lines: list[dict], name: str = "session.jsonl") -> Path:
    p = tmp_path / name
    with open(p, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return p


def test_detect_claude_code_jsonl_with_leading_bookkeeping_records(tmp_path):
    """Real Claude Code session files often lead with non-message records
    (queue-operation, mode, ai-title, summary) before the first turn.
    Detection must scan past them instead of giving up after line one."""
    lines = [
        {"type": "mode", "mode": "plan"},
        {"type": "queue-operation", "op": "enqueue"},
        {"type": "ai-title", "title": "Project-x session"},
        {
            "type": "user", "sessionId": "sess-1", "uuid": "u1",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": "user", "content": "hello there"},
        },
        {
            "type": "assistant", "sessionId": "sess-1", "uuid": "u2",
            "timestamp": "2026-01-01T00:00:30Z",
            "message": {"role": "assistant", "content": "hi, how can I help?"},
        },
    ]
    p = _write_jsonl(tmp_path, lines)
    assert detect_format(p) == "claude_code"


def test_claude_code_jsonl_full_ingest_skips_bookkeeping_records(tmp_path):
    """A full ingest.run() on a real-shaped session file (auto-detected, no
    --format) must import the actual turns and must not crash or emit
    garbage rows for the non-message bookkeeping records."""
    lines = [
        {"type": "queue-operation", "sessionId": "sess-1", "op": "enqueue"},
        {"type": "mode", "sessionId": "sess-1", "mode": "plan"},
        {"type": "ai-title", "sessionId": "sess-1", "title": "Project-x session"},
        {"type": "summary", "sessionId": "sess-1", "summary": "discussing project-x"},
        {
            "type": "user", "sessionId": "sess-1", "uuid": "u1",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"role": "user", "content": "hello there"},
        },
        {
            "type": "assistant", "sessionId": "sess-1", "uuid": "u2",
            "timestamp": "2026-01-01T00:00:30Z",
            "message": {"role": "assistant", "content": "hi, how can I help?"},
        },
    ]
    p = _write_jsonl(tmp_path, lines)
    db_path = tmp_path / "archive.sqlite"

    inserted, dupes = ingest.run(p, db_path)  # no format_name -> must auto-detect

    assert inserted == 2, "only the two real turns should be imported"
    assert dupes == 0
    con = db.get_connection(db_path)
    assert db.message_count(con) == 2
    con.close()


def test_generic_jsonl_with_type_field_not_misdetected_as_claude_code(tmp_path):
    """A JSONL file that merely happens to use `type: user`/`assistant`
    values but carries none of Claude Code's session metadata (sessionId,
    uuid, cwd, ...) must not be claimed by the claude_code detector."""
    lines = [
        {"type": "user", "text": "hello"},
        {"type": "assistant", "text": "hi"},
    ]
    p = _write_jsonl(tmp_path, lines, name="generic.jsonl")
    assert detect_format(p) != "claude_code"
