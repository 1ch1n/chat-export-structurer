"""CLI regression tests for argparse wiring and dispatch."""

import sqlite3
import sys

import pytest

from mychatarchive import cli


def _run(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["mychatarchive"] + argv)
    cli.main()


def test_bare_groups_without_db_attr_does_not_crash(tmp_path, monkeypatch, capsys):
    # Regression for issue #10 / PR #11: `mychatarchive groups` used to raise
    # AttributeError because the groups parser namespace had no `db` attribute.
    missing = tmp_path / "nope.db"
    monkeypatch.setattr(cli, "get_db_path", lambda: missing)
    with pytest.raises(SystemExit) as exc:
        _run(["groups"], monkeypatch)
    assert exc.value.code == 1
    assert "No database found" in capsys.readouterr().err


def test_groups_subcommands_accept_db_flag(tmp_path, monkeypatch, capsys):
    db_file = tmp_path / "archive.db"
    sqlite3.connect(db_file).close()  # _cmd_groups requires the file to exist

    _run(["groups", "create", "jarvis", "--db", str(db_file)], monkeypatch)
    assert "created" in capsys.readouterr().out

    _run(["groups", "list", "--db", str(db_file)], monkeypatch)
    assert "jarvis" in capsys.readouterr().out


def _seeded_db(tmp_path):
    """Archive with two threads for classify tests."""
    from mychatarchive.backends.storage import sqlite as store

    db_file = tmp_path / "archive.db"
    con = store.get_connection(db_file)
    store.ensure_schema(con)
    store.insert_message(con, "m1", "t-px", "chatgpt", "main",
                         "2024-01-01T00:00:00", "user",
                         "planning the project-x rollout", "Project X", "s")
    store.insert_message(con, "m2", "t-code", "chatgpt", "main",
                         "2024-06-01T00:00:00", "user",
                         "rust borrow checker", "Code", "s")
    con.commit()
    con.close()
    return db_file


def test_classify_query_never_applies_without_confirm(tmp_path, monkeypatch, capsys):
    db_file = _seeded_db(tmp_path)

    _run(["classify", "--query", "project-x", "--level", "sealed",
          "--db", str(db_file)], monkeypatch)
    out = capsys.readouterr().out
    assert "Preview only" in out

    _run(["classify", "--list", "--db", str(db_file)], monkeypatch)
    assert "sealed" in capsys.readouterr().out  # header row only
    con = sqlite3.connect(db_file)
    sealed = con.execute(
        "SELECT count(*) FROM messages WHERE sensitivity='sealed'"
    ).fetchone()[0]
    con.close()
    assert sealed == 0


def test_classify_query_applies_with_confirm(tmp_path, monkeypatch, capsys):
    db_file = _seeded_db(tmp_path)

    _run(["classify", "--query", "project-x", "--level", "sealed", "--confirm",
          "--db", str(db_file)], monkeypatch)
    assert "Applied 'sealed' to 1 threads" in capsys.readouterr().out

    con = sqlite3.connect(db_file)
    rows = dict(con.execute(
        "SELECT canonical_thread_id, sensitivity FROM messages"
    ).fetchall())
    con.close()
    assert rows == {"t-px": "sealed", "t-code": "public"}


def test_classify_dry_run_wins_over_confirm(tmp_path, monkeypatch, capsys):
    db_file = _seeded_db(tmp_path)

    _run(["classify", "--query", "project-x", "--level", "sealed",
          "--confirm", "--dry-run", "--db", str(db_file)], monkeypatch)
    assert "Dry run" in capsys.readouterr().out
    con = sqlite3.connect(db_file)
    sealed = con.execute(
        "SELECT count(*) FROM messages WHERE sensitivity='sealed'"
    ).fetchone()[0]
    con.close()
    assert sealed == 0


def test_classify_thread_dry_run_does_not_apply(tmp_path, monkeypatch, capsys):
    # Regression: the single-thread branch ignored --dry-run and applied the
    # change immediately, so a preview silently mutated the archive.
    db_file = _seeded_db(tmp_path)

    _run(["classify", "--thread", "t-px", "--level", "sealed", "--dry-run",
          "--db", str(db_file)], monkeypatch)
    out = capsys.readouterr().out
    assert "Dry run" in out
    assert "public -> sealed" in out  # preview still shows the intended change

    con = sqlite3.connect(db_file)
    levels = {r[0] for r in con.execute("SELECT sensitivity FROM messages")}
    con.close()
    assert levels == {"public"}, "dry run must not modify the archive"


def test_classify_thread_applies_without_dry_run(tmp_path, monkeypatch, capsys):
    db_file = _seeded_db(tmp_path)

    _run(["classify", "--thread", "t-px", "--level", "sealed",
          "--db", str(db_file)], monkeypatch)
    assert "public -> sealed" in capsys.readouterr().out

    con = sqlite3.connect(db_file)
    rows = dict(con.execute(
        "SELECT canonical_thread_id, sensitivity FROM messages"
    ).fetchall())
    con.close()
    assert rows == {"t-px": "sealed", "t-code": "public"}


def test_classify_before_is_thread_scoped_on_last_message(tmp_path, monkeypatch, capsys):
    db_file = _seeded_db(tmp_path)

    _run(["classify", "--before", "2024-03-01", "--level", "private", "--confirm",
          "--db", str(db_file)], monkeypatch)
    con = sqlite3.connect(db_file)
    rows = dict(con.execute(
        "SELECT canonical_thread_id, sensitivity FROM messages"
    ).fetchall())
    con.close()
    assert rows == {"t-px": "private", "t-code": "public"}


def test_sqlite_export_refuses_sealed_without_flag(tmp_path, monkeypatch, capsys):
    db_file = _seeded_db(tmp_path)
    _run(["classify", "--thread", "t-px", "--level", "sealed",
          "--db", str(db_file)], monkeypatch)
    capsys.readouterr()

    out_file = tmp_path / "copy.db"
    with pytest.raises(SystemExit) as exc:
        _run(["export", str(out_file), "--db", str(db_file)], monkeypatch)
    assert exc.value.code == 1
    assert "SEALED" in capsys.readouterr().err
    assert not out_file.exists()

    _run(["export", str(out_file), "--include-sealed", "--db", str(db_file)],
         monkeypatch)
    assert "copied" in capsys.readouterr().out
    assert out_file.exists()


def _seed_many_matching_threads(tmp_path, n):
    """n threads, each with one message matching 'WIDGET', plus one distractor
    thread that does not match. Synthetic stand-in for "more matching threads
    than any row cap" -- see test_fts_search_thread_ids_covers_every_matching_
    thread_beyond_any_row_cap in test_sensitivity.py for the storage-level
    version of this regression at the point where it actually bites (a
    message-row LIMIT, not a thread count)."""
    from mychatarchive.backends.storage import sqlite as store

    db_file = tmp_path / "archive.db"
    con = store.get_connection(db_file)
    store.ensure_schema(con)
    for i in range(n):
        store.insert_message(con, f"m-{i}", f"thread-{i}", "chatgpt", "main",
                             "2024-01-01T00:00:00", "user",
                             "WIDGET rollout notes", f"Title {i}", "s")
    store.insert_message(con, "m-distractor", "t-other", "chatgpt", "main",
                         "2024-01-01T00:00:00", "user",
                         "unrelated content", "Other", "s")
    con.commit()
    con.close()
    return db_file


def test_classify_query_covers_every_matching_thread(tmp_path, monkeypatch, capsys):
    # Regression: --query used to derive thread_ids from a row-limited
    # fts_search (limit=10000), so threads whose match fell outside that cap
    # were never classified. Every matching thread must be covered.
    n = 15
    db_file = _seed_many_matching_threads(tmp_path, n)

    _run(["classify", "--query", "WIDGET", "--level", "private", "--confirm",
          "--db", str(db_file)], monkeypatch)
    out = capsys.readouterr().out
    assert f"Applied 'private' to {n:,} threads" in out

    con = sqlite3.connect(db_file)
    levels = dict(con.execute("SELECT canonical_thread_id, sensitivity FROM messages"))
    con.close()
    assert all(levels[f"thread-{i}"] == "private" for i in range(n))
    assert levels["t-other"] == "public"  # non-matching thread untouched


def test_classify_query_uses_uncapped_thread_lookup(tmp_path, monkeypatch, capsys):
    # Wiring check: --query selection must go through the dedicated uncapped
    # fts_search_thread_ids, not the row-limited fts_search used for
    # interactive message search/display.
    db_file = _seed_many_matching_threads(tmp_path, 3)

    from mychatarchive import db as db_module
    calls = {"fts_search": 0, "fts_search_thread_ids": 0}
    orig_fts_search = db_module.fts_search
    orig_thread_ids = db_module.fts_search_thread_ids

    def spy_fts_search(*a, **k):
        calls["fts_search"] += 1
        return orig_fts_search(*a, **k)

    def spy_thread_ids(*a, **k):
        calls["fts_search_thread_ids"] += 1
        return orig_thread_ids(*a, **k)

    monkeypatch.setattr(db_module, "fts_search", spy_fts_search)
    monkeypatch.setattr(db_module, "fts_search_thread_ids", spy_thread_ids)

    _run(["classify", "--query", "WIDGET", "--level", "private", "--confirm",
          "--db", str(db_file)], monkeypatch)

    assert calls["fts_search_thread_ids"] == 1
    assert calls["fts_search"] == 0
