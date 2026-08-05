"""OpenAI embeddings backend.

Uses the OpenAI API to generate embeddings remotely.
Requires the 'openai' package and an API key configured via:
  - config embeddings.openai_api_key
  - or OPENAI_API_KEY environment variable

Example config.json:
{
  "embeddings": {
    "backend": "openai",
    "model": "text-embedding-3-small",
    "openai_api_key": "sk-...",
    "dimension": 1536
  }
}
"""

import os

# Known dimensions for OpenAI embedding models
_MODEL_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}

_DEFAULT_MODEL = "text-embedding-3-small"
_BATCH_SIZE = 2048  # OpenAI's max inputs per request

# Only the newer -3 models accept the "dimensions" request param (Matryoshka
# shortening); text-embedding-ada-002 always returns its fixed 1536 dims and
# errors if "dimensions" is sent at all.
_SUPPORTS_DIMENSIONS_PARAM = {"text-embedding-3-small", "text-embedding-3-large"}

_client = None


def _get_config() -> dict:
    from mychatarchive.config import load_config
    return load_config().get("embeddings", {})


def _get_model() -> str:
    return _get_config().get("model", _DEFAULT_MODEL)


def _get_client():
    global _client
    if _client is not None:
        return _client

    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError(
            "The 'openai' package is required for the OpenAI embeddings backend. "
            "Install it with: pip install 'mychatarchive[openai]'  or  pip install openai"
        )

    cfg = _get_config()
    api_key = cfg.get("openai_api_key") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OpenAI API key not configured. "
            "Set 'openai_api_key' under 'embeddings' in ~/.mychatarchive/config.json "
            "or export OPENAI_API_KEY."
        )

    _client = OpenAI(api_key=api_key)
    return _client


def embed_texts(texts: list[str]) -> list[list[float]]:
    client = _get_client()
    model = _get_model()
    results: list[list[float]] = []

    # Request vectors sized to match what vec_chunks was actually built with
    # (see dimension() below). Without this, the API always returns the
    # model's native size regardless of the configured/table dimension, and
    # the first insert into vec_chunks fails with a sqlite-vec dimension
    # error after the batch has already been paid for.
    kwargs = {}
    if model in _SUPPORTS_DIMENSIONS_PARAM:
        kwargs["dimensions"] = dimension()

    for i in range(0, len(texts), _BATCH_SIZE):
        batch = texts[i : i + _BATCH_SIZE]
        response = client.embeddings.create(input=batch, model=model, **kwargs)
        # API guarantees order matches input, but sort by index to be safe
        ordered = sorted(response.data, key=lambda item: item.index)
        results.extend(item.embedding for item in ordered)

    return results


def embed_single(text: str) -> list[float]:
    return embed_texts([text])[0]


def dimension() -> int:
    cfg = _get_config()
    # Explicit config override takes precedence (e.g. when using dimensions param)
    if "dimension" in cfg:
        return int(cfg["dimension"])
    return _MODEL_DIMS.get(_get_model(), 1536)
