"""ModelToolbar widget behavior — provider selection and its side effects.

Ollama is a local server the user must run themselves; OpenRouter is a
cloud API key with nothing local to start. The toolbar surfaces that
difference as a hover hint on the provider selector.
"""

from __future__ import annotations

import os
import re
import unittest
from typing import Any

import pytest

pytestmark = pytest.mark.gui


@pytest.mark.usefixtures("tmp_home")
class ModelToolbarProviderHintTests(unittest.TestCase):
    qapp: Any

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.qapp = QApplication.instance() or QApplication([])

    def test_ollama_backend_shows_server_hint_tooltip(self) -> None:
        from grc_agent_gui.model_toolbar import ModelToolbar

        toolbar = ModelToolbar(backend="ollama", model="gemma4:e4b-it-qat-120k")
        try:
            tooltip = toolbar.provider_combo.toolTip()
            self.assertIn("OLLAMA_CONTEXT_LENGTH", tooltip)
            self.assertIn("120000", tooltip)
            # Must not tell the user to manually launch a second server —
            # Ollama is typically already running in the background, and a
            # duplicate `ollama serve` just conflicts with it. Word-boundary
            # match: "...the Ollama server..." is fine, "ollama serve" as a
            # standalone command is not.
            self.assertIsNone(re.search(r"\bollama serve\b", tooltip.lower()))
        finally:
            toolbar.deleteLater()

    def test_openrouter_backend_has_no_server_hint_tooltip(self) -> None:
        """OpenRouter is a cloud API key — there is no local server to
        start, so the Ollama-specific hint must not appear."""
        from grc_agent_gui.model_toolbar import ModelToolbar

        toolbar = ModelToolbar(backend="openrouter", model="deepseek/deepseek-v4-flash")
        try:
            self.assertEqual(toolbar.provider_combo.toolTip(), "")
        finally:
            toolbar.deleteLater()

    def test_switching_provider_updates_the_tooltip(self) -> None:
        from grc_agent_gui.model_toolbar import ModelToolbar

        toolbar = ModelToolbar(backend="openrouter", model="deepseek/deepseek-v4-flash")
        try:
            self.assertEqual(toolbar.provider_combo.toolTip(), "")
            toolbar.set_backend("ollama")
            self.assertIn("OLLAMA_CONTEXT_LENGTH", toolbar.provider_combo.toolTip())
            toolbar.set_backend("openrouter")
            self.assertEqual(toolbar.provider_combo.toolTip(), "")
        finally:
            toolbar.deleteLater()


if __name__ == "__main__":
    unittest.main()
