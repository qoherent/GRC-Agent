"""Model-selector dialog for the GRC Agent GUI.

Phase 2 of the model-selector rollout. Surfaces three pieces of
information to the user:

1. A ``QComboBox`` listing every ``.gguf`` discoverable in the local
   Hugging Face cache (and an optional ``[llama].models_dir``).
2. A confirm-strip row with a "Switch model" button. The button is
   disabled until the user picks a different model than the one
   currently loaded. The actual swap is wired in Phase 3.
3. A system-specs panel showing GPU/VRAM/RAM/CPU. Compact one-liner in
   the label, full names in the tooltip.

The dialog is intentionally non-modal: a user can keep chatting while
it is open. The chat input and the Validate button are disabled while
the dialog is open, mirroring the existing pattern for the
``ProcessManager`` lock (see ``MainWindow.on_process_started``).

Public surface:

- :class:`ModelDialog` — the dialog itself. Emits
  ``model_accepted(CachedModel)`` when the user confirms a selection.
- :func:`discover_models_for_dialog` — runs ``discover_cached_models``
  with the right paths and returns the list. Centralized so the
  MainWindow can call it from the worker thread without re-implementing
  the path resolution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from grc_agent.model_manager import (
    CachedModel,
    SystemSpecs,
    discover_cached_models,
    list_system_specs,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
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
    """The user-confirmed pick from the model selector.

    Captures both the dropdown's resolved :class:`CachedModel` and the
    optional explicit alias the user typed. ``alias_override`` is empty
    unless the user supplied one — Phase 3 will use it to disambiguate
    the llama.cpp ``--alias`` flag when two repos ship the same
    filename.
    """

    cached_model: CachedModel
    alias_override: str


def discover_models_for_dialog(
    *,
    hf_cache: Path | None = None,
    models_dir: Path | None = None,
) -> list[CachedModel]:
    """Run ``discover_cached_models`` with the supplied paths.

    Wraps the model_manager call so the dialog does not need to know
    the exact Path resolution rules. The MainWindow also uses this
    helper from its worker thread.
    """
    return discover_cached_models(hf_cache=hf_cache, models_dir=models_dir)


def _format_size_compact(num_bytes: int | None) -> str:
    """Return a compact human-readable size, or 'n/a' for None."""
    if num_bytes is None:
        return "n/a"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} KiB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f} MiB"
    return f"{num_bytes / (1024 * 1024 * 1024):.2f} GiB"


def _truncate_for_label(text: str, max_length: int = 32) -> str:
    """Truncate ``text`` for compact label rendering.

    Uses the middle-ellipsis form (``foo…bar``) so a long CPU model
    name stays recognizable. The full text is preserved for the
    tooltip.
    """
    if len(text) <= max_length:
        return text
    if max_length <= 4:
        return text[:max_length]
    keep = (max_length - 1) // 2
    return f"{text[:keep]}…{text[-keep:]}"


def _format_specs_compact(specs: SystemSpecs) -> tuple[str, str]:
    """Return ``(compact_label, full_tooltip)`` for the system specs."""
    parts: list[str] = []
    full_parts: list[str] = []
    if specs.gpu_name:
        parts.append(f"GPU: {_truncate_for_label(specs.gpu_name)}")
        vram = _format_size_compact(specs.gpu_vram_bytes)
        if specs.gpu_vram_bytes is not None:
            parts[-1] = f"{parts[-1]} ({vram})"
        full_parts.append(f"GPU: {specs.gpu_name} ({vram} VRAM)")
    else:
        parts.append("GPU: unknown")
        full_parts.append("GPU: unknown (nvidia-smi not found or no GPU)")
    if specs.ram_bytes is not None:
        parts.append(f"RAM: {_format_size_compact(specs.ram_bytes)}")
        full_parts.append(f"RAM: {_format_size_compact(specs.ram_bytes)}")
    else:
        parts.append("RAM: unknown")
        full_parts.append("RAM: unknown (/proc/meminfo unavailable)")
    if specs.cpu_name:
        parts.append(f"CPU: {_truncate_for_label(specs.cpu_name)}")
        full_parts.append(
            f"CPU: {specs.cpu_name}"
            + (
                f" ({specs.cpu_cores_logical} logical cores)"
                if specs.cpu_cores_logical is not None
                else ""
            )
        )
    else:
        parts.append("CPU: unknown")
        full_parts.append("CPU: unknown (/proc/cpuinfo unavailable)")
    return " · ".join(parts), "\n".join(full_parts)


class ModelDialog(QDialog):
    """Non-modal model selector with confirm strip and system-specs panel.

    The dropdown shows ``<filename> · <size>`` for each discovered
    model. The current model is preselected. The "Switch model" button
    is enabled only when the user picks a *different* model, to
    prevent the no-op "I clicked it by accident" case.

    Emits :pyattr:`model_accepted` when the user clicks the confirm
    button. The MainWindow's Phase-3 swap wiring is responsible for
    turning that signal into a live model swap.
    """

    model_accepted = Signal(ModelDialogSelection)

    def __init__(
        self,
        *,
        current_model: CachedModel | None,
        models: list[CachedModel],
        specs: SystemSpecs | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Model")
        self.setModal(False)  # non-modal per the agreed design
        self.setMinimumWidth(560)

        self._models: list[CachedModel] = list(models)
        self._current_model = current_model
        self._specs = specs or list_system_specs()
        self._current_index: int | None = None

        layout = QVBoxLayout(self)

        # -- Dropdown row --
        dropdown_row = QHBoxLayout()
        dropdown_label = QLabel("Model file:", self)
        dropdown_label.setMinimumWidth(70)
        dropdown_row.addWidget(dropdown_label)

        self.combo = QComboBox(self)
        self.combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.combo.setToolTip(
            "All .gguf files in the local Hugging Face cache "
            "(and any [llama].models_dir from grc_agent.toml)."
        )
        self._populate_combo()
        dropdown_row.addWidget(self.combo, stretch=1)
        layout.addLayout(dropdown_row)

        # -- Confirm strip --
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

        # -- System specs panel --
        separator = QFrame(self)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator)

        compact, full_tooltip = _format_specs_compact(self._specs)
        self.specs_label = QLabel(compact, self)
        self.specs_label.setStyleSheet("color: #cdd6f4;")
        self.specs_label.setToolTip(full_tooltip)
        self.specs_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.specs_label)

        # -- Discovery metadata --
        cache_label_text = self._cache_label_text()
        cache_label = QLabel(cache_label_text, self)
        cache_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
        cache_label.setWordWrap(True)
        layout.addWidget(cache_label)

        self.combo.currentIndexChanged.connect(self._on_combo_changed)
        self._refresh_button_state()

    def _populate_combo(self) -> None:
        """Fill the dropdown with one row per discovered model."""
        for index, model in enumerate(self._models):
            size = _format_size_compact(model.size_bytes)
            self.combo.addItem(
                f"{model.filename}  ·  {size}",
                userData=index,
            )
            if (
                self._current_model is not None
                and model.hf_repo == self._current_model.hf_repo
                and model.filename == self._current_model.filename
            ):
                self._current_index = index
        if self._current_index is not None:
            self.combo.setCurrentIndex(self._current_index)
        if not self._models:
            self.combo.addItem("(no .gguf files found)", userData=-1)
            self.combo.setEnabled(False)

    def _cache_label_text(self) -> str:
        """Return a one-line description of where models were scanned."""
        count = len(self._models)
        if count == 0:
            return (
                "No .gguf files found. Download a model with `grc-agent chat` "
                "or place .gguf files under [llama].models_dir."
            )
        return f"Scanned {count} model file(s) from the local Hugging Face cache."

    def _on_combo_changed(self, _index: int) -> None:
        self._refresh_button_state()

    def _refresh_button_state(self) -> None:
        """Enable Switch only when the picked model differs from the loaded one."""
        if not self._models:
            self.switch_btn.setEnabled(False)
            self.alias_input_note.setText("No models to switch to.")
            return
        picked = self.combo.currentData()
        if picked is None or picked < 0:
            self.switch_btn.setEnabled(False)
            self.alias_input_note.setText("")
            return
        if self._current_index is None or picked != self._current_index:
            self.switch_btn.setEnabled(True)
            model = self._models[picked]
            self.alias_input_note.setText(
                f"Restart llama.cpp with {model.hf_model_token}"
            )
        else:
            self.switch_btn.setEnabled(False)
            self.alias_input_note.setText("Already loaded.")

    def _on_switch_clicked(self) -> None:
        picked_index = self.combo.currentData()
        if picked_index is None or picked_index < 0:
            return
        if not (0 <= picked_index < len(self._models)):
            return
        model = self._models[picked_index]
        selection = ModelDialogSelection(
            cached_model=model,
            alias_override="",
        )
        self.model_accepted.emit(selection)
        self.accept()

    def selected_model(self) -> CachedModel | None:
        """Return the currently selected model, or None if the dropdown is empty."""
        picked_index = self.combo.currentData()
        if picked_index is None or picked_index < 0:
            return None
        if not (0 <= picked_index < len(self._models)):
            return None
        return self._models[picked_index]


__all__ = [
    "ModelDialog",
    "ModelDialogSelection",
    "discover_models_for_dialog",
]
