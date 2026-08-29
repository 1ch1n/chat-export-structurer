"""Parser for Anthropic Claude conversation exports."""

import logging
from datetime import datetime
from typing import Iterator

import ijson

logger = logging.getLogger(__name__)


def parse(input_path: str) -> Iterator[dict]:
    # Anthropic exports are a top-level JSON array of conversations. Stream
    # one conversation at a time with ijson so a multi-GB export never has to
    # fit in memory (json.load here was the old whole-file materialization).
    with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
        start = f.read(4096).lstrip()[:1]

    if start != "[":
        raise ValueError("Anthropic export should be a JSON array")

    with open(input_path, "rb") as f:
        for convo in ijson.items(f, "item"):
            if isinstance(convo, dict):
                yield from _parse_conversation(convo)


def _parse_conversation(convo: dict) -> Iterator[dict]:
    thread_id = convo.get("uuid", "")
    title = convo.get("name", "")
    chat_messages = convo.get("chat_messages", [])

    for msg in chat_messages:
        sender = msg.get("sender", "").lower()
        role = "user" if sender == "human" else sender if sender else "unknown"

        content_list = msg.get("content", [])
        text = msg.get("text", "")

        if isinstance(content_list, list) and content_list:
            content = "\n".join(
                item.get("text", "")
                for item in content_list
                if isinstance(item, dict) and item.get("type") == "text"
            )
        else:
            content = text

        created_at_str = msg.get("created_at", "")
        try:
            dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            created_at = dt.timestamp()
        except (ValueError, TypeError, AttributeError) as exc:
            logger.debug(
                "Unparseable created_at %r in thread %r: %s", created_at_str, thread_id, exc,
            )
            created_at = 0.0

        yield {
            "thread_id": thread_id,
            "thread_title": title,
            "role": role,
            "content": content,
            "created_at": created_at,
        }
