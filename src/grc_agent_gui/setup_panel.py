"""In-window setup panels for the GRC Agent GUI.

On every launch the main view shows a two-step setup:

1. **ProviderPickerWidget** — pick Ollama (local) or OpenRouter (cloud).
2. **OllamaSetupWidget** (only after Ollama) — show running status,
   VRAM / RAM readings, and let the user choose a model with
   Back / Confirm.

No daemon management and no auto-start: the application does not
own the Ollama lifecycle. VRAM comes from ``nvidia-smi`` (NVIDIA
GPU only; shows "(no NVIDIA GPU detected)" otherwise). RAM comes
from ``psutil``.

Both widgets are pure ``QWidget``s, not ``QDialog``s, so they embed
into the main window's stacked layout instead of opening as a
pre-launch modal. The user sees the picker as part of the main view.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from grc_agent.model_manager import discover_ollama_models
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENROUTER = "openrouter"


DEFAULT_OLLAMA_SERVER_URL = "http://localhost:11434"


class ProviderPickerWidget(QWidget):
    """Radio-button picker. Emits ``provider_chosen(backend)``."""

    provider_chosen = Signal(str)
    cancelled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("providerPickerWidget")

        # Outer layout centers the inner card.
        outer = QVBoxLayout(self)
        outer.addStretch(1)

        inner = QFrame(self)
        inner.setObjectName("providerPickerCard")
        inner.setStyleSheet(
            "#providerPickerCard {"
            "  background-color: #1e1e2e;"
            "  border: 1px solid #45475a;"
            "  border-radius: 8px;"
            "}"
        )
        inner.setMaximumWidth(520)

        layout = QVBoxLayout(inner)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(12)

        title = QLabel("Choose your LLM provider", inner)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #cdd6f4;")
        layout.addWidget(title)

        subtitle = QLabel(
            "GRC Agent can talk to a local Ollama daemon or the OpenRouter "
            "cloud API. Pick one to continue.",
            inner,
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #a6adc8; font-size: 13px;")
        layout.addWidget(subtitle)

        layout.addSpacing(8)

        self.ollama_radio = QRadioButton(
            "Ollama (Local) — runs on your machine",
            inner,
        )
        self.openrouter_radio = QRadioButton(
            "OpenRouter (Cloud) — uses OPENROUTER_API_KEY from .env",
            inner,
        )
        self.ollama_radio.setChecked(True)

        self._group = QButtonGroup(inner)
        self._group.addButton(self.ollama_radio)
        self._group.addButton(self.openrouter_radio)

        layout.addWidget(self.ollama_radio)
        layout.addWidget(self.openrouter_radio)

        layout.addStretch(1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        self.cancel_btn = QPushButton("Quit", inner)
        self.cancel_btn.setObjectName("providerQuitButton")
        self.cancel_btn.clicked.connect(self.cancelled.emit)
        button_row.addWidget(self.cancel_btn)

        self.continue_btn = QPushButton("Continue", inner)
        self.continue_btn.setObjectName("providerContinueButton")
        self.continue_btn.setDefault(True)
        self.continue_btn.clicked.connect(self._on_continue)
        button_row.addWidget(self.continue_btn)

        layout.addLayout(button_row)

        # Center the card in the outer layout.
        h_row = QHBoxLayout()
        h_row.addStretch(1)
        h_row.addWidget(inner)
        h_row.addStretch(1)
        outer.addLayout(h_row)
        outer.addStretch(1)

    def selected_backend(self) -> str:
        if self.openrouter_radio.isChecked():
            return PROVIDER_OPENROUTER
        return PROVIDER_OLLAMA

    def _on_continue(self) -> None:
        backend = self.selected_backend()
        logger.info("Provider picker: user chose %s", backend)
        self.provider_chosen.emit(backend)


@dataclass(frozen=True)
class OllamaSetupSelection:
    """Resolved user choice from the Ollama setup panel."""

    server_url: str
    model_name: str


class OllamaSetupWidget(QWidget):
    """Status line + VRAM/RAM + model dropdown + Back/Confirm.

    Emits ``confirmed(selection)`` or ``cancelled``. Daemon management
    is NOT performed — the Confirm button is disabled when the
    daemon is unreachable. VRAM/RAM readings come from ``psutil``
    and ``nvidia-smi`` (via subprocess, no pynvml dependency).
    """

    confirmed = Signal(OllamaSetupSelection)
    cancelled = Signal()

    def __init__(
        self,
        *,
        server_url: str = DEFAULT_OLLAMA_SERVER_URL,
        current_model: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ollamaSetupWidget")
        self._server_url = str(server_url)
        self._current_model = str(current_model)

        # Outer layout centers the inner card.
        outer = QVBoxLayout(self)
        outer.addStretch(1)

        inner = QFrame(self)
        inner.setObjectName("ollamaSetupCard")
        inner.setStyleSheet(
            "#ollamaSetupCard {"
            "  background-color: #1e1e2e;"
            "  border: 1px solid #45475a;"
            "  border-radius: 8px;"
            "}"
        )
        inner.setMaximumWidth(560)

        layout = QVBoxLayout(inner)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(8)

        title = QLabel("Ollama setup", inner)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #cdd6f4;")
        layout.addWidget(title)

        self.status_label = QLabel("Checking Ollama status…", inner)
        self.status_label.setStyleSheet("font-size: 13px; color: #cdd6f4;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        form = QFormLayout()
        self.url_label = QLabel(self._server_url, inner)
        self.url_label.setStyleSheet("color: #a6adc8;")
        form.addRow("Server URL:", self.url_label)

        self.vram_label = QLabel("VRAM: —", inner)
        self.vram_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
        form.addRow("VRAM:", self.vram_label)

        self.ram_label = QLabel("RAM: —", inner)
        self.ram_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
        form.addRow("RAM:", self.ram_label)

        layout.addLayout(form)

        sep = QFrame(inner)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        model_label = QLabel("Model:", inner)
        model_label.setStyleSheet("font-size: 13px; color: #cdd6f4;")
        layout.addWidget(model_label)

        self.model_combo = QComboBox(inner)
        self.model_combo.setEditable(True)
        self.model_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.model_combo.setToolTip(
            "Pick a discovered model or type any Ollama tag."
        )
        layout.addWidget(self.model_combo)

        refresh_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh", inner)
        self.refresh_btn.setObjectName("ollamaRefreshButton")
        self.refresh_btn.clicked.connect(self._refresh_all)
        refresh_row.addWidget(self.refresh_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        refresh_row.addStretch(1)
        layout.addLayout(refresh_row)

        self.refresh_label = QLabel("", inner)
        self.refresh_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
        self.refresh_label.setWordWrap(True)
        layout.addWidget(self.refresh_label)

        layout.addStretch(1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        self.cancel_btn = QPushButton("Back", inner)
        self.cancel_btn.setObjectName("ollamaSetupBackButton")
        self.cancel_btn.clicked.connect(self.cancelled.emit)
        button_row.addWidget(self.cancel_btn)

        self.confirm_btn = QPushButton("Confirm", inner)
        self.confirm_btn.setObjectName("ollamaSetupConfirmButton")
        self.confirm_btn.setDefault(True)
        self.confirm_btn.clicked.connect(self._on_confirm)
        button_row.addWidget(self.confirm_btn)

        layout.addLayout(button_row)

        # Center the card in the outer layout.
        h_row = QHBoxLayout()
        h_row.addStretch(1)
        h_row.addWidget(inner)
        h_row.addStretch(1)
        outer.addLayout(h_row)
        outer.addStretch(1)

        self._refresh_all()

    def _populate_models(self) -> None:
        self.refresh_label.setText("Discovering models…")
        self.model_combo.clear()
        try:
            models = discover_ollama_models(self._server_url)
        except Exception as exc:  # noqa: BLE001
            logger.debug("discover_ollama_models failed: %s", exc)
            models = []
        if self._current_model and self._current_model not in models:
            models.insert(0, self._current_model)
        for m in models:
            self.model_combo.addItem(m)
        if self._current_model:
            self.model_combo.setCurrentText(self._current_model)
        if models:
            self.status_label.setText(
                f"Ollama is running at {self._server_url}."
            )
            self.status_label.setStyleSheet("color: #a6e3a1; font-size: 13px;")
            self.refresh_label.setText(
                f"{len(models)} model(s) discovered. Pick one or type a tag."
            )
            self.confirm_btn.setEnabled(True)
        else:
            self.status_label.setText(
                f"Ollama is not reachable at {self._server_url}."
            )
            self.status_label.setStyleSheet("color: #f38ba8; font-size: 13px;")
            self.refresh_label.setText(
                "Start the Ollama daemon externally (OS service / desktop app), "
                "then click Refresh."
            )
            self.confirm_btn.setEnabled(False)

    def _refresh_all(self) -> None:
        self._populate_models()
        self._refresh_diagnostics()

    def _refresh_diagnostics(self) -> None:
        import shutil
        import subprocess

        try:
            import psutil

            vm = psutil.virtual_memory()
            total_gb = round(vm.total / (1024**3), 1)
            used_gb = round(vm.used / (1024**3), 1)
            self.ram_label.setText(f"{used_gb} / {total_gb} GB used")
            self.ram_label.setStyleSheet("color: #cdd6f4; font-size: 12px;")
        except Exception as exc:  # noqa: BLE001
            logger.debug("psutil RAM read failed: %s", exc)
            self.ram_label.setText("RAM: unavailable")
            self.ram_label.setStyleSheet("color: #a6adc8; font-size: 12px;")

        try:
            if shutil.which("nvidia-smi") is None:
                self.vram_label.setText("(no NVIDIA GPU detected)")
                self.vram_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
                return
            proc = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.total,memory.used",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"nvidia-smi exit {proc.returncode}")
            total_mb = 0
            used_mb = 0
            for line in proc.stdout.strip().splitlines():
                parts = line.split(",")
                if len(parts) >= 2:
                    total_mb += int(parts[0].strip())
                    used_mb += int(parts[1].strip())
            if total_mb <= 0:
                raise RuntimeError("zero VRAM parsed")
            total_gb = round(total_mb / 1024.0, 1)
            used_gb = round(used_mb / 1024.0, 1)
            self.vram_label.setText(f"{used_gb} / {total_gb} GB used")
            self.vram_label.setStyleSheet("color: #cdd6f4; font-size: 12px;")
        except Exception as exc:  # noqa: BLE001
            logger.debug("nvidia-smi VRAM read failed: %s", exc)
            self.vram_label.setText("(no NVIDIA GPU detected)")
            self.vram_label.setStyleSheet("color: #a6adc8; font-size: 12px;")

    def _on_confirm(self) -> None:
        if not self.confirm_btn.isEnabled():
            return
        model_name = self.model_combo.currentText().strip()
        if not model_name:
            return
        self.confirmed.emit(
            OllamaSetupSelection(
                server_url=self._server_url,
                model_name=model_name,
            )
        )


__all__ = [
    "DEFAULT_OLLAMA_SERVER_URL",
    "OllamaSetupSelection",
    "OllamaSetupWidget",
    "PROVIDER_OLLAMA",
    "PROVIDER_OPENROUTER",
    "ProviderPickerWidget",
]
