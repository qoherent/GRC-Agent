"""Recent Sessions Sidebar for the GRC Agent GUI.

Lists past chat sessions and provides controls to start a new chat
or reload past sessions directly inside the main window.
"""

from __future__ import annotations

import logging

from grc_agent.sessions_store import SessionRecord
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class SidebarWidget(QWidget):
    """Left sidebar listing past sessions, with 'New Chat' and collapse button."""

    session_selected = Signal(int)  # session_id
    new_chat_requested = Signal()
    collapse_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sessionSidebar")
        self.setStyleSheet(
            "#sessionSidebar { background-color: #181825; border-right: 1px solid #313244; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Header controls
        header = QHBoxLayout()
        header.setSpacing(4)

        self.new_chat_btn = QPushButton("+ New Chat", self)
        self.new_chat_btn.setObjectName("newChatButton")
        self.new_chat_btn.setToolTip("Start a fresh chat session")
        self.new_chat_btn.setStyleSheet(
            "QPushButton { background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; "
            "border-radius: 4px; padding: 6px 12px; font-weight: bold; } "
            "QPushButton:hover { background-color: #45475a; }"
        )
        self.new_chat_btn.clicked.connect(self.new_chat_requested.emit)
        header.addWidget(self.new_chat_btn, stretch=1)

        self.collapse_btn = QPushButton("◀", self)
        self.collapse_btn.setObjectName("collapseSidebarButton")
        self.collapse_btn.setToolTip("Collapse Sidebar")
        self.collapse_btn.setStyleSheet(
            "QPushButton { background-color: transparent; color: #a6adc8; border: none; font-size: 14px; padding: 4px; } "
            "QPushButton:hover { color: #f38ba8; }"
        )
        self.collapse_btn.clicked.connect(self.collapse_requested.emit)
        header.addWidget(self.collapse_btn)

        layout.addLayout(header)

        # Section Label
        label = QLabel("Recent Chats", self)
        label.setStyleSheet(
            "color: #bac2de; font-size: 11px; font-weight: bold; margin-top: 6px; padding-left: 2px;"
        )
        layout.addWidget(label)

        # List of sessions
        self.list_widget = QListWidget(self)
        self.list_widget.setStyleSheet(
            "QListWidget { background-color: transparent; border: none; color: #cdd6f4; } "
            "QListWidget::item { padding: 6px; border-bottom: 1px solid #1e1e2e; border-radius: 4px; } "
            "QListWidget::item:hover { background-color: #313244; } "
            "QListWidget::item:selected { background-color: #45475a; color: #a6e3a1; font-weight: bold; }"
        )
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list_widget)

    def populate_sessions(self, sessions: list[SessionRecord]) -> None:
        """Clear the list and repopulate it with the provided session records."""
        self.list_widget.clear()
        for s in sessions:
            title = s.title or "(untitled)"
            display_date = s.started_at[:10] if len(s.started_at) >= 10 else s.started_at
            item = QListWidgetItem(f"💬 {title}\n  {display_date} · msgs={s.message_count}")
            item.setData(Qt.ItemDataRole.UserRole, s.id)
            item.setToolTip(
                f"Session #{s.id}\nModel: {s.model_alias or 'N/A'}\nGraph: {s.graph_path or 'N/A'}"
            )
            self.list_widget.addItem(item)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        session_id = item.data(Qt.ItemDataRole.UserRole)
        if session_id is not None:
            self.session_selected.emit(session_id)
