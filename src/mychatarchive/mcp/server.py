"""MCP server exposing MyChatArchive as tools for Claude Desktop, Cursor, and other MCP clients.

Tools: search_brain, search_recent, get_context, capture_thought, get_current_datetime
Transport: stdio (default) or sse (for remote access)

IMPORTANT: Never print() to stdout -- it corrupts the JSON-RPC stream.
Use sys.stderr or logging for any debug output.
"""

import datetime
import logging
import hashlib
import json
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mychatarchive import db
from mychatarchive.config import get_db_path

# Quiet MCP SDK INFO logs (they go to stderr and show as [error] in Cursor's MCP panel)
logging.getLogger("mcp").setLevel(logging.WARNING)

mcp = FastMCP("mychatarchive")

_con = None


def _ensure_migrated(con) -> None:
    """Migrate the archive on open (pre-v3 archives gain the sensitivity column).

    Read paths never ran ensure_schema before v0.4.0; scope-filtered SQL needs
    the column, so serve must migrate too. If the file is not writable (e.g. a
    read-only mount), proceed only when the archive is already current.
    """
    try:
        db.ensure_schema(con)
    except Exception as exc:
        try:
            row = con.execute(
                "SELECT value FROM archive_meta WHERE key = 'schema_version'"
            ).fetchone()
            version = int(row[0]) if row else 0
        except Exception:
            version = 0
        if version >= 3:
            print(f"Warning: schema maintenance failed ({exc}); archive is "
                  f"already v{version}, continuing read-only.", file=sys.stderr)
            return
        raise RuntimeError(
            f"This archive needs the v3 sensitivity migration but it could not "
            f"be applied here ({exc}). Run 'mychatarchive classify --list "
            f"--db <path>' on the host with write access, then restart."
        ) from exc


def _get_con():
    global _con
    if _con is None:
        db_path = get_db_path()
        if not db_path.exists():
            print(
                f"Database not found at {db_path}. Run 'mychatarchive import' first.",
                file=sys.stderr,
            )
            raise FileNotFoundError(f"No database at {db_path}")
        _con = db.get_connection(db_path)
        _ensure_migrated(_con)
    return _con


# The MCP surface can express at most public+private. Sealed does not appear
# here at all — no parameter value reaches a scope containing it, and this
# allowlist strips it defensively even if a future edit widens the input.
_MCP_ALLOWED_LEVELS = ("public", "private")


def _scope(include_private: bool) -> tuple:
    scope = ("public", "private") if include_private else ("public",)
    return tuple(s for s in scope if s in _MCP_ALLOWED_LEVELS)


def _lazy_embed(text: str) -> list[float]:
    from mychatarchive.embeddings import embed_single
    return embed_single(text)


def _current_datetime_json() -> dict:
    """Current UTC datetime for injection into tool responses."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "current_datetime_utc": now.isoformat(),
        "current_date": now.strftime("%Y-%m-%d"),
        "current_time_utc": now.strftime("%H:%M:%S"),
    }


@mcp.tool()
def get_current_datetime() -> str:
    """Return the current date and time (UTC).

    Use this when you need to know today's date, the current time, or
    temporal context for interpreting archived messages.
    """
    data = _current_datetime_json()
    return json.dumps(data, indent=2)


def _resolve_group_thread_ids(con, group: str | None) -> set | None:
    """Resolve group name → set of thread IDs (or None if no group filter)."""
    if not group:
        return None
    group_row = db.get_group_by_name(con, group)
    if not group_row:
        return set()  # group doesn't exist → return empty set (no results)
    return db.get_group_thread_ids(con, group_row[0])


@mcp.tool()
def search_brain(
    query: str,
    limit: int = 10,
    platform: str | None = None,
    hours_back: int | None = None,
    since: str | None = None,
    sort_by_time: bool = False,
    group: str | None = None,
    include_private: bool = False,
) -> str:
    """Semantic search across all chat history by meaning.

    Finds messages most similar to the query using vector embeddings.
    Returns relevant messages with metadata (thread, timestamp, role).

    Args:
        query: What to search for -- use natural language
        limit: Maximum number of results (default 10)
        platform: Filter by platform (chatgpt, anthropic, grok, claude_code, cursor).
            Omit to search all. Comma-separated for multiple: "chatgpt,anthropic,grok".
        hours_back: Only include messages from the last N hours
        since: Only include messages from this date (YYYY-MM-DD)
        sort_by_time: If true, sort by newest first instead of relevance
        group: Filter to threads in this user-curated group (e.g. "jarvis", "coding")
        include_private: Also search content the user has classified as private.
            Default false — only set this when the user explicitly asks for
            private material.
    """
    con = _get_con()
    scope = _scope(include_private)
    embedding = _lazy_embed(query)
    platforms = [p.strip() for p in platform.split(",")] if platform else None
    group_thread_ids = _resolve_group_thread_ids(con, group)

    cutoff_iso = None
    if hours_back is not None:
        cutoff_iso = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=hours_back)
        ).isoformat()
    elif since:
        try:
            dt = datetime.datetime.strptime(since, "%Y-%m-%d")
            cutoff_iso = dt.replace(tzinfo=datetime.timezone.utc).isoformat()
        except ValueError:
            return json.dumps({"error": "Invalid since format. Use YYYY-MM-DD."})

    results = db.search_chunks(
        con, embedding, limit=limit, platform=platforms,
        cutoff_iso=cutoff_iso, sort_by_time=sort_by_time,
        group_thread_ids=group_thread_ids, scope=scope,
    )

    if not results:
        out = {"query": query, "count": 0, "results": [], **_current_datetime_json()}
        return json.dumps(out, indent=2)

    output = []
    for chunk_id, distance in results:
        row = db.get_chunk_by_id(con, chunk_id, scope=scope)
        if row:
            meta = json.loads(row[4]) if row[4] else {}
            output.append({
                "text": row[0][:1000],
                "thread_id": row[1],
                "timestamp": row[2],
                "role": meta.get("role", ""),
                "title": meta.get("title", ""),
                "similarity": round(1.0 - distance, 4),
            })

    out = {"query": query, "count": len(output), "results": output, **_current_datetime_json()}
    return json.dumps(out, indent=2)


@mcp.tool()
def search_recent(
    hours: int = 24,
    limit: int = 20,
    platform: str | None = None,
    include_private: bool = False,
) -> str:
    """Retrieve recent conversations and captured thoughts by time range.

    Args:
        hours: How many hours back to look (default 24)
        limit: Maximum results per category (default 20)
        platform: Filter by platform (chatgpt, anthropic, grok, claude_code, cursor).
            Comma-separated for multiple. Omit for all.
        include_private: Also include content the user has classified as private.
            Default false — only set this when the user explicitly asks.
    """
    con = _get_con()
    scope = _scope(include_private)
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)
    ).isoformat()
    platforms = [p.strip() for p in platform.split(",")] if platform else None

    chunk_rows = db.get_recent_chunks(con, cutoff, limit, platform=platforms, scope=scope)
    thought_rows = db.get_recent_thoughts(con, cutoff, limit, scope=scope)

    messages = []
    for row in chunk_rows:
        meta = json.loads(row[4]) if row[4] else {}
        messages.append({
            "text": row[1][:500],
            "thread_id": row[2],
            "timestamp": row[3],
            "role": meta.get("role", ""),
            "title": meta.get("title", ""),
        })

    thoughts = []
    for row in thought_rows:
        thoughts.append({"text": row[1], "created_at": row[2]})

    out = {
        "hours": hours,
        "messages": messages,
        "thoughts": thoughts,
        **_current_datetime_json(),
    }
    return json.dumps(out, indent=2)


@mcp.tool()
def get_context(
    topic: str,
    limit: int = 10,
    platform: str | None = None,
    hours_back: int | None = None,
    since: str | None = None,
    sort_by_time: bool = False,
    group: str | None = None,
    include_private: bool = False,
) -> str:
    """Given a topic, return a comprehensive context bundle.

    Gathers related conversations, captured thoughts, and thread summaries
    to provide full context about a subject from your chat history.

    Args:
        topic: The topic to gather context about
        limit: Maximum results per category (default 10)
        platform: Filter by platform (chatgpt, anthropic, grok, claude_code, cursor).
            Comma-separated for multiple. Omit for all.
        hours_back: Only include messages from the last N hours
        since: Only include messages from this date (YYYY-MM-DD)
        sort_by_time: If true, sort by newest first instead of relevance
        group: Filter to threads in this user-curated group (e.g. "jarvis", "coding")
        include_private: Also include content the user has classified as private.
            Default false — only set this when the user explicitly asks.
    """
    con = _get_con()
    scope = _scope(include_private)
    embedding = _lazy_embed(topic)
    platforms = [p.strip() for p in platform.split(",")] if platform else None
    group_thread_ids = _resolve_group_thread_ids(con, group)

    cutoff_iso = None
    if hours_back is not None:
        cutoff_iso = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=hours_back)
        ).isoformat()
    elif since:
        try:
            dt = datetime.datetime.strptime(since, "%Y-%m-%d")
            cutoff_iso = dt.replace(tzinfo=datetime.timezone.utc).isoformat()
        except ValueError:
            return json.dumps({"error": "Invalid since format. Use YYYY-MM-DD.",
                               **_current_datetime_json()})

    chunk_results = db.search_chunks(
        con, embedding, limit=limit, platform=platforms,
        cutoff_iso=cutoff_iso, sort_by_time=sort_by_time,
        group_thread_ids=group_thread_ids, scope=scope,
    )
    thought_results = db.search_thoughts(con, embedding, limit=5, scope=scope)

    related_messages = []
    thread_ids = set()
    for chunk_id, distance in chunk_results:
        row = db.get_chunk_by_id(con, chunk_id, scope=scope)
        if row:
            meta = json.loads(row[4]) if row[4] else {}
            related_messages.append({
                "text": row[0][:500],
                "thread_id": row[1],
                "timestamp": row[2],
                "role": meta.get("role", ""),
                "title": meta.get("title", ""),
                "similarity": round(1.0 - distance, 4),
            })
            thread_ids.add(row[1])

    # Include thread-level summaries for matched threads (richer context).
    # Use get_thread_summaries (all segments) so multi-segment threads show their full content.
    # 10-col: summary_id[0], canonical_thread_id[1], segment_index[2], title[3],
    #         platform[4], message_count[5], ts_start[6], ts_end[7], summary[8], key_topics[9]
    thread_summaries_out = []
    for tid in list(thread_ids)[:5]:
        segs = db.get_thread_summaries(con, tid, scope=scope)
        if not segs:
            continue
        # Merge all segments into a single thread-level summary
        title = segs[0][3] or ""
        ts_start = segs[0][6] or ""
        ts_end = segs[-1][7] or ""
        # For multi-segment threads, prefix each segment so the model can orient itself
        if len(segs) == 1:
            combined_summary = segs[0][8] or ""
        else:
            parts = []
            for seg in segs:
                seg_text = seg[8] or ""
                parts.append(f"Part {seg[2] + 1}: {seg_text}")
            combined_summary = "\n\n".join(parts)
        # Deduplicate key_topics across all segments, preserving insertion order
        seen_topics: set = set()
        all_topics = []
        for seg in segs:
            for t in (json.loads(seg[9]) if seg[9] else []):
                if t not in seen_topics:
                    seen_topics.add(t)
                    all_topics.append(t)
        thread_summaries_out.append({
            "thread_id": tid,
            "title": title,
            "summary": combined_summary,
            "key_topics": all_topics,
            "ts_start": ts_start,
            "ts_end": ts_end,
            "segment_count": len(segs),
        })

    related_thoughts = []
    for thought_id, distance in thought_results:
        row = db.get_thought_by_id(con, thought_id, scope=scope)
        if row:
            related_thoughts.append({
                "text": row[0],
                "created_at": row[1],
                "similarity": round(1.0 - distance, 4),
            })

    out = {
        "topic": topic,
        "related_messages": related_messages,
        "thread_summaries": thread_summaries_out,
        "related_thoughts": related_thoughts,
        "unique_threads": len(thread_ids),
        **_current_datetime_json(),
    }
    return json.dumps(out, indent=2)


@mcp.tool()
def capture_thought(thought: str, tags: str = "", sensitivity: str = "public") -> str:
    """Capture a new thought or note into your archive with auto-embedding.

    The thought is stored and embedded so it's retrievable via search_brain later.
    Use this to save insights, decisions, ideas, or anything worth remembering.

    Args:
        thought: The thought or note to capture
        tags: Optional comma-separated tags for organization
        sensitivity: "public" (default) or "private". Private thoughts are only
            returned when a retrieval call sets include_private. ("sealed" is
            managed from the CLI, never over MCP.)
    """
    if sensitivity not in _MCP_ALLOWED_LEVELS:
        # Sealing over MCP is rejected by design: an agent sealing content it
        # just wrote would make it invisible to its own session, and sealed
        # stays CLI-only end to end.
        return json.dumps({
            "error": f"sensitivity must be one of {list(_MCP_ALLOWED_LEVELS)}; "
                     f"sealed content is managed via the mychatarchive CLI.",
            **_current_datetime_json(),
        })

    con = _get_con()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    thought_id = hashlib.sha1(f"{now}|{thought[:64]}".encode()).hexdigest()

    embedding = _lazy_embed(thought)
    meta = {"tags": [t.strip() for t in tags.split(",") if t.strip()]} if tags else None

    db.insert_thought(con, thought_id, thought, now, embedding, meta,
                      sensitivity=sensitivity)
    con.commit()

    out = {
        "status": "captured",
        "thought_id": thought_id,
        "created_at": now,
        "preview": thought[:200],
        **_current_datetime_json(),
    }
    return json.dumps(out, indent=2)


@mcp.tool()
def get_profile(
    days_back: int = 30,
    platform: str | None = None,
    group: str | None = None,
    include_private: bool = False,
) -> str:
    """Get a snapshot of the user's current context: active projects, themes, recent focus.

    Use this at the start of a session to understand who the user is and what
    they're currently working on. Returns thread summaries (if available) plus
    recent conversations and captured thoughts, built on lossless archive data.

    Args:
        days_back: How many days of history to include (default 30)
        platform: Filter by platform (chatgpt, anthropic, grok, claude_code, cursor)
        group: Filter to threads in a specific group (e.g. "jarvis", "coding")
        include_private: Also include content the user has classified as private.
            Default false — only set this when the user explicitly asks.
    """
    con = _get_con()
    scope = _scope(include_private)
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_back)
    ).isoformat()
    platforms = [p.strip() for p in platform.split(",")] if platform else None
    group_thread_ids = _resolve_group_thread_ids(con, group)

    # 1. Thread summaries from the recent window (thread-level context)
    # 10-col layout: summary_id[0], canonical_thread_id[1], segment_index[2], title[3],
    #                platform[4], message_count[5], ts_start[6], ts_end[7], summary[8], key_topics[9]
    recent_summaries_raw = db.list_thread_summaries(
        con, limit=20, platform=platforms, since_iso=cutoff, scope=scope
    )
    thread_summaries = []
    for row in recent_summaries_raw:
        # Filter by group if specified (canonical_thread_id is col 1)
        if group_thread_ids is not None and row[1] not in group_thread_ids:
            continue
        thread_summaries.append({
            "thread_id": row[1],
            "title": row[3],
            "platform": row[4],
            "ts_start": row[6],
            "ts_end": row[7],
            "summary": row[8],
            "key_topics": json.loads(row[9]) if row[9] else [],
        })

    # 2. Recent message chunks (always include — fallback if no summaries)
    chunk_rows = db.get_recent_chunks(con, cutoff, limit=15, platform=platforms, scope=scope)
    recent_messages = []
    seen_threads: set[str] = set()
    for row in chunk_rows:
        tid = row[2]
        if group_thread_ids is not None and tid not in group_thread_ids:
            continue
        meta = json.loads(row[4]) if row[4] else {}
        seen_threads.add(tid)
        recent_messages.append({
            "text": row[1][:400],
            "thread_id": tid,
            "timestamp": row[3],
            "role": meta.get("role", ""),
            "title": meta.get("title", ""),
        })

    # 3. Recent captured thoughts
    thought_rows = db.get_recent_thoughts(con, cutoff, limit=10, scope=scope)
    thoughts = [{"text": r[1], "created_at": r[2]} for r in thought_rows]

    # 4. Aggregate key topics from summaries for a "focus areas" view
    all_topics: dict[str, int] = {}
    for s in thread_summaries:
        for t in s.get("key_topics", []):
            all_topics[t] = all_topics.get(t, 0) + 1
    top_topics = sorted(all_topics.items(), key=lambda x: -x[1])[:15]

    out = {
        "profile_window_days": days_back,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "group_filter": group,
        "platform_filter": platform,
        "recent_threads_count": len(seen_threads | {s["thread_id"] for s in thread_summaries}),
        "focus_areas": [t for t, _ in top_topics],
        "thread_summaries": thread_summaries[:10],
        "recent_messages": recent_messages[:10],
        "captured_thoughts": thoughts,
        "hint": (
            "No thread summaries found — run 'mychatarchive summarize' to generate them "
            "for richer profile context."
        ) if not thread_summaries else None,
        **_current_datetime_json(),
    }
    # Remove null hint
    if out.get("hint") is None:
        del out["hint"]

    return json.dumps(out, indent=2)


# Default bind address for SSE transport: loopback-only. Binding every
# interface is an explicit, caller-provided opt-in (CLI: `serve --host
# <bind-all-address>`), never a hardcoded default -- a fixed bind-all literal
# would bind every archive-serving process to the LAN whether or not the
# operator wanted that.
_DEFAULT_SSE_HOST = "127.0.0.1"


def run(db_path: Path | None = None, transport: str = "stdio", port: int = 8420,
        ssl_certfile: str | None = None, ssl_keyfile: str | None = None,
        host: str | None = None):
    """Start the MCP server."""
    if db_path:
        global _con
        _con = db.get_connection(db_path)
        _ensure_migrated(_con)

    if transport == "sse":
        bind_host = host or _DEFAULT_SSE_HOST
        print(
            f"Starting MCP server with SSE transport on {bind_host}:{port}",
            file=sys.stderr,
        )
        mcp.settings.port = port
        mcp.settings.host = bind_host
        if ssl_certfile or ssl_keyfile:
            import anyio
            import uvicorn

            # Disable localhost-only DNS rebinding protection only when the
            # caller explicitly asked for a non-loopback bind (CLI: --host
            # <bind-all>) -- a proper mkcert cert over HTTPS on a private LAN
            # is the intended use; the loopback-only default keeps it on.
            if bind_host != _DEFAULT_SSE_HOST:
                mcp.settings.transport_security.enable_dns_rebinding_protection = False

            async def _run():
                config = uvicorn.Config(
                    mcp.sse_app(),
                    host=bind_host,
                    port=port,
                    log_level=mcp.settings.log_level.lower(),
                    ssl_certfile=ssl_certfile,
                    ssl_keyfile=ssl_keyfile,
                )
                await uvicorn.Server(config).serve()

            anyio.run(_run)
        else:
            mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")
