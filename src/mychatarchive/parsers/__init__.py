"""Parser registry and auto-detection."""

import json
import re
from pathlib import Path
from typing import Iterator

import ijson

from mychatarchive.parsers import chatgpt, anthropic, grok, claude_code, cursor

PARSERS = {
    "chatgpt": chatgpt,
    "anthropic": anthropic,
    "grok": grok,
    "claude_code": claude_code,
    "cursor": cursor,
}

DIRECTORY_PARSERS = {"claude_code", "cursor"}

# Claude Code session .jsonl files interleave message records with
# bookkeeping records; all of these `type` values are shapes the parser
# already knows to either read or skip (see parsers/claude_code.py).
_CLAUDE_CODE_JSONL_TYPES = {
    "user", "assistant", "file-history-snapshot",
    "queue-operation", "mode", "ai-title", "summary",
}

# A recognized `type` alone is too generic — other tools could plausibly emit
# JSONL with {"type": "user", ...} lines. Require at least one of these
# claude_code-specific fields too, so detection stays specific to real
# session files rather than stealing any JSONL with a matching `type`.
_CLAUDE_CODE_JSONL_MARKER_KEYS = {
    "sessionId", "uuid", "parentUuid", "leafUuid", "cwd", "gitBranch", "version",
}


def _looks_like_claude_code_jsonl(p: Path, max_lines: int = 10) -> bool:
    """Scan the first few non-empty lines of a .jsonl file for a
    claude_code-shaped record.

    Real Claude Code session files commonly lead with one or more bookkeeping
    records (queue-operation, mode, ai-title, summary) before the first
    user/assistant turn, so checking only the first line (the old behavior)
    misses them and detection falls through to "unknown format".
    """
    checked = 0
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                checked += 1
                if checked > max_lines:
                    break
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                if (
                    record.get("type") in _CLAUDE_CODE_JSONL_TYPES
                    and _CLAUDE_CODE_JSONL_MARKER_KEYS & record.keys()
                ):
                    return True
    except OSError:
        return False
    return False


def detect_format(file_path: Path) -> str | None:
    """Auto-detect export format by inspecting the JSON structure.

    For file-based exports (ChatGPT, Anthropic, Grok), inspects the JSON.
    For directory-based sources (Claude Code, Cursor), use --format explicitly
    or pass "auto" as the path.
    """
    p = Path(file_path)

    if p.is_dir():
        if (p / "projects").is_dir() or p.name == ".claude":
            return "claude_code"
        if (p / "globalStorage" / "state.vscdb").exists():
            return "cursor"
        return None

    if p.suffix == ".jsonl":
        if _looks_like_claude_code_jsonl(p):
            return "claude_code"

    if p.name == "state.vscdb":
        return "cursor"

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        head = f.read(8192)

    stripped = head.lstrip()

    if stripped.startswith("["):
        # Stream just the FIRST array element — detection must not load a
        # multi-GB export into memory (json.load here used to).
        try:
            with open(file_path, "rb") as f:
                first = next(ijson.items(f, "item"), None)
        except Exception:
            return None
        if first is None:
            return None
    elif re.match(r'^\{\s*"conversations"\s*:', stripped):
        # Grok's official {"conversations": [...]} wrapper can also be huge —
        # stream the first conversation only.
        try:
            with open(file_path, "rb") as f:
                item = next(ijson.items(f, "conversations.item"), None)
        except Exception:
            return None
        if isinstance(item, dict) and "responses" in item:
            return "grok"
        return None
    elif stripped.startswith("{"):
        # Other single-object shapes are small (one conversation).
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                first = json.load(f)
        except json.JSONDecodeError:
            return None
    else:
        return None

    if not isinstance(first, dict):
        return None

    if "mapping" in first and "title" in first:
        return "chatgpt"
    if "chat_messages" in first and "uuid" in first:
        return "anthropic"
    if "conversations" in first:
        peek = first["conversations"]
        if isinstance(peek, list) and len(peek) > 0:
            item = peek[0]
            if isinstance(item, dict) and "responses" in item:
                return "grok"
    if "conversation" in first and "responses" in first:
        return "grok"
    if "messages" in first or ("id" in first and "text" in first):
        return "grok"

    return None


def parse(file_path: Path, format_name: str | None = None) -> Iterator[dict]:
    """Parse an export file, auto-detecting format if not specified."""
    if format_name is None:
        format_name = detect_format(file_path)
        if format_name is None:
            raise ValueError(
                f"Could not auto-detect format for {file_path}. "
                f"Use --format with one of: {', '.join(PARSERS.keys())}"
            )

    if format_name not in PARSERS:
        raise ValueError(f"Unknown format '{format_name}'. Options: {', '.join(PARSERS.keys())}")

    yield from PARSERS[format_name].parse(str(file_path))
