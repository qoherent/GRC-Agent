"""Catalog vector-search pipeline (vec1 + embeddinggemma).

Parallel to :mod:`grc_agent.runtime.doc_answer` for GNU Radio catalog blocks.
The same uniform rules apply:
  * ``embeddinggemma:latest`` (gemma-3-embedding, 300M) produces the vectors.
  * Google gemma-3-embedding spec: prefix every query and every document
    with the same task descriptor. We use the same prefix as docs.
  * ``vec1`` provides cosine-distance nearest-neighbour over a flat packed
    index of 768-d float32 vectors.

Param filtering for the embed text uses the same GRC-native
``evaluated_param_hides`` that :func:`inspect_graph._param_keys_by_block`
uses. GRC itself marks GUI-only parameters with ``hide='all'`` (color
grids, alpha slots, per-channel device knobs); reading that attribute is
the native answer to "which params should the embedding model see".
"""

from __future__ import annotations

import logging
import os
import sqlite3
import struct
from pathlib import Path
from typing import Any

from grc_agent.runtime.block_semantics import evaluated_param_hides
from grc_agent.runtime.doc_answer import get_embedding

logger = logging.getLogger(__name__)


DB_DIR = Path(os.environ.get("GRC_AGENT_VECTORS_DIR", ".grc_agent/vectors"))
CATALOG_DB_PATH = DB_DIR / "catalog_v1.db"


# --- gemma-3 task prefixes (uniform across query and document) --------------
_QUERY_PREFIX = "task: search result | query: "
_DOCUMENT_PREFIX = "task: search result | document: "

# --- Embed-text body cap (mirrors doc_answer.CHUNK_MAX_WORDS) ---------------
_CATALOG_EMBED_MAX_WORDS = 256


def _visible_param_keys(
    block_id: str,
    params: list[str] | tuple[str, ...],
    param_values: dict[str, Any] | None = None,
) -> list[str]:
    """Return param keys that GRC's own ``hide`` evaluation marks as visible.

    Delegates to :func:`evaluated_param_hides` (the same call
    :func:`inspect_graph._param_keys_by_block` uses). Params with
    ``hide='all'`` (color slots, alpha grids, per-channel knobs) are
    excluded. ``hide='part'`` and ``hide='none'`` are kept. If the GRC
    platform is unavailable, the function returns the full list
    unchanged — never silently drops a parameter that might be useful.
    """
    evaluated = evaluated_param_hides(block_id, param_values or {})
    if not evaluated:
        return list(params)
    visible = [key for key in params if evaluated.get(str(key)) != "all"]
    return visible


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
    (see :func:`_visible_param_keys`); this function trusts the
    ``parameters`` argument as the already-filtered list. The resulting
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


def embed_query(server_url: str, query: str, *, model: str = "embeddinggemma:latest") -> list[float]:
    """Embed a search query with the uniform query prefix."""
    return get_embedding(server_url, _QUERY_PREFIX + query, model=model)


class VectorCatalogStore:
    """vec1-backed KNN store for GNU Radio catalog blocks."""

    def __init__(self, db_path: Path, server_url: str):
        self.db_path = db_path
        self.server_url = server_url

    def _get_connection(self) -> sqlite3.Connection:
        resolved: Path | None = None
        for parent in Path(__file__).resolve().parents:
            cand = parent / "vec1.so"
            if cand.exists():
                resolved = cand
                break
        if resolved is None:
            raise RuntimeError(
                "vec1.so not found alongside grc_agent package. "
                "Place vec1.so in src/grc_agent/ (or a parent) and retry."
            )
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        try:
            conn.enable_load_extension(True)
            conn.load_extension(str(resolved))
        except Exception:
            conn.close()
            raise
        return conn

    def init_db(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS catalog_chunks ("
            "rowid INTEGER PRIMARY KEY, "
            "block_id TEXT, "
            "payload TEXT)"
        )
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS catalog_idx USING vec1(embedding)")

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
                visible_params = tuple(
                    _visible_param_keys(block_id, raw_params, param_values)
                )
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
                    (rowid, struct.pack(f"{len(embedding)}f", *embedding)),
                )
                inserted += 1
            conn.execute(
                "INSERT INTO catalog_idx(cmd, arg) "
                "VALUES('rebuild', '{\"index\": \"flat\", \"distance\": \"cos\"}')"
            )
            conn.commit()
            logger.info("Catalog vector index ingested %d blocks.", inserted)
        finally:
            conn.close()

    def search(self, query_vector: list[float], limit: int) -> list[dict[str, Any]]:
        """Return up to ``limit + 1`` nearest neighbours.

        Same "carry overflow to the caller" rule as :meth:`VectorDocsStore.search`:
        we do not slice here.
        """
        conn = self._get_connection()
        try:
            conn.row_factory = sqlite3.Row
            packed_vec = struct.pack(f"{len(query_vector)}f", *query_vector)
            cursor = conn.execute(
                "SELECT rowid, distance FROM catalog_idx(?, ?)",
                (packed_vec, f'{{"K": {limit + 1}}}'),
            )
            matched: list[dict[str, Any]] = []
            for row in cursor.fetchall():
                rowid = row["rowid"]
                distance = row["distance"]
                chunk = conn.execute(
                    "SELECT block_id, payload FROM catalog_chunks WHERE rowid = ?",
                    (rowid,),
                ).fetchone()
                if chunk:
                    matched.append({
                        "rowid": rowid,
                        "distance": distance,
                        "block_id": chunk["block_id"],
                        "payload": chunk["payload"],
                    })
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
        rowids = [r[0] for r in conn.execute(
            f"SELECT rowid FROM catalog_chunks ORDER BY RANDOM() LIMIT {int(n)}"
        ).fetchall()]
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
            if any(abs(a - b) > 1e-6 for a, b in zip(first, other)):
                return True
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass
