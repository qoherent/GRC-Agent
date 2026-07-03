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
from typing import Any, Callable

import sqlite_vec

logger = logging.getLogger(__name__)


class VectorStoreBase:
    """Thin skeleton for sqlite-vec backed KNN stores.

    Subclasses MUST set ``self.db_path`` and ``self.server_url`` in
    ``__init__``, and override the hook methods (``_table_chunks``,
    ``_table_idx``, ``_vector_columns``, ``_chunk_columns``,
    ``_read_chunk``).
    """

    db_path: Path
    server_url: str

    # --- hooks subclasses override ----------------------------------------

    def _table_chunks(self) -> str:
        raise NotImplementedError

    def _table_idx(self) -> str:
        raise NotImplementedError

    def _vector_columns(self) -> str:
        """``"embedding float[768]"`` etc."""
        raise NotImplementedError

    def _chunk_columns(self) -> str:
        """SQL column definitions for the chunks table (after ``rowid`` PK)."""
        raise NotImplementedError

    def _read_chunk(self, conn: sqlite3.Connection, rowid: int) -> dict[str, Any] | None:
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
