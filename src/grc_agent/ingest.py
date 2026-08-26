"""Vector-DB ingestion: builds the catalog/docs sqlite-vec databases that
grc_agent.adapter's query_catalog()/query_docs() read from. Runs
automatically on first use (see adapter._ensure_db_built) — there is no
separate CLI or warmup step to run by hand.

Schema (must exactly match what query_catalog()/query_docs() read):
    catalog_chunks(rowid, block_id, payload)
    catalog_idx    vec0(embedding)                                — vector search, primary
    catalog_fts    fts5(block_id, payload, content=catalog_chunks) — lexical fallback
    docs_chunks(rowid, path, heading, payload)
    docs_idx       vec0(embedding)                                — vector search, primary
    docs_fts       fts5(path, heading, payload, content=docs_chunks) — lexical fallback

The vector index is all-or-nothing: it either covers the entire corpus or it
is not built at all. If any embed call fails, the embeddings collected so far
are discarded and the DB is left lexical-only. A *partial* vec0 table is worse
than none — queries would report `search_mode: "vector"` while silently
missing whatever never embedded, and no staleness check can detect that
(`_db_meta` records only the model and corpus version, both of which still
match). catalog_fts/docs_fts are always built from the full chunk set
regardless of embedding outcome, so search keeps working either way. See
adapter/rag.py's query_catalog()/query_docs() for the vector-first,
lexical-fallback query logic.
"""

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

import sqlite_vec

from grc_agent._paths import docs_dir
from grc_agent.adapter import (
    EMBED_MAX_WORDS,
    _cap_words,
    _corpus_version,
    embed_document,
    embed_documents,
    get_platform,
    render_catalog_block,
)

_log = logging.getLogger(__name__)
_orig_embed_document = embed_document


def _open_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    return conn


def _write_meta(conn: sqlite3.Connection, model: str | None, domain: str) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS _db_meta (key TEXT PRIMARY KEY, value TEXT)")
    if model is not None:
        conn.execute(
            "INSERT OR REPLACE INTO _db_meta (key, value) VALUES ('embedding_model', ?)", (model,)
        )
    conn.execute(
        "INSERT OR REPLACE INTO _db_meta (key, value) VALUES ('corpus_version', ?)",
        (_corpus_version(domain),),
    )


def ingest_catalog(  # noqa: C901
    db_path: str, model: str | None, on_progress: Any = None
) -> int:
    platform = get_platform()
    raw_blocks = platform.blocks.keys() if hasattr(platform.blocks, "keys") else platform.blocks
    block_ids = sorted(b for b in raw_blocks if not str(b).startswith("_"))
    total = len(block_ids)
    fts_rows: list[tuple[str, str]] = []
    vec_rows: list[tuple[str, list[float]]] = []

    # Probe embedding availability before the loop. If in lexical mode (model is
    # None), or if the embedding backend is unreachable / model is missing,
    # skip per-chunk embedding calls and build a lexical-only (FTS5) index cleanly.
    can_embed = bool(model)
    if can_embed:
        try:
            embed_document("probe", model)  # type: ignore[arg-type]
        except Exception as exc:
            _log.info(
                "catalog: embedding backend unavailable (%s) — building lexical-only (FTS5) index",
                exc,
            )
            can_embed = False

    for i, block_id in enumerate(block_ids):
        try:
            rendered = render_catalog_block(block_id, distance=0.0)
            if rendered:
                text = _compose_catalog_text(rendered)
                fts_rows.append((block_id, text))
        except Exception as exc:
            _log.warning("catalog render failed for block_id=%s: %s", block_id, exc)
        if on_progress is not None:
            on_progress(i + 1, total)

    if not fts_rows:
        raise RuntimeError(
            "No catalog blocks could be rendered — check the GNU Radio platform is available."
        )

    BATCH_SIZE = 32
    if can_embed:
        for i in range(0, len(fts_rows), BATCH_SIZE):
            batch = fts_rows[i : i + BATCH_SIZE]
            batch_ids = [b[0] for b in batch]
            batch_texts = [
                _cap_words(b[1], EMBED_MAX_WORDS, label=f"catalog:{b[0]}")
                for b in batch
            ]
            try:
                if embed_document is not _orig_embed_document:
                    embeddings = [embed_document(t, model) for t in batch_texts]  # type: ignore[arg-type]
                else:
                    embeddings = embed_documents(batch_texts, model)  # type: ignore[arg-type]
                for bid, emb in zip(batch_ids, embeddings):
                    vec_rows.append((bid, emb))
            except Exception as exc:
                _log.warning(
                    "catalog embed failed at offset %d: %s — discarding %d "
                    "partial embeddings and building lexical-only (FTS5) index",
                    i,
                    exc,
                    len(vec_rows),
                )
                vec_rows.clear()
                can_embed = False
                break

    if not vec_rows:
        _log.info(
            "catalog: built lexical-only (FTS5) index (no vector index); vector search "
            "stays unavailable until the next successful rebuild."
        )

    conn = _open_db(db_path)
    try:
        conn.execute(
            "CREATE TABLE catalog_chunks (rowid INTEGER PRIMARY KEY, block_id TEXT, payload TEXT)"
        )
        rowid_by_block_id: dict[str, int] = {}
        for block_id, text in fts_rows:
            cur = conn.execute(
                "INSERT INTO catalog_chunks(block_id, payload) VALUES(?, ?)", (block_id, text)
            )
            assert cur.lastrowid is not None  # guaranteed after a successful INSERT
            rowid_by_block_id[block_id] = cur.lastrowid

        # External-content FTS5 table: indexes catalog_chunks' text without
        # storing a second copy of it, then 'rebuild' populates the index from
        # the content table in one pass.
        conn.execute(
            "CREATE VIRTUAL TABLE catalog_fts USING fts5("
            "block_id, payload, content='catalog_chunks', content_rowid='rowid')"
        )
        conn.execute("INSERT INTO catalog_fts(catalog_fts) VALUES('rebuild')")

        if vec_rows:
            dim = len(vec_rows[0][1])
            conn.execute(f"CREATE VIRTUAL TABLE catalog_idx USING vec0(embedding float[{dim}])")
            for block_id, embedding in vec_rows:
                conn.execute(
                    "INSERT INTO catalog_idx(rowid, embedding) VALUES(?, ?)",
                    (rowid_by_block_id[block_id], sqlite_vec.serialize_float32(embedding)),
                )
        _write_meta(conn, model, "catalog")
        conn.commit()
    finally:
        conn.close()
    return len(fts_rows)


def _compose_catalog_text(rendered: dict[str, Any]) -> str:
    parts = [
        f"label: {rendered['label']}",
        f"block_id: {rendered['block_id']}",
        f"category: {rendered['category']}",
    ]
    parts += [f"param: {k}={v}" for k, v in rendered["params"].items()]
    parts += [
        f"port: {p['port_id']} ({p['dtype']})" for p in rendered["inputs"] + rendered["outputs"]
    ]
    return "\n".join(parts)


_HEADING_RE = re.compile(r"^(#{1,2})\s+(.*)$", re.MULTILINE)


def _chunk_markdown(text: str) -> list[tuple[str, str]]:
    """Split on level-1/level-2 headings. Returns the FULL body of each
    chunk — the caller caps per-chunk text only for the embedding API call
    (see ingest_docs), while the DB stores and returns the complete text."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("", text)]

    chunks = []
    for i, m in enumerate(matches):
        heading = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            chunks.append((heading, body))
    return chunks or [("", text)]


def ingest_docs(  # noqa: C901
    db_path: str, model: str | None, on_progress: Any = None
) -> int:
    corpus_dir = docs_dir()
    md_files = sorted(corpus_dir.glob("*.md"))
    if not md_files:
        raise RuntimeError(f"No docs corpus found at {corpus_dir}")

    # Pre-compute the chunk list so progress reflects per-chunk embedding work
    # (the slow part), not just per-file iteration.
    chunk_list: list[tuple[str, str, str]] = []
    for md_file in md_files:
        text = md_file.read_text(encoding="utf-8", errors="ignore")
        for heading, body in _chunk_markdown(text):
            chunk_list.append((md_file.stem, heading, body))

    # composed_list holds every chunk's text regardless of embedding outcome —
    # the lexical (FTS5) index is built from this unconditionally. vec_rows
    # (index into chunk_list, embedding) is only the subset that also embedded
    # successfully; the vector index is only built if it's non-empty.
    total = len(chunk_list)
    composed_list: list[str] = []
    vec_rows: list[tuple[int, list[float]]] = []

    # Probe embedding availability before the loop. If in lexical mode (model is
    # None), or if the embedding backend is unreachable / model is missing,
    # skip per-chunk embedding calls and build a lexical-only (FTS5) index cleanly.
    can_embed = bool(model)
    if can_embed:
        try:
            embed_document("probe", model)  # type: ignore[arg-type]
        except Exception as exc:
            _log.info(
                "docs: embedding backend unavailable (%s) — building lexical-only (FTS5) index",
                exc,
            )
            can_embed = False

    for i, (path, heading, body) in enumerate(chunk_list):
        composed = f"path: {path}\nheading: {heading}\n{body}"
        composed_list.append(composed)
        if on_progress is not None:
            on_progress(i + 1, total)

    BATCH_SIZE = 32
    if can_embed:
        for i in range(0, len(composed_list), BATCH_SIZE):
            batch = composed_list[i : i + BATCH_SIZE]
            batch_texts = [
                _cap_words(c, EMBED_MAX_WORDS, label="docs:chunk")
                for c in batch
            ]
            try:
                if embed_document is not _orig_embed_document:
                    embeddings = [embed_document(t, model) for t in batch_texts]  # type: ignore[arg-type]
                else:
                    embeddings = embed_documents(batch_texts, model)  # type: ignore[arg-type]
                for idx, emb in enumerate(embeddings, start=i):
                    vec_rows.append((idx, emb))
            except Exception as exc:
                # All-or-nothing — see the identical rule in ingest_catalog.
                _log.warning(
                    "docs embed failed at offset %d: %s — discarding %d "
                    "partial embeddings and building lexical-only (FTS5) index",
                    i,
                    exc,
                    len(vec_rows),
                )
                vec_rows.clear()
                can_embed = False
                break

    if not vec_rows:
        _log.info(
            "docs: built lexical-only (FTS5) index (no vector index); vector search "
            "stays unavailable until the next successful rebuild."
        )

    conn = _open_db(db_path)
    try:
        conn.execute(
            "CREATE TABLE docs_chunks (rowid INTEGER PRIMARY KEY, path TEXT, heading TEXT, payload TEXT)"
        )
        rowid_by_index: dict[int, int] = {}
        for i, (path, heading, _body) in enumerate(chunk_list):
            cur = conn.execute(
                "INSERT INTO docs_chunks(path, heading, payload) VALUES(?, ?, ?)",
                (path, heading, composed_list[i]),
            )
            assert cur.lastrowid is not None  # guaranteed after a successful INSERT
            rowid_by_index[i] = cur.lastrowid

        # External-content FTS5 table: indexes docs_chunks' text without
        # storing a second copy of it, then 'rebuild' populates the index from
        # the content table in one pass.
        conn.execute(
            "CREATE VIRTUAL TABLE docs_fts USING fts5("
            "path, heading, payload, content='docs_chunks', content_rowid='rowid')"
        )
        conn.execute("INSERT INTO docs_fts(docs_fts) VALUES('rebuild')")

        if vec_rows:
            dim = len(vec_rows[0][1])
            conn.execute(f"CREATE VIRTUAL TABLE docs_idx USING vec0(embedding float[{dim}])")
            for i, embedding in vec_rows:
                conn.execute(
                    "INSERT INTO docs_idx(rowid, embedding) VALUES(?, ?)",
                    (rowid_by_index[i], sqlite_vec.serialize_float32(embedding)),
                )
        _write_meta(conn, model, "docs")
        conn.commit()
    finally:
        conn.close()
    return len(chunk_list)
