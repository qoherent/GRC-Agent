"""Single home for the embedding-model constants shared by both vector stores.

Both :mod:`grc_agent.runtime.doc_answer` and
:mod:`grc_agent.runtime.catalog_vector` import from here. One uniform rule:
every chunk (doc or catalog block) gets ``_DOCUMENT_PREFIX``; every query
gets ``_QUERY_PREFIX``. Same model, same dim, same word cap — no per-store
overrides.
"""

from __future__ import annotations

_QUERY_PREFIX = "task: search result | query: "
_DOCUMENT_PREFIX = "task: search result | document: "
_EMBED_MODEL = "embeddinggemma:latest"
_EMBED_DIM = 768  # embeddinggemma float32
_EMBED_MAX_WORDS = 256
_MAX_CONTEXT_WORDS = 6000

__all__ = [
    "_QUERY_PREFIX",
    "_DOCUMENT_PREFIX",
    "_EMBED_MODEL",
    "_EMBED_DIM",
    "_EMBED_MAX_WORDS",
    "_MAX_CONTEXT_WORDS",
]
