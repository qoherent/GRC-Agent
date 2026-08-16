"""User preferences for the interactive desktop agent — the provider, the
per-provider chat model names, and the API keys for the cloud providers — all
persisted in a single `.env` file (the source of truth), editable from the
Settings dialog or directly. Separate from the scenario-harness's fixed
MODEL/OLLAMA_V1 constants in agent.py, which stay pinned for reproducible
benchmarking.

Env vars (resolved by env_path(): GRC_AGENT_ENV override -> repo-root `.env`
-> ~/.config/grc_agent/.env for an installed package):

  GRC_PROVIDER              active chat provider: ollama | openai_compatible
  GRC_EMBED_BACKEND         embeddings backend: auto | ollama | llamacpp |
                            openai_compatible ("auto" follows GRC_PROVIDER)
  OLLAMA_CHAT_MODEL         local Ollama chat model
  OPENAI_COMPATIBLE_MODEL   OpenAI-compatible chat model
  OLLAMA_BASE_URL           local/remote Ollama base URL
  OPENAI_COMPATIBLE_BASE_URL
  OPENAI_COMPATIBLE_API_KEY

`load_settings()` reads the `.env` *file* (the saved source of truth), never
os.environ. A model/provider change is applied live by the Settings dialog's
Save handler — `chat_sidebar.py:_apply_settings_save` writes here, then calls
`build_agent_from_cfg(load_settings())` to rebuild the Agent in-place and
swaps it via `sidebar.set_agent`. The Settings dialog surfaces "Changes apply
immediately on Save." to make this explicit.
"""

import os
from pathlib import Path

from dotenv import dotenv_values, set_key

_VALID_PROVIDERS = ("ollama", "openai_compatible", "openai_codex")

# Which backend serves RAG embeddings. Deliberately independent of the chat
# provider: a chat endpoint that speaks the OpenAI API does not necessarily
# implement /v1/embeddings (llama-server started without `--embeddings`
# answers 501), and when it does not, vector search silently degrades to
# lexical BM25. "auto" keeps the historical behaviour of following the chat
# provider; the others pin it explicitly.
_VALID_EMBED_BACKENDS = ("auto", "ollama", "llamacpp", "openai_compatible")
_DEFAULT_EMBED_BACKEND = "auto"

# Per-provider chat-model env var name + settings dict key.
_PROVIDER_ENV_VAR = {
    "ollama": "OLLAMA_CHAT_MODEL",
    "openai_compatible": "OPENAI_COMPATIBLE_MODEL",
    "openai_codex": "OPENAI_CODEX_MODEL",
}
_PROVIDER_MODEL_KEY = {
    "ollama": "ollama_model",
    "openai_compatible": "openai_compatible_model",
    "openai_codex": "openai_codex_model",
}

_DEFAULT_MODELS = {
    "ollama_model": "qwen3.6:35b-a3b-q4_K_M",
    "openai_compatible_model": "deepseek/deepseek-v4-flash",
    "openai_codex_model": "gpt-5.6-luna",
}
_DEFAULT_PROVIDER = "ollama"
_DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
_DEFAULT_OPENAI_COMPATIBLE_BASE_URL = "https://openrouter.ai/api/v1"
_DEFAULT_OLLAMA_THINKING_ENABLED = True

# mtime-gated cache for dotenv_values(env_path()). dotenv_values re-parses the
# whole .env from disk on every call; callers like rag.py's embedding path hit
# it thousands of times per ingestion run, so this gates the parse on a cheap
# stat(). Keyed on (resolved path, mtime) so test isolation via GRC_AGENT_ENV
# tmp-path redirects and live settings swaps both invalidate correctly.
_dotenv_cache: tuple[str, float, dict[str, str]] | None = None


def _cached_dotenv() -> dict[str, str]:
    global _dotenv_cache
    path = env_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    path_str = str(path)
    if _dotenv_cache is None or _dotenv_cache[0] != path_str or _dotenv_cache[1] != mtime:
        _dotenv_cache = (
            path_str,
            mtime,
            {k: v for k, v in dotenv_values(path).items() if v is not None},
        )
    return _dotenv_cache[2]


def env_path() -> Path:
    """Resolve the `.env` file that is the single source of truth for GUI
    preferences. Resolution order:

    1. `GRC_AGENT_ENV` env var (explicit override — used by tests, and by an
       operator who wants prefs somewhere specific). Takes priority so a test
       redirect can never accidentally pick up the real repo `.env`.
    2. A `.env` file in the package repository root to prevent GRC dynamic
       CWD changes from loading/saving settings from/to different folders.
    3. `~/.config/grc_agent/.env` fallback.
    """
    override = os.environ.get("GRC_AGENT_ENV")
    if override:
        return Path(override)
    repo_env = Path(__file__).resolve().parent.parent.parent / ".env"
    if repo_env.exists():
        return repo_env
    return Path.home() / ".config" / "grc_agent" / ".env"


def resolve_embed_backend(cfg: dict) -> str:
    """The backend that actually serves embeddings for this config.

    Resolves "auto" to the chat provider. Single source of truth so `rag.py`
    (which picks the endpoint and the vector-DB filename) and the Settings
    dialog can never disagree about which backend is in use.
    """
    backend = cfg.get("embed_backend", _DEFAULT_EMBED_BACKEND)
    if backend != "auto":
        return backend
    provider = cfg.get("provider", _DEFAULT_PROVIDER)
    # "auto" can only follow a chat provider that also serves embeddings. The
    # ChatGPT/Codex transport does not expose /v1/embeddings at all, so it
    # falls back to the default rather than resolving to a backend that would
    # fail every call.
    return provider if provider in ("ollama", "openai_compatible") else _DEFAULT_PROVIDER


def default_settings() -> dict:
    res = {
        "provider": _DEFAULT_PROVIDER,
        "embed_backend": _DEFAULT_EMBED_BACKEND,
        "ollama_base_url": _DEFAULT_OLLAMA_BASE_URL,
        "openai_compatible_base_url": _DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
        "ollama_thinking_enabled": _DEFAULT_OLLAMA_THINKING_ENABLED,
        **_DEFAULT_MODELS,
    }
    res["model"] = res[_PROVIDER_MODEL_KEY[res["provider"]]]
    return res


def load_settings() -> dict:
    """Read the saved preferences from the `.env` file (the source of truth),
    applying defaults for any vars not present. Returns a dict with keys:
    provider, model, ollama_model, openai_compatible_model,
    ollama_base_url, openai_compatible_base_url, ollama_thinking_enabled."""
    vals = _cached_dotenv()

    raw_provider = vals.get("GRC_PROVIDER", _DEFAULT_PROVIDER)
    if raw_provider in ("openrouter", "openai_compatible"):
        provider = "openai_compatible"
    elif raw_provider in ("ollama", "ollama_cloud"):
        provider = "ollama"
    elif raw_provider in _VALID_PROVIDERS:
        provider = raw_provider
    else:
        provider = _DEFAULT_PROVIDER

    thinking_val = vals.get("OLLAMA_THINKING_ENABLED")
    if thinking_val is None:
        thinking_enabled = _DEFAULT_OLLAMA_THINKING_ENABLED
    else:
        thinking_enabled = thinking_val.lower() in ("true", "1", "yes")

    ollama_model = (
        vals.get("OLLAMA_CHAT_MODEL")
        or vals.get("OLLAMA_CLOUD_MODEL")
        or _DEFAULT_MODELS["ollama_model"]
    )
    openai_compatible_model = (
        vals.get("OPENAI_COMPATIBLE_MODEL")
        or vals.get("OPENROUTER_MODEL")
        or _DEFAULT_MODELS["openai_compatible_model"]
    )
    openai_codex_model = (
        vals.get("OPENAI_CODEX_MODEL") or _DEFAULT_MODELS["openai_codex_model"]
    )

    ollama_url = vals.get("OLLAMA_BASE_URL")
    if not ollama_url:
        ollama_url = (
            "https://ollama.com/v1"
            if vals.get("OLLAMA_CLOUD_API_KEY")
            else _DEFAULT_OLLAMA_BASE_URL
        )

    openai_url = vals.get("OPENAI_COMPATIBLE_BASE_URL")
    if not openai_url:
        openai_url = _DEFAULT_OPENAI_COMPATIBLE_BASE_URL

    embed_backend = vals.get("GRC_EMBED_BACKEND", _DEFAULT_EMBED_BACKEND)
    if embed_backend not in _VALID_EMBED_BACKENDS:
        embed_backend = _DEFAULT_EMBED_BACKEND

    res = {
        "provider": provider,
        "embed_backend": embed_backend,
        "ollama_model": ollama_model,
        "openai_compatible_model": openai_compatible_model,
        "openai_codex_model": openai_codex_model,
        "ollama_base_url": ollama_url,
        "openai_compatible_base_url": openai_url,
        "ollama_thinking_enabled": thinking_enabled,
    }
    res["model"] = res[_PROVIDER_MODEL_KEY[provider]]
    return res


def upsert_env_key(key: str, value: str, path: Path | None = None) -> None:
    """Insert or update a ``KEY=value`` line in the ``.env`` file."""
    target = path or env_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    set_key(str(target), key, value, quote_mode="never")


def save_settings(
    provider: str,
    model: str,
    ollama_base_url: str | None = None,
    openai_compatible_base_url: str | None = None,
    thinking_enabled: bool | None = None,
    embed_backend: str | None = None,
) -> None:
    """Persist the active provider, chat model name, base URLs, embedding
    backend, and thinking toggle into the `.env` file."""
    if provider not in _VALID_PROVIDERS:
        raise ValueError(f"Unknown provider: {provider!r}")
    if embed_backend is not None and embed_backend not in _VALID_EMBED_BACKENDS:
        raise ValueError(f"Unknown embedding backend: {embed_backend!r}")
    if not model.strip():
        raise ValueError("model must be non-empty")
    upsert_env_key("GRC_PROVIDER", provider)
    upsert_env_key(_PROVIDER_ENV_VAR[provider], model.strip())
    if ollama_base_url is not None:
        url = ollama_base_url.strip() or _DEFAULT_OLLAMA_BASE_URL
        upsert_env_key("OLLAMA_BASE_URL", url)
    if openai_compatible_base_url is not None:
        url = openai_compatible_base_url.strip() or _DEFAULT_OPENAI_COMPATIBLE_BASE_URL
        upsert_env_key("OPENAI_COMPATIBLE_BASE_URL", url)
    if thinking_enabled is not None:
        upsert_env_key("OLLAMA_THINKING_ENABLED", "true" if thinking_enabled else "false")
    if embed_backend is not None:
        upsert_env_key("GRC_EMBED_BACKEND", embed_backend)


def get_env_value(key: str) -> str | None:
    """Read a single key from the ``.env`` file (the saved source of truth)."""
    return _cached_dotenv().get(key)
