"""Unit tests for adapter_rag — split from the former test_unit.py god file.

Minimal set per the clustered test plan; shared fixtures/helpers live in conftest.py.
"""

import asyncio
import json


def test_vector_db_dimension_check_is_cached(tmp_path, monkeypatch):
    """Regression for P1-2: _ensure_db_built used to call embed_document("test")
    on every query, doubling embedding API calls. The dimension check must be
    cached per (domain, model) so subsequent queries only issue the real
    query embedding."""
    from grc_agent.adapter import _ensure_db_built, get_db_and_model

    tmp_vectors = tmp_path / "vectors"
    tmp_vectors.mkdir()
    monkeypatch.setenv("GRC_AGENT_VECTORS_DIR", str(tmp_vectors))
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    # Isolate the module-level embedding-dimension cache so a prior test's
    # real 768-dim entry doesn't cause a false dimension mismatch (768 != 3)
    # that triggers a full DB rebuild instead of the cached dimension check
    # this test is measuring.
    monkeypatch.setattr("grc_agent.adapter.rag._EMBEDDING_DIM_CACHE", {})

    from grc_agent.settings import save_settings

    save_settings("ollama_local", "qwen3.6:35b-a3b-q4_K_M", embed_backend="llamacpp")
    db_path, model = get_db_and_model("catalog")

    # Build a minimal valid sqlite-vec DB with a known dimension so
    # _ensure_db_built reaches the dimension-check branch.
    import sqlite3

    import sqlite_vec

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.execute(
        "CREATE TABLE catalog_chunks(rowid INTEGER PRIMARY KEY, block_id TEXT, payload TEXT);"
    )
    conn.execute("CREATE VIRTUAL TABLE catalog_idx USING vec0(embedding float[3]);")
    # A catalog_fts table must also exist, or _ensure_db_built treats this as
    # a pre-lexical-fallback DB and rebuilds it (see rag.py's _build_db).
    conn.execute(
        "CREATE VIRTUAL TABLE catalog_fts USING fts5("
        "block_id, payload, content='catalog_chunks', content_rowid='rowid')"
    )
    # _db_meta must exist with the correct model name and corpus_version,
    # otherwise _ensure_db_built deletes and rebuilds the DB (calling
    # embed_document many times during ingestion, not just once for the
    # dimension check).
    from grc_agent.adapter import _corpus_version

    conn.execute("CREATE TABLE _db_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO _db_meta (key, value) VALUES ('embedding_model', ?)", (model,))
    conn.execute(
        "INSERT INTO _db_meta (key, value) VALUES ('corpus_version', ?)",
        (_corpus_version("catalog"),),
    )
    conn.commit()
    conn.close()

    call_count = 0

    def counting_embed_document(text, m):  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        return [0.0, 0.0, 0.0]

    monkeypatch.setattr("grc_agent.adapter.rag.embed_document", counting_embed_document)

    _ensure_db_built("catalog", db_path, model)
    first_count = call_count
    assert first_count == 1, "first query should perform the dimension check"

    _ensure_db_built("catalog", db_path, model)
    assert call_count == first_count, "second query must not repeat the dimension check"

    # Clean up: the monkeypatched embed_document populated the module-level
    # _EMBEDDING_DIM_CACHE with a 3-dim entry for this model. Without this
    # cleanup, subsequent tests that use the real embed_document (768-dim)
    # would see a dimension mismatch, delete the real DB, and rebuild it
    # unnecessarily — or worse, leave a stale 3-dim DB behind.
    from grc_agent.adapter import _EMBEDDING_DIM_CACHE

    _EMBEDDING_DIM_CACHE.pop(model, None)


def test_fts_query_string_dedupes_and_caps_tokens():
    """Regression: an adversarially long or highly repetitive query used to
    build an uncapped OR-joined FTS5 MATCH expression whose own size drove
    evaluation cost — measured at 8-46 seconds for a ~100k-character query,
    synchronously blocking the calling thread. _fts_query_string must
    dedupe (case-insensitively, order-preserving) and cap at _FTS_MAX_TOKENS."""
    from grc_agent.adapter.rag import _FTS_MAX_TOKENS, _fts_query_string

    # Repetitive input collapses to a single token, not one term per repeat.
    result = _fts_query_string("filter Filter FILTER filter filter")
    assert result == ('"filter"', False)

    # A very long, highly-repetitive query stays bounded regardless of input size.
    huge_query = " ".join(f"word{i % 5}" for i in range(50_000))
    result = _fts_query_string(huge_query)
    assert result is not None
    assert result[0].count(" OR ") + 1 <= _FTS_MAX_TOKENS

    # Genuinely varied input beyond the cap is truncated, not rejected, and flagged.
    many_unique = " ".join(f"uniqueterm{i}" for i in range(1000))
    result = _fts_query_string(many_unique)
    assert result is not None
    assert result[0].count(" OR ") + 1 == _FTS_MAX_TOKENS
    assert '"uniqueterm0"' in result[0]
    assert result[1] is True  # was_capped


def test_query_knowledge_func_raises_model_retry_on_failure(monkeypatch):
    """Same uniform rule as inspect_graph/change_graph: a failed lookup raises
    ModelRetry carrying the engine's own message, instead of handing the model
    an ok=false blob inside a successful tool return."""
    import pytest
    from pydantic_ai.exceptions import ModelRetry

    from grc_agent.agent import query_knowledge_func

    def failing(_query, _limit=5):
        return {"ok": False, "results": [], "message": "query must be non-empty"}

    monkeypatch.setattr("grc_agent.agent.query_catalog", failing)
    monkeypatch.setattr("grc_agent.agent.query_docs", failing)

    for domain in ("catalog", "docs"):
        with pytest.raises(ModelRetry, match="query must be non-empty"):
            asyncio.run(query_knowledge_func(None, "", domain))


def test_query_knowledge_func_passes_through_k(monkeypatch):
    """The model can control how many results come back via k (default 5,
    clamped 1-20) — no live LLM/backend needed, just verifying the plumbing."""

    from grc_agent.agent import query_knowledge_func

    seen_calls: list[tuple[str, int]] = []

    def fake_query_catalog(query, limit=5):
        seen_calls.append((query, limit))
        return {"ok": True, "query": query, "results": [], "search_mode": "vector"}

    def fake_query_docs(query, limit=5):
        seen_calls.append((query, limit))
        return {"ok": True, "query": query, "answer": "", "search_mode": "vector"}

    monkeypatch.setattr("grc_agent.agent.query_catalog", fake_query_catalog)
    monkeypatch.setattr("grc_agent.agent.query_docs", fake_query_docs)

    asyncio.run(query_knowledge_func(None, "low pass filter", "catalog", k=10))
    assert seen_calls[-1] == ("low pass filter", 10)

    asyncio.run(query_knowledge_func(None, "stream tags", "docs"))
    assert seen_calls[-1] == ("stream tags", 5), "default k must still be 5"

    # Out-of-range k is clamped, not passed through raw or rejected.
    asyncio.run(query_knowledge_func(None, "x", "catalog", k=1000))
    assert seen_calls[-1] == ("x", 20)
    asyncio.run(query_knowledge_func(None, "x", "catalog", k=0))
    assert seen_calls[-1] == ("x", 1)


def test_generate_python_func_passes_through_k_and_wraps_valueerror(monkeypatch):
    """Mirrors test_query_knowledge_func_passes_through_k for generate_python_func:
    k (default 5) reaches preview_flowgraph_py correctly (the engine clamps
    1-20 internally), and a ValueError from it (invalid/hb/cpp graph) becomes
    a ModelRetry, not a raw exception — no live LLM/backend or real gnuradio
    flowgraph needed."""
    from types import SimpleNamespace

    from pydantic_ai import ModelRetry

    from grc_agent.agent import generate_python_func

    seen_calls: list[tuple[object, int]] = []

    def fake_preview_flowgraph_py(flow_graph, k=5):
        seen_calls.append((flow_graph, k))
        return {"files": [{"path": "x.py", "source": "..."}], "omitted_files": 0}

    monkeypatch.setattr("grc_agent.agent.preview_flowgraph_py", fake_preview_flowgraph_py)
    ctx = SimpleNamespace(deps=object())

    asyncio.run(generate_python_func(ctx, k=10))
    assert seen_calls[-1] == (ctx.deps, 10)

    asyncio.run(generate_python_func(ctx))
    assert seen_calls[-1] == (ctx.deps, 5), "default k must still be 5"

    # The wrapper passes k through verbatim — the 1-20 clamp lives in the
    # engine (preview_flowgraph_py), not here.
    asyncio.run(generate_python_func(ctx, k=1000))
    assert seen_calls[-1] == (ctx.deps, 1000)

    def raising_preview_flowgraph_py(flow_graph, k=5):  # noqa: ARG001
        raise ValueError("Flowgraph is not valid: ['boom']")

    monkeypatch.setattr("grc_agent.agent.preview_flowgraph_py", raising_preview_flowgraph_py)
    try:
        asyncio.run(generate_python_func(ctx))
        raise AssertionError("expected ModelRetry")
    except ModelRetry as exc:
        assert "boom" in str(exc)


def test_save_block_func_branch_coverage(monkeypatch):
    """All three branches of save_block_func in one test: deps.save_block
    (live-app proxy), save_block_to_library fallback (raw FlowGraph deps),
    and ModelRetry on a failed save."""
    from types import SimpleNamespace

    from pydantic_ai import ModelRetry

    from grc_agent.agent import save_block_func

    # Branch 1: ctx.deps.save_block() is called directly.
    seen_calls = []

    async def fake_save_block(
        instance_name, block_id=None, label=None, category=None, overwrite=False
    ):
        seen_calls.append((instance_name, block_id, label, category, overwrite))
        return {"ok": True, "block_id": block_id or instance_name}

    ctx = SimpleNamespace(deps=SimpleNamespace(save_block=fake_save_block))
    result = asyncio.run(save_block_func(ctx, "my_epy", block_id="my_saved_block"))
    assert seen_calls == [("my_epy", "my_saved_block", None, None, False)]
    assert json.loads(result)["ok"] is True

    # Branch 2: no deps.save_block -> save_block_to_library fallback.
    seen_calls = []

    def fake_save_block_to_library(
        flow_graph, instance_name, block_id=None, label=None, category=None, overwrite=False
    ):
        seen_calls.append((flow_graph, instance_name, block_id, label, category, overwrite))
        return {"ok": True, "block_id": block_id or instance_name}

    monkeypatch.setattr("grc_agent.agent.save_block_to_library", fake_save_block_to_library)
    ctx = SimpleNamespace(deps=object())
    asyncio.run(save_block_func(ctx, "my_epy", block_id="my_saved_block"))
    assert seen_calls == [(ctx.deps, "my_epy", "my_saved_block", None, None, False)]

    # Branch 3: a failed save raises ModelRetry (never returns ok=false).
    def failing_save_block_to_library(flow_graph, instance_name, **kwargs):  # noqa: ARG001
        return {"ok": False, "error_type": "block_id_collision", "errors": ["boom"]}

    monkeypatch.setattr("grc_agent.agent.save_block_to_library", failing_save_block_to_library)
    ctx = SimpleNamespace(deps=object())
    try:
        asyncio.run(save_block_func(ctx, "my_epy"))
        raise AssertionError("expected ModelRetry")
    except ModelRetry as exc:
        assert "boom" in str(exc)


def test_render_catalog_block_exposes_vlen():
    """The catalog renderer must carry the same vlen rule as render_port so
    query_knowledge can show vector ports (fft_vxx vlen 1024) vs scalar."""
    from grc_agent.adapter import render_catalog_block

    r = render_catalog_block("fft_vxx", 0.0)
    assert r is not None
    assert r["inputs"][0].get("vlen") not in (None, 1, "1", "")
    r2 = render_catalog_block("blocks_float_to_complex", 0.0)
    assert r2 is not None
    for p in r2["inputs"] + r2["outputs"]:
        assert "vlen" not in p
