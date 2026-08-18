import os

import pytest
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel

from grc_agent.adapter import _embed_endpoint, get_db_and_model
from grc_agent.agent import build_scenario_model, grc_tools
from grc_agent.agent_factory import _build_model, _retrying_http_client
from grc_agent.settings import (
    env_path,
    get_env_value,
    load_settings,
    save_settings,
    upsert_env_key,
)


def test_settings_isolation_and_defaults(tmp_path, monkeypatch):
    """Verify that settings are saved/loaded correctly and that ollama_model
    and openai_compatible_model are handled independently (no overwriting).
    """
    tmp_env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_env_file))

    # 1. Load initial settings (defaults)
    cfg = load_settings()
    assert cfg["provider"] == "ollama_local"
    assert cfg["ollama_model"] == "qwen3.6:35b-a3b-q4_K_M"
    assert cfg["openai_compatible_model"] == "deepseek/deepseek-v4-flash"

    # 2. Switch provider to openai_compatible and change model
    save_settings("openai_compatible", "google/gemini-2.5-flash")
    cfg = load_settings()
    assert cfg["provider"] == "openai_compatible"
    assert cfg["model"] == "google/gemini-2.5-flash"
    assert cfg["openai_compatible_model"] == "google/gemini-2.5-flash"
    assert cfg["ollama_model"] == "qwen3.6:35b-a3b-q4_K_M"  # preserved!

    # 3. Switch back to ollama and change model
    save_settings("ollama_local", "mistral-large")
    cfg = load_settings()
    assert cfg["provider"] == "ollama_local"
    assert cfg["model"] == "mistral-large"
    assert cfg["ollama_model"] == "mistral-large"
    assert cfg["openai_compatible_model"] == "google/gemini-2.5-flash"  # preserved!


def test_db_and_model_isolation(tmp_path, monkeypatch):
    """Verify database filenames and embedding model settings are disjoint.

    Ollama queries/embeddings should only target *_ollama.db.
    OpenRouter queries/embeddings should only target *_openrouter.db.
    """
    tmp_env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_env_file))

    # Test under Ollama provider
    save_settings("ollama_local", "qwen3.6:35b-a3b-q4_K_M")
    db_path_ollama, model_ollama = get_db_and_model("catalog")
    assert db_path_ollama.endswith("catalog_ollama.db")
    assert "catalog_openai_compatible.db" not in db_path_ollama

    # Test under OpenAI-Compatible provider
    save_settings(
        "openai_compatible",
        "openai/gpt-4o-mini",
        openai_compatible_base_url="https://openrouter.ai/api/v1",
    )
    db_path_compat, model_compat = get_db_and_model("catalog")
    assert db_path_compat.endswith("catalog_openai_compatible.db")
    assert "catalog_ollama.db" not in db_path_compat


def test_embed_endpoint_isolation(tmp_path, monkeypatch):
    """Verify API endpoints and keys do not leak or overlap.

    When Ollama is selected, it must target localhost:11434 and use 'not-needed'.
    When OpenAI-compatible is selected, it must target configured endpoint and use key.
    """
    tmp_env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_env_file))
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "dummy-openrouter-key")

    # Ollama provider check
    save_settings("ollama_local", "qwen3.6:35b-a3b-q4_K_M")
    base_url, api_key, _uds = _embed_endpoint()
    assert base_url == "http://localhost:11434/v1"
    assert api_key == "not-needed"

    # OpenAI-compatible provider check
    save_settings(
        "openai_compatible",
        "openai/gpt-4o-mini",
        openai_compatible_base_url="https://openrouter.ai/api/v1",
    )
    base_url, api_key, _uds = _embed_endpoint()
    assert base_url == "https://openrouter.ai/api/v1"
    assert api_key == "dummy-openrouter-key"


def test_get_embed_client_never_returns_mismatched_client_for_key(tmp_path, monkeypatch):
    """Regression: _embed_client/_embed_client_key used to be two separate
    globals updated in two statements — a thread race between two different
    endpoints (e.g. a provider switch overlapping a cold catalog+docs query)
    could leave a NEW client paired with the OLD key-tag, so a later caller
    computing the old key would see it "match" and silently reuse the wrong
    endpoint/credentials. Bundled into one atomically-assigned tuple; this
    verifies the client returned always matches the endpoint it was built
    for, across repeated endpoint changes (a structural check that the
    cache-key and the cached client can never be observed out of sync,
    which the single-tuple design guarantees regardless of thread timing)."""
    import grc_agent.adapter.rag as rag_mod

    tmp_env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_env_file))
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "dummy-openrouter-key")
    rag_mod._embed_client_state = None

    try:
        save_settings("ollama_local", "qwen3.6:35b-a3b-q4_K_M")
        client_ollama = rag_mod._get_embed_client()
        assert str(client_ollama.base_url).rstrip("/") == "http://localhost:11434/v1"

        save_settings(
            "openai_compatible",
            "openai/gpt-4o-mini",
            openai_compatible_base_url="https://openrouter.ai/api/v1",
        )
        client_openrouter = rag_mod._get_embed_client()
        assert str(client_openrouter.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
        assert client_openrouter is not client_ollama

        # Switch back — must rebuild again
        save_settings("ollama_local", "qwen3.6:35b-a3b-q4_K_M")
        client_ollama_again = rag_mod._get_embed_client()
        assert str(client_ollama_again.base_url).rstrip("/") == "http://localhost:11434/v1"
    finally:
        rag_mod._embed_client_state = None


def test_web_build_model_isolation(tmp_path, monkeypatch):
    """Verify that agent_factory._build_model instantiates the correct model type based on the settings."""
    tmp_env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_env_file))
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "dummy-test-key")

    http_client = _retrying_http_client()

    cfg = {"provider": "ollama_local", "model": "qwen3.6:35b-a3b-q4_K_M"}
    m = _build_model(cfg, http_client)
    assert isinstance(m, OllamaModel)
    assert m.model_name == "qwen3.6:35b-a3b-q4_K_M"

    cfg = {
        "provider": "openai_compatible",
        "model": "deepseek/deepseek-v4-flash",
        "openai_compatible_base_url": "https://openrouter.ai/api/v1",
    }
    m = _build_model(cfg, http_client)
    assert isinstance(m, OpenAIChatModel)
    assert m.model_name == "deepseek/deepseek-v4-flash"

    # Ollama remote / cloud (ollama.com) with API key
    upsert_env_key("OLLAMA_API_KEY", "dummy-test-key")
    cfg = {
        "provider": "ollama_cloud",
        "model": "deepseek-v4-flash:cloud",
    }
    m = _build_model(cfg, http_client)
    assert isinstance(m, OllamaModel)
    assert m.model_name == "deepseek-v4-flash:cloud"

    cfg = {
        "provider": "openai_compatible",
        "model": "vllm-model",
        "openai_compatible_base_url": "http://localhost:8000/v1",
    }
    m = _build_model(cfg, http_client)
    assert isinstance(m, OpenAIChatModel)
    assert m.model_name == "vllm-model"


def test_scenario_model_builder_uses_provider(monkeypatch):
    """Regression for P2-7: the scenario harness must be able to build a model
    for either backend so integration tests can run against Ollama or OpenAI-compatible."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-test-key")
    ollama = build_scenario_model("ollama")
    assert isinstance(ollama, OllamaModel)

    ollama_cloud = build_scenario_model("ollama_cloud", "deepseek-v4-flash:cloud")
    assert isinstance(ollama_cloud, OllamaModel)
    assert ollama_cloud.model_name == "deepseek-v4-flash:cloud"

    openrouter = build_scenario_model("openrouter", "google/gemini-2.5-flash")
    assert isinstance(openrouter, OpenAIChatModel)
    assert openrouter.model_name == "google/gemini-2.5-flash"

    openai_compat = build_scenario_model("openai_compatible", "my-custom-model")
    assert isinstance(openai_compat, OpenAIChatModel)
    assert openai_compat.model_name == "my-custom-model"


# ── New comprehensive tests for the .env consolidation ──────────────────────


def test_env_path_resolution(tmp_path, monkeypatch):
    """GRC_AGENT_ENV override must take priority over find_dotenv and the
    ~/.config fallback — otherwise a test with a temp .env could accidentally
    pick up the real repo .env (with live API keys)."""
    # 1. Override takes priority
    override = tmp_path / "custom.env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(override))
    assert env_path() == override

    # 2. Without override, env_path() resolves the fixed, package-relative
    # repo-root .env — it deliberately ignores CWD (GRC changes the working
    # directory dynamically), unlike the old find_dotenv()-based CWD walk.
    monkeypatch.delenv("GRC_AGENT_ENV", raising=False)
    found = env_path()
    assert found.name == ".env"
    assert found.exists()


def test_upsert_env_key_inserts_and_updates(tmp_path):
    """upsert_env_key must insert a new key, update an existing one, and
    preserve unrelated keys."""
    env = tmp_path / ".env"

    # Insert
    upsert_env_key("GRC_PROVIDER", "ollama_local", path=env)
    content = env.read_text(encoding="utf-8")
    assert "GRC_PROVIDER=ollama_local" in content

    # Update
    upsert_env_key("GRC_PROVIDER", "openai_compatible", path=env)
    content = env.read_text(encoding="utf-8")
    assert content.count("GRC_PROVIDER=") == 1
    assert "GRC_PROVIDER=openai_compatible" in content

    # Insert second key — first must be preserved
    upsert_env_key("OPENAI_COMPATIBLE_MODEL", "deepseek/deepseek-v4-flash", path=env)
    content = env.read_text(encoding="utf-8")
    assert "GRC_PROVIDER=openai_compatible" in content
    assert "OPENAI_COMPATIBLE_MODEL=deepseek/deepseek-v4-flash" in content
    assert content.count("GRC_PROVIDER=") == 1
    assert content.count("OPENAI_COMPATIBLE_MODEL=") == 1


def test_get_env_value_reads_from_file_not_os_environ(tmp_path, monkeypatch):
    """get_env_value must read from the .env file, not os.environ — the
    health check uses it to distinguish saved keys from the running process's
    startup snapshot."""
    env = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env))

    # Write a key to the file
    upsert_env_key("OPENAI_COMPATIBLE_API_KEY", "file-key-123", path=env)

    # Set a DIFFERENT value in os.environ (simulating a stale startup snapshot)
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "env-key-456")

    # get_env_value must return the file value, not the env var
    assert get_env_value("OPENAI_COMPATIBLE_API_KEY") == "file-key-123"

    # For a key not in the file, must return None
    assert get_env_value("NONEXISTENT_KEY") is None


def test_build_model_ollama_cloud_raises_on_missing_api_key(tmp_path, monkeypatch):
    """When connecting to Ollama Cloud (https://ollama.com), _build_model
    must raise explicitly if no API key is configured."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    monkeypatch.delenv("OLLAMA_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    http_client = _retrying_http_client()
    with pytest.raises(ValueError, match="API key is required"):
        _build_model(
            {
                "provider": "ollama_cloud",
                "model": "deepseek-v4-flash:cloud",
                "ollama_base_url": "https://ollama.com/v1",
            },
            http_client,
        )


def test_save_settings_writes_openai_compatible_model_to_env(tmp_path, monkeypatch):
    """save_settings for openai_compatible must write GRC_PROVIDER and
    OPENAI_COMPATIBLE_MODEL to the .env file, and preserve other providers' models."""
    env = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env))

    # First save ollama — sets GRC_PROVIDER + OLLAMA_CHAT_MODEL
    save_settings("ollama_local", "qwen3.6:35b-a3b-q4_K_M")
    content = env.read_text(encoding="utf-8")
    assert "GRC_PROVIDER=ollama_local" in content
    assert "OLLAMA_CHAT_MODEL=qwen3.6:35b-a3b-q4_K_M" in content

    # Now save openai_compatible — must add OPENAI_COMPATIBLE_MODEL and update
    # GRC_PROVIDER, but preserve OLLAMA_CHAT_MODEL
    save_settings("openai_compatible", "deepseek/deepseek-v4-flash")
    content = env.read_text(encoding="utf-8")
    assert "GRC_PROVIDER=openai_compatible" in content
    assert "OPENAI_COMPATIBLE_MODEL=deepseek/deepseek-v4-flash" in content
    assert "OLLAMA_CHAT_MODEL=qwen3.6:35b-a3b-q4_K_M" in content  # preserved

    # load_settings must reflect the saved state
    cfg = load_settings()
    assert cfg["provider"] == "openai_compatible"
    assert cfg["model"] == "deepseek/deepseek-v4-flash"
    assert cfg["openai_compatible_model"] == "deepseek/deepseek-v4-flash"
    assert cfg["ollama_model"] == "qwen3.6:35b-a3b-q4_K_M"  # preserved


def test_build_model_fallback_does_not_mutate_cfg(tmp_path, monkeypatch):
    """When _build_model() fails (e.g. OpenAI-compatible with bad config), the
    fallback in build_interactive_agent must NOT mutate the saved cfg."""
    env = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env))
    save_settings("openai_compatible", "openai/gpt-4o-mini")

    from grc_agent.settings import default_settings

    http_client = _retrying_http_client()
    saved_cfg = load_settings()
    fallback_cfg = default_settings()
    fallback_model = _build_model(fallback_cfg, http_client)
    assert isinstance(fallback_model, OllamaModel)
    assert saved_cfg["provider"] == "openai_compatible"
    assert saved_cfg["model"] == "openai/gpt-4o-mini"


def test_rag_building_flag_set_during_ensure_db_built(tmp_path, monkeypatch):
    """_rag_building must be set to 'building' before the DB build and
    'ready' after (per-domain), so the GUI can show a progress banner."""
    import grc_agent.adapter as adapter_mod
    from grc_agent.adapter import _ensure_db_built, get_db_and_model

    tmp_vectors = tmp_path / "vectors"
    tmp_vectors.mkdir()
    monkeypatch.setenv("GRC_AGENT_VECTORS_DIR", str(tmp_vectors))
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    save_settings("ollama_local", "qwen3.6:35b-a3b-q4_K_M")
    db_path, model = get_db_and_model("catalog")

    # _rag_building is module-global; a prior test's build may have left a
    # catalog entry. This test verifies the building->ready transition, so
    # reset to pristine.
    adapter_mod._rag_building.pop("catalog", None)
    assert adapter_mod._rag_building.get("catalog") is None

    # Import ingest first so it's in sys.modules, then patch it.
    import grc_agent.ingest as ingest_mod

    def mock_ingest(db_path, model, on_progress=None):  # noqa: ARG001
        # _build_db MUST forward the progress callback — assert it unconditionally
        # (an `if on_progress is not None` guard here would silently pass if the
        # wiring regressed and None was passed).
        assert on_progress is not None, "_build_db did not forward on_progress to ingest"
        entry = adapter_mod._rag_building["catalog"]
        # Verify the entry is 'building' during the ingest call, with counters reset.
        assert entry["status"] == "building"
        assert entry["current"] == 0
        assert entry["total"] == 0
        # The progress callback must write back into the per-domain entry so the
        # GUI poller can surface live progress.
        on_progress(7, 10)
        assert entry["current"] == 7
        assert entry["total"] == 10
        return 5  # embedded count (distinct from total=10, to prove the GUI can show it)

    monkeypatch.setattr(ingest_mod, "ingest_catalog", mock_ingest)

    # Build the DB (mocked)
    _ensure_db_built("catalog", db_path, model)
    # After build, the entry should be 'ready' and carry the embedded count.
    entry = adapter_mod._rag_building["catalog"]
    assert entry["status"] == "ready"
    assert entry["indexed"] == 5


def test_ingest_catalog_reports_progress_per_block(tmp_path, monkeypatch):
    """ingest_catalog must call on_progress once per block with (current, total)
    — including blocks that fail to render/embed — so the GUI progress bar
    reflects processed/total, not successful/total."""
    import grc_agent.ingest as ingest_mod

    class FakePlatform:
        blocks = ["blocks/keep_a", "blocks/fails_render", "_skip_internal", "blocks/keep_c"]

    def fake_render(block_id, distance=0.0):  # noqa: ARG001
        if block_id == "blocks/fails_render":
            raise RuntimeError("render boom")
        return {
            "label": block_id,
            "block_id": block_id,
            "category": "test",
            "params": {},
            "inputs": [],
            "outputs": [],
        }

    monkeypatch.setattr(ingest_mod, "get_platform", lambda: FakePlatform())
    monkeypatch.setattr(ingest_mod, "render_catalog_block", fake_render)
    monkeypatch.setattr(ingest_mod, "embed_document", lambda text, model: [0.1, 0.2, 0.3])  # noqa: ARG005

    db_path = str(tmp_path / "catalog.db")
    seen: list[tuple[int, int]] = []
    n = ingest_mod.ingest_catalog(
        db_path, "fake-model", on_progress=lambda cur, tot: seen.append((cur, tot))
    )

    # 2 of the 3 non-underscore blocks indexed (the failing-render one skipped);
    # total still counts all 3 non-underscore blocks.
    assert n == 2
    totals = {t for _, t in seen}
    assert totals == {3}
    # Progress still ticks once per block — including the one that failed to render.
    assert [c for c, _ in seen] == [1, 2, 3]


def test_catalog_corpus_version_reflects_block_set(monkeypatch):
    """OOT detection: the catalog corpus_version must change when the live block
    set changes (so a freshly installed OOT module triggers a rebuild), instead
    of being pinned to GNU Radio's version string. Order-independent (sorted)."""
    import grc_agent.adapter.graph as graph_mod
    from grc_agent.adapter.rag import _CORPUS_VERSION_CACHE, _corpus_version

    class FakePlatform:
        def __init__(self, blocks):
            self.blocks = blocks

    def version_for(blocks):
        _CORPUS_VERSION_CACHE.pop("catalog", None)
        monkeypatch.setattr(graph_mod, "get_platform", lambda: FakePlatform(blocks))
        return _corpus_version("catalog")

    try:
        v1 = version_for(["blocks/a", "blocks/b"])
        # Same set, reordered → stable hash.
        assert version_for(["blocks/b", "blocks/a"]) == v1
        # A newly added OOT block changes the identity → triggers rebuild.
        assert version_for(["blocks/a", "blocks/b", "blocks/oot_new"]) != v1
        # A removed block also changes the identity.
        assert version_for(["blocks/a"]) != v1
    finally:
        # Don't poison the module-level cache for tests that run after this one
        # (test_unit's RAG tests call _corpus_version("catalog") for real, and a
        # stale fake-platform hash here would force a spurious rebuild there).
        _CORPUS_VERSION_CACHE.pop("catalog", None)


def test_ingest_catalog_builds_lexical_only_when_all_embeds_fail(tmp_path, monkeypatch):
    """When the embedding backend is unreachable for every block, ingest_catalog
    must still build a usable FTS5 lexical index from the real block catalog
    (no vector index) instead of raising — this is what makes the
    query_knowledge fallback possible on a cold cache with no reachable
    embedding backend at all."""
    import sqlite3

    import sqlite_vec

    import grc_agent.ingest as ingest_mod

    calls = []

    def fail_embed(text, model):  # noqa: ARG001
        calls.append(text)
        raise RuntimeError("backend down")

    monkeypatch.setattr(ingest_mod, "embed_document", fail_embed)

    db_path = str(tmp_path / "catalog.db")
    n = ingest_mod.ingest_catalog(db_path, "fake-model")
    assert n > 0
    assert len(calls) == 1, "backend probe failure must avoid re-calling embed_document per block"

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
        ).fetchall()
    }
    assert "catalog_fts" in tables
    assert "catalog_idx" not in tables, "no vector index should exist when every embed failed"

    rows = conn.execute(
        "SELECT rowid FROM catalog_fts WHERE catalog_fts MATCH ? ORDER BY bm25(catalog_fts) LIMIT 5",
        ('"low" OR "pass" OR "filter"',),
    ).fetchall()
    block_ids = {
        conn.execute("SELECT block_id FROM catalog_chunks WHERE rowid = ?", (r[0],)).fetchone()[0]
        for r in rows
    }
    conn.close()
    assert any("low_pass_filter" in b for b in block_ids)


def test_build_lock_for_returns_same_lock_under_real_thread_contention():
    """Regression: _build_lock_for's lazy per-domain lock creation used to be
    unsynchronized check-then-act (get, then if-None construct-and-store) —
    two real OS threads racing to build the SAME domain for the first time
    could each construct their own Lock() before either published it,
    returning two DIFFERENT lock objects and taking zero mutual exclusion
    from each other (exactly the race the lock exists to prevent). Fixed via
    dict.setdefault (atomic in CPython). Stress-tested with real threads and
    a barrier to maximize contention at the exact race window."""
    import threading

    from grc_agent.adapter.rag import _BUILD_LOCKS, _build_lock_for

    domain = "stress-test-domain"
    _BUILD_LOCKS.pop(domain, None)
    try:
        n = 50
        barrier = threading.Barrier(n)
        results: list[threading.Lock] = [None] * n  # type: ignore[list-item]

        def worker(i: int) -> None:
            barrier.wait()  # release all threads at (as close to) the same instant
            results[i] = _build_lock_for(domain)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        distinct_lock_ids = {id(lock) for lock in results}
        assert len(distinct_lock_ids) == 1, (
            f"expected every thread to receive the SAME lock object, got "
            f"{len(distinct_lock_ids)} distinct lock instances — mutual "
            f"exclusion was bypassed"
        )
    finally:
        _BUILD_LOCKS.pop(domain, None)


def test_lexical_only_db_does_not_rehammer_embedding_backend(tmp_path, monkeypatch):
    """Once a catalog DB has settled into lexical-only (the embedding backend
    was down when it was last built), subsequent queries must not keep
    re-attempting a full re-embed on every call — only a genuine corpus
    change should give embedding a fresh chance (see rag.py's _build_db)."""
    import grc_agent.ingest as ingest_mod
    from grc_agent.adapter import _ensure_db_built, get_db_and_model
    from grc_agent.adapter.rag import _FRESHNESS_CACHE

    tmp_vectors = tmp_path / "vectors"
    tmp_vectors.mkdir()
    monkeypatch.setenv("GRC_AGENT_VECTORS_DIR", str(tmp_vectors))
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    save_settings("ollama_local", "qwen3.6:35b-a3b-q4_K_M")
    db_path, model = get_db_and_model("catalog")

    def fail_embed(text, model):  # noqa: ARG001
        raise RuntimeError("backend down")

    monkeypatch.setattr(ingest_mod, "embed_document", fail_embed)

    # First build: real ingestion, every embed call fails -> lexical-only DB.
    _ensure_db_built("catalog", db_path, model)
    assert os.path.exists(db_path)

    # Second call, same (unchanged) corpus: must not re-invoke ingestion.
    real_ingest_catalog = ingest_mod.ingest_catalog
    called = {"n": 0}

    def counting_ingest(*args, **kwargs):
        called["n"] += 1
        return real_ingest_catalog(*args, **kwargs)

    monkeypatch.setattr(ingest_mod, "ingest_catalog", counting_ingest)
    try:
        _ensure_db_built("catalog", db_path, model)
        assert called["n"] == 0, (
            "a lexical-only DB with an unchanged corpus must not re-attempt ingestion"
        )
    finally:
        _FRESHNESS_CACHE.pop("catalog", None)


def test_query_catalog_falls_back_to_lexical_when_embedding_unreachable(tmp_path, monkeypatch):
    """End-to-end: query_catalog must return real, tagged results via the
    FTS5 fallback when embed_query fails, instead of the old hard failure
    ({"ok": False, "message": "Embedding failed: ..."})."""
    import grc_agent.ingest as ingest_mod
    from grc_agent.adapter import get_db_and_model, query_catalog
    from grc_agent.adapter.rag import _FRESHNESS_CACHE

    tmp_vectors = tmp_path / "vectors"
    tmp_vectors.mkdir()
    monkeypatch.setenv("GRC_AGENT_VECTORS_DIR", str(tmp_vectors))
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    save_settings("ollama_local", "qwen3.6:35b-a3b-q4_K_M")
    db_path, model = get_db_and_model("catalog")

    def fail_embed(text, model):  # noqa: ARG001
        raise RuntimeError("backend down")

    # Build a real lexical-only DB (embedding fails during ingest too — the
    # cold-start-with-no-backend case).
    monkeypatch.setattr(ingest_mod, "embed_document", fail_embed)
    ingest_mod.ingest_catalog(db_path, model)

    import grc_agent.adapter.rag as rag_mod

    def fail_embed_query(q):  # noqa: ARG001
        raise RuntimeError("backend down")

    monkeypatch.setattr(rag_mod, "embed_query", fail_embed_query)

    try:
        res = query_catalog("low pass filter")
        assert res["ok"] is True
        assert res["search_mode"] == "lexical"
        assert "fallback" in res.get("message", "").lower()
        assert res["results"]
        assert any("low_pass_filter" in r["block_id"] for r in res["results"])
    finally:
        _FRESHNESS_CACHE.pop("catalog", None)


def test_query_catalog_lexical_message_present_even_when_embed_succeeds(tmp_path, monkeypatch):
    """Regression: a DB that's lexical-only (built during a past embedding
    outage) must still explain itself via "message" even when the CURRENT
    embed_query call succeeds — previously the message was only attached
    when search_mode == "lexical" AND embed_error was set, silently omitting
    the explanation in exactly this case (no vector index exists, but the
    embedding backend has since recovered), breaking AGENTS.md's "no silent
    transformation" contract."""
    import grc_agent.ingest as ingest_mod
    from grc_agent.adapter import get_db_and_model, query_catalog
    from grc_agent.adapter.rag import _FRESHNESS_CACHE

    tmp_vectors = tmp_path / "vectors"
    tmp_vectors.mkdir()
    monkeypatch.setenv("GRC_AGENT_VECTORS_DIR", str(tmp_vectors))
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    save_settings("ollama_local", "qwen3.6:35b-a3b-q4_K_M")
    db_path, model = get_db_and_model("catalog")

    def fail_embed(text, model):  # noqa: ARG001
        raise RuntimeError("backend down")

    # Build lexical-only (embeddings failed at build time — no catalog_idx table).
    monkeypatch.setattr(ingest_mod, "embed_document", fail_embed)
    ingest_mod.ingest_catalog(db_path, model)

    # Simulate the embedding backend having recovered since: embed_query now succeeds.
    import grc_agent.adapter.rag as rag_mod

    monkeypatch.setattr(rag_mod, "embed_query", lambda q, domain="catalog": [0.1, 0.2, 0.3])  # noqa: ARG005

    try:
        res = query_catalog("low pass filter")
        assert res["ok"] is True
        assert res["search_mode"] == "lexical"
        assert "message" in res, (
            "a lexical result must always explain itself, even when the "
            "current embed call succeeded but no vector index exists yet"
        )
        assert "no vector index" in res["message"].lower()
    finally:
        _FRESHNESS_CACHE.pop("catalog", None)


def test_query_docs_falls_back_to_lexical_when_embedding_unreachable(tmp_path, monkeypatch):
    """Same fallback behavior as query_catalog, exercised on the docs domain
    (different table shape: path/heading/payload instead of block_id/payload)."""
    import grc_agent.ingest as ingest_mod
    from grc_agent.adapter import get_db_and_model, query_docs
    from grc_agent.adapter.rag import _FRESHNESS_CACHE

    tmp_vectors = tmp_path / "vectors"
    tmp_vectors.mkdir()
    monkeypatch.setenv("GRC_AGENT_VECTORS_DIR", str(tmp_vectors))
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    save_settings("ollama_local", "qwen3.6:35b-a3b-q4_K_M")
    db_path, model = get_db_and_model("docs")
    docs_calls = []

    def fail_embed(text, model):  # noqa: ARG001
        docs_calls.append(text)
        raise RuntimeError("backend down")

    monkeypatch.setattr(ingest_mod, "embed_document", fail_embed)
    ingest_mod.ingest_docs(db_path, model)
    assert len(docs_calls) == 1, (
        "ingest_docs backend probe failure must avoid re-calling embed_document per chunk"
    )

    import grc_agent.adapter.rag as rag_mod

    def fail_embed_query(q):  # noqa: ARG001
        raise RuntimeError("backend down")

    monkeypatch.setattr(rag_mod, "embed_query", fail_embed_query)

    try:
        res = query_docs("what is a stream tag")
        assert res["ok"] is True
        assert res["search_mode"] == "lexical"
        assert "fallback" in res.get("message", "").lower()
        assert "tag" in res["answer"].lower()
    finally:
        _FRESHNESS_CACHE.pop("docs", None)


def test_ensure_db_built_rebuilds_when_fts_table_missing(tmp_path, monkeypatch):
    """Migration path: a DB built before the lexical-fallback feature existed
    (vec0 index + _db_meta, no FTS5 table) must be detected as stale and
    rebuilt — not silently left without lexical fallback forever."""
    import sqlite3

    import sqlite_vec

    import grc_agent.ingest as ingest_mod
    from grc_agent.adapter import _corpus_version, _ensure_db_built, get_db_and_model

    tmp_vectors = tmp_path / "vectors"
    tmp_vectors.mkdir()
    monkeypatch.setenv("GRC_AGENT_VECTORS_DIR", str(tmp_vectors))
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    save_settings("ollama_local", "qwen3.6:35b-a3b-q4_K_M")
    db_path, model = get_db_and_model("catalog")

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.execute(
        "CREATE TABLE catalog_chunks(rowid INTEGER PRIMARY KEY, block_id TEXT, payload TEXT);"
    )
    conn.execute("CREATE VIRTUAL TABLE catalog_idx USING vec0(embedding float[3]);")
    conn.execute("CREATE TABLE _db_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO _db_meta (key, value) VALUES ('embedding_model', ?)", (model,))
    conn.execute(
        "INSERT INTO _db_meta (key, value) VALUES ('corpus_version', ?)",
        (_corpus_version("catalog"),),
    )
    conn.commit()
    conn.close()
    # Deliberately no catalog_fts table — the pre-lexical-fallback DB shape.

    called = {"n": 0}

    def mock_ingest(db_path, model, on_progress=None):  # noqa: ARG001
        called["n"] += 1
        return 0

    monkeypatch.setattr(ingest_mod, "ingest_catalog", mock_ingest)

    from grc_agent.adapter.rag import _FRESHNESS_CACHE

    try:
        _ensure_db_built("catalog", db_path, model)
        assert called["n"] == 1, "a DB missing the FTS5 table must trigger a rebuild"
    finally:
        _FRESHNESS_CACHE.pop("catalog", None)


def test_ollama_cloud_model_builds_and_runs():
    """Build an OllamaModel against Ollama Cloud (https://ollama.com/v1) with
    the saved API key and run a real chat turn. This is a non-trivial,
    non-mocked integration test that exercises the exact same code path
    web._build_model() uses for the ollama_cloud provider."""
    import os

    from dotenv import load_dotenv
    from pydantic_ai import Agent
    from pydantic_ai.models.ollama import OllamaModel
    from pydantic_ai.providers.ollama import OllamaProvider

    from grc_agent.settings import env_path

    load_dotenv(env_path())
    api_key = os.environ.get("OLLAMA_CLOUD_API_KEY", "")
    if not api_key:
        pytest.skip("OLLAMA_CLOUD_API_KEY not set — cannot test Ollama Cloud")

    # Build the model exactly as web._build_model() does for ollama_cloud
    model = OllamaModel(
        "deepseek-v4-flash:cloud",
        provider=OllamaProvider(
            base_url="https://ollama.com/v1",
            api_key=api_key,
        ),
    )
    assert isinstance(model, OllamaModel)
    assert model.model_name == "deepseek-v4-flash:cloud"

    # Run a real agent turn against Ollama Cloud
    import asyncio

    async def run_turn():
        agent = Agent(
            model=model,
            output_type=str,
            instructions="You are a terse assistant. Reply in one short sentence.",
        )
        result = await agent.run("Reply with exactly: OLLAMA_CLOUD_OK")
        return result.output.strip()

    reply = asyncio.run(run_turn())
    assert "OLLAMA_CLOUD_OK" in reply, f"Expected OLLAMA_CLOUD_OK, got: {reply}"


def test_grc_tools_includes_generate_python():
    # Structural check only (no LLM, no gnuradio execution) — confirms the
    # tool is actually wired into the agent's tool list, not just defined.
    names = {tool.name for tool in grc_tools()}
    assert names == {
        "inspect_graph",
        "query_knowledge",
        "generate_python",
        "change_graph",
        "get_run_log",
        "save_block",
    }


def test_system_prompt_names_every_real_tool():
    """The prompt's own explicit allowed-tools sentence (prompts.py) must
    name every tool grc_tools() actually registers, plus the two
    capability-backed tools (web_search/web_fetch, native or fallback,
    never registered via grc_tools()). No existing test in this repo
    checked prompt CONTENT before — this is deliberately a first precedent
    (following test_grc_tools_includes_generate_python's exact-set-style
    check) so a new/removed/renamed tool can never silently drift out of
    sync with what the model is told it's allowed to call."""
    from grc_agent.prompts import build_system_prompt

    prompt = build_system_prompt()
    real_tool_names = {tool.name for tool in grc_tools()}
    capability_tool_names = {"web_search", "web_fetch"}

    for name in real_tool_names | capability_tool_names:
        assert name in prompt, f"{name!r} is a real tool but missing from the system prompt"


def test_build_agent_from_cfg_produces_correct_model_type_per_provider(tmp_path, monkeypatch):
    """Regression: build_agent_from_cfg must produce a model whose type
    matches the saved provider — OllamaModel for ollama/ollama_cloud,
    OpenRouterModel for openrouter. No LLM call is made (the model is built
    but never .run()); this just locks the provider -> model-type mapping
    that the live-swap path relies on. Catches the original "swapped to
    openrouter but the backend still kept calling ollama cloud" class of
    bug at the construction layer."""
    env = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env))

    from pydantic_ai.models.ollama import OllamaModel
    from pydantic_ai.models.openai import OpenAIChatModel

    from grc_agent.agent_factory import build_agent_from_cfg

    # ollama (local default)
    save_settings("ollama_local", "qwen3.6:35b-a3b-q4_K_M")
    agent_local, _ = build_agent_from_cfg(load_settings())
    assert isinstance(agent_local.model, OllamaModel), (
        f"local ollama cfg must produce OllamaModel, got {type(agent_local.model).__name__}"
    )

    # ollama (remote/cloud)
    save_settings("ollama_cloud", "deepseek-v4-flash:cloud")
    upsert_env_key("OLLAMA_API_KEY", "dummy-key-for-build-test")
    agent_cloud, _ = build_agent_from_cfg(load_settings())
    assert isinstance(agent_cloud.model, OllamaModel), (
        f"ollama cloud cfg must produce OllamaModel, got {type(agent_cloud.model).__name__}"
    )
    assert "ollama.com" in str(agent_cloud.model._provider.base_url), (
        f"ollama cloud base_url must be ollama.com, got {agent_cloud.model._provider.base_url}"
    )

    # openai_compatible (pointing at OpenRouter endpoint)
    save_settings(
        "openai_compatible",
        "openai/gpt-4o-mini",
        openai_compatible_base_url="https://openrouter.ai/api/v1",
    )
    upsert_env_key("OPENAI_COMPATIBLE_API_KEY", "sk-or-dummy-key-for-build-test")
    agent_or, _ = build_agent_from_cfg(load_settings())
    assert isinstance(agent_or.model, OpenAIChatModel), (
        f"openai_compatible cfg must produce OpenAIChatModel, got {type(agent_or.model).__name__}"
    )
    assert "openrouter.ai" in str(agent_or.model._provider.base_url), (
        f"openrouter base_url must be openrouter.ai, got {agent_or.model._provider.base_url}"
    )


def test_live_swap_rebuilds_agent_with_new_provider(tmp_path, monkeypatch):
    """Regression for the reported bug: changing provider via save_settings +
    rebuild must produce an Agent whose model actually points at the NEW
    provider — not the one the process booted with. Live OpenRouter call
    validates end-to-end (the swap was applied AND the new backend is
    actually reachable). Skipped without OPENROUTER_API_KEY."""
    import asyncio

    from dotenv import load_dotenv
    from pydantic_ai.models.ollama import OllamaModel
    from pydantic_ai.models.openai import OpenAIChatModel

    # Load the repo .env first so OPENROUTER_API_KEY is visible when set
    # there (matches the existing Ollama Cloud live-test pattern). The
    # monkeypatched GRC_AGENT_ENV below redirects only the grc_agent
    # settings module's .env reads — os.environ is independent and still
    # sees this loaded key.
    load_dotenv(env_path())
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        pytest.skip("OPENROUTER_API_KEY not set — cannot validate live swap end-to-end")

    env = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env))

    from grc_agent.agent_factory import build_agent_from_cfg

    # 1. Boot with ollama cfg + a dummy key. We never send a real
    #    request on this agent, so the dummy key is fine — it just exercises
    #    the build path and gives us a baseline agent to "swap away from".
    save_settings("ollama_cloud", "deepseek-v4-flash:cloud")
    upsert_env_key("OLLAMA_API_KEY", "dummy-boot-key-not-used")
    agent1, _ = build_agent_from_cfg(load_settings())
    assert isinstance(agent1.model, OllamaModel)
    assert "ollama.com" in str(agent1.model._provider.base_url)

    # 2. Simulate the Settings dialog's Save path: write the new provider +
    #    real key to .env, then rebuild (exactly what
    #    ChatSidebar._rebuild_agent invokes after a successful Save).
    save_settings(
        "openai_compatible",
        "openai/gpt-4o-mini",
        openai_compatible_base_url="https://openrouter.ai/api/v1",
    )
    upsert_env_key("OPENAI_COMPATIBLE_API_KEY", api_key)
    agent2, _ = build_agent_from_cfg(load_settings())

    # 3. The new agent's model must actually be the new provider's type and
    #    point at the new base_url. This is the assertion that would have
    #    failed under the old restart-gated design if you forgot to restart
    #    (the agent would silently still be the old OllamaModel-on-ollama.com
    #    instance).
    assert agent2 is not agent1, "live-swap must build a NEW agent, not return the cached one"
    assert isinstance(agent2.model, OpenAIChatModel), (
        f"post-swap model must be OpenAIChatModel, got {type(agent2.model).__name__}"
    )
    assert "openrouter.ai" in str(agent2.model._provider.base_url)

    # 4. End-to-end: the new agent actually reaches OpenRouter and gets a
    #    coherent reply. A simple no-tools prompt; output_type=str so the
    #    agent doesn't need a flowgraph deps for its tools.
    async def _run():
        # Build a tiny no-tools agent that reuses agent2's model — agent2
        # itself has grc_tools wired in, which would need a real flowgraph.
        from pydantic_ai import Agent

        mini = Agent(
            agent2.model,
            output_type=str,
        )
        res = await mini.run("reply with the single word PONG")
        assert "PONG" in res.output.upper(), f"unexpected reply: {res.output!r}"

    asyncio.run(_run())


def test_probe_backend_returns_none_on_success_and_error_on_failure():
    """probe_backend must return (None, None) on a reachable endpoint and a
    descriptive reachability error string on any failure."""
    from grc_agent.agent_factory import probe_backend

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if api_key:
        # Real success path — exercises the actual endpoint.
        err, warn = probe_backend("openrouter", api_key, "", "", timeout=10.0)
        assert err is None, f"expected None for a valid OpenRouter key, got: {err!r}"

    # Deterministic failure: missing key for OpenRouter must return a non-empty error string.
    err, _w = probe_backend("openrouter", "", "", "", timeout=10.0)
    assert isinstance(err, str) and err, "missing openrouter key must produce a non-empty error"

    # Deterministic failure: missing key for Ollama Cloud must return a non-empty error string.
    err, _w = probe_backend("ollama_cloud", "", "", "", timeout=10.0)
    assert isinstance(err, str) and err, "missing ollama cloud key must produce a non-empty error"


# ---------------------------------------------------------------------------
# Embeddings backend selection (independent of the chat provider)
# ---------------------------------------------------------------------------


def test_embed_backend_is_independent_of_chat_provider(tmp_path, monkeypatch):
    """The embeddings backend must be selectable on its own.

    A chat endpoint that speaks the OpenAI API need not implement
    /v1/embeddings — llama-server started without `--embeddings` answers 501 —
    so pinning embeddings to the chat provider silently degrades the knowledge
    base to lexical search with no way to fix it.
    """
    from grc_agent.settings import resolve_embed_backend

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    # "auto" (the default) keeps the historical behaviour: follow the chat provider.
    save_settings("openai_compatible", "some/model")
    cfg = load_settings()
    assert cfg["embed_backend"] == "auto"
    assert resolve_embed_backend(cfg) == "openai_compatible"

    save_settings("ollama_local", "qwen3.6:35b-a3b-q4_K_M")
    assert resolve_embed_backend(load_settings()) == "ollama"

    # Pinned explicitly, the chat provider no longer has any say.
    save_settings("openai_compatible", "some/model", embed_backend="llamacpp")
    cfg = load_settings()
    assert resolve_embed_backend(cfg) == "llamacpp"
    assert cfg["provider"] == "openai_compatible"

    db_path, model = get_db_and_model("catalog")
    assert db_path.endswith("catalog_llamacpp.db"), "each backend needs its own index"
    assert "embeddinggemma" in model.lower()

    with pytest.raises(ValueError):
        save_settings("ollama_local", "m", embed_backend="not-a-backend")


def test_gemma_task_prefix_follows_the_model_not_the_provider():
    """Regression: the EmbeddingGemma task prefix used to be gated on
    `provider != "openrouter"`, but load_settings() normalizes "openrouter" to
    "openai_compatible" and can never return it — so the condition was always
    true and the Gemma-specific prefix was prepended for every backend,
    including endpoints serving non-Gemma models, corrupting their embeddings.

    The prefix is correct only for EmbeddingGemma, so that is what it keys on.
    """
    from grc_agent.adapter.rag import _uses_gemma_prefix

    assert _uses_gemma_prefix("embeddinggemma:latest")
    assert _uses_gemma_prefix("llamacpp/embeddinggemma-300m-qat-Q8_0")
    assert not _uses_gemma_prefix("perplexity/pplx-embed-v1-0.6b")
    assert not _uses_gemma_prefix("text-embedding-3-small")
    assert not _uses_gemma_prefix(None)


def test_embed_query_and_document_agree_on_the_prefix():
    """Ingest and query must never disagree: a document embedded with the task
    prefix and a query embedded without it land in different regions of the
    space, which silently degrades every ranking rather than failing."""
    from grc_agent.adapter import rag as rag_mod

    seen: list[str] = []
    orig = rag_mod._embed
    try:
        rag_mod._embed = lambda _model, body: seen.append(body) or [0.1, 0.2]
        for model in ("embeddinggemma:latest", "text-embedding-3-small"):
            seen.clear()
            rag_mod.embed_document("hello", model)
            doc_prefixed = seen[0] != "hello"
            seen.clear()
            query_body = (
                rag_mod._QUERY_PREFIX + "hello" if rag_mod._uses_gemma_prefix(model) else "hello"
            )
            assert doc_prefixed == (query_body != "hello"), f"prefix disagreement for {model}"
    finally:
        rag_mod._embed = orig


def test_llamacpp_runtime_paths_are_xdg_and_overridable(tmp_path, monkeypatch):
    """Several hundred MB of binaries and weights must not land inside the
    installed package."""
    from grc_agent import embed_runtime

    monkeypatch.delenv("GRC_AGENT_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert embed_runtime.data_dir() == tmp_path / "xdg" / "grc-agent"

    monkeypatch.setenv("GRC_AGENT_RUNTIME_DIR", str(tmp_path / "custom"))
    assert embed_runtime.data_dir() == tmp_path / "custom"
    assert embed_runtime.bin_dir().parent == embed_runtime.data_dir()
    assert embed_runtime.model_path().name == embed_runtime.MODEL["file"]


def test_runtime_plan_refuses_platforms_it_cannot_run(monkeypatch):
    """Deciding before downloading is the point: the failure being avoided is
    a successful download of a binary that cannot start."""
    from grc_agent import embed_runtime

    assert embed_runtime.runtime_plan(("Linux", "riscv64"))["kind"] == "none"

    monkeypatch.setattr(embed_runtime, "is_musl", lambda: True)
    plan = embed_runtime.runtime_plan(("Linux", "x86_64"))
    assert plan["kind"] == "none" and "musl" in plan["reason"]

    monkeypatch.setattr(embed_runtime, "is_musl", lambda: False)
    monkeypatch.setattr(embed_runtime, "glibc_version", lambda: (2, 17))
    plan = embed_runtime.runtime_plan(("Linux", "x86_64"))
    assert plan["kind"] == "none" and "glibc" in plan["reason"]

    monkeypatch.setattr(embed_runtime, "glibc_version", lambda: (2, 39))
    plan = embed_runtime.runtime_plan(("Linux", "x86_64"))
    assert plan["kind"] == "upstream" and plan["url"].endswith("ubuntu-x64.tar.gz")


def test_download_verifies_before_it_renames(tmp_path, monkeypatch):
    """A corrupted transfer must never be left at the destination path, where
    a later run would treat it as a complete install."""
    from grc_agent import embed_runtime

    class _Fake:
        headers = {"content-length": "5"}

        def read(self, _n):
            chunk, self._done = (b"hello" if not getattr(self, "_done", False) else b""), True
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(embed_runtime, "_open", lambda *_a, **_k: _Fake())
    dest = tmp_path / "artifact.bin"

    with pytest.raises(embed_runtime.FetchError, match="checksum mismatch"):
        embed_runtime.download("http://x/y", dest, sha256="00" * 32)
    assert not dest.exists(), "a failed download must not appear at the destination"
    assert not dest.with_name(dest.name + ".part").exists(), "partial file must be cleaned up"

    import hashlib

    good = hashlib.sha256(b"hello").hexdigest()
    embed_runtime.download("http://x/y", dest, sha256=good)
    assert dest.read_bytes() == b"hello"


def test_tar_extraction_refuses_path_traversal(tmp_path):
    """The archive is fetched over the network; a member escaping the target
    directory must be a hard failure, not a surprising write."""
    import io
    import tarfile

    from grc_agent import embed_runtime

    archive = tmp_path / "evil.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        data = b"pwned"
        info = tarfile.TarInfo("../escaped.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    with pytest.raises(embed_runtime.FetchError, match="escapes the target"):
        embed_runtime.extract_runtime(archive, tmp_path / "unpacked")
    assert not (tmp_path / "escaped.txt").exists()


def test_partial_embedding_failure_yields_no_vector_index(tmp_path, monkeypatch):
    """A vector index must cover the whole corpus or not exist.

    Regression: one mid-build embed failure used to disable embedding for the
    remainder of the run while KEEPING what had already been collected, so the
    vec0 table was created over a fraction of the corpus. Queries then reported
    search_mode "vector" with silently missing recall, and nothing could detect
    it — `_db_meta` records only the model and corpus version, both of which
    still match. Observed live: a docs index with 4 vector rows against 718
    chunks, which ranked an AGC page top for "what is a stream tag".
    """
    import sqlite3

    import sqlite_vec

    import grc_agent.ingest as ingest_mod
    from grc_agent.adapter import get_db_and_model

    monkeypatch.setenv("GRC_AGENT_VECTORS_DIR", str(tmp_path / "vectors"))
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    save_settings("ollama_local", "qwen3.6:35b-a3b-q4_K_M")
    db_path, model = get_db_and_model("catalog")

    calls = {"n": 0}

    def flaky_embed(text, model):  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] > 5:  # the probe plus a handful of blocks succeed first
            raise RuntimeError("input too large to process")
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(ingest_mod, "embed_document", flaky_embed)
    ingest_mod.ingest_catalog(db_path, model)

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        chunks = conn.execute("SELECT count(*) FROM catalog_chunks").fetchone()[0]
        assert chunks > 0, "the lexical index must still be built in full"
        assert "catalog_idx" not in tables, (
            "a partially-embedded corpus must produce NO vector index — a partial "
            "one reports itself as healthy while silently missing recall"
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ChatGPT Plus/Pro (Codex) OAuth provider
# ---------------------------------------------------------------------------


def _fake_jwt(account_id: str | None) -> str:
    import base64
    import json

    claims = {"https://api.openai.com/auth": {}}
    if account_id is not None:
        claims["https://api.openai.com/auth"]["chatgpt_account_id"] = account_id
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{body}.signature"


def test_codex_is_a_real_third_provider(tmp_path, monkeypatch):
    """Regression: load_settings() reverts unknown providers to the default, so
    a saved GRC_PROVIDER=openai_codex would silently vanish on next load unless
    the normalization knows about it."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    save_settings("openai_codex", "gpt-5.1-codex")
    cfg = load_settings()
    assert cfg["provider"] == "openai_codex"
    assert cfg["model"] == "gpt-5.1-codex", "res['model'] needs a _PROVIDER_MODEL_KEY entry"

    # Switching away and back must not lose either provider's model.
    save_settings("ollama_local", "qwen3.6:35b-a3b-q4_K_M")
    cfg = load_settings()
    assert cfg["openai_codex_model"] == "gpt-5.1-codex"


def test_codex_auto_embeddings_do_not_follow_a_backend_without_embeddings(tmp_path, monkeypatch):
    """The Codex transport exposes no /v1/embeddings, so "auto" must not
    resolve to it — every embed call would fail."""
    from grc_agent.settings import resolve_embed_backend

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    save_settings("openai_codex", "gpt-5.1-codex", embed_backend="auto")
    assert resolve_embed_backend(load_settings()) == "ollama"

    save_settings("openai_codex", "gpt-5.1-codex", embed_backend="llamacpp")
    assert resolve_embed_backend(load_settings()) == "llamacpp"


def test_codex_badge_resolves_from_its_base_url():
    """Regression: resolve_provider_from_base_url() maps any non-Ollama URL to
    openai_compatible, and set_agent() then reports a healthy connection as
    'Fallback default (configured provider unreachable)'."""
    from grc_agent.providers.openai_codex.model import BASE_URL
    from grc_agent.ui.providers import (
        PROVIDER_API_KEY,
        PROVIDER_BADGE_LABEL,
        PROVIDER_MODEL_KEY,
        PROVIDER_ORDER,
        resolve_provider_from_base_url,
    )

    assert resolve_provider_from_base_url(BASE_URL) == "openai_codex"
    # Every parallel catalog dict must carry an entry, or the dialog KeyErrors.
    for table in (PROVIDER_MODEL_KEY, PROVIDER_API_KEY, PROVIDER_BADGE_LABEL):
        assert "openai_codex" in table
    assert "openai_codex" in PROVIDER_ORDER
    assert PROVIDER_API_KEY["openai_codex"] is None, "OAuth tokens must never be written to .env"


def test_codex_authorize_url_matches_the_registered_client(tmp_path, monkeypatch):
    """The redirect URI is registered against this client id — an ephemeral
    port produces a redirect_uri mismatch rather than a working login."""
    import urllib.parse

    from grc_agent.providers.openai_codex import auth

    monkeypatch.setenv("GRC_AGENT_CODEX_AUTH", str(tmp_path / "auth.json"))
    flow = auth.start_login()
    parsed = urllib.parse.urlparse(flow.url)
    params = dict(urllib.parse.parse_qsl(parsed.query))

    assert parsed.netloc == "auth.openai.com"
    assert params["redirect_uri"] == "http://localhost:1455/auth/callback"
    assert params["code_challenge_method"] == "S256"
    assert params["code_challenge"] != flow.verifier, (
        "the challenge must be the hash, not the verifier"
    )
    assert params["state"] == flow.state
    assert "offline_access" in params["scope"], "a refresh token requires offline_access"


def test_codex_redirect_parsing_accepts_what_users_actually_paste(tmp_path, monkeypatch):
    from grc_agent.providers.openai_codex import auth
    from grc_agent.providers.openai_codex.credentials import AuthenticationError

    monkeypatch.setenv("GRC_AGENT_CODEX_AUTH", str(tmp_path / "auth.json"))
    state = "abc123state"
    assert (
        auth.parse_redirect(f"http://localhost:1455/auth/callback?code=C1&state={state}", state)
        == "C1"
    )
    assert auth.parse_redirect(f"code=C2&state={state}", state) == "C2"
    assert auth.parse_redirect("  C3  ", state) == "C3"

    with pytest.raises(AuthenticationError, match="State mismatch"):
        auth.parse_redirect("http://x/?code=C&state=wrong", state)
    with pytest.raises(AuthenticationError, match="Authorization failed"):
        auth.parse_redirect("http://x/?error=access_denied", state)


def test_codex_credentials_are_private_and_never_in_the_repo(tmp_path, monkeypatch):
    """These are rotating OAuth tokens, not a preference: 0600, and never in
    `.env` (which is world-readable and sits in the repo root for a dev
    checkout)."""
    import stat

    from grc_agent.providers.openai_codex import credentials as creds

    auth_file = tmp_path / "nested" / "auth.json"
    monkeypatch.setenv("GRC_AGENT_CODEX_AUTH", str(auth_file))
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    assert creds.load() is None and not creds.is_signed_in()

    cred = creds.credential_from_token_response(
        {"access_token": _fake_jwt("acct-42"), "refresh_token": "r1", "expires_in": 3600}
    )
    creds.save(cred)

    assert stat.S_IMODE(auth_file.stat().st_mode) == 0o600
    assert creds.load().account_id == "acct-42"
    assert creds.is_signed_in()

    env_text = (tmp_path / ".env").read_text() if (tmp_path / ".env").exists() else ""
    assert "r1" not in env_text and cred.access not in env_text

    creds.clear()
    assert not creds.is_signed_in()


def test_codex_rejects_a_token_without_a_codex_entitlement(tmp_path, monkeypatch):
    """No chatgpt_account_id means the account cannot use Codex. Failing here
    beats sending every request without the header the endpoint requires."""
    from grc_agent.providers.openai_codex import credentials as creds
    from grc_agent.providers.openai_codex.credentials import AuthenticationError

    monkeypatch.setenv("GRC_AGENT_CODEX_AUTH", str(tmp_path / "auth.json"))
    with pytest.raises(AuthenticationError, match="entitlement"):
        creds.credential_from_token_response(
            {"access_token": _fake_jwt(None), "refresh_token": "r", "expires_in": 3600}
        )


def test_codex_refreshes_only_when_expiry_is_near(tmp_path, monkeypatch):
    """A rotated refresh token invalidates the previous one, so refreshing
    twice concurrently would log the user out. Fresh tokens must not refresh
    at all; expiring ones must refresh exactly once under contention."""
    import asyncio

    from grc_agent.providers.openai_codex import credentials as creds

    monkeypatch.setenv("GRC_AGENT_CODEX_AUTH", str(tmp_path / "auth.json"))
    calls = {"n": 0}

    async def fake_refresh(cred):  # noqa: ARG001
        calls["n"] += 1
        await asyncio.sleep(0.01)
        return creds.Credential(
            access=_fake_jwt("acct-42"),
            refresh=f"r{calls['n']}",
            expires=9e12,
            account_id="acct-42",
        )

    monkeypatch.setattr(creds, "_refresh", fake_refresh)

    creds.save(
        creds.Credential(
            access=_fake_jwt("acct-42"), refresh="r0", expires=9e12, account_id="acct-42"
        )
    )
    assert asyncio.run(creds.get_valid()).refresh == "r0"
    assert calls["n"] == 0, "a token far from expiry must not be refreshed"

    creds.save(
        creds.Credential(access=_fake_jwt("acct-42"), refresh="r0", expires=0, account_id="acct-42")
    )

    async def race():
        return await asyncio.gather(*(creds.get_valid() for _ in range(4)))

    results = asyncio.run(race())
    assert calls["n"] == 1, f"expected exactly one refresh under contention, got {calls['n']}"
    assert {r.refresh for r in results} == {"r1"}


def test_codex_model_targets_the_codex_responses_endpoint(tmp_path, monkeypatch):
    """The OpenAI SDK posts to the literal path /responses, so the base URL
    must be .../backend-api/codex — and building the model must not require
    credentials, so an unauthenticated config still starts the app."""
    from grc_agent.agent_factory import _build_model, _retrying_http_client
    from grc_agent.providers.openai_codex.model import CodexResponsesModel

    monkeypatch.setenv("GRC_AGENT_CODEX_AUTH", str(tmp_path / "absent.json"))
    model = _build_model(
        {"provider": "openai_codex", "model": "gpt-5.1-codex"}, _retrying_http_client()
    )
    assert isinstance(model, CodexResponsesModel)
    assert str(model._provider.base_url).rstrip("/") == "https://chatgpt.com/backend-api/codex"


def test_codex_preflight_reports_signed_out_without_a_network_call(tmp_path, monkeypatch):
    """There is no /models endpoint on the Codex transport; the equivalent
    check is whether a usable credential exists."""
    import httpx

    from grc_agent.agent_factory import probe_backend
    from grc_agent.providers.openai_codex import credentials as creds

    monkeypatch.setenv("GRC_AGENT_CODEX_AUTH", str(tmp_path / "auth.json"))

    def explode(*a, **k):  # noqa: ARG001
        raise AssertionError("probe must not hit the network for openai_codex")

    monkeypatch.setattr(httpx, "get", explode)

    err, warn = probe_backend("openai_codex", "", "", "")
    assert err and "Not signed in" in err

    creds.save(creds.Credential(access=_fake_jwt("a"), refresh="r", expires=9e12, account_id="a"))
    assert probe_backend("openai_codex", "", "", "") == (None, None)


def test_codex_callback_listens_on_both_loopback_families():
    """Regression: the redirect URI says `localhost`, and the browser decides
    how to resolve it. On a dual-stack box `localhost` resolves to ::1 first,
    so binding only 127.0.0.1 got the redirect refused — the browser landed on
    the authorization server's own "return to your app" page and the sign-in
    never completed, with no code anywhere for the user to paste instead.
    """
    import asyncio
    import socket

    from grc_agent.providers.openai_codex import auth

    async def deliver(family: str, host: str) -> str:
        flow = auth.start_login()
        waiter = asyncio.ensure_future(auth.wait_for_callback(flow, timeout=10))
        await asyncio.sleep(0.3)
        try:
            reader, writer = await asyncio.open_connection(host, auth.CALLBACK_PORT)
            writer.write(
                f"GET {auth.CALLBACK_PATH}?code={family}CODE&state={flow.state} "
                f"HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
            )
            await writer.drain()
            await reader.read(64)
            writer.close()
            return await asyncio.wait_for(waiter, 5)
        finally:
            if not waiter.done():
                waiter.cancel()

    for family, host in (("IPV4", "127.0.0.1"), ("IPV6", "::1")):
        if family == "IPV6" and not socket.has_ipv6:
            continue
        assert asyncio.run(deliver(family, host)) == f"{family}CODE", (
            f"the callback must be reachable over {family}"
        )


def test_codex_login_dialog_completes_from_the_browser_callback(tmp_path, monkeypatch):
    """End-to-end through the real dialog: a callback must save a credential.

    Regression: `_finish` ran *inside* the callback-waiter task and called
    `_cancel_task()`, which cancelled that same task. The CancelledError landed
    on the await inside `_finish`, and since it derives from BaseException the
    `except Exception` there never saw it — so the token exchange was killed
    mid-flight, nothing was saved, and no error was displayed. The dialog just
    sat waiting for a paste, while the browser showed a successful sign-in.

    Unit-testing the pieces missed this entirely; only driving the dialog the
    way the app does reproduces it.
    """
    import asyncio

    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    if not Gtk.init_check([])[0]:
        pytest.skip("no display")

    from grc_agent.providers.openai_codex import auth as auth_mod
    from grc_agent.providers.openai_codex import credentials as creds
    from grc_agent.ui import codex_login_dialog as dlg_mod

    monkeypatch.setenv("GRC_AGENT_CODEX_AUTH", str(tmp_path / "auth.json"))

    async def fake_wait(flow, timeout=300.0):  # noqa: ARG001
        await asyncio.sleep(0.01)
        return "THE_CODE"

    exchanged = {}

    async def fake_exchange(code, verifier, redirect_uri=None):  # noqa: ARG001
        await asyncio.sleep(0.01)  # a real network round trip has an await
        exchanged["code"] = code
        cred = creds.Credential(
            access=_fake_jwt("acct-1"), refresh="r", expires=9e12, account_id="acct-1"
        )
        creds.save(cred)
        return cred

    monkeypatch.setattr(dlg_mod.auth, "wait_for_callback", fake_wait)
    monkeypatch.setattr(dlg_mod.auth, "exchange_code", fake_exchange)
    monkeypatch.setattr(dlg_mod.auth, "start_login", auth_mod.start_login)
    monkeypatch.setattr(dlg_mod.Gio.AppInfo, "launch_default_for_uri", lambda *_a, **_k: True)

    done = {}

    async def drive():
        dialog = dlg_mod.CodexLoginDialog(None, on_done=lambda ok, err: done.update(ok=ok, err=err))
        for _ in range(200):  # up to ~2s
            await asyncio.sleep(0.01)
            if done:
                break
        return dialog

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(drive())
    finally:
        loop.close()

    assert exchanged.get("code") == "THE_CODE", "the callback's code must reach the exchange"
    assert creds.is_signed_in(), "a successful callback must persist a credential"
    assert done.get("ok") is True, "the dialog must report success so Settings can refresh"


def test_ensure_server_starts_exactly_one_llama_server(tmp_path, monkeypatch):
    """Regression: ensure_server() had no lock, and embedding fans out across
    asyncio.to_thread workers (catalog and docs build concurrently, since
    rag.py's _BUILD_LOCKS is per-domain). Every thread saw no live server and
    spawned its own: the first bound the socket, the rest died with "couldn't
    bind HTTP server socket" — and because each had already written its own
    server.token, the survivor answered "unauthorized: Invalid API Key" to
    everything. Observed live: three starts inside one second.
    """
    import concurrent.futures as cf

    from grc_agent import embed_runtime

    monkeypatch.setenv("GRC_AGENT_RUNTIME_DIR", str(tmp_path))
    starts = {"n": 0}
    alive = {"up": False}

    monkeypatch.setattr(embed_runtime, "is_alive", lambda *_a, **_k: alive["up"])
    monkeypatch.setattr(embed_runtime, "is_provisioned", lambda: True)

    def fake_start(_wait):
        starts["n"] += 1
        embed_runtime._write_private(embed_runtime._token_path(), f"token{starts['n']}")
        alive["up"] = True
        return f"token{starts['n']}"

    monkeypatch.setattr(embed_runtime, "_start_server", fake_start)

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        tokens = list(ex.map(lambda _: embed_runtime.ensure_server(), range(8)))

    assert starts["n"] == 1, f"expected one server start under contention, got {starts['n']}"
    assert len(set(tokens)) == 1, "every caller must get the surviving server's token"


def test_model_catalog_lists_what_each_backend_reports(monkeypatch):
    """Typing a model id by hand is how `gpt-5.1-codex` reached an endpoint
    that rejects it. Both response shapes must be understood."""
    import asyncio

    import httpx

    from grc_agent import model_catalog

    class _Resp:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def json(self):
            return self._payload

    # Ollama's /api/tags shape.
    monkeypatch.setattr(
        httpx, "get", lambda *_a, **_k: _Resp({"models": [{"name": "b"}, {"name": "a"}]})
    )
    got = asyncio.run(
        model_catalog.list_models({"provider": "ollama_local"}, base_url="http://x:11434")
    )
    assert got == ["a", "b"], "ollama tags must be listed and sorted"

    # The OpenAI /v1/models shape.
    monkeypatch.setattr(httpx, "get", lambda *_a, **_k: _Resp({"data": [{"id": "z"}, {"id": "y"}]}))
    got = asyncio.run(
        model_catalog.list_models(
            {"provider": "openai_compatible"}, api_key="k", base_url="http://x/v1"
        )
    )
    assert got == ["y", "z"]


def test_codex_model_list_drops_hidden_entries_and_keeps_server_order(monkeypatch):
    """`codex-auto-review` is internal to Codex and is not selectable.

    Server order is preserved because it comes back newest-first, which is
    what a dropdown wants — the `priority` field ranks the *older* models
    higher, so sorting by it buries the newest at the bottom."""
    import asyncio

    import httpx

    from grc_agent.providers.openai_codex import credentials as creds
    from grc_agent.providers.openai_codex import model as codex_model

    payload = {
        "models": [
            {"slug": "codex-auto-review", "visibility": "hide", "priority": 43},
            {"slug": "gpt-5.4", "visibility": "list", "priority": 16},
            {"slug": "gpt-5.4-mini", "visibility": "list", "priority": 23},
        ]
    }

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *_a, **_k):
            return httpx.Response(200, json=payload)

    async def fake_valid():
        return creds.Credential(access="a", refresh="r", expires=9e12, account_id="acct")

    monkeypatch.setattr(codex_model.credentials, "get_valid", fake_valid)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *_a, **_k: _Client())

    assert asyncio.run(codex_model.list_models()) == ["gpt-5.4", "gpt-5.4-mini"]


def test_codex_client_version_does_not_hide_newer_models():
    """Regression: the models endpoint filters on ?client_version= against each
    model's `minimal_client_version`. A pinned 0.104.0 returned 3 models and
    silently hid gpt-5.5 (0.124.0) and the whole gpt-5.6 family (0.144.0) — so
    the dropdown, and the default derived from it, were quietly a generation
    behind. The gate describes Codex CLI features this app does not use.
    """
    from grc_agent.providers.openai_codex.model import CLIENT_VERSION

    parts = [int(p) for p in CLIENT_VERSION.split(".")]
    assert parts >= [1, 0, 0], (
        f"client_version {CLIENT_VERSION} is below 1.0.0 and will hide models "
        "whose minimal_client_version is newer"
    )


def test_codex_requests_reasoning_summaries():
    """Every Codex model reports `default_reasoning_summary: "none"`, so
    without asking for one the reasoning is computed and discarded and the
    per-turn trace stays empty. Confirmed live: with a summary requested the
    endpoint emits response.reasoning_summary_text.delta, which pydantic-ai
    turns into ThinkingParts."""
    from grc_agent.providers.openai_codex.model import CODEX_MODEL_SETTINGS

    assert CODEX_MODEL_SETTINGS.get("openai_reasoning_summary"), (
        "Codex defaults reasoning summaries off; one must be requested"
    )
    # store:true and non-streaming are both rejected outright by Codex.
    assert CODEX_MODEL_SETTINGS["openai_store"] is False


def test_thinking_delta_without_content_does_not_crash_the_turn():
    """Regression: ThinkingPartDelta.content_delta is Optional (unlike
    TextPartDelta's), because a delta may carry only a signature or provider
    metadata update. Codex sends those around its reasoning summary parts, and
    `ctx.think_acc += delta.content_delta` raised "can only concatenate str
    (not NoneType) to str", killing the whole turn.
    """
    from pydantic_ai.messages import PartDeltaEvent, ThinkingPartDelta

    from grc_agent.chat_sidebar import ChatSidebar, _StreamCtx

    # The field really is optional — that is what makes this reachable.
    assert ThinkingPartDelta.__dataclass_fields__["content_delta"].type == "str | None"

    ctx = _StreamCtx.__new__(_StreamCtx)
    ctx.think_acc = "so far"
    ctx.full_raw_text = "so far"

    sidebar = ChatSidebar.__new__(ChatSidebar)
    event = PartDeltaEvent(index=0, delta=ThinkingPartDelta(signature_delta="sig"))
    ChatSidebar._on_part_delta(sidebar, ctx, event)  # must not raise

    assert ctx.think_acc == "so far", "a content-free delta must leave the buffer alone"


def test_token_rate_is_omitted_rather_than_shown_as_zero():
    from grc_agent.chat_sidebar import _tokens_per_second

    assert _tokens_per_second(160, 4000) == 40.0
    # A turn with no output, or no measurable duration, has no rate — showing
    # "0 tok/s" would read as a stalled backend.
    assert _tokens_per_second(0, 4000) is None
    assert _tokens_per_second(160, 0) is None
    assert _tokens_per_second(None, None) is None


def test_is_musl_false_positive_on_glibc_host_with_musl_loader():
    """Regression: the Debian/Ubuntu `musl` package (a rust
    cross-compilation dependency) installs /lib/ld-musl-x86_64.so.1 alongside
    glibc. The loader-only fallback used to read that as Alpine and disable
    the prebuilt runtime ("musl libc (Alpine)") on a perfectly good glibc
    host — a definitive libc_ver() answer must always win."""
    import platform
    from unittest.mock import patch

    from grc_agent import embed_runtime

    # Real loader file present on this test host class; keep the check honest
    # by patching both signals explicitly for each scenario.
    with (
        patch.object(platform, "libc_ver", return_value=("glibc", "2.39")),
        patch.object(embed_runtime.Path, "exists", return_value=True),
    ):
        assert embed_runtime.is_musl() is False, "glibc answer must beat a stray musl loader"

    with (
        patch.object(platform, "libc_ver", return_value=("musl", "1.2.5")),
        patch.object(embed_runtime.Path, "exists", return_value=False),
    ):
        assert embed_runtime.is_musl() is True

    # Inconclusive libc_ver ("" on Alpine) falls back to the loader file.
    with (
        patch.object(platform, "libc_ver", return_value=("", "")),
        patch.object(embed_runtime.Path, "exists", return_value=True),
    ):
        assert embed_runtime.is_musl() is True


def test_ollama_context_length_targets_cloud_url_for_cloud_users(tmp_path, monkeypatch):
    """Regression: the cloud endpoint was keyed on the dead
    `provider == "ollama_cloud"` string, so since the v0.1.5 consolidation a
    cloud user's context-length lookup silently went to localhost:11434. The
    endpoint must come from the resolved ollama_base_url (load_settings
    defaults it to ollama.com when a cloud key is present and no local URL is
    set), with the API key attached — the same source of truth _build_model
    uses."""
    import httpx

    import grc_agent.chat_sidebar as cs

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    (tmp_path / ".env").write_text("OLLAMA_CLOUD_API_KEY=test-cloud-key\n")

    seen: dict = {}

    def fake_post(self, url, **kwargs):  # noqa: ARG001
        seen["url"] = url
        seen["headers"] = kwargs.get("headers") or {}

        class R:
            status_code = 200

            def json(self):
                return {
                    "model_info": {"context_length": 1_048_576},
                    "parameters": "",
                }

        return R()

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    cs._context_length_cache.clear()
    cs._context_negative_cache.clear()
    try:
        out = cs._ollama_context_length("deepseek-v4-flash:cloud")
    finally:
        cs._context_length_cache.clear()
        cs._context_negative_cache.clear()

    assert out == 1_048_576
    assert "ollama.com" in seen["url"], f"cloud user must query ollama.com, got {seen['url']}"
    assert seen["headers"].get("Authorization") == "Bearer test-cloud-key"


def test_run_usage_output_override_uses_run_totals():
    """The context-label tooltip's 'Last turn output' must use the run's
    aggregated totals when a live run is available, and fall back to the
    last-response extraction otherwise."""
    from types import SimpleNamespace

    from pydantic_ai.usage import RunUsage

    from grc_agent.chat_sidebar import _run_usage_output_override

    run = SimpleNamespace(usage=RunUsage(output_tokens=13, details={"reasoning_tokens": 5}))
    assert _run_usage_output_override(run, 9, 0) == (13, 5)
    assert _run_usage_output_override(None, 9, 0) == (9, 0)
    assert _run_usage_output_override(SimpleNamespace(usage=None), 9, 0) == (9, 0)
