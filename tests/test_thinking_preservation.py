"""Tests that pin the absence of any ollama params interfering with thinking.

The `gemma4:e4b-it-qat-120k` model (and every other reasoning-capable
model) needs to receive a request shape that lets it emit reasoning
tokens. If we ever re-introduce ``think: False`` in an Ollama
``extra_body`` (or any other knob that suppresses reasoning) the chat UI
silently stops showing the thinking block.

These tests guard against regression in the two call sites that build
Ollama requests:

- :class:`grc_agent.toolagents_runtime.ToolAgentsLlamaProviderConfig` for
  the main chat path (must send ``think: True`` for ollama).
- :func:`grc_agent.runtime.doc_answer._generate_grounded_answer` for the
  docs RAG path (must NOT override Ollama's default thinking behavior).
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


class DocAnswerOllamaThinkingTests(unittest.TestCase):
    """The docs RAG path must not silence Ollama's reasoning mode."""

    def test_doc_answer_ollama_call_does_not_send_think_false(self) -> None:
        """Grep the source: no ``think=False`` / ``"think": False`` in
        ``doc_answer.py``. This is the canonical regression guard."""
        from pathlib import Path

        src = Path(__file__).resolve().parents[1] / "src" / "grc_agent" / "runtime" / "doc_answer.py"
        text = src.read_text(encoding="utf-8")
        # The single, literal false form we want to be gone.
        assert "think\"][\"think\"" not in text, (
            f"doc_answer.py still contains a think override; see:\n{text}"
        )
        # And no inline False assignment either.
        self.assertNotIn("think\"] = False", text)
        self.assertNotIn('"think": False', text)
        self.assertNotIn("'think': False", text)


class ToolAgentsOllamaThinkingTests(unittest.TestCase):
    """The main chat path must keep sending ``think: True`` for ollama."""

    def test_ollama_settings_set_think_true(self) -> None:
        from grc_agent.toolagents_runtime import GrcOpenAIChatAPI, ToolAgentsLlamaProviderConfig

        mock_provider = mock.MagicMock(spec=GrcOpenAIChatAPI)
        mock_settings = mock.MagicMock()
        mock_provider.get_default_settings.return_value = mock_settings

        cfg = ToolAgentsLlamaProviderConfig(
            base_url="http://localhost:11434",
            model="gemma4:e4b-it-qat-120k",
            backend="ollama",
        )
        cfg.create_settings(mock_provider)

        mock_settings.set_value.assert_any_call(
            "extra_body",
            {"think": True},
        )


if __name__ == "__main__":
    unittest.main()
