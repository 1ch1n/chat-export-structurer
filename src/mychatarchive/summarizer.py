"""Thread summarization pipeline.

Reads threads from the archive, generates LLM summaries, and stores them in
thread_summaries. Longer threads are split into segments — each segment gets
its own summary row and embedding, giving proportional representation in retrieval.

Pipeline slot: after Sync, before Chunk + Embed.
    mychatarchive sync
    mychatarchive summarize          <-- this module
    mychatarchive embed

Usage:
    mychatarchive summarize [--model MODEL] [--force] [--limit N]
    mychatarchive summarize [--messages-per-segment N]
    mychatarchive summarize --base-url https://openrouter.ai/api/v1 --key sk-or-...

The LLM API must be OpenAI-compatible (OpenRouter, Anthropic-compatible, Ollama, etc.).
Defaults: OpenRouter, anthropic/claude-haiku-4-5 (fast, cheap, good summarizer).

Resolution order (CLI overrides config overrides env over default):
  - Model:    --model  > config.json summarize.model  > anthropic/claude-haiku-4-5
  - Base URL: --base-url > config.json summarize.base_url > https://openrouter.ai/api/v1
  - API key:  --key > OPENROUTER_API_KEY > ANTHROPIC_API_KEY > OPENAI_API_KEY > config.json summarize.api_key
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    from tqdm import tqdm
    _HAS_TQDM = True
except ImportError:
    _HAS_TQDM = False

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_MODEL = "anthropic/claude-haiku-4-5"
_DEFAULT_MESSAGES_PER_SEGMENT = 15   # Threads longer than this get multiple summaries
_SEGMENT_CONTEXT_CHARS = 6000        # ~1500 tokens per segment; keeps API cost low
_BATCH_COMMIT = 20                   # commit every N segments

_SYSTEM_PROMPT = """\
You are summarizing an AI conversation for personal archival and retrieval.
Given a thread title and its messages (or a portion of a long thread), return a JSON object
with exactly two keys:
  "summary"    – 2-4 sentence description of the main topics, decisions, and outcomes.
                 Be specific: name tools, projects, decisions, and conclusions discussed.
  "key_topics" – array of 3-8 keyword strings (project names, tools, concepts, people, places).
                 Use specific terms, not generic ones like "AI" or "chat".

Return ONLY valid JSON. No markdown fences, no explanation."""


def _resolve_api_key(cli_key: str | None) -> str:
    """Return API key from CLI flag, env vars, or config."""
    if cli_key:
        return cli_key
    for env in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        v = os.environ.get(env)
        if v:
            return v
    # Check config
    try:
        from mychatarchive.config import load_config
        cfg = load_config()
        key = cfg.get("summarize", {}).get("api_key")
        if key:
            return key
    except Exception:
        pass
    return ""


def _segment_messages(messages: list[dict], per_segment: int) -> list[list[dict]]:
    """Split messages into segments of up to per_segment messages each."""
    if not messages:
        return []
    return [messages[i:i + per_segment] for i in range(0, len(messages), per_segment)]


def _segment_ts(messages: list[dict]) -> tuple[str | None, str | None]:
    """Return (ts_start, ts_end) for a list of messages."""
    timestamps = [m.get("ts") for m in messages if m.get("ts")]
    if not timestamps:
        return None, None
    return min(timestamps), max(timestamps)


def _segment_chars(messages: list[dict]) -> int:
    """Total character count of message texts in this segment."""
    return sum(len(m.get("text") or "") for m in messages)


def _format_segment(
    thread_meta: dict,
    messages: list[dict],
    seg_idx: int,
    total_segments: int,
) -> str:
    """Format a segment of messages for the LLM prompt."""
    title = thread_meta.get("title") or "Untitled"
    if total_segments > 1:
        header = f"Thread: {title} (Part {seg_idx + 1} of {total_segments})\n---"
    else:
        header = f"Thread: {title}\n---"
    parts = [header]
    chars = len(header)
    for msg in messages:
        role = msg.get("role", "?")
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        line = f"\n[{role}]: {text}"
        if chars + len(line) > _SEGMENT_CONTEXT_CHARS:
            parts.append(f"\n[... segment truncated at {_SEGMENT_CONTEXT_CHARS} chars ...]")
            break
        parts.append(line)
        chars += len(line)
    return "".join(parts)


def _call_api(prompt: str, api_key: str, base_url: str, model: str) -> dict:
    """Call an OpenAI-compatible chat completions endpoint. Returns parsed JSON response."""
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 800,
        "temperature": 0.2,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("HTTP-Referer", "https://mychatarchive.com")
    req.add_header("X-Title", "MyChatArchive Summarizer")

    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read())


def _parse_response(raw: dict) -> tuple[str, list[str]]:
    """Extract (summary, key_topics) from the API response."""
    text = raw["choices"][0]["message"]["content"].strip()
    # Strip markdown fences if model added them anyway
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rstrip("`").strip()
    parsed = json.loads(text)
    summary = str(parsed.get("summary", "")).strip()
    topics = [str(t) for t in parsed.get("key_topics", []) if t]
    return summary, topics


def run(
    db_path: Path,
    model: str = _DEFAULT_MODEL,
    base_url: str = _DEFAULT_BASE_URL,
    api_key: str = "",
    force: bool = False,
    limit: int | None = None,
    embed_summaries: bool = True,
    messages_per_segment: int = _DEFAULT_MESSAGES_PER_SEGMENT,
    include_private: bool = False,
) -> dict:
    """Generate LLM summaries for all unsummarized threads.

    Long threads are split into segments of messages_per_segment messages each.
    Each segment gets its own summary row and embedding in the DB.

    Summarization sends raw thread text to an external LLM API — the one place
    archive content leaves the machine. Sealed threads are never summarized;
    private threads only with include_private=True.

    Args:
        db_path:              Path to archive.db
        model:                LLM model identifier (OpenAI-compatible name)
        base_url:             API base URL (default: OpenRouter)
        api_key:              API key (falls back to env vars if empty)
        force:                Re-summarize already-summarized threads (deletes old segments)
        limit:                Max threads to process in this run
        embed_summaries:      Also embed summaries for thread-level semantic search
        messages_per_segment: Messages per summary segment (default: 15)
        include_private:      Also summarize private threads (sealed never leaves
                              the machine regardless)

    Returns:
        {"processed": N, "skipped": N, "errors": N, "total_threads": N, "segments": N}
    """
    from mychatarchive import db

    if not db_path.exists():
        raise FileNotFoundError(
            f"Archive not found at {db_path}. Run 'mychatarchive import' first."
        )

    api_key = api_key or _resolve_api_key(None)
    if not api_key:
        raise ValueError(
            "No API key found. Set OPENROUTER_API_KEY, or pass --key.\n"
            "  export OPENROUTER_API_KEY=sk-or-..."
        )

    con = db.get_connection(db_path)
    db.ensure_schema(con)

    now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Collect threads to process. Scope is explicit egress control: this is
    # the only path that ships raw archive text to an external API.
    scope = ("public", "private") if include_private else ("public",)
    threads = list(db.iter_threads(con, scope=scope))
    total = len(threads)
    print(f"  [summarize] {total:,} threads in scope {list(scope)}", file=sys.stderr)

    if not force:
        threads = [t for t in threads if not db.has_thread_summary(con, t["canonical_thread_id"])]
        print(f"  [summarize] {len(threads):,} without summaries", file=sys.stderr)
    else:
        print(f"  [summarize] --force: re-summarizing all", file=sys.stderr)

    if limit:
        threads = threads[:limit]

    if not threads:
        print("  [summarize] Nothing to do.", file=sys.stderr)
        con.close()
        return {"processed": 0, "skipped": total, "errors": 0, "total_threads": total, "segments": 0}

    print(
        f"  [summarize] Processing {len(threads):,} threads with {model} "
        f"(up to {messages_per_segment} msgs/segment)",
        file=sys.stderr,
    )

    # Optionally embed summaries
    embedder = None
    if embed_summaries:
        try:
            from mychatarchive.embeddings import embed_single
            embedder = embed_single
        except Exception:
            print("  [summarize] Warning: embeddings not available, skipping summary embeddings.",
                  file=sys.stderr)

    processed = errors = total_segments = 0
    iterator = tqdm(threads, desc="Summarizing", unit="thread") if _HAS_TQDM else threads

    for thread_meta in iterator:
        thread_id = thread_meta["canonical_thread_id"]
        try:
            messages = db.get_thread_messages(con, thread_id, scope=scope)
            if not messages:
                continue

            if force:
                db.delete_thread_summaries(con, thread_id)

            segments = _segment_messages(messages, messages_per_segment)
            n_segments = len(segments)

            for seg_idx, seg_messages in enumerate(segments):
                summary_id = f"{thread_id}::{seg_idx:04d}"
                seg_ts_start, seg_ts_end = _segment_ts(seg_messages)
                seg_chars = _segment_chars(seg_messages)

                prompt = _format_segment(thread_meta, seg_messages, seg_idx, n_segments)
                raw = _call_api(prompt, api_key, base_url, model)
                summary, key_topics = _parse_response(raw)

                if not summary:
                    raise ValueError("Empty summary returned")

                db.insert_thread_summary(
                    con,
                    summary_id=summary_id,
                    canonical_thread_id=thread_id,
                    segment_index=seg_idx,
                    title=thread_meta.get("title"),
                    platform=thread_meta.get("platform"),
                    message_count=len(seg_messages),
                    segment_chars=seg_chars,
                    ts_start=seg_ts_start,
                    ts_end=seg_ts_end,
                    summary=summary,
                    key_topics=key_topics,
                    summary_model=model,
                    now=now_str,
                    sensitivity=thread_meta.get("sensitivity", "public"),
                )

                if embedder:
                    try:
                        emb = embedder(summary)
                        db.insert_thread_summary_embedding(con, summary_id, emb)
                    except Exception as e:
                        print(f"  [summarize] Embedding failed for {summary_id}: {e}",
                              file=sys.stderr)

                total_segments += 1

            processed += 1

            if processed % _BATCH_COMMIT == 0:
                con.commit()

        except urllib.error.HTTPError as e:
            errors += 1
            body = e.read().decode(errors="replace")[:200] if e.fp else ""
            print(f"  [summarize] HTTP {e.code} for {thread_id[:8]}: {body}", file=sys.stderr)
        except Exception as e:
            errors += 1
            print(f"  [summarize] Error on {thread_id[:8]}: {e}", file=sys.stderr)

    con.commit()
    con.close()

    skipped = total - len(threads)
    print(
        f"  [summarize] Done: {processed:,} threads ({total_segments:,} segments), "
        f"{skipped:,} skipped, {errors} errors",
        file=sys.stderr,
    )
    return {
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "total_threads": total,
        "segments": total_segments,
    }
