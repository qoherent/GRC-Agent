import logging
import os
from typing import Any

from grc_agent.domain_models import BlockRole
from PySide6.QtCore import QProcess, Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


class InspectorWidget(QWidget):
    """Sidebar inspector displaying active GRC flowgraph state (variables, blocks, connections).

    Implements:
    - State-preserving updates (scroll, expansion) on refresh.
    - Stable Qt.UserRole keys for category identification (audit 5.2) so
      that expansion state survives renames / future label changes.
    - Explicit scroll clamping (audit 5.3) when the new range is smaller
      than the previous scroll value.
    - Failure handling on QProcess.startDetached (audit 5.6) so a missing
      ``gnuradio-companion`` binary surfaces a user-visible message
      instead of failing silently.

    The widget currently consumes the ``inspect_graph`` **overview** payload
    only (audit 5.4). The variables table reads ``value`` for blocks whose
    ``role == "variable"``; ``variable_*`` variants and non-variable
    blocks do not have a ``value`` field in the overview and will show
    an empty cell. Per-block parameter details require the ``details``
    view, which is intentionally out of scope for this sidebar widget.
    """

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.grc_file_path = ""

        layout = QVBoxLayout(self)

        # ToolBar / Header panel with Open in GRC button
        header_layout = QHBoxLayout()
        self.header_label = QLabel("<b>Flowgraph Inspector</b>", self)
        header_layout.addWidget(self.header_label)

        header_layout.addStretch()

        self.open_grc_btn = QPushButton("Open in GRC", self)
        self.open_grc_btn.setEnabled(False)
        self.open_grc_btn.clicked.connect(self.open_in_grc)
        self.open_grc_btn.setToolTip("Open the active flowgraph in the GNU Radio Companion editor.")
        header_layout.addWidget(self.open_grc_btn)

        layout.addLayout(header_layout)

        # Section 1: Variables Table
        layout.addWidget(QLabel("<b>Variables</b>", self))
        self.variables_table = QTableWidget(self)
        self.variables_table.setColumnCount(2)
        self.variables_table.setHorizontalHeaderLabels(["Name", "Value"])
        self.variables_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.variables_table)

        # Section 2: Blocks Tree
        layout.addWidget(QLabel("<b>Blocks</b>", self))
        self.blocks_tree = QTreeWidget(self)
        self.blocks_tree.setHeaderLabels(["Instance Name", "Block Type"])
        self.blocks_tree.header().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.blocks_tree)

        # Section 3: Connections Wires
        layout.addWidget(QLabel("<b>Connections</b>", self))
        self.connections_list = QListWidget(self)
        layout.addWidget(self.connections_list)

    def set_grc_file_path(self, path: str) -> None:
        """Update the path to the active GRC file and enable the companion launch button."""
        self.grc_file_path = path
        self.open_grc_btn.setEnabled(bool(path))
        # Reset failure state when the path is reassigned.
        if path:
            self.open_grc_btn.setToolTip(
                "Open the active flowgraph in the GNU Radio Companion editor."
            )

    def open_in_grc(self) -> None:
        """Launch the official gnuradio-companion GUI editor detached.

        If the binary is not on PATH, ``QProcess.startDetached`` returns
        ``(False, 0)``; we disable the button and surface a tooltip so the
        failure is not silent (audit 5.6).
        """
        if not self.grc_file_path:
            logger.warning("No active GRC file path configured to open.")
            return

        if not os.path.exists(self.grc_file_path):
            self.open_grc_btn.setEnabled(False)
            self.open_grc_btn.setToolTip(
                f"GRC file no longer exists at {self.grc_file_path}. "
                "Reload the session to refresh the path."
            )
            logger.warning(
                "Cannot launch gnuradio-companion: file not found at %s",
                self.grc_file_path,
            )
            return

        logger.info(f"Launching gnuradio-companion detached for file: {self.grc_file_path}")
        result = QProcess.startDetached("gnuradio-companion", [self.grc_file_path])
        # PySide6 returns a tuple (ok: bool, pid: int) on success, or
        # False on failure depending on the binding version. Handle both.
        ok = bool(result[0]) if isinstance(result, tuple) else bool(result)
        if not ok:
            self.open_grc_btn.setEnabled(False)
            self.open_grc_btn.setToolTip(
                "gnuradio-companion binary not found in PATH. "
                "Install GNU Radio or add its bin directory to PATH."
            )
            logger.warning(
                "Failed to launch gnuradio-companion for %s: binary not found.",
                self.grc_file_path,
            )

    def update_state(self, inspect_graph_data: dict[str, Any]) -> None:
        """Parse inspect_graph tool payload and update sub-widgets with state preservation."""
        # 1. Capture user UI states before clearing. Use Qt.UserRole
        # (audit 5.2) for a stable identifier that survives display
        # label changes.
        expanded_ids: set[str] = set()
        for i in range(self.blocks_tree.topLevelItemCount()):
            item = self.blocks_tree.topLevelItem(i)
            if item.isExpanded():
                key = item.data(0, Qt.UserRole)
                if key is not None:
                    expanded_ids.add(str(key))

        # Scroll positions.
        tree_scroll = self.blocks_tree.verticalScrollBar().value()
        table_scroll = self.variables_table.verticalScrollBar().value()
        conn_scroll = self.connections_list.verticalScrollBar().value()

        # 2. Extract sections from overview payload (flat shape, Phase 6+).
        graph = inspect_graph_data.get("graph") or {}
        blocks = graph.get("blocks", []) or []
        connections = graph.get("connections", []) or []

        variables = [
            b for b in blocks if b.get("role") == "variable"
        ]
        self.variables_table.setRowCount(0)
        self.variables_table.setRowCount(len(variables))
        for row_idx, var in enumerate(variables):
            name_item = QTableWidgetItem(str(var.get("instance_name", "")))
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            params_dict = var.get("params", {}) or {}
            val_str = str(params_dict.get("value", ""))
            val_item = QTableWidgetItem(val_str)
            val_item.setFlags(val_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.variables_table.setItem(row_idx, 0, name_item)
            self.variables_table.setItem(row_idx, 1, val_item)

        # 4. Repopulate Blocks Tree (grouped categorisation)
        # Categories are derived solely from the native block ``role`` emitted
        # by ``domain_models.BlockRole`` (StrEnum). There is no
        # substring matching on block_id/role — one uniform rule.
        self.blocks_tree.clear()

        categories: dict[str, list[dict[str, Any]]] = {
            "variables": [],
            "sources": [],
            "sinks": [],
            "other_blocks": [],
        }
        display_names = {
            "variables": "Variables",
            "sources": "Sources",
            "sinks": "Sinks",
            "other_blocks": "Other Blocks",
        }
        # Native BlockRole -> GUI category. Roles not listed here
        # (transform, message_or_event, metadata, unknown) fold into
        # ``other_blocks``.
        category_for_role = {
            BlockRole.VARIABLE: "variables",
            BlockRole.SOURCE: "sources",
            BlockRole.SINK: "sinks",
        }

        for block in blocks:
            role = BlockRole(str(block.get("role", "")).lower())
            categories[category_for_role.get(role, "other_blocks")].append(block)

        for cat_key, cat_blocks in categories.items():
            if not cat_blocks:
                continue

            cat_node = QTreeWidgetItem(self.blocks_tree)
            cat_node.setText(0, display_names[cat_key])
            # Store the stable key for state preservation across refreshes.
            cat_node.setData(0, Qt.UserRole, cat_key)

            for block in cat_blocks:
                name = str(block.get("instance_name", ""))
                child = QTreeWidgetItem(cat_node)
                child.setText(0, name)
                child.setText(1, str(block.get("block_id", "")))

                # Inline parameters (no _block_params sidecar — Phase 6+).
                params_dict = block.get("params", {}) or {}
                for pkey, pval in params_dict.items():
                    param_item = QTreeWidgetItem(child)
                    param_item.setText(0, str(pkey))
                    param_item.setText(1, str(pval))

        # 5. Repopulate Connections Wires
        self.connections_list.clear()
        for conn in connections:
            self.connections_list.addItem(str(conn))

        # 6. Restore captured user UI states
        for i in range(self.blocks_tree.topLevelItemCount()):
            item = self.blocks_tree.topLevelItem(i)
            key = item.data(0, Qt.UserRole)
            if key is not None and str(key) in expanded_ids:
                item.setExpanded(True)

        # 7. Explicit scroll clamp (audit 5.3): Qt silently clamps
        # setValue but a larger old value can be confusing when restoring.
        for bar, old in (
            (self.blocks_tree.verticalScrollBar(), tree_scroll),
            (self.variables_table.verticalScrollBar(), table_scroll),
            (self.connections_list.verticalScrollBar(), conn_scroll),
        ):
            clamped = max(bar.minimum(), min(old, bar.maximum()))
            bar.setValue(clamped)
