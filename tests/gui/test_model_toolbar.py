"""Tests for the inline model toolbar.

Covers the graph-path label, open-location button, and browse
button (added in 2026-07-02). The toolbar remains a pure UI
widget — discovery, swap, and persistence are orchestrated by
:class:`MainWindow` via signals.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

from grc_agent_gui.model_toolbar import ModelToolbar


def test_toolbar_exposes_graph_path_signals(qtbot):
    """The toolbar must expose both graph-action signals plus the
    open-location button, browse button, and graph-path label."""
    widget = ModelToolbar()
    qtbot.addWidget(widget)
    assert hasattr(widget, "open_graph_location_requested")
    assert hasattr(widget, "browse_graph_requested")
    assert hasattr(widget, "open_location_btn")
    assert hasattr(widget, "browse_btn")
    assert hasattr(widget, "graph_path_label")


def test_toolbar_set_graph_path_updates_label_and_enables_button(qtbot):
    """Setting a graph path must (a) put the filename in the
    visible label, (b) put the absolute path in the tooltip, and
    (c) enable the open-location button."""
    widget = ModelToolbar()
    qtbot.addWidget(widget)

    widget.set_graph_path("/tmp/dial_tone.grc")
    assert widget.graph_path_label.text() == "dial_tone.grc"
    assert widget.graph_path_label.toolTip() == "/tmp/dial_tone.grc"
    assert widget.open_location_btn.isEnabled()
    assert widget.current_graph_path() == "/tmp/dial_tone.grc"


def test_toolbar_clear_graph_path_resets_label_and_disables_button(qtbot):
    """Passing an empty path must reset the label to the
    placeholder and disable the open-location button (no graph → no
    folder to open)."""
    widget = ModelToolbar()
    qtbot.addWidget(widget)

    widget.set_graph_path("/tmp/dial_tone.grc")
    widget.set_graph_path("")
    from grc_agent_gui.model_toolbar import _NO_GRAPH_PLACEHOLDER

    assert widget.graph_path_label.text() == _NO_GRAPH_PLACEHOLDER
    assert not widget.open_location_btn.isEnabled()
    assert widget.current_graph_path() == ""


def test_browse_button_emits_browse_signal(qtbot):
    widget = ModelToolbar()
    qtbot.addWidget(widget)
    emitted = False

    def on_browse():
        nonlocal emitted
        emitted = True

    widget.browse_graph_requested.connect(on_browse)
    widget.browse_btn.click()
    assert emitted


def test_open_location_button_emits_signal_only_when_graph_loaded(qtbot):
    """The open-location button must be a no-op (signal-wise) until a
    graph is loaded — otherwise the handler would have to defensively
    re-check, leaking layout concerns into the controller."""
    widget = ModelToolbar()
    qtbot.addWidget(widget)

    # No graph loaded: button is disabled, so even a click shouldn't fire.
    assert not widget.open_location_btn.isEnabled()

    widget.set_graph_path("/tmp/example.grc")
    assert widget.open_location_btn.isEnabled()

    emitted = False

    def on_open():
        nonlocal emitted
        emitted = True

    widget.open_graph_location_requested.connect(on_open)
    widget.open_location_btn.click()
    assert emitted
