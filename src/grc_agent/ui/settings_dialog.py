# ruff: noqa: E402
"""Settings dialog for the chat sidebar.

Owns only the dialog *UI* (provider dropdown, model/key entries, Ollama URL,
embedding-backend selector) and the per-provider field-sync logic. On Save it reads the
widget values *before* destroying itself (regression-critical — reading after
destroy returns ''/-1), then hands them to the sidebar's ``on_save`` callback,
which owns preflight, persistence and live-swap.
"""

from __future__ import annotations

import asyncio
import logging

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from .. import embed_runtime
from ..model_catalog import list_models
from ..providers.openai_codex import clear as codex_sign_out
from ..providers.openai_codex import is_signed_in as codex_is_signed_in
from ..settings import get_env_value
from .codex_login_dialog import CodexLoginDialog
from .embed_runtime_dialog import EmbedRuntimeDialog
from .providers import (
    EMBED_BACKEND_LABELS,
    EMBED_BACKEND_ORDER,
    PROVIDER_API_KEY,
    PROVIDER_BASE_URL,
    PROVIDER_KEY_PLACEHOLDER,
    PROVIDER_LABELS,
    PROVIDER_MODEL_KEY,
    PROVIDER_MODEL_PLACEHOLDER,
    PROVIDER_ORDER,
)

_log = logging.getLogger(__name__)


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
        grid.attach(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), 0, 14, 2, 1)
        self._build_theme_section(grid)

        info = Gtk.Label(label="Changes apply immediately on Save.")
        info.get_style_context().add_class("dim-label")

        content.pack_start(grid, False, False, 0)
        content.pack_start(info, False, False, 0)
        content.show_all()
        # After show_all, never before: show_all() forces every child visible,
        # so any set_visible(False) made during construction is undone by it.
        self._sync_provider_fields(self.provider_combo)
        self._sync_embed_fields()

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
            "• Ollama (local) — a local or LAN Ollama daemon\n"
            "• Ollama Cloud — ollama.com (API key required)\n"
            "• OpenRouter / OpenAI API — cloud aggregators (API key required)\n"
            "• Other OpenAI-compatible — llama.cpp, vLLM, LM Studio, Groq…\n"
            "• ChatGPT Plus/Pro (Codex) — OAuth sign-in"
        )
        for p in PROVIDER_ORDER:
            self.provider_combo.append_text(PROVIDER_LABELS[p])
        active_idx = (
            PROVIDER_ORDER.index(cfg["provider"]) if cfg["provider"] in PROVIDER_ORDER else 0
        )
        self.provider_combo.set_active(active_idx)
        grid.attach(lbl_p, 0, 1, 1, 1)
        grid.attach(self.provider_combo, 1, 1, 1, 1)

        lbl_m = Gtk.Label(label="Model:")
        lbl_m.set_xalign(0.0)
        lbl_m.set_tooltip_text("The specific LLM model ID or tag for chat responses")
        # Editable combo: the list is whatever the backend reports, but a
        # model can still be typed in — a brand-new id should not be
        # unreachable just because the catalog has not caught up.
        self.model_combo = Gtk.ComboBoxText.new_with_entry()
        self.model_entry = self.model_combo.get_child()
        self.model_entry.set_activates_default(True)
        self.model_combo.set_hexpand(True)
        self.model_combo.set_tooltip_text(
            "Model ID or tag for chat. Click Load to list what the configured "
            "backend actually serves, or type one in."
        )
        self.model_load_button = Gtk.Button(label="Load")
        self.model_load_button.set_tooltip_text("Ask the backend which models it serves")
        self.model_load_button.connect("clicked", self._on_load_models)
        model_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        model_box.pack_start(self.model_combo, True, True, 0)
        model_box.pack_start(self.model_load_button, False, False, 0)
        grid.attach(lbl_m, 0, 2, 1, 1)
        grid.attach(model_box, 1, 2, 1, 1)

        lbl_k = Gtk.Label(label="API Key:")
        lbl_k.set_xalign(0.0)
        lbl_k.set_tooltip_text(
            "Authentication key (required for cloud endpoints, optional for local servers)"
        )
        self.key_entry = Gtk.Entry()
        self.key_entry.set_visibility(False)
        self.key_entry.set_activates_default(True)
        self.key_entry.set_tooltip_text(
            "API key for the selected provider (e.g. OpenRouter, Ollama Cloud, OpenAI). Optional for local servers."
        )
        self.key_label = lbl_k
        grid.attach(lbl_k, 0, 3, 1, 1)
        grid.attach(self.key_entry, 1, 3, 1, 1)

        # ChatGPT signs in with OAuth instead of an API key, so it gets an
        # account row in place of the key entry rather than an empty one.
        self.codex_status = Gtk.Label()
        self.codex_status.set_xalign(0.0)
        self.codex_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.codex_button = Gtk.Button()
        self.codex_button.connect("clicked", self._on_codex_button)
        self.codex_box.pack_start(self.codex_status, True, True, 0)
        self.codex_box.pack_start(self.codex_button, False, False, 0)
        self.codex_label = Gtk.Label(label="Account:")
        self.codex_label.set_xalign(0.0)
        grid.attach(self.codex_label, 0, 3, 1, 1)
        grid.attach(self.codex_box, 1, 3, 1, 1)

    def _build_execution_section(self, grid: Gtk.Grid) -> None:
        hdr = Gtk.Label()
        hdr.set_markup("<b>Endpoint &amp; Execution Options</b>")
        hdr.set_xalign(0.0)
        grid.attach(hdr, 0, 5, 2, 1)

        self.url_label = Gtk.Label(label="Base URL:")
        self.url_label.set_xalign(0.0)
        self.url_label.set_tooltip_text("Base URL endpoint for the model server")
        self.url_entry = Gtk.Entry()
        # Initial text is irrelevant: _sync_provider_fields() (called at the
        # end of __init__) sets it per provider.
        self.url_entry.set_text("")
        self.url_entry.set_hexpand(True)
        self.url_entry.set_activates_default(True)
        grid.attach(self.url_label, 0, 7, 1, 1)
        grid.attach(self.url_entry, 1, 7, 1, 1)

        self.provider_combo.connect("changed", self._sync_provider_fields)

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
        current = self._cfg.get("embed_backend", "lexical")
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

    def _build_theme_section(self, grid: Gtk.Grid) -> None:
        hdr = Gtk.Label()
        hdr.set_markup("<b>Appearance &amp; Theme</b>")
        hdr.set_xalign(0.0)
        grid.attach(hdr, 0, 15, 2, 1)

        lbl_t = Gtk.Label(label="Theme:")
        lbl_t.set_xalign(0.0)
        lbl_t.set_tooltip_text("Switch between Dark (Black), Light, or System Default theme")
        self.theme_combo = Gtk.ComboBoxText()
        self.theme_combo.append("dark", "Dark (Black)")
        self.theme_combo.append("light", "Light")
        self.theme_combo.append("system", "System Default")
        current_theme = self._cfg.get("theme", "system")
        self.theme_combo.set_active_id(
            current_theme if current_theme in ("dark", "light", "system") else "system"
        )
        grid.attach(lbl_t, 0, 16, 1, 1)
        grid.attach(self.theme_combo, 1, 16, 1, 1)

    def _sync_embed_fields(self) -> None:
        idx = self.embed_combo.get_active()
        backend = EMBED_BACKEND_ORDER[idx] if idx >= 0 else "lexical"
        is_local = backend == "llamacpp"
        self.embed_install_button.set_visible(is_local)
        self.embed_status.set_visible(True)
        if not is_local:
            self.embed_status.set_text("Using SQLite FTS5/BM25 keyword search (no runtime required).")
            return
        if embed_runtime.is_provisioned():
            self.embed_status.set_text(f"Installed at {embed_runtime.data_dir()}")
            self.embed_install_button.set_label("Reinstall…")
        else:
            self.embed_status.set_text("Not installed — vector search stays lexical until installed.")
            self.embed_install_button.set_label("Install local runtime…")

    def _on_install_embed_runtime(self, _btn: Gtk.Button) -> None:
        dlg = EmbedRuntimeDialog(self, on_done=lambda _ok, _err: self._sync_embed_fields())
        dlg.show()

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

        is_codex = p == "openai_codex"
        # The key entry and the account row occupy the same grid cell.
        self.key_entry.set_visible(not is_codex)
        self.key_label.set_visible(not is_codex)
        self.codex_box.set_visible(is_codex)
        self.codex_label.set_visible(is_codex)
        if is_codex:
            self._sync_codex_account()

        if is_codex:
            # No base URL and no key: the endpoint is fixed and auth is OAuth.
            self.url_label.set_visible(False)
            self.url_entry.set_visible(False)
            return
        self.url_label.set_visible(True)
        self.url_entry.set_visible(True)

        canonical = PROVIDER_BASE_URL[p]
        if canonical is not None:
            # Fixed-endpoint provider (Ollama Cloud / OpenRouter / OpenAI):
            # show the real URL read-only — no editable URL to get wrong.
            self.url_label.set_text("Endpoint:")
            self.url_entry.set_text(canonical)
            self.url_entry.set_sensitive(False)
            self.url_entry.set_tooltip_text(
                f"Fixed endpoint for {PROVIDER_LABELS[p]} — not editable."
            )
        elif p == "ollama_local":
            self.url_label.set_text("Base URL:")
            self.url_entry.set_text(cfg.get("ollama_base_url", "http://localhost:11434"))
            self.url_entry.set_placeholder_text("http://localhost:11434 (default)")
            self.url_entry.set_sensitive(True)
            self.url_entry.set_tooltip_text(
                "Default is http://localhost:11434. Change only if running Ollama on a custom port or remote host."
            )
        else:  # openai_compatible — any OpenAI-shaped endpoint
            self.url_label.set_text("Base URL:")
            self.url_entry.set_text(cfg["openai_compatible_base_url"])
            self.url_entry.set_placeholder_text(
                "e.g. http://localhost:8080/v1 (llama.cpp), http://localhost:1234/v1 (LM Studio), https://api.groq.com/openai/v1"
            )
            self.url_entry.set_sensitive(True)
            self.url_entry.set_tooltip_text(
                "Base URL for your OpenAI-compatible server (llama.cpp, vLLM, LM Studio, Groq, …)"
            )

    def _collect(self) -> tuple:
        idx = self.provider_combo.get_active()
        provider = PROVIDER_ORDER[idx] if idx >= 0 else "ollama_local"
        model = self.model_entry.get_text().strip()
        key_var = PROVIDER_API_KEY.get(provider)
        key_val = self.key_entry.get_text().strip()
        canonical = PROVIDER_BASE_URL[provider]
        # Editable URL: pass the raw field through; save_settings applies the
        # provider's own documented default when it is empty (the dialog must
        # not hardcode a second, divergent default).
        base_url = (
            canonical
            if canonical is not None
            else self.url_entry.get_text().strip()
        )
        eidx = self.embed_combo.get_active()
        embed_backend = EMBED_BACKEND_ORDER[eidx] if eidx >= 0 else "lexical"
        theme_mode = self.theme_combo.get_active_id() or "system"
        return provider, model, key_var, key_val, base_url, embed_backend, theme_mode

    def _on_response(self, _dlg: Gtk.Dialog, response: int) -> None:
        # Read widget values BEFORE destroy — reading after destroy returns
        # ''/-1 and silently drops the model name / API key.
        values = self._collect() if response == Gtk.ResponseType.APPLY else None
        self.destroy()
        if response != Gtk.ResponseType.APPLY:
            return
        self._on_save(*values)

    def _sync_codex_account(self) -> None:
        if codex_is_signed_in():
            self.codex_status.set_text("Signed in")
            self.codex_button.set_label("Sign out")
        else:
            self.codex_status.set_text("Not signed in")
            self.codex_button.set_label("Sign in with ChatGPT")

    def _on_codex_button(self, _btn: Gtk.Button) -> None:
        if codex_is_signed_in():
            codex_sign_out()
            self._sync_codex_account()
            return
        CodexLoginDialog(self, on_done=lambda _ok, _err: self._sync_codex_account()).show()

    def _on_load_models(self, _btn: Gtk.Button) -> None:
        self.model_load_button.set_sensitive(False)
        self.model_load_button.set_label("Loading…")
        asyncio.ensure_future(self._load_models())

    async def _load_models(self) -> None:
        """Populate the dropdown from the backend, preserving what is typed.

        Reads the *pending* provider/key/URL from the widgets rather than the
        saved config, so the list matches what Save would apply — otherwise
        switching provider and hitting Load would query the previous backend.
        """
        provider, _model, _kv, key_val, base_url, _embed = self._collect()
        current = self.model_entry.get_text().strip()
        try:
            names = await list_models({"provider": provider}, api_key=key_val, base_url=base_url)
        except Exception as exc:
            self._set_model_load_state(f"Load failed: {exc}")
            return
        if not names:
            self._set_model_load_state("Backend listed no models")
            return
        self.model_combo.remove_all()
        for name in names:
            self.model_combo.append_text(name)
        # append_text on a combo-with-entry does not touch the entry, but
        # remove_all clears it, so the typed value is restored explicitly.
        self.model_entry.set_text(current or names[0])
        self._set_model_load_state(None)

    def _set_model_load_state(self, error: str | None) -> None:
        self.model_load_button.set_sensitive(True)
        self.model_load_button.set_label("Load")
        if error:
            _log.info("model list unavailable: %s", error)
            self.model_combo.set_tooltip_text(error)
