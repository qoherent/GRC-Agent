"""Recent Sessions dialog for the GRC Agent GUI.

Lists past chat sessions from ``~/.grc_agent/sessions.db`` and
lets the user reopen one. Per the agreed design, opening a
``.grc`` always starts a fresh session; "continue last session"
was rejected by the user. The dialog is a one-stop browser for
the user's history.

Public surface:

- :class:`RecentSessionsDialog` — the ``QDialog`` itself. Emits
  ``session_opened(int)`` when the user double-clicks a row or
  clicks "Open".
"""

from __future__ import annotations

import logging
from typing import Any

from grc_agent.sessions_store import MessageRecord, SessionRecord
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class RecentSessionsDialog(QDialog):
    """Browse past chat sessions, double-click to reopen one.

    Two-pane layout: list of sessions on the left, markdown
    preview of the selected session on the right. The preview
    is bounded to the first ~200 lines to keep the dialog
    snappy on long sessions.
    """

    session_opened = Signal(int)  # session_id

    def __init__(
        self,
        *,
        sessions: list[SessionRecord],
        message_preview_loader: Any,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Recent Sessions")
        self.setMinimumSize(820, 480)
        self._sessions: list[SessionRecord] = list(sessions)
        # ``message_preview_loader`` is a callable
        # ``(session_id) -> list[MessageRecord]`` provided by the
        # MainWindow. We keep it injected so this dialog does not
        # need to know about the store's threading model.
        self._message_preview_loader = message_preview_loader

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                f"Found {len(self._sessions)} session(s) in the local "
                "chat-session store. Double-click a row to reopen it in "
                "the chat panel. The new session is opened in read-only "
                "mode (the next user message starts a fresh turn).",
                self,
            )
        )

        splitter = QSplitter(Qt.Horizontal, self)
        layout.addWidget(splitter, stretch=1)

        self.list_widget = QListWidget(splitter)
        self.list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.list_widget.itemSelectionChanged.connect(self._refresh_preview)
        self.list_widget.itemDoubleClicked.connect(self._on_open_clicked)
        for session in self._sessions:
            label = self._format_row_label(session)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, session.id)
            self.list_widget.addItem(item)

        self.preview = QPlainTextEdit(splitter)
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText(
            "Select a session on the left to see its messages here."
        )

        splitter.setSizes([260, 560])

        button_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh", self)
        self.refresh_btn.setObjectName("recentSessionsRefresh")
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        button_row.addWidget(self.refresh_btn)
        button_row.addStretch()

        self.open_btn = QPushButton("Open", self)
        self.open_btn.setObjectName("recentSessionsOpen")
        self.open_btn.setDefault(True)
        self.open_btn.clicked.connect(self._on_open_clicked)
        button_row.addWidget(self.open_btn)

        # Standard dialog button box for Cancel/Esc.
        button_box = QDialogButtonBox(self)
        button_box.setStandardButtons(QDialogButtonBox.StandardButton.Close)
        button_box.rejected.connect(self.reject)
        button_row.addWidget(button_box)

        layout.addLayout(button_row)

        if self._sessions:
            self.list_widget.setCurrentRow(0)

    @staticmethod
    def _format_row_label(session: SessionRecord) -> str:
        status = "open" if session.ended_at is None else "closed"
        return (
            f"#{session.id}  ·  {session.started_at}  ·  "
            f"msgs={session.message_count}  ·  {status}\n"
            f"      {session.title or '(untitled)'}"
        )

    def _refresh_preview(self) -> None:
        current = self.list_widget.currentItem()
        if current is None:
            self.preview.clear()
            return
        session_id = int(current.data(Qt.ItemDataRole.UserRole))
        try:
            messages = self._message_preview_loader(session_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to load session preview: %s", exc)
            self.preview.setPlainText(f"(failed to load session {session_id}: {exc})")
            return
        self.preview.setPlainText(_render_preview(messages))

    def _on_open_clicked(self) -> None:
        current = self.list_widget.currentItem()
        if current is None:
            return
        session_id = int(current.data(Qt.ItemDataRole.UserRole))
        self.session_opened.emit(session_id)
        self.accept()

    def _on_refresh_clicked(self) -> None:
        # Refresh is a no-op for the dialog itself; the MainWindow
        # will close+reopen the dialog to repopulate from disk.
        # The button exists so the user has a visible affordance
        # but the real work is upstream. We just close.
        self.reject()


def _render_preview(messages: list[MessageRecord], *, max_lines: int = 200) -> str:
    """Render a list of messages as a markdown-ish preview.

    Capped at ``max_lines`` so a 10 k-message session does not
    freeze the dialog. The full session is still accessible via
    the Open button, which loads the entire transcript into the
    chat widget.
    """
    role_heading = {
        "user": "## You",
        "assistant": "## Agent",
        "tool_started": "## Tool (started)",
        "tool_finished": "## Tool (finished)",
        "mutation": "## Tool (mutation)",
        "error": "## Error",
        "system": "## System",
    }
    out: list[str] = []
    for msg in messages:
        heading = role_heading.get(msg.role, f"## {msg.role}")
        out.append(heading)
        out.append("")
        if msg.text:
            out.append(msg.text)
        out.append("")
        if len(out) >= max_lines:
            out.append(f"... ({len(messages) - len(out) // 4} more messages truncated)")
            break
    return "\n".join(out)


__all__ = ["RecentSessionsDialog"]
