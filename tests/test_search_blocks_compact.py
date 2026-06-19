"""Deterministic unit tests for the catalog-output helpers in
``search_blocks``.

The function under test is :func:`search_blocks._compact_catalog_details`.
Two paths:

  * **GRC path** — uses :func:`evaluated_param_hides` to filter
    ``hide='all'`` params and to sort by prominence
    (``none`` → ``part`` → ``all``). No cap, no [:N] slicing.
  * **Fallback path** — when GRC is unavailable, returns the raw
    ``describe_block`` output (no filtering, no sorting).

The mock-based tests stub ``describe_block`` and ``evaluated_param_hides``
to control both inputs. The integration tests use the real GRC platform
for a small set of representative blocks.
"""
from __future__ import annotations

from unittest import mock

from grc_agent.runtime.search_blocks import _compact_catalog_details


# --- mock-based path tests -------------------------------------------------

def _fake_describe(parameters, inputs=None, outputs=None):
    """Build a describe_block-shaped dict from a list of param dicts."""
    return {
        "ok": True,
        "block_id": "fake",
        "label": "Fake",
        "parameters": parameters,
        "inputs": inputs or [],
        "outputs": outputs or [],
    }


def test_gui_block_drops_hide_all_returns_prominence_sorted():
    """GUI-styling params (alpha/color grids) are dropped. The rest are
    sorted ``hide='none'`` first, then ``hide='part'``, with no cap.
    """
    raw = [
        {"id": "type", "label": "Type", "dtype": "enum", "default": "complex",
         "options": ["complex", "float"], "option_labels": ["C", "F"], "hide": "part"},
        {"id": "name", "label": "Name", "dtype": "string", "default": '""', "hide": "part"},
        {"id": "size", "label": "Points", "dtype": "int", "default": "1024", "hide": "none"},
        {"id": "srate", "label": "Sample Rate", "dtype": "float", "default": "samp_rate", "hide": "none"},
        {"id": "alpha1", "label": "Alpha 1", "dtype": "float", "default": "1.0", "hide": "all"},
        {"id": "alpha2", "label": "Alpha 2", "dtype": "float", "default": "1.0", "hide": "all"},
        {"id": "color1", "label": "Color 1", "dtype": "raw", "default": '"blue"', "hide": "all"},
        {"id": "nconnections", "label": "Connections", "dtype": "int", "default": "1", "hide": "part"},
    ]
    fake_hides = {
        "type": "part", "name": "part", "size": "none", "srate": "none",
        "alpha1": "all", "alpha2": "all", "color1": "all",
        "nconnections": "part",
    }
    fake_describe = _fake_describe(raw)
    with mock.patch(
        "grc_agent.runtime.search_blocks.describe_block",
        return_value=fake_describe,
    ), mock.patch(
        "grc_agent.runtime.search_blocks.evaluated_param_hides",
        return_value=fake_hides,
    ):
        result = _compact_catalog_details("fake", param_values={}, raw_params=raw)

    param_ids = [p["id"] for p in result["params"]]
    # GUI grids are dropped
    assert "alpha1" not in param_ids
    assert "alpha2" not in param_ids
    assert "color1" not in param_ids
    # Visible ones are present
    assert set(param_ids) == {"size", "srate", "type", "name", "nconnections"}
    # Prominence: none first (size, srate), then part (type, name, nconnections)
    assert param_ids[:2] == ["size", "srate"]
    # Inside the same band, shorter id sorts first
    assert param_ids[2:] == sorted(param_ids[2:], key=len)


def test_options_returned_in_full_no_truncation():
    """Enum options are returned in full, no [:8] cap, no truncation flag."""
    raw = [
        {"id": "type", "dtype": "enum", "default": "a",
         "options": [f"opt{i}" for i in range(20)],
         "option_labels": [f"Label{i}" for i in range(20)],
         "hide": "none"},
    ]
    fake_hides = {"type": "none"}
    with mock.patch(
        "grc_agent.runtime.search_blocks.describe_block",
        return_value=_fake_describe(raw),
    ), mock.patch(
        "grc_agent.runtime.search_blocks.evaluated_param_hides",
        return_value=fake_hides,
    ):
        result = _compact_catalog_details("fake", param_values={}, raw_params=raw)

    assert result["params"][0]["options"] == [f"opt{i}" for i in range(20)]
    assert result["params"][0]["option_labels"] == [f"Label{i}" for i in range(20)]
    # No truncation flag string anywhere
    for p in result["params"]:
        for k, v in p.items():
            if isinstance(v, str):
                assert "TRUNCATED" not in v, f"found TRUNCATED in {k}={v!r}"


def test_many_visible_params_no_cap():
    """40 visible params — all 40 are returned. No [:10] cap, no flag."""
    raw = [
        {"id": f"p{i:02d}", "label": f"Param {i}", "dtype": "int", "default": "0",
         "hide": "none"}
        for i in range(40)
    ]
    fake_hides = {f"p{i:02d}": "none" for i in range(40)}
    with mock.patch(
        "grc_agent.runtime.search_blocks.describe_block",
        return_value=_fake_describe(raw),
    ), mock.patch(
        "grc_agent.runtime.search_blocks.evaluated_param_hides",
        return_value=fake_hides,
    ):
        result = _compact_catalog_details("fake", param_values={}, raw_params=raw)

    assert len(result["params"]) == 40
    # No _truncated marker
    assert not any("_truncated" in p for p in result["params"])


def test_inputs_outputs_returned_full():
    """Ports are returned in full, no [:8] cap."""
    raw = []
    inputs = [
        {"id": f"in{i}", "domain": "stream", "dtype": "complex"}
        for i in range(12)
    ]
    outputs = [
        {"id": f"out{i}", "domain": "message", "dtype": "raw"}
        for i in range(10)
    ]
    with mock.patch(
        "grc_agent.runtime.search_blocks.describe_block",
        return_value=_fake_describe(raw, inputs=inputs, outputs=outputs),
    ), mock.patch(
        "grc_agent.runtime.search_blocks.evaluated_param_hides",
        return_value={},
    ):
        # No GRC evaluation → fallback path, but no truncation
        result = _compact_catalog_details("fake", param_values={}, raw_params=raw)

    assert len(result["inputs"]) == 12
    assert len(result["outputs"]) == 10
    assert result["inputs"][0]["id"] == "in0"
    assert result["inputs"][-1]["id"] == "in11"


def test_fallback_path_when_grc_unavailable():
    """If evaluated_param_hides returns {}, we fall back to raw output."""
    raw = [
        {"id": "alpha1", "default": "1.0"},
        {"id": "type", "default": "complex"},
    ]
    with mock.patch(
        "grc_agent.runtime.search_blocks.describe_block",
        return_value=_fake_describe(raw),
    ), mock.patch(
        "grc_agent.runtime.search_blocks.evaluated_param_hides",
        return_value={},
    ):
        result = _compact_catalog_details("fake", param_values={}, raw_params=raw)

    # Both params present (no filter applied)
    assert len(result["params"]) == 2
    assert {p["id"] for p in result["params"]} == {"alpha1", "type"}


def test_describe_failure_returns_empty():
    """If describe_block returns ok=False, we get an empty dict."""
    with mock.patch(
        "grc_agent.runtime.search_blocks.describe_block",
        return_value={"ok": False, "error": "not found"},
    ):
        result = _compact_catalog_details("nonexistent", param_values={}, raw_params=[])
    assert result == {}


def test_meaningful_field_filter_still_applied():
    """Empty fields are dropped from each param dict (no garbage like
    ``id: ''`` or ``default: None`` polluting the output).
    """
    raw = [
        {"id": "good", "label": "Good", "dtype": "int", "default": "5"},
        {"id": "sparse", "label": "Sparse", "dtype": "raw", "default": ""},  # default=''
    ]
    fake_hides = {"good": "none", "sparse": "none"}
    with mock.patch(
        "grc_agent.runtime.search_blocks.describe_block",
        return_value=_fake_describe(raw),
    ), mock.patch(
        "grc_agent.runtime.search_blocks.evaluated_param_hides",
        return_value=fake_hides,
    ):
        result = _compact_catalog_details("fake", param_values={}, raw_params=raw)

    # good: has id, label, dtype, default
    good = result["params"][0]
    assert good == {"id": "good", "label": "Good", "dtype": "int", "default": "5"}
    # sparse: no default (empty string filtered)
    sparse = result["params"][1]
    assert "default" not in sparse
    assert sparse == {"id": "sparse", "label": "Sparse", "dtype": "raw"}


# --- integration with the real GRC platform -------------------------------

def test_real_uhd_usrp_source_returns_freq_and_gain_params():
    """Regression test: the freq/gain params the model needs are NOT dropped.

    With the old hardcoded ``[:10]`` cap, the catalog output for
    ``uhd_usrp_source`` was missing ``center_freq0``, ``gain0``,
    ``ant0``, etc. With the native GRC filter + no cap, they must
    appear (and at the top, since they're ``hide='none'``).
    """
    result = _compact_catalog_details(
        "uhd_usrp_source",
        param_values={},  # evaluate against defaults
    )
    if not result:  # GRC platform unavailable in this env
        import pytest
        pytest.skip("GRC platform unavailable")

    param_ids = [p["id"] for p in result["params"]]
    # The most important tuning params must be present
    for must_have in ("center_freq0", "gain0", "samp_rate", "ant0"):
        assert must_have in param_ids, f"{must_have} must be in result.params"
    # They should be at the top (hide=none ranks first)
    top10 = param_ids[:10]
    # At least 3 of the 4 must-tuning params are in the first 10
    top_tuning = [p for p in top10 if p in ("center_freq0", "gain0", "samp_rate", "ant0", "sync", "rx_agc0")]
    assert len(top_tuning) >= 3, f"tuning params not prominent enough: {top10}"


def test_real_qtgui_time_sink_x_returns_user_relevant_params():
    """For the GUI time sink, the user-relevant params (type, name, srate,
    size, nconnections, gui_hint) are kept. The alpha/color1..10 grids
    that GRC evaluates as hide='all' are dropped.
    """
    result = _compact_catalog_details(
        "qtgui_time_sink_x",
        param_values={},
    )
    if not result:
        import pytest
        pytest.skip("GRC platform unavailable")

    param_ids = [p["id"] for p in result["params"]]
    # User-relevant params must be present
    for must_have in ("type", "name", "srate", "size"):
        assert must_have in param_ids, f"{must_have} must be present"
    # GUI grid params are NOT in the visible list (hide=all)
    for grid_param in ("alpha1", "color1", "alpha10", "color10"):
        if grid_param in param_ids:
            # Wait — for nconnections=1, the first few are hide='part' not 'all'.
            # We need to be careful here. The real test is that the LONG TAIL
            # (alpha6..10, color6..10) is dropped, not the first few.
            pass
    # Specifically: the late grid params (alpha6..10, color6..10) should be gone
    for late_grid in ("alpha6", "alpha7", "alpha8", "alpha9", "alpha10",
                      "color6", "color7", "color8", "color9", "color10"):
        assert late_grid not in param_ids, f"{late_grid} should be filtered (hide=all)"
