"""Tests for the shared provider-behavior catalog (grc_agent.ui.providers).

Pure data plus one pure resolver: no GTK, no display. The chat-side
consumers (thinking labels, sign-in gate, preflight hints) read this
table instead of comparing provider id strings.
"""

from grc_agent.ui.providers import provider_behavior


def test_codex_behavior():
    """The Codex backend streams a reasoning summary and gates on sign-in."""
    behavior = provider_behavior("openai_codex")
    assert behavior["thinking_label_streaming"] == "Thinking (summary)..."
    assert behavior["thinking_label"] == "Thought summary (Codex)"
    assert behavior["requires_sign_in"] is True
    assert "Sign in with ChatGPT" in behavior["preflight_hint"]


def test_default_behavior_for_unknown_provider():
    """Unknown providers fall back to the default behavior: raw-thinking
    labels, no sign-in gate, the generic OpenAI-compatible preflight hint.
    The returned dict is a copy — mutating it must not corrupt the table."""
    behavior = provider_behavior("not_a_real_provider")
    assert behavior["thinking_label_streaming"] == "Thinking..."
    assert behavior["thinking_label"] == "Thought"
    assert behavior["requires_sign_in"] is False
    assert behavior["preflight_hint"].format(base_url="http://x:1", provider="p") == (
        "• Ensure your OpenAI-compatible server is running.\n"
        "• Verify endpoint is reachable at http://x:1."
    )
    behavior["requires_sign_in"] = True
    assert provider_behavior("not_a_real_provider")["requires_sign_in"] is False


def test_providers_without_quirks_inherit_default_labels():
    """Only Codex has a thinking-summary quirk; every other catalog entry
    keeps the default thinking labels and no sign-in requirement."""
    from grc_agent.ui.providers import PROVIDER_ORDER

    for provider in PROVIDER_ORDER:
        behavior = provider_behavior(provider)
        if provider == "openai_codex":
            continue
        assert behavior["thinking_label_streaming"] == "Thinking...", provider
        assert behavior["thinking_label"] == "Thought", provider
        assert behavior["requires_sign_in"] is False, provider


def test_preflight_hints_render_without_residual_placeholders():
    """Every provider-specific preflight hint formats cleanly with the
    base URL and provider id; nothing ships a raw {placeholder}."""
    from grc_agent.ui.providers import PROVIDER_BEHAVIORS

    for provider, behavior in PROVIDER_BEHAVIORS.items():
        rendered = behavior["preflight_hint"].format(base_url="B", provider=provider)
        assert "{" not in rendered and "}" not in rendered, provider
