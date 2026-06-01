import os
import sys
from unittest.mock import patch

# Add src to system path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

try:
    from grc_agent_gui.inspector import InspectorWidget
except ImportError:
    InspectorWidget = None


def test_inspector_widget_imports_exist():
    """Assert that InspectorWidget module exists and can be imported under TDD."""
    assert InspectorWidget is not None, "InspectorWidget class not implemented yet"


def test_variables_table_mapping(qtbot):
    """Verify that variables from inspect_graph populate QTableWidget rows and columns."""
    widget = InspectorWidget()
    qtbot.addWidget(widget)
    
    mock_payload = {
        "ok": True,
        "view": "overview",
        "state_revision": 1,
        "summary": {
            "blocks": [
                {"instance_name": "samp_rate", "block_type": "variable", "role": "variable", "value": "32000"},
                {"instance_name": "freq", "block_type": "variable", "role": "variable", "value": "1000"},
                {"instance_name": "analog_sig_source_x_0", "block_type": "analog_sig_source_x", "role": "source"}
            ]
        }
    }
    
    widget.update_state(mock_payload)
    
    # We should have exactly 2 variables (samp_rate and freq) in the variables table
    assert widget.variables_table.rowCount() == 2
    
    # Check values
    name_item_1 = widget.variables_table.item(0, 0)
    val_item_1 = widget.variables_table.item(0, 1)
    assert name_item_1.text() == "samp_rate"
    assert val_item_1.text() == "32000"
    
    name_item_2 = widget.variables_table.item(1, 0)
    val_item_2 = widget.variables_table.item(1, 1)
    assert name_item_2.text() == "freq"
    assert val_item_2.text() == "1000"


def test_blocks_tree_mapping(qtbot):
    """Verify that blocks from inspect_graph populate QTreeWidget categorised by role."""
    widget = InspectorWidget()
    qtbot.addWidget(widget)
    
    mock_payload = {
        "ok": True,
        "view": "overview",
        "state_revision": 1,
        "summary": {
            "blocks": [
                {"instance_name": "samp_rate", "block_type": "variable", "role": "variable", "value": "32000"},
                {"instance_name": "analog_sig_source_x_0", "block_type": "analog_sig_source_x", "role": "source"},
                {"instance_name": "blocks_throttle_0", "block_type": "blocks_throttle", "role": "throttle"}
            ]
        }
    }
    
    widget.update_state(mock_payload)
    
    # Check that categories exist as top level items in the tree
    top_items = []
    for i in range(widget.blocks_tree.topLevelItemCount()):
        top_items.append(widget.blocks_tree.topLevelItem(i).text(0))
        
    assert "Sources" in top_items
    assert "Variables" in top_items
    assert "Other Blocks" in top_items


def test_inspector_preserves_scroll_and_expansion(qtbot):
    """Verify state-preserving updates restore tree expanded states and scroll values."""
    widget = InspectorWidget()
    qtbot.addWidget(widget)
    
    mock_payload_1 = {
        "ok": True,
        "view": "overview",
        "state_revision": 1,
        "summary": {
            "blocks": [
                {"instance_name": "samp_rate", "block_type": "variable", "role": "variable", "value": "32000"},
                {"instance_name": "analog_sig_source_x_0", "block_type": "analog_sig_source_x", "role": "source"}
            ]
        }
    }
    
    # Initial load
    widget.update_state(mock_payload_1)
    
    # Expand "Sources" category
    sources_item = None
    for i in range(widget.blocks_tree.topLevelItemCount()):
        item = widget.blocks_tree.topLevelItem(i)
        if item.text(0) == "Sources":
            sources_item = item
            break
            
    assert sources_item is not None
    sources_item.setExpanded(True)
    assert sources_item.isExpanded()
    
    # Mock scroll range and value for headless testing
    widget.blocks_tree.verticalScrollBar().setRange(0, 100)
    widget.blocks_tree.verticalScrollBar().setValue(50)
    
    # Perform refresh
    widget.update_state(mock_payload_1)
    
    # Assert expanded state and scroll are preserved
    new_sources_item = None
    for i in range(widget.blocks_tree.topLevelItemCount()):
        item = widget.blocks_tree.topLevelItem(i)
        if item.text(0) == "Sources":
            new_sources_item = item
            break
            
    assert new_sources_item.isExpanded(), "Expanded category state was lost"
    assert widget.blocks_tree.verticalScrollBar().value() == 50


def test_open_in_grc_is_detached(qtbot):
    """Assert clicking "Open GRC" invokes gnuradio-companion detached."""
    widget = InspectorWidget()
    qtbot.addWidget(widget)
    
    widget.set_grc_file_path("/tmp/test_flowgraph.grc")
    
    with patch("PySide6.QtCore.QProcess.startDetached") as mock_start_detached:
        widget.open_in_grc()
        
        # Verify detached process was run with the right path argument
        mock_start_detached.assert_called_once_with("gnuradio-companion", ["/tmp/test_flowgraph.grc"])
