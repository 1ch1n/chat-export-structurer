"""Regression tests for `mychatarchive init` config preservation.

_cmd_init used to rebuild config.json from a hardcoded whitelist of six keys
(storage, embeddings, transport, drop_folder, auto_sources, sources), so any
other top-level key -- notably the documented `summarize` block (model,
base_url, messages_per_segment, api_key) written by `mychatarchive
summarize` -- was silently deleted on the next `init` run. It now starts
from the existing config and only fills in the keys it manages, so unknown
top-level keys always survive.
"""

import json

from mychatarchive import cli


def _run_init_noninteractive(monkeypatch):
    """Run _cmd_init with every prompt accepting its default (empty input)."""
    monkeypatch.setattr("builtins.input", lambda *a, **k: "")
    cli._cmd_init()


def test_init_preserves_summarize_and_unknown_keys(tmp_path, monkeypatch, capsys):
    import mychatarchive.config as config_mod

    config_file = tmp_path / "config.json"
    original = {
        "storage": {"backend": "sqlite", "path": str(tmp_path / "archive.db")},
        "embeddings": {"backend": "local"},
        "transport": {"type": "stdio"},
        "drop_folder": str(tmp_path / "imports"),
        "auto_sources": {"claude_code": True, "cursor": True},
        "sources": {},
        "summarize": {
            "api_key": "sk-test-placeholder-key",
            "model": "project-x/test-model",
            "base_url": "https://example.test/api/v1",
            "messages_per_segment": 15,
        },
        # Stand-in for any future top-level key init doesn't know about --
        # e.g. a setting added by a later feature. It must survive too.
        "future_feature_block": {"enabled": True, "note": "unrelated to init"},
    }
    config_file.write_text(json.dumps(original))
    monkeypatch.setattr(config_mod, "get_config_path", lambda: config_file)

    _run_init_noninteractive(monkeypatch)
    capsys.readouterr()

    saved = json.loads(config_file.read_text())
    assert saved["summarize"] == original["summarize"]
    assert saved["future_feature_block"] == original["future_feature_block"]
    # The keys init does manage are still present and sane.
    assert saved["storage"]["backend"] == "sqlite"
    assert saved["drop_folder"] == str(tmp_path / "imports")


def test_init_on_fresh_config_still_sets_managed_defaults(tmp_path, monkeypatch, capsys):
    """No pre-existing config.json: init must still populate the keys it manages."""
    import mychatarchive.config as config_mod

    config_file = tmp_path / "config.json"  # does not exist yet
    monkeypatch.setattr(config_mod, "get_config_path", lambda: config_file)
    monkeypatch.setattr(
        config_mod, "_DEFAULT_DROP_FOLDER", str(tmp_path / "imports"), raising=False
    )
    # _cmd_init imports _DEFAULT_DROP_FOLDER by value at call time from the
    # config module, so patch it there before _cmd_init's local import runs.

    _run_init_noninteractive(monkeypatch)
    capsys.readouterr()

    saved = json.loads(config_file.read_text())
    for key in ("storage", "embeddings", "transport", "drop_folder", "auto_sources", "sources"):
        assert key in saved
    assert "summarize" not in saved  # nothing to preserve, nothing invented
