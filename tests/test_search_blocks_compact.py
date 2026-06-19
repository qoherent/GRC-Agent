"""Deterministic unit tests for the catalog-output helpers in
``search_blocks``.

The function under test is :func:`search_blocks._compact_catalog_details`.
It applies three native GRC filters (see ``docs/GNU_NATIVE_METHODS.md``):

  1. ``hide != 'all'`` — GRC's evaluated visibility
  2. ``category != ADVANCED_PARAM_TAB`` — drop auto-added metadata
  3. ``category != 'Config'`` — drop 100%-styling params

The mock-based tests stub ``describe_block``, ``evaluated_param_hides``,
and ``_param_categories`` to control all three inputs. The integration
tests use the real GRC platform.
"""
from __future__ import annotations

from unittest import mock

from grc_agent.runtime.search_blocks import _compact_catalog_details


# --- mock-based path tests -------------------------------------------------

def _fake_describe(parameters, inputs=None, outputs=None):
    return {
        "ok": True,
        "block_id": "fake",
        "label": "Fake",
        "parameters": parameters,
        "inputs": inputs or [],
        "outputs": outputs or [],
    }


def test_styling_params_dropped_via_config_category():
    """Params in the 'Config' category (colors, markers) are dropped."""
    raw = [
        {"id": "type", "label": "Type", "dtype": "enum", "default": "complex"},
        {"id": "srate", "label": "Sample Rate", "dtype": "float", "default": "samp_rate"},
        {"id": "color1", "label": "Line 1 Color", "dtype": "enum", "default": "blue"},
        {"id": "alpha1", "label": "Line 1 Alpha", "dtype": "real", "default": "1.0"},
    ]
    fake_hides = {"type": "part", "srate": "none", "color1": "part", "alpha1": "part"}
    fake_cats = {"type": "General", "srate": "General", "color1": "Config", "alpha1": "Config"}
    with mock.patch("grc_agent.runtime.search_blocks.describe_block", return_value=_fake_describe(raw)), \
         mock.patch("grc_agent.runtime.search_blocks.evaluated_param_hides", return_value=fake_hides), \
         mock.patch("grc_agent.runtime.search_blocks._param_categories", return_value=fake_cats):
        result = _compact_catalog_details("fake", param_values={}, raw_params=raw)

    param_ids = [p["id"] for p in result["params"]]
    assert "type" in param_ids
    assert "srate" in param_ids
    assert "color1" not in param_ids  # Config category dropped
    assert "alpha1" not in param_ids  # Config category dropped


def test_advanced_params_dropped():
    """Params in the ADVANCED_PARAM_TAB category (alias, affinity, comment) are dropped."""
    raw = [
        {"id": "type", "label": "Type", "dtype": "enum", "default": "complex"},
        {"id": "alias", "label": "Block Alias", "dtype": "string", "default": ""},
        {"id": "affinity", "label": "Core Affinity", "dtype": "int_vector", "default": ""},
        {"id": "comment", "label": "Comment", "dtype": "_multiline", "default": ""},
    ]
    fake_hides = {"type": "part", "alias": "part", "affinity": "part", "comment": "part"}
    fake_cats = {"type": "General", "alias": "Advanced", "affinity": "Advanced", "comment": "Advanced"}
    with mock.patch("grc_agent.runtime.search_blocks.describe_block", return_value=_fake_describe(raw)), \
         mock.patch("grc_agent.runtime.search_blocks.evaluated_param_hides", return_value=fake_hides), \
         mock.patch("grc_agent.runtime.search_blocks._param_categories", return_value=fake_cats):
        result = _compact_catalog_details("fake", param_values={}, raw_params=raw)

    param_ids = [p["id"] for p in result["params"]]
    assert "type" in param_ids
    assert "alias" not in param_ids
    assert "affinity" not in param_ids
    assert "comment" not in param_ids


def test_hide_all_params_dropped():
    """Params with hide='all' are dropped regardless of category."""
    raw = [
        {"id": "visible", "label": "Visible", "dtype": "int", "default": "1"},
        {"id": "hidden", "label": "Hidden", "dtype": "int", "default": "0"},
    ]
    fake_hides = {"visible": "none", "hidden": "all"}
    fake_cats = {"visible": "General", "hidden": "General"}
    with mock.patch("grc_agent.runtime.search_blocks.describe_block", return_value=_fake_describe(raw)), \
         mock.patch("grc_agent.runtime.search_blocks.evaluated_param_hides", return_value=fake_hides), \
         mock.patch("grc_agent.runtime.search_blocks._param_categories", return_value=fake_cats):
        result = _compact_catalog_details("fake", param_values={}, raw_params=raw)

    param_ids = [p["id"] for p in result["params"]]
    assert "visible" in param_ids
    assert "hidden" not in param_ids


def test_prominence_sort_none_before_part():
    """hide='none' params appear before hide='part' params."""
    raw = [
        {"id": "part_param", "label": "Part", "dtype": "int", "default": "1"},
        {"id": "none_param", "label": "None", "dtype": "int", "default": "2"},
        {"id": "another_part", "label": "Another Part", "dtype": "int", "default": "3"},
    ]
    fake_hides = {"part_param": "part", "none_param": "none", "another_part": "part"}
    fake_cats = {"part_param": "General", "none_param": "General", "another_part": "General"}
    with mock.patch("grc_agent.runtime.search_blocks.describe_block", return_value=_fake_describe(raw)), \
         mock.patch("grc_agent.runtime.search_blocks.evaluated_param_hides", return_value=fake_hides), \
         mock.patch("grc_agent.runtime.search_blocks._param_categories", return_value=fake_cats):
        result = _compact_catalog_details("fake", param_values={}, raw_params=raw)

    param_ids = [p["id"] for p in result["params"]]
    assert param_ids[0] == "none_param"  # hide='none' first


def test_no_options_in_output():
    """Discovery context: no options/option_labels in the output."""
    raw = [
        {"id": "type", "label": "Type", "dtype": "enum", "default": "complex",
         "options": ["complex", "float"], "option_labels": ["Complex", "Float"]},
    ]
    fake_hides = {"type": "none"}
    fake_cats = {"type": "General"}
    with mock.patch("grc_agent.runtime.search_blocks.describe_block", return_value=_fake_describe(raw)), \
         mock.patch("grc_agent.runtime.search_blocks.evaluated_param_hides", return_value=fake_hides), \
         mock.patch("grc_agent.runtime.search_blocks._param_categories", return_value=fake_cats):
        result = _compact_catalog_details("fake", param_values={}, raw_params=raw)

    p = result["params"][0]
    assert "options" not in p
    assert "option_labels" not in p
    assert p == {"id": "type", "label": "Type", "dtype": "enum", "default": "complex"}


def test_rf_options_category_kept():
    """RF Options params (center_freq, gain, antenna) are NOT dropped."""
    raw = [
        {"id": "samp_rate", "label": "Sample Rate", "dtype": "real", "default": "1e6"},
        {"id": "center_freq0", "label": "Center Freq", "dtype": "raw", "default": "0"},
        {"id": "gain0", "label": "Gain", "dtype": "float", "default": "0"},
    ]
    fake_hides = {"samp_rate": "none", "center_freq0": "none", "gain0": "none"}
    fake_cats = {"samp_rate": "General", "center_freq0": "RF Options", "gain0": "RF Options"}
    with mock.patch("grc_agent.runtime.search_blocks.describe_block", return_value=_fake_describe(raw)), \
         mock.patch("grc_agent.runtime.search_blocks.evaluated_param_hides", return_value=fake_hides), \
         mock.patch("grc_agent.runtime.search_blocks._param_categories", return_value=fake_cats):
        result = _compact_catalog_details("fake", param_values={}, raw_params=raw)

    param_ids = [p["id"] for p in result["params"]]
    assert "samp_rate" in param_ids
    assert "center_freq0" in param_ids  # RF Options kept!
    assert "gain0" in param_ids          # RF Options kept!


def test_inputs_outputs_returned_full():
    """Ports are returned in full, no cap."""
    raw = []
    inputs = [{"id": f"in{i}", "domain": "stream", "dtype": "complex"} for i in range(12)]
    outputs = [{"id": f"out{i}", "domain": "message", "dtype": "raw"} for i in range(10)]
    with mock.patch("grc_agent.runtime.search_blocks.describe_block", return_value=_fake_describe(raw, inputs=inputs, outputs=outputs)), \
         mock.patch("grc_agent.runtime.search_blocks.evaluated_param_hides", return_value={}), \
         mock.patch("grc_agent.runtime.search_blocks._param_categories", return_value={}):
        result = _compact_catalog_details("fake", param_values={}, raw_params=raw)

    assert len(result["inputs"]) == 12
    assert len(result["outputs"]) == 10


def test_describe_failure_returns_empty():
    with mock.patch("grc_agent.runtime.search_blocks.describe_block", return_value={"ok": False}):
        result = _compact_catalog_details("nonexistent", param_values={}, raw_params=[])
    assert result == {}


# --- integration with the real GRC platform -------------------------------

def test_real_uhd_usrp_source_keeps_rf_tuning_params():
    """center_freq0, gain0, ant0 must be in the output (RF Options category)."""
    result = _compact_catalog_details("uhd_usrp_source", param_values={})
    if not result:
        import pytest
        pytest.skip("GRC platform unavailable")

    param_ids = [p["id"] for p in result["params"]]
    for must_have in ("center_freq0", "gain0", "samp_rate", "ant0"):
        assert must_have in param_ids, f"{must_have} must be present"
    # No Config-category params
    for styling in ("color1", "alpha1", "marker1"):
        assert styling not in param_ids


def test_real_qtgui_time_sink_x_drops_styling():
    """GUI styling (color, alpha, marker) must be dropped. Functional params kept."""
    result = _compact_catalog_details("qtgui_time_sink_x", param_values={})
    if not result:
        import pytest
        pytest.skip("GRC platform unavailable")

    param_ids = [p["id"] for p in result["params"]]
    for must_have in ("type", "srate", "size"):
        assert must_have in param_ids
    # Config styling params must be gone
    for styling in ("color1", "color2", "alpha1", "alpha2", "marker1", "style1", "width1", "label1"):
        assert styling not in param_ids, f"{styling} should be dropped (Config category)"
    # Advanced metadata must be gone
    for advanced in ("alias", "affinity", "comment"):
        assert advanced not in param_ids
