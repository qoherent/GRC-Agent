"""User preferences for the interactive web agent — the provider, the per-
provider chat model names, and the API keys for the cloud providers — all
persisted in a single `.env` file (the source of truth), editable from the
dashboard GUI or directly. Separate from the scenario-harness's fixed
MODEL/OLLAMA_V1 constants in agent.py, which stay pinned for reproducible
benchmarking.

Env vars (read in order: GRC_AGENT_ENV override -> repo `.env` found by
walking up from CWD -> ~/.config/grc_agent/.env for an installed package):

  GRC_PROVIDER          active provider: ollama | openrouter | ollama_cloud
  OLLAMA_CHAT_MODEL     local Ollama chat model
  OPENROUTER_MODEL      OpenRouter chat model
  OLLAMA_CLOUD_MODEL    Ollama Cloud chat model
  OPENROUTER_API_KEY    OpenRouter API key (written by /grc/apikey)
  OLLAMA_CLOUD_API_KEY  Ollama Cloud API key (written by /grc/apikey)

`load_settings()` reads the `.env` *file* (the saved source of truth), NOT
os.environ — the latter is the running process's startup snapshot, which is
what `_build_model()` captures once at import. That split is what lets the
dashboard's restart badge distinguish "saved" from "actually running". A
model/provider change therefore needs an app restart to take effect (the
Agent is built once); only the API key is also set into os.environ on save
so the health check can see it immediately.
"""

import os
from pathlib import Path

from dotenv import dotenv_values, find_dotenv, get_key, set_key

_VALID_PROVIDERS = ("ollama", "openrouter", "ollama_cloud")

# Per-provider chat-model env var name + settings dict key.
_PROVIDER_ENV_VAR = {
    "ollama": "OLLAMA_CHAT_MODEL",
    "openrouter": "OPENROUTER_MODEL",
    "ollama_cloud": "OLLAMA_CLOUD_MODEL",
}
_PROVIDER_MODEL_KEY = {
    "ollama": "ollama_model",
    "openrouter": "openrouter_model",
    "ollama_cloud": "ollama_cloud_model",
}

_DEFAULT_MODELS = {
    "ollama_model": "qwen3.6:35b-a3b-q4_K_M",
    "openrouter_model": "deepseek/deepseek-v4-flash",
    "ollama_cloud_model": "deepseek-v4-flash:cloud",
}
_DEFAULT_PROVIDER = "ollama"


def env_path() -> Path:
    """Resolve the `.env` file that is the single source of truth for GUI
    preferences. Resolution order:

    1. `GRC_AGENT_ENV` env var (explicit override — used by tests, and by an
       operator who wants prefs somewhere specific). Takes priority so a test
       redirect can never accidentally pick up the real repo `.env`.
    2. A `.env` in or above the CWD (find_dotenv, usecwd=True) — the dev
       workflow (`uv run grc-agent` from the repo root reads repo `.env`).
    3. `~/.config/grc_agent/.env` — the stable home for an installed package,
       where there is no repo root to find. Never CWD-relative (that produced
       inconsistent reads/writes across launch directories).
    """
    override = os.environ.get("GRC_AGENT_ENV")
    if override:
        return Path(override)
    found = find_dotenv(usecwd=True)
    return Path(found) if found else Path.home() / ".config" / "grc_agent" / ".env"


def default_settings() -> dict:
    res = {
        "provider": _DEFAULT_PROVIDER,
        **_DEFAULT_MODELS,
    }
    res["model"] = res[_PROVIDER_MODEL_KEY[res["provider"]]]
    return res


def load_settings() -> dict:
    """Read the saved preferences from the `.env` file (the source of truth),
    applying defaults for any vars not present. Returns a dict with keys:
    provider, model, ollama_model, openrouter_model, ollama_cloud_model."""
    vals = {k: v for k, v in dotenv_values(env_path()).items() if v is not None}

    provider = vals.get("GRC_PROVIDER", _DEFAULT_PROVIDER)
    if provider not in _VALID_PROVIDERS:
        provider = _DEFAULT_PROVIDER

    res = {
        "provider": provider,
        "ollama_model": vals.get("OLLAMA_CHAT_MODEL", _DEFAULT_MODELS["ollama_model"]),
        "openrouter_model": vals.get("OPENROUTER_MODEL", _DEFAULT_MODELS["openrouter_model"]),
        "ollama_cloud_model": vals.get("OLLAMA_CLOUD_MODEL", _DEFAULT_MODELS["ollama_cloud_model"]),
    }
    res["model"] = res[_PROVIDER_MODEL_KEY[provider]]
    return res


def upsert_env_key(key: str, value: str, path: Path | None = None) -> None:
    """Insert or update a ``KEY=value`` line in the ``.env`` file."""
    target = path or env_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    set_key(str(target), key, value, quote_mode="never")


def save_settings(provider: str, model: str) -> None:
    """Persist the active provider + its chat model name into the `.env` file.
    Only the selected provider's model var is touched — the other providers'
    saved model names are preserved verbatim (standard `.env` upsert). Does
    NOT touch os.environ: a model/provider change is restart-gated (the Agent
    is built once at import), so updating the running snapshot here would only
    mask the pending-restart state the dashboard badge exists to surface."""
    if provider not in _VALID_PROVIDERS:
        raise ValueError(f"Unknown provider: {provider!r}")
    if not model.strip():
        raise ValueError("model must be non-empty")
    upsert_env_key("GRC_PROVIDER", provider)
    upsert_env_key(_PROVIDER_ENV_VAR[provider], model.strip())


def get_env_value(key: str) -> str | None:
    """Read a single key from the ``.env`` file (the saved source of truth)."""
    return get_key(str(env_path()), key)
