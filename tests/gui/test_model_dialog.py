"""Tests for the Phase-2 model-selector dialog and Model menu wiring."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from grc_agent.model_manager import CachedModel, SystemSpecs
from grc_agent_gui.main_window import MainWindow
from grc_agent_gui.model_dialog import (
    ModelDialog,
    ModelDialogSelection,
    _format_size_compact,
    _format_specs_compact,
    _truncate_for_label,
    discover_models_for_dialog,
)
from PySide6.QtWidgets import QApplication


def _make_cached_model(
    repo: str,
    filename: str,
    size_bytes: int = 1024 * 1024 * 100,
    mtime: datetime | None = None,
) -> CachedModel:
    """Build a CachedModel for tests, avoiding real filesystem stat calls."""
    if mtime is None:
        mtime = datetime(2026, 1, 1, tzinfo=UTC)
    return CachedModel(
        hf_repo=repo,
        filename=filename,
        snapshot_path=Path("/tmp") / filename,
        size_bytes=size_bytes,
        last_used=mtime,
    )


class FormatHelpersTests(unittest.TestCase):
    def test_format_size_compact(self) -> None:
        self.assertEqual(_format_size_compact(None), "n/a")
        self.assertEqual(_format_size_compact(512), "0 KiB")
        self.assertEqual(_format_size_compact(2 * 1024 * 1024), "2.0 MiB")
        self.assertEqual(_format_size_compact(3 * 1024 * 1024 * 1024), "3.00 GiB")

    def test_truncate_short_text_unchanged(self) -> None:
        self.assertEqual(_truncate_for_label("short"), "short")
        # Length exactly at the boundary.
        text = "x" * 32
        self.assertEqual(_truncate_for_label(text, 32), text)

    def test_truncate_long_text_uses_middle_ellipsis(self) -> None:
        text = "abcdefghij" * 4  # 40 chars
        out = _truncate_for_label(text, 16)
        self.assertIn("…", out)
        self.assertLessEqual(len(out), 16)

    def test_format_specs_compact_handles_unknown(self) -> None:
        specs = SystemSpecs(
            gpu_name=None,
            gpu_vram_bytes=None,
            ram_bytes=None,
            cpu_name=None,
            cpu_cores_logical=None,
        )
        compact, tooltip = _format_specs_compact(specs)
        self.assertIn("GPU: unknown", compact)
        self.assertIn("CPU: unknown", compact)
        self.assertIn("unknown", tooltip)

    def test_format_specs_compact_renders_full_names_in_tooltip(self) -> None:
        specs = SystemSpecs(
            gpu_name="NVIDIA GeForce RTX 4090",
            gpu_vram_bytes=24 * 1024 * 1024 * 1024,
            ram_bytes=64 * 1024 * 1024 * 1024,
            cpu_name="AMD Ryzen 9 7950X",
            cpu_cores_logical=32,
        )
        compact, tooltip = _format_specs_compact(specs)
        # Both compact and tooltip contain the model names (compact is
        # under the 32-char default max_length so nothing is truncated
        # here). The contract is that the cores count lives in the
        # tooltip but is NOT redundant in the compact label.
        self.assertIn("RTX 4090", compact)
        self.assertIn("RTX 4090", tooltip)
        self.assertIn("Ryzen 9 7950X", compact)
        self.assertIn("Ryzen 9 7950X", tooltip)
        # Cores count lives only in the tooltip.
        self.assertIn("32 logical cores", tooltip)
        self.assertNotIn("logical cores", compact)


class DiscoverModelsForDialogTests(unittest.TestCase):
    """Helper uses the same path-resolution rules as ``discover_cached_models``."""

    def test_returns_empty_for_missing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                discover_models_for_dialog(
                    hf_cache=Path(tmp) / "no_such_hf",
                    models_dir=Path(tmp) / "no_such_models",
                ),
                [],
            )


class ModelDialogTests(unittest.TestCase):
    """Build the dialog, exercise the confirm-strip state, emit the signal."""

    @classmethod
    def setUpClass(cls) -> None:
        # QApplication is required for any QWidget; reuse a single instance.
        cls._app = QApplication.instance() or QApplication([])

    def _build_dialog(
        self,
        models: list[CachedModel],
        current: CachedModel | None,
    ) -> tuple[ModelDialog, list[ModelDialogSelection]]:
        captured: list[ModelDialogSelection] = []
        dialog = ModelDialog(
            current_model=current,
            models=models,
            specs=SystemSpecs(
                gpu_name=None,
                gpu_vram_bytes=None,
                ram_bytes=None,
                cpu_name=None,
                cpu_cores_logical=None,
            ),
        )
        dialog.model_accepted.connect(captured.append)
        return dialog, captured

    def test_switch_button_disabled_when_only_current_selected(self) -> None:
        m1 = _make_cached_model("org/repo", "model-a.gguf")
        dialog, captured = self._build_dialog([m1], current=m1)
        self.assertFalse(dialog.switch_btn.isEnabled())
        self.assertEqual(captured, [])

    def test_switch_button_enabled_when_different_model_picked(self) -> None:
        m1 = _make_cached_model("org/repo", "model-a.gguf")
        m2 = _make_cached_model("org/repo", "model-b.gguf")
        dialog, captured = self._build_dialog([m1, m2], current=m1)
        # Switch to m2 (index 1).
        dialog.combo.setCurrentIndex(1)
        self.assertTrue(dialog.switch_btn.isEnabled())

    def test_accept_emits_model_accepted_signal(self) -> None:
        m1 = _make_cached_model("org/repo", "model-a.gguf")
        m2 = _make_cached_model("org/repo", "model-b.gguf")
        dialog, captured = self._build_dialog([m1, m2], current=m1)
        dialog.combo.setCurrentIndex(1)
        dialog._on_switch_clicked()
        self.assertEqual(len(captured), 1)
        selection = captured[0]
        assert isinstance(selection, ModelDialogSelection)
        self.assertEqual(selection.cached_model.filename, "model-b.gguf")
        self.assertEqual(selection.alias_override, "")

    def test_empty_model_list_disables_everything(self) -> None:
        dialog, _ = self._build_dialog([], current=None)
        self.assertFalse(dialog.combo.isEnabled())
        self.assertFalse(dialog.switch_btn.isEnabled())
        self.assertIn("No .gguf files found", dialog._cache_label_text())

    def test_dialog_does_not_raise_when_no_specs_provided(self) -> None:
        # Specs is optional; when None we fall back to list_system_specs().
        m1 = _make_cached_model("org/repo", "model-a.gguf")
        with mock.patch(
            "grc_agent_gui.model_dialog.list_system_specs",
            return_value=SystemSpecs(
                gpu_name="Test GPU",
                gpu_vram_bytes=8 * 1024 * 1024 * 1024,
                ram_bytes=16 * 1024 * 1024 * 1024,
                cpu_name="Test CPU",
                cpu_cores_logical=8,
            ),
        ):
            dialog = ModelDialog(current_model=m1, models=[m1])
            self.assertIn("Test GPU", dialog.specs_label.toolTip())


class MainWindowModelMenuTests(unittest.TestCase):
    """Wire-up tests for the new ``Model`` menu in the menubar."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def _build_window(
        self, llama_config: object | None = None
    ) -> MainWindow:
        mock_agent = mock.MagicMock()
        mock_agent.session = None
        mock_provider = mock.MagicMock()
        mock_provider.model = None
        return MainWindow(
            mock_agent,
            provider_config=mock_provider,
            llama_config=llama_config,
        )

    def test_model_menu_is_registered(self) -> None:
        window = self._build_window()
        try:
            actions: list[str] = []
            current_loaded_actions: list = []
            for menu_action in window.menuBar().actions():
                menu = menu_action.menu()
                if menu is None:
                    continue
                for action in menu.actions():
                    text = action.text().replace("&", "")
                    actions.append(text)
                    if text.startswith("Currently loaded:"):
                        current_loaded_actions.append(action)
            self.assertIn("Select Model...", actions)
            self.assertEqual(
                len(current_loaded_actions),
                1,
                f"expected exactly one 'Currently loaded' action, got {actions}",
            )
            # The "Currently loaded" action must be disabled by default.
            self.assertFalse(current_loaded_actions[0].isEnabled())
        finally:
            window.close()

    def test_current_model_menu_uses_alias_when_no_path(self) -> None:
        cfg = mock.MagicMock()
        cfg.model = "Qwen3.5-2B.gguf"
        cfg.model_path = None
        window = self._build_window(llama_config=cfg)
        try:
            window._update_current_model_menu()
            self.assertEqual(
                window.current_model_action.text(),
                "Currently loaded: Qwen3.5-2B.gguf",
            )
        finally:
            window.close()

    def test_current_model_menu_falls_back_to_path_stem(self) -> None:
        cfg = mock.MagicMock()
        cfg.model = ""
        cfg.hf_model = ""
        cfg.model_path = "/tmp/Qwen3.5-9B.gguf"
        window = self._build_window(llama_config=cfg)
        try:
            window._update_current_model_menu()
            self.assertEqual(
                window.current_model_action.text(),
                "Currently loaded: Qwen3.5-9B.gguf",
            )
        finally:
            window.close()

    def test_current_model_menu_uses_hf_model_filename(self) -> None:
        cfg = mock.MagicMock()
        cfg.model = ""
        cfg.hf_model = "unsloth/Qwen3.5-2B-GGUF:Qwen3.5-2B-UD-Q4_K_XL.gguf"
        cfg.model_path = None
        window = self._build_window(llama_config=cfg)
        try:
            window._update_current_model_menu()
            self.assertEqual(
                window.current_model_action.text(),
                "Currently loaded: Qwen3.5-2B-UD-Q4_K_XL.gguf",
            )
        finally:
            window.close()

    def test_resolve_current_model_prefers_hf_repo_match(self) -> None:
        cfg = mock.MagicMock()
        cfg.model = ""
        cfg.hf_model = "unsloth/Qwen3.5-2B-GGUF:Qwen3.5-2B-UD-Q4_K_XL.gguf"
        cfg.model_path = None
        window = self._build_window(llama_config=cfg)
        try:
            a = _make_cached_model("other/repo", "Qwen3.5-2B-UD-Q4_K_XL.gguf")
            b = _make_cached_model("unsloth/Qwen3.5-2B-GGUF", "Qwen3.5-2B-UD-Q4_K_XL.gguf")
            self.assertIs(window._resolve_current_model([a, b]), b)
        finally:
            window.close()

    def test_open_model_dialog_dispatches_swap_to_worker(self) -> None:
        """Phase 3: confirming a model selection dispatches a background
        ``ModelSwapRunnable`` rather than emitting a placeholder message.
        The launcher's ``swap_model`` is patched so the test never spawns
        a real ``llama-server`` process.
        """
        cfg = mock.MagicMock()
        cfg.model = "Current.gguf"
        cfg.model_path = None
        cfg.hf_model = "org/repo:Current.gguf"
        cfg.models_dir = None
        window = self._build_window(llama_config=cfg)
        try:
            current = _make_cached_model("org/repo", "Current.gguf")
            other = _make_cached_model("org/repo", "Other.gguf")
            with mock.patch(
                "grc_agent_gui.main_window.discover_models_for_dialog",
                return_value=[current, other],
            ):
                window.open_model_dialog()
            self.assertIsNotNone(window._model_dialog)
            self.assertTrue(window._model_dialog.isVisible())
            self.assertFalse(window._model_dialog.isModal())

            # Stub the swap so it never touches the real launcher.
            fake_result = mock.MagicMock()
            fake_result.model_alias = "Other.gguf"
            fake_result.status = "started"
            fake_result.provider_config = mock.MagicMock()
            fake_result.provider_config.model = "Other.gguf"

            with mock.patch.object(
                window, "_on_model_swap_finished"
            ) as on_finished, mock.patch(
                "grc_agent_gui.main_window.QThreadPool.globalInstance"
            ) as pool_mock:
                pool = mock.MagicMock()
                pool_mock.return_value = pool
                window._model_dialog.combo.setCurrentIndex(1)
                window._model_dialog._on_switch_clicked()
                # The runnable should have been scheduled on the pool.
                self.assertEqual(pool.start.call_count, 1)
                runnable = pool.start.call_args[0][0]
                # Trigger the finished signal synchronously to verify
                # the success path.
                runnable.signals.finished.emit(fake_result)
                on_finished.assert_called_once_with(fake_result)
        finally:
            window.close()

    def test_open_model_dialog_swap_error_surfaces_to_chat(self) -> None:
        cfg = mock.MagicMock()
        cfg.model = "Current.gguf"
        cfg.model_path = None
        cfg.hf_model = "org/repo:Current.gguf"
        cfg.models_dir = None
        window = self._build_window(llama_config=cfg)
        try:
            current = _make_cached_model("org/repo", "Current.gguf")
            other = _make_cached_model("org/repo", "Other.gguf")
            with mock.patch(
                "grc_agent_gui.main_window.discover_models_for_dialog",
                return_value=[current, other],
            ):
                window.open_model_dialog()
            fake_runnable = mock.MagicMock()
            with mock.patch(
                "grc_agent_gui.main_window.ModelSwapRunnable",
                return_value=fake_runnable,
            ), mock.patch(
                "grc_agent_gui.main_window.QThreadPool.globalInstance"
            ) as pool_mock:
                pool = mock.MagicMock()
                pool_mock.return_value = pool
                window._model_dialog.combo.setCurrentIndex(1)
                window._model_dialog._on_switch_clicked()
                # Emit the error signal and verify the chat widget
                # received a swap-failed note.
                window._on_model_swap_error("server timed out")
                history = window.chat_widget.get_history()
                self.assertTrue(
                    any(
                        "swap failed" in (msg.get("text") or "").lower()
                        for msg in history
                    ),
                    history,
                )
        finally:
            window.close()

    def test_swap_finished_updates_hf_model_to_new_repo_filename(self) -> None:
        """F4 regression: ``_on_model_swap_finished`` must rebuild the
        in-memory ``hf_model`` from the originally-selected
        ``CachedModel.hf_repo:filename``, not from
        ``provider_config.model`` (which is the bare alias).
        """
        from grc_agent.config import LlamaConfig

        cfg = LlamaConfig(
            server_url="http://127.0.0.1:8080",
            model="Current.gguf",
            hf_model="old/repo:Current.gguf",
            model_path=None,
            device="CUDA0",
            gpu_layers=999,
            desired_context_tokens=120000,
            startup_timeout_seconds=300.0,
            max_tokens=4096,
            max_tool_rounds=8,
            temperature=0.0,
            enable_thinking=False,
            request_timeout_seconds=60.0,
            log_retention_days=7,
            models_dir=None,
        )
        window = self._build_window(llama_config=cfg)
        try:
            current = _make_cached_model("old/repo", "Current.gguf")
            new_model = _make_cached_model("new/repo", "new-model.gguf")
            fake_result = mock.MagicMock()
            fake_result.model_alias = "new-model.gguf"
            fake_result.status = "started"
            fake_result.provider_config = mock.MagicMock()
            # Crucially, ``provider_config.model`` is the bare alias,
            # NOT the ``hf_repo:filename`` token.
            fake_result.provider_config.model = "new-model.gguf"
            with mock.patch(
                "grc_agent_gui.main_window.discover_models_for_dialog",
                return_value=[current, new_model],
            ):
                window.open_model_dialog()
            # Pre-seed the pending selection as the runnable would
            # have done after the user confirmed a non-current model.
            from grc_agent_gui.model_dialog import ModelDialogSelection
            window._pending_swap_selection = ModelDialogSelection(
                cached_model=new_model, alias_override=""
            )
            window._on_model_swap_finished(fake_result)
            self.assertEqual(
                window.llama_config.hf_model, "new/repo:new-model.gguf"
            )
            self.assertEqual(window.llama_config.model, "new-model.gguf")
            # Runnable + selection refs cleared so the next swap is clean.
            self.assertIsNone(window._model_swap_runnable)
            self.assertIsNone(window._pending_swap_selection)
        finally:
            window.close()

    def test_select_model_action_disabled_during_turn(self) -> None:
        cfg = mock.MagicMock()
        cfg.model = "Current.gguf"
        cfg.model_path = None
        window = self._build_window(llama_config=cfg)
        try:
            # Simulate the lifecycle the worker uses.
            window.select_model_action.setEnabled(True)
            window.on_worker_started()
            self.assertFalse(window.select_model_action.isEnabled())
            window.on_turn_finished({"assistant_text": ""})
            self.assertTrue(window.select_model_action.isEnabled())
        finally:
            window.close()

    def test_reopen_raises_existing_dialog(self) -> None:
        cfg = mock.MagicMock()
        cfg.model = None
        cfg.model_path = None
        cfg.models_dir = None
        window = self._build_window(llama_config=cfg)
        try:
            with mock.patch(
                "grc_agent_gui.main_window.discover_models_for_dialog",
                return_value=[],
            ):
                window.open_model_dialog()
                first = window._model_dialog
                # Second open should reuse, not replace.
                window.open_model_dialog()
                self.assertIs(window._model_dialog, first)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
