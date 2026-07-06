"""Single home for the embedding constants shared by both vector stores.

Both :mod:`grc_agent.runtime.doc_answer` and
:mod:`grc_agent.runtime.catalog_vector` import from here. One uniform rule:
every chunk (doc or catalog block) gets ``_DOCUMENT_PREFIX``; every query
gets ``_QUERY_PREFIX``. Same prefixes, same word cap — no per-store overrides.

The embedding MODEL NAME and VECTOR DIMENSION are not constants anymore:
the model comes from ``.env`` (per backend) via :mod:`grc_agent.config`, and
the dimension is probed from the first embedding at index-build time so a
model swap (e.g. embeddinggemma 768-d → pplx-embed) cannot corrupt the
``vec0`` table. ``_EMBED_MODEL`` below is only a function-signature default
for direct/unit calls; the live pipeline always passes the config model.
"""

from __future__ import annotations

_QUERY_PREFIX = "task: search result | query: "
_DOCUMENT_PREFIX = "task: search result | document: "
_EMBED_MODEL = "embeddinggemma:latest"  # function-signature default only

# Bound by the smaller of the two configured embedding models' real input
# limits: embeddinggemma (Ollama) accepts 2048 tokens; pplx-embed-v1-0.6b
# (OpenRouter) accepts 32K, so Gemma is the binding constraint regardless of
# backend. 900 words * ~1.8 tokens/word (GNU Radio text is denser than plain
# English: snake_case params, colons, parens) + prefix/heading overhead stays
# within ~1720/2048 tokens, a ~16% margin even under that pessimistic ratio.
_EMBED_MAX_WORDS = 900

__all__ = [
    "_QUERY_PREFIX",
    "_DOCUMENT_PREFIX",
    "_EMBED_MODEL",
    "_EMBED_MAX_WORDS",
]
