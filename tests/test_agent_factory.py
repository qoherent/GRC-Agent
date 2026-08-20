"""Unit tests for agent_factory — from the former test_unit.py god file.

Minimal set per the clustered test plan; shared fixtures/helpers live in conftest.py.
"""

from conftest import _FakeResponse


def test_settings_custom_ollama_url(tmp_path, monkeypatch):
    """Test OLLAMA_BASE_URL settings load, default, and save."""
    from grc_agent.settings import default_settings, load_settings, save_settings

    env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env_file))

    # 1. Defaults
    defaults = default_settings()
    assert defaults["ollama_base_url"] == "http://localhost:11434"

    # 2. Load from empty .env returns defaults
    cfg = load_settings()
    assert cfg["ollama_base_url"] == "http://localhost:11434"

    # 3. Save custom settings
    save_settings(
        "ollama_local",
        "qwen3.6:35b-a3b-q4_K_M",
        ollama_base_url="http://192.168.1.100:11434",
    )

    # 4. Load persisted custom settings
    cfg2 = load_settings()
    assert cfg2["ollama_base_url"] == "http://192.168.1.100:11434"


def test_agent_factory_custom_ollama_url(monkeypatch):
    """Test build_agents_from_cfg passes custom base_url to the provider."""
    from grc_agent.agent_factory import build_agents_from_cfg, probe_backend

    cfg = {
        "provider": "ollama_local",
        "model": "qwen3.6:35b-a3b-q4_K_M",
        "ollama_base_url": "http://192.168.1.200:11434",
    }

    # The compaction window probe must not hit the network in a fast test.
    monkeypatch.setattr(
        "grc_agent.agent_factory.resolve_model_context_length", lambda *_a, **_k: None
    )
    agents = build_agents_from_cfg(cfg)
    agent = agents.executor
    assert agents.model_build_error is None
    # No thinking request knobs: the provider's native default stands.
    assert not (agent.model_settings or {}).get("extra_body")

    # Verify provider base_url contains custom IP (with /v1 appended)
    provider = getattr(agent.model, "_provider", None) or getattr(agent.model, "provider", None)
    assert provider is not None
    assert "192.168.1.200:11434" in provider.base_url

    # Verify probe_backend uses custom ollama_base_url (will fail connection gracefully)
    err, _w = probe_backend("ollama_local", "", "http://127.0.0.1:9999", "", timeout=0.1)
    assert err is not None


def test_openai_compatible_provider_and_factory(tmp_path, monkeypatch):
    """Test openai_compatible provider settings, agent creation, and preflight connection."""
    from grc_agent.agent_factory import build_agents_from_cfg, probe_backend
    from grc_agent.settings import load_settings, save_settings

    env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env_file))

    # Save openai_compatible settings
    save_settings(
        "openai_compatible",
        "llama3.3:70b-gguf",
        openai_compatible_base_url="http://localhost:8080/v1",
    )

    cfg = load_settings()
    assert cfg["provider"] == "openai_compatible"
    assert cfg["model"] == "llama3.3:70b-gguf"
    assert cfg["openai_compatible_base_url"] == "http://localhost:8080/v1"

    monkeypatch.setattr(
        "grc_agent.agent_factory.resolve_model_context_length", lambda *_a, **_k: None
    )
    agents = build_agents_from_cfg(cfg)
    agent = agents.executor
    assert agents.model_build_error is None

    # Check model provider
    provider = getattr(agent.model, "_provider", None) or getattr(agent.model, "provider", None)
    assert provider is not None
    assert "localhost:8080" in provider.base_url

    err, _w = probe_backend("openai_compatible", "", "http://127.0.0.1:9999/v1", "", timeout=0.1)
    assert err is not None


def test_probe_backend_branches(monkeypatch):
    """Every branch of probe_backend in one test: a tag the backend does not
    serve is flagged from its own catalog in the SAME bounded call that
    checks reachability (the reported hung-chat cause — the daemon silently
    pulls multi-GB while the request stays open with zero output), an
    unreachable backend is a reachability error only, codex is credential-
    checked, and an empty model skips the listing check."""
    import httpx as _httpx

    from grc_agent.agent_factory import probe_backend

    monkeypatch.setattr(
        _httpx,
        "get",
        lambda *_a, **_kw: _FakeResponse(
            payload={
                "models": [{"name": "deepseek-v4-flash:cloud"}, {"name": "qwen3.6:35b-a3b-q4_K_M"}]
            }
        ),
    )
    # Mismatched tag -> warning that names what IS served.
    reach, warn = probe_backend(
        "ollama_local", "", "http://localhost:11434", "deepseek-v4-flash:0731-cloud"
    )
    assert reach is None
    assert warn is not None and "not served" in warn
    assert "deepseek-v4-flash:cloud" in warn
    # Matching tag -> silent.
    _, warn2 = probe_backend(
        "ollama_local", "", "http://localhost:11434", "deepseek-v4-flash:cloud"
    )
    assert warn2 is None

    # Unreachable backend -> reachability error, no model warning, bounded.
    monkeypatch.setattr(
        _httpx, "get", lambda *_a, **_kw: (_ for _ in ()).throw(_httpx.ConnectError("down"))
    )
    reach, warn = probe_backend("ollama_local", "", "http://localhost:9999", "m")
    assert reach is not None and "could not reach http://localhost:9999" in reach
    assert warn is None

    # Codex: credential check only, never a model-list probe.
    monkeypatch.setattr(
        _httpx, "get", lambda *_a, **_kw: _FakeResponse(payload={"data": [{"id": "m"}]})
    )
    monkeypatch.setattr("grc_agent.providers.openai_codex.is_signed_in", lambda: True)
    assert probe_backend("openai_codex", "", "", "gpt-5.6-luna") == (None, None)
    # Empty model -> reachability only.
    assert probe_backend("ollama_local", "", "http://localhost:11434", "")[1] is None
