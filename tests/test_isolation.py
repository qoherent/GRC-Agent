from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openrouter import OpenRouterModel

from grc_agent.adapter import _embed_endpoint, get_db_and_model
from grc_agent.settings import load_settings, save_settings
from grc_agent.web import _build_model


def test_settings_isolation_and_defaults(tmp_path, monkeypatch):
    """Verify that settings are saved/loaded correctly and that ollama_model

    and openrouter_model are handled independently (no overwriting).
    """
    tmp_config_file = tmp_path / "settings.json"
    monkeypatch.setenv("GRC_AGENT_CONFIG_PATH", str(tmp_config_file))

    # 1. Load initial settings (defaults)
    cfg = load_settings()
    assert cfg["provider"] == "ollama"
    assert cfg["ollama_model"] == "qwen3.6:35b-a3b-q4_K_M"
    assert cfg["openrouter_model"] == "openai/gpt-4o-mini"

    # 2. Switch provider to openrouter and change model
    save_settings("openrouter", "google/gemini-2.5-flash")
    cfg = load_settings()
    assert cfg["provider"] == "openrouter"
    assert cfg["model"] == "google/gemini-2.5-flash"
    assert cfg["openrouter_model"] == "google/gemini-2.5-flash"
    assert cfg["ollama_model"] == "qwen3.6:35b-a3b-q4_K_M"  # preserved!

    # 3. Switch back to ollama and change model
    save_settings("ollama", "mistral-large")
    cfg = load_settings()
    assert cfg["provider"] == "ollama"
    assert cfg["model"] == "mistral-large"
    assert cfg["ollama_model"] == "mistral-large"
    assert cfg["openrouter_model"] == "google/gemini-2.5-flash"  # preserved!


def test_db_and_model_isolation(tmp_path, monkeypatch):
    """Verify database filenames and embedding model settings are disjoint.

    Ollama queries/embeddings should only target *_ollama.db.
    OpenRouter queries/embeddings should only target *_openrouter.db.
    """
    tmp_config_file = tmp_path / "settings.json"
    monkeypatch.setenv("GRC_AGENT_CONFIG_PATH", str(tmp_config_file))

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
    tmp_config_file = tmp_path / "settings.json"
    monkeypatch.setenv("GRC_AGENT_CONFIG_PATH", str(tmp_config_file))
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
    """Verify that web._build_model instantiates the correct model type based on the settings."""
    tmp_config_file = tmp_path / "settings.json"
    monkeypatch.setenv("GRC_AGENT_CONFIG_PATH", str(tmp_config_file))

    import grc_agent.web

    # Setup provider: ollama
    monkeypatch.setitem(grc_agent.web._cfg, "provider", "ollama")
    monkeypatch.setitem(grc_agent.web._cfg, "model", "qwen3.6:35b-a3b-q4_K_M")
    m = _build_model()
    assert isinstance(m, OllamaModel)
    assert m.model_name == "qwen3.6:35b-a3b-q4_K_M"

    # Setup provider: openrouter
    monkeypatch.setitem(grc_agent.web._cfg, "provider", "openrouter")
    monkeypatch.setitem(grc_agent.web._cfg, "model", "openai/gpt-4o-mini")
    m = _build_model()
    assert isinstance(m, OpenRouterModel)
    assert m.model_name == "openai/gpt-4o-mini"
