# ruff: noqa: E402
"""Settings-controller mixin for ChatSidebar.

Owns opening the Preferences dialog and its post-Save flow: a bounded
preflight probe, persisting the new config to ``.env``, and live-swapping
the running Agent in place — plus the non-blocking Yes/No confirm dialogs
that flow uses when a provider is unreachable. Split out of
``chat_sidebar.py`` by U15 — a GTK-owning mixin, not a pure-function module,
so it still needs a display to test against.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gtk

from ..settings import load_settings, save_settings, upsert_env_key
from ..ui.css import apply_theme
from ..ui.providers import PROVIDER_BASE_URL_SETTING as _PROVIDER_BASE_URL_SETTING
from ..ui.providers import PROVIDER_LABELS as _PROVIDER_LABELS
from ..ui.settings_dialog import SettingsDialog

_log = logging.getLogger(__name__)


class SettingsControllerMixin:
    """Settings-controller behavior mixed into ``ChatSidebar``.

    Every method here assumes the full ``ChatSidebar`` instance attributes
    (``self._open_dialog``, ``self._rebuild_agent``, and the turn-driver/
    session state still living on ``ChatSidebar`` itself) — this is an
    organizational split, not an encapsulation boundary.
    """

    def _open_settings(self) -> None:
        toplevel = self.get_toplevel()
        if not isinstance(toplevel, Gtk.Window):
            toplevel = None
        dlg = SettingsDialog(
            toplevel=toplevel,
            cfg=load_settings(),
            on_save=self._apply_settings_save,
        )
        self._open_dialog = dlg
        dlg.connect("destroy", lambda *_: setattr(self, "_open_dialog", None))
        dlg.show()

    @staticmethod
    def _persist_settings(
        provider: str,
        model: str,
        key_var: str | None,
        key_val: str,
        base_url: str,
        embed_backend: str,
        theme: str | None = None,
    ) -> None:
        """Write the new config to `.env`. Base-URL routing: editable-URL
        providers (ollama_local, openai_compatible) persist their URL var;
        fixed-endpoint providers (ollama_cloud, openrouter, openai) have a
        canonical URL that is never persisted; ChatGPT/Codex has neither a
        base URL nor an API key."""
        url_kwarg = _PROVIDER_BASE_URL_SETTING.get(provider)
        save_settings(
            provider,
            model,
            embed_backend=embed_backend,
            theme=theme,
            **({url_kwarg: base_url} if url_kwarg else {}),
        )
        if key_var:
            upsert_env_key(key_var, key_val)

    def _apply_settings_save(
        self,
        provider: str,
        model: str,
        key_var: str | None,
        key_val: str,
        base_url: str = "http://localhost:11434",
        embed_backend: str = "lexical",
        theme: str = "system",
    ) -> None:
        """Post-Save flow: preflight → persist → live-swap.

        All three phases run synchronously and are bounded (preflight ≤ 5s),
        which is acceptable for a user-initiated action and lets tests assert
        on the persisted state immediately after the dialog's APPLY response.
        """
        from ..agent_factory import probe_backend

        if not model:
            self.set_status("Settings not saved — model name is required.", error=True)
            return

        toplevel = self.get_toplevel()
        if not isinstance(toplevel, Gtk.Window):
            toplevel = None

        provider_label = _PROVIDER_LABELS.get(provider, provider)

        # 1. ONE bounded probe answers both questions: can we reach the
        #    backend, and does it serve this model? A missing tag on a local
        #    daemon means a silent multi-GB pull that reads as a hung chat —
        #    surface it, but never block on it: the status bar warns, the
        #    save proceeds, and the live-swap still happens.
        self.set_status(f"Checking {provider_label}\u2026")
        reach_err, model_warn = probe_backend(provider, key_val, base_url, model)

        def _finish_save() -> None:
            if model_warn:
                self.set_status(model_warn, error=True)

            # 2. Persist to .env synchronously — tests assert on load_settings()
            #    immediately after emitting the response signal.
            try:
                self._persist_settings(
                    provider, model, key_var, key_val, base_url, embed_backend, theme=theme
                )
                apply_theme(theme)
                self._render_history()
            except Exception as e:
                _log.exception("Failed to save settings")
                self.set_status(f"Settings not saved ({e}).", error=True)
                return

            # 3. Live-swap the running Agent in-place. Dispatched async so the
            #    gbulb loop stays responsive during model construction (which
            #    spins up an httpx client and pydantic-ai Agent). The history is
            #    kept verbatim — ModelMessage objects are provider-agnostic.
            warn_suffix = f" ⚠ {model_warn}" if model_warn else ""
            if self._rebuild_agent is None:
                self.set_status(
                    f"Settings saved. Restart to apply.{warn_suffix}", error=bool(model_warn)
                )
                return
            try:
                agents = self._rebuild_agent()
            except Exception as e:
                _log.exception("Live-swap rebuild failed")
                self.set_status(f"Settings saved but live-swap failed: {e}", error=True)
                return
            self.set_agents(
                agents.executor,
                agents.planner,
                model_error=agents.model_build_error,
            )
            if agents.model_build_error:
                self.set_status(
                    f"Switched with warning ({agents.model_build_error}). Running on defaults.",
                    error=True,
                )
            else:
                self.set_status(
                    f"Switched to {provider_label} \u00b7 {model}.{warn_suffix}",
                    error=bool(model_warn),
                )

        if reach_err:
            def _on_confirm(save_anyway: bool) -> None:
                if not save_anyway:
                    self.set_status("Settings not saved — provider unreachable.", error=True)
                    return
                _finish_save()

            self._confirm_unreachable(
                provider, reach_err, toplevel, base_url=base_url, on_confirm=_on_confirm
            )
            return

        _finish_save()

    def _confirm_unreachable(
        self,
        provider: str,
        err: str,
        toplevel: Gtk.Window | None,
        *,
        base_url: str = "http://localhost:11434",
        on_confirm: Callable[[bool], None] | None = None,
    ) -> None:
        """Non-blocking Yes/No confirm when the preflight ping fails."""
        provider_label = _PROVIDER_LABELS.get(provider, provider)
        if provider == "openai_codex":
            hint = "• Click 'Sign in with ChatGPT' in Preferences.\n• Codex requires an active ChatGPT Plus or Pro subscription."
        elif provider == "ollama_local":
            hint = f"• Ensure local Ollama daemon is running ('ollama serve').\n• Verify host is reachable at {base_url}."
        elif provider == "ollama_cloud":
            hint = f"• Verify your Ollama Cloud API key.\n• Check reachability of {base_url}."
        elif provider in ("openrouter", "openai"):
            hint = f"• Verify your API key for {provider}.\n• Check reachability of {base_url}."
        else:
            hint = f"• Ensure your OpenAI-compatible server is running.\n• Verify endpoint is reachable at {base_url}."
        self._confirm_yes_no(
            toplevel,
            title=f"Cannot reach {provider_label}",
            body=(
                f"Preflight check error: {err}\n\n"
                f"Actionable hints:\n{hint}\n\n"
                f"Save anyway? The agent will retry when you send a message."
            ),
            on_response=on_confirm,
        )

    def _confirm_yes_no(
        self,
        toplevel: Gtk.Window | None,
        *,
        title: str,
        body: str,
        on_response: Callable[[bool], None] | None = None,
    ) -> None:
        """Non-blocking Yes/No warning dialog using connect("response")."""
        confirm = Gtk.MessageDialog(
            transient_for=toplevel,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text=title,
        )
        confirm.format_secondary_text(body)
        self._open_dialog = confirm

        def _on_dlg_response(_dlg: Gtk.Dialog, response: int) -> None:
            self._open_dialog = None
            confirm.destroy()
            if on_response is not None:
                on_response(response == Gtk.ResponseType.YES)

        confirm.connect("response", _on_dlg_response)
        confirm.show()
