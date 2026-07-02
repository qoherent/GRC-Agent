"""Shared UI constants for the desktop GUI: palette, status markers, layout.

Single source of truth for literals that were previously copy-pasted
across ``main_window.py`` call sites. Other GUI modules (``app.py``,
``chat_widget.py``, ``model_toolbar.py``, ``sidebar_widget.py``) define
the same Catppuccin Mocha palette inline today; consolidating them is a
separate follow-up and out of scope here.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Catppuccin Mocha palette subset used by main_window.py                      #
# --------------------------------------------------------------------------- #
COLOR_BLUE = "#89b4fa"  # active/info accent (status bar tool indicator)
COLOR_BASE = "#11111b"  # darkest background (status bar backdrop)
COLOR_SURFACE = "#45475a"  # border / divider
COLOR_TEXT = "#cdd6f4"  # primary text
COLOR_SUBTEXT = "#a6adc8"  # secondary / muted text
COLOR_RED = "#f38ba8"  # error / invalid
COLOR_GREEN = "#a6e3a1"  # success / valid
COLOR_YELLOW = "#f9e2af"  # warning / checking / pending


# --------------------------------------------------------------------------- #
# Validation status presentation                                              #
# --------------------------------------------------------------------------- #
VALID_ICON = "\U0001f7e2"  # 🟢
INVALID_ICON = "\U0001f534"  # 🔴
UNVALIDATED_ICON = "⚪"  # ⚪


# --------------------------------------------------------------------------- #
# In-band chat message markers (prefix tags on synthetic assistant rows)      #
# --------------------------------------------------------------------------- #
BACKEND_STATUS_MARKER = "[backend status]"
MODEL_SELECTOR_MARKER = "[model selector]"


# --------------------------------------------------------------------------- #
# Splitter proportions and pixel floors                                       #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SplitterProportions:
    """Fractions of total window width and pixel-floor minimums.

    Three call sites (``__init__``, ``showEvent``, ``toggle_sidebar``)
    independently re-derived these numbers; this is the one place they
    are defined.
    """

    sidebar_fraction_initial: float = 0.09
    sidebar_fraction_restored: float = 0.18
    sidebar_fraction_max: float = 0.20
    chat_fraction: float = 0.50
    inspector_fraction: float = 0.32

    sidebar_min_px_initial: int = 80
    sidebar_min_px_restored: int = 150
    sidebar_collapsed_floor_px: int = 50
    inspector_min_px: int = 200
    chat_min_px: int = 300


SPLITTER = SplitterProportions()
