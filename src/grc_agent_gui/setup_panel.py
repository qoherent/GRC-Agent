"""In-window setup panels for the GRC Agent GUI.

On every launch the main view shows a three-step setup:

1. **ProviderPickerWidget** — pick Ollama (local) or OpenRouter (cloud).
2. **OllamaSetupWidget** (only after Ollama) — show probe status, a
   "Models on this machine" list (everything the Ollama ``/api/tags``
   endpoint reports), VRAM / RAM readings, and a Confirm button that
   is always enabled. The user picks from whatever is installed; the
   widget never gates the button on probe state.
3. **OllamaStartHintWidget** (only when the Ollama server is
   unreachable) — two read-only copy boxes the user can use to start
   the daemon and pull a model in a new terminal of their own.

No daemon management and no auto-start: the application does not own
the Ollama lifecycle. The user runs ``ollama serve`` and
``ollama pull <model>`` themselves. Both widgets are pure ``QWidget``s,
not ``QDialog``s, so they embed into the main window's stacked layout
instead of opening as a pre-launch modal. The user sees the picker as
part of the main view.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from grc_agent.model_manager import OllamaBackendStatus, probe_ollama_backend
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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


CONFIRM_OLLAMA_EXTERNAL_TEXT = (
    "Confirm you started the ollama externally yourself"
)


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


def _copy_to_clipboard(text: str) -> None:
    """Push ``text`` onto the application clipboard.

    Centralized so the copy buttons on pages 2 and 3 share one fallback
    path; ``QApplication.clipboard()`` is None during very early Qt
    bootstrap, so the helper is defensive.
    """
    try:
        clipboard = QApplication.clipboard()
    except Exception:  # noqa: BLE001
        clipboard = None
    if clipboard is not None:
        try:
            clipboard.setText(text)
        except Exception:  # noqa: BLE001
            logger.debug("Clipboard setText failed for %r", text)


_GENERIC_PULL_HINT = (
    "Use `ollama pull <model_name>` to download any ollama model, "
    "e.g. `ollama pull qwen3.5:9b-q4_K_M`."
)


class OllamaSetupWidget(QWidget):
    """Status line + VRAM/RAM + "Models on this machine" list + Confirm.

    The widget re-runs :func:`probe_ollama_backend` whenever the user
    clicks **Refresh**. The result drives two non-exclusive states:

    * **Server reachable** — the "Models on this machine" list shows
      the installed tags from ``/api/tags``. The status label is
      green. The hint label always carries the generic
      ``ollama pull <model_name>`` reminder so the user can fetch a
      new tag without leaving the wizard.
    * **Server unreachable** — the list is empty, the status label is
      red, and a **Start the server** button is shown to route to the
      start-hint page.

    The Confirm button is always enabled. Its text is
    :data:`CONFIRM_OLLAMA_EXTERNAL_TEXT` so the user is reminded, on
    every click, that they are asserting ownership of the Ollama
    lifecycle. The user picks from the list; if the list is empty the
    widget emits an empty ``model_name`` and downstream surfaces the
    error (no in-widget gate).
    """

    confirmed = Signal(OllamaSetupSelection)
    cancelled = Signal()
    next_requested = Signal()

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
        inner.setMaximumWidth(620)

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

        models_heading = QLabel("Models on this machine", inner)
        models_heading.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #cdd6f4;"
        )
        layout.addWidget(models_heading)

        self.models_list = QListWidget(inner)
        self.models_list.setObjectName("ollamaModelsList")
        self.models_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.models_list.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.models_list.setMinimumHeight(120)
        self.models_list.setToolTip(
            "Models reported by the Ollama /api/tags endpoint. Click "
            "Refresh after running `ollama pull <model_name>` to see "
            "newly downloaded tags here."
        )
        layout.addWidget(self.models_list)

        self.pull_hint_label = QLabel(_GENERIC_PULL_HINT, inner)
        self.pull_hint_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
        self.pull_hint_label.setWordWrap(True)
        layout.addWidget(self.pull_hint_label)

        refresh_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh", inner)
        self.refresh_btn.setObjectName("ollamaRefreshButton")
        self.refresh_btn.clicked.connect(self._refresh_all)
        refresh_row.addWidget(
            self.refresh_btn, alignment=Qt.AlignmentFlag.AlignLeft
        )
        refresh_row.addStretch(1)
        layout.addLayout(refresh_row)

        self.status_detail_label = QLabel("", inner)
        self.status_detail_label.setStyleSheet(
            "color: #a6adc8; font-size: 12px;"
        )
        self.status_detail_label.setWordWrap(True)
        layout.addWidget(self.status_detail_label)

        layout.addStretch(1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        self.cancel_btn = QPushButton("Back", inner)
        self.cancel_btn.setObjectName("ollamaSetupBackButton")
        self.cancel_btn.clicked.connect(self.cancelled.emit)
        button_row.addWidget(self.cancel_btn)

        self.next_btn = QPushButton("Start the server", inner)
        self.next_btn.setObjectName("ollamaSetupNextButton")
        self.next_btn.setVisible(False)
        self.next_btn.clicked.connect(self.next_requested.emit)
        button_row.addWidget(self.next_btn)

        self.confirm_btn = QPushButton(CONFIRM_OLLAMA_EXTERNAL_TEXT, inner)
        self.confirm_btn.setObjectName("ollamaSetupConfirmButton")
        self.confirm_btn.setDefault(True)
        # Always enabled — the user is on the hook for the
        # installation, the wizard never blocks them.
        self.confirm_btn.setEnabled(True)
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

    def current_selection(self) -> OllamaSetupSelection:
        """Return the user's selection as an :class:`OllamaSetupSelection`.

        Prefers the highlighted list item; falls back to the first
        installed tag, then to the empty string. The widget never
        blocks the click, so a missing selection is a legitimate
        state the caller has to handle (downstream surfaces the
        model-not-found error).
        """
        model_name = ""
        current = self.models_list.currentItem()
        if current is not None:
            model_name = current.text().strip()
        if not model_name and self.models_list.count() > 0:
            first = self.models_list.item(0)
            if first is not None:
                model_name = first.text().strip()
        return OllamaSetupSelection(
            server_url=self._server_url,
            model_name=model_name,
        )

    def _populate_models(self, status: OllamaBackendStatus) -> None:
        previous_text = self.models_list.currentItem().text().strip() if self.models_list.currentItem() else ""
        self.models_list.blockSignals(True)
        self.models_list.clear()
        for m in status.available_models:
            item = QListWidgetItem(m)
            self.models_list.addItem(item)
        # Restore the previously selected tag if it is still
        # installed, otherwise select the configured model if it is
        # installed, otherwise the first item.
        desired = previous_text or self._current_model
        target_row = -1
        if desired:
            for i in range(self.models_list.count()):
                if self.models_list.item(i).text() == desired:
                    target_row = i
                    break
        if target_row < 0 and self.models_list.count() > 0:
            target_row = 0
        if target_row >= 0:
            self.models_list.setCurrentRow(target_row)
        self.models_list.blockSignals(False)

    def _set_status(self, status: OllamaBackendStatus) -> None:
        if not status.server_reachable:
            self.status_label.setText(
                f"Ollama server is not reachable at {status.server_url}."
            )
            self.status_label.setStyleSheet("color: #f38ba8; font-size: 13px;")
            self.status_detail_label.setText(
                "Click Refresh after starting the server, or use "
                "**Start the server** for the exact commands to run."
            )
            self.next_btn.setVisible(True)
        else:
            installed = status.available_models
            if installed:
                self.status_label.setText(
                    f"Ollama is running at {status.server_url} · "
                    f"{len(installed)} model(s) installed."
                )
                self.status_label.setStyleSheet("color: #a6e3a1; font-size: 13px;")
                self.status_detail_label.setText(
                    "Pick one of the installed models above, or run "
                    "`ollama pull <model_name>` in a new terminal and "
                    "click Refresh to add a new one."
                )
            else:
                self.status_label.setText(
                    f"Ollama is running at {status.server_url} · no models installed yet."
                )
                self.status_label.setStyleSheet("color: #f9e2af; font-size: 13px;")
                self.status_detail_label.setText(
                    "Run `ollama pull <model_name>` in a new terminal, "
                    "then click Refresh to populate the list."
                )
            self.next_btn.setVisible(False)

    def _refresh_all(self) -> None:
        status = probe_ollama_backend(self._server_url, self._current_model)
        self._populate_models(status)
        self._set_status(status)
        self._refresh_diagnostics()

    def _refresh_diagnostics(self) -> None:
        self.ram_label.setText("RAM: decoupled")
        self.ram_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
        self.vram_label.setText("VRAM: decoupled")
        self.vram_label.setStyleSheet("color: #a6adc8; font-size: 12px;")

    def _on_confirm(self) -> None:
        # Always enabled: the user clicks whenever they are ready.
        # Downstream surfaces model-not-found if the list is empty.
        selection = self.current_selection()
        self.confirmed.emit(selection)


class OllamaStartHintWidget(QWidget):
    """Start-hint page shown only when the Ollama server is unreachable.

    Two read-only copy boxes stacked vertically — ``ollama serve`` and
    ``ollama pull <model_name>`` — let the user run the commands in a
    terminal of their own. **Back** returns to the probe page;
    **Next** re-runs the probe and, on success, emits the same
    ``confirmed(OllamaSetupSelection)`` signal the probe page uses, so
    ``MainWindow`` only needs one completion path.
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
        self.setObjectName("ollamaStartHintWidget")
        self._server_url = str(server_url)
        self._current_model = str(current_model)
        self._start_command = "ollama serve"
        # The pull command is generic — there is no "configured model"
        # to fall back on. The user runs `ollama pull <model_name>`
        # with whichever tag they want.
        self._pull_command = (
            f"ollama pull {current_model}" if current_model else "ollama pull <model_name>"
        )

        outer = QVBoxLayout(self)
        outer.addStretch(1)

        inner = QFrame(self)
        inner.setObjectName("ollamaStartHintCard")
        inner.setStyleSheet(
            "#ollamaStartHintCard {"
            "  background-color: #1e1e2e;"
            "  border: 1px solid #45475a;"
            "  border-radius: 8px;"
            "}"
        )
        inner.setMaximumWidth(620)

        layout = QVBoxLayout(inner)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(12)

        title = QLabel("Start the Ollama server", inner)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #cdd6f4;")
        layout.addWidget(title)

        self.subtitle = QLabel(
            f"GRC Agent could not reach the Ollama server at {self._server_url}. "
            "Copy the commands below into a new terminal, run them, then "
            "click **Next** to retry the probe.",
            inner,
        )
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet("color: #a6adc8; font-size: 13px;")
        layout.addWidget(self.subtitle)

        layout.addSpacing(8)

        layout.addWidget(self._build_command_section(
            "Step 1 — start the daemon",
            self._start_command,
            "ollamaStartHintStartCopyButton",
        ))

        layout.addWidget(self._build_command_section(
            "Step 2 — pull a model",
            self._pull_command,
            "ollamaStartHintPullCopyButton",
        ))

        self.status_label = QLabel("", inner)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
        layout.addWidget(self.status_label)

        self.footer = QLabel(
            "GRC Agent does not start the Ollama daemon and does not "
            "download models — you are responsible for both. The agent "
            "communicates with the server you provide.",
            inner,
        )
        self.footer.setWordWrap(True)
        self.footer.setStyleSheet("color: #a6adc8; font-size: 12px;")
        layout.addWidget(self.footer)

        layout.addStretch(1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)

        self.cancel_btn = QPushButton("Back", inner)
        self.cancel_btn.setObjectName("ollamaStartHintBackButton")
        self.cancel_btn.clicked.connect(self.cancelled.emit)
        button_row.addWidget(self.cancel_btn)

        self.next_btn = QPushButton("Next", inner)
        self.next_btn.setObjectName("ollamaStartHintNextButton")
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self._on_next)
        button_row.addWidget(self.next_btn)

        layout.addLayout(button_row)

        h_row = QHBoxLayout()
        h_row.addStretch(1)
        h_row.addWidget(inner)
        h_row.addStretch(1)
        outer.addLayout(h_row)
        outer.addStretch(1)

    def _build_command_section(
        self,
        heading: str,
        command: str,
        copy_button_object_name: str,
    ) -> QWidget:
        """Build a small section: heading + read-only command + copy button."""
        section = QWidget(self)
        section_layout = QVBoxLayout(section)
        section_layout.setContentsMargins(0, 0, 0, 0)
        section_layout.setSpacing(4)

        heading_label = QLabel(heading, section)
        heading_label.setStyleSheet(
            "color: #cdd6f4; font-size: 13px; font-weight: bold;"
        )
        section_layout.addWidget(heading_label)

        row = QHBoxLayout()
        command_field = QLineEdit(command, section)
        command_field.setReadOnly(True)
        command_field.setObjectName(
            f"{copy_button_object_name}Field"
        )
        command_field.setStyleSheet(
            "QLineEdit { background-color: #181825; color: #a6e3a1; "
            "border: 1px solid #45475a; border-radius: 4px; "
            "padding: 6px 10px; font-family: monospace; font-size: 12px; }"
        )
        row.addWidget(command_field, stretch=1)

        copy_button = QPushButton("Copy", section)
        copy_button.setObjectName(copy_button_object_name)
        copy_button.clicked.connect(lambda: _copy_to_clipboard(command))
        row.addWidget(copy_button)

        section_layout.addLayout(row)
        return section

    def update_status(self, status: OllamaBackendStatus) -> None:
        """Render the result of a re-probe on the start-hint page.

        Called by ``MainWindow`` after the user clicks **Next**. When the
        server is reachable, the caller should treat the widget as
        "done" — :meth:`confirm` emits the shared
        ``OllamaSetupSelection`` signal and the wizard moves on.
        """
        if status.server_reachable:
            self.status_label.setText(
                f"Connected to Ollama at {status.server_url} · "
                f"{len(status.available_models)} model(s) available."
            )
            self.status_label.setStyleSheet("color: #a6e3a1; font-size: 12px;")
        else:
            self.status_label.setText(
                f"Still not reachable at {status.server_url}. Run the "
                "commands above in another terminal, then click Next again."
            )
            self.status_label.setStyleSheet("color: #f38ba8; font-size: 12px;")

    def _on_next(self) -> None:
        status = probe_ollama_backend(self._server_url, self._current_model)
        self.update_status(status)
        if not status.server_reachable:
            return
        # The wizard hands the user back to page 2 (the model list)
        # by routing via MainWindow's `_on_setup_ollama_next_requested`
        # path; the page-3 widget itself does not emit ``confirmed``
        # — it only forwards a successful re-probe so the caller can
        # re-route. We carry the first installed model name so the
        # user has a sensible default to confirm in page 2.
        self.confirmed.emit(
            OllamaSetupSelection(
                server_url=self._server_url,
                model_name=(
                    status.available_models[0] if status.available_models else self._current_model
                ),
            )
        )


__all__ = [
    "CONFIRM_OLLAMA_EXTERNAL_TEXT",
    "DEFAULT_OLLAMA_SERVER_URL",
    "OllamaSetupSelection",
    "OllamaSetupWidget",
    "OllamaStartHintWidget",
    "PROVIDER_OLLAMA",
    "PROVIDER_OPENROUTER",
    "ProviderPickerWidget",
]
