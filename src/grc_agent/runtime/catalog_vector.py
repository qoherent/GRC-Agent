"""Catalog vector-search pipeline (sqlite-vec + embeddinggemma).

Parallel to :mod:`grc_agent.runtime.doc_answer` for GNU Radio catalog blocks.
The same uniform rules apply:
  * ``embeddinggemma:latest`` (gemma-3-embedding, 300M) produces the vectors.
  * Google gemma-3-embedding spec: prefix every query and every document
    with the same task descriptor. We use the same prefix as docs.
  * ``sqlite-vec`` provides L2 KNN over packed float32 vectors in a
    ``vec0`` virtual table.

Param filtering for the embed text is delegated to the single authority
:mod:`grc_agent.runtime.param_filter` (Stage A details: drop ``hide='all'``,
Advanced, Config). GRC itself marks GUI-only parameters with ``hide='all'``
(color grids, alpha slots, per-channel device knobs); reading that attribute
is the native answer to "which params should the embedding model see".
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import struct
from pathlib import Path
from typing import Any

import sqlite_vec
from grc_agent.runtime.doc_answer import get_embedding
from grc_agent.runtime.param_filter import visible_param_keys

logger = logging.getLogger(__name__)


DB_DIR = Path(os.environ.get("GRC_AGENT_VECTORS_DIR", ".grc_agent/vectors"))
CATALOG_DB_PATH = DB_DIR / "catalog_v1.db"


# --- gemma-3 task prefixes (uniform across query and document) --------------
_QUERY_PREFIX = "task: search result | query: "
_DOCUMENT_PREFIX = "task: search result | document: "

# --- Embed-text body cap (mirrors doc_answer.CHUNK_MAX_WORDS) ---------------
_CATALOG_EMBED_MAX_WORDS = 256
_EMBED_DIM = 768  # embeddinggemma float32

# --- Hybrid retrieval (consultant-approved: FTS5-porter + vector, weighted RRF)
RRF_K = 60  # standard Reciprocal Rank Fusion constant
FUSION_POOL = 30  # candidates pulled from each backend before fusion
VEC_WEIGHT = 2.0  # vector rank weighted 2x lexical — lexical is a boost-only signal
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def fuse_ranks(
    vec_ranks: list[Any],
    lex_ranks: list[Any],
    *,
    w_vec: float = VEC_WEIGHT,
    k: int = RRF_K,
) -> list[Any]:
    """Weighted Reciprocal Rank Fusion of two ranked id lists.

    Vector rank is weighted ``w_vec`` (default 2x) so lexical matches act as a
    boost-only signal: a lexical-only hit cannot demote a strong vector match,
    but a block that BOTH backends like is promoted. Ties break by id for
    deterministic ordering.
    """
    scores: dict[Any, float] = {}
    for pos, rid in enumerate(vec_ranks):
        scores[rid] = scores.get(rid, 0.0) + w_vec / (k + pos + 1)
    for pos, rid in enumerate(lex_ranks):
        scores[rid] = scores.get(rid, 0.0) + 1.0 / (k + pos + 1)
    return [rid for rid, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]


def fts_match(conn: sqlite3.Connection, query: str, limit: int) -> list[int]:
    """Porter-stemmed lexical match over ``catalog_fts``.

    Query terms are OR-joined (recall-friendly) and stemmed by the FTS5 porter
    tokenizer, closing morphology gaps like ``multiplier`` <-> ``multiply``.
    Returns ``[]`` if the FTS5 table is absent (graceful degradation to vector).
    """
    terms = _TOKEN_RE.findall((query or "").lower())
    if not terms:
        return []
    fts_query = " OR ".join(terms)
    try:
        rows = conn.execute(
            "SELECT rowid FROM catalog_fts WHERE catalog_fts MATCH ? "
            "ORDER BY bm25(catalog_fts) LIMIT ?",
            (fts_query, limit),
        ).fetchall()
        return [r[0] for r in rows]
    except sqlite3.OperationalError:
        return []


def _cap_embed_body(parts: list[str], max_words: int) -> str:
    """Join ``parts`` with newlines, capping total words at ``max_words``.

    If the body would exceed ``max_words``, the body is truncated to
    ``max_words`` words and a visible flag is appended:
    ``[TRUNCATED by catalog embed: was N words, kept M]``. This mirrors
    the docs pipeline's ``CHUNK_MAX_WORDS = 256`` cap and the
    ``[TRUNCATED ...]`` convention from ``text_utils``.
    """
    body = "\n".join(parts)
    words = body.split()
    if len(words) <= max_words:
        return body
    original = len(words)
    truncated: list[str] = []
    word_count = 0
    for part in parts:
        part_words = part.split()
        if not part_words:
            continue
        if word_count + len(part_words) > max_words:
            remaining = max_words - word_count
            if remaining > 0:
                truncated.append(" ".join(part_words[:remaining]))
            break
        truncated.append(part)
        word_count += len(part_words)
    return (
        "\n".join(truncated)
        + f"\n[TRUNCATED by catalog embed: was {original} words, kept {max_words}]"
    )


def compose_block_embed_text(
    *,
    block_id: str,
    label: str,
    categories: tuple[str, ...] | list[str],
    parameters: tuple[str, ...] | list[str],
    ports: tuple[str, ...] | list[str],
    documentation: str,
) -> str:
    """Compose one uniform embed text per block.

    The format is fixed and applied to every block. It mirrors the docs
    pipeline's ``_compose_chunk_text`` in spirit (title + heading + body)
    so the embedding model sees a consistent shape.

    Param filtering is the caller's responsibility
    (see :func:`grc_agent.runtime.param_filter.visible_param_keys`); this
    function trusts the ``parameters`` argument as the already-filtered list. The resulting
    body is capped at 256 words; if the cap fires, a visible
    ``[TRUNCATED ...]`` flag is appended.
    """
    parts: list[str] = []
    if label:
        parts.append(f"label: {label}")
    if block_id:
        parts.append(f"block_id: {block_id}")
    if categories:
        parts.append("category: " + "/".join(categories))
    if parameters:
        parts.extend(f"param: {p}" for p in parameters)
    if ports:
        parts.extend(f"port: {p}" for p in ports)
    if documentation:
        parts.append(documentation.strip())
    body = _cap_embed_body(parts, _CATALOG_EMBED_MAX_WORDS)
    return _DOCUMENT_PREFIX + body


def embed_block_text(
    server_url: str,
    body: str,
    *,
    model: str = "embeddinggemma:latest",
) -> list[float]:
    """Embed a block text with the uniform document prefix.

    The prefix is applied here (and not re-applied if the body already
    carries it), so ``compose_block_embed_text`` can include the prefix
    in its returned string for direct display while ``embed_block_text``
    can be called with raw text and still get the same final embedding.
    """
    if not body.startswith(_DOCUMENT_PREFIX):
        body = _DOCUMENT_PREFIX + body
    return get_embedding(server_url, body, model=model)


def embed_query(
    server_url: str, query: str, *, model: str = "embeddinggemma:latest"
) -> list[float]:
    """Embed a search query with the uniform query prefix."""
    return get_embedding(server_url, _QUERY_PREFIX + query, model=model)


class VectorCatalogStore:
    """sqlite-vec backed KNN store for GNU Radio catalog blocks."""

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
            "CREATE TABLE IF NOT EXISTS catalog_chunks ("
            "rowid INTEGER PRIMARY KEY, "
            "block_id TEXT, "
            "payload TEXT)"
        )
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS catalog_idx USING vec0("
            f"embedding float[{_EMBED_DIM}])"
        )
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS catalog_fts "
            "USING fts5(content, tokenize='porter')"
        )

    def _ensure_fts5(self, conn: sqlite3.Connection) -> None:
        """Ensure the FTS5 porter table exists and is populated.

        Fresh DBs build it during :meth:`ingest_if_needed`; this self-heals
        older vec-only DBs from the already-stored chunk payloads (no
        re-embedding). No-op once the table holds rows.
        """
        try:
            n = conn.execute("SELECT count(*) FROM catalog_fts").fetchone()[0]
        except sqlite3.OperationalError:
            conn.execute(
                "CREATE VIRTUAL TABLE catalog_fts USING fts5(content, tokenize='porter')"
            )
            n = 0
        if n > 0:
            return
        for rid, payload in conn.execute(
            "SELECT rowid, payload FROM catalog_chunks"
        ).fetchall():
            conn.execute(
                "INSERT INTO catalog_fts(rowid, content) VALUES (?, ?)",
                (rid, payload or ""),
            )
        conn.commit()

    def ingest_if_needed(
        self,
        *,
        blocks: list[dict[str, Any]],
        server_url: str | None = None,
    ) -> None:
        """Build the catalog vector index from a list of block dicts.

        Each block dict must have keys: ``block_id``, ``label``,
        ``categories`` (iterable of strings, may be nested — flattened),
        ``parameters`` (iterable of strings), ``ports`` (iterable of strings),
        ``documentation`` (str). Extra keys are ignored.
        """
        server_url = server_url or self.server_url
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_connection()
        try:
            try:
                count = conn.execute("SELECT count(*) FROM catalog_chunks").fetchone()[0]
                if count > 0:
                    return
            except sqlite3.OperationalError:
                pass

            self.init_db(conn)
            inserted = 0
            for block in blocks:
                block_id = str(block.get("block_id", "")).strip()
                if not block_id:
                    continue
                raw_params = tuple(str(p) for p in (block.get("parameters") or ()))
                param_values = block.get("param_values") or {}
                visible_params = tuple(visible_param_keys(block_id, raw_params, param_values))
                body = compose_block_embed_text(
                    block_id=block_id,
                    label=str(block.get("label", "") or ""),
                    categories=_flatten_categories(block.get("categories") or ()),
                    parameters=visible_params,
                    ports=tuple(str(p) for p in (block.get("ports") or ())),
                    documentation=str(block.get("documentation", "") or ""),
                )
                try:
                    embedding = embed_block_text(server_url, body)
                except Exception as exc:
                    logger.error("Failed to embed catalog block %s: %s", block_id, exc)
                    continue
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO catalog_chunks(block_id, payload) VALUES(?, ?)",
                    (block_id, body),
                )
                rowid = cursor.lastrowid
                conn.execute(
                    "INSERT INTO catalog_idx(rowid, embedding) VALUES(?, ?)",
                    (rowid, sqlite_vec.serialize_float32(embedding)),
                )
                conn.execute(
                    "INSERT INTO catalog_fts(rowid, content) VALUES(?, ?)",
                    (rowid, body),
                )
                inserted += 1
            conn.commit()
            logger.info("Catalog vector index ingested %d blocks.", inserted)
        finally:
            conn.close()

    def search(
        self, query: str, query_vector: list[float], limit: int
    ) -> list[dict[str, Any]]:
        """Hybrid retrieval: weighted-RRF fusion of vector KNN + FTS5-porter.

        Pulls a ``FUSION_POOL``-wide candidate set from each backend, fuses via
        :func:`fuse_ranks` (vector weighted 2x lexical), and returns the top
        ``limit`` with their vector L2 distance (sentinel for lexical-only
        hits). Degrades gracefully to pure-vector when the FTS5 table is absent.
        """
        conn = self._get_connection()
        try:
            conn.row_factory = sqlite3.Row
            self._ensure_fts5(conn)

            vec_rows = conn.execute(
                "SELECT rowid, distance FROM catalog_idx "
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (sqlite_vec.serialize_float32(query_vector), FUSION_POOL),
            ).fetchall()
            vec_ranks = [r["rowid"] for r in vec_rows]
            vec_dist = {r["rowid"]: r["distance"] for r in vec_rows}
            worst_vec = max(vec_dist.values()) if vec_dist else 0.0

            lex_ranks = fts_match(conn, query, FUSION_POOL)

            fused = fuse_ranks(vec_ranks, lex_ranks, w_vec=VEC_WEIGHT)[:limit]
            matched: list[dict[str, Any]] = []
            for rowid in fused:
                chunk = conn.execute(
                    "SELECT block_id, payload FROM catalog_chunks WHERE rowid = ?",
                    (rowid,),
                ).fetchone()
                if chunk:
                    matched.append(
                        {
                            "rowid": rowid,
                            # Lexical-only hits carry no vector distance; mark
                            # them clearly farther than any real neighbour.
                            "distance": vec_dist.get(rowid, worst_vec + 1.0),
                            "block_id": chunk["block_id"],
                            "payload": chunk["payload"],
                        }
                    )
            return matched
        finally:
            conn.close()


def _flatten_categories(categories: Any) -> tuple[str, ...]:
    """Accept either ``['Core', 'Variables']`` or ``[('Core', 'Variables')]``."""
    flat: list[str] = []
    for item in categories:
        if isinstance(item, (list, tuple)):
            flat.extend(str(x) for x in item if x)
        elif item:
            flat.append(str(item))
    return tuple(flat)


def is_catalog_db_populated(db_path: Path) -> bool:
    if not os.path.exists(db_path):
        return False
    try:
        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT count(*) FROM catalog_chunks").fetchone()[0]
        conn.close()
        return count > 0
    except sqlite3.Error:
        return False


def is_catalog_db_usable(db_path: Path, *, sample_size: int = 16) -> bool:
    """Sole gate: populated AND stored vectors have non-zero variance.

    Mirrors :func:`grc_agent.runtime.doc_answer.is_db_usable` — one uniform
    rule applied to every catalog DB regardless of provenance.
    """
    if not is_catalog_db_populated(db_path):
        return False
    try:
        store = VectorCatalogStore(db_path, "")
        conn = store._get_connection()
    except Exception:
        return False
    try:
        total = conn.execute("SELECT count(*) FROM catalog_chunks").fetchone()[0]
        if total == 0:
            return False
        n = min(sample_size, total)
        rowids = [
            r[0]
            for r in conn.execute(
                f"SELECT rowid FROM catalog_chunks ORDER BY RANDOM() LIMIT {int(n)}"
            ).fetchall()
        ]
        vectors: list[list[float]] = []
        for rid in rowids:
            raw = conn.execute(
                "SELECT embedding FROM catalog_idx WHERE rowid = ?",
                (rid,),
            ).fetchone()
            if not raw or not raw[0]:
                continue
            vectors.append(list(struct.unpack(f"{len(raw[0]) // 4}f", raw[0])))
        # At least one pair must differ in some dimension.
        if len(vectors) < 2:
            return False
        first = vectors[0]
        for other in vectors[1:]:
            if any(abs(a - b) > 1e-6 for a, b in zip(first, other, strict=True)):
                return True
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass
