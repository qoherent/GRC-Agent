import logging
from typing import Any
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QListWidget,
    QLabel,
    QPushButton,
    QHBoxLayout,
    QHeaderView,
)

logger = logging.getLogger(__name__)


class InspectorWidget(QWidget):
    """Sidebar inspector displaying active GRC flowgraph state (variables, blocks, connections).
    
    Implements State-Preserving Updates to prevent scroll/expansion resets on refreshes.
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

    def open_in_grc(self) -> None:
        """Launch the official gnuradio-companion GUI editor detached."""
        if not self.grc_file_path:
            logger.warning("No active GRC file path configured to open.")
            return
            
        logger.info(f"Launching gnuradio-companion detached for file: {self.grc_file_path}")
        # Uses detached start to prevent blocking PySide6 main event loop
        QProcess.startDetached("gnuradio-companion", [self.grc_file_path])

    def update_state(self, inspect_graph_data: dict[str, Any]) -> None:
        """Parse inspect_graph tool payload and update sub-widgets with state preservation."""
        # 1. Capture user UI states before clearing
        # Tree expansion states (we map category name -> expanded bool)
        expanded_nodes = set()
        for i in range(self.blocks_tree.topLevelItemCount()):
            item = self.blocks_tree.topLevelItem(i)
            if item.isExpanded():
                expanded_nodes.add(item.text(0))
                
        # Scroll positions
        tree_scroll = self.blocks_tree.verticalScrollBar().value()
        table_scroll = self.variables_table.verticalScrollBar().value()
        conn_scroll = self.connections_list.verticalScrollBar().value()
        
        # 2. Extract sections from overview payload
        summary = inspect_graph_data.get("summary", {})
        blocks = summary.get("blocks", [])
        connections = summary.get("connections", [])
        
        # 3. Repopulate Variables Table
        variables = [b for b in blocks if b.get("block_type") == "variable" or b.get("role") == "variable"]
        self.variables_table.setRowCount(0)
        self.variables_table.setRowCount(len(variables))
        for row_idx, var in enumerate(variables):
            name_item = QTableWidgetItem(str(var.get("instance_name", "")))
            val_item = QTableWidgetItem(str(var.get("value", "")))
            self.variables_table.setItem(row_idx, 0, name_item)
            self.variables_table.setItem(row_idx, 1, val_item)
            
        # 4. Repopulate Blocks Tree (grouped categorisation)
        self.blocks_tree.clear()
        
        categories = {
            "Variables": [],
            "Sources": [],
            "Sinks": [],
            "Filters": [],
            "Other Blocks": [],
        }
        
        for block in blocks:
            role = str(block.get("role", "")).lower()
            btype = str(block.get("block_type", "")).lower()
            
            # Map block to categorisation bucket
            if role == "variable" or btype == "variable":
                categories["Variables"].append(block)
            elif "source" in role or "source" in btype:
                categories["Sources"].append(block)
            elif "sink" in role or "sink" in btype:
                categories["Sinks"].append(block)
            elif "filter" in role or "filter" in btype or "resampler" in btype or "decimator" in btype:
                categories["Filters"].append(block)
            else:
                categories["Other Blocks"].append(block)
                
        # Create category nodes and mount children
        for cat_name, cat_blocks in categories.items():
            if not cat_blocks:
                continue
                
            cat_node = QTreeWidgetItem(self.blocks_tree)
            cat_node.setText(0, cat_name)
            
            for block in cat_blocks:
                child = QTreeWidgetItem(cat_node)
                child.setText(0, str(block.get("instance_name", "")))
                child.setText(1, str(block.get("block_type", "")))
                
        # 5. Repopulate Connections Wires
        self.connections_list.clear()
        for conn in connections:
            self.connections_list.addItem(str(conn))
            
        # 6. Restore captured user UI states
        # Restore tree expansion states
        for i in range(self.blocks_tree.topLevelItemCount()):
            item = self.blocks_tree.topLevelItem(i)
            if item.text(0) in expanded_nodes:
                item.setExpanded(True)
                
        # Restore vertical scroll ranges
        self.blocks_tree.verticalScrollBar().setValue(tree_scroll)
        self.variables_table.verticalScrollBar().setValue(table_scroll)
        self.connections_list.verticalScrollBar().setValue(conn_scroll)
