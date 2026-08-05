"""Tests for the OpenAI embeddings backend's dimension handling.

The 'openai' package is never imported here — the client is faked out via
_get_client, so these tests run without the optional dependency installed
and without any network access.
"""

from __future__ import annotations

import json

import pytest

from mychatarchive.backends.embeddings import openai as openai_backend
from mychatarchive.config import get_embedding_dim


@pytest.fixture(autouse=True)
def config_path(tmp_path, monkeypatch):
    """Point config at a tmp file per test and reset the cached client.

    Tests must write config through this fixture's path, never by calling
    mychatarchive.config.get_config_path() directly — that name was already
    bound at import time in this module and monkeypatching the module
    attribute afterward would not reroute it, which would otherwise touch
    the real ~/.mychatarchive/config.json.
    """
    path = tmp_path / "config.json"
    monkeypatch.setattr("mychatarchive.config.get_config_path", lambda: path)
    monkeypatch.setattr(openai_backend, "_client", None)
    return path


def _write_config(path, embeddings_cfg: dict):
    path.write_text(json.dumps({"embeddings": embeddings_cfg}))


class _FakeItem:
    def __init__(self, index, embedding):
        self.index = index
        self.embedding = embedding


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeEmbeddings:
    def __init__(self):
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        n = len(kwargs["input"])
        dim = kwargs.get("dimensions", 4)  # arbitrary small stand-in when absent
        return _FakeResponse([_FakeItem(i, [0.0] * dim) for i in range(n)])


class _FakeClient:
    def __init__(self):
        self.embeddings = _FakeEmbeddings()


# ── Bug 3, part 1: the dimensions param actually reaches the API call ─────────

def test_embed_texts_passes_dimensions_for_matryoshka_models(config_path, monkeypatch):
    _write_config(config_path, {
        "backend": "openai", "model": "text-embedding-3-small", "dimension": 512,
    })
    fake_client = _FakeClient()
    monkeypatch.setattr(openai_backend, "_get_client", lambda: fake_client)

    openai_backend.embed_texts(["project-x sample text"])

    assert len(fake_client.embeddings.calls) == 1
    assert fake_client.embeddings.calls[0]["dimensions"] == 512


def test_embed_texts_omits_dimensions_for_ada_002(config_path, monkeypatch):
    """ada-002 doesn't accept the 'dimensions' param at all — sending it errors."""
    _write_config(config_path, {
        "backend": "openai", "model": "text-embedding-ada-002", "dimension": 1536,
    })
    fake_client = _FakeClient()
    monkeypatch.setattr(openai_backend, "_get_client", lambda: fake_client)

    openai_backend.embed_texts(["project-x sample text"])

    assert "dimensions" not in fake_client.embeddings.calls[0]


# ── Bug 3, part 2: default table dimension is backend-aware ───────────────────

def test_default_dim_is_backend_aware(config_path):
    _write_config(config_path, {"backend": "openai"})
    assert get_embedding_dim() == 1536  # text-embedding-3-small's native dim

    _write_config(config_path, {"backend": "openai", "model": "text-embedding-3-large"})
    assert get_embedding_dim() == 3072

    _write_config(config_path, {"backend": "openai", "model": "text-embedding-ada-002"})
    assert get_embedding_dim() == 1536

    # Conservative: local backend (and no backend at all) keep the original
    # 384 default — existing local archives see no behavior change.
    _write_config(config_path, {"backend": "local"})
    assert get_embedding_dim() == 384

    config_path.write_text(json.dumps({}))
    assert get_embedding_dim() == 384

    # Explicit config always wins over both the backend default and the
    # model's native size.
    _write_config(config_path, {"backend": "openai", "dimension": 256})
    assert get_embedding_dim() == 256
