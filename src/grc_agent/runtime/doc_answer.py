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

Module-level ``DB_PATH`` is the single filesystem coordinate; tests and the
agent warmup capture it at import time (see ``conftest.py``).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import sqlite_vec
from grc_agent.domain_models import ErrorCode

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent, ToolResult

logger = logging.getLogger(__name__)


# --- Filesystem / model coordinates ------------------------------------------

DB_DIR = Path(os.environ.get("GRC_AGENT_VECTORS_DIR", ".grc_agent/vectors"))
DB_PATH = DB_DIR / "docs_v1.db"
DOCS_DIR = Path(__file__).resolve().parents[3] / "docs" / "wiki_gnuradio_org"

_QUERY_PREFIX = "task: search result | query: "
_DOCUMENT_PREFIX = "task: search result | document: "
_EMBED_MODEL = "embeddinggemma:latest"
_EMBED_MAX_WORDS = 256
_MAX_CONTEXT_WORDS = 6000


# --- Embedding ---------------------------------------------------------------


def get_embedding(server_url: str, text: str, *, model: str = _EMBED_MODEL) -> list[float]:
    """Embed one text via the local embeddinggemma server.

    Exposed as the single shared embedder; catalog blocks use the same call.
    """
    response = httpx.post(
        f"{server_url.rstrip('/')}/api/embeddings",
        json={"model": model, "prompt": text},
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    return list(payload["embedding"])


def embed_query(server_url: str, query: str, *, model: str = _EMBED_MODEL) -> list[float]:
    """Embed a search query with the uniform query prefix."""
    return get_embedding(server_url, _QUERY_PREFIX + query, model=model)


# --- Chunking ---------------------------------------------------------------


def _cap_words(text: str, max_words: int) -> str:
    """Cap ``text`` at ``max_words`` whitespace-separated words."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + f" [TRUNCATED: was {len(words)} words]"


def _chunk_markdown(path: Path) -> list[dict[str, str]]:
    """Split one markdown file on top-level ``#`` sections.

    Each section becomes ``{heading, text}`` where ``text`` is the heading
    line plus the body that follows it, word-capped to ``_EMBED_MAX_WORDS``.
    Files with no ``#`` heading collapse into one chunk whose ``heading``
    is the file stem.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    heading = path.stem.replace("_", " ")
    sections: list[tuple[str, list[str]]] = [(heading, [])]
    for line in raw.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            heading = line.lstrip("# ").strip() or path.stem
            sections.append((heading, []))
        else:
            sections[-1][1].append(line)
    return [
        {"heading": h, "text": _cap_words("\n".join([h] + body).strip(), _EMBED_MAX_WORDS)}
        for h, body in sections
        if "\n".join(body).strip()
    ]


def compose_chunk_text(path: Path, heading: str, body: str) -> str:
    """Compose the uniform embed text for one chunk (prefix + path/heading + body)."""
    parts = [f"path: {path.stem}", f"heading: {heading}", body.strip()]
    return _DOCUMENT_PREFIX + _cap_words("\n".join(parts), _EMBED_MAX_WORDS)


# --- Vector store ------------------------------------------------------------

_EMBED_DIM = 768  # embeddinggemma float32


class VectorDocsStore:
    """sqlite-vec backed KNN store over the GNU Radio docs wiki."""

    def __init__(self, db_path: Path, server_url: str):
        self.db_path = db_path
        self.server_url = server_url

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        return conn

    def init_db(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS docs_chunks ("
            "rowid INTEGER PRIMARY KEY, "
            "path TEXT, "
            "heading TEXT, "
            "payload TEXT)"
        )
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS docs_idx USING vec0("
            f"embedding float[{_EMBED_DIM}])"
        )

    def ingest_if_needed(self, corpus_dir: Path | None = None) -> int:
        """Build the docs index from ``corpus_dir`` (default ``DOCS_DIR``).

        Idempotent: returns early when ``docs_chunks`` is already populated.
        Returns the number of newly-inserted chunks.
        """
        corpus_dir = corpus_dir or DOCS_DIR
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_connection()
        try:
            try:
                count = conn.execute("SELECT count(*) FROM docs_chunks").fetchone()[0]
                if count > 0:
                    return 0
            except sqlite3.OperationalError:
                pass

            self.init_db(conn)
            inserted = 0
            for md_path in sorted(corpus_dir.glob("*.md")):
                for chunk in _chunk_markdown(md_path):
                    embed_text = compose_chunk_text(md_path, chunk["heading"], chunk["text"])
                    try:
                        embedding = get_embedding(self.server_url, embed_text)
                    except Exception as exc:
                        logger.warning("Failed to embed %s#%s: %s", md_path.name, chunk["heading"], exc)
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


def is_docs_db_usable(db_path: Path) -> bool:
    """Sole gate: the docs DB exists and has chunks indexed."""
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(str(db_path))
        total = conn.execute("SELECT count(*) FROM docs_chunks").fetchone()[0]
        conn.close()
    except sqlite3.Error:
        return False
    return total > 0


# --- Warmup ------------------------------------------------------------------


def initialize_vector_db_background(db_path: Path, server_url: str) -> None:
    """Ingest the docs corpus if not yet indexed. Thread-safe; tolerates failures."""
    try:
        store = VectorDocsStore(db_path, server_url)
        store.ingest_if_needed()
    except Exception as exc:
        logger.warning("docs vector warmup failed: %s", exc)


# --- Tool wrapper ------------------------------------------------------------


def _generate_grounded_answer(
    agent: GrcAgent, question: str, sources: list[dict[str, Any]]
) -> str:
    """Single LLM call: answer ``question`` from the full source files.

    Uses the same Ollama server + chat model the agent is configured with.
    The model is instructed to answer concisely, truthfully, and ONLY from
    the provided documentation.
    """
    context_parts = [
        f"# Source: {s['path']} — {s.get('heading', '')}\n{s['content']}"
        for s in sources
    ]
    context = "\n\n---\n\n".join(context_parts)
    # Cap total context to fit the model's context window. Use _cap_words
    # (explicitly flagged) — never a raw slice, per AGENTS.md "no silent
    # transformation."
    context = _cap_words(context, _MAX_CONTEXT_WORDS)

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
    response = httpx.post(
        f"{agent._llama_server_url.rstrip('/')}/api/chat",
        json={
            "model": agent._llama_model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "options": {"num_ctx": 32768, "num_predict": 2048},
        },
        timeout=agent._llama_request_timeout_seconds,
    )
    response.raise_for_status()
    return response.json()["message"]["content"].strip()


def ask_grc_docs(
    agent: GrcAgent,
    question: str,
    k: int | None = None,
    focus: str | None = None,
) -> ToolResult:
    """Ground one GNU Radio docs question in the wiki corpus.

    Flow: embed question → sqlite-vec KNN → take the top-k **chunks**
    directly (each ≤256 words, already the most relevant sections) → single
    LLM call produces a concise, grounded answer.

    ``k`` controls the number of chunks (default from
    ``agent._retrieval_cfg.ask_grc_docs_default_k``). Sending chunks instead
    of full files eliminates the old 75% silent-truncation and cuts the
    grounding call's input tokens ~10×.
    """
    if not isinstance(question, str) or not question.strip():
        return agent._tool_result(
            "ask_grc_docs",
            ok=False,
            message="question must be non-empty.",
            error_type=ErrorCode.INVALID_REQUEST,
        )

    num_chunks = (
        k
        if isinstance(k, int) and k > 0
        else agent._retrieval_cfg.ask_grc_docs_default_k
    )

    try:
        store = VectorDocsStore(DB_PATH, agent._llama_server_url)
        query_vec = embed_query(agent._llama_server_url, question.strip())
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

    # Each chunk hit carries the raw chunk text (heading + body, ≤256 words)
    # in its "text" field — no full-file reload from disk.
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
        "sources": [
            {"path": s["path"], "distance": s["distance"]} for s in sources
        ],
    }
    return agent._payload_result("ask_grc_docs", payload, include_active_session=False)
