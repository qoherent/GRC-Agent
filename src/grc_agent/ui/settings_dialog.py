# ruff: noqa: E402
"""Settings dialog for the chat sidebar.

Owns only the dialog *UI* (provider dropdown, model/key entries, Ollama URL +
reasoning checkbox) and the per-provider field-sync logic. On Save it reads the
widget values *before* destroying itself (regression-critical — reading after
destroy returns ''/-1), then hands them to the sidebar's ``on_save`` callback,
which owns preflight, persistence and live-swap.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from ..settings import get_env_value
from .providers import (
    PROVIDER_API_KEY,
    PROVIDER_KEY_PLACEHOLDER,
    PROVIDER_LABELS,
    PROVIDER_MODEL_KEY,
    PROVIDER_MODEL_PLACEHOLDER,
    PROVIDER_ORDER,
)


class SettingsDialog(Gtk.Dialog):
    """Modal Chat Settings dialog. Emits nothing; calls ``on_save`` on APPLY."""

    def __init__(self, toplevel: Gtk.Window | None, cfg: dict, on_save) -> None:
        super().__init__(title="Chat Settings", transient_for=toplevel, modal=True)
        self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.add_button("Save", Gtk.ResponseType.APPLY)
        self.set_default_response(Gtk.ResponseType.APPLY)
        self._on_save = on_save
        self._cfg = cfg

        content = self.get_content_area()
        content.set_spacing(8)
        content.set_border_width(12)

        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        self._build_provider_section(grid)
        grid.attach(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), 0, 4, 2, 1)
        self._build_execution_section(grid)
        grid.attach(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), 0, 8, 2, 1)

        info = Gtk.Label(label="Changes apply immediately on Save.")
        info.get_style_context().add_class("dim-label")

        content.pack_start(grid, False, False, 0)
        content.pack_start(info, False, False, 0)
        content.show_all()

        self.connect("response", self._on_response)

    def _build_provider_section(self, grid: Gtk.Grid) -> None:
        cfg = self._cfg
        hdr = Gtk.Label()
        hdr.set_markup("<b>Provider &amp; Model Configuration</b>")
        hdr.set_xalign(0.0)
        grid.attach(hdr, 0, 0, 2, 1)

        lbl_p = Gtk.Label(label="Provider:")
        lbl_p.set_xalign(0.0)
        lbl_p.set_tooltip_text("Select local Ollama daemon or a cloud provider (OpenRouter / Ollama Cloud)")
        self.provider_combo = Gtk.ComboBoxText()
        self.provider_combo.set_tooltip_text(
            "Select your AI model provider:\n"
            "• Ollama (local) — Local or custom Ollama daemon\n"
            "• OpenRouter (cloud) — OpenRouter cloud API\n"
            "• Ollama Cloud (cloud) — Remote Ollama cloud API"
        )
        for p in PROVIDER_ORDER:
            self.provider_combo.append_text(PROVIDER_LABELS[p])
        self.provider_combo.set_active(PROVIDER_ORDER.index(cfg["provider"]))
        grid.attach(lbl_p, 0, 1, 1, 1)
        grid.attach(self.provider_combo, 1, 1, 1, 1)

        lbl_m = Gtk.Label(label="Model:")
        lbl_m.set_xalign(0.0)
        lbl_m.set_tooltip_text("The specific LLM model ID or tag for chat responses")
        self.model_entry = Gtk.Entry()
        self.model_entry.set_text(cfg["model"])
        self.model_entry.set_hexpand(True)
        self.model_entry.set_activates_default(True)
        self.model_entry.set_tooltip_text(
            "Enter model ID or tag for chat.\n"
            "Examples:\n"
            "• Local Ollama: qwen3.6:35b-a3b-q4_K_M\n"
            "• OpenRouter: deepseek/deepseek-v4-flash\n"
            "• Ollama Cloud: deepseek-v4-flash:cloud"
        )
        grid.attach(lbl_m, 0, 2, 1, 1)
        grid.attach(self.model_entry, 1, 2, 1, 1)

        lbl_k = Gtk.Label(label="API Key:")
        lbl_k.set_xalign(0.0)
        lbl_k.set_tooltip_text("Authentication key required for OpenRouter or Ollama Cloud")
        self.key_entry = Gtk.Entry()
        self.key_entry.set_visibility(False)
        self.key_entry.set_activates_default(True)
        self.key_entry.set_tooltip_text("API key for the selected cloud provider. Not required for local Ollama.")
        grid.attach(lbl_k, 0, 3, 1, 1)
        grid.attach(self.key_entry, 1, 3, 1, 1)

    def _build_execution_section(self, grid: Gtk.Grid) -> None:
        cfg = self._cfg
        hdr = Gtk.Label()
        hdr.set_markup("<b>Ollama &amp; Model Execution Options</b>")
        hdr.set_xalign(0.0)
        grid.attach(hdr, 0, 5, 2, 1)

        self.url_label = Gtk.Label(label="Ollama Base URL:")
        self.url_label.set_xalign(0.0)
        self.url_label.set_tooltip_text("Base URL endpoint for the Ollama daemon (default http://localhost:11434)")
        self.url_entry = Gtk.Entry()
        self.url_entry.set_text(cfg.get("ollama_base_url", "http://localhost:11434"))
        self.url_entry.set_hexpand(True)
        self.url_entry.set_activates_default(True)
        self.url_entry.set_tooltip_text(
            "Base URL for the Ollama daemon (e.g. http://localhost:11434 or http://192.168.1.100:11434)"
        )
        grid.attach(self.url_label, 0, 6, 1, 1)
        grid.attach(self.url_entry, 1, 6, 1, 1)

        lbl_t = Gtk.Label(label="Model Reasoning:")
        lbl_t.set_xalign(0.0)
        lbl_t.set_tooltip_text("Enable or disable model thinking output (think: true/false)")
        self.thinking_check = Gtk.CheckButton(label="Enable reasoning / thinking tags (think: true)")
        self.thinking_check.set_active(cfg.get("ollama_thinking_enabled", True))
        self.thinking_check.set_tooltip_text(
            "Controls whether model reasoning is enabled (think: True/False) for supported Ollama models."
        )
        grid.attach(lbl_t, 0, 7, 1, 1)
        grid.attach(self.thinking_check, 1, 7, 1, 1)

        self.provider_combo.connect("changed", self._sync_provider_fields)
        self._sync_provider_fields(self.provider_combo)

    def _sync_provider_fields(self, combo: Gtk.ComboBoxText) -> None:
        idx = combo.get_active()
        if idx < 0:
            return
        cfg = self._cfg
        p = PROVIDER_ORDER[idx]
        self.model_entry.set_text(cfg.get(PROVIDER_MODEL_KEY[p], ""))
        self.model_entry.set_placeholder_text(f"e.g. {PROVIDER_MODEL_PLACEHOLDER[p]}")
        key_var = PROVIDER_API_KEY[p]
        if key_var:
            self.key_entry.set_text(get_env_value(key_var) or "")
            self.key_entry.set_sensitive(True)
            self.key_entry.set_placeholder_text(PROVIDER_KEY_PLACEHOLDER[p])
        else:
            self.key_entry.set_text("")
            self.key_entry.set_sensitive(False)
            self.key_entry.set_placeholder_text("")

        if p == "ollama":
            self.url_label.set_text("Ollama Base URL:")
            self.url_entry.set_text(cfg.get("ollama_base_url", "http://localhost:11434"))
            self.url_entry.set_sensitive(True)
            self.thinking_check.set_sensitive(True)
        elif p == "openai_compatible":
            self.url_label.set_text("Base URL:")
            self.url_entry.set_text(cfg.get("openai_compatible_base_url", "http://localhost:8080/v1"))
            self.url_entry.set_sensitive(True)
            self.thinking_check.set_sensitive(False)
        elif p == "ollama_cloud":
            self.url_label.set_text("Ollama Base URL:")
            self.url_entry.set_sensitive(False)
            self.thinking_check.set_sensitive(True)
        else:
            self.url_label.set_text("Base URL:")
            self.url_entry.set_sensitive(False)
            self.thinking_check.set_sensitive(False)

    def _collect(self) -> tuple:
        idx = self.provider_combo.get_active()
        provider = PROVIDER_ORDER[idx] if idx >= 0 else "ollama"
        model = self.model_entry.get_text().strip()
        key_var = PROVIDER_API_KEY.get(provider)
        key_val = self.key_entry.get_text().strip()
        ollama_url = self.url_entry.get_text().strip()
        thinking = self.thinking_check.get_active()
        return provider, model, key_var, key_val, ollama_url, thinking

    def _on_response(self, _dlg: Gtk.Dialog, response: int) -> None:
        # Read widget values BEFORE destroy — reading after destroy returns
        # ''/-1 and silently drops the model name / API key.
        values = self._collect() if response == Gtk.ResponseType.APPLY else None
        self.destroy()
        if response != Gtk.ResponseType.APPLY:
            return
        self._on_save(*values)
