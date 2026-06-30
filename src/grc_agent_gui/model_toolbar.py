"""Inline model selection toolbar — replaces the setup wizard and Model dialog.

A thin horizontal bar at the top of the chat area with:
  [Provider ▾]  [Model ▾]  [● Status]  [↻ Refresh]

The toolbar is a pure UI widget. Discovery, swapping, and persistence
are orchestrated by :class:`MainWindow` via signals.
"""

from __future__ import annotations

from grc_agent.config import ALLOWED_BACKENDS
from PySide6.QtCore import Signal
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

_STYLE = """
QFrame#modelToolbar {
    background-color: #181825;
    border-bottom: 1px solid #313244;
}
QLabel#toolbarLabel {
    color: #a6adc8;
    font-size: 11px;
}
QComboBox {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 3px 8px;
    min-height: 20px;
    font-size: 12px;
}
QComboBox:hover {
    border-color: #585b70;
}
QComboBox::drop-down {
    border: none;
    width: 18px;
}
QComboBox QAbstractItemView {
    background-color: #313244;
    color: #cdd6f4;
    selection-background-color: #45475a;
    border: 1px solid #45475a;
}
QToolButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 3px 6px;
    font-size: 12px;
}
QToolButton:hover {
    border-color: #89b4fa;
    color: #89b4fa;
}
QToolButton:disabled {
    color: #585b70;
    border-color: #313244;
}
QLabel#statusConnected { color: #a6e3a1; font-size: 11px; }
QLabel#statusDisconnected { color: #f38ba8; font-size: 11px; }
QLabel#statusNoModel { color: #f9e2af; font-size: 11px; }
"""


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
    """

    connect_requested = Signal(str, str)
    refresh_requested = Signal()

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
        self.setStyleSheet(_STYLE)
        self._suppress_signals = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        provider_label = QLabel("Provider", self)
        provider_label.setObjectName("toolbarLabel")
        layout.addWidget(provider_label)

        self.provider_combo = QComboBox(self)
        self.provider_combo.addItem("Ollama (Local)", _BACKEND_OLLAMA)
        self.provider_combo.addItem("OpenRouter (Cloud)", _BACKEND_OPENROUTER)
        self.provider_combo.setCurrentIndex(0 if backend == _BACKEND_OLLAMA else 1)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        layout.addWidget(self.provider_combo)

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

        self.status_label = QLabel(self)
        self.status_label.setObjectName("statusNoModel")
        layout.addWidget(self.status_label)

        self.refresh_btn = QToolButton(self)
        self.refresh_btn.setText("\u21bb")  # ↻
        self.refresh_btn.setToolTip("Refresh model list")
        self.refresh_btn.clicked.connect(self.refresh_requested.emit)
        layout.addWidget(self.refresh_btn)

        self._update_status(model)
        self.set_backend(backend)

    def set_backend(self, backend: str) -> None:
        idx = 0 if backend == _BACKEND_OLLAMA else 1
        self._suppress_signals = True
        self.provider_combo.setCurrentIndex(idx)
        self._suppress_signals = False
        editable = backend == _BACKEND_OLLAMA
        self.model_combo.setEditable(editable)
        if not editable:
            from grc_agent.config import default_openrouter_model

            env_model = default_openrouter_model()
            self.model_combo.clear()
            self.model_combo.setEditText(env_model)
            self._update_status(env_model)

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
        self._update_status(effective)

    def set_current_model(self, model: str) -> None:
        self._suppress_signals = True
        idx = self.model_combo.findText(model)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        else:
            self.model_combo.setEditText(model)
        self._suppress_signals = False
        self._update_status(model)

    def set_status(self, connected: bool | None) -> None:
        if connected is None:
            self.status_label.setObjectName("statusNoModel")
            self.status_label.setText("\u25cf checking")
        elif connected:
            self.status_label.setObjectName("statusConnected")
            self.status_label.setText("\u25cf connected")
        else:
            self.status_label.setObjectName("statusDisconnected")
            self.status_label.setText("\u25cf unreachable")
        self.status_label.setStyleSheet(self.styleSheet())
        self._force_style_refresh()

    def current_backend(self) -> str:
        return self.provider_combo.currentData()

    def current_model(self) -> str:
        text = self.model_combo.currentText().strip()
        return "" if text == _PLACEHOLDER_MODEL else text

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

    def _update_status(self, model: str) -> None:
        if not model or model == _PLACEHOLDER_MODEL:
            self.status_label.setObjectName("statusNoModel")
            self.status_label.setText("\u25cf no model")
        else:
            self.status_label.setObjectName("statusConnected")
            self.status_label.setText("\u25cf ready")
        self._force_style_refresh()

    def _force_style_refresh(self) -> None:
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
