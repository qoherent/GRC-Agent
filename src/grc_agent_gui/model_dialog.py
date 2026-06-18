"""Model-selector selection dataclass.

The legacy ``ModelDialog`` QDialog was removed when the inline
:class:`ModelToolbar` replaced the wizard. Only the selection dataclass
is still imported by :mod:`grc_agent_gui.main_window`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelDialogSelection:
    """The user-confirmed pick from the model selector."""

    backend: str
    ollama_model_name: str | None = None


__all__ = ["ModelDialogSelection"]
