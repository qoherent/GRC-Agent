# ruff: noqa: E402
"""Empty-state UI for the chat listbox: the welcome card (with quick-action
prompt chips when a flowgraph is open) and the recent-sessions list.

Pure widget construction — the sidebar owns the actual session/quick-prompt
*behavior* (busy checks, loading a session, switching its tab) and passes it in
as callbacks, so this module never imports the sidebar (no cycle) and stays
trivially testable.
"""

from __future__ import annotations

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import GLib, Gtk, Pango

from ..db import get_recent_sessions

_log = logging.getLogger(__name__)


def _esc(text: str) -> str:
    return GLib.markup_escape_text(text, -1)


def format_relative_time(timestamp_str: str) -> str:
    from datetime import UTC, datetime

    try:
        dt = (
            datetime.fromisoformat(timestamp_str)
            if "T" in timestamp_str
            else datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        )
        now = datetime.now(UTC)
        seconds = (now - dt).total_seconds()
        if seconds < 60:
            return "just now"
        minutes = seconds // 60
        if minutes < 60:
            return f"{int(minutes)}m ago"
        hours = minutes // 60
        if hours < 24:
            return f"{int(hours)}h ago"
        days = hours // 24
        if days < 7:
            return f"{int(days)}d ago"
        return dt.strftime("%b %d, %Y")
    except Exception:
        return timestamp_str


# (button label, prompt sent on click) — shown only when a flowgraph is open.
_QUICK_PROMPTS = [
    ("\U0001f50d Inspect", "Inspect this flowgraph and summarize its architecture."),
    ("\u26a1 Validate", "Check this flowgraph for configuration errors or missing parameters."),
    (
        "\u2753 Explain",
        "Explain what signal processing pipeline this flowgraph implements.",
    ),
]


class WelcomeView:
    """Builds the welcome card + recent-sessions list into the chat listbox."""

    def __init__(
        self,
        listbox: Gtk.ListBox,
        on_quick_prompt,
        on_open_session,
        on_delete_session,
        on_clear_all_sessions=None,
    ) -> None:
        self._listbox = listbox
        self._on_quick_prompt = on_quick_prompt
        self._on_open_session = on_open_session
        self._on_delete_session = on_delete_session
        self._on_clear_all_sessions = on_clear_all_sessions

    def render(self, current_page, active_session_id: int | None) -> None:
        self._listbox.add(self._welcome_card(current_page))
        try:
            sessions = get_recent_sessions()
        except Exception as e:
            # A corrupt/unwritable chat_sessions.db must not abort the UI —
            # degrade to an empty recent list rather than crashing launch.
            _log.error("Failed to load recent sessions: %s", e)
            sessions = []
        if active_session_id is not None:
            sessions = [s for s in sessions if s["id"] != active_session_id]
        if sessions:
            self._add_recent_sessions(sessions)

    def _welcome_card(self, current_page) -> Gtk.Box:
        welcome_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        welcome_box.get_style_context().add_class("chat-welcome-box")

        title_lbl = Gtk.Label()
        title_lbl.set_markup("<span weight='bold'>GRC Agent</span>")
        title_lbl.set_xalign(0.0)
        welcome_box.pack_start(title_lbl, False, False, 0)

        sub_lbl = Gtk.Label()
        sub_lbl.set_line_wrap(True)
        sub_lbl.set_xalign(0.0)
        sub_lbl.get_style_context().add_class("dim-label")

        if current_page is not None:
            sub_lbl.set_markup(
                "<span size='small'>"
                "Ask a question or request flowgraph changes."
                "</span>"
            )
            welcome_box.pack_start(sub_lbl, False, False, 0)

            # Quick Action Prompt Chips — a FlowBox so chips wrap at narrow
            # sidebar widths (a plain Box forces the window to ~539px min).
            chips_box = Gtk.FlowBox()
            chips_box.set_selection_mode(Gtk.SelectionMode.NONE)
            chips_box.set_max_children_per_line(3)
            chips_box.set_valign(Gtk.Align.START)
            chips_box.set_row_spacing(4)
            chips_box.set_column_spacing(4)
            chips_box.set_margin_top(2)
            for label_text, prompt_text in _QUICK_PROMPTS:
                btn = Gtk.Button(label=label_text)
                btn.get_style_context().add_class("chat-quick-prompt-btn")
                btn.set_tooltip_text(f'Send: "{prompt_text}"')
                btn.connect("clicked", lambda _, p=prompt_text: self._on_quick_prompt(p))
                chips_box.add(btn)
            welcome_box.pack_start(chips_box, False, False, 0)
        else:
            sub_lbl.set_markup(
                "<span size='small'>"
                "Open or create a flowgraph in GRC to begin, or pick a recent session:"
                "</span>"
            )
            welcome_box.pack_start(sub_lbl, False, False, 0)
        return welcome_box

    def _add_recent_sessions(self, sessions: list[dict]) -> None:
        hdr_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hdr_box.get_style_context().add_class("chat-recent-header")
        hdr_box.pack_start(
            Gtk.Image.new_from_icon_name("document-open-recent-symbolic", Gtk.IconSize.MENU),
            False,
            False,
            0,
        )
        lbl = Gtk.Label()
        lbl.set_markup(f"<b>Recent ({len(sessions)})</b>")
        hdr_box.pack_start(lbl, False, False, 0)

        if self._on_clear_all_sessions is not None:
            clear_btn = Gtk.Button(label="Delete all sessions")
            clear_btn.set_tooltip_text("Delete all saved chat sessions")
            clear_btn.get_style_context().add_class("chat-compact-btn")
            clear_btn.set_halign(Gtk.Align.END)
            clear_btn.set_hexpand(True)
            clear_btn.connect(
                "clicked",
                lambda btn: self._on_clear_all_sessions(btn)
                if self._on_clear_all_sessions
                else None,
            )
            hdr_box.pack_end(clear_btn, False, False, 0)

        self._listbox.add(hdr_box)

        for s in sessions:
            self._listbox.add(self._recent_row(s))

    def _recent_row(self, s: dict) -> Gtk.Box:
        sid = s["id"]
        grc_path = s["grc_file_path"]
        first_message = s.get("first_message", "")
        updated_at = s.get("updated_at", "")

        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)

        btn = Gtk.Button()
        btn.get_style_context().add_class("chat-recent-item")
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_hexpand(True)

        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        inner.pack_start(
            Gtk.Image.new_from_icon_name("text-x-generic-symbolic", Gtk.IconSize.MENU),
            False,
            False,
            0,
        )

        text_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        top_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        top_hbox.set_hexpand(True)
        name_lbl = Gtk.Label()
        name_lbl.set_markup(f"<b>{_esc(Path(grc_path).name)}</b>")
        name_lbl.set_xalign(0.0)
        name_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        top_hbox.pack_start(name_lbl, True, True, 0)
        if updated_at:
            time_lbl = Gtk.Label()
            time_lbl.get_style_context().add_class("dim-label")
            time_lbl.set_markup(
                f"<span size='small'>{_esc(format_relative_time(updated_at))}</span>"
            )
            time_lbl.set_xalign(1.0)
            top_hbox.pack_end(time_lbl, False, False, 0)

        metadata = str(Path(grc_path).parent)
        if first_message:
            snippet = first_message.replace("\n", " ").strip()
            metadata = f"{metadata} · {snippet}"
        meta_lbl = Gtk.Label(label=metadata)
        meta_lbl.get_style_context().add_class("chat-recent-meta")
        meta_lbl.set_xalign(0.0)
        meta_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        text_vbox.pack_start(top_hbox, False, False, 0)
        text_vbox.pack_start(meta_lbl, False, False, 0)

        inner.pack_start(text_vbox, True, True, 0)
        btn.add(inner)
        tooltip = grc_path if not first_message else f"{grc_path}\n\n{first_message}"
        btn.set_tooltip_text(tooltip)
        btn.connect("clicked", lambda _, session_id=sid: self._on_open_session(session_id))

        del_btn = Gtk.Button()
        del_btn.get_style_context().add_class("chat-recent-delete-btn")
        del_btn.set_relief(Gtk.ReliefStyle.NONE)
        del_btn.set_image(Gtk.Image.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.MENU))
        del_btn.set_tooltip_text("Delete this session permanently")
        del_btn.connect("clicked", lambda _, session_id=sid: self._on_delete_session(session_id))

        row_box.pack_start(btn, True, True, 0)
        row_box.pack_start(del_btn, False, False, 0)
        return row_box
