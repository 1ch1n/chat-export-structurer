"""Tests for the summarizer pipeline (mychatarchive.summarizer).

This is the only path that sends raw archive text to an external API, so
every test here mocks the network layer (summarizer._call_api) and never
makes a real HTTP call. Coverage:
  - scope: sealed/private threads never reach the mocked API call by default;
    include_private=True adds private but sealed still never leaves.
  - Bug 1 regression: an API failure during --force leaves a thread's
    pre-existing summaries (and their embeddings) untouched.
  - Bug 2 regression: a thread with some segments already on disk only pays
    for the missing/changed ones, and is not permanently skipped.
  - happy path: segment_id format, sensitivity inheritance, multi-segment
    threads.
"""

from __future__ import annotations

import json

import pytest

from mychatarchive import summarizer
from mychatarchive.backends.storage import sqlite as store


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Point config at an empty tmp file so a real dev config (e.g. a
    configured openai backend or custom dimension) can never leak into these
    tests, and so _resolve_api_key never touches real env/config."""
    import mychatarchive.config as config_mod

    config_path = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "get_config_path", lambda: config_path)
    for env in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(env, raising=False)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "archive.sqlite"
    con = store.get_connection(path)
    store.ensure_schema(con)
    con.close()
    return path


def _msg(con, mid, thread, text, ts, title, platform="chatgpt"):
    store.insert_message(con, mid, thread, platform, "main", ts, "user", text, title, "src")


class FakeAPI:
    """Stand-in for summarizer._call_api. Records every prompt it receives
    and can be told to fail on a specific (1-based) call."""

    def __init__(self, fail_at: int | None = None, fail_error: Exception | None = None):
        self.calls: list[str] = []
        self.fail_at = fail_at
        self.fail_error = fail_error or RuntimeError("simulated API failure")

    def __call__(self, prompt: str, api_key: str, base_url: str, model: str) -> dict:
        self.calls.append(prompt)
        if self.fail_at is not None and len(self.calls) == self.fail_at:
            raise self.fail_error
        n = len(self.calls)
        return {
            "choices": [
                {"message": {"content": json.dumps({
                    "summary": f"generated summary #{n}",
                    "key_topics": [f"topic-{n}"],
                })}}
            ]
        }


def _run(db_path, monkeypatch, fake_api, **kwargs):
    monkeypatch.setattr(summarizer, "_call_api", fake_api)
    kwargs.setdefault("api_key", "sk-test-not-real")
    kwargs.setdefault("embed_summaries", False)
    return summarizer.run(db_path, **kwargs)


# ── Scope: the egress control this whole module exists to enforce ─────────────

def test_default_scope_excludes_private_and_sealed(db_path, monkeypatch):
    con = store.get_connection(db_path)
    _msg(con, "m-pub", "pub", "project-x public MARKER-PUB", "2024-01-01T00:00:00", "Pub thread")
    _msg(con, "m-priv", "priv", "project-x private MARKER-PRIV", "2024-01-01T00:00:00", "Priv thread")
    _msg(con, "m-seal", "seal", "project-x sealed MARKER-SEAL", "2024-01-01T00:00:00", "Seal thread")
    con.commit()
    store.set_thread_sensitivity(con, ["priv"], "private")
    store.set_thread_sensitivity(con, ["seal"], "sealed")
    con.close()

    fake = FakeAPI()
    result = _run(db_path, monkeypatch, fake)

    assert result["processed"] == 1
    assert len(fake.calls) == 1
    assert "MARKER-PUB" in fake.calls[0]
    assert not any("MARKER-PRIV" in c or "MARKER-SEAL" in c for c in fake.calls)
    assert not any("Priv thread" in c or "Seal thread" in c for c in fake.calls)


def test_include_private_adds_private_but_never_sealed(db_path, monkeypatch):
    con = store.get_connection(db_path)
    _msg(con, "m-pub", "pub", "project-x public MARKER-PUB", "2024-01-01T00:00:00", "Pub thread")
    _msg(con, "m-priv", "priv", "project-x private MARKER-PRIV", "2024-01-01T00:00:00", "Priv thread")
    _msg(con, "m-seal", "seal", "project-x sealed MARKER-SEAL", "2024-01-01T00:00:00", "Seal thread")
    con.commit()
    store.set_thread_sensitivity(con, ["priv"], "private")
    store.set_thread_sensitivity(con, ["seal"], "sealed")
    con.close()

    # First pass: public only (as above) so the public thread is already
    # complete and won't be re-summarized on the second pass below.
    _run(db_path, monkeypatch, FakeAPI())

    fake2 = FakeAPI()
    result = _run(db_path, monkeypatch, fake2, include_private=True)

    assert result["processed"] == 1  # only "priv" was left incomplete in scope
    assert len(fake2.calls) == 1
    assert "MARKER-PRIV" in fake2.calls[0]
    assert not any("MARKER-SEAL" in c for c in fake2.calls)


# ── Bug 1: --force must not destroy summaries before the replacement lands ────

def test_force_failure_preserves_existing_summaries_and_embeddings(db_path, monkeypatch):
    con = store.get_connection(db_path)
    _msg(con, "m1", "t1", "project-x original content", "2024-01-01T00:00:00", "Original title")
    con.commit()
    con.close()

    def fake_embed(text):
        return [0.1] * 384

    import mychatarchive.embeddings as embeddings_mod
    monkeypatch.setattr(embeddings_mod, "embed_single", fake_embed)

    # Successful initial run, with embeddings this time, to establish a
    # baseline that --force must not be able to destroy on failure.
    ok = _run(db_path, monkeypatch, FakeAPI(), embed_summaries=True)
    assert ok["processed"] == 1

    con = store.get_connection(db_path)
    before = store.get_thread_summaries(con, "t1")
    assert len(before) == 1
    assert before[0][8] == "generated summary #1"
    vec_before = con.execute(
        "SELECT count(*) FROM vec_thread_summaries WHERE summary_id = 't1::0000'"
    ).fetchone()[0]
    assert vec_before == 1
    con.close()

    # Now --force with an API that fails on every call (e.g. an expired key).
    failing = FakeAPI(fail_at=1)
    result = _run(db_path, monkeypatch, failing, force=True, embed_summaries=True)
    assert result["processed"] == 0
    assert result["errors"] == 1

    con = store.get_connection(db_path)
    after = store.get_thread_summaries(con, "t1")
    assert len(after) == 1
    assert after[0][8] == "generated summary #1"  # untouched, not wiped
    vec_after = con.execute(
        "SELECT count(*) FROM vec_thread_summaries WHERE summary_id = 't1::0000'"
    ).fetchone()[0]
    assert vec_after == 1  # embedding survived too
    con.close()


# ── Bug 2: partial threads must be resumable, not permanently skipped ─────────

def test_resume_only_calls_api_for_segments_that_changed(db_path, monkeypatch):
    """Seed segments 0 and 1 directly (as if two earlier runs had completed
    them) with a thread now long enough for 3 segments. A run should only
    call the API for segment 2, and must not touch 0/1's stored summaries."""
    con = store.get_connection(db_path)
    now = "2024-01-01T00:00:00"
    # 5 messages, messages_per_segment=2 -> segments of size [2, 2, 1]
    for i in range(5):
        _msg(con, f"m{i}", "t1", f"project-x message {i}", now, "Growing thread")
    con.commit()

    store.insert_thread_summary(
        con, "t1::0000", "t1", 0, "Growing thread", "chatgpt", 2, 20, now, now,
        "already summarized segment 0", ["old-topic"], "prior-model", now,
    )
    store.insert_thread_summary(
        con, "t1::0001", "t1", 1, "Growing thread", "chatgpt", 2, 20, now, now,
        "already summarized segment 1", ["old-topic"], "prior-model", now,
    )
    con.commit()
    con.close()

    fake = FakeAPI()
    result = _run(db_path, monkeypatch, fake, messages_per_segment=2)

    assert len(fake.calls) == 1  # only segment 2 needed an API call
    assert result["processed"] == 1

    con = store.get_connection(db_path)
    segs = store.get_thread_summaries(con, "t1")
    con.close()
    assert len(segs) == 3
    by_index = {r[2]: r for r in segs}
    assert by_index[0][8] == "already summarized segment 0"  # untouched
    assert by_index[1][8] == "already summarized segment 1"  # untouched
    assert by_index[2][8] == "generated summary #1"  # newly generated


def test_thread_not_permanently_stuck_after_a_failed_run(db_path, monkeypatch):
    """Old bug: once any summary row existed for a thread, has_thread_summary()
    marked it complete forever. Verify a thread that fails on its first
    attempt is picked up again (and fully summarized) on the next run."""
    con = store.get_connection(db_path)
    _msg(con, "m1", "t1", "project-x retry-me content", "2024-01-01T00:00:00", "Retry thread")
    con.commit()
    con.close()

    failing = FakeAPI(fail_at=1)
    r1 = _run(db_path, monkeypatch, failing)
    assert r1["processed"] == 0
    assert r1["errors"] == 1

    con = store.get_connection(db_path)
    assert store.get_thread_summaries(con, "t1") == []
    con.close()

    succeeding = FakeAPI()
    r2 = _run(db_path, monkeypatch, succeeding)
    assert r2["processed"] == 1
    assert len(succeeding.calls) == 1

    con = store.get_connection(db_path)
    segs = store.get_thread_summaries(con, "t1")
    con.close()
    assert len(segs) == 1
    assert segs[0][8] == "generated summary #1"


def test_growing_thread_only_pays_for_the_new_segment(db_path, monkeypatch):
    """End-to-end (not pre-seeded): a thread fully summarized in one run,
    then grown, should only re-call the API for the new tail segment."""
    con = store.get_connection(db_path)
    now = "2024-01-01T00:00:00"
    _msg(con, "m0", "t1", "project-x first message", now, "Growing thread")
    _msg(con, "m1", "t1", "project-x second message", now, "Growing thread")
    con.commit()
    con.close()

    first = FakeAPI()
    r1 = _run(db_path, monkeypatch, first, messages_per_segment=2)
    assert r1["processed"] == 1
    assert len(first.calls) == 1  # one segment, 2/2 messages

    con = store.get_connection(db_path)
    _msg(con, "m2", "t1", "project-x third message", now, "Growing thread")
    con.commit()
    con.close()

    second = FakeAPI()
    r2 = _run(db_path, monkeypatch, second, messages_per_segment=2)
    assert r2["processed"] == 1
    assert len(second.calls) == 1  # only the new segment, not a re-summarize of segment 0

    con = store.get_connection(db_path)
    segs = store.get_thread_summaries(con, "t1")
    con.close()
    by_index = {r[2]: r for r in segs}
    assert len(segs) == 2
    assert by_index[0][8] == "generated summary #1"  # from run 1, untouched by run 2
    assert by_index[1][8] == "generated summary #1"  # run 2's own first (and only) call


# ── Happy path ──────────────────────────────────────────────────────────────

def test_happy_path_segment_ids_and_sensitivity(db_path, monkeypatch):
    con = store.get_connection(db_path)
    now = "2024-01-01T00:00:00"
    for i in range(3):
        _msg(con, f"m{i}", "t1", f"project-x message {i}", now, "Multi-segment thread")
    con.commit()
    store.set_thread_sensitivity(con, ["t1"], "private")
    con.close()

    fake = FakeAPI()
    result = _run(db_path, monkeypatch, fake, messages_per_segment=1, include_private=True)

    assert result["processed"] == 1
    assert result["segments"] == 3
    assert len(fake.calls) == 3

    con = store.get_connection(db_path)
    segs = store.get_thread_summaries(con, "t1", scope=store.SENSITIVITY_LEVELS)
    sens = con.execute(
        "SELECT sensitivity FROM thread_summaries WHERE canonical_thread_id='t1'"
    ).fetchall()
    con.close()
    assert [r[0] for r in segs] == ["t1::0000", "t1::0001", "t1::0002"]
    assert all(r[0] == "private" for r in sens)
