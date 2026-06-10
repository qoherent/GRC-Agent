"""First-launch provider picker for the GRC Agent GUI.

A minimal modal dialog with two radio options: Ollama (local) and
OpenRouter (cloud). The user's choice is returned via the
:attr:`provider_chosen` signal. ``OpenRouter`` is wired but the
detailed flow is intentionally a stub for now — per project scope
the Ollama path is the only one that surfaces the setup screen.

Cancel emits :attr:`provider_cancelled` and the GUI's main window
is never built.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENROUTER = "openrouter"


@dataclass(frozen=True)
class ProviderChoice:
    """Result of the provider-picker dialog."""

    backend: str  # "ollama" | "openrouter"


class ProviderPickerDialog(QDialog):
    """Modal two-option picker shown on every GUI launch."""

    provider_chosen = Signal(ProviderChoice)
    provider_cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose LLM Provider")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setMinimumHeight(200)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "GRC Agent can talk to a local Ollama daemon or to the\n"
            "OpenRouter cloud API. Choose one to continue.",
            self,
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #cdd6f4; font-size: 13px;")
        layout.addWidget(intro)

        self.ollama_radio = QRadioButton(
            "Ollama (Local) — runs on your machine, needs a working daemon",
            self,
        )
        self.openrouter_radio = QRadioButton(
            "OpenRouter (Cloud) — uses OPENROUTER_API_KEY from .env",
            self,
        )
        self.ollama_radio.setChecked(True)

        self._group = QButtonGroup(self)
        self._group.addButton(self.ollama_radio)
        self._group.addButton(self.openrouter_radio)

        layout.addWidget(self.ollama_radio)
        layout.addWidget(self.openrouter_radio)

        layout.addStretch(1)

        confirm_row = QHBoxLayout()
        confirm_row.addStretch(1)

        self.cancel_btn = QPushButton("Cancel", self)
        self.cancel_btn.setObjectName("providerCancelButton")
        self.cancel_btn.clicked.connect(self._on_cancel)
        confirm_row.addWidget(self.cancel_btn)

        self.continue_btn = QPushButton("Continue", self)
        self.continue_btn.setObjectName("providerContinueButton")
        self.continue_btn.setDefault(True)
        self.continue_btn.clicked.connect(self._on_continue)
        confirm_row.addWidget(self.continue_btn)

        layout.addLayout(confirm_row)

    def selected_backend(self) -> str:
        if self.openrouter_radio.isChecked():
            return PROVIDER_OPENROUTER
        return PROVIDER_OLLAMA

    def _on_continue(self) -> None:
        choice = ProviderChoice(backend=self.selected_backend())
        logger.info("Provider picker: user chose %s", choice.backend)
        self.provider_chosen.emit(choice)
        self.accept()

    def _on_cancel(self) -> None:
        logger.info("Provider picker: user cancelled")
        self.provider_cancelled.emit()
        self.reject()


__all__ = [
    "PROVIDER_OLLAMA",
    "PROVIDER_OPENROUTER",
    "ProviderChoice",
    "ProviderPickerDialog",
]
