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


def test_toolbar_icons_are_not_confusing_magnifying_glass(qtbot):
    """The toolbar buttons must use icons that clearly map to their
    actions. The previous set (📂 for "open folder" + 🔍 for
    "browse") was confusing — the magnifying glass reads as
    "search" rather than "browse for a file", and the open-folder
    icon was visually ambiguous.
    """
    widget = ModelToolbar()
    qtbot.addWidget(widget)

    # The "open containing folder" button must NOT use the old
    # 📂 emoji (open-file-folder) and the "browse" button must
    # NOT use the 🔍 emoji (search).
    assert "\U0001f4c2" not in widget.open_location_btn.text(), (
        f"open_location_btn still uses 📂; text={widget.open_location_btn.text()!r}"
    )
    assert "🔍" not in widget.browse_btn.text(), (
        f"browse_btn still uses 🔍 (magnifying glass); text={widget.browse_btn.text()!r}"
    )


def test_toolbar_layout_order_graph_model_provider(qtbot):
    """The toolbar lays widgets out left-to-right in this order:
    graph section (label + name + 📂 + 🔍), then the model section
    (label + combo), then the provider section (label + combo).
    The refresh button lives next to the model combo since it
    refreshes the model list.
    """
    from PySide6.QtWidgets import QComboBox, QLabel, QToolButton, QWidget

    widget = ModelToolbar()
    qtbot.addWidget(widget)
    children = widget.children()

    # Collect the visible widgets in the layout, in left-to-right
    # order. We walk the children list filtering for things with
    # geometry in the toolbar.
    visible: list[QWidget] = []
    for c in children:
        if isinstance(c, (QLabel, QToolButton, QComboBox)) and c.parent() is widget:
            visible.append(c)

    # Verify the section markers rather than the exact sequence,
    # which is brittle to cosmetic tweaks. The graph section must
    # be leftmost; the model combo must be left of the provider
    # combo.
    graph_section = [c for c in visible if isinstance(c, QLabel) and c.text() == "Graph"]
    assert graph_section, "graph label not found in toolbar"
    assert visible.index(graph_section[0]) < visible.index(widget.model_combo), (
        "graph section must be left of the model combo"
    )
    assert visible.index(widget.model_combo) < visible.index(widget.provider_combo), (
        "model combo must be left of the provider combo"
    )


def test_toolbar_exposes_embedding_model_field(qtbot):
    """The toolbar must expose an embedding-model combo + pencil button +
    change signal so the user can pick the embedding model per backend."""
    widget = ModelToolbar()
    qtbot.addWidget(widget)
    assert hasattr(widget, "embed_combo")
    assert hasattr(widget, "edit_embed_btn")
    assert hasattr(widget, "embed_model_changed")
    assert hasattr(widget, "current_embed_model")
    # Defaults to the Ollama embedding model on the Ollama backend.
    assert widget.current_embed_model() == "embeddinggemma:latest"


def test_embedding_combo_editable_on_ollama_pencil_on_openrouter(qtbot):
    """Mirrors the chat-model combo: editable on Ollama, non-editable with a
    pencil button on OpenRouter."""
    widget = ModelToolbar()
    qtbot.addWidget(widget)
    widget.show()  # isVisible() reflects real visibility only once shown.

    widget.set_backend("ollama")
    assert widget.embed_combo.isEditable()
    assert not widget.edit_embed_btn.isVisible()

    widget.set_backend("openrouter")
    assert not widget.embed_combo.isEditable()
    assert widget.edit_embed_btn.isVisible()


def test_set_backend_openrouter_shows_perplexity_default(qtbot):
    """On the OpenRouter backend the embedding combo shows the project default
    OpenRouter embedding model (perplexity/pplx-embed-v1-0.6b)."""
    widget = ModelToolbar()
    qtbot.addWidget(widget)
    widget.set_backend("openrouter")
    assert widget.current_embed_model() == "perplexity/pplx-embed-v1-0.6b"


def test_embed_model_changed_emits_on_ollama_edit(qtbot):
    """Typing a new embedding model on the Ollama combo fires the change signal."""
    widget = ModelToolbar()
    qtbot.addWidget(widget)
    widget.set_backend("ollama")
    received: list[str] = []
    widget.embed_model_changed.connect(lambda m: received.append(m))

    widget.embed_combo.setEditText("nomic-embed-text")
    # setEditText does not change the combo index; drive the handler directly
    # as the currentIndexChanged signal would in the real editable combo.
    widget._on_embed_model_changed(0)
    assert received, "embed_model_changed did not fire"
    assert "nomic-embed-text" in widget.current_embed_model()


def test_set_current_embed_model_does_not_fire_signal(qtbot):
    """Programmatic updates (e.g. after a backend swap) must not re-emit the
    change signal — only user edits should."""
    widget = ModelToolbar()
    qtbot.addWidget(widget)
    fired: list[str] = []
    widget.embed_model_changed.connect(lambda m: fired.append(m))

    widget.set_current_embed_model("nomic-embed-text")
    assert not fired
    assert widget.current_embed_model() == "nomic-embed-text"
