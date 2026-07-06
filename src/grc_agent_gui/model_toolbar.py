"""Inline model selection toolbar — replaces the setup wizard and Model dialog.

A thin horizontal bar at the top of the chat area with:
  [Provider ▾]  [Model ▾]  [↻ Refresh]  |  [Graph: <name>]  [📂]  [🔍]

Backend connectivity and model status are surfaced in the main
window's permanent status bar (``connection_status_label`` and
``model_status_label``), not in this toolbar.

The toolbar is a pure UI widget. Discovery, swapping, and persistence
are orchestrated by :class:`MainWindow` via signals.
"""

from __future__ import annotations

from grc_agent.config import ALLOWED_BACKENDS
from PySide6.QtCore import Signal
from PySide6.QtGui import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QWidget,
)

_BACKEND_OLLAMA = "ollama"
_BACKEND_OPENROUTER = "openrouter"
# Single source of truth: ``config.ALLOWED_BACKENDS``. Fail loud on drift.
assert {_BACKEND_OLLAMA, _BACKEND_OPENROUTER} == ALLOWED_BACKENDS, (
    f"GUI backends out of sync with config.ALLOWED_BACKENDS={ALLOWED_BACKENDS}"
)
_PLACEHOLDER_MODEL = "(select model)"
_NO_GRAPH_PLACEHOLDER = "(no graph loaded)"

# _STYLE removed. Style is generated dynamically by grc_agent_gui.styles.get_model_toolbar_style.


class ModelToolbar(QFrame):
    """Inline provider + model selector bar.

    Signals
    -------
    connect_requested(str, str):
        Emitted when the user picks a model. Arguments are
        ``(backend, model_name)``. The MainWindow handles the actual
        swap, probe, and persistence.
    refresh_requested():
        Emitted when the user clicks the refresh button.
    open_graph_location_requested():
        Emitted when the user clicks the "open containing folder"
        button. The MainWindow resolves the folder from the
        currently-loaded graph path.
    browse_graph_requested():
        Emitted when the user clicks the "browse for a .grc" button.
        The MainWindow opens a QFileDialog and routes the picked
        file through :meth:`MainWindow.open_file`.
    """

    connect_requested = Signal(str, str)
    refresh_requested = Signal()
    open_graph_location_requested = Signal()
    browse_graph_requested = Signal()
    edit_openrouter_model_requested = Signal()
    embed_model_changed = Signal(str)
    edit_openrouter_embed_model_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        backend: str = _BACKEND_OLLAMA,
        model: str = "",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("modelToolbar")
        self.setFrameShape(QFrame.Shape.NoFrame)
        from grc_agent_gui.styles import get_model_toolbar_style

        self.setStyleSheet(get_model_toolbar_style(1.0))
        self._suppress_signals = False
        # Remembers the last known-good Ollama model so switching the
        # provider combo away from Ollama and back doesn't leave the
        # OpenRouter model id sitting in the (now editable) combo — which
        # would otherwise get sent straight to `ollama pull` on reconnect.
        if backend == _BACKEND_OLLAMA and model:
            self._last_ollama_model = model
        else:
            from grc_agent.config import default_app_config

            self._last_ollama_model = default_app_config().llama.model
        # Same idea for the embedding model (Ollama side is editable).
        from grc_agent.config import default_embedding_model

        self._last_ollama_embed_model = default_embedding_model(_BACKEND_OLLAMA)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        # Left-to-right layout, per the 2026-07-02 spec:
        #   [Graph section] | [Model section + refresh] | [Provider section]
        # The graph (the file the user is editing) is the leftmost
        # element; the model (the assistant's identity) sits in the
        # middle; the provider (the backend) is rightmost.
        graph_label = QLabel("Graph", self)
        graph_label.setObjectName("toolbarLabel")
        layout.addWidget(graph_label)

        self.graph_path_label = QLabel(_NO_GRAPH_PLACEHOLDER, self)
        self.graph_path_label.setObjectName("graphPathLabel")
        self.graph_path_label.setToolTip("Path of the currently loaded .grc flowgraph")
        self.graph_path_label.setMinimumWidth(160)
        self.graph_path_label.setTextInteractionFlags(
            self.graph_path_label.textInteractionFlags()
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.graph_path_label, stretch=1)

        self.open_location_btn = QToolButton(self)
        # Closed-folder icon — the universal "this is a folder"
        # glyph. The previous open-folder (📂) icon was visually
        # similar to the browse button's icon and read as
        # "open file" rather than "open the folder this file
        # lives in".
        self.open_location_btn.setText("\U0001f4c1")  # 📁
        self.open_location_btn.setToolTip("Open the folder containing the loaded .grc file")
        self.open_location_btn.setEnabled(False)
        self.open_location_btn.clicked.connect(self.open_graph_location_requested.emit)
        layout.addWidget(self.open_location_btn)

        self.browse_btn = QToolButton(self)
        # "Open file" emoji — a file with a folded corner, the
        # universal "open a document" glyph. The previous
        # magnifying-glass icon (🔍) was misleading: users
        # associate it with "search", not "browse for a file".
        self.browse_btn.setText("\U0001f4c4")  # 📄
        self.browse_btn.setToolTip("Browse for a .grc file to load (File > Open)")
        self.browse_btn.clicked.connect(self.browse_graph_requested.emit)
        layout.addWidget(self.browse_btn)

        # Vertical separator between the graph section and the
        # model / provider selection.
        graph_separator = QFrame(self)
        graph_separator.setFrameShape(QFrame.Shape.VLine)
        graph_separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(graph_separator)

        # Model section (middle).
        model_label = QLabel("Model", self)
        model_label.setObjectName("toolbarLabel")
        layout.addWidget(model_label)

        self.model_combo = QComboBox(self)
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumWidth(220)
        if model:
            self.model_combo.setEditText(model)
        else:
            self.model_combo.setEditText(_PLACEHOLDER_MODEL)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        layout.addWidget(self.model_combo, stretch=1)

        self.refresh_btn = QToolButton(self)
        self.refresh_btn.setText("\u21bb")  # ↻
        self.refresh_btn.setToolTip("Refresh model list")
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)
        layout.addWidget(self.refresh_btn)

        self.edit_model_btn = QToolButton(self)
        self.edit_model_btn.setText("✏")  # pencil
        self.edit_model_btn.setToolTip("Edit the OpenRouter model id")
        self.edit_model_btn.clicked.connect(self.edit_openrouter_model_requested.emit)
        self.edit_model_btn.setVisible(False)
        layout.addWidget(self.edit_model_btn)

        # Embedding model section. Mirrors the chat model section: editable
        # combo for Ollama (type any embedding model), non-editable + pencil
        # edit for OpenRouter. The model name lives in .env per backend
        # (OLLAMA_EMBEDDING_MODEL / OPENROUTER_EMBEDDING_MODEL).
        embed_separator = QFrame(self)
        embed_separator.setFrameShape(QFrame.Shape.VLine)
        embed_separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(embed_separator)

        embed_label = QLabel("Embed", self)
        embed_label.setObjectName("toolbarLabel")
        layout.addWidget(embed_label)

        self.embed_combo = QComboBox(self)
        self.embed_combo.setEditable(True)
        self.embed_combo.setMinimumWidth(180)
        self.embed_combo.setEditText(self._last_ollama_embed_model)
        self.embed_combo.currentIndexChanged.connect(self._on_embed_model_changed)
        layout.addWidget(self.embed_combo)

        self.edit_embed_btn = QToolButton(self)
        self.edit_embed_btn.setText("✏")  # pencil
        self.edit_embed_btn.setToolTip("Edit the OpenRouter embedding model id")
        self.edit_embed_btn.clicked.connect(self.edit_openrouter_embed_model_requested.emit)
        self.edit_embed_btn.setVisible(False)
        layout.addWidget(self.edit_embed_btn)

        # Vertical separator between model and provider.
        provider_separator = QFrame(self)
        provider_separator.setFrameShape(QFrame.Shape.VLine)
        provider_separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(provider_separator)

        # Provider section (right).
        provider_label = QLabel("Provider", self)
        provider_label.setObjectName("toolbarLabel")
        layout.addWidget(provider_label)

        self.provider_combo = QComboBox(self)
        self.provider_combo.addItem("Ollama (Local)", _BACKEND_OLLAMA)
        self.provider_combo.addItem("OpenRouter (Cloud)", _BACKEND_OPENROUTER)
        self.provider_combo.setCurrentIndex(0 if backend == _BACKEND_OLLAMA else 1)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        layout.addWidget(self.provider_combo)

        self.set_backend(backend)
        # Tracks the absolute path of the currently loaded graph so
        # ``open_graph_location_requested`` handlers can resolve the
        # parent folder without re-reading the agent session.
        self._graph_path: str = ""

    def set_backend(self, backend: str) -> None:
        idx = 0 if backend == _BACKEND_OLLAMA else 1
        self._suppress_signals = True
        self.provider_combo.setCurrentIndex(idx)
        editable = backend == _BACKEND_OLLAMA
        if editable:
            # The combo may currently hold an OpenRouter model id (from the
            # non-editable branch below) — restore the last known-good
            # Ollama model instead of leaving it there, or a subsequent
            # swap would try to ``ollama pull`` an OpenRouter model id.
            self.model_combo.clear()
            self.model_combo.setEditable(True)
            self.model_combo.setEditText(self._last_ollama_model or _PLACEHOLDER_MODEL)
        else:
            from grc_agent.config import default_openrouter_model

            # setEditText() is a no-op once setEditable(False) has already
            # torn down the combo's internal QLineEdit — populate via
            # addItem()/setCurrentIndex() instead, which works on a
            # non-editable QComboBox.
            env_model = default_openrouter_model() or _PLACEHOLDER_MODEL
            self.model_combo.clear()
            self.model_combo.addItem(env_model)
            self.model_combo.setCurrentIndex(0)
            self.model_combo.setEditable(False)
        self.edit_model_btn.setVisible(not editable)
        # Mirror the chat-model editability for the embedding combo.
        if editable:
            self.embed_combo.setEditable(True)
            self.embed_combo.clear()
            self.embed_combo.setEditText(self._last_ollama_embed_model)
        else:
            from grc_agent.config import default_openrouter_embedding_model

            env_embed = default_openrouter_embedding_model() or _PLACEHOLDER_MODEL
            self.embed_combo.clear()
            self.embed_combo.addItem(env_embed)
            self.embed_combo.setCurrentIndex(0)
            self.embed_combo.setEditable(False)
        self.edit_embed_btn.setVisible(not editable)
        self._suppress_signals = False

    def set_models(self, models: list[str], *, current: str = "") -> None:
        self._suppress_signals = True
        self.model_combo.clear()
        if models:
            self.model_combo.addItems(models)
        effective = current or (models[0] if models else "")
        if effective:
            idx = self.model_combo.findText(effective)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
            else:
                self.model_combo.setEditText(effective)
        else:
            self.model_combo.setEditText(_PLACEHOLDER_MODEL)
        self._suppress_signals = False

    def set_current_model(self, model: str) -> None:
        if self.current_backend() == _BACKEND_OLLAMA and model and model != _PLACEHOLDER_MODEL:
            self._last_ollama_model = model
        self._suppress_signals = True
        idx = self.model_combo.findText(model)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        elif self.model_combo.isEditable():
            self.model_combo.setEditText(model)
        else:
            # setEditText() is a no-op on a non-editable combo (OpenRouter);
            # replace the single displayed item instead.
            self.model_combo.clear()
            self.model_combo.addItem(model)
            self.model_combo.setCurrentIndex(0)
        self._suppress_signals = False

    def current_backend(self) -> str:
        return self.provider_combo.currentData()

    def current_model(self) -> str:
        text = self.model_combo.currentText().strip()
        return "" if text == _PLACEHOLDER_MODEL else text

    def current_embed_model(self) -> str:
        text = self.embed_combo.currentText().strip()
        return "" if text == _PLACEHOLDER_MODEL else text

    def set_current_embed_model(self, model: str) -> None:
        """Update the embedding combo display without firing change signals."""
        if self.current_backend() == _BACKEND_OLLAMA and model and model != _PLACEHOLDER_MODEL:
            self._last_ollama_embed_model = model
        self._suppress_signals = True
        idx = self.embed_combo.findText(model)
        if idx >= 0:
            self.embed_combo.setCurrentIndex(idx)
        elif self.embed_combo.isEditable():
            self.embed_combo.setEditText(model)
        else:
            self.embed_combo.clear()
            self.embed_combo.addItem(model)
            self.embed_combo.setCurrentIndex(0)
        self._suppress_signals = False

    def _on_provider_changed(self, _index: int) -> None:
        if self._suppress_signals:
            return
        backend = self.current_backend()
        self.set_backend(backend)
        model = self.current_model()
        if model:
            self.connect_requested.emit(backend, model)

    def _on_model_changed(self, _index: int) -> None:
        if self._suppress_signals:
            return
        model = self.current_model()
        if model and model != _PLACEHOLDER_MODEL:
            self.connect_requested.emit(self.current_backend(), model)

    def _on_embed_model_changed(self, _index: int) -> None:
        if self._suppress_signals:
            return
        model = self.current_embed_model()
        if model and model != _PLACEHOLDER_MODEL:
            if self.current_backend() == _BACKEND_OLLAMA:
                self._last_ollama_embed_model = model
            self.embed_model_changed.emit(model)

    def _force_style_refresh(self) -> None:
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)

    def apply_zoom(self, zoom_factor: float) -> None:
        from grc_agent_gui.styles import get_model_toolbar_style

        self.setStyleSheet(get_model_toolbar_style(zoom_factor))
        self._force_style_refresh()

    def set_graph_path(self, path: str) -> None:
        """Update the toolbar's graph-path display.

        ``path`` is the absolute path of the currently loaded
        ``.grc`` file. Pass an empty string to clear the display
        (e.g. when a session resumes with no graph).
        """
        self._graph_path = path or ""
        if not self._graph_path:
            self.graph_path_label.setText(_NO_GRAPH_PLACEHOLDER)
            self.open_location_btn.setEnabled(False)
            return
        # Show only the filename in the toolbar (the full path lives in
        # the tooltip to keep the bar narrow).
        from pathlib import Path

        name = Path(self._graph_path).name
        self.graph_path_label.setText(name)
        self.graph_path_label.setToolTip(self._graph_path)
        self.open_location_btn.setEnabled(True)

    def current_graph_path(self) -> str:
        """Return the absolute path of the loaded graph (or ``""``)."""
        return self._graph_path
