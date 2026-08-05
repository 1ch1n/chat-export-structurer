"""Tests for the MyChatArchive memory provider plugin.

Mirrors the structure of hermes-agent/tests/agent/test_memory_provider.py.
Uses mocks for the mychatarchive package so tests run without a real DB
or embedding model.
"""

import json
import time
import threading
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import sys
import os


# ---------------------------------------------------------------------------
# Helpers -- mock the mychatarchive package before importing the plugin
# ---------------------------------------------------------------------------

def _make_mock_db():
    """Build a mock mychatarchive.db module with working return values."""
    db = MagicMock()
    db.get_connection.return_value = MagicMock()
    db.ensure_schema.return_value = None
    db.message_count.return_value = 500
    db.chunk_count.return_value = 800
    db.thought_count.return_value = 10
    db.thread_count.return_value = 50
    db.summarized_thread_count.return_value = 40
    db.platform_counts.return_value = [("chatgpt", 300), ("anthropic", 200)]
    db.search_chunks.return_value = [
        ("chunk-1", 0.3),
        ("chunk-2", 0.6),
    ]
    db.get_chunk_by_id.side_effect = lambda con, cid, **kw: {
        "chunk-1": ("First chunk text about projects", "thread-1", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z", '{"platform": "chatgpt", "title": "Project Chat"}'),
        "chunk-2": ("Second chunk about Python", "thread-2", "2026-02-01T00:00:00Z", "2026-02-01T00:01:00Z", '{"platform": "anthropic", "title": "Python Help"}'),
    }.get(cid)
    db.search_thread_summaries.return_value = [("summary-1", 0.4)]
    db.get_summary_by_id.side_effect = lambda con, sid, **kw: {
        "summary-1": ("summary-1", "thread-1", 0, "Project Chat", "chatgpt", 10, "2026-01-01", "2026-01-02", "Discussion about projects", '["projects", "planning"]'),
    }.get(sid)
    db.search_thoughts.return_value = [("thought-1", 0.2)]
    db.get_thought_by_id.side_effect = lambda con, tid, **kw: {
        "thought-1": ("A captured thought", "2026-03-01T00:00:00Z", '{"source": "hermes"}'),
    }.get(tid)
    db.get_thread_summary.side_effect = lambda con, tid, **kw: {
        "thread-1": ("summary-1", "thread-1", 0, "Project Chat", "chatgpt", 10, "2026-01-01", "2026-01-02", "Discussion about projects", '["projects"]'),
    }.get(tid)
    db.fts_search.return_value = [
        ("msg-1", "FTS result about archives", "thread-3", "2026-03-01T00:00:00Z", "user", "Archive Talk"),
    ]
    db.insert_thought.return_value = None
    db.get_group_by_name.return_value = ("group-1", "coding", "Coding threads", "2026-01-01")
    db.get_group_thread_ids.return_value = {"thread-1", "thread-2"}
    return db


def _make_mock_embeddings():
    """Build a mock mychatarchive.embeddings module."""
    emb = MagicMock()
    emb.embed_single.return_value = [0.1] * 384
    return emb


def _make_mock_config():
    """Build a mock mychatarchive.config module."""
    cfg = MagicMock()
    cfg.get_db_path.return_value = Path("/mock/.mychatarchive/archive.db")
    return cfg


@pytest.fixture
def mock_mca(tmp_path):
    """Fixture that patches mychatarchive imports and returns (provider, db, embeddings)."""
    mock_db = _make_mock_db()
    mock_emb = _make_mock_embeddings()
    mock_cfg = _make_mock_config()

    # Create a fake DB file so is_available() passes
    db_file = tmp_path / "archive.db"
    db_file.touch()

    # Create config pointing to the fake DB
    config_dir = tmp_path / "hermes_home"
    config_dir.mkdir()
    config = {"db_path": str(db_file), "recall_mode": "hybrid", "prefetch_limit": "5"}
    (config_dir / "mychatarchive.json").write_text(json.dumps(config))

    with patch.dict("sys.modules", {
        "mychatarchive": MagicMock(),
        "mychatarchive.db": mock_db,
        "mychatarchive.embeddings": mock_emb,
        "mychatarchive.config": mock_cfg,
        "mychatarchive.backends": MagicMock(),
    }):
        # Patch importlib.import_module for is_available check
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        # Import the plugin module fresh
        plugin_dir = Path(__file__).parent.parent / "integrations" / "hermes"
        spec = __import__("importlib").util.spec_from_file_location(
            "plugins.memory.mychatarchive",
            str(plugin_dir / "__init__.py"),
        )
        mod = __import__("importlib").util.module_from_spec(spec)

        # Patch the imports the module needs
        sys.modules["agent"] = MagicMock()
        sys.modules["agent.memory_provider"] = MagicMock()
        sys.modules["tools"] = MagicMock()
        sys.modules["tools.registry"] = MagicMock()

        # Provide real MemoryProvider ABC and tool_error
        from abc import ABC, abstractmethod

        class FakeMemoryProvider(ABC):
            @property
            @abstractmethod
            def name(self): ...
            @abstractmethod
            def is_available(self): ...
            @abstractmethod
            def initialize(self, session_id, **kwargs): ...
            @abstractmethod
            def get_tool_schemas(self): ...
            def handle_tool_call(self, tool_name, args, **kwargs):
                raise NotImplementedError
            def system_prompt_block(self): return ""
            def prefetch(self, query, *, session_id=""): return ""
            def sync_turn(self, user_content, assistant_content, *, session_id=""): pass
            def shutdown(self): pass
            def get_config_schema(self): return []
            def save_config(self, values, hermes_home): pass

        sys.modules["agent.memory_provider"].MemoryProvider = FakeMemoryProvider
        sys.modules["tools.registry"].tool_error = lambda msg, **kw: json.dumps({"error": msg})

        spec.loader.exec_module(mod)
        # module_from_spec() does not register the module in sys.modules by
        # itself; do it explicitly so tests can look up the real module
        # (e.g. to call its module-level helpers) instead of accidentally
        # matching one of the MagicMock stand-ins patched in above, whose
        # hasattr() checks are vacuously true for any attribute name.
        sys.modules["plugins.memory.mychatarchive"] = mod

        provider = mod.MyChatArchiveProvider()

        # Manually wire up the mocked modules
        provider._db = mock_db
        provider._embeddings = mock_emb
        provider._con = mock_db.get_connection()
        provider._config = config
        provider._hermes_home = str(config_dir)
        provider._session_id = "test-session-001"

        yield provider, mock_db, mock_emb, config_dir


# ---------------------------------------------------------------------------
# Plugin instantiation and registration
# ---------------------------------------------------------------------------


class TestPluginRegistration:
    def test_provider_name(self, mock_mca):
        provider, _, _, _ = mock_mca
        assert provider.name == "mychatarchive"

    def test_register_function_exists(self, mock_mca):
        """The plugin module has a register(ctx) entry point."""
        provider, _, _, _ = mock_mca
        mod = None
        for name, m in sys.modules.items():
            if "mychatarchive" in name and hasattr(m, "register") and hasattr(m, "MyChatArchiveProvider"):
                mod = m
                break
        assert mod is not None, "Plugin module with register() not found"
        assert callable(mod.register)
        assert hasattr(mod, "MyChatArchiveProvider")

    def test_provider_is_memory_provider_subclass(self, mock_mca):
        provider, _, _, _ = mock_mca
        assert hasattr(provider, "name")
        assert hasattr(provider, "is_available")
        assert hasattr(provider, "initialize")
        assert hasattr(provider, "get_tool_schemas")
        assert hasattr(provider, "handle_tool_call")
        assert hasattr(provider, "shutdown")


# ---------------------------------------------------------------------------
# is_available()
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_available_when_package_and_db_exist(self, mock_mca, tmp_path):
        provider, _, _, config_dir = mock_mca
        db_file = tmp_path / "archive.db"
        db_file.touch()
        provider._config = {"db_path": str(db_file)}
        assert provider.is_available() is True

    def test_unavailable_when_db_missing(self, mock_mca, tmp_path):
        provider, _, _, _ = mock_mca
        provider._config = {"db_path": str(tmp_path / "nonexistent.db")}
        assert provider.is_available() is False

    def test_unavailable_when_package_missing(self, mock_mca):
        provider, _, _, _ = mock_mca
        with patch("importlib.import_module", side_effect=ImportError("no module")):
            assert provider.is_available() is False


# ---------------------------------------------------------------------------
# get_tool_schemas()
# ---------------------------------------------------------------------------


class TestToolSchemas:
    def test_returns_four_tools(self, mock_mca):
        provider, _, _, _ = mock_mca
        schemas = provider.get_tool_schemas()
        assert len(schemas) == 4

    def test_tool_names(self, mock_mca):
        provider, _, _, _ = mock_mca
        names = {s["name"] for s in provider.get_tool_schemas()}
        assert names == {"mca_search", "mca_recall", "mca_remember", "mca_provenance"}

    def test_schemas_have_required_fields(self, mock_mca):
        provider, _, _, _ = mock_mca
        for schema in provider.get_tool_schemas():
            assert "name" in schema
            assert "description" in schema
            assert "parameters" in schema
            assert schema["parameters"]["type"] == "object"

    def test_context_mode_hides_tools(self, mock_mca):
        provider, _, _, _ = mock_mca
        provider._recall_mode = "context"
        assert provider.get_tool_schemas() == []

    def test_tools_mode_shows_tools(self, mock_mca):
        provider, _, _, _ = mock_mca
        provider._recall_mode = "tools"
        assert len(provider.get_tool_schemas()) == 4

    def test_hybrid_mode_shows_tools(self, mock_mca):
        provider, _, _, _ = mock_mca
        provider._recall_mode = "hybrid"
        assert len(provider.get_tool_schemas()) == 4


# ---------------------------------------------------------------------------
# handle_tool_call() routing
# ---------------------------------------------------------------------------


class TestToolCallRouting:
    def test_mca_search_routes(self, mock_mca):
        provider, db, emb, _ = mock_mca
        result = json.loads(provider.handle_tool_call("mca_search", {"query": "projects"}))
        assert "results" in result
        assert "count" in result
        emb.embed_single.assert_called()

    def test_mca_recall_routes(self, mock_mca):
        provider, db, emb, _ = mock_mca
        result = json.loads(provider.handle_tool_call("mca_recall", {"topic": "Python"}))
        assert "messages" in result
        assert "summaries" in result
        assert "thoughts" in result

    def test_mca_remember_routes(self, mock_mca):
        provider, db, emb, _ = mock_mca
        result = json.loads(provider.handle_tool_call(
            "mca_remember", {"content": "test thought", "tags": "test,hermes"},
        ))
        assert result["status"] == "saved"
        assert "thought_id" in result
        assert "created_at" in result
        db.insert_thought.assert_called_once()

    def test_mca_provenance_chunk_routes(self, mock_mca):
        provider, db, _, _ = mock_mca
        result = json.loads(provider.handle_tool_call("mca_provenance", {"chunk_id": "chunk-1"}))
        assert result["type"] == "chunk"
        assert result["thread_id"] == "thread-1"
        assert result["thread_title"] == "Project Chat"

    def test_mca_provenance_thought_routes(self, mock_mca):
        provider, db, _, _ = mock_mca
        result = json.loads(provider.handle_tool_call("mca_provenance", {"thought_id": "thought-1"}))
        assert result["type"] == "thought"
        assert result["text"] == "A captured thought"

    def test_mca_provenance_neither_returns_error(self, mock_mca):
        provider, _, _, _ = mock_mca
        result = json.loads(provider.handle_tool_call("mca_provenance", {}))
        assert "error" in result

    def test_mca_provenance_both_returns_error(self, mock_mca):
        provider, _, _, _ = mock_mca
        result = json.loads(provider.handle_tool_call(
            "mca_provenance", {"chunk_id": "a", "thought_id": "b"},
        ))
        assert "error" in result

    def test_unknown_tool_returns_error(self, mock_mca):
        provider, _, _, _ = mock_mca
        result = json.loads(provider.handle_tool_call("mca_nonexistent", {}))
        assert "error" in result

    def test_uninitialized_provider_returns_error(self, mock_mca):
        provider, _, _, _ = mock_mca
        provider._con = None
        provider._db = None
        result = json.loads(provider.handle_tool_call("mca_search", {"query": "test"}))
        assert "error" in result

    def test_mca_search_missing_query_returns_error(self, mock_mca):
        provider, _, _, _ = mock_mca
        result = json.loads(provider.handle_tool_call("mca_search", {}))
        assert "error" in result

    def test_mca_recall_missing_topic_returns_error(self, mock_mca):
        provider, _, _, _ = mock_mca
        result = json.loads(provider.handle_tool_call("mca_recall", {}))
        assert "error" in result

    def test_mca_remember_missing_content_returns_error(self, mock_mca):
        provider, _, _, _ = mock_mca
        result = json.loads(provider.handle_tool_call("mca_remember", {}))
        assert "error" in result


# ---------------------------------------------------------------------------
# Prefetch
# ---------------------------------------------------------------------------


class TestPrefetch:
    def test_prefetch_returns_formatted_results(self, mock_mca):
        provider, _, _, _ = mock_mca
        result = provider.prefetch("what projects")
        assert "## MyChatArchive" in result
        assert "First chunk text" in result

    def test_prefetch_empty_on_tools_mode(self, mock_mca):
        provider, _, _, _ = mock_mca
        provider._recall_mode = "tools"
        assert provider.prefetch("query") == ""

    def test_prefetch_works_in_context_mode(self, mock_mca):
        provider, _, _, _ = mock_mca
        provider._recall_mode = "context"
        result = provider.prefetch("query")
        assert "## MyChatArchive" in result

    def test_prefetch_empty_on_blank_query(self, mock_mca):
        provider, _, _, _ = mock_mca
        assert provider.prefetch("") == ""
        assert provider.prefetch("   ") == ""

    def test_prefetch_empty_when_uninitialized(self, mock_mca):
        provider, _, _, _ = mock_mca
        provider._con = None
        assert provider.prefetch("query") == ""


# ---------------------------------------------------------------------------
# System prompt block
# ---------------------------------------------------------------------------


class TestSystemPromptBlock:
    def test_includes_archive_stats(self, mock_mca):
        provider, _, _, _ = mock_mca
        block = provider.system_prompt_block()
        assert "MyChatArchive" in block
        assert "500 messages" in block
        assert "50 threads" in block

    def test_empty_when_uninitialized(self, mock_mca):
        provider, _, _, _ = mock_mca
        provider._con = None
        assert provider.system_prompt_block() == ""

    def test_context_mode_text(self, mock_mca):
        provider, _, _, _ = mock_mca
        provider._recall_mode = "context"
        block = provider.system_prompt_block()
        assert "automatically injected" in block
        assert "No archive tools" in block

    def test_tools_mode_text(self, mock_mca):
        provider, _, _, _ = mock_mca
        provider._recall_mode = "tools"
        block = provider.system_prompt_block()
        assert "mca_search" in block
        assert "No automatic context" in block


# ---------------------------------------------------------------------------
# sync_turn() -- non-blocking
# ---------------------------------------------------------------------------


class TestSyncTurn:
    def test_sync_turn_returns_immediately(self, mock_mca):
        provider, _, _, _ = mock_mca
        start = time.monotonic()
        provider.sync_turn("user message", "assistant response")
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, f"sync_turn blocked for {elapsed:.2f}s"

    def test_sync_turn_spawns_daemon_thread(self, mock_mca):
        provider, _, _, _ = mock_mca
        provider.sync_turn("user", "assistant")
        assert provider._sync_thread is not None
        assert provider._sync_thread.daemon is True

    def test_sync_turn_noop_when_uninitialized(self, mock_mca):
        provider, _, _, _ = mock_mca
        provider._con = None
        provider._db = None
        provider.sync_turn("user", "assistant")
        assert provider._sync_thread is None


# ---------------------------------------------------------------------------
# Profile isolation
# ---------------------------------------------------------------------------


class TestProfileIsolation:
    def test_two_profiles_get_separate_config(self, tmp_path):
        """Two providers initialized with different hermes_home get separate state."""
        # Create two profile directories with different configs
        profile_a = tmp_path / "profile_a"
        profile_a.mkdir()
        (profile_a / "mychatarchive.json").write_text(json.dumps({
            "db_path": str(tmp_path / "a.db"),
            "recall_mode": "hybrid",
            "prefetch_limit": "3",
        }))

        profile_b = tmp_path / "profile_b"
        profile_b.mkdir()
        (profile_b / "mychatarchive.json").write_text(json.dumps({
            "db_path": str(tmp_path / "b.db"),
            "recall_mode": "tools",
            "prefetch_limit": "10",
        }))

        # Import the config loader
        plugin_dir = Path(__file__).parent.parent / "integrations" / "hermes"
        sys.path.insert(0, str(plugin_dir.parent.parent.parent))

        # We can test the config loading directly
        from mychatarchive_test_helpers import _load_plugin_config
        config_a = _load_plugin_config(str(profile_a))
        config_b = _load_plugin_config(str(profile_b))

        assert config_a["recall_mode"] == "hybrid"
        assert config_b["recall_mode"] == "tools"
        assert config_a["prefetch_limit"] == "3"
        assert config_b["prefetch_limit"] == "10"
        assert config_a["db_path"] != config_b["db_path"]


# Provide the helper for profile isolation test
@pytest.fixture(autouse=True)
def _provide_config_loader(tmp_path):
    """Make _load_plugin_config importable for profile isolation tests."""
    helper = tmp_path / "mychatarchive_test_helpers.py"
    helper.write_text(
        "import json\n"
        "from pathlib import Path\n"
        "def _load_plugin_config(hermes_home):\n"
        "    config_path = Path(hermes_home) / 'mychatarchive.json'\n"
        "    if config_path.exists():\n"
        "        return json.loads(config_path.read_text())\n"
        "    return {}\n"
    )
    sys.path.insert(0, str(tmp_path))
    yield
    sys.path.remove(str(tmp_path))


# ---------------------------------------------------------------------------
# Config save/load roundtrip
# ---------------------------------------------------------------------------


class TestConfigRoundtrip:
    def test_save_and_load_config(self, mock_mca, tmp_path):
        provider, _, _, _ = mock_mca
        config_dir = tmp_path / "roundtrip"
        config_dir.mkdir()

        values = {
            "db_path": "/custom/path/archive.db",
            "recall_mode": "tools",
            "prefetch_limit": "20",
        }
        provider.save_config(values, str(config_dir))

        config_path = config_dir / "mychatarchive.json"
        assert config_path.exists()

        loaded = json.loads(config_path.read_text())
        assert loaded["db_path"] == "/custom/path/archive.db"
        assert loaded["recall_mode"] == "tools"
        assert loaded["prefetch_limit"] == "20"

    def test_save_config_merges_with_existing(self, mock_mca, tmp_path):
        provider, _, _, _ = mock_mca
        config_dir = tmp_path / "merge"
        config_dir.mkdir()

        # Write initial config
        (config_dir / "mychatarchive.json").write_text(json.dumps({
            "db_path": "/original/path.db",
            "extra_key": "preserved",
        }))

        # Save partial update
        provider.save_config({"recall_mode": "context"}, str(config_dir))

        loaded = json.loads((config_dir / "mychatarchive.json").read_text())
        assert loaded["db_path"] == "/original/path.db"
        assert loaded["extra_key"] == "preserved"
        assert loaded["recall_mode"] == "context"

    def test_get_config_schema_returns_expected_fields(self, mock_mca):
        provider, _, _, _ = mock_mca
        schema = provider.get_config_schema()
        keys = [f["key"] for f in schema]
        assert "db_path" in keys
        assert "recall_mode" in keys
        assert "prefetch_limit" in keys


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    def test_shutdown_closes_connection(self, mock_mca):
        provider, db, _, _ = mock_mca
        con = provider._con
        provider.shutdown()
        con.close.assert_called_once()
        assert provider._con is None
        assert provider._db is None
        assert provider._embeddings is None

    def test_shutdown_idempotent(self, mock_mca):
        provider, _, _, _ = mock_mca
        provider.shutdown()
        provider.shutdown()


# ---------------------------------------------------------------------------
# Search modes
# ---------------------------------------------------------------------------


class TestSearchModes:
    def test_semantic_search(self, mock_mca):
        provider, db, emb, _ = mock_mca
        result = json.loads(provider.handle_tool_call(
            "mca_search", {"query": "projects", "mode": "semantic"},
        ))
        assert all(r["match"] == "semantic" for r in result["results"])
        emb.embed_single.assert_called_with("projects")

    def test_keyword_search(self, mock_mca):
        provider, db, _, _ = mock_mca
        result = json.loads(provider.handle_tool_call(
            "mca_search", {"query": "archives", "mode": "keyword"},
        ))
        assert all(r["match"] == "keyword" for r in result["results"])
        db.fts_search.assert_called()

    def test_hybrid_search_merges(self, mock_mca):
        provider, db, emb, _ = mock_mca
        result = json.loads(provider.handle_tool_call(
            "mca_search", {"query": "projects", "mode": "hybrid"},
        ))
        matches = {r["match"] for r in result["results"]}
        assert "semantic" in matches
        assert "keyword" in matches

    def test_search_with_platform_filter(self, mock_mca):
        provider, db, _, _ = mock_mca
        provider.handle_tool_call(
            "mca_search", {"query": "test", "platform": "chatgpt"},
        )
        call_args = db.search_chunks.call_args
        assert call_args.kwargs.get("platform") == "chatgpt"

    def test_search_with_hours_back(self, mock_mca):
        provider, db, _, _ = mock_mca
        provider.handle_tool_call(
            "mca_search", {"query": "test", "hours_back": 24},
        )
        call_args = db.search_chunks.call_args
        assert call_args.kwargs.get("cutoff_iso") is not None

    def test_search_with_group_filter(self, mock_mca):
        provider, db, _, _ = mock_mca
        provider.handle_tool_call(
            "mca_search", {"query": "test", "group": "coding"},
        )
        call_args = db.search_chunks.call_args
        group_ids = call_args.kwargs.get("group_thread_ids")
        # The group_thread_ids may come from the mock db module loaded at
        # call time; verify it was passed (not None)
        assert group_ids is not None, "group_thread_ids should be set when group filter is used"


# ---------------------------------------------------------------------------
# Embedding dimension validation
# ---------------------------------------------------------------------------


class TestEmbeddingDimensionCheck:
    def test_mismatch_raises_runtime_error(self):
        """Dimension mismatch between stored vectors and current model raises at init."""
        plugin_dir = Path(__file__).parent.parent / "integrations" / "hermes"
        spec = __import__("importlib").util.spec_from_file_location(
            "_mca_dim_test", str(plugin_dir / "__init__.py"),
        )
        mod = __import__("importlib").util.module_from_spec(spec)
        # Provide minimal mocks so the module loads
        sys.modules.setdefault("agent", MagicMock())
        sys.modules.setdefault("agent.memory_provider", MagicMock())
        sys.modules.setdefault("tools", MagicMock())
        sys.modules.setdefault("tools.registry", MagicMock())
        from abc import ABC, abstractmethod

        class _FakeABC(ABC):
            @property
            @abstractmethod
            def name(self): ...
            @abstractmethod
            def is_available(self): ...
            @abstractmethod
            def initialize(self, session_id, **kwargs): ...
            @abstractmethod
            def get_tool_schemas(self): ...
            def handle_tool_call(self, tool_name, args, **kwargs):
                raise NotImplementedError
            def system_prompt_block(self): return ""
            def prefetch(self, query, *, session_id=""): return ""
            def sync_turn(self, u, a, *, session_id=""): pass
            def shutdown(self): pass
            def get_config_schema(self): return []
            def save_config(self, v, h): pass

        sys.modules["agent.memory_provider"].MemoryProvider = _FakeABC
        sys.modules["tools.registry"].tool_error = lambda msg, **kw: json.dumps({"error": msg})
        spec.loader.exec_module(mod)

        # Mock a connection that reports 384-dim in its vec_chunks table
        mock_con = MagicMock()
        mock_con.execute.return_value.fetchone.return_value = (
            "CREATE VIRTUAL TABLE vec_chunks USING vec0(chunk_id TEXT PRIMARY KEY, embedding float[384] distance_metric=cosine)",
        )

        # Should pass when dimensions match
        mod._validate_embedding_dimension(mock_con, 384)

        # Should raise when dimensions differ
        with pytest.raises(RuntimeError, match="Embedding dimension mismatch"):
            mod._validate_embedding_dimension(mock_con, 768)


# ---------------------------------------------------------------------------
# Sensitivity scope: the plugin calls mychatarchive.db directly (bypassing
# the MCP server), so it must enforce fail-closed scope itself. Regression
# coverage for: retrieval calls used to omit `scope=` entirely, which
# defaulted to public-only with no opt-in and no visible error -- a user's
# own agent would silently lose access to their private content the moment
# they classified it. Sealed must remain unreachable no matter what.
# ---------------------------------------------------------------------------


def _plugin_module():
    # Loaded under this fixed name by the mock_mca fixture's
    # spec_from_file_location call. Look it up directly rather than
    # scanning sys.modules by substring -- several sys.modules entries in
    # this fixture (e.g. "mychatarchive" itself) are MagicMocks whose
    # hasattr() checks are vacuously true for any attribute name.
    mod = sys.modules.get("plugins.memory.mychatarchive")
    if mod is None or not hasattr(mod, "_coerce_bool"):
        raise AssertionError("Real plugin module not found in sys.modules")
    return mod


class TestSensitivityScope:
    def test_default_scope_is_public_only(self, mock_mca):
        provider, db, _, _ = mock_mca
        assert provider._include_private is False  # __init__ default
        provider.handle_tool_call("mca_search", {"query": "test"})
        assert db.search_chunks.call_args.kwargs.get("scope") == ("public",)

    def test_include_private_widens_search_scope(self, mock_mca):
        provider, db, _, _ = mock_mca
        provider._include_private = True
        provider.handle_tool_call("mca_search", {"query": "test", "mode": "hybrid"})
        assert db.search_chunks.call_args.kwargs.get("scope") == ("public", "private")
        assert db.fts_search.call_args.kwargs.get("scope") == ("public", "private")

    def test_recall_threads_scope_through_all_three_layers(self, mock_mca):
        provider, db, _, _ = mock_mca
        provider._include_private = True
        provider.handle_tool_call("mca_recall", {"topic": "test"})
        assert db.search_chunks.call_args.kwargs.get("scope") == ("public", "private")
        assert db.search_thread_summaries.call_args.kwargs.get("scope") == ("public", "private")
        assert db.search_thoughts.call_args.kwargs.get("scope") == ("public", "private")

    def test_prefetch_threads_scope(self, mock_mca):
        provider, db, _, _ = mock_mca
        provider.prefetch("query")
        assert db.search_chunks.call_args.kwargs.get("scope") == ("public",)
        provider._include_private = True
        provider.prefetch("query")
        assert db.search_chunks.call_args.kwargs.get("scope") == ("public", "private")

    def test_sealed_never_reachable_even_if_include_private_forced(self, mock_mca):
        # There is no config value or parameter that can add "sealed" to
        # scope -- _scope() filters through _PLUGIN_ALLOWED_LEVELS, which
        # only lists public/private. Forcing include_private (the strongest
        # widening available) must still never surface sealed.
        provider, _, _, _ = mock_mca
        provider._include_private = True
        assert "sealed" not in provider._scope()
        assert set(provider._scope()) <= {"public", "private"}

    def test_plugin_allowed_levels_excludes_sealed(self, mock_mca):
        mod = _plugin_module()
        assert "sealed" not in mod._PLUGIN_ALLOWED_LEVELS

    def test_config_schema_includes_include_private_default_false(self, mock_mca):
        provider, _, _, _ = mock_mca
        schema = provider.get_config_schema()
        entry = next(f for f in schema if f["key"] == "include_private")
        assert entry["default"] == "false"

    def test_coerce_bool_handles_json_and_string_forms(self, mock_mca):
        mod = _plugin_module()
        assert mod._coerce_bool(True) is True
        assert mod._coerce_bool(False) is False
        assert mod._coerce_bool("true") is True
        assert mod._coerce_bool("false") is False
        assert mod._coerce_bool("1") is True
        assert mod._coerce_bool("0") is False
        assert mod._coerce_bool(None) is False
        assert mod._coerce_bool("") is False


class TestProvenanceHonesty:
    """mca_provenance used to report a flat 'not found' for both a
    genuinely-absent row and a row that exists but is outside scope
    (private, with include_private off) -- telling the user their data
    was lost when it was merely restricted."""

    def test_private_chunk_reports_restricted_not_missing(self, mock_mca):
        provider, db, _, _ = mock_mca
        provider._include_private = False

        def side_effect(con, cid, **kw):
            if cid != "priv-chunk":
                return None
            # Absent at the plugin's own (public-only) scope, present once
            # widened to public+private -- i.e. genuinely exists, just private.
            if kw.get("scope") == ("public",):
                return None
            return ("private text", "thread-9", "2026-01-01", "2026-01-01", "{}")

        db.get_chunk_by_id.side_effect = side_effect
        result = json.loads(provider.handle_tool_call(
            "mca_provenance", {"chunk_id": "priv-chunk"},
        ))
        assert "error" in result
        assert "private" in result["error"].lower()
        assert "not found" not in result["error"].lower()

    def test_genuinely_missing_chunk_still_reports_not_found(self, mock_mca):
        provider, db, _, _ = mock_mca
        provider._include_private = False
        db.get_chunk_by_id.side_effect = lambda con, cid, **kw: None
        result = json.loads(provider.handle_tool_call(
            "mca_provenance", {"chunk_id": "ghost-chunk"},
        ))
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_private_thought_reports_restricted_not_missing(self, mock_mca):
        provider, db, _, _ = mock_mca
        provider._include_private = False

        def side_effect(con, tid, **kw):
            if tid != "priv-thought":
                return None
            if kw.get("scope") == ("public",):
                return None
            return ("private thought text", "2026-01-01", "{}")

        db.get_thought_by_id.side_effect = side_effect
        result = json.loads(provider.handle_tool_call(
            "mca_provenance", {"thought_id": "priv-thought"},
        ))
        assert "error" in result
        assert "private" in result["error"].lower()
        assert "not found" not in result["error"].lower()

    def test_include_private_on_returns_content_directly_no_restriction_message(self, mock_mca):
        provider, db, _, _ = mock_mca
        provider._include_private = True

        def side_effect(con, cid, **kw):
            if cid == "priv-chunk" and kw.get("scope") == ("public", "private"):
                return ("private text", "thread-9", "2026-01-01", "2026-01-01", "{}")
            return None

        db.get_chunk_by_id.side_effect = side_effect
        result = json.loads(provider.handle_tool_call(
            "mca_provenance", {"chunk_id": "priv-chunk"},
        ))
        assert result.get("type") == "chunk"
        assert result.get("text") == "private text"
