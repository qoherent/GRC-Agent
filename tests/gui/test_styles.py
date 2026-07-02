"""Tests for the unified UI font metrics + stylesheet helpers.

These tests pin down the font metrics the GUI uses at every zoom level.
`ui_font_metrics` is the single source of truth for every text style; the
stylesheet and the chat-display QFont both derive their size from it.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

from grc_agent_gui.styles import get_stylesheet, ui_font_metrics


def test_ui_font_metrics_at_zoom_1():
    """At zoom 1.0: body=15, mono=14, chat_pt=round(15*0.8)=12."""
    f = ui_font_metrics(1.0)
    assert f.body_px == 15
    assert f.mono_px == 14
    assert f.small_px == 12
    assert f.chat_pt == 12


def test_ui_font_metrics_at_zoom_3_5_default():
    """At the persisted default zoom 3.5: body=52, mono=49, chat_pt=round(52*0.8)=42."""
    f = ui_font_metrics(3.5)
    assert f.body_px == 52
    assert f.mono_px == 49
    assert f.small_px == 42
    assert f.chat_pt == 42


def test_ui_font_metrics_floor_clamps_at_low_zoom():
    """A small zoom_factor must still produce legible (non-tiny) sizes."""
    f = ui_font_metrics(0.5)
    assert f.body_px >= 12
    assert f.mono_px >= 11
    assert f.small_px >= 10
    assert f.chat_pt >= 9


def test_get_stylesheet_scales_body_font_with_zoom():
    """The stylesheet body font-size line must reflect the zoom factor."""
    sheet_small = get_stylesheet(1.0)
    sheet_large = get_stylesheet(3.5)
    assert "font-size: 15px" in sheet_small
    assert "font-size: 52px" in sheet_large
