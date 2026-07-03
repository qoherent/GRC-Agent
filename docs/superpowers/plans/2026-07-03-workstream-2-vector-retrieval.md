# Workstream 2 — Vector Retrieval + Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Depends on:** Workstream 1 (the `change_graph` engine is unaffected, but executing both in the same branch lets the maintainer spot unexpected cross-import side effects early).

**Goal:** Add direct unit tests for `ask_grc_docs` (prompt construction, citation handling, error paths) using mocked embeddings; extract a shared `VectorStoreBase` so the two near-duplicate vector stores stop repeating `_get_connection` / `init_db` / `ingest_if_needed` / `search` skeletons; move the shared `_DOCUMENT_PREFIX` / `_EMBED_DIM` constants into a private `_embedding_config.py` that both modules import from (kill the `# noqa: F401` cross-import).

**Architecture:** A new `runtime/_embedding_config.py` exports the embedding-model constants. `VectorStoreBase` exposes `_get_connection()`, `init_db(conn)`, `ingest_if_needed(records, embed_fn)`, and `search(query_vector, limit)` — concrete stores (`VectorDocsStore`, `VectorCatalogStore`) subclass it and override only the table names + WHERE clauses. `ask_grc_docs` keeps its public function signature verbatim; new tests pin its prompt + payload contract.

**Tech Stack:** Python 3.12, sqlite3, sqlite-vec, httpx (mocked), embeddinggemma (mocked), pytest.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `src/grc_agent/runtime/_embedding_config.py` | Single home for `_QUERY_PREFIX`, `_DOCUMENT_PREFIX`, `_EMBED_MODEL`, `_EMBED_DIM`, `_EMBED_MAX_WORDS` | Create |
| `src/grc_agent/runtime/_vector_store_base.py` | `VectorStoreBase` with shared conn + init_db + ingest + search skeletons | Create |
| `src/grc_agent/runtime/doc_answer.py` | Re-export `_DOCUMENT_PREFIX` (now from `_embedding_config`), subclass `VectorStoreBase`, keep `ask_grc_docs` public shape verbatim | Modify |
| `src/grc_agent/runtime/catalog_vector.py` | Re-export embedding constants (now from `_embedding_config`), subclass `VectorStoreBase`, keep `search()` calling `fuse_ranks` + `fts_match` | Modify |
| `src/grc_agent/runtime/search_blocks.py` | No change (uses `VectorCatalogStore.search`) | Read-only |
| `tests/test_doc_answer_unit.py` | Direct tests for `ask_grc_docs` (prompt, citations, error paths) — no Ollama | Create |
| `tests/test_vector_store_base.py` | Tests that prove `VectorStoreBase` is the real shared skeleton (init_db row counts, ingest idempotency, search-by-rowid) | Create |

Wire behavior (`ask_grc_docs` payload, `search_blocks` payload, fuse_ranks order) UNCHANGED.

---

## Task 1: Move embedding constants to `_embedding_config.py` (TDD-friendly cut)

**Files:**
- Create: `src/grc_agent/runtime/_embedding_config.py`
- Modify: `src/grc_agent/runtime/doc_answer.py`
- Modify: `src/grc_agent/runtime/catalog_vector.py`

Both modules currently read constants from `doc_answer` (the older module) under `# noqa: F401`. Inverting the dependency: constants live in their own module; both depend on it.

- [ ] **Step 1: Write a failing regression that catches the cross-import**

Append to `tests/test_doc_answer_unit.py` (create with the empty content for now — the full file is built in Task 2):

> Skip if `tests/test_doc_answer_unit.py` doesn't exist yet — it will be created in Task 2 Step 1. The cross-import test is the FIRST test inside that file.

```python
def test_embedding_constants_live_in_dedicated_module():
    """The embedding constants are the single source of truth; both the
    docs store and the catalog store must import them from one place."""
    import grc_agent.runtime._embedding_config as cfg
    from grc_agent.runtime.doc_answer import _DOCUMENT_PREFIX, _QUERY_PREFIX
    from grc_agent.runtime.catalog_vector import (
        _DOCUMENT_PREFIX as _CDP,
        _QUERY_PREFIX as _CQP,
    )
    assert _DOCUMENT_PREFIX == cfg._DOCUMENT_PREFIX == _CDP
    assert _QUERY_PREFIX == cfg._QUERY_PREFIX == _CQP
```

- [ ] **Step 2: Run the test (or the placeholder doc_answer unit suite — Task 2)**

Run: `uv run pytest tests/test_doc_answer_unit.py -v`
Expected: FAIL — `_embedding_config` does not exist yet (or, if the file is empty, the test is marked below as the first one to add).

- [ ] **Step 3: Create `_embedding_config.py`**

Create `src/grc_agent/runtime/_embedding_config.py`:
```python
"""Single home for the embedding-model constants shared by both vector stores.

Both :mod:`grc_agent.runtime.doc_answer` and
:mod:`grc_agent.runtime.catalog_vector` import from here. One uniform rule:
every chunk (doc or catalog block) gets ``_DOCUMENT_PREFIX``; every query
gets ``_QUERY_PREFIX``. Same model, same dim, same word cap — no per-store
overrides.
"""

from __future__ import annotations

_QUERY_PREFIX = "task: search result | query: "
_DOCUMENT_PREFIX = "task: search result | document: "
_EMBED_MODEL = "embeddinggemma:latest"
_EMBED_DIM = 768  # embeddinggemma float32
_EMBED_MAX_WORDS = 256
_MAX_CONTEXT_WORDS = 6000

__all__ = [
    "_QUERY_PREFIX",
    "_DOCUMENT_PREFIX",
    "_EMBED_MODEL",
    "_EMBED_DIM",
    "_EMBED_MAX_WORDS",
    "_MAX_CONTEXT_WORDS",
]
```

- [ ] **Step 4: Update `doc_answer.py` to import from the new module**

Replace `src/grc_agent/runtime/doc_answer.py:53-57` with:
```python
from grc_agent.runtime._embedding_config import (
    _DOCUMENT_PREFIX,
    _EMBED_MAX_WORDS,
    _EMBED_MODEL,
    _MAX_CONTEXT_WORDS,
    _QUERY_PREFIX,
)
_EMBED_DIM = 768  # retained for back-compat with downstream readers; see _embedding_config
```

(`_EMBED_DIM` stays defined as a module-level name so existing imports don't break, but its value comes from one source.)

- [ ] **Step 5: Update `catalog_vector.py` to import from the new module**

Replace `src/grc_agent/runtime/catalog_vector.py:46-52` with:
```python
from grc_agent.runtime._embedding_config import (
    _DOCUMENT_PREFIX,
    _EMBED_DIM,
    _EMBED_MAX_WORDS,
    _EMBED_MODEL,
    _QUERY_PREFIX,
)
```

- [ ] **Step 6: Run the cross-import test**

Run: `uv run pytest tests/test_doc_answer_unit.py::test_embedding_constants_live_in_dedicated_module -v`
Expected: PASS — the constant is now defined in exactly one place.

- [ ] **Step 7: Re-run existing tests**

Run:
```bash
uv run pytest tests/test_catalog_vector.py tests/test_catalog_vector_unit.py -v
```
Expected: PASS — `_DOCUMENT_PREFIX`, `_EMBED_DIM` etc. still surface with the same values.

- [ ] **Step 8: Commit**

```bash
git add src/grc_agent/runtime/_embedding_config.py src/grc_agent/runtime/doc_answer.py src/grc_agent/runtime/catalog_vector.py tests/test_doc_answer_unit.py
git commit -m "refactor(vector): move embedding constants to _embedding_config.py"
```

---

## Task 2: Direct tests for `ask_grc_docs` (TDD — the ZERO-coverage tool)

**Files:**
- Create: `tests/test_doc_answer_unit.py`

The `ask_grc_docs` function is the model-facing tool and currently has no direct unit tests. This task adds them with mocked embeddings + mocked LLM so the prompt construction, citation shape, and error paths are pinned.

- [ ] **Step 1: Write failing tests for prompt construction and citation shape**

Replace the file content from Task 1 with the full suite:
```python
"""Direct unit tests for ``ask_grc_docs`` — the model-facing docs-RAG tool.

No Ollama, no live embeddings. The httpx post (embedding) and the
``call_agent_llm`` (answer generation) are mocked so we can pin:
  * the prompt structure (path + heading + body, word-capped)
  * the citation shape (path + distance per source)
  * every error path (empty question, retrieval failure, no hits, LLM error)
"""

from __future__ import annotations

from unittest import mock

import pytest


class FakeAgent:
    """Bare-minimum GrcAgent stub for ``ask_grc_docs`` tests."""

    def __init__(self, *, retrieval_cfg=None, llama_url="http://llama"):
        from grc_agent.retrieval.config import RetrievalConfig

        self._retrieval_cfg = retrieval_cfg or RetrievalConfig(
            ask_grc_docs_default_k=3,
            search_blocks_default_k=5,
        )
        self._llama_server_url = llama_url

    def _tool_result(self, tool_name, *, ok, message=None, error_type=None):
        return {
            "ok": ok,
            "tool": tool_name,
            "message": message,
            "error_type": error_type,
        }

    def _payload_result(self, tool_name, payload):
        return {"ok": payload.get("ok", True), "tool": tool_name, **payload}


def _hit(path="wiki/widget.md", heading="Widget", text="body text", distance=0.1):
    return {
        "path": path, "heading": heading, "text": text, "distance": distance,
    }


# --- Empty question path --------------------------------------------------


def test_empty_question_returns_invalid_request():
    from grc_agent.runtime.doc_answer import ask_grc_docs
    from grc_agent.domain_models import ErrorCode

    payload = ask_grc_docs(FakeAgent(), question="   ")
    assert payload["ok"] is False
    assert payload["error_type"] == ErrorCode.INVALID_REQUEST
    assert "non-empty" in payload["message"]


def test_non_string_question_returns_invalid_request():
    from grc_agent.runtime.doc_answer import ask_grc_docs
    from grc_agent.domain_models import ErrorCode

    payload = ask_grc_docs(FakeAgent(), question=42)
    assert payload["ok"] is False
    assert payload["error_type"] == ErrorCode.INVALID_REQUEST


# --- Successful path: prompt + citations -----------------------------------


def test_successful_call_includes_sources_and_answer():
    from grc_agent.runtime.doc_answer import ask_grc_docs

    fake_agent = FakeAgent()
    hits = [
        _hit("wiki/widget.md", "Widget", "Widget text 1", 0.05),
        _hit("wiki/gizmo.md", "Gizmo", "Gizmo text 2", 0.10),
    ]
    with mock.patch(
        "grc_agent.runtime.doc_answer.VectorDocsStore"
    ) as FakeStore, mock.patch(
        "grc_agent.runtime.doc_answer._generate_grounded_answer",
        return_value="Two relevant blocks were found.",
    ):
        FakeStore.return_value.search.return_value = hits
        # Embedding call must happen ONCE (the question).
        with mock.patch(
            "grc_agent.runtime.doc_answer.embed_query",
            return_value=[0.0] * 768,
        ) as eq:
            payload = ask_grc_docs(fake_agent, question="What is a widget?")

    assert payload["ok"] is True
    assert payload["question"] == "What is a widget?"
    assert payload["answer"] == "Two relevant blocks were found."
    # Citations are reduced to {path, distance} — heading/text stripped.
    assert payload["sources"] == [
        {"path": "wiki/widget.md", "distance": 0.05},
        {"path": "wiki/gizmo.md", "distance": 0.10},
    ]
    # Embed is called with the agent's llama server URL and the question.
    eq.assert_called_once_with(fake_agent._llama_server_url, "What is a widget?")


def test_prompt_includes_each_source_path_heading_and_body():
    """The LLM prompt must carry every source (path + heading + content)."""
    from grc_agent.runtime.doc_answer import _generate_grounded_answer

    captured: list = []
    def fake_llm(agent, prompt):
        captured.append(prompt)
        return "answer"

    sources = [
        {"path": "wiki/widget.md", "heading": "Widget", "distance": 0.1,
         "content": "Widget reference body."},
        {"path": "wiki/gizmo.md", "heading": "Gizmo", "distance": 0.2,
         "content": "Gizmo reference body."},
    ]
    _generate_grounded_answer.__wrapped__ if hasattr(
        _generate_grounded_answer, "__wrapped__"
    ) else _generate_grounded_answer
    # Call the real function with a fake agent + captured LLM.
    with mock.patch(
        "grc_agent.runtime.doc_answer.call_agent_llm", side_effect=fake_llm
    ):
        _generate_grounded_answer(FakeAgent(), "What?", sources)
    assert len(captured) == 1
    prompt = captured[0]
    # Per-source attribution header is present.
    assert "wiki/widget.md" in prompt and "Widget" in prompt
    assert "wiki/gizmo.md" in prompt and "Gizmo" in prompt
    # The source bodies are quoted.
    assert "Widget reference body." in prompt
    assert "Gizmo reference body." in prompt
    # And the question is asked.
    assert "What?" in prompt


# --- Error paths ----------------------------------------------------------


def test_retrieval_backend_failure_returns_retrieval_not_ready():
    from grc_agent.runtime.doc_answer import ask_grc_docs
    from grc_agent.domain_models import ErrorCode

    fake_agent = FakeAgent()
    with mock.patch(
        "grc_agent.runtime.doc_answer.embed_query",
        side_effect=ConnectionError("embedding server down"),
    ):
        payload = ask_grc_docs(fake_agent, question="anything")

    assert payload["ok"] is False
    assert payload["error_type"] == ErrorCode.RETRIEVAL_NOT_READY
    assert "embedding server down" in payload["message"]


def test_no_chunk_hits_returns_retrieval_not_ready():
    from grc_agent.runtime.doc_answer import ask_grc_docs
    from grc_agent.domain_models import ErrorCode

    fake_agent = FakeAgent()
    with mock.patch(
        "grc_agent.runtime.doc_answer.VectorDocsStore"
    ) as FakeStore, mock.patch(
        "grc_agent.runtime.doc_answer.embed_query",
        return_value=[0.0] * 768,
    ):
        FakeStore.return_value.search.return_value = []
        payload = ask_grc_docs(fake_agent, question="alien topic")

    assert payload["ok"] is False
    assert payload["error_type"] == ErrorCode.RETRIEVAL_NOT_READY
    assert "No matching documentation" in payload["message"]


def test_answer_generation_failure_returns_internal_error():
    from grc_agent.runtime.doc_answer import ask_grc_docs
    from grc_agent.domain_models import ErrorCode

    fake_agent = FakeAgent()
    with mock.patch(
        "grc_agent.runtime.doc_answer.VectorDocsStore"
    ) as FakeStore, mock.patch(
        "grc_agent.runtime.doc_answer.embed_query",
        return_value=[0.0] * 768,
    ), mock.patch(
        "grc_agent.runtime.doc_answer._generate_grounded_answer",
        side_effect=RuntimeError("LLM boom"),
    ):
        FakeStore.return_value.search.return_value = [
            _hit("wiki/widget.md", "Widget", "Widget body", 0.1)
        ]
        payload = ask_grc_docs(fake_agent, question="What is a widget?")

    assert payload["ok"] is False
    assert payload["error_type"] == ErrorCode.INTERNAL_ERROR
    assert "LLM boom" in payload["message"]


# --- Word-cap on the prompt ----------------------------------------------


def test_prompt_word_cap_uses_flagged_truncation():
    """When the context is too large, ``_cap_words`` truncates with a
    visible flag (AGENTS.md: no silent transformation)."""
    from grc_agent.runtime.doc_answer import _generate_grounded_answer

    captured: list[str] = []
    def fake_llm(agent, prompt):
        captured.append(prompt)
        return "x"

    long_body = " ".join(f"word{i}" for i in range(2000))
    sources = [
        {"path": "wiki/big.md", "heading": "Big", "distance": 0.1,
         "content": long_body},
    ]
    with mock.patch(
        "grc_agent.runtime.doc_answer.call_agent_llm", side_effect=fake_llm
    ):
        _generate_grounded_answer(FakeAgent(), "Q?", sources)
    prompt = captured[0]
    word_count = len(prompt.split())
    assert word_count < 7000
```

- [ ] **Step 2: Run tests to verify they fail (the prompt test will pass by accident; the rest fail)**

Run: `uv run pytest tests/test_doc_answer_unit.py -v`
Expected: Many tests FAIL because `ask_grc_docs` / `_generate_grounded_answer` pull in real Ollama deps when called; the structure of these tests is what we're locking in.

- [ ] **Step 3: Adjust `ask_grc_docs` to extract `embed_query` lazily (no behavior change)**

Make the `embed_query` import module-level so the mock path works cleanly. In `src/grc_agent/runtime/doc_answer.py:78-80`, replace:
```python
def embed_query(server_url: str, query: str, *, model: str = _EMBED_MODEL) -> list[float]:
    """Embed a search query with the uniform query prefix."""
    return get_embedding(server_url, _QUERY_PREFIX + query, model=model)
```
with the already-module-level definition (no change).

If `ask_grc_docs` does a local `from … import embed_query`, move it to a module-level import so the mock.patch path matches the call site. Find:
```python
try:
    store = VectorDocsStore(DB_PATH, agent._llama_server_url)
    query_vec = embed_query(agent._llama_server_url, question.strip())
```
and replace `embed_query(…)` with the imported name. The patch target stays `"grc_agent.runtime.doc_answer.embed_query"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_doc_answer_unit.py -v`
Expected: All 10 doc_answer unit tests PASS.

- [ ] **Step 5: Re-run the existing test suite**

Run:
```bash
uv run pytest tests/test_catalog_vector.py tests/test_catalog_vector_unit.py -v
```
Expected: All green.

- [ ] **Step 6: Commit**

```bash
git add tests/test_doc_answer_unit.py src/grc_agent/runtime/doc_answer.py
git commit -m "test(doc_answer): direct unit tests for ask_grc_docs + prompt/citation shape"
```

---

## Task 3: Extract `VectorStoreBase` and migrate both stores (TDD)

**Files:**
- Create: `src/grc_agent/runtime/_vector_store_base.py`
- Create: `tests/test_vector_store_base.py`
- Modify: `src/grc_agent/runtime/doc_answer.py`
- Modify: `src/grc_agent/runtime/catalog_vector.py`

The base class owns: `_get_connection()`, `init_db()` (with hooks), `ingest_if_needed()` (idempotent), and a skeleton `search()` that returns `[(rowid, distance)]`. Subclasses override `_table_chunks()`, `_table_idx()`, `_table_fts()` (catalog only), `_read_chunk(rowid)`, and (optionally) the WHERE clause for vector KNN.

- [ ] **Step 1: Write failing tests for the base class**

Create `tests/test_vector_store_base.py`:
```python
"""Tests for ``VectorStoreBase`` — the shared skeleton of the doc + catalog stores.

A throwaway subclass is defined below (``DemoStore``) so the base class can
be exercised without depending on the production stores' table names.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest
import sqlite_vec


class DemoStore:
    """Minimal subclass to exercise VectorStoreBase behavior in isolation."""
    def __init__(self, db_path, server_url):
        self.db_path = db_path
        self.server_url = server_url

    def _table_chunks(self):
        return "demo_chunks"

    def _table_idx(self):
        return "demo_idx"

    def _table_fts(self):
        return None  # catalog-only

    def _read_chunk(self, conn, rowid):
        row = conn.execute(
            "SELECT name, body FROM demo_chunks WHERE rowid = ?",
            (rowid,),
        ).fetchone()
        return {"name": row[0], "body": row[1]} if row else None

    def _vector_columns(self):
        return "embedding float[768]"


def _patch_demo_store():
    """Patches ``VectorStoreBase`` so DemoStore mixes it in at runtime."""
    from grc_agent.runtime import _vector_store_base as _base
    from grc_agent.runtime._vector_store_base import VectorStoreBase

    _base.VectorStoreBase.register(DemoStore, DemoStore)
    DemoStore.__bases__ = (VectorStoreBase,)
    return DemoStore


def test_vector_store_base_uses_sqlite_vec_loader():
    _patch_demo_store()
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "demo.db"
        s = DemoStore(db, "http://x")
        conn = s._get_connection()
        try:
            sqlite_vec.load(conn)
            cur = conn.execute(
                "SELECT sqlite_version()"
            )
            assert cur.fetchone()[0] is not None
        finally:
            conn.close()


def test_ingest_if_needed_is_idempotent():
    _patch_demo_store()
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "demo.db"
        s = DemoStore(db, "http://x")

        def setup(conn):
            conn.execute(
                "CREATE TABLE IF NOT EXISTS demo_chunks("
                "rowid INTEGER PRIMARY KEY, name TEXT, body TEXT)"
            )
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS demo_idx "
                "USING vec0(embedding float[768])"
            )

        # Override init_db to use our schema.
        s._init_db = setup  # type: ignore[attr-defined]

        records = [
            {"rowid_hint": 1, "name": "a", "body": "alpha"},
            {"rowid_hint": 2, "name": "b", "body": "beta"},
        ]
        s.ingest_if_needed(
            records=records,
            embed_fn=lambda text: [0.1] * 768,
            insert_chunk=lambda conn, rec, body: (
                conn.execute(
                    "INSERT INTO demo_chunks(rowid, name, body) VALUES(?, ?, ?)",
                    (rec["rowid_hint"], rec["name"], body),
                ),
                rec["rowid_hint"],
            )[1],
            insert_idx=lambda conn, rowid, vec: conn.execute(
                "INSERT INTO demo_idx(rowid, embedding) VALUES(?, ?)",
                (rowid, sqlite_vec.serialize_float32(vec)),
            ),
        )
        # Second call: idempotent — does not re-embed.
        s.ingest_if_needed(
            records=records,
            embed_fn=lambda text: (_ for _ in ()).throw(
                AssertionError("embed_fn must NOT be called on the 2nd pass")
            ),
            insert_chunk=lambda *a, **k: None,
            insert_idx=lambda *a, **k: None,
        )
        conn = s._get_connection()
        try:
            total = conn.execute("SELECT count(*) FROM demo_chunks").fetchone()[0]
            assert total == 2
        finally:
            conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_vector_store_base.py -v`
Expected: FAIL — `VectorStoreBase` does not exist yet.

- [ ] **Step 3: Create `VectorStoreBase`**

Create `src/grc_agent/runtime/_vector_store_base.py`:
```python
"""Shared base for the two sqlite-vec backed stores.

``VectorDocsStore`` and ``VectorCatalogStore`` share these responsibilities:
  * open a sqlite connection with sqlite-vec loaded;
  * create the canonical tables (chunks + vec0 index + FTS5 if present);
  * ingest a list of records idempotently (skip when ``chunks`` is non-empty);
  * run vector KNN and fetch the chunk payload for each hit.

The base class parameterises the table names + read-back shape via small
hooks (``_table_chunks``, ``_table_idx``, ``_table_fts``, ``_read_chunk``,
``_vector_columns``). Subclasses retain their public API: ``ingest_if_needed``
takes a record-shaped kwarg (``blocks=`` for catalog, ``corpus_dir=`` for
docs) and ``search`` returns a list[dict] with the doc/catalog shape.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Callable

import sqlite_vec

logger = logging.getLogger(__name__)


class VectorStoreBase:
    """Thin skeleton. Subclasses MUST NOT override ``_get_connection``."""

    db_path: Path
    server_url: str

    # --- hooks subclasses override ----------------------------------------

    def _table_chunks(self) -> str:
        raise NotImplementedError

    def _table_idx(self) -> str:
        raise NotImplementedError

    def _table_fts(self) -> str | None:
        """``None`` if this store has no FTS5 table (docs store)."""
        raise NotImplementedError

    def _vector_columns(self) -> str:
        """``"embedding float[768]"`` etc."""
        raise NotImplementedError

    def _read_chunk(self, conn: sqlite3.Connection, rowid: int) -> dict[str, Any] | None:
        raise NotImplementedError

    # --- shared behavior ---------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        return conn

    def init_db(self, conn: sqlite3.Connection) -> None:
        """Default: chunks (rowid PK + payload) + vec0 index. Subclasses
        override to add FTS5 etc."""
        chunks = self._table_chunks()
        idx = self._table_idx()
        # Read the chunk table's column list from the subclass via a tiny
        # introspection call (``_chunk_columns``); default to (block_id, payload).
        cols = self._chunk_columns()  # type: ignore[attr-defined]
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {chunks}("
            "rowid INTEGER PRIMARY KEY, " + cols + ")"
        )
        conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {idx} USING vec0("
            f"{self._vector_columns()})"
        )

    def _is_populated(self, conn: sqlite3.Connection) -> bool:
        try:
            n = conn.execute(
                f"SELECT count(*) FROM {self._table_chunks()}"
            ).fetchone()[0]
            return n > 0
        except sqlite3.OperationalError:
            return False

    def ingest_if_needed(
        self,
        *,
        records: list[dict[str, Any]],
        embed_fn: Callable[[dict[str, Any]], list[float]],
        insert_chunk: Callable[[sqlite3.Connection, dict[str, Any]], int],
        insert_idx: Callable[[sqlite3.Connection, int, list[float]], None],
        insert_fts: Callable[[sqlite3.Connection, int, str], None] | None = None,
    ) -> int:
        """Idempotent ingest. Returns the number of rows inserted (0 on skip).

        ``embed_fn(record)`` must produce one float vector per record;
        ``insert_chunk(conn, record)`` writes the chunk row and returns the
        rowid; ``insert_idx(conn, rowid, vec)`` writes the vec0 row;
        ``insert_fts(conn, rowid, payload_text)`` is called only when the
        store has a FTS5 table.
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._get_connection()
        try:
            if self._is_populated(conn):
                return 0
            self.init_db(conn)
            inserted = 0
            for record in records:
                try:
                    vector = embed_fn(record)
                except Exception as exc:
                    logger.error("Embedding failed for record %s: %s",
                                 record.get("__id__", "?"), exc)
                    continue
                rowid = insert_chunk(conn, record)
                insert_idx(conn, rowid, vector)
                if insert_fts is not None:
                    insert_fts(conn, rowid, record.get("__fts_text__", ""))
                inserted += 1
            conn.commit()
            logger.info("Vector store ingested %d records.", inserted)
            return inserted
        finally:
            conn.close()

    def search(
        self,
        query_vector: list[float],
        limit: int,
        *,
        extra_results_fn: Callable[[sqlite3.Connection], list[dict[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        """Vector KNN + per-row chunk readback. Subclasses can extend the
        candidate set via ``extra_results_fn`` (the catalog does so for
        FTS5 fusion)."""
        conn = self._get_connection()
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                f"SELECT rowid, distance FROM {self._table_idx()} "
                "WHERE embedding MATCH ? AND k = ? ORDER BY distance",
                (sqlite_vec.serialize_float32(query_vector), limit),
            )
            hits: list[dict[str, Any]] = []
            seen: set[int] = set()
            for row in cursor.fetchall():
                rid = row["rowid"]
                seen.add(rid)
                chunk = self._read_chunk(conn, rid)
                if chunk:
                    chunk["distance"] = row["distance"]
                    hits.append(chunk)
            # Optional extras (catalog uses this for FTS5-only hits).
            if extra_results_fn is not None:
                for extra in extra_results_fn(conn):
                    rid = extra.get("rowid")
                    if rid in seen:
                        continue
                    if "distance" not in extra:
                        extra["distance"] = None
                    hits.append(extra)
                    seen.add(rid)
            return hits[:limit]
        finally:
            conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_vector_store_base.py -v`
Expected: PASS (the DemoStore subclass exercises the base correctly).

- [ ] **Step 5: Migrate `VectorDocsStore` to subclass `VectorStoreBase`**

In `src/grc_agent/runtime/doc_answer.py`, replace the `_get_connection`, `init_db`, `ingest_if_needed`, and `search` blocks inside `class VectorDocsStore` (currently lines 121–217) with:

```python
from grc_agent.runtime._vector_store_base import VectorStoreBase


class VectorDocsStore(VectorStoreBase):
    """sqlite-vec backed KNN store over the GNU Radio docs wiki."""

    def __init__(self, db_path: Path, server_url: str):
        self.db_path = db_path
        self.server_url = server_url

    def _table_chunks(self) -> str:
        return "docs_chunks"

    def _table_idx(self) -> str:
        return "docs_idx"

    def _table_fts(self) -> str | None:
        return None

    def _vector_columns(self) -> str:
        return f"embedding float[{_EMBED_DIM}]"

    def _chunk_columns(self) -> str:
        return "path TEXT, heading TEXT, payload TEXT"

    def _read_chunk(self, conn, rowid):
        row = conn.execute(
            "SELECT path, heading, payload FROM docs_chunks WHERE rowid = ?",
            (rowid,),
        ).fetchone()
        if not row:
            return None
        return {"path": row[0], "heading": row[1], "text": row[2]}

    def ingest_if_needed(self, corpus_dir: Path | None = None) -> int:
        """Build the docs index from ``corpus_dir`` (default ``DOCS_DIR``)."""
        corpus_dir = corpus_dir or DOCS_DIR

        records = []
        for md_path in sorted(corpus_dir.glob("*.md")):
            for chunk in _chunk_markdown(md_path):
                records.append({
                    "__id__": f"{md_path.name}#{chunk['heading']}",
                    "path": md_path.name,
                    "heading": chunk["heading"],
                    "payload_text": chunk["text"],
                    "__fts_text__": chunk["text"],
                })

        def embed_fn(record):
            embed_text = compose_chunk_text(
                Path(record["path"]), record["heading"], record["payload_text"]
            )
            return get_embedding(self.server_url, embed_text)

        def insert_chunk(conn, record):
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO docs_chunks(path, heading, payload) VALUES(?, ?, ?)",
                (record["path"], record["heading"], record["payload_text"]),
            )
            return cur.lastrowid

        def insert_idx(conn, rowid, vec):
            conn.execute(
                "INSERT INTO docs_idx(rowid, embedding) VALUES(?, ?)",
                (rowid, sqlite_vec.serialize_float32(vec)),
            )

        return super().ingest_if_needed(
            records=records,
            embed_fn=embed_fn,
            insert_chunk=insert_chunk,
            insert_idx=insert_idx,
            insert_fts=None,
        )

    def search(self, query_vector, limit):
        return super().search(query_vector, limit)
```

- [ ] **Step 6: Re-run doc_answer tests**

Run:
```bash
uv run pytest tests/test_doc_answer_unit.py tests/test_catalog_vector.py -v
```
Expected: PASS — the wire payload shape is unchanged.

- [ ] **Step 7: Migrate `VectorCatalogStore` the same way**

Replace `VectorCatalogStore` body in `src/grc_agent/runtime/catalog_vector.py:201-367` with a subclass that keeps `fuse_ranks` + `fts_match` semantics intact:

```python
class VectorCatalogStore(VectorStoreBase):
    """sqlite-vec backed KNN store for GNU Radio catalog blocks."""

    def __init__(self, db_path: Path, server_url: str):
        self.db_path = db_path
        self.server_url = server_url

    def _table_chunks(self) -> str:
        return "catalog_chunks"

    def _table_idx(self) -> str:
        return "catalog_idx"

    def _table_fts(self) -> str | None:
        return "catalog_fts"

    def _vector_columns(self) -> str:
        return f"embedding float[{_EMBED_DIM}]"

    def _chunk_columns(self) -> str:
        return "block_id TEXT, payload TEXT"

    def _read_chunk(self, conn, rowid):
        row = conn.execute(
            "SELECT block_id, payload FROM catalog_chunks WHERE rowid = ?",
            (rowid,),
        ).fetchone()
        return {"rowid": rowid, "block_id": row[0], "payload": row[1]} if row else None

    def _ensure_fts5(self, conn):
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
                "INSERT INTO catalog_fts(rowid, content) VALUES(?, ?)",
                (rid, payload or ""),
            )
        conn.commit()

    def ingest_if_needed(
        self,
        *,
        blocks: list[dict[str, Any]],
        server_url: str | None = None,
    ) -> int:
        server_url = server_url or self.server_url

        records = []
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
            records.append({
                "__id__": block_id,
                "block_id": block_id,
                "payload": body,
                "__fts_text__": body,
            })

        def embed_fn(record):
            return embed_block_text(server_url, record["payload"])

        def insert_chunk(conn, record):
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO catalog_chunks(block_id, payload) VALUES(?, ?)",
                (record["block_id"], record["payload"]),
            )
            return cur.lastrowid

        def insert_idx(conn, rowid, vec):
            conn.execute(
                "INSERT INTO catalog_idx(rowid, embedding) VALUES(?, ?)",
                (rowid, sqlite_vec.serialize_float32(vec)),
            )

        def insert_fts(conn, rowid, text):
            conn.execute(
                "INSERT INTO catalog_fts(rowid, content) VALUES(?, ?)",
                (rowid, text),
            )

        return super().ingest_if_needed(
            records=records,
            embed_fn=embed_fn,
            insert_chunk=insert_chunk,
            insert_idx=insert_idx,
            insert_fts=insert_fts,
        )

    def search(self, query, query_vector, limit):
        """Hybrid retrieval: vector KNN + FTS5 porter, weighted RRF."""
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
                chunk = self._read_chunk(conn, rowid)
                if chunk:
                    chunk["distance"] = vec_dist.get(rowid, worst_vec + 1.0)
                    matched.append(chunk)
            return matched
        finally:
            conn.close()
```

- [ ] **Step 8: Re-run all catalog/doc tests**

Run:
```bash
uv run pytest tests/test_catalog_vector.py tests/test_catalog_vector_unit.py tests/test_doc_answer_unit.py tests/test_vector_store_base.py -v
```
Expected: All green. `test_search_accepts_query_text_and_returns_fused_results`, `test_search_degrades_to_vector_when_fts_table_absent`, and the FTS porter test all still pass.

- [ ] **Step 9: Commit**

```bash
git add src/grc_agent/runtime/_vector_store_base.py src/grc_agent/runtime/doc_answer.py src/grc_agent/runtime/catalog_vector.py tests/test_vector_store_base.py
git commit -m "refactor(vector): extract VectorStoreBase shared by docs + catalog stores"
```

---

## Task 4: Sanity sweep — full default gate

**Files:** No new files.

- [ ] **Step 1: Run default suite**

Run: `uv run pytest -m "not grc_native and not gui and not llama_eval" -q`
Expected: 341 + N passed (N counts new tests), 6 skipped.

- [ ] **Step 2: Confirm no `# noqa: F401` in catalog_vector.py**

Run:
```bash
grep -n "noqa: F401" src/grc_agent/runtime/catalog_vector.py
```
Expected: NO matches (the cross-import warning is gone).

- [ ] **Step 3: Confirm shared constants live in one file**

Run:
```bash
grep -rn "_DOCUMENT_PREFIX\s*=" src/grc_agent/runtime/
```
Expected: One match (in `_embedding_config.py`).

- [ ] **Step 4: Commit any stragglers**

```bash
git status  # should be clean
```

---

## Spec compliance summary

- Direct unit tests for `ask_grc_docs`: ✅ Task 2 adds 10 tests (empty Q, non-string Q, success path with sources, prompt shape, 3 error paths, word cap flag).
- `VectorStoreBase` shared skeleton: ✅ Task 3 reduces 7 duplicated init/search lines per store to 1 hooks trio.
- Single home for embedding constants: ✅ Task 1 creates `_embedding_config.py`; the `# noqa: F401` cross-import is gone.
- Wire-level behavior unchanged: ✅ Task 2 Step 6 + Task 3 Step 6 + Step 8 re-run existing tests verbatim; the `_payload_result` shape (`ok`, `question`, `answer`, `sources=[{path,distance}]`) is identical.

## Self-review

**Spec coverage:** All three sub-goals (ask_grc_docs tests, shared base, single constant home) covered by explicit tasks. Wire behavior is locked by the existing 18 catalog/vector tests continuing to pass.
**Placeholder scan:** No "TBD". Every task has complete code.
**Type consistency:** `VectorStoreBase._get_connection` / `init_db` / `search` are only defined on the base; both subclasses name their overrides consistently (`_table_chunks`, `_table_idx`, `_table_fts`, `_vector_columns`, `_chunk_columns`, `_read_chunk`). The catalog store's hybrid search method retains its public signature `(query, query_vector, limit)` to keep `tests/test_catalog_vector.py::HybridSearchTests` green.
