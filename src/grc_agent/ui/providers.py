"""Provider catalog shared by the chat sidebar and the settings dialog.

Pure data + one pure resolver. Kept here so both ``chat_sidebar.py`` (the
toolbar's active-provider badge) and ``ui/settings_dialog.py`` (the provider
dropdown) share one source of truth, and so neither imports the other.
"""

# Display order in the Settings dropdown.
PROVIDER_ORDER = ("ollama", "openai_compatible", "openai_codex")

# Full human-readable labels for the Settings dialog.
PROVIDER_LABELS = {
    "ollama": "Ollama (local / cloud)",
    "openai_compatible": "OpenAI Compatible (OpenRouter, llama.cpp, vLLM, OpenAI, Groq, etc.)",
    "openai_codex": "ChatGPT Plus/Pro (Codex)",
}

# Which .env key holds the per-provider model id (see settings.py).
PROVIDER_MODEL_KEY = {
    "ollama": "ollama_model",
    "openai_compatible": "openai_compatible_model",
    "openai_codex": "openai_codex_model",
}

# Which .env key (if any) holds the provider's API key.
# None for openai_codex: it authenticates with an OAuth token pair stored
# outside .env (see providers/openai_codex/credentials.py), so the Save path
# must not write an API-key line for it.
PROVIDER_API_KEY = {
    "ollama": "OLLAMA_API_KEY",
    "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
    "openai_codex": None,
}

PROVIDER_MODEL_PLACEHOLDER = {
    "ollama": "qwen3.6:35b-a3b-q4_K_M (or deepseek-v4-flash:cloud)",
    "openai_compatible": "deepseek/deepseek-v4-flash, qwen2.5-coder:32b, gpt-4o",
    "openai_codex": "gpt-5.1-codex",
}

PROVIDER_KEY_PLACEHOLDER = {
    "ollama": "Optional for local, or paste Ollama Cloud API key",
    "openai_compatible": "Optional for local (e.g. not-required) or paste API key",
    "openai_codex": "",
}

# Short labels for the toolbar badge — PROVIDER_LABELS forms are too long.
PROVIDER_BADGE_LABEL = {
    "ollama": "ollama",
    "openai_compatible": "openai",
    "openai_codex": "chatgpt",
}


def resolve_provider_from_base_url(base_url: str) -> str:
    """Map a provider's base_url back to its canonical cfg key. Returns
    '' if base_url is empty. Provider identity comes from the base_url host,
    never from OllamaProvider.name (which is "ollama" for both local + cloud)."""
    if "chatgpt.com" in base_url:
        return "openai_codex"
    if "11434" in base_url or "ollama.com" in base_url:
        return "ollama"
    if base_url:
        return "openai_compatible"
    return ""


# Embeddings backend, chosen independently of the chat provider. "auto" keeps
# the historical behaviour of following the chat provider; the rest pin it.
EMBED_BACKEND_ORDER = ("auto", "llamacpp", "ollama", "openai_compatible")
EMBED_BACKEND_LABELS = {
    "auto": "Follow chat provider",
    "llamacpp": "Local llama.cpp (bundled EmbeddingGemma)",
    "ollama": "Ollama",
    "openai_compatible": "OpenAI Compatible",
}
