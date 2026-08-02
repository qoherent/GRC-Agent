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

/* Non-editable TextViews blend into the conversation, not look like inputs. */
textview.chat-agent-label,
textview.chat-agent-label text,
textview.chat-thinking-textview,
textview.chat-thinking-textview text {
    background: transparent;
}

/* ---- panel separators ----------------------------------------------- */
.chat-sidebar { border-left: 1px solid @SOFT@; }
.chat-toolbar { border-bottom: 1px solid @SOFT@; }
.chat-status-bar { border-top: 1px solid @SOFT@; }
.chat-side-toggle { border-right: 1px solid @SOFT@; }

/* ---- content containers (clear boundaries) -------------------------- */
.chat-code-block,
.chat-entry-frame,
.chat-welcome-box,
.chat-tool-expander,
.chat-thinking-expander,
.chat-agent-msg-box {
    border: 1px solid @STRONG@;
    border-radius: 6px;
}
.chat-code-header {
    border-bottom: 1px solid @SOFT@;
    border-radius: 6px 6px 0 0;
}

/* Inline status messages */
.chat-error-label,
.chat-aborted-label {
    border: 1px solid @STRONG@;
    border-radius: 6px;
}

/* ---- buttons: explicit bounded affordance --------------------------- */
.chat-toolbar-btn,
.chat-copy-btn,
.chat-recent-delete-btn {
    border: 1px solid @SOFT@;
    border-radius: 4px;
}
.chat-toolbar-btn:hover,
.chat-copy-btn:hover,
.chat-recent-delete-btn:hover {
    border-color: @STRONG@;
}

/* Recent-session list rows */
.chat-recent-item {
    border: 1px solid @SOFT@;
    border-radius: 6px 0 0 6px;
}
.chat-recent-delete-btn {
    border-radius: 0 6px 6px 0;
}

/* Block-name pill badge */
.chat-block-badge {
    border: 1px solid @STRONG@;
    border-radius: 10px;
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
.chat-context-label { color: alpha(@theme_fg_color, 0.65); }
.chat-context-label.warn { color: @theme_fg_color; font-weight: bold; }
.chat-context-label.alarm { color: @theme_selected_bg_color; font-weight: bold; }

/* ---- status bar: quiet base, loud errors ----------------------------- */
.chat-status-bar { color: alpha(@theme_fg_color, 0.78); }
.validation-invalid { color: @theme_fg_color; font-weight: bold; }
"""

_CSS = _CSS_TEMPLATE.replace("@STRONG@", _STRONG).replace("@SOFT@", _SOFT).encode()

_applied = False


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
