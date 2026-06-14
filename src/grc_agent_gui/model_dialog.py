"""Model-selector dialog for the GRC Agent GUI — Ollama native.

Surfaces:
1. Backend picker: Ollama (local) or OpenRouter (cloud).
2. For Ollama: a combo of discovered models plus a text-entry field for
   any model name (e.g. ``qwen3.5:9b-q4_K_M``).
3. Confirm strip with "Switch model" button.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from grc_agent.model_manager import discover_ollama_models
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelDialogSelection:
    """The user-confirmed pick from the model selector."""

    backend: str
    ollama_model_name: str | None = None


def _format_refresh_label(count: int) -> str:
    if count == 0:
        return "(no models discovered — Ollama server unreachable?)"
    return f"{count} model(s) discovered from Ollama"


class ModelDialog(QDialog):
    """Non-modal model selector with discover/type-ahead and confirm strip."""

    model_accepted = Signal(ModelDialogSelection)

    def __init__(
        self,
        *,
        current_backend: str = "ollama",
        current_ollama_model: str = "",
        ollama_server_url: str = "http://localhost:11434",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Model & Client")
        self.setModal(False)
        self.setMinimumWidth(520)

        self._current_backend = current_backend
        self._current_ollama_model = current_ollama_model
        self._ollama_server_url = ollama_server_url
        self._ollama_models: list[str] = []

        layout = QVBoxLayout(self)

        # -- Client/Backend row --
        client_row = QHBoxLayout()
        client_label = QLabel("Client:", self)
        client_label.setMinimumWidth(70)
        client_row.addWidget(client_label)

        self.client_combo = QComboBox(self)
        self.client_combo.addItem("Ollama (Local)", "ollama")
        self.client_combo.addItem("OpenRouter (Cloud)", "openrouter")
        client_row.addWidget(self.client_combo, stretch=1)
        layout.addLayout(client_row)

        # -- Model name row (Ollama only) --
        self.model_form = QFormLayout()

        self.model_combo = QComboBox(self)
        self.model_combo.setEditable(True)
        self.model_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.model_combo.setToolTip(
            "Select a discovered model or type any Ollama model name "
            "(e.g. qwen3.5:9b-q4_K_M). The model will be pulled "
            "automatically if not already available."
        )
        self.model_form.addRow("Model:", self.model_combo)

        self.refresh_label = QLabel("", self)
        self.refresh_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
        self.model_form.addRow("", self.refresh_label)

        layout.addLayout(self.model_form)

        # -- OpenRouter info (hidden by default) --
        self.openrouter_info = QLabel(
            "Model configured via OPENROUTER_MODEL in .env file.\n"
            "Set OPENROUTER_API_KEY to authenticate.",
            self,
        )
        self.openrouter_info.setStyleSheet("color: #a6adc8;")
        self.openrouter_info.setWordWrap(True)
        self.openrouter_info.hide()
        layout.addWidget(self.openrouter_info)

        # -- Confirm strip --
        separator = QFrame(self)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator)

        confirm_row = QHBoxLayout()
        self.alias_input_note = QLabel("", self)
        self.alias_input_note.setStyleSheet("color: #a6adc8;")
        confirm_row.addWidget(self.alias_input_note, stretch=1)

        self.switch_btn = QPushButton("Switch model", self)
        self.switch_btn.setObjectName("modelSwitchButton")
        self.switch_btn.setEnabled(False)
        self.switch_btn.clicked.connect(self._on_switch_clicked)
        confirm_row.addWidget(self.switch_btn)

        self.cancel_btn = QPushButton("Cancel", self)
        self.cancel_btn.setObjectName("modelCancelButton")
        self.cancel_btn.clicked.connect(self.reject)
        confirm_row.addWidget(self.cancel_btn)

        layout.addLayout(confirm_row)

        # Connect signals
        self.client_combo.currentIndexChanged.connect(self._on_client_changed)
        self.model_combo.currentTextChanged.connect(self._on_model_text_changed)

        # Initialize to current backend
        idx = self.client_combo.findData(self._current_backend)
        if idx >= 0:
            self.client_combo.setCurrentIndex(idx)
        else:
            self.client_combo.setCurrentIndex(0)

        self._populate_ollama_models()
        self._refresh_button_state()

    def _on_client_changed(self, _idx: int) -> None:
        backend = self.client_combo.currentData()
        is_ollama = backend == "ollama"
        self.model_form.labelForField(self.model_combo).setVisible(is_ollama)  # type: ignore[union-attr]
        self.model_combo.setVisible(is_ollama)
        self.refresh_label.setVisible(is_ollama)
        self.openrouter_info.setVisible(not is_ollama)
        if is_ollama:
            self._populate_ollama_models()
        self._refresh_button_state()

    def _populate_ollama_models(self) -> None:
        try:
            self._ollama_models = discover_ollama_models(self._ollama_server_url)
        except Exception:
            self._ollama_models = []

        self.model_combo.blockSignals(True)
        current_text = self.model_combo.currentText()
        self.model_combo.clear()

        # Add current model first if it's not in the discovered list
        if self._current_ollama_model:
            stored_current = self._current_ollama_model
            if stored_current not in self._ollama_models:
                self._ollama_models.insert(0, stored_current)

        for m in self._ollama_models:
            self.model_combo.addItem(m)

        # Restore previous text or set to current model
        if current_text:
            self.model_combo.setCurrentText(current_text)
        elif self._current_ollama_model:
            self.model_combo.setCurrentText(self._current_ollama_model)

        self.refresh_label.setText(_format_refresh_label(len(self._ollama_models)))
        self.model_combo.blockSignals(False)

    def _on_model_text_changed(self, _text: str) -> None:
        self._refresh_button_state()

    def _refresh_button_state(self) -> None:
        backend = self.client_combo.currentData()

        if backend == "openrouter":
            is_same = backend == self._current_backend
            if is_same:
                self.switch_btn.setEnabled(False)
                self.alias_input_note.setText("Already loaded (configure in .env).")
            else:
                self.switch_btn.setEnabled(True)
                import os
                env_model = os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash")
                self.alias_input_note.setText(f"Switch client to OpenRouter ({env_model})")
            return

        # Ollama backend
        model_name = self.model_combo.currentText().strip()
        if not model_name:
            self.switch_btn.setEnabled(False)
            self.alias_input_note.setText("")
            return

        is_same_backend = backend == self._current_backend
        is_same_model = model_name == self._current_ollama_model

        if is_same_backend and is_same_model:
            self.switch_btn.setEnabled(False)
            self.alias_input_note.setText("Already loaded.")
        elif not is_same_backend:
            self.switch_btn.setEnabled(True)
            self.alias_input_note.setText(f"Switch to Ollama ({model_name})")
        else:
            self.switch_btn.setEnabled(True)
            self.alias_input_note.setText(f"Switch Ollama model to '{model_name}'")

    def _on_switch_clicked(self) -> None:
        backend = self.client_combo.currentData()

        if backend == "ollama":
            model_name = self.model_combo.currentText().strip()
            if not model_name:
                return
            selection = ModelDialogSelection(
                backend=backend,
                ollama_model_name=model_name,
            )
        elif backend == "openrouter":
            selection = ModelDialogSelection(backend=backend)
        else:
            return

        self.model_accepted.emit(selection)
        self.accept()
