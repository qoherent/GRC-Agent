"""Provider catalog shared by the chat sidebar and the settings dialog.

Pure data + one pure resolver. Kept here so both ``chat_sidebar.py`` (the
toolbar's active-provider badge) and ``ui/settings_dialog.py`` (the provider
dropdown) share one source of truth, and so neither imports the other.

Providers are concrete — "Ollama (local)" and "Ollama Cloud" are separate
entries, as are OpenRouter / OpenAI / a generic OpenAI-compatible endpoint —
so the user picks the actual service and the URL/key requirements follow,
instead of one ambiguous provider whose meaning depends on a base URL.
"""

# Display order in the Settings dropdown.
PROVIDER_ORDER = (
    "ollama_local",
    "ollama_cloud",
    "openrouter",
    "openai",
    "openai_compatible",
    "openai_codex",
)

# Full human-readable labels for the Settings dialog.
PROVIDER_LABELS = {
    "ollama_local": "Ollama (local)",
    "ollama_cloud": "Ollama Cloud",
    "openrouter": "OpenRouter",
    "openai": "OpenAI API",
    "openai_compatible": "Other — OpenAI-compatible (llama.cpp, vLLM, LM Studio, Groq…)",
    "openai_codex": "ChatGPT Plus/Pro (Codex)",
}

# Which .env key holds the per-provider model id (see settings.py).
PROVIDER_MODEL_KEY = {
    "ollama_local": "ollama_model",
    "ollama_cloud": "ollama_model",
    "openrouter": "openrouter_model",
    "openai": "openai_model",
    "openai_compatible": "openai_compatible_model",
    "openai_codex": "openai_codex_model",
}

# Which .env key (if any) holds the provider's API key.
# None for openai_codex: it authenticates with an OAuth token pair stored
# outside .env (see providers/openai_codex/credentials.py), so the Save path
# must not write an API-key line for it.
PROVIDER_API_KEY = {
    "ollama_local": "OLLAMA_API_KEY",
    "ollama_cloud": "OLLAMA_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
    "openai_codex": None,
}

PROVIDER_MODEL_PLACEHOLDER = {
    "ollama_local": "qwen3.6:35b-a3b-q4_K_M",
    "ollama_cloud": "deepseek-v4-flash:cloud",
    "openrouter": "deepseek/deepseek-v4-flash, qwen/qwen3-coder…",
    "openai": "gpt-5.6-sol — click Load to list yours",
    "openai_compatible": "model id served by your endpoint",
    "openai_codex": "gpt-5.6-luna — click Load to list yours",
}

PROVIDER_KEY_PLACEHOLDER = {
    "ollama_local": "Optional (only if your Ollama requires auth)",
    "ollama_cloud": "Ollama Cloud API key (sk-…)",
    "openrouter": "OpenRouter API key (sk-or-…)",
    "openai": "OpenAI API key (sk-…)",
    "openai_compatible": "API key (optional for local endpoints)",
    "openai_codex": "",
}

# Short labels for the toolbar badge — PROVIDER_LABELS forms are too long.
# Local and cloud Ollama get distinct badges so a saved switch is visibly
# applied (the old single "ollama" badge was indistinguishable).
PROVIDER_BADGE_LABEL = {
    "ollama_local": "ollama",
    "ollama_cloud": "ollama cloud",
    "openrouter": "openrouter",
    "openai": "openai",
    "openai_compatible": "openai-compat",
    "openai_codex": "chatgpt",
}

# Canonical endpoint per provider. None = user-editable URL persisted under
# the provider's base-URL env var (OLLAMA_BASE_URL / OPENAI_COMPATIBLE_BASE_
# URL, see settings.py); a fixed string = the service's real endpoint, shown
# read-only in the dialog and never persisted. "" = no URL surface at all
# (ChatGPT/Codex is chatgpt.com OAuth managed by its own transport).
PROVIDER_BASE_URL = {
    "ollama_local": None,
    "ollama_cloud": "https://ollama.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "openai_compatible": None,
    "openai_codex": "",
}


def resolve_provider_from_base_url(base_url: str) -> str:
    """Map a provider's base_url back to its canonical cfg key. Returns
    '' if base_url is empty. Provider identity comes from the base_url host,
    never from OllamaProvider.name (which is "ollama" for both local + cloud).
    """
    if not base_url:
        return ""
    if "chatgpt.com" in base_url:
        return "openai_codex"
    if "ollama.com" in base_url:
        return "ollama_cloud"
    if "openrouter.ai" in base_url:
        return "openrouter"
    if "api.openai.com" in base_url:
        return "openai"
    if ":11434" in base_url:
        return "ollama_local"
    return "openai_compatible"


# Embeddings backend, chosen independently of the chat provider. "auto" keeps
# the historical behaviour of following the chat provider; the rest pin it.
EMBED_BACKEND_ORDER = ("auto", "llamacpp", "ollama", "openai_compatible")
EMBED_BACKEND_LABELS = {
    "auto": "Follow chat provider",
    "llamacpp": "Local llama.cpp (bundled EmbeddingGemma)",
    "ollama": "Ollama",
    "openai_compatible": "OpenAI Compatible",
}
