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
    import os
    import tempfile

    widget = InspectorWidget()
    qtbot.addWidget(widget)

    with tempfile.NamedTemporaryFile(suffix=".grc", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        widget.set_grc_file_path(tmp_path)

        with patch("PySide6.QtCore.QProcess.startDetached") as mock_start_detached:
            mock_start_detached.return_value = (True, 1234)
            widget.open_in_grc()

            mock_start_detached.assert_called_once_with("gnuradio-companion", [tmp_path])
    finally:
        os.unlink(tmp_path)


def test_start_detached_failure_disables_button(qtbot):
    """5.6: when gnuradio-companion is missing, the button must be disabled
    and the tooltip must explain the failure.
    """
    import os
    import tempfile

    widget = InspectorWidget()
    qtbot.addWidget(widget)

    with tempfile.NamedTemporaryFile(suffix=".grc", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        widget.set_grc_file_path(tmp_path)

        with patch("PySide6.QtCore.QProcess.startDetached") as mock_start_detached:
            mock_start_detached.return_value = (False, 0)
            widget.open_in_grc()

        assert not widget.open_grc_btn.isEnabled()
        assert "not found" in widget.open_grc_btn.toolTip().lower()
    finally:
        os.unlink(tmp_path)


def test_expansion_state_uses_user_role(qtbot):
    """5.2: category expansion state is keyed on Qt.UserRole, not display text."""
    from PySide6.QtCore import Qt
    widget = InspectorWidget()
    qtbot.addWidget(widget)

    payload = {
        "ok": True,
        "view": "overview",
        "state_revision": 1,
        "summary": {
            "blocks": [
                {"instance_name": "analog_sig_source_x_0", "block_type": "analog_sig_source_x", "role": "source"}
            ]
        },
    }
    widget.update_state(payload)
    sources_item = None
    for i in range(widget.blocks_tree.topLevelItemCount()):
        item = widget.blocks_tree.topLevelItem(i)
        if item.text(0) == "Sources":
            sources_item = item
            break
    assert sources_item is not None
    # The UserRole key is the stable "sources" identifier.
    assert sources_item.data(0, Qt.UserRole) == "sources"
    sources_item.setExpanded(True)

    # Refresh and confirm expansion survives a render.
    widget.update_state(payload)
    for i in range(widget.blocks_tree.topLevelItemCount()):
        item = widget.blocks_tree.topLevelItem(i)
        if item.text(0) == "Sources":
            assert item.isExpanded()
            break


def test_scroll_clamp_on_smaller_range(qtbot):
    """5.3: scroll restoration must clamp to the new maximum when the range shrinks."""
    widget = InspectorWidget()
    qtbot.addWidget(widget)

    # First load with many blocks so the range is large.
    many_blocks = [
        {"instance_name": f"src_{i}", "block_type": "analog_sig_source_x", "role": "source"}
        for i in range(50)
    ]
    payload = {
        "ok": True,
        "view": "overview",
        "state_revision": 1,
        "summary": {"blocks": many_blocks, "connections": []},
    }
    widget.update_state(payload)
    # Simulate the user scrolling to a high value.
    bar = widget.blocks_tree.verticalScrollBar()
    bar.setRange(0, 1000)
    bar.setValue(900)

    # Now refresh with a tiny payload.
    tiny_payload = {
        "ok": True,
        "view": "overview",
        "state_revision": 2,
        "summary": {"blocks": [], "connections": []},
    }
    widget.update_state(tiny_payload)
    # Scroll must be clamped to the new maximum (which is now 0 since the
    # tree is empty).
    assert 0 <= bar.value() <= bar.maximum()


def test_open_in_grc_disables_button_on_missing_file(qtbot):
    """M9-09: open_in_grc must verify the GRC file exists on disk before
    launching gnuradio-companion. If the file is missing, the button must
    be disabled and a tooltip must explain the failure.
    """
    import os
    import tempfile

    widget = InspectorWidget()
    qtbot.addWidget(widget)

    with tempfile.NamedTemporaryFile(suffix=".grc", delete=False) as tmp:
        tmp_path = tmp.name
    os.unlink(tmp_path)

    widget.set_grc_file_path(tmp_path)

    with patch("PySide6.QtCore.QProcess.startDetached") as mock_start_detached:
        widget.open_in_grc()

    mock_start_detached.assert_not_called()
    assert not widget.open_grc_btn.isEnabled()
    assert "no longer exists" in widget.open_grc_btn.toolTip().lower()
