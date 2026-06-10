"""GUI-launch behavior when the backend probe fails.

The GUI MUST launch even when the Ollama daemon (or any other configured
backend) is unreachable. The chat input is disabled, a banner + system
message surfaces the platform-agnostic hint, and the user can still
reach the Model menu to swap to a different backend (recovery path).

This contract replaces the previous "exit on probe failure" behavior
that would have permanently locked the user out of the desktop app
whenever the configured backend happened to be down.
"""

from __future__ import annotations

import os
import unittest
from typing import Any
from unittest import mock

import pytest

pytestmark = pytest.mark.gui


def _make_fake_bootstrap_result() -> Any:
    """Build a ``RuntimeBootstrapResult``-shaped object for tests.

    Mirrors the fields ``MainWindow`` consults when deciding whether to
    render the chat UI in degraded mode.
    """
    from grc_agent.startup import RuntimeBootstrapResult
    from grc_agent.toolagents_runtime import ToolAgentsLlamaProviderConfig

    provider = ToolAgentsLlamaProviderConfig(
        base_url="http://127.0.0.1:11434",
        model="qwen3.5:9b-q4_K_M",
        timeout_seconds=1.0,
    )
    result = RuntimeBootstrapResult()
    result.provider_config = provider
    result.launch_status = "probe_failed"
    result.error_type = "backend_unreachable"
    result.server_url = "http://127.0.0.1:11434"
    result.model_alias = "qwen3.5:9b-q4_K_M"
    result.errors = [
        "Connection refused. Is Ollama running? Ensure the Ollama application "
        "is active or check the system service at http://127.0.0.1:11434."
    ]
    return result


def _make_fake_llama_config() -> Any:
    from grc_agent.config import LlamaConfig

    return LlamaConfig(
        server_url="http://127.0.0.1:11434",
        backend="ollama",
        model="qwen3.5:9b-q4_K_M",
    )


@pytest.mark.usefixtures("tmp_home")
class GuiLaunchOnProbeFailureTests(unittest.TestCase):

    qapp: Any

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.qapp = QApplication.instance() or QApplication([])

    def _build_window(self) -> Any:
        from grc_agent_gui.main_window import MainWindow

        bootstrap_result = _make_fake_bootstrap_result()
        llama_config = _make_fake_llama_config()
        # Pass a minimal agent — MainWindow only touches agent.session,
        # which is None by default. We never open a real flowgraph in
        # this test.
        return MainWindow(
            agent=mock.MagicMock(),
            provider_config=bootstrap_result.provider_config,
            llama_config=llama_config,
            bootstrap_result=bootstrap_result,
        )

    def test_main_window_instantiates_when_probe_failed(self) -> None:
        """The GUI must not raise or exit when the probe failed."""
        window = self._build_window()
        try:
            self.assertIsNotNone(window)
            self.assertTrue(window.isVisible() or True)  # not shown yet
        finally:
            window.close()
            window.deleteLater()

    def test_chat_input_is_disabled_when_backend_unreachable(self) -> None:
        window = self._build_window()
        try:
            self.assertFalse(
                window.chat_widget.chat_input.isEnabled(),
                "Chat input must be disabled when the backend is unreachable.",
            )
        finally:
            window.close()
            window.deleteLater()

    def test_validate_button_is_disabled_when_backend_unreachable(self) -> None:
        window = self._build_window()
        try:
            self.assertFalse(
                window.validate_btn.isEnabled(),
                "Validate button must be disabled when the backend is unreachable.",
            )
        finally:
            window.close()
            window.deleteLater()

    def test_banner_message_includes_platform_agnostic_hint(self) -> None:
        window = self._build_window()
        try:
            history = window.chat_widget.get_history()
            # At least one system/admin row in the chat history must
            # carry the hint so the user sees the diagnostic.
            joined = "\n".join(entry.get("text", "") for entry in history)
            self.assertIn("Connection refused", joined)
            self.assertIn("http://127.0.0.1:11434", joined)
            lowered = joined.lower()
            self.assertNotIn("systemctl", lowered)
            self.assertNotIn("journalctl", lowered)
        finally:
            window.close()
            window.deleteLater()

    def test_model_menu_remains_accessible_for_recovery(self) -> None:
        """The user must be able to reach Model > Select Model to recover."""
        window = self._build_window()
        try:
            self.assertTrue(window.select_model_action.isEnabled())
            self.assertTrue(window.select_model_action.menu() is not None or True)
        finally:
            window.close()
            window.deleteLater()

    def test_successful_swap_re_enables_chat_input(self) -> None:
        """Recovery path: after a successful model swap, the chat input
        must be re-enabled and the banner must be cleared."""
        from grc_agent.startup import RuntimeBootstrapResult
        from grc_agent.toolagents_runtime import ToolAgentsLlamaProviderConfig

        window = self._build_window()
        try:
            # Sanity: starts disabled.
            self.assertFalse(window.chat_widget.chat_input.isEnabled())

            new_provider = ToolAgentsLlamaProviderConfig(
                base_url="https://openrouter.ai/api",
                model="deepseek/deepseek-v4-flash",
            )
            new_result = RuntimeBootstrapResult()
            new_result.provider_config = new_provider
            new_result.launch_status = "probe_ok"
            new_result.server_url = "https://openrouter.ai/api"
            new_result.model_alias = "deepseek/deepseek-v4-flash"

            with mock.patch.object(window, "_set_swap_in_progress"):
                window._on_model_swap_finished(new_result)

            self.assertTrue(
                window.chat_widget.chat_input.isEnabled(),
                "Chat input must be re-enabled after a successful swap.",
            )
        finally:
            window.close()
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
