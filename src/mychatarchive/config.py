"""Configuration and default paths.

Config file (~/.mychatarchive/config.json) is optional. All values have sensible defaults
so the tool works with zero configuration out of the box.

Example config.json:
{
  "storage": {"backend": "sqlite", "path": "~/.mychatarchive/archive.db"},
  "embeddings": {"backend": "local", "model": "sentence-transformers/all-MiniLM-L6-v2"},
  "transport": {"type": "stdio"},
  "drop_folder": "~/.mychatarchive/imports",
  "auto_sources": {"claude_code": true, "cursor": true},
  "sources": {
    "nas": {"path": "//server.local/share/exports", "format": "chatgpt"}
  },
  "summarize": {
    "api_key": "sk-or-...",
    "model": "anthropic/claude-haiku-4-5",
    "base_url": "https://openrouter.ai/api/v1",
    "messages_per_segment": 15
  }
}
"""

import json
import sys
from pathlib import Path

# Defaults
_DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_DEFAULT_EMBEDDING_DIM = 384
_DEFAULT_CHUNK_MAX_CHARS = 2000   # legacy — superseded by chunk_size
# 900 chars ≈ 225 tokens — stays under all-MiniLM-L6-v2's 256-token window so
# the tail of each chunk isn't silently dropped by the tokenizer. (1200 ≈ 320
# tokens overflowed it.) Raise via config for larger-context embedding models.
_DEFAULT_CHUNK_SIZE = 900         # target chars per chunk
_DEFAULT_CHUNK_OVERLAP = 100      # overlap chars between adjacent chunks (~25 tokens)
_DEFAULT_SEARCH_LIMIT = 10
_DEFAULT_STORAGE_BACKEND = "sqlite"
_DEFAULT_EMBEDDER_BACKEND = "local"
_DEFAULT_TRANSPORT = "stdio"

EMBEDDING_MODEL = _DEFAULT_EMBEDDING_MODEL
EMBEDDING_DIM = _DEFAULT_EMBEDDING_DIM
CHUNK_MAX_CHARS = _DEFAULT_CHUNK_MAX_CHARS
DEFAULT_SEARCH_LIMIT = _DEFAULT_SEARCH_LIMIT

APP_NAME = "mychatarchive"


def get_data_dir() -> Path:
    """~/.mychatarchive/ on all platforms."""
    data_dir = Path.home() / f".{APP_NAME}"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_db_path() -> Path:
    cfg = load_config()
    custom = cfg.get("storage", {}).get("path")
    if custom:
        return Path(custom).expanduser()
    return get_data_dir() / "archive.db"


def get_config_path() -> Path:
    return get_data_dir() / "config.json"


def load_config() -> dict:
    path = get_config_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(cfg: dict):
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2))


def get_embedding_model() -> str:
    cfg = load_config()
    return cfg.get("embeddings", {}).get("model", _DEFAULT_EMBEDDING_MODEL)


def get_embedding_dim() -> int:
    """Effective embedding dimension — what vec_chunks (and friends) get sized to.

    An explicit "dimension" in config always wins. Otherwise the sane default
    depends on the embedder backend: 384 is the LOCAL sentence-transformers
    model's dim, not a universal default. The OpenAI backend's models return
    much larger vectors natively (1536 / 3072); defaulting an OpenAI-backend
    archive to 384 built a table too small for what the API actually
    returns, breaking the very first insert. text-embedding-ada-002 in
    particular always returns 1536 and can't be Matryoshka-shortened via the
    "dimensions" request param (see backends/embeddings/openai.py), so this
    matters even after that param is wired up.
    """
    cfg = load_config()
    emb = cfg.get("embeddings", {})
    if "dimension" in emb:
        return int(emb["dimension"])
    if emb.get("backend") == "openai":
        from mychatarchive.backends.embeddings.openai import _DEFAULT_MODEL, _MODEL_DIMS
        return _MODEL_DIMS.get(emb.get("model", _DEFAULT_MODEL), 1536)
    return _DEFAULT_EMBEDDING_DIM


def get_chunk_max_chars() -> int:
    """Legacy getter — prefer get_chunk_size()."""
    cfg = load_config()
    return cfg.get("embeddings", {}).get("chunk_max_chars", _DEFAULT_CHUNK_MAX_CHARS)


def get_chunk_size() -> int:
    """Target characters per chunk for variable chunking (default 900 ≈ 225 tokens)."""
    cfg = load_config()
    emb = cfg.get("embeddings", {})
    # chunk_size takes precedence; fall back to legacy chunk_max_chars if explicitly set
    if "chunk_size" in emb:
        return int(emb["chunk_size"])
    if "chunk_max_chars" in emb:
        return int(emb["chunk_max_chars"])
    return _DEFAULT_CHUNK_SIZE


def get_chunk_overlap() -> int:
    """Overlap characters between adjacent chunks (default 100 ≈ 25 tokens)."""
    cfg = load_config()
    return int(cfg.get("embeddings", {}).get("chunk_overlap", _DEFAULT_CHUNK_OVERLAP))


# --- Drop folder ---

_DEFAULT_DROP_FOLDER = "~/.mychatarchive/imports"


def get_drop_folder() -> Path:
    cfg = load_config()
    folder = cfg.get("drop_folder", _DEFAULT_DROP_FOLDER)
    return Path(folder).expanduser()


def ensure_drop_folder() -> Path:
    folder = get_drop_folder()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def set_drop_folder(path: str):
    cfg = load_config()
    cfg["drop_folder"] = path
    save_config(cfg)


# --- Auto-discovery sources ---

_AUTO_SOURCE_DEFAULTS = {"claude_code": True, "cursor": True}


def get_auto_sources() -> dict[str, bool]:
    cfg = load_config()
    return cfg.get("auto_sources", dict(_AUTO_SOURCE_DEFAULTS))


def set_auto_source(name: str, enabled: bool):
    cfg = load_config()
    auto = cfg.setdefault("auto_sources", dict(_AUTO_SOURCE_DEFAULTS))
    auto[name] = enabled
    save_config(cfg)


# --- Source management ---

def get_sources() -> dict[str, dict]:
    cfg = load_config()
    return cfg.get("sources", {})


def get_source(name: str) -> dict | None:
    return get_sources().get(name)


def add_source(name: str, path: str, format_name: str | None = None, account: str = "main"):
    cfg = load_config()
    sources = cfg.setdefault("sources", {})
    entry: dict = {"path": path, "account": account}
    if format_name:
        entry["format"] = format_name
    sources[name] = entry
    save_config(cfg)


def remove_source(name: str) -> bool:
    cfg = load_config()
    sources = cfg.get("sources", {})
    if name not in sources:
        return False
    del sources[name]
    save_config(cfg)
    return True


def rename_source(old_name: str, new_name: str) -> bool:
    cfg = load_config()
    sources = cfg.get("sources", {})
    if old_name not in sources or new_name in sources:
        return False
    sources[new_name] = sources.pop(old_name)
    save_config(cfg)
    return True
