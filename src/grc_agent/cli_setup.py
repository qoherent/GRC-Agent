"""Interactive provider picker for the CLI.

Single, narrow responsibility: ask the user which LLM provider they
want (Ollama or OpenRouter), persist the choice to user
preferences, and return. No daemon management, no hardware
polling, no model discovery. If Ollama is down, the existing
backend_unreachable path in ``bootstrap_runtime`` handles it.

Skipped when stdin is not a TTY so the CLI remains scriptable.
"""

from __future__ import annotations

import logging

from grc_agent.config import AppConfig

logger = logging.getLogger(__name__)


PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENROUTER = "openrouter"


def _ask_provider() -> str | None:
    """Prompt the user to pick a provider. Returns ``None`` on EOF/Ctrl-C."""
    print("\nGRC Agent: choose an LLM provider")
    print("  [1] Ollama (Local)")
    print("  [2] OpenRouter (Cloud)")
    print("  [q] Quit")
    try:
        choice = input("> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None
    if choice in {"q", "quit", "exit", ""}:
        return None
    if choice in {"1", "ollama", "local"}:
        return PROVIDER_OLLAMA
    if choice in {"2", "openrouter", "cloud", "api"}:
        return PROVIDER_OPENROUTER
    print(f"Unrecognized choice: {choice!r}. Try again.")
    return _ask_provider()


def run_cli_setup(
    *,
    config: AppConfig,
    is_tty: bool,
) -> bool:
    """Show the provider picker if not yet chosen. Returns ``True`` to continue.

    Returns ``False`` if the user quits the picker (caller exits
    cleanly). When ``is_tty`` is ``False`` or the user has already
    chosen a provider in a previous run, returns ``True`` without
    prompting.
    """
    from grc_agent.preferences import (
        load_user_preferences,
        update_provider_chosen,
    )

    prefs = load_user_preferences()
    if prefs.provider_chosen:
        return True
    if not is_tty:
        # Non-interactive: fall back to the config's existing backend.
        # The user can set it explicitly via ``[llama].backend`` in
        # ``grc_agent.toml`` or via the GUI's picker.
        return True

    while True:
        backend = _ask_provider()
        if backend is None:
            return False
        try:
            update_provider_chosen(provider=backend)
        except OSError as exc:
            logger.warning("Failed to persist provider choice: %s", exc)
        return True


__all__ = [
    "PROVIDER_OLLAMA",
    "PROVIDER_OPENROUTER",
    "run_cli_setup",
]
