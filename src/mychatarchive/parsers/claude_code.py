"""Parser for Claude Code conversation history (~/.claude/projects/)."""

import json
import logging
import os
import platform
from datetime import datetime
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


def _default_claude_dir() -> Path:
    if platform.system() == "Windows":
        return Path(os.path.expanduser("~")) / ".claude"
    return Path.home() / ".claude"


def _discover_sessions(claude_dir: Path) -> list[dict]:
    """Find all session JSONL files across all projects."""
    projects_dir = claude_dir / "projects"
    if not projects_dir.exists():
        return []

    sessions = []
    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue

        index_path = project_dir / "sessions-index.json"
        index_data = {}
        if index_path.exists():
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    index_data = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.debug("Skipping unreadable sessions index %s: %s", index_path, exc)

        entries_by_id = {}
        for entry in index_data.get("entries", []):
            entries_by_id[entry.get("sessionId", "")] = entry

        for jsonl_file in project_dir.glob("*.jsonl"):
            session_id = jsonl_file.stem
            meta = entries_by_id.get(session_id, {})
            sessions.append({
                "path": jsonl_file,
                "session_id": session_id,
                "project_path": meta.get("projectPath", index_data.get("originalPath", project_dir.name)),
                "summary": meta.get("summary", ""),
                "created": meta.get("created"),
                "modified": meta.get("modified"),
            })

    return sessions


def _parse_timestamp(ts) -> float:
    """Parse ISO string or epoch milliseconds to epoch seconds."""
    if ts is None:
        return 0.0
    if isinstance(ts, (int, float)):
        if ts > 1e12:
            return ts / 1000.0
        return float(ts)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.timestamp()
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def _extract_text(content) -> str:
    """Extract plain text from Claude Code message content."""
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "tool_use":
                    tool_name = item.get("name", "unknown")
                    tool_input = item.get("input", {})
                    if isinstance(tool_input, dict):
                        cmd = tool_input.get("command", "")
                        if cmd:
                            parts.append(f"[Tool: {tool_name}] {cmd}")
                        else:
                            parts.append(f"[Tool: {tool_name}]")
                elif item.get("type") == "tool_result":
                    result_content = item.get("content", "")
                    if isinstance(result_content, str) and result_content:
                        parts.append(result_content[:500])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(p for p in parts if p)

    return ""


def parse_session(session_path: Path, project_path: str = "", summary: str = "") -> Iterator[dict]:
    """Parse a single Claude Code session JSONL file."""
    thread_title = summary or project_path or session_path.stem

    with open(session_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            rec_type = record.get("type")
            if rec_type not in ("user", "assistant"):
                continue

            message = record.get("message", {})
            if not isinstance(message, dict):
                continue

            role = message.get("role", rec_type)
            content_raw = message.get("content", "")
            text = _extract_text(content_raw)

            if not text or not text.strip():
                continue

            ts = _parse_timestamp(record.get("timestamp"))
            if ts == 0.0:
                continue

            session_id = record.get("sessionId", session_path.stem)

            yield {
                "thread_id": session_id,
                "thread_title": thread_title,
                "role": role,
                "content": text,
                "created_at": ts,
            }


def parse(input_path: str) -> Iterator[dict]:
    """Parse Claude Code history.

    input_path can be:
    - A path to a specific .jsonl session file
    - A path to a project directory containing .jsonl files
    - A path to the ~/.claude directory (scans all projects)
    - The literal string "auto" to auto-discover from default location
    """
    p = Path(input_path)

    if input_path == "auto" or (p.is_dir() and (p / "projects").is_dir()):
        claude_dir = _default_claude_dir() if input_path == "auto" else p
        sessions = _discover_sessions(claude_dir)
        for session in sorted(sessions, key=lambda s: s.get("created") or ""):
            yield from parse_session(
                session["path"],
                project_path=session["project_path"],
                summary=session["summary"],
            )
    elif p.is_dir():
        for jsonl_file in sorted(p.glob("*.jsonl")):
            yield from parse_session(jsonl_file, project_path=str(p))
    elif p.is_file() and p.suffix == ".jsonl":
        yield from parse_session(p)
    else:
        raise ValueError(
            f"Claude Code parser: {input_path} is not a .jsonl file, "
            f"directory, or 'auto'. Pass a session file, project dir, "
            f"or the ~/.claude directory."
        )
