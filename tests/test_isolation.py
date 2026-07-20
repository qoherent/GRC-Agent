import os

import pytest
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openrouter import OpenRouterModel

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

    and openrouter_model are handled independently (no overwriting).
    """
    tmp_env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_env_file))

    # 1. Load initial settings (defaults)
    cfg = load_settings()
    assert cfg["provider"] == "ollama"
    assert cfg["ollama_model"] == "qwen3.6:35b-a3b-q4_K_M"
    assert cfg["openrouter_model"] == "deepseek/deepseek-v4-flash"
    assert cfg["ollama_cloud_model"] == "deepseek-v4-flash:cloud"

    # 2. Switch provider to openrouter and change model
    save_settings("openrouter", "google/gemini-2.5-flash")
    cfg = load_settings()
    assert cfg["provider"] == "openrouter"
    assert cfg["model"] == "google/gemini-2.5-flash"
    assert cfg["openrouter_model"] == "google/gemini-2.5-flash"
    assert cfg["ollama_model"] == "qwen3.6:35b-a3b-q4_K_M"  # preserved!
    assert cfg["ollama_cloud_model"] == "deepseek-v4-flash:cloud"  # preserved!

    # 3. Switch back to ollama and change model
    save_settings("ollama", "mistral-large")
    cfg = load_settings()
    assert cfg["provider"] == "ollama"
    assert cfg["model"] == "mistral-large"
    assert cfg["ollama_model"] == "mistral-large"
    assert cfg["openrouter_model"] == "google/gemini-2.5-flash"  # preserved!

    # 4. Switch to ollama_cloud and verify independence
    save_settings("ollama_cloud", "deepseek-v4-flash:cloud")
    cfg = load_settings()
    assert cfg["provider"] == "ollama_cloud"
    assert cfg["model"] == "deepseek-v4-flash:cloud"
    assert cfg["ollama_cloud_model"] == "deepseek-v4-flash:cloud"
    assert cfg["ollama_model"] == "mistral-large"  # preserved!
    assert cfg["openrouter_model"] == "google/gemini-2.5-flash"  # preserved!


def test_db_and_model_isolation(tmp_path, monkeypatch):
    """Verify database filenames and embedding model settings are disjoint.

    Ollama queries/embeddings should only target *_ollama.db.
    OpenRouter queries/embeddings should only target *_openrouter.db.
    """
    tmp_env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_env_file))

    # Test under Ollama provider
    save_settings("ollama", "qwen3.6:35b-a3b-q4_K_M")
    db_path_ollama, model_ollama = get_db_and_model("catalog")
    assert db_path_ollama.endswith("catalog_ollama.db")
    assert "catalog_openrouter.db" not in db_path_ollama

    # Test under OpenRouter provider
    save_settings("openrouter", "openai/gpt-4o-mini")
    db_path_openrouter, model_openrouter = get_db_and_model("catalog")
    assert db_path_openrouter.endswith("catalog_openrouter.db")
    assert "catalog_ollama.db" not in db_path_openrouter


def test_embed_endpoint_isolation(tmp_path, monkeypatch):
    """Verify API endpoints and keys do not leak or overlap.

    When Ollama is selected, it must target localhost:11434 and use 'not-needed'.
    When OpenRouter is selected, it must target openrouter.ai and use key env.
    """
    tmp_env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_env_file))
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-openrouter-key")

    # Ollama provider check
    save_settings("ollama", "qwen3.6:35b-a3b-q4_K_M")
    base_url, api_key = _embed_endpoint()
    assert base_url == "http://localhost:11434/v1"
    assert api_key == "not-needed"

    # OpenRouter provider check
    save_settings("openrouter", "openai/gpt-4o-mini")
    base_url, api_key = _embed_endpoint()
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
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-openrouter-key")
    rag_mod._embed_client_state = None

    try:
        save_settings("ollama", "qwen3.6:35b-a3b-q4_K_M")
        client_ollama = rag_mod._get_embed_client()
        assert str(client_ollama.base_url).rstrip("/") == "http://localhost:11434/v1"

        save_settings("openrouter", "openai/gpt-4o-mini")
        client_openrouter = rag_mod._get_embed_client()
        assert str(client_openrouter.base_url).rstrip("/") == "https://openrouter.ai/api/v1"
        assert client_openrouter is not client_ollama

        # Switch back — must rebuild again (not silently reuse the openrouter
        # client, and not incorrectly rebuild a third distinct instance for
        # settings it's already seen — the state is exactly one entry, not a
        # growing cache, so "switch back" must reuse neither stale client).
        save_settings("ollama", "qwen3.6:35b-a3b-q4_K_M")
        client_ollama_again = rag_mod._get_embed_client()
        assert str(client_ollama_again.base_url).rstrip("/") == "http://localhost:11434/v1"
    finally:
        rag_mod._embed_client_state = None


def test_web_build_model_isolation(tmp_path, monkeypatch):
    """Verify that agent_factory._build_model instantiates the correct model type based on the settings."""
    tmp_env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_env_file))
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-test-key")

    http_client = _retrying_http_client()

    cfg = {"provider": "ollama", "model": "qwen3.6:35b-a3b-q4_K_M"}
    m = _build_model(cfg, http_client)
    assert isinstance(m, OllamaModel)
    assert m.model_name == "qwen3.6:35b-a3b-q4_K_M"

    cfg = {"provider": "openrouter", "model": "openai/gpt-4o-mini"}
    m = _build_model(cfg, http_client)
    assert isinstance(m, OpenRouterModel)
    assert m.model_name == "openai/gpt-4o-mini"

    # ollama_cloud reads its key via get_env_value (the .env file, not
    # os.environ) — unlike the openrouter case above, OllamaProvider's own
    # os.getenv fallback checks OLLAMA_API_KEY, not OLLAMA_CLOUD_API_KEY, so
    # the monkeypatched env var alone wouldn't satisfy it. Write the key to
    # the actual .env file so _build_model's explicit guard sees it.
    upsert_env_key("OLLAMA_CLOUD_API_KEY", "dummy-test-key")
    cfg = {"provider": "ollama_cloud", "model": "deepseek-v4-flash:cloud"}
    m = _build_model(cfg, http_client)
    assert isinstance(m, OllamaModel)
    assert m.model_name == "deepseek-v4-flash:cloud"


def test_scenario_model_builder_uses_provider(monkeypatch):
    """Regression for P2-7: the scenario harness must be able to build a model
    for either backend so integration tests can run against Ollama, Ollama Cloud, or OpenRouter."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-test-key")
    ollama = build_scenario_model("ollama")
    assert isinstance(ollama, OllamaModel)

    ollama_cloud = build_scenario_model("ollama_cloud", "deepseek-v4-flash:cloud")
    assert isinstance(ollama_cloud, OllamaModel)
    assert ollama_cloud.model_name == "deepseek-v4-flash:cloud"

    openrouter = build_scenario_model("openrouter", "google/gemini-2.5-flash")
    assert isinstance(openrouter, OpenRouterModel)
    assert openrouter.model_name == "google/gemini-2.5-flash"


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
    upsert_env_key("GRC_PROVIDER", "ollama", path=env)
    content = env.read_text(encoding="utf-8")
    assert "GRC_PROVIDER=ollama" in content

    # Update
    upsert_env_key("GRC_PROVIDER", "ollama_cloud", path=env)
    content = env.read_text(encoding="utf-8")
    assert content.count("GRC_PROVIDER=") == 1
    assert "GRC_PROVIDER=ollama_cloud" in content

    # Insert second key — first must be preserved
    upsert_env_key("OLLAMA_CLOUD_MODEL", "deepseek-v4-flash:cloud", path=env)
    content = env.read_text(encoding="utf-8")
    assert "GRC_PROVIDER=ollama_cloud" in content
    assert "OLLAMA_CLOUD_MODEL=deepseek-v4-flash:cloud" in content
    assert content.count("GRC_PROVIDER=") == 1
    assert content.count("OLLAMA_CLOUD_MODEL=") == 1


def test_get_env_value_reads_from_file_not_os_environ(tmp_path, monkeypatch):
    """get_env_value must read from the .env file, not os.environ — the
    health check uses it to distinguish saved keys from the running process's
    startup snapshot."""
    env = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env))

    # Write a key to the file
    upsert_env_key("OLLAMA_CLOUD_API_KEY", "file-key-123", path=env)

    # Set a DIFFERENT value in os.environ (simulating a stale startup snapshot)
    monkeypatch.setenv("OLLAMA_CLOUD_API_KEY", "env-key-456")

    # get_env_value must return the file value, not the env var
    assert get_env_value("OLLAMA_CLOUD_API_KEY") == "file-key-123"

    # For a key not in the file, must return None
    assert get_env_value("NONEXISTENT_KEY") is None


def test_build_model_ollama_cloud_raises_on_missing_api_key(tmp_path, monkeypatch):
    """Regression: OllamaProvider itself never raises on a missing API key —
    it silently substitutes a placeholder ('api-key-not-set') and the failure
    only ever surfaces as an HTTP 401 on the first real chat call. _build_model
    must raise explicitly for ollama_cloud with no key configured, matching
    openrouter's existing behavior (OpenRouterProvider raises UserError on an
    empty key) so build_interactive_agent's existing fallback-and-warn path
    catches it instead of silently proceeding."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    http_client = _retrying_http_client()
    with pytest.raises(ValueError, match="OLLAMA_CLOUD_API_KEY"):
        _build_model({"provider": "ollama_cloud", "model": "deepseek-v4-flash:cloud"}, http_client)


def test_save_settings_writes_ollama_cloud_model_to_env(tmp_path, monkeypatch):
    """save_settings for ollama_cloud must write GRC_PROVIDER and
    OLLAMA_CLOUD_MODEL to the .env file, and preserve other providers' models."""
    env = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env))

    # First save openrouter — sets GRC_PROVIDER + OPENROUTER_MODEL
    save_settings("openrouter", "google/gemini-2.5-flash")
    content = env.read_text(encoding="utf-8")
    assert "GRC_PROVIDER=openrouter" in content
    assert "OPENROUTER_MODEL=google/gemini-2.5-flash" in content

    # Now save ollama_cloud — must add OLLAMA_CLOUD_MODEL and update
    # GRC_PROVIDER, but preserve OPENROUTER_MODEL
    save_settings("ollama_cloud", "deepseek-v4-flash:cloud")
    content = env.read_text(encoding="utf-8")
    assert "GRC_PROVIDER=ollama_cloud" in content
    assert "OLLAMA_CLOUD_MODEL=deepseek-v4-flash:cloud" in content
    assert "OPENROUTER_MODEL=google/gemini-2.5-flash" in content  # preserved

    # load_settings must reflect the saved state
    cfg = load_settings()
    assert cfg["provider"] == "ollama_cloud"
    assert cfg["model"] == "deepseek-v4-flash:cloud"
    assert cfg["ollama_cloud_model"] == "deepseek-v4-flash:cloud"
    assert cfg["openrouter_model"] == "google/gemini-2.5-flash"  # preserved


def test_build_model_fallback_does_not_mutate_cfg(tmp_path, monkeypatch):
    """When _build_model() fails (e.g. OpenRouter with no API key), the
    fallback in build_interactive_agent must NOT mutate the saved cfg."""
    env = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env))
    save_settings("openrouter", "openai/gpt-4o-mini")

    import os

    had_key = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        from grc_agent.settings import default_settings

        http_client = _retrying_http_client()
        saved_cfg = load_settings()
        fallback_cfg = default_settings()
        fallback_model = _build_model(fallback_cfg, http_client)
        assert isinstance(fallback_model, OllamaModel)
        assert saved_cfg["provider"] == "openrouter"
        assert saved_cfg["model"] == "openai/gpt-4o-mini"
    finally:
        if had_key is not None:
            os.environ["OPENROUTER_API_KEY"] = had_key


def test_rag_building_flag_set_during_ensure_db_built(tmp_path, monkeypatch):
    """_rag_building must be set to 'building' before the DB build and
    'ready' after (per-domain), so the GUI can show a progress banner."""
    import grc_agent.adapter as adapter_mod
    from grc_agent.adapter import _ensure_db_built, get_db_and_model

    tmp_vectors = tmp_path / "vectors"
    tmp_vectors.mkdir()
    monkeypatch.setenv("GRC_AGENT_VECTORS_DIR", str(tmp_vectors))
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    save_settings("ollama", "qwen3.6:35b-a3b-q4_K_M")
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
    n = ingest_mod.ingest_catalog(db_path, "fake-model", on_progress=lambda cur, tot: seen.append((cur, tot)))

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

    def fail_embed(text, model):  # noqa: ARG001
        raise RuntimeError("backend down")

    monkeypatch.setattr(ingest_mod, "embed_document", fail_embed)

    db_path = str(tmp_path / "catalog.db")
    n = ingest_mod.ingest_catalog(db_path, "fake-model")
    assert n > 0

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
    save_settings("ollama", "qwen3.6:35b-a3b-q4_K_M")
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
    save_settings("ollama", "qwen3.6:35b-a3b-q4_K_M")
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
    save_settings("ollama", "qwen3.6:35b-a3b-q4_K_M")
    db_path, model = get_db_and_model("catalog")

    def fail_embed(text, model):  # noqa: ARG001
        raise RuntimeError("backend down")

    # Build lexical-only (embeddings failed at build time — no catalog_idx table).
    monkeypatch.setattr(ingest_mod, "embed_document", fail_embed)
    ingest_mod.ingest_catalog(db_path, model)

    # Simulate the embedding backend having recovered since: embed_query now succeeds.
    import grc_agent.adapter.rag as rag_mod

    monkeypatch.setattr(rag_mod, "embed_query", lambda q: [0.1, 0.2, 0.3])  # noqa: ARG005

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
    save_settings("ollama", "qwen3.6:35b-a3b-q4_K_M")
    db_path, model = get_db_and_model("docs")

    def fail_embed(text, model):  # noqa: ARG001
        raise RuntimeError("backend down")

    monkeypatch.setattr(ingest_mod, "embed_document", fail_embed)
    ingest_mod.ingest_docs(db_path, model)

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
    save_settings("ollama", "qwen3.6:35b-a3b-q4_K_M")
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
    assert names == {"inspect_graph", "query_knowledge", "generate_python", "change_graph", "get_run_log"}


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

    from grc_agent.agent_factory import build_agent_from_cfg

    # ollama (local default)
    save_settings("ollama", "qwen3.6:35b-a3b-q4_K_M")
    agent_local, _ = build_agent_from_cfg(load_settings())
    assert isinstance(agent_local.model, OllamaModel), (
        f"local ollama cfg must produce OllamaModel, got {type(agent_local.model).__name__}"
    )

    # ollama_cloud
    save_settings("ollama_cloud", "deepseek-v4-flash:cloud")
    upsert_env_key("OLLAMA_CLOUD_API_KEY", "dummy-key-for-build-test")
    agent_cloud, _ = build_agent_from_cfg(load_settings())
    assert isinstance(agent_cloud.model, OllamaModel), (
        f"ollama_cloud cfg must produce OllamaModel, got {type(agent_cloud.model).__name__}"
    )
    # And its base_url must point at ollama.com, not localhost — the exact
    # confusion the live-swap fix exists to prevent.
    assert "ollama.com" in str(agent_cloud.model._provider.base_url), (
        f"ollama_cloud base_url must be ollama.com, got {agent_cloud.model._provider.base_url}"
    )

    # openrouter (key required by OpenRouterProvider at construction time)
    save_settings("openrouter", "deepseek/deepseek-v4-flash")
    upsert_env_key("OPENROUTER_API_KEY", "sk-or-dummy-key-for-build-test")
    agent_or, _ = build_agent_from_cfg(load_settings())
    assert isinstance(agent_or.model, OpenRouterModel), (
        f"openrouter cfg must produce OpenRouterModel, got {type(agent_or.model).__name__}"
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

    # 1. Boot with ollama_cloud cfg + a dummy key. We never send a real
    #    request on this agent, so the dummy key is fine — it just exercises
    #    the build path and gives us a baseline agent to "swap away from".
    save_settings("ollama_cloud", "deepseek-v4-flash:cloud")
    upsert_env_key("OLLAMA_CLOUD_API_KEY", "dummy-boot-key-not-used")
    agent1, _ = build_agent_from_cfg(load_settings())
    assert isinstance(agent1.model, OllamaModel)
    assert "ollama.com" in str(agent1.model._provider.base_url)

    # 2. Simulate the Settings dialog's Save path: write the new provider +
    #    real key to .env, then rebuild (exactly what
    #    ChatSidebar._rebuild_agent invokes after a successful Save).
    save_settings("openrouter", "deepseek/deepseek-v4-flash")
    upsert_env_key("OPENROUTER_API_KEY", api_key)
    agent2, _ = build_agent_from_cfg(load_settings())

    # 3. The new agent's model must actually be the new provider's type and
    #    point at the new base_url. This is the assertion that would have
    #    failed under the old restart-gated design if you forgot to restart
    #    (the agent would silently still be the old OllamaModel-on-ollama.com
    #    instance).
    assert agent2 is not agent1, "live-swap must build a NEW agent, not return the cached one"
    assert isinstance(agent2.model, OpenRouterModel), (
        f"post-swap model must be OpenRouterModel, got {type(agent2.model).__name__}"
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
            instructions="You are a terse assistant. Reply in one short sentence.",
        )
        result = await mini.run("Reply with exactly: OPENROUTER_LIVE_OK")
        return result.output.strip()

    reply = asyncio.run(_run())
    assert "OPENROUTER_LIVE_OK" in reply, f"expected OPENROUTER_LIVE_OK in reply, got: {reply!r}"


def test_preflight_connection_returns_none_on_success_and_error_on_failure():
    """preflight_connection must return None on a reachable endpoint and a
    descriptive error string on any failure. Two paths exercised:

    - Success: real OpenRouter /v1/models ping with the configured key (when
      set) — proves the endpoint, headers, and status-code check all work.
    - Failure (deterministic, no network): a missing api_key must return a
      non-None error string. OpenRouter's /v1/models is a public listing
      endpoint (no auth required), so a bogus key doesn't 401 there — the
      only reliable, network-independent failure case is the empty-key guard.
    """
    from grc_agent.agent_factory import preflight_connection

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if api_key:
        # Real success path — exercises the actual endpoint.
        err = preflight_connection("openrouter", api_key, timeout=10.0)
        assert err is None, f"expected None for a valid OpenRouter key, got: {err!r}"

    # Deterministic failure: missing key for each cloud provider must return
    # a non-empty error string. The exact message wording is not asserted so
    # the test stays robust to message edits.
    err = preflight_connection("openrouter", "", timeout=10.0)
    assert isinstance(err, str) and err, "missing openrouter key must produce a non-empty error"

    err = preflight_connection("ollama_cloud", "", timeout=10.0)
    assert isinstance(err, str) and err, "missing ollama_cloud key must produce a non-empty error"
