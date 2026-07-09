"""Grounded GNU Radio docs RAG (sqlite-vec + embeddinggemma).

Minimal pipeline mirroring :mod:`grc_agent.runtime.catalog_vector`. One uniform
shape applied to every docs chunk:

  * Corpus: markdown files under ``docs/wiki_gnuradio_org/``.
  * Chunking: split each file on top-level ``#`` headings. One chunk per
    section (``heading`` + body, word-capped).
  * Embedding: ``embeddinggemma:latest`` (gemma-3-embedding, 768-d float32)
    with the same ``task: search result`` prefix used by the catalog index.
  * Index: sqlite + the ``sqlite-vec`` extension (``vec0`` virtual table
    providing L2 KNN over packed float32 vectors).
  * Answer composition: a single uniform rule — join the top-k excerpts
    in distance order with their source attribution. No query taxonomy,
    no answer-type classification, no catalog cross-reference.

Module-level ``DB_DIR`` is the single filesystem coordinate; tests and the
agent warmup capture it at import time (see ``conftest.py``).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sqlite_vec
from grc_agent.domain_models import ErrorCode
from grc_agent.runtime._embedding_config import (
    _DOCUMENT_PREFIX,
    _EMBED_MAX_WORDS,
    _EMBED_MODEL,
    _QUERY_PREFIX,
)
from grc_agent.runtime._vector_store_base import VectorStoreBase
from grc_agent.runtime.llm_client import _openai_base_url, call_agent_llm
from grc_agent.runtime.llm_client import cap_words as _cap_words

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult

logger = logging.getLogger(__name__)


# --- Filesystem / model coordinates ------------------------------------------

DB_DIR = Path(
    os.environ.get(
        "GRC_AGENT_VECTORS_DIR",
        str(Path(__file__).resolve().parents[1] / "vectors"),
    )
)


def docs_db_path(backend: str) -> Path:
    """Per-backend docs vector DB: ``<DB_DIR>/docs_<backend>.db``.

    Each backend owns its own embedding model and therefore its own index;
    switching backend swaps which pair is active without a rebuild.
    """
    return DB_DIR / f"docs_{backend}.db"


DOCS_DIR = Path(__file__).resolve().parents[3] / "docs" / "wiki_gnuradio_org"


# --- Embedding ---------------------------------------------------------------


def get_embedding(
    server_url: str,
    text: str,
    *,
    model: str = _EMBED_MODEL,
    api_key: str = "not-needed",
    timeout: float = 30.0,
) -> list[float]:
    """Embed one text via the OpenAI-compatible ``/v1/embeddings`` endpoint.

    Single code path for both backends (Approach A): Ollama
    (``api_key`` ignored by the server) and OpenRouter (``api_key``
    required). Mirrors how the chat path uses the ``openai`` SDK against
    ``/v1/chat/completions`` for both backends. Exposed as the single shared
    embedder; catalog blocks use the same call.
    """
    from openai import OpenAI

    client = OpenAI(
        base_url=_openai_base_url(server_url),
        api_key=api_key,
        timeout=timeout,
    )
    response = client.embeddings.create(model=model, input=text)
    return list(response.data[0].embedding)


def embed_query(
    server_url: str,
    query: str,
    *,
    model: str = _EMBED_MODEL,
    api_key: str = "not-needed",
) -> list[float]:
    """Embed a search query with the uniform query prefix."""
    return get_embedding(server_url, _QUERY_PREFIX + query, model=model, api_key=api_key)


# --- Chunking ---------------------------------------------------------------

_HEADING_MARKERS = ("# ", "## ", "### ", "#### ")


def _split_on_marker(lines: list[str], marker: str) -> list[tuple[str | None, list[str]]]:
    """Split ``lines`` into ``(heading, body)`` groups on lines starting with ``marker``.

    A line matches only at this exact heading level — one level deeper
    (``marker`` prefixed with one more ``#``) is not a boundary, so a
    ``## `` pass doesn't also break on ``### `` lines. Content before the
    first match becomes a ``(None, ...)`` preamble group.
    """
    deeper = "#" + marker
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in lines:
        if line.startswith(marker) and not line.startswith(deeper):
            sections.append((line[len(marker) :].strip(), []))
        else:
            sections[-1][1].append(line)
    return sections


def _split_section(
    heading: str, body_lines: list[str], marker_index: int = 1
) -> list[dict[str, str]]:
    """Recursively split an oversized section on progressively deeper headings.

    Splits into ``"Parent > Sub"`` sub-chunks only when this section's body
    would otherwise be truncated (word count exceeds ``_EMBED_MAX_WORDS`` —
    the same measurement ``cap_words`` already uses, so no new arbitrary
    threshold is introduced) and a deeper heading marker exists in the body.
    Falls back to ``_cap_words`` truncation, unchanged, once no deeper
    marker is available, none is found, or a leaf is still oversized after
    the deepest level (``#### ``). Purely word-count- and marker-driven —
    applies identically to every file, no per-file special-casing.
    """
    if not "\n".join(body_lines).strip():
        return []
    text = "\n".join([heading, *body_lines]).strip()
    if len(text.split()) <= _EMBED_MAX_WORDS or marker_index >= len(_HEADING_MARKERS):
        return [{"heading": heading, "text": _cap_words(text, _EMBED_MAX_WORDS)}]

    subsections = _split_on_marker(body_lines, _HEADING_MARKERS[marker_index])
    if len(subsections) <= 1:
        return _split_section(heading, body_lines, marker_index + 1)

    chunks: list[dict[str, str]] = []
    for sub_heading, sub_body in subsections:
        child_heading = heading if sub_heading is None else f"{heading} > {sub_heading}"
        chunks.extend(_split_section(child_heading, sub_body, marker_index + 1))
    return chunks


def _chunk_markdown(path: Path) -> list[dict[str, str]]:
    """Split one markdown file on ``#`` sections, recursing into ``##``/``###``
    when a section would otherwise be truncated.

    Each leaf becomes ``{heading, text}``, where ``heading`` chains as
    ``"Parent > Sub"`` for recursed sub-sections and ``text`` is the
    heading plus body, word-capped to ``_EMBED_MAX_WORDS`` as the final
    safety net. Files with no ``#`` heading collapse into one top-level
    section whose heading is the file stem.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    fallback_heading = path.stem.replace("_", " ")
    top_sections = _split_on_marker(raw.splitlines(), _HEADING_MARKERS[0])
    chunks: list[dict[str, str]] = []
    for heading, body in top_sections:
        chunks.extend(_split_section(heading or fallback_heading, body))
    return chunks


def compose_chunk_text(path: Path, heading: str, body: str) -> str:
    """Compose the uniform embed text for one chunk (prefix + path/heading + body)."""
    parts = [f"path: {path.stem}", f"heading: {heading}", body.strip()]
    return _DOCUMENT_PREFIX + _cap_words("\n".join(parts), _EMBED_MAX_WORDS)


# --- Vector store ------------------------------------------------------------


class VectorDocsStore(VectorStoreBase):
    """sqlite-vec backed KNN store over the GNU Radio docs wiki."""

    def __init__(
        self,
        db_path: Path,
        server_url: str,
        embedding_model: str,
        *,
        api_key: str = "not-needed",
    ):
        self.db_path = db_path
        self.server_url = server_url
        self.embedding_model = embedding_model
        self.api_key = api_key

    def _table_chunks(self) -> str:
        return "docs_chunks"

    def _table_idx(self) -> str:
        return "docs_idx"

    def init_db(self, conn: sqlite3.Connection, dim: int) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS docs_chunks ("
            "rowid INTEGER PRIMARY KEY, "
            "path TEXT, "
            "heading TEXT, "
            "payload TEXT)"
        )
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS docs_idx USING vec0(embedding float[{dim}])"
        )

    def ingest_if_needed(self, corpus_dir: Path | None = None) -> int:
        """Build the docs index from ``corpus_dir`` (default ``DOCS_DIR``).

        Idempotent: returns early when the index already matches the current
        embedding model. Rebuilds (drops + re-ingests) when the stamped model
        no longer matches, so switching embedding models is safe and automatic.
        Returns the number of newly-inserted chunks.
        """
        corpus_dir = corpus_dir or DOCS_DIR
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_connection()
        try:
            meta = self._read_embed_meta(conn)
            if meta is not None and meta[0] == self.embedding_model and self._is_populated(conn):
                return 0

            # Either fresh, or the stamped model differs → probe dim + (re)build.
            probe = get_embedding(
                self.server_url,
                _DOCUMENT_PREFIX + "dimension probe",
                model=self.embedding_model,
                api_key=self.api_key,
            )
            dim = len(probe)

            # Either fresh, or the stamped model/dim differs → (re)build.
            self._drop_index_tables(conn)
            self.init_db(conn, dim)
            inserted = 0
            for md_path in sorted(corpus_dir.glob("*.md")):
                for chunk in _chunk_markdown(md_path):
                    embed_text = compose_chunk_text(md_path, chunk["heading"], chunk["text"])
                    try:
                        embedding = get_embedding(
                            self.server_url,
                            embed_text,
                            model=self.embedding_model,
                            api_key=self.api_key,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to embed %s#%s: %s", md_path.name, chunk["heading"], exc
                        )
                        continue
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT INTO docs_chunks(path, heading, payload) VALUES(?, ?, ?)",
                        (md_path.name, chunk["heading"], chunk["text"]),
                    )
                    rowid = cursor.lastrowid
                    conn.execute(
                        "INSERT INTO docs_idx(rowid, embedding) VALUES(?, ?)",
                        (rowid, sqlite_vec.serialize_float32(embedding)),
                    )
                    inserted += 1
            self._write_embed_meta(conn, self.embedding_model, dim)
            conn.commit()
            logger.info("Docs vector index ingested %d chunks.", inserted)
            return inserted
        finally:
            conn.close()

    def search(self, query_vector: list[float], limit: int) -> list[dict[str, Any]]:
        """Return up to ``limit`` nearest neighbours via sqlite-vec KNN."""
        conn = self._get_connection()
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT rowid, distance FROM docs_idx WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (sqlite_vec.serialize_float32(query_vector), limit),
            )
            hits: list[dict[str, Any]] = []
            for row in cursor.fetchall():
                chunk = conn.execute(
                    "SELECT path, heading, payload FROM docs_chunks WHERE rowid = ?",
                    (row["rowid"],),
                ).fetchone()
                if chunk:
                    hits.append(
                        {
                            "path": chunk["path"],
                            "heading": chunk["heading"],
                            "text": chunk["payload"],
                            "distance": row["distance"],
                        }
                    )
            return hits
        finally:
            conn.close()


# --- Warmup ------------------------------------------------------------------


def initialize_vector_db_background(
    db_path: Path,
    server_url: str,
    embedding_model: str,
    *,
    api_key: str = "not-needed",
) -> None:
    """Ingest the docs corpus if not yet indexed. Thread-safe; tolerates failures."""
    try:
        store = VectorDocsStore(db_path, server_url, embedding_model, api_key=api_key)
        store.ingest_if_needed()
    except Exception as exc:
        logger.warning("docs vector warmup failed: %s", exc)


# --- Tool wrapper ------------------------------------------------------------


def _generate_grounded_answer(agent: GrcAgent, question: str, sources: list[dict[str, Any]]) -> str:
    """Single LLM call: answer ``question`` from the full source files.

    Uses the same Ollama server + chat model the agent is configured with.
    The model is instructed to answer concisely, truthfully, and ONLY from
    the provided documentation.
    """
    context_parts = [
        f"# Source: {s['path']} — {s.get('heading', '')}\n{s['content']}" for s in sources
    ]
    context = "\n\n---\n\n".join(context_parts)

    prompt = (
        "You are answering a GNU Radio question. Use ONLY the documentation "
        "below. Ground every claim in the docs and cite the source file name. "
        "The sources below were retrieved as relevant to this question.\n\n"
        "Answer concisely and directly. If a specific sub-question is not "
        "addressed by the sources, say which part is not covered, but still "
        "answer what IS covered.\n\n"
        "Do not make up information. If NONE of the sources are related to "
        'the question, say exactly: "The provided documentation does not '
        'cover this."\n\n'
        f"Question: {question}\n\n"
        f"Documentation:\n{context}"
    )
    return call_agent_llm(agent, prompt)


def ask_grc_docs(
    agent: GrcAgent,
    question: str,
) -> ToolResult:
    """Ground one GNU Radio docs question in the wiki corpus.

    Flow: embed question → sqlite-vec KNN → take the default chunks
    directly (each ≤``_EMBED_MAX_WORDS`` words, already the most relevant
    sections) → single LLM call produces a concise, grounded answer.
    """
    if not isinstance(question, str) or not question.strip():
        return agent._tool_result(
            "ask_grc_docs",
            ok=False,
            message="question must be non-empty.",
            error_type=ErrorCode.INVALID_REQUEST,
        )

    num_chunks = agent._retrieval_cfg.ask_grc_docs_default_k

    try:
        store = VectorDocsStore(
            docs_db_path(agent._llama_backend),
            agent._llama_server_url,
            agent._embedding_model,
            api_key=agent._embedding_api_key,
        )
        query_vec = embed_query(
            agent._llama_server_url,
            question.strip(),
            model=agent._embedding_model,
            api_key=agent._embedding_api_key,
        )
        # Retrieve the top-k chunks directly — the KNN already ranks by
        # relevance; no file-level dedup or full-file reload needed.
        chunk_hits = store.search(query_vec, num_chunks)
    except Exception as exc:
        return agent._tool_result(
            "ask_grc_docs",
            ok=False,
            message=f"Docs retrieval unavailable: {exc}",
            error_type=ErrorCode.RETRIEVAL_NOT_READY,
        )

    if not chunk_hits:
        return agent._tool_result(
            "ask_grc_docs",
            ok=False,
            message="No matching documentation found.",
            error_type=ErrorCode.RETRIEVAL_NOT_READY,
        )

    # Each chunk hit carries the raw chunk text (heading + body, capped at
    # _EMBED_MAX_WORDS words) in its "text" field — no full-file reload.
    sources: list[dict[str, Any]] = [
        {
            "path": h["path"],
            "heading": h.get("heading", ""),
            "distance": h["distance"],
            "content": h["text"],
        }
        for h in chunk_hits
    ]

    # Single LLM call: question + relevant chunks → concise grounded answer.
    try:
        answer = _generate_grounded_answer(agent, question.strip(), sources)
    except Exception as exc:
        return agent._tool_result(
            "ask_grc_docs",
            ok=False,
            message=f"Answer generation failed: {exc}",
            error_type=ErrorCode.INTERNAL_ERROR,
        )

    payload = {
        "ok": True,
        "question": question.strip(),
        "answer": answer,
        "sources": [{"path": s["path"], "distance": s["distance"]} for s in sources],
    }
    return agent._payload_result("ask_grc_docs", payload)
