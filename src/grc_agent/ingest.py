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

catalog_idx/docs_idx are only built for rows that embedded successfully; if
every embed call fails (e.g. the embedding backend is unreachable), the
respective vec0 table is skipped entirely and the DB is left lexical-only —
catalog_fts/docs_fts are always built from the full chunk set regardless of
embedding outcome. See adapter/rag.py's query_catalog()/query_docs() for the
vector-first, lexical-fallback query logic.
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
    get_platform,
    render_catalog_block,
)

_log = logging.getLogger(__name__)


def _open_db(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    return conn


def _write_meta(conn: sqlite3.Connection, model: str, domain: str) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS _db_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT OR REPLACE INTO _db_meta (key, value) VALUES ('embedding_model', ?)", (model,)
    )
    conn.execute(
        "INSERT OR REPLACE INTO _db_meta (key, value) VALUES ('corpus_version', ?)",
        (_corpus_version(domain),),
    )


def ingest_catalog(  # noqa: C901
    db_path: str, model: str, on_progress: Any = None
) -> int:
    platform = get_platform()
    block_ids = sorted(b for b in platform.blocks if not b.startswith("_"))
    total = len(block_ids)

    # fts_rows holds every renderable block regardless of embedding outcome —
    # the lexical (FTS5) index is built from this unconditionally, so it stays
    # usable even when embedding fails for some/all blocks (e.g. the embedding
    # backend is unreachable). vec_rows is the subset that also embedded
    # successfully; the vector index is only built if it's non-empty.
    fts_rows: list[tuple[str, str]] = []
    vec_rows: list[tuple[str, list[float]]] = []
    for i, block_id in enumerate(block_ids):
        # Render + embed; a failure for one block skips it without aborting the
        # whole build. on_progress fires per iteration (including skipped
        # blocks) so the caller's progress bar reflects processed/total, not
        # just successful/total.
        try:
            rendered = render_catalog_block(block_id, distance=0.0)
            if rendered:
                text = _compose_catalog_text(rendered)
                fts_rows.append((block_id, text))
                try:
                    embed_text = _cap_words(text, EMBED_MAX_WORDS, label=f"catalog:{block_id}")
                    embedding = embed_document(embed_text, model)
                    vec_rows.append((block_id, embedding))
                except Exception as exc:
                    _log.warning("catalog embed failed for block_id=%s: %s", block_id, exc)
        except Exception as exc:
            _log.warning("catalog render failed for block_id=%s: %s", block_id, exc)
        if on_progress is not None:
            on_progress(i + 1, total)

    if not fts_rows:
        raise RuntimeError(
            "No catalog blocks could be rendered — check the GNU Radio platform is available."
        )
    if not vec_rows:
        _log.warning(
            "catalog: no blocks could be embedded (embedding backend unreachable?) — "
            "building a lexical-only (FTS5) index; vector search stays unavailable "
            "until the next successful rebuild."
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
    db_path: str, model: str, on_progress: Any = None
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
    for i, (path, heading, body) in enumerate(chunk_list):
        composed = f"path: {path}\nheading: {heading}\n{body}"
        composed_list.append(composed)
        try:
            embed_text = _cap_words(composed, EMBED_MAX_WORDS, label=f"docs:{path}:{heading}")
            embedding = embed_document(embed_text, model)
            vec_rows.append((i, embedding))
        except Exception as exc:
            _log.warning("docs embed failed for path=%s heading=%s: %s", path, heading, exc)
        if on_progress is not None:
            on_progress(i + 1, total)

    if not vec_rows:
        _log.warning(
            "docs: no chunks could be embedded (embedding backend unreachable?) — "
            "building a lexical-only (FTS5) index; vector search stays unavailable "
            "until the next successful rebuild."
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
