"""Provider catalog shared by the chat sidebar and the settings dialog.

Pure data + one pure resolver. Kept here so both ``chat_sidebar.py`` (the
toolbar's active-provider badge) and ``ui/settings_dialog.py`` (the provider
dropdown) share one source of truth, and so neither imports the other.
"""

# Display order in the Settings dropdown.
PROVIDER_ORDER = ("ollama", "openai_compatible", "openrouter", "ollama_cloud")

# Full human-readable labels for the Settings dialog.
PROVIDER_LABELS = {
    "ollama": "Ollama (local)",
    "openai_compatible": "OpenAI Compatible / llama.cpp (local)",
    "openrouter": "OpenRouter (cloud)",
    "ollama_cloud": "Ollama Cloud (cloud)",
}

# Which .env key holds the per-provider model id (see settings.py).
PROVIDER_MODEL_KEY = {
    "ollama": "ollama_model",
    "openai_compatible": "openai_compatible_model",
    "openrouter": "openrouter_model",
    "ollama_cloud": "ollama_cloud_model",
}

# Which .env key (if any) holds the provider's API key. None = keyless.
PROVIDER_API_KEY = {
    "ollama": None,
    "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "ollama_cloud": "OLLAMA_CLOUD_API_KEY",
}

PROVIDER_MODEL_PLACEHOLDER = {
    "ollama": "qwen3.6:35b-a3b-q4_K_M",
    "openai_compatible": "local-model",
    "openrouter": "deepseek/deepseek-v4-flash",
    "ollama_cloud": "deepseek-v4-flash:cloud",
}

PROVIDER_KEY_PLACEHOLDER = {
    "openai_compatible": "Optional (e.g. not-required)",
    "openrouter": "sk-or-v1-...",
    "ollama_cloud": "Paste your API key",
}

# Short labels for the toolbar badge — PROVIDER_LABELS forms are too long.
PROVIDER_BADGE_LABEL = {
    "ollama": "ollama",
    "openai_compatible": "llama.cpp / openai",
    "ollama_cloud": "ollama cloud",
    "openrouter": "openrouter",
}


def resolve_provider_from_base_url(base_url: str) -> str:
    """Map a provider's base_url back to its canonical cfg key. Returns
    '' if base_url is empty. Provider identity comes from the base_url host,
    never from OllamaProvider.name (which is "ollama" for both local + cloud)."""
    if "openrouter.ai" in base_url:
        return "openrouter"
    if "ollama.com" in base_url:
        return "ollama_cloud"
    if "11434" in base_url:
        return "ollama"
    if base_url:
        return "openai_compatible"
    return ""
