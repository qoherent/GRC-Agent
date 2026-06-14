"""Tests for the provider-picker dialog."""

from __future__ import annotations

import os
import unittest
from typing import Any

import pytest

pytestmark = pytest.mark.gui


@pytest.mark.usefixtures("tmp_home")
class ProviderPickerDialogTests(unittest.TestCase):

    qapp: Any

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.qapp = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from grc_agent_gui.provider_picker_dialog import (
            PROVIDER_OLLAMA,
            PROVIDER_OPENROUTER,
            ProviderPickerDialog,
        )
        self.PROVIDER_OLLAMA = PROVIDER_OLLAMA
        self.PROVIDER_OPENROUTER = PROVIDER_OPENROUTER
        self.dialog = ProviderPickerDialog()

    def tearDown(self) -> None:
        self.dialog.close()
        self.dialog.deleteLater()

    def test_default_selection_is_ollama(self) -> None:
        self.assertEqual(self.dialog.selected_backend(), self.PROVIDER_OLLAMA)
        self.assertTrue(self.dialog.ollama_radio.isChecked())

    def test_continue_emits_chosen_signal_with_ollama(self) -> None:
        captured: list[Any] = []
        self.dialog.provider_chosen.connect(captured.append)
        self.dialog.continue_btn.click()
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].backend, self.PROVIDER_OLLAMA)

    def test_continue_emits_chosen_signal_with_openrouter(self) -> None:
        captured: list[Any] = []
        self.dialog.provider_chosen.connect(captured.append)
        self.dialog.openrouter_radio.setChecked(True)
        self.dialog.continue_btn.click()
        self.assertEqual(captured[0].backend, self.PROVIDER_OPENROUTER)

    def test_cancel_emits_cancelled_signal(self) -> None:
        counter = {"n": 0}

        def _on_cancel() -> None:
            counter["n"] += 1

        self.dialog.provider_cancelled.connect(_on_cancel)
        self.dialog.cancel_btn.click()
        self.assertEqual(counter["n"], 1)

    def test_continue_returns_dialog_accepted(self) -> None:
        self.dialog.continue_btn.click()
        self.assertEqual(self.dialog.result(), self.dialog.DialogCode.Accepted)

    def test_cancel_returns_dialog_rejected(self) -> None:
        self.dialog.cancel_btn.click()
        self.assertEqual(self.dialog.result(), self.dialog.DialogCode.Rejected)


if __name__ == "__main__":
    unittest.main()
