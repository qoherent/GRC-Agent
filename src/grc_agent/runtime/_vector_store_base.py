"""Shared base for the two sqlite-vec backed stores.

``VectorDocsStore`` and ``VectorCatalogStore`` share these responsibilities:
  * open a sqlite connection with sqlite-vec loaded;
  * create the canonical tables (chunks + vec0 index + FTS5 if present);
  * ingest a list of records idempotently (skip when ``chunks`` is non-empty);
  * run vector KNN and fetch the chunk payload for each hit.

The base class parameterises the table names + read-back shape via small
hooks (``_table_chunks``, ``_table_idx``, ``_table_fts``, ``_read_chunk``,
``_vector_columns``, ``_chunk_columns``). Subclasses retain their public API.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import sqlite_vec

logger = logging.getLogger(__name__)


class VectorStoreBase:
    """Thin skeleton for sqlite-vec backed KNN stores.

    Subclasses MUST set ``self.db_path``, ``self.server_url``,
    ``self.embedding_model`` and ``self.api_key`` in ``__init__``, override
    ``_table_chunks``, ``_table_idx``, and ``init_db``, and (optionally)
    override ``_table_fts`` when an FTS5 table is in use.
    """

    db_path: Path
    server_url: str
    embedding_model: str
    api_key: str

    # --- hooks subclasses override ----------------------------------------

    def _table_chunks(self) -> str:
        raise NotImplementedError

    def _table_idx(self) -> str:
        raise NotImplementedError

    def init_db(self, conn: sqlite3.Connection, dim: int) -> None:
        """Create the chunks/idx/fts tables sized for ``dim``. Subclasses override."""
        raise NotImplementedError

    # --- shared behavior ---------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        return conn

    def _is_populated(self, conn: sqlite3.Connection) -> bool:
        try:
            n = conn.execute(
                f"SELECT count(*) FROM {self._table_chunks()}"
            ).fetchone()[0]
            return n > 0
        except sqlite3.OperationalError:
            return False

    # --- model stamp: rebuild when the embedding model changes -------------

    def _embed_meta_table(self) -> str:
        return "embed_meta"

    def _read_embed_meta(self, conn: sqlite3.Connection) -> tuple[str, int] | None:
        """Return ``(embedding_model, dim)`` stamped on this DB, or ``None``."""
        try:
            row = conn.execute(
                f"SELECT model, dim FROM {self._embed_meta_table()} LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        if not row:
            return None
        return (str(row[0]), int(row[1]))

    def _write_embed_meta(self, conn: sqlite3.Connection, model: str, dim: int) -> None:
        conn.execute(f"DROP TABLE IF EXISTS {self._embed_meta_table()}")
        conn.execute(
            f"CREATE TABLE {self._embed_meta_table()} (model TEXT NOT NULL, dim INTEGER NOT NULL)"
        )
        conn.execute(
            f"INSERT INTO {self._embed_meta_table()} (model, dim) VALUES (?, ?)",
            (model, dim),
        )

    def _drop_index_tables(self, conn: sqlite3.Connection) -> None:
        """Drop the chunks/idx/fts tables (and meta) so a rebuild starts clean."""
        for table in (
            self._table_idx(),
            self._table_chunks(),
            self._embed_meta_table(),
        ):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        fts = getattr(self, "_table_fts", None)
        if callable(fts):
            conn.execute(f"DROP TABLE IF EXISTS {fts()}")
