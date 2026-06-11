"""Tests for the three-step Ollama setup flow in the GUI.

Covers:

* Page 2 (OllamaSetupWidget) — always-enabled Confirm with the
  "Confirm you started the ollama externally yourself" text, the
  "Models on this machine" ``QListWidget`` populated from
  ``/api/tags``, the generic ``ollama pull <model_name>`` hint.
* Page 3 (OllamaStartHintWidget) — two vertical copy boxes
  (``ollama serve`` and ``ollama pull <model_name>``); the Start
  button puts ``ollama serve`` on the clipboard; Next re-probes and
  emits ``confirmed`` on success.

These tests follow the same offscreen ``QApplication`` harness used by
``tests/test_gui_launch.py`` so the test suite never needs a real
display server.
"""

from __future__ import annotations

import os
import unittest
from typing import Any
from unittest import mock

import pytest

pytestmark = pytest.mark.gui


CONFIRM_TEXT = "Confirm you started the ollama externally yourself"


def _build_fake_status(
    *,
    server_reachable: bool,
    model_available: bool = False,
    available_models: list[str] | None = None,
    model_alias: str = "",
) -> Any:
    """Build an :class:`OllamaBackendStatus` substitute for the GUI tests."""
    from grc_agent.model_manager import OllamaBackendStatus

    return OllamaBackendStatus(
        server_url="http://localhost:11434",
        server_reachable=server_reachable,
        model_alias=model_alias,
        model_available=model_available,
        available_models=available_models or [],
        start_command="ollama serve",
        pull_command=(
            f"ollama pull {model_alias}" if model_alias else "ollama pull <model_name>"
        ),
        hint=(
            "Ollama is running and model is ready."
            if (server_reachable and model_available)
            else "Use `ollama pull <model_name>` to download any ollama model, "
                 "e.g. `ollama pull qwen3.5:9b-q4_K_M`."
            if server_reachable
            else "Ollama server is not reachable."
        ),
    )


@pytest.mark.usefixtures("tmp_home")
class OllamaSetupWidgetTests(unittest.TestCase):

    qapp: Any

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.qapp = QApplication.instance() or QApplication([])

    def _build_widget(
        self,
        *,
        current_model: str = "",
        server_url: str = "http://localhost:11434",
        fake_status: Any | None = None,
    ) -> Any:
        """Build an :class:`OllamaSetupWidget` with the probe stubbed."""
        from grc_agent_gui.setup_panel import OllamaSetupWidget

        if fake_status is None:
            fake_status = _build_fake_status(
                server_reachable=True,
                model_available=True,
                available_models=["qwen3.5:9b-q4_K_M", "llama3.2:latest"],
            )
        with mock.patch(
            "grc_agent_gui.setup_panel.probe_ollama_backend",
            return_value=fake_status,
        ):
            widget = OllamaSetupWidget(
                server_url=server_url,
                current_model=current_model,
            )
        return widget

    def test_confirm_button_is_always_enabled(self) -> None:
        """The user owns the Ollama lifecycle; the wizard never
        blocks the Confirm button. The text must be the
        self-attestation message."""
        widget = self._build_widget()
        try:
            self.assertTrue(widget.confirm_btn.isEnabled())
            self.assertEqual(widget.confirm_btn.text(), CONFIRM_TEXT)
        finally:
            widget.close()
            widget.deleteLater()

    def test_confirm_is_enabled_even_when_model_missing(self) -> None:
        """Server reachable, no installed models, configured model
        empty — Confirm must still be enabled. The widget never
        gates on probe state."""
        fake_status = _build_fake_status(
            server_reachable=True,
            model_available=False,
            available_models=[],
        )
        widget = self._build_widget(fake_status=fake_status)
        try:
            self.assertTrue(widget.confirm_btn.isEnabled())
        finally:
            widget.close()
            widget.deleteLater()

    def test_confirm_is_enabled_when_server_unreachable(self) -> None:
        fake_status = _build_fake_status(
            server_reachable=False,
            model_available=False,
        )
        widget = self._build_widget(fake_status=fake_status)
        try:
            self.assertTrue(widget.confirm_btn.isEnabled())
        finally:
            widget.close()
            widget.deleteLater()

    def test_models_list_populated_from_api_tags(self) -> None:
        """The "Models on this machine" list is a QListWidget that
        shows the discovered tags from ``/api/tags``."""
        widget = self._build_widget()
        try:
            self.assertEqual(widget.models_list.count(), 2)
            self.assertEqual(widget.models_list.item(0).text(), "qwen3.5:9b-q4_K_M")
            self.assertEqual(widget.models_list.item(1).text(), "llama3.2:latest")
        finally:
            widget.close()
            widget.deleteLater()

    def test_pull_hint_shows_generic_command(self) -> None:
        """The pull-hint label always carries the generic
        ``ollama pull <model_name>`` reminder, even when the user
        hasn't typed anything."""
        widget = self._build_widget()
        try:
            self.assertIn("Use `ollama pull <model_name>`", widget.pull_hint_label.text())
            self.assertIn("ollama pull qwen3.5:9b-q4_K_M", widget.pull_hint_label.text())
        finally:
            widget.close()
            widget.deleteLater()

    def test_confirm_emits_selected_model(self) -> None:
        """Clicking Confirm emits the highlighted list item."""
        widget = self._build_widget()
        try:
            widget.models_list.setCurrentRow(1)
            captured: list[Any] = []
            widget.confirmed.connect(captured.append)
            widget.confirm_btn.click()
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0].model_name, "llama3.2:latest")
        finally:
            widget.close()
            widget.deleteLater()

    def test_confirm_falls_back_to_first_item_with_no_selection(self) -> None:
        """If nothing is highlighted, Confirm falls back to the first
        list item (deterministic, sensible default)."""
        widget = self._build_widget()
        try:
            widget.models_list.clearSelection()
            widget.models_list.setCurrentRow(-1)
            captured: list[Any] = []
            widget.confirmed.connect(captured.append)
            widget.confirm_btn.click()
            self.assertEqual(captured[0].model_name, "qwen3.5:9b-q4_K_M")
        finally:
            widget.close()
            widget.deleteLater()

    def test_server_down_shows_next_button(self) -> None:
        """When the server is unreachable, the **Start the server**
        button is shown so the user can advance to the start-hint
        page. Confirm stays enabled."""
        fake_status = _build_fake_status(
            server_reachable=False,
            model_available=False,
        )
        widget = self._build_widget(fake_status=fake_status)
        try:
            self.assertTrue(widget.confirm_btn.isEnabled())
            self.assertFalse(widget.next_btn.isHidden())
            self.assertIn("not reachable", widget.status_label.text())
        finally:
            widget.close()
            widget.deleteLater()

    def test_next_requested_signal_fires_on_start_server_click(self) -> None:
        """Clicking **Start the server** must emit ``next_requested``
        so :class:`MainWindow` can route to page 3."""
        fake_status = _build_fake_status(
            server_reachable=False,
            model_available=False,
        )
        widget = self._build_widget(fake_status=fake_status)
        try:
            counter = {"n": 0}

            def _on_next() -> None:
                counter["n"] += 1

            widget.next_requested.connect(_on_next)
            widget.next_btn.click()
            self.assertEqual(counter["n"], 1)
        finally:
            widget.close()
            widget.deleteLater()

    def test_refresh_reruns_the_probe(self) -> None:
        """Clicking **Refresh** must call ``probe_ollama_backend``
        again so the user can recover from a transient outage."""
        widget = self._build_widget()
        try:
            with mock.patch(
                "grc_agent_gui.setup_panel.probe_ollama_backend"
            ) as probe_mock:
                probe_mock.return_value = _build_fake_status(
                    server_reachable=True,
                    model_available=True,
                    available_models=["qwen3.5:9b-q4_K_M"],
                )
                widget.refresh_btn.click()
            self.assertGreaterEqual(probe_mock.call_count, 1)
        finally:
            widget.close()
            widget.deleteLater()

    def test_status_detail_label_shows_pull_instructions_when_empty(self) -> None:
        """When the server is reachable but the model list is empty,
        the detail label guides the user to ``ollama pull`` instead
        of the old "configured model is missing" wording."""
        fake_status = _build_fake_status(
            server_reachable=True,
            model_available=False,
            available_models=[],
        )
        widget = self._build_widget(fake_status=fake_status)
        try:
            self.assertIn("ollama pull", widget.status_detail_label.text())
            self.assertNotIn(
                "Configured model", widget.status_detail_label.text()
            )
        finally:
            widget.close()
            widget.deleteLater()


@pytest.mark.usefixtures("tmp_home")
class OllamaStartHintWidgetTests(unittest.TestCase):

    qapp: Any

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.qapp = QApplication.instance() or QApplication([])

    def _build_widget(
        self,
        *,
        current_model: str = "",
        server_url: str = "http://localhost:11434",
    ) -> Any:
        from grc_agent_gui.setup_panel import OllamaStartHintWidget

        return OllamaStartHintWidget(
            server_url=server_url,
            current_model=current_model,
        )

    def _all_line_edits(self, widget: Any) -> list[Any]:
        from PySide6.QtWidgets import QLineEdit

        return widget.findChildren(QLineEdit)

    def test_renders_ollama_serve_and_pull_commands(self) -> None:
        """The two read-only command boxes must carry the literal
        ``ollama serve`` and the generic ``ollama pull
        <model_name>`` strings in vertical order. The pull command
        is generic because there is no "configured model" anymore."""
        widget = self._build_widget()
        try:
            line_edits = self._all_line_edits(widget)
            texts = [le.text() for le in line_edits]
            self.assertIn("ollama serve", texts)
            self.assertIn("ollama pull <model_name>", texts)
            # Both must be read-only — the user must not be able to
            # edit the canonical command, only copy it.
            for le in line_edits:
                self.assertTrue(le.isReadOnly())
        finally:
            widget.close()
            widget.deleteLater()

    def test_copy_button_puts_ollama_serve_on_clipboard(self) -> None:
        """The first copy button must push ``ollama serve`` onto
        the QApplication clipboard."""
        widget = self._build_widget()
        try:
            from PySide6.QtWidgets import QApplication, QPushButton

            clipboard = QApplication.clipboard()
            clipboard.clear()
            start_copy = widget.findChild(QPushButton, "ollamaStartHintStartCopyButton")
            self.assertIsNotNone(start_copy)
            start_copy.click()
            self.assertEqual(clipboard.text(), "ollama serve")
        finally:
            widget.close()
            widget.deleteLater()

    def test_next_button_re_probes_and_emits_confirmed_on_success(self) -> None:
        """When the user has run the commands, clicking **Next**
        re-runs the probe and, on success, emits ``confirmed`` with
        the first installed model so the wizard can advance."""
        widget = self._build_widget()
        try:
            ok_status = _build_fake_status(
                server_reachable=True,
                model_available=True,
                available_models=["qwen3.5:9b-q4_K_M"],
            )
            with mock.patch(
                "grc_agent_gui.setup_panel.probe_ollama_backend",
                return_value=ok_status,
            ):
                captured: list[Any] = []
                widget.confirmed.connect(captured.append)
                widget.next_btn.click()
            self.assertEqual(len(captured), 1)
            self.assertEqual(captured[0].server_url, "http://localhost:11434")
            self.assertEqual(captured[0].model_name, "qwen3.5:9b-q4_K_M")
        finally:
            widget.close()
            widget.deleteLater()

    def test_next_button_shows_red_status_when_still_down(self) -> None:
        """If the server is still unreachable after a Next click,
        the status label turns red and no ``confirmed`` signal
        fires."""
        widget = self._build_widget()
        try:
            still_down = _build_fake_status(
                server_reachable=False,
                model_available=False,
            )
            with mock.patch(
                "grc_agent_gui.setup_panel.probe_ollama_backend",
                return_value=still_down,
            ):
                captured: list[Any] = []
                widget.confirmed.connect(captured.append)
                widget.next_btn.click()
            self.assertEqual(captured, [])
            self.assertIn("Still not reachable", widget.status_label.text())
        finally:
            widget.close()
            widget.deleteLater()

    def test_back_button_emits_cancelled(self) -> None:
        """**Back** must emit ``cancelled`` so MainWindow returns
        to page 2 (the probe widget)."""
        widget = self._build_widget()
        try:
            counter = {"n": 0}

            def _on_cancel() -> None:
                counter["n"] += 1

            widget.cancelled.connect(_on_cancel)
            widget.cancel_btn.click()
            self.assertEqual(counter["n"], 1)
        finally:
            widget.close()
            widget.deleteLater()


if __name__ == "__main__":
    unittest.main()
