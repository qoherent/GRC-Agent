"""User preferences for the interactive desktop agent — the provider, the
per-provider chat model names, and the API keys for the cloud providers — all
persisted in a single `.env` file (the source of truth), editable from the
Settings dialog or directly. Separate from the scenario-harness's fixed
MODEL/OLLAMA_V1 constants in agent.py, which stay pinned for reproducible
benchmarking.

Env vars (resolved by env_path(): GRC_AGENT_ENV override -> repo-root `.env`
-> ~/.config/grc_agent/.env for an installed package):

  GRC_PROVIDER              active chat provider: one of the twelve concrete
                            ids in _VALID_PROVIDERS (ollama_local |
                            ollama_cloud | openrouter | openai |
                            openai_compatible | anthropic | google | groq |
                            mistral | cohere | xai | openai_codex)
  GRC_EMBED_BACKEND         embeddings backend: lexical | llamacpp
                            (default: lexical)
  OLLAMA_CHAT_MODEL         Ollama chat model (local and cloud share it)
  OPENROUTER_MODEL          OpenRouter chat model
  OPENAI_MODEL              OpenAI API chat model
  OPENAI_COMPATIBLE_MODEL   other OpenAI-compatible chat model
  OPENAI_CODEX_MODEL        ChatGPT/Codex chat model
  OLLAMA_BASE_URL           local Ollama base URL (ollama_local only;
                            ollama_cloud is fixed to https://ollama.com/v1)
  OPENAI_COMPATIBLE_BASE_URL
  OLLAMA_API_KEY            Ollama Cloud key (local only needs it if auth'd)
  OPENROUTER_API_KEY / OPENAI_API_KEY / OPENAI_COMPATIBLE_API_KEY

`load_settings()` reads the `.env` *file* (the saved source of truth), never
os.environ. A model/provider change is applied live by the Settings dialog's
Save handler — `chat_sidebar.py:_apply_settings_save` writes here, then calls
`build_agents_from_cfg(load_settings())` to rebuild both roles in-place and
swaps them via `sidebar.set_agents`. The Settings dialog surfaces "Changes
apply immediately on Save." to make this explicit.
"""

import os
from pathlib import Path

from dotenv import dotenv_values, set_key

_VALID_PROVIDERS = (
    "ollama_local",
    "ollama_cloud",
    "openrouter",
    "openai",
    "openai_compatible",
    "anthropic",
    "google",
    "groq",
    "mistral",
    "cohere",
    "xai",
    "openai_codex",
)

# Which backend serves RAG embeddings: "lexical" (SQLite FTS5/BM25 keyword
# search, zero dependencies/runtime) or "llamacpp" (local llama.cpp runtime
# serving EmbeddingGemma over a private UNIX socket).
_VALID_EMBED_BACKENDS = ("lexical", "llamacpp")
_DEFAULT_EMBED_BACKEND = "lexical"

_VALID_THEMES = ("system", "dark", "light")
_DEFAULT_THEME = "system"

# Per-provider chat-model env var name + settings dict key.
_PROVIDER_ENV_VAR = {
    "ollama_local": "OLLAMA_CHAT_MODEL",
    "ollama_cloud": "OLLAMA_CHAT_MODEL",
    "openrouter": "OPENROUTER_MODEL",
    "openai": "OPENAI_MODEL",
    "openai_compatible": "OPENAI_COMPATIBLE_MODEL",
    "anthropic": "ANTHROPIC_MODEL",
    "google": "GOOGLE_MODEL",
    "groq": "GROQ_MODEL",
    "mistral": "MISTRAL_MODEL",
    "cohere": "COHERE_MODEL",
    "xai": "XAI_MODEL",
    "openai_codex": "OPENAI_CODEX_MODEL",
}
_PROVIDER_MODEL_KEY = {
    "ollama_local": "ollama_model",
    "ollama_cloud": "ollama_model",
    "openrouter": "openrouter_model",
    "openai": "openai_model",
    "openai_compatible": "openai_compatible_model",
    "anthropic": "anthropic_model",
    "google": "google_model",
    "groq": "groq_model",
    "mistral": "mistral_model",
    "cohere": "cohere_model",
    "xai": "xai_model",
    "openai_codex": "openai_codex_model",
}

_DEFAULT_MODELS = {
    "ollama_model": "qwen3.8:latest",
    "openrouter_model": "deepseek/deepseek-v4-flash",
    "openai_model": "gpt-5.6-terra",
    "openai_compatible_model": "deepseek/deepseek-v4-flash",
    "anthropic_model": "claude-sonnet-5",
    "google_model": "gemini-3.7-flash",
    "groq_model": "llama-3.3-70b-versatile",
    "mistral_model": "codestral-latest",
    "cohere_model": "north-mini-code-1-0",
    "xai_model": "grok-4.6",
    "openai_codex_model": "gpt-5.6-luna",
}
_DEFAULT_PROVIDER = "ollama_local"

_DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
_DEFAULT_OPENAI_COMPATIBLE_BASE_URL = "http://localhost:8080/v1"

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

    Either "lexical" (SQLite FTS5/BM25, no runtime required) or "llamacpp"
    (local llama.cpp runtime serving EmbeddingGemma).
    """
    backend = cfg.get("embed_backend", _DEFAULT_EMBED_BACKEND)
    if backend in _VALID_EMBED_BACKENDS:
        return backend
    return _DEFAULT_EMBED_BACKEND


def default_settings() -> dict:
    res = {
        "provider": _DEFAULT_PROVIDER,
        "embed_backend": _DEFAULT_EMBED_BACKEND,
        "theme": _DEFAULT_THEME,
        "ollama_base_url": _DEFAULT_OLLAMA_BASE_URL,
        "openai_compatible_base_url": _DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
        "project_dir": "",
        **_DEFAULT_MODELS,
    }
    res["model"] = res[_PROVIDER_MODEL_KEY[res["provider"]]]
    return res


def load_settings() -> dict:
    """Read the saved preferences from the `.env` file (the source of truth),
    applying defaults for any vars not present. Returns a dict with keys:
    provider, model, per-provider model keys, ollama_base_url,
    openai_compatible_base_url, embed_backend, project_dir, theme."""
    vals = _cached_dotenv()

    provider = vals.get("GRC_PROVIDER", _DEFAULT_PROVIDER)
    if provider not in _VALID_PROVIDERS:
        provider = _DEFAULT_PROVIDER

    ollama_model = vals.get("OLLAMA_CHAT_MODEL") or _DEFAULT_MODELS["ollama_model"]
    openrouter_model = vals.get("OPENROUTER_MODEL") or _DEFAULT_MODELS["openrouter_model"]
    openai_model = vals.get("OPENAI_MODEL") or _DEFAULT_MODELS["openai_model"]
    openai_compatible_model = (
        vals.get("OPENAI_COMPATIBLE_MODEL") or _DEFAULT_MODELS["openai_compatible_model"]
    )
    openai_codex_model = vals.get("OPENAI_CODEX_MODEL") or _DEFAULT_MODELS["openai_codex_model"]
    anthropic_model = vals.get("ANTHROPIC_MODEL") or _DEFAULT_MODELS["anthropic_model"]
    google_model = vals.get("GOOGLE_MODEL") or _DEFAULT_MODELS["google_model"]
    groq_model = vals.get("GROQ_MODEL") or _DEFAULT_MODELS["groq_model"]
    mistral_model = vals.get("MISTRAL_MODEL") or _DEFAULT_MODELS["mistral_model"]
    cohere_model = vals.get("COHERE_MODEL") or _DEFAULT_MODELS["cohere_model"]
    xai_model = vals.get("XAI_MODEL") or _DEFAULT_MODELS["xai_model"]

    ollama_url = vals.get("OLLAMA_BASE_URL")
    if not ollama_url:
        # The cloud endpoint is canonical for the ollama_cloud provider; the
        # local daemon URL applies to ollama_local (and as a fallback).
        ollama_url = "https://ollama.com/v1" if provider == "ollama_cloud" else _DEFAULT_OLLAMA_BASE_URL

    openai_url = vals.get("OPENAI_COMPATIBLE_BASE_URL")
    if not openai_url:
        openai_url = _DEFAULT_OPENAI_COMPATIBLE_BASE_URL

    embed_backend = resolve_embed_backend(
        {"embed_backend": vals.get("GRC_EMBED_BACKEND", _DEFAULT_EMBED_BACKEND)}
    )

    project_dir = vals.get("GRC_PROJECT_DIR", "")

    theme = vals.get("GRC_THEME_MODE", _DEFAULT_THEME)
    if theme not in _VALID_THEMES:
        theme = _DEFAULT_THEME

    res = {
        "provider": provider,
        "embed_backend": embed_backend,
        "theme": theme,
        "ollama_model": ollama_model,
        "openrouter_model": openrouter_model,
        "openai_model": openai_model,
        "openai_compatible_model": openai_compatible_model,
        "anthropic_model": anthropic_model,
        "google_model": google_model,
        "groq_model": groq_model,
        "mistral_model": mistral_model,
        "cohere_model": cohere_model,
        "xai_model": xai_model,
        "openai_codex_model": openai_codex_model,
        "ollama_base_url": ollama_url,
        "openai_compatible_base_url": openai_url,
        "project_dir": project_dir,
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
    embed_backend: str | None = None,
    theme: str | None = None,
) -> None:
    """Persist the active provider, chat model name, base URLs, embedding
    backend, and theme into the `.env` file."""
    if provider not in _VALID_PROVIDERS:
        raise ValueError(f"Unknown provider: {provider!r}")
    if embed_backend is not None and embed_backend not in _VALID_EMBED_BACKENDS:
        raise ValueError(f"Unknown embedding backend: {embed_backend!r}")
    if theme is not None and theme not in _VALID_THEMES:
        raise ValueError(f"Unknown theme mode: {theme!r}")
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
    if embed_backend is not None:
        upsert_env_key("GRC_EMBED_BACKEND", embed_backend)
    if theme is not None:
        upsert_env_key("GRC_THEME_MODE", theme)


def get_theme_mode() -> str:
    """Get the persisted theme mode ('dark', 'light', or 'system')."""
    val = get_env_value("GRC_THEME_MODE")
    return val if val in _VALID_THEMES else _DEFAULT_THEME


def set_theme_mode(mode: str) -> None:
    """Persist the chosen theme mode into the `.env` file."""
    if mode in _VALID_THEMES:
        upsert_env_key("GRC_THEME_MODE", mode)


def get_project_dir() -> Path | None:
    """Get the persisted project directory if configured and valid on disk."""
    val = get_env_value("GRC_PROJECT_DIR")
    if not val:
        return None
    p = Path(val).resolve()
    return p if p.is_dir() else None


def set_project_dir(path: Path | str | None) -> None:
    """Persist the chosen project directory into the `.env` file."""
    if path is not None and str(path).strip():
        upsert_env_key("GRC_PROJECT_DIR", str(Path(path).resolve()))
    else:
        upsert_env_key("GRC_PROJECT_DIR", "")


def get_env_value(key: str) -> str | None:
    """Read a single key from the ``.env`` file (the saved source of truth)."""
    return _cached_dotenv().get(key)
