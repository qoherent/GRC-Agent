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


def test_agent_factory_custom_ollama_url(tmp_path, monkeypatch):
    """Test build_agents_from_cfg passes custom base_url to the provider."""
    from grc_agent import db
    from grc_agent.agent_factory import build_agents_from_cfg, probe_backend

    # Redirect the step store to a fresh tmp DB: the factory eagerly binds
    # StepPersistence via init_db(), whose orphan sweeps DELETE rows — never
    # against the developer's real .grc_agent/chat_sessions.db.
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    db._initialized_paths.clear()
    db._step_stores.clear()

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
                "models": [{"name": "glm-5.3-flash:cloud"}, {"name": "qwen3.6:35b-a3b-q4_K_M"}]
            }
        ),
    )
    # Mismatched tag -> warning that names what IS served.
    reach, warn = probe_backend(
        "ollama_local", "", "http://localhost:11434", "glm-5.3-flash:0731-cloud"
    )
    assert reach is None
    assert warn is not None and "not served" in warn
    assert "glm-5.3-flash:cloud" in warn
    # Matching tag -> silent.
    _, warn2 = probe_backend(
        "ollama_local", "", "http://localhost:11434", "glm-5.3-flash:cloud"
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


def test_every_provider_accepts_the_client_it_is_built_with(monkeypatch):
    """The HTTP stack handed to each provider must be the one its SDK accepts.

    pydantic-ai 2.37 is mid-migration: Anthropic rejects an httpx.AsyncClient
    outright and Groq rejects an httpx2.AsyncClient outright, so a single
    shared client cannot serve both. _retrying_http_client picks per provider;
    this pins that every provider the Settings UI exposes actually builds.

    When Groq migrates to httpx2 this test still passes and
    _HTTPX1_ONLY_PROVIDERS can be emptied -- see the companion test below.
    """
    import warnings

    from grc_agent.agent_factory import _build_model, _retrying_http_client
    from grc_agent.ui.providers import PROVIDER_API_KEY

    for var in PROVIDER_API_KEY.values():
        if var:
            monkeypatch.setenv(var, "dummy-key")

    # openai_codex authenticates through its own OAuth credential store rather
    # than an API key, so it is exercised by its own suite.
    providers = [p for p in PROVIDER_API_KEY if p != "openai_codex"]
    assert providers, "provider catalog must not be empty"

    # OpenRouter validates that the model name carries an upstream prefix.
    model_names = {"openrouter": "openai/test-model"}

    for provider in providers:
        client = _retrying_http_client(provider)
        name = model_names.get(provider, "test-model")
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # a deprecated stack must fail loudly
            model = _build_model({"provider": provider, "model": name}, client)
        assert model is not None, f"{provider} produced no model"


def test_httpx1_exception_set_is_still_needed(monkeypatch):
    """_HTTPX1_ONLY_PROVIDERS is a temporary carve-out; prove it is still real.

    If this fails because Groq now accepts httpx2, delete the entry (and this
    test) rather than widening it.
    """
    import httpx2
    import pytest

    from grc_agent.agent_factory import _HTTPX1_ONLY_PROVIDERS, _build_model
    from grc_agent.ui.providers import PROVIDER_API_KEY

    for provider in _HTTPX1_ONLY_PROVIDERS:
        monkeypatch.setenv(PROVIDER_API_KEY[provider], "dummy-key")
        with pytest.raises(TypeError, match="http_client"):
            _build_model({"provider": provider, "model": "x"}, httpx2.AsyncClient())


def test_repeated_terminal_failures_end_the_run():
    """ToolFailed spends no retry budget, so the run-level bound must exist.

    Converting the environment faults from ModelRetry to ToolFailed removed
    the per-tool retry ceiling that used to stop a model hammering a dead
    end. StopGracefully now ends the run after a tool fails terminally three
    times in a row -- the bound AGENTS.md section 3 names in place of the old
    prose "do not retry" instruction.
    """
    from types import SimpleNamespace

    from grc_agent.agent import StopGracefully

    cap = StopGracefully()

    def failed(tool: str):
        return SimpleNamespace(parts=[SimpleNamespace(outcome="failed", tool_name=tool)])

    def ok(tool: str):
        return SimpleNamespace(parts=[SimpleNamespace(outcome="success", tool_name=tool)])

    ctx = SimpleNamespace(messages=[failed("get_run_log"), failed("get_run_log")])
    assert cap._repeatedly_failing_tool(ctx) is None, "two failures must not end the run"

    ctx = SimpleNamespace(messages=[failed("get_run_log")] * 3)
    assert cap._repeatedly_failing_tool(ctx) == "get_run_log"

    # A success breaks the streak: fail, recover, fail again is not a dead end.
    ctx = SimpleNamespace(messages=[failed("x"), failed("x"), ok("x"), failed("x")])
    assert cap._repeatedly_failing_tool(ctx) is None

    # Failures of *different* tools do not aggregate into one streak.
    ctx = SimpleNamespace(messages=[failed("a"), failed("b"), failed("c")])
    assert cap._repeatedly_failing_tool(ctx) is None


def test_agent_module_imports_without_pygobject():
    """The tool layer must not drag GTK in through its type annotations.

    agent.py has no postponed-annotation import, so annotating ctx.deps with
    NativeFlowgraphProxy would evaluate that name at definition time and pull
    gi/GTK into the import path -- the separation agent_factory already keeps
    behind `if TYPE_CHECKING`. The deps Protocol exists to avoid that.
    """
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(
        """
        import sys
        # Make any gi import fail, the way a machine without PyGObject would.
        class _Blocked:
            def find_module(self, name, path=None):
                if name == "gi" or name.startswith("gi."):
                    raise ImportError("PyGObject is not installed")
                return None
        sys.meta_path.insert(0, _Blocked())
        import grc_agent.agent  # noqa: F401
        assert "gi" not in sys.modules, "importing the tool layer pulled in GTK"
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr[-1500:]
    assert "OK" in result.stdout
