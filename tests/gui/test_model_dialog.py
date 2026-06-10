"""Tests for the model-selector dialog — Ollama native."""

from __future__ import annotations

import unittest
from unittest import mock

from grc_agent_gui.model_dialog import (
    ModelDialog,
    ModelDialogSelection,
    _format_refresh_label,
)
from PySide6.QtWidgets import QApplication


class FormatHelpersTests(unittest.TestCase):
    def test_format_refresh_label_empty(self) -> None:
        self.assertIn("no models discovered", _format_refresh_label(0))
        self.assertIn("Ollama server unreachable", _format_refresh_label(0))

    def test_format_refresh_label_with_models(self) -> None:
        label = _format_refresh_label(3)
        self.assertIn("3 model(s)", label)
        self.assertIn("Ollama", label)


class ModelDialogTests(unittest.TestCase):
    """Build the dialog, exercise client switching, confirm strip state."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    @mock.patch("grc_agent_gui.model_dialog.discover_ollama_models", return_value=[])
    def test_empty_model_list_disables_everything(self, _mock: mock.MagicMock) -> None:
        dialog = ModelDialog(
            current_backend="ollama",
            current_ollama_model="",
        )
        self.assertIn("no models discovered", dialog.refresh_label.text())
        self.assertFalse(dialog.switch_btn.isEnabled())
        dialog.close()

    @mock.patch("grc_agent_gui.model_dialog.discover_ollama_models", return_value=["llama3.2", "qwen2.5"])
    def test_switch_button_disabled_when_same_model_loaded(self, _mock: mock.MagicMock) -> None:
        dialog = ModelDialog(
            current_backend="ollama",
            current_ollama_model="llama3.2",
        )
        self.assertFalse(dialog.switch_btn.isEnabled())
        self.assertIn("Already loaded", dialog.alias_input_note.text())
        dialog.close()

    @mock.patch("grc_agent_gui.model_dialog.discover_ollama_models", return_value=["llama3.2", "qwen2.5"])
    def test_switch_button_enabled_when_different_model_picked(self, _mock: mock.MagicMock) -> None:
        dialog = ModelDialog(
            current_backend="ollama",
            current_ollama_model="llama3.2",
        )
        dialog.model_combo.setCurrentText("qwen2.5")
        self.assertTrue(dialog.switch_btn.isEnabled())
        self.assertIn("Switch Ollama model", dialog.alias_input_note.text())
        dialog.close()

    @mock.patch("grc_agent_gui.model_dialog.discover_ollama_models", return_value=["llama3.2"])
    def test_accept_emits_model_accepted_signal_ollama(self, _mock: mock.MagicMock) -> None:
        captured: list[ModelDialogSelection] = []
        dialog = ModelDialog(
            current_backend="ollama",
            current_ollama_model="qwen2.5",
        )
        dialog.model_accepted.connect(captured.append)
        dialog._on_switch_clicked()
        self.assertEqual(len(captured), 1)
        selection = captured[0]
        self.assertEqual(selection.backend, "ollama")
        self.assertEqual(selection.ollama_model_name, "qwen2.5")
        dialog.close()

    @mock.patch("grc_agent_gui.model_dialog.discover_ollama_models", return_value=[])
    def test_accept_emits_openrouter_selection(self, _mock: mock.MagicMock) -> None:
        captured: list[ModelDialogSelection] = []
        dialog = ModelDialog(
            current_backend="ollama",
            current_ollama_model="llama3.2",
        )
        dialog.model_accepted.connect(captured.append)
        # Switch to openrouter
        idx = dialog.client_combo.findData("openrouter")
        dialog.client_combo.setCurrentIndex(idx)
        dialog._on_switch_clicked()
        self.assertEqual(len(captured), 1)
        selection = captured[0]
        self.assertEqual(selection.backend, "openrouter")
        self.assertIsNone(selection.ollama_model_name)
        dialog.close()

    @mock.patch("grc_agent_gui.model_dialog.discover_ollama_models", return_value=["llama3.2"])
    def test_switch_from_ollama_to_openrouter_enables_button(self, _mock: mock.MagicMock) -> None:
        dialog = ModelDialog(
            current_backend="ollama",
            current_ollama_model="llama3.2",
        )
        idx = dialog.client_combo.findData("openrouter")
        dialog.client_combo.setCurrentIndex(idx)
        self.assertTrue(dialog.switch_btn.isEnabled())
        self.assertIn("Switch client to OpenRouter", dialog.alias_input_note.text())
        dialog.close()

    @mock.patch("grc_agent_gui.model_dialog.discover_ollama_models", return_value=["llama3.2"])
    def test_openrouter_same_backend_disables_button(self, _mock: mock.MagicMock) -> None:
        dialog = ModelDialog(
            current_backend="openrouter",
            current_ollama_model="",
        )
        idx = dialog.client_combo.findData("openrouter")
        dialog.client_combo.setCurrentIndex(idx)
        self.assertFalse(dialog.switch_btn.isEnabled())
        self.assertIn("Already loaded", dialog.alias_input_note.text())
        dialog.close()


if __name__ == "__main__":
    unittest.main()
