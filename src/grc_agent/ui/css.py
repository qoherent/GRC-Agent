# ruff: noqa: E402
"""Structural CSS for the chat sidebar.

No theme is defined and no colors are picked: borders use the active GTK
theme's foreground color at low opacity (``alpha(@theme_fg_color, …)``). Because
the foreground inherently contrasts with the background, this yields clearly
visible boundaries on BOTH light and dark themes, while remaining fully
naive/out-of-the-box (we never name a color or add a dark/light variant). The
``@theme_selected_bg_color`` reference for the Send button is likewise the
theme's own accent, not a hardcoded one.
"""

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk

# Boundary colors: the theme's own foreground at low opacity — visible on any
# theme. STRONG for content containers, SOFT for separators.
_STRONG = "alpha(@theme_fg_color, 0.30)"
_SOFT = "alpha(@theme_fg_color, 0.16)"

_CSS_TEMPLATE = """
/* ---- fonts / sizing -------------------------------------------------- */
.chat-monospace { font-family: monospace; }
.chat-side-toggle { min-width: 18px; }

/* Non-editable TextViews blend into the conversation with crisp, clear text. */
textview.chat-agent-label,
textview.chat-agent-label text,
textview.chat-thinking-textview,
textview.chat-thinking-textview text {
    background: transparent;
    color: @theme_fg_color;
}
.chat-agent-label {
    color: @theme_fg_color;
}

/* ---- panel separators ----------------------------------------------- */
.chat-sidebar { border-left: 1px solid @SOFT@; }
.chat-project-bar { border-bottom: 1px solid @SOFT@; }
.chat-project-label {
    color: alpha(@theme_fg_color, 0.85);
    font-size: 0.90em;
}
.chat-toolbar { border-bottom: 1px solid @SOFT@; }
.chat-status-bar { border-top: 1px solid @SOFT@; }
.chat-side-toggle { border-right: 1px solid @SOFT@; }

/* Active graph and backend are bounded status badges, so the header's
   flexible width reads as useful state instead of unexplained empty space. */
.chat-header-badge {
    border: 1px solid @SOFT@;
    border-radius: 4px;
    padding: 3px 8px;
    font-size: 0.92em;
    font-weight: 500;
}

.chat-mode-btn,
.chat-mode-btn label {
    font-size: 0.92em;
}
.chat-mode-btn {
    border-radius: 10px;
    padding: 2px 8px;
    font-weight: 500;
    min-height: 22px;
}
.chat-mode-agent {
    border: 1px solid #3584e4;
    background: rgba(53, 132, 228, 0.18);
    color: #3584e4;
}
.chat-mode-agent:hover {
    background: rgba(53, 132, 228, 0.32);
    border-color: #1c71d8;
    color: #1c71d8;
}
.chat-mode-planner {
    border: 1px solid #e66100;
    background: rgba(230, 97, 0, 0.18);
    color: #e66100;
}
.chat-mode-planner:hover {
    background: rgba(230, 97, 0, 0.32);
    border-color: #c64600;
    color: #c64600;
}
.chat-mode-manual {
    border: 1px solid @SOFT@;
}
.chat-mode-auto {
    border: 1px solid #3584e4;
    background: rgba(53, 132, 228, 0.18);
    color: #3584e4;
}
.chat-mode-auto:hover {
    background: rgba(53, 132, 228, 0.32);
    border-color: #1c71d8;
    color: #1c71d8;
}
.chat-mode-yolo {
    border: 1px solid #e01b24;
    background: rgba(224, 27, 36, 0.18);
    color: #e01b24;
}
.chat-mode-yolo:hover {
    background: rgba(224, 27, 36, 0.32);
    border-color: #c01c28;
    color: #c01c28;
}

/* ---- content containers (clear boundaries) -------------------------- */
.chat-code-block,
.chat-entry-frame,
.chat-welcome-box,
.chat-tool-expander,
.chat-thinking-expander {
    border: 1px solid @STRONG@;
    border-radius: 6px;
}
.chat-code-block {
    border: 1px solid alpha(@theme_fg_color, 0.22);
    border-radius: 6px;
    margin: 4px 0;
}
.chat-code-header {
    border-bottom: 1px solid @SOFT@;
    border-radius: 6px 6px 0 0;
    background-color: alpha(@theme_fg_color, 0.04);
    padding: 2px 6px;
}
.chat-thinking-expander,
.chat-tool-expander {
    border: 1px solid alpha(@theme_fg_color, 0.20);
    border-radius: 6px;
    background-color: alpha(@theme_fg_color, 0.035);
    margin-bottom: 2px;
}
.chat-thinking-expander:hover,
.chat-tool-expander:hover {
    border-color: alpha(@theme_fg_color, 0.40);
}

/* Inline status messages */
.chat-error-label,
.chat-aborted-label {
    border: 1px solid @STRONG@;
    border-radius: 6px;
}

/* Approval card for human-in-the-loop change approval */
.chat-approval-card {
    border: 1px solid @STRONG@;
    border-radius: 6px;
    padding: 8px;
}
.chat-approval-reason {
    font-weight: bold;
}

/* ---- buttons: explicit bounded affordance --------------------------- */
.chat-toolbar-btn,
.chat-compact-btn,
.chat-quick-prompt-btn,
.chat-recent-delete-btn {
    border: 1px solid @SOFT@;
    border-radius: 4px;
}
.chat-toolbar-btn:hover,
.chat-compact-btn:hover,
.chat-quick-prompt-btn:hover,
.chat-recent-delete-btn:hover {
    border-color: @STRONG@;
}
.chat-compact-btn,
.chat-compact-btn label {
    font-size: 0.92em;
}
.chat-compact-btn {
    min-height: 22px;
    padding: 2px 7px;
}
.chat-agent-mode-label,
.chat-agent-mode-label label {
    color: alpha(@theme_fg_color, 0.82);
    font-size: 0.92em;
}

/* Copy buttons (symbolic icon, unobtrusive flat button) */
.chat-copy-btn {
    border: none;
    background: transparent;
    border-radius: 4px;
    padding: 3px 5px;
    min-height: 22px;
    min-width: 22px;
    opacity: 0.60;
}
.chat-copy-btn:hover {
    background: alpha(@theme_fg_color, 0.14);
    opacity: 1.0;
}
.chat-msg-actions {
    margin-top: 4px;
}

/* Recent-session list rows */
.chat-recent-item {
    border: 1px solid @SOFT@;
    border-radius: 6px 0 0 6px;
    padding: 3px 6px;
}
.chat-recent-delete-btn {
    border-radius: 0 6px 6px 0;
    min-width: 26px;
    padding: 2px;
}
.chat-recent-meta {
    color: alpha(@theme_fg_color, 0.68);
    font-size: 0.85em;
}

/* Quick prompts are suggestions, not primary actions. Keep their labels
   compact so all three fit across a normal sidebar without widening it. */
.chat-quick-prompt-btn {
    font-size: 0.85em;
    min-height: 22px;
    padding: 2px 6px;
}

/* Separate the composer from the scrolling transcript and make its active
   text area unmistakable on both light and dark themes. */
.chat-input-area {
    border-top: 1px solid @SOFT@;
    padding-top: 4px;
}
.chat-entry-frame {
    border-color: alpha(@theme_fg_color, 0.46);
    border-radius: 8px;
    background-color: alpha(@theme_fg_color, 0.025);
}
.chat-send-btn,
.chat-attach-btn {
    min-width: 34px;
    min-height: 34px;
}
.chat-attachment-chip {
    border: 1px solid alpha(@theme_fg_color, 0.22);
    border-radius: 8px;
    padding: 2px;
    background-color: alpha(@theme_fg_color, 0.08);
}
.chat-user-msg-images image {
    border-radius: 6px;
}
.chat-agent-msg-box {
    border: 1px solid @SOFT@;
    border-radius: 8px;
    padding: 8px 12px;
    background-color: alpha(@theme_fg_color, 0.025);
}
.chat-user-msg-box {
    border: 1px solid alpha(@theme_fg_color, 0.22);
    border-radius: 8px;
    padding: 8px 12px;
    background-color: alpha(@theme_fg_color, 0.08);
}
.chat-plan-action-box {
    border: 1px solid @STRONG@;
    border-radius: 8px;
    padding: 7px;
}
.chat-implement-plan-btn {
    border: 1px solid @theme_selected_bg_color;
    border-radius: 4px;
    min-height: 30px;
}
.chat-implement-plan-btn:hover {
    background: alpha(@theme_selected_bg_color, 0.18);
}

/* Block-name pill badge (for table cells and container widgets) */
.chat-block-badge {
    border: 1px solid @STRONG@;
    border-radius: 10px;
    padding: 1px 6px;
}

/* Send button: primary action — the theme's accent (selected bg) so it reads
   as the main affordance on any theme. */
.chat-send-btn {
    border: 1px solid @theme_selected_bg_color;
    border-radius: 4px;
}
.chat-send-btn:hover {
    background: alpha(@theme_selected_bg_color, 0.85);
}

/* ---- context-usage label ramp (theme-safe escalation) ---------------- */
/* Base is muted fg; escalation via full-fg bold (75-89%) then the theme's
   required accent (>=90%). No error/warning symbols used — they are not
   defined by every GTK theme, and a dropped rule would silently lose the cue. */
.chat-context-controls,
.chat-context-controls label,
.chat-context-label,
.chat-context-label label {
    color: alpha(@theme_fg_color, 0.72);
    font-size: 0.92em;
}
.chat-context-label.warn { color: @theme_fg_color; font-weight: bold; }
.chat-context-label.alarm { color: @theme_selected_bg_color; font-weight: bold; }

/* ---- status bar: quiet base, loud errors ----------------------------- */
.chat-status-bar,
.chat-status-bar label {
    color: alpha(@theme_fg_color, 0.85);
    font-size: 0.92em;
}
.validation-invalid { color: @theme_fg_color; font-weight: bold; }
"""

_CSS = _CSS_TEMPLATE.replace("@STRONG@", _STRONG).replace("@SOFT@", _SOFT).encode()

_applied = False
_original_system_theme: str | None = None


def apply_css() -> None:
    """Register the chat CSS provider once for the default screen. Idempotent."""
    global _applied
    if _applied:
        return
    screen = Gdk.Screen.get_default()
    if screen is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _applied = True


def _get_theme_pair() -> tuple[str, str]:
    """Find installed system GTK3 dark and light theme pair."""
    from pathlib import Path

    try:
        installed = {
            p.name
            for p in Path("/usr/share/themes").iterdir()
            if p.is_dir() and (p / "gtk-3.0").exists()
        }
    except Exception:
        installed = set()

    if "Yaru-dark" in installed and "Yaru" in installed:
        return "Yaru-dark", "Yaru"
    if "Adwaita-dark" in installed and "Adwaita" in installed:
        return "Adwaita-dark", "Adwaita"
    return "Adwaita-dark", "Adwaita"


def apply_theme(mode: str) -> None:
    """Apply the application theme mode ('dark', 'light', or 'system').

    Requests the native GTK dark/light variant from the host theme or sets
    the system GTK theme, allowing full visual harmony with GNU Radio
    Companion and the desktop environment.
    """
    global _original_system_theme
    settings = Gtk.Settings.get_default()
    if settings is None:
        return

    if _original_system_theme is None:
        _original_system_theme = settings.get_property("gtk-theme-name") or "Adwaita"

    dark_theme, light_theme = _get_theme_pair()

    if mode == "dark":
        settings.set_property("gtk-theme-name", dark_theme)
        settings.set_property("gtk-application-prefer-dark-theme", True)
    elif mode == "light":
        settings.set_property("gtk-theme-name", light_theme)
        settings.set_property("gtk-application-prefer-dark-theme", False)
    else:  # "system"
        if _original_system_theme:
            settings.set_property("gtk-theme-name", _original_system_theme)
        settings.reset_property("gtk-application-prefer-dark-theme")


def is_dark_theme(widget: Gtk.Widget | None = None) -> bool:
    """Determine whether the active theme is dark."""
    settings = Gtk.Settings.get_default()
    if settings is not None:
        if settings.get_property("gtk-application-prefer-dark-theme"):
            return True
        theme_name = (settings.get_property("gtk-theme-name") or "").lower()
        if "dark" in theme_name or "black" in theme_name:
            return True

    try:
        ctx = widget.get_style_context() if widget else Gtk.StyleContext()
        val, color = ctx.lookup_color("theme_bg_color")
        if val and color:
            lum = 0.2126 * color.red + 0.7152 * color.green + 0.0722 * color.blue
            return lum < 0.5
    except Exception:
        pass

    return False


def get_code_style(widget: Gtk.Widget | None = None) -> str:
    """Return Pygments syntax theme name for the current theme mode."""
    return "monokai" if is_dark_theme(widget) else "friendly"

