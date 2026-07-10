"""Persisted user preference for which provider/model the interactive web
agent uses. Separate from the scenario-harness's fixed MODEL/OLLAMA_V1
constants in agent.py, which stay pinned for reproducible benchmarking."""

import json
import os
from pathlib import Path

_DEFAULTS = {
    "provider": "ollama",
    "model": "qwen3.6:35b-a3b-q4_K_M",
    "ollama_model": "qwen3.6:35b-a3b-q4_K_M",
    "openrouter_model": "openai/gpt-4o-mini",
}
_VALID_PROVIDERS = ("ollama", "openrouter")


def settings_path() -> Path:
    override = os.environ.get("GRC_AGENT_CONFIG_PATH")
    if override:
        return Path(override)
    return Path.home() / ".config" / "grc_agent" / "settings.json"


def load_settings() -> dict:
    path = settings_path()
    if not path.exists():
        return dict(_DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULTS)

    res = dict(_DEFAULTS)
    for k in ("provider", "model", "ollama_model", "openrouter_model"):
        if k in data:
            res[k] = data[k]

    # Legacy file compatibility: if individual provider keys aren't set,
    # fill them in from active provider/model state.
    if "ollama_model" not in data and res["provider"] == "ollama":
        res["ollama_model"] = res["model"]
    if "openrouter_model" not in data and res["provider"] == "openrouter":
        res["openrouter_model"] = res["model"]

    return res


def save_settings(provider: str, model: str) -> None:
    if provider not in _VALID_PROVIDERS:
        raise ValueError(f"Unknown provider: {provider!r}")
    if not model.strip():
        raise ValueError("model must be non-empty")

    current = load_settings()
    current["provider"] = provider
    current["model"] = model.strip()
    if provider == "ollama":
        current["ollama_model"] = model.strip()
    elif provider == "openrouter":
        current["openrouter_model"] = model.strip()

    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2), encoding="utf-8")
