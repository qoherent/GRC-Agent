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
    "anthropic",
    "google",
    "groq",
    "mistral",
    "cohere",
    "xai",
    "openai_codex",
)

# Full human-readable labels for the Settings dialog.
PROVIDER_LABELS = {
    "ollama_local": "Ollama (local)",
    "ollama_cloud": "Ollama Cloud",
    "openrouter": "OpenRouter",
    "openai": "OpenAI API",
    "openai_compatible": "Other — OpenAI-compatible (llama.cpp, vLLM, LM Studio…)",
    "anthropic": "Anthropic (Claude)",
    "google": "Google (Gemini)",
    "groq": "Groq",
    "mistral": "Mistral",
    "cohere": "Cohere",
    "xai": "xAI (Grok)",
    "openai_codex": "ChatGPT Plus/Pro (Codex)",
}

# Which .env key holds the per-provider model id (see settings.py).
PROVIDER_MODEL_KEY = {
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
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
    "xai": "XAI_API_KEY",
    "openai_codex": None,
}

# Providers whose API key is OPTIONAL: they have a key var above (a self-hosted
# endpoint may still require auth) but work without one, so a missing key is not
# a startup error. Kept here with the other provider facts rather than as an
# inline literal at the one call site, and checked against PROVIDER_API_KEY so a
# provider that loses its key var cannot linger in this set.
PROVIDER_KEY_OPTIONAL = frozenset({"ollama_local", "openai_compatible"})
assert all(PROVIDER_API_KEY.get(p) for p in PROVIDER_KEY_OPTIONAL), (
    "PROVIDER_KEY_OPTIONAL entries must still declare a key var in PROVIDER_API_KEY"
)


PROVIDER_MODEL_PLACEHOLDER = {
    "ollama_local": "qwen3.8:latest",
    # Both ollama providers share one model key (OLLAMA_CHAT_MODEL), so the
    # placeholder must not suggest a second, drifted default.
    "ollama_cloud": "qwen3.8:latest",
    "openrouter": "deepseek/deepseek-v4-flash, anthropic/claude-sonnet-5…",
    "openai": "gpt-5.6-terra — click Load to list yours",
    "openai_compatible": "model id served by your endpoint",
    "anthropic": "claude-sonnet-5 — click Load to list yours",
    "google": "gemini-3.7-flash — click Load to list yours",
    "groq": "llama-3.3-70b-versatile — click Load to list yours",
    "mistral": "codestral-latest — click Load to list yours",
    "cohere": "north-mini-code-1-0 — click Load to list yours",
    "xai": "grok-4.6 — click Load to list yours",
    "openai_codex": "gpt-5.6-luna — click Load to list yours",
}

PROVIDER_KEY_PLACEHOLDER = {
    "ollama_local": "Optional (only if your Ollama requires auth)",
    "ollama_cloud": "Ollama Cloud API key (sk-…)",
    "openrouter": "OpenRouter API key (sk-or-…)",
    "openai": "OpenAI API key (sk-…)",
    "openai_compatible": "API key (optional for local endpoints)",
    "anthropic": "Anthropic API key (sk-ant-…)",
    "google": "Google AI Studio API key (AIza…)",
    "groq": "Groq API key (gsk_…)",
    "mistral": "Mistral API key",
    "cohere": "Cohere API key",
    "xai": "xAI API key (xai-…)",
    "openai_codex": "",
}

# Short labels for the toolbar badge — PROVIDER_LABELS forms are too long.
# Local and cloud Ollama get distinct badges so a saved switch is visibly
# applied (the old single "ollama" badge was indistinguishable).
PROVIDER_BADGE_LABEL = {
    "ollama_local": "Ollama Local",
    "ollama_cloud": "Ollama Cloud",
    "openrouter": "OpenRouter",
    "openai": "OpenAI",
    "openai_compatible": "OpenAI Compat",
    "anthropic": "Anthropic",
    "google": "Gemini",
    "groq": "Groq",
    "mistral": "Mistral",
    "cohere": "Cohere",
    "xai": "xAI",
    "openai_codex": "ChatGPT",
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
    "anthropic": "https://api.anthropic.com/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta",
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "cohere": "https://api.cohere.com/v1",
    "xai": "https://api.x.ai/v1",
    "openai_codex": "",
}


# The `save_settings` keyword each user-editable endpoint persists under.
# Derived-consistent with PROVIDER_BASE_URL above: exactly the providers whose
# canonical URL is None (i.e. user-editable) appear here, asserted at import so
# adding an editable provider to one table without the other fails loudly
# instead of silently dropping the user's URL on save.
PROVIDER_BASE_URL_SETTING = {
    "ollama_local": "ollama_base_url",
    "openai_compatible": "openai_compatible_base_url",
}
assert set(PROVIDER_BASE_URL_SETTING) == {
    p for p, url in PROVIDER_BASE_URL.items() if url is None
}, "PROVIDER_BASE_URL_SETTING must cover exactly the user-editable providers"


# Host fragment -> provider id, for mapping a running model's base_url back
# to its canonical cfg key (the toolbar badge). One uniform table — no
# per-provider branches. Local Ollama is keyed on the port because its host
# is user-configurable; everything else is keyed on the service's host.
_BASE_URL_PROVIDER = (
    ("chatgpt.com", "openai_codex"),
    ("ollama.com", "ollama_cloud"),
    ("openrouter.ai", "openrouter"),
    ("api.openai.com", "openai"),
    ("api.anthropic.com", "anthropic"),
    ("generativelanguage.googleapis.com", "google"),
    ("api.groq.com", "groq"),
    ("api.mistral.ai", "mistral"),
    ("api.cohere.com", "cohere"),
    ("api.x.ai", "xai"),
    (":11434", "ollama_local"),
)


def resolve_provider_from_base_url(base_url: str) -> str:
    """Map a provider's base_url back to its canonical cfg key. Returns
    '' if base_url is empty. Provider identity comes from the base_url host,
    never from OllamaProvider.name (which is "ollama" for both local + cloud).
    """
    if not base_url:
        return ""
    for fragment, provider in _BASE_URL_PROVIDER:
        if fragment in base_url:
            return provider
    return "openai_compatible"


# Embeddings backend: "lexical" (fast BM25 keyword matching) or "llamacpp" (local
# vector search via llama.cpp + EmbeddingGemma-300M).
EMBED_BACKEND_ORDER = ("lexical", "llamacpp")
EMBED_BACKEND_LABELS = {
    "lexical": "Lexical Search (Fast BM25 keyword matching, default)",
    "llamacpp": "Local Vector Search (llama.cpp + EmbeddingGemma 300M)",
}
