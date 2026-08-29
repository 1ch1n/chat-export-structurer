"""Parser for Cursor IDE conversation history (state.vscdb SQLite databases).

Cursor stores conversations in two SQLite databases:
- Global: %APPDATA%/Cursor/User/globalStorage/state.vscdb
  Contains all conversation data across all workspaces in the `cursorDiskKV` table.
  Key patterns:
    - bubbleId:<composerId>:<bubbleId> → individual chat messages
  And in the `ItemTable`:
    - composer.composerData → JSON with all composer (conversation) metadata

- Per-workspace: %APPDATA%/Cursor/User/workspaceStorage/<hash>/state.vscdb
  Contains workspace-specific composer metadata, linked via workspace.json.
"""

import json
import logging
import os
import platform
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


def _default_cursor_dir() -> Path:
    if platform.system() == "Windows":
        return Path(os.environ.get("APPDATA", "")) / "Cursor" / "User"
    elif platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Cursor" / "User"
    else:
        return Path.home() / ".config" / "Cursor" / "User"


def _extract_lexical_text(rich_text_json: str) -> str:
    """Extract plain text from Cursor's Lexical editor JSON format."""
    try:
        root = json.loads(rich_text_json)
    except (json.JSONDecodeError, TypeError):
        return ""

    texts = []
    _walk_lexical_nodes(root, texts)
    return "\n".join(texts)


def _walk_lexical_nodes(node, texts: list):
    """Recursively walk Lexical AST nodes to extract text."""
    if isinstance(node, dict):
        if node.get("type") == "text" and "text" in node:
            texts.append(node["text"])

        for child in node.get("children", []):
            _walk_lexical_nodes(child, texts)

        if "root" in node:
            _walk_lexical_nodes(node["root"], texts)

    elif isinstance(node, list):
        for item in node:
            _walk_lexical_nodes(item, texts)


def _parse_timestamp(ts) -> float:
    if ts is None:
        return 0.0
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.timestamp()
        except (ValueError, TypeError):
            return 0.0
    if isinstance(ts, (int, float)):
        if ts > 1e12:
            return ts / 1000.0
        return float(ts)
    return 0.0


def _read_composers_from_global(global_db_path: Path) -> dict[str, dict]:
    """Read all composer (conversation) metadata from all workspace DBs."""
    composers = {}
    user_dir = global_db_path.parent.parent

    ws_storage = user_dir / "workspaceStorage"
    if not ws_storage.exists():
        return composers

    for ws_dir in ws_storage.iterdir():
        if not ws_dir.is_dir():
            continue
        ws_db = ws_dir / "state.vscdb"
        if not ws_db.exists():
            continue

        workspace_folder = ""
        ws_json = ws_dir / "workspace.json"
        if ws_json.exists():
            try:
                with open(ws_json, "r", encoding="utf-8") as f:
                    ws_data = json.load(f)
                folder_uri = ws_data.get("folder", "")
                workspace_folder = _uri_to_path(folder_uri)
            except (json.JSONDecodeError, OSError) as exc:
                logger.debug("Skipping unreadable workspace.json %s: %s", ws_json, exc)

        try:
            con = sqlite3.connect(f"file:{ws_db}?mode=ro", uri=True)
            row = con.execute(
                "SELECT value FROM ItemTable WHERE key = 'composer.composerData'"
            ).fetchone()
            con.close()
        except (sqlite3.Error, OSError):
            continue

        if not row:
            continue

        try:
            cd = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            continue

        for comp in cd.get("allComposers", []):
            cid = comp.get("composerId", "")
            if not cid:
                continue
            if comp.get("subagentInfo"):
                continue

            composers[cid] = {
                "composerId": cid,
                "name": comp.get("name", comp.get("subtitle", "")),
                "createdAt": comp.get("createdAt"),
                "unifiedMode": comp.get("unifiedMode", ""),
                "totalLinesAdded": comp.get("totalLinesAdded", 0),
                "totalLinesRemoved": comp.get("totalLinesRemoved", 0),
                "filesChangedCount": comp.get("filesChangedCount", 0),
                "workspace": workspace_folder,
            }

    return composers


def _uri_to_path(uri: str) -> str:
    """Convert a file:// URI to a readable path."""
    if not uri:
        return ""
    from urllib.parse import unquote, urlparse
    parsed = urlparse(uri)
    path = unquote(parsed.path)
    if platform.system() == "Windows" and path.startswith("/"):
        path = path[1:]
    return path


def _read_bubbles_for_composer(global_db_path: Path, composer_id: str) -> list[dict]:
    """Read all bubble (message) records for a given composer from the global DB."""
    try:
        con = sqlite3.connect(f"file:{global_db_path}?mode=ro", uri=True)
        prefix = f"bubbleId:{composer_id}:"
        rows = con.execute(
            "SELECT key, value FROM cursorDiskKV WHERE key LIKE ?",
            (prefix + "%",),
        ).fetchall()
        con.close()
    except (sqlite3.Error, OSError):
        return []

    bubbles = []
    for key, value in rows:
        try:
            data = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            continue

        bubble_type = data.get("type")
        rich_text = data.get("richText", "")
        text = _extract_lexical_text(rich_text) if rich_text else ""

        raw_text = data.get("text", "")
        if not text and raw_text:
            text = raw_text

        if not text or not text.strip():
            continue

        created_at = _parse_timestamp(data.get("createdAt"))

        if bubble_type == 1:
            role = "user"
        elif bubble_type == 2:
            role = "assistant"
        else:
            role = "user" if bubble_type in (1, None) else "assistant"

        bubbles.append({
            "role": role,
            "content": text,
            "created_at": created_at,
            "bubble_id": data.get("bubbleId", key.split(":")[-1]),
        })

    bubbles.sort(key=lambda b: b["created_at"])
    return bubbles


def parse_from_db(cursor_user_dir: Path | None = None) -> Iterator[dict]:
    """Parse all Cursor conversations from the global state database."""
    if cursor_user_dir is None:
        cursor_user_dir = _default_cursor_dir()

    global_db = cursor_user_dir / "globalStorage" / "state.vscdb"
    if not global_db.exists():
        raise FileNotFoundError(
            f"Cursor global database not found at {global_db}. "
            f"Is Cursor installed?"
        )

    composers = _read_composers_from_global(global_db)
    if not composers:
        return

    for cid, meta in sorted(composers.items(), key=lambda x: x[1].get("createdAt") or 0):
        bubbles = _read_bubbles_for_composer(global_db, cid)
        if not bubbles:
            continue

        thread_title = meta.get("name") or meta.get("workspace") or cid
        workspace = meta.get("workspace", "")
        if workspace and meta.get("name"):
            thread_title = f"{meta['name']} ({workspace})"

        for bubble in bubbles:
            yield {
                "thread_id": cid,
                "thread_title": thread_title,
                "role": bubble["role"],
                "content": bubble["content"],
                "created_at": bubble["created_at"],
            }


def parse(input_path: str) -> Iterator[dict]:
    """Parse Cursor conversation history.

    input_path can be:
    - "auto" to auto-discover from default location
    - Path to the Cursor User directory (e.g., %APPDATA%/Cursor/User)
    - Path to a specific state.vscdb file
    """
    if input_path == "auto":
        yield from parse_from_db()
    else:
        p = Path(input_path)
        if p.is_file() and p.name == "state.vscdb":
            yield from parse_from_db(p.parent.parent)
        elif p.is_dir():
            global_db = p / "globalStorage" / "state.vscdb"
            if global_db.exists():
                yield from parse_from_db(p)
            else:
                raise ValueError(
                    f"No globalStorage/state.vscdb found in {p}. "
                    f"Pass the Cursor User directory or 'auto'."
                )
        else:
            raise ValueError(
                f"Cursor parser: {input_path} is not a valid path. "
                f"Pass 'auto', the Cursor User directory, or a state.vscdb file."
            )
