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

from .. import embed_runtime
from ..settings import get_env_value
from .embed_runtime_dialog import EmbedRuntimeDialog
from .providers import (
    EMBED_BACKEND_LABELS,
    EMBED_BACKEND_ORDER,
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
        self.set_default_size(520, -1)
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
        grid.attach(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), 0, 9, 2, 1)
        self._build_embeddings_section(grid)

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
        lbl_p.set_tooltip_text(
            "Select Ollama (local/cloud) or OpenAI-compatible endpoint (OpenRouter, llama.cpp, vLLM, OpenAI, Groq, etc.)"
        )
        self.provider_combo = Gtk.ComboBoxText()
        self.provider_combo.set_tooltip_text(
            "Select your AI model provider:\n"
            "• Ollama (local / cloud) — Local Ollama server or Ollama Cloud\n"
            "• OpenAI Compatible — OpenRouter, llama.cpp, vLLM, LM Studio, OpenAI, Groq, etc."
        )
        for p in PROVIDER_ORDER:
            self.provider_combo.append_text(PROVIDER_LABELS[p])
        active_idx = PROVIDER_ORDER.index(cfg["provider"]) if cfg["provider"] in PROVIDER_ORDER else 0
        self.provider_combo.set_active(active_idx)
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
            "• Ollama: qwen3.6:35b-a3b-q4_K_M or deepseek-v4-flash:cloud\n"
            "• OpenAI Compatible: deepseek/deepseek-v4-flash, qwen2.5-coder:32b, gpt-4o"
        )
        grid.attach(lbl_m, 0, 2, 1, 1)
        grid.attach(self.model_entry, 1, 2, 1, 1)

        lbl_k = Gtk.Label(label="API Key:")
        lbl_k.set_xalign(0.0)
        lbl_k.set_tooltip_text("Authentication key (required for cloud endpoints, optional for local servers)")
        self.key_entry = Gtk.Entry()
        self.key_entry.set_visibility(False)
        self.key_entry.set_activates_default(True)
        self.key_entry.set_tooltip_text(
            "API key for the selected provider (e.g. OpenRouter, Ollama Cloud, OpenAI). Optional for local servers."
        )
        grid.attach(lbl_k, 0, 3, 1, 1)
        grid.attach(self.key_entry, 1, 3, 1, 1)

    def _build_execution_section(self, grid: Gtk.Grid) -> None:
        cfg = self._cfg
        hdr = Gtk.Label()
        hdr.set_markup("<b>Endpoint &amp; Execution Options</b>")
        hdr.set_xalign(0.0)
        grid.attach(hdr, 0, 5, 2, 1)

        self.ollama_cloud_check = Gtk.CheckButton(
            label="Use Ollama Cloud (https://ollama.com/v1)"
        )
        is_cloud = "ollama.com" in cfg.get("ollama_base_url", "")
        self.ollama_cloud_check.set_active(is_cloud)
        self.ollama_cloud_check.set_tooltip_text(
            "Use Ollama's official cloud service at https://ollama.com/v1 instead of a local daemon."
        )
        grid.attach(self.ollama_cloud_check, 1, 6, 1, 1)

        self.url_label = Gtk.Label(label="Base URL (default):")
        self.url_label.set_xalign(0.0)
        self.url_label.set_tooltip_text(
            "Base URL endpoint for the model server"
        )
        self.url_entry = Gtk.Entry()
        self.url_entry.set_text(cfg.get("ollama_base_url", "http://localhost:11434"))
        self.url_entry.set_hexpand(True)
        self.url_entry.set_activates_default(True)
        grid.attach(self.url_label, 0, 7, 1, 1)
        grid.attach(self.url_entry, 1, 7, 1, 1)

        lbl_t = Gtk.Label(label="Model Reasoning:")
        lbl_t.set_xalign(0.0)
        lbl_t.set_tooltip_text("Enable or disable model thinking output (think: true/false)")
        self.thinking_check = Gtk.CheckButton(
            label="Enable reasoning / thinking tags (think: true)"
        )
        self.thinking_check.set_active(cfg.get("ollama_thinking_enabled", True))
        self.thinking_check.set_tooltip_text(
            "Controls whether model reasoning is enabled (think: True/False) for supported Ollama models."
        )
        grid.attach(lbl_t, 0, 8, 1, 1)
        grid.attach(self.thinking_check, 1, 8, 1, 1)

        self.provider_combo.connect("changed", self._sync_provider_fields)
        self.ollama_cloud_check.connect("toggled", self._on_ollama_cloud_toggled)
        self._sync_provider_fields(self.provider_combo)

    def _build_embeddings_section(self, grid: Gtk.Grid) -> None:
        """Embeddings backend, chosen independently of the chat provider.

        These are separate concerns: a chat endpoint that speaks the OpenAI
        API need not implement /v1/embeddings (llama-server without
        `--embeddings` answers 501), and when embeddings fail the knowledge
        base silently falls back to lexical search. The bundled llama.cpp
        runtime makes vector search work with nothing installed system-wide.
        """
        hdr = Gtk.Label()
        hdr.set_markup("<b>Knowledge Base (RAG) Embeddings</b>")
        hdr.set_xalign(0.0)
        grid.attach(hdr, 0, 10, 2, 1)

        lbl_e = Gtk.Label(label="Embeddings:")
        lbl_e.set_xalign(0.0)
        lbl_e.set_tooltip_text(
            "Which backend computes vectors for block/doc search. Independent "
            "of the chat provider — many chat endpoints do not serve embeddings."
        )
        self.embed_combo = Gtk.ComboBoxText()
        for backend in EMBED_BACKEND_ORDER:
            self.embed_combo.append_text(EMBED_BACKEND_LABELS[backend])
        current = self._cfg.get("embed_backend", "auto")
        self.embed_combo.set_active(
            EMBED_BACKEND_ORDER.index(current) if current in EMBED_BACKEND_ORDER else 0
        )
        grid.attach(lbl_e, 0, 11, 1, 1)
        grid.attach(self.embed_combo, 1, 11, 1, 1)

        self.embed_status = Gtk.Label()
        self.embed_status.set_xalign(0.0)
        self.embed_status.set_line_wrap(True)
        self.embed_status.get_style_context().add_class("dim-label")
        grid.attach(self.embed_status, 1, 12, 1, 1)

        self.embed_install_button = Gtk.Button(label="Install local runtime…")
        self.embed_install_button.set_tooltip_text(
            "Download a pinned llama.cpp build and the EmbeddingGemma model "
            "into your user data directory. Nothing is installed system-wide."
        )
        self.embed_install_button.connect("clicked", self._on_install_embed_runtime)
        grid.attach(self.embed_install_button, 1, 13, 1, 1)

        self.embed_combo.connect("changed", lambda _c: self._sync_embed_fields())
        self._sync_embed_fields()

    def _sync_embed_fields(self) -> None:
        idx = self.embed_combo.get_active()
        backend = EMBED_BACKEND_ORDER[idx] if idx >= 0 else "auto"
        is_local = backend == "llamacpp"
        self.embed_install_button.set_visible(is_local)
        self.embed_status.set_visible(is_local)
        if not is_local:
            return
        if embed_runtime.is_provisioned():
            self.embed_status.set_text(f"Installed at {embed_runtime.data_dir()}")
            self.embed_install_button.set_label("Reinstall…")
        else:
            self.embed_status.set_text("Not installed — vector search stays lexical until it is.")
            self.embed_install_button.set_label("Install local runtime…")

    def _on_install_embed_runtime(self, _btn: Gtk.Button) -> None:
        dlg = EmbedRuntimeDialog(self, on_done=lambda _ok, _err: self._sync_embed_fields())
        dlg.show()

    def _on_ollama_cloud_toggled(self, check: Gtk.CheckButton) -> None:
        idx = self.provider_combo.get_active()
        if idx < 0:
            return
        p = PROVIDER_ORDER[idx]
        if p != "ollama":
            return

        use_cloud = check.get_active()
        if use_cloud:
            self.url_label.set_text("Base URL:")
            self.url_entry.set_text("https://ollama.com/v1")
            self.url_entry.set_sensitive(False)
            self.url_entry.set_tooltip_text(
                "Ollama Cloud endpoint is set automatically to https://ollama.com/v1"
            )
            self.key_entry.set_placeholder_text("Enter Ollama Cloud API Key (sk-...)")
            cur_model = self.model_entry.get_text().strip()
            if not cur_model or cur_model == "qwen3.6:35b-a3b-q4_K_M":
                self.model_entry.set_text("deepseek-v4-flash:cloud")
        else:
            self.url_label.set_text("Base URL (default):")
            cur_url = self.url_entry.get_text().strip()
            if not cur_url or "ollama.com" in cur_url:
                local_url = (
                    self._cfg.get("ollama_base_url")
                    if "ollama.com" not in self._cfg.get("ollama_base_url", "")
                    else "http://localhost:11434"
                )
                self.url_entry.set_text(local_url or "http://localhost:11434")
            self.url_entry.set_placeholder_text("http://localhost:11434 (default)")
            self.url_entry.set_sensitive(True)
            self.url_entry.set_tooltip_text(
                "Default is http://localhost:11434. Change only if running Ollama on a custom port or remote host."
            )
            self.key_entry.set_placeholder_text("Optional for local Ollama")
            cur_model = self.model_entry.get_text().strip()
            if not cur_model or cur_model == "deepseek-v4-flash:cloud":
                self.model_entry.set_text("qwen3.6:35b-a3b-q4_K_M")

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
            self.key_entry.set_placeholder_text(PROVIDER_KEY_PLACEHOLDER[p])
        else:
            self.key_entry.set_text("")
            self.key_entry.set_placeholder_text("")

        if p == "ollama":
            self.ollama_cloud_check.set_visible(True)
            self.thinking_check.set_sensitive(True)
            self._on_ollama_cloud_toggled(self.ollama_cloud_check)
        else:  # openai_compatible
            self.ollama_cloud_check.set_visible(False)
            self.url_label.set_text("Base URL:")
            self.url_entry.set_text(
                cfg.get("openai_compatible_base_url", "https://openrouter.ai/api/v1")
            )
            self.url_entry.set_placeholder_text(
                "e.g. https://openrouter.ai/api/v1, http://localhost:8080/v1, https://api.openai.com/v1"
            )
            self.url_entry.set_sensitive(True)
            self.url_entry.set_tooltip_text(
                "Base URL for OpenAI-compatible server (e.g. OpenRouter, llama.cpp, vLLM, OpenAI)"
            )
            self.thinking_check.set_sensitive(False)

    def _collect(self) -> tuple:
        idx = self.provider_combo.get_active()
        provider = PROVIDER_ORDER[idx] if idx >= 0 else "ollama"
        model = self.model_entry.get_text().strip()
        key_var = PROVIDER_API_KEY.get(provider)
        key_val = self.key_entry.get_text().strip()
        if provider == "ollama":
            if self.ollama_cloud_check.get_active():
                base_url = "https://ollama.com/v1"
            else:
                base_url = self.url_entry.get_text().strip() or "http://localhost:11434"
        else:
            base_url = self.url_entry.get_text().strip() or "https://openrouter.ai/api/v1"
        thinking = self.thinking_check.get_active()
        eidx = self.embed_combo.get_active()
        embed_backend = EMBED_BACKEND_ORDER[eidx] if eidx >= 0 else "auto"
        return provider, model, key_var, key_val, base_url, thinking, embed_backend

    def _on_response(self, _dlg: Gtk.Dialog, response: int) -> None:
        # Read widget values BEFORE destroy — reading after destroy returns
        # ''/-1 and silently drops the model name / API key.
        values = self._collect() if response == Gtk.ResponseType.APPLY else None
        self.destroy()
        if response != Gtk.ResponseType.APPLY:
            return
        self._on_save(*values)
