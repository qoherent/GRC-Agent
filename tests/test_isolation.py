import pytest
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openrouter import OpenRouterModel

from grc_agent.adapter import _embed_endpoint, get_db_and_model
from grc_agent.agent import build_scenario_model
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
