"""Vector-DB ingestion: builds the catalog/docs sqlite-vec databases that
grc_agent.adapter's query_catalog()/query_docs() read from. Runs
automatically on first use (see adapter._ensure_db_built) — there is no
separate CLI or warmup step to run by hand.

Schema (must exactly match what query_catalog()/query_docs() read):
    catalog_chunks(rowid, block_id, payload)
    catalog_idx    vec0(embedding)
    docs_chunks(rowid, path, heading, payload)
    docs_idx       vec0(embedding)
"""

import re
import sqlite3
from pathlib import Path
from typing import Any

import sqlite_vec

from grc_agent._paths import docs_dir
from grc_agent.adapter import (
    EMBED_MAX_WORDS,
    _cap_words,
    embed_document,
    get_platform,
    render_catalog_block,
)


def _open_db(db_path: str, dim: int) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    return conn


def ingest_catalog(db_path: str, model: str) -> int:
    platform = get_platform()
    block_ids = sorted(b for b in platform.blocks if not b.startswith("_"))

    rows: list[tuple[str, str, list[float]]] = []
    for block_id in block_ids:
        try:
            rendered = render_catalog_block(block_id, distance=0.0)
        except Exception:
            continue
        if not rendered:
            continue
        text = _compose_catalog_text(rendered)
        try:
            embedding = embed_document(text, model)
        except Exception:
            continue
        rows.append((block_id, text, embedding))

    if not rows:
        raise RuntimeError(
            "No catalog blocks could be embedded — check the embedding backend is reachable."
        )

    dim = len(rows[0][2])
    conn = _open_db(db_path, dim)
    try:
        conn.execute(
            "CREATE TABLE catalog_chunks (rowid INTEGER PRIMARY KEY, block_id TEXT, payload TEXT)"
        )
        conn.execute(f"CREATE VIRTUAL TABLE catalog_idx USING vec0(embedding float[{dim}])")
        for block_id, text, embedding in rows:
            cur = conn.execute(
                "INSERT INTO catalog_chunks(block_id, payload) VALUES(?, ?)", (block_id, text)
            )
            conn.execute(
                "INSERT INTO catalog_idx(rowid, embedding) VALUES(?, ?)",
                (cur.lastrowid, sqlite_vec.serialize_float32(embedding)),
            )
        conn.commit()
    finally:
        conn.close()
    return len(rows)


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
    return _cap_words("\n".join(parts), EMBED_MAX_WORDS)


_HEADING_RE = re.compile(r"^(#{1,2})\s+(.*)$", re.MULTILINE)


def _chunk_markdown(text: str) -> list[tuple[str, str]]:
    """Split on level-1/level-2 headings; cap each chunk's body separately.
    A flatter, simplified version of the original recursive splitter — good
    enough since chunks only need to stay under the embedding word cap, not
    preserve full document structure."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("", _cap_words(text, EMBED_MAX_WORDS))]

    chunks = []
    for i, m in enumerate(matches):
        heading = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            chunks.append((heading, _cap_words(body, EMBED_MAX_WORDS)))
    return chunks or [("", _cap_words(text, EMBED_MAX_WORDS))]


def ingest_docs(db_path: str, model: str) -> int:
    corpus_dir = docs_dir()
    md_files = sorted(corpus_dir.glob("*.md"))
    if not md_files:
        raise RuntimeError(f"No docs corpus found at {corpus_dir}")

    rows: list[tuple[str, str, str, list[float]]] = []
    for md_file in md_files:
        text = md_file.read_text(encoding="utf-8", errors="ignore")
        for heading, body in _chunk_markdown(text):
            composed = f"path: {md_file.stem}\nheading: {heading}\n{body}"
            try:
                embedding = embed_document(composed, model)
            except Exception:
                continue
            rows.append((md_file.stem, heading, composed, embedding))

    if not rows:
        raise RuntimeError(
            "No docs chunks could be embedded — check the embedding backend is reachable."
        )

    dim = len(rows[0][3])
    conn = _open_db(db_path, dim)
    try:
        conn.execute(
            "CREATE TABLE docs_chunks (rowid INTEGER PRIMARY KEY, path TEXT, heading TEXT, payload TEXT)"
        )
        conn.execute(f"CREATE VIRTUAL TABLE docs_idx USING vec0(embedding float[{dim}])")
        for path, heading, payload, embedding in rows:
            cur = conn.execute(
                "INSERT INTO docs_chunks(path, heading, payload) VALUES(?, ?, ?)",
                (path, heading, payload),
            )
            conn.execute(
                "INSERT INTO docs_idx(rowid, embedding) VALUES(?, ?)",
                (cur.lastrowid, sqlite_vec.serialize_float32(embedding)),
            )
        conn.commit()
    finally:
        conn.close()
    return len(rows)
