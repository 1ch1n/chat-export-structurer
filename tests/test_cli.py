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
    store.insert_message(con, "m1", "t-med", "chatgpt", "main",
                         "2024-01-01T00:00:00", "user",
                         "my medical appointment", "Medical", "s")
    store.insert_message(con, "m2", "t-code", "chatgpt", "main",
                         "2024-06-01T00:00:00", "user",
                         "rust borrow checker", "Code", "s")
    con.commit()
    con.close()
    return db_file


def test_classify_query_never_applies_without_confirm(tmp_path, monkeypatch, capsys):
    db_file = _seeded_db(tmp_path)

    _run(["classify", "--query", "medical", "--level", "sealed",
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

    _run(["classify", "--query", "medical", "--level", "sealed", "--confirm",
          "--db", str(db_file)], monkeypatch)
    assert "Applied 'sealed' to 1 threads" in capsys.readouterr().out

    con = sqlite3.connect(db_file)
    rows = dict(con.execute(
        "SELECT canonical_thread_id, sensitivity FROM messages"
    ).fetchall())
    con.close()
    assert rows == {"t-med": "sealed", "t-code": "public"}


def test_classify_dry_run_wins_over_confirm(tmp_path, monkeypatch, capsys):
    db_file = _seeded_db(tmp_path)

    _run(["classify", "--query", "medical", "--level", "sealed",
          "--confirm", "--dry-run", "--db", str(db_file)], monkeypatch)
    assert "Dry run" in capsys.readouterr().out
    con = sqlite3.connect(db_file)
    sealed = con.execute(
        "SELECT count(*) FROM messages WHERE sensitivity='sealed'"
    ).fetchone()[0]
    con.close()
    assert sealed == 0


def test_classify_before_is_thread_scoped_on_last_message(tmp_path, monkeypatch, capsys):
    db_file = _seeded_db(tmp_path)

    _run(["classify", "--before", "2024-03-01", "--level", "private", "--confirm",
          "--db", str(db_file)], monkeypatch)
    con = sqlite3.connect(db_file)
    rows = dict(con.execute(
        "SELECT canonical_thread_id, sensitivity FROM messages"
    ).fetchall())
    con.close()
    assert rows == {"t-med": "private", "t-code": "public"}


def test_sqlite_export_refuses_sealed_without_flag(tmp_path, monkeypatch, capsys):
    db_file = _seeded_db(tmp_path)
    _run(["classify", "--thread", "t-med", "--level", "sealed",
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
