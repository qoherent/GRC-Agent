"""Unit tests for block_library — split from the former test_unit.py god file.

Minimal set per the clustered test plan; shared fixtures/helpers live in conftest.py.
"""

import asyncio
from pathlib import Path

import pytest
from conftest import _add_scale_epy_block

from grc_agent.adapter import (
    _validate_block_definition,
    change_graph,
    inspect_graph,
    load_flow_graph,
    preview_flowgraph_py,
    save_block_to_library,
)


def test_save_block_round_trip_and_reuse_in_second_flowgraph(temp_empty, temp_hier_block_lib_dir):
    from grc_agent.adapter.graph import get_platform

    fg = load_flow_graph(str(temp_empty))
    assert _add_scale_epy_block(fg)["ok"] is True

    result = save_block_to_library(fg, "my_epy", block_id="test_saved_scale_block")
    assert result["ok"] is True
    assert result["block_id"] == "test_saved_scale_block"
    assert result["params"] == ["scale"]
    assert result["inputs"] == ["0"]
    assert result["outputs"] == ["0"]
    yml_path = Path(result["saved_to"]["block_yml"])
    py_path = Path(result["saved_to"]["py"])
    assert yml_path.exists()
    assert py_path.exists()
    assert yml_path.parent == temp_hier_block_lib_dir

    platform = get_platform()
    assert "test_saved_scale_block" in platform.blocks

    fg2 = platform.make_flow_graph()
    fg2.grc_file_path = ""
    fg2.options_block.params["id"].set_value("reuse_fg")
    res2 = change_graph(
        fg2,
        add_blocks=[
            {
                "block_id": "test_saved_scale_block",
                "instance_name": "reused",
                "params": {"scale": "5.0"},
            }
        ],
        force=True,
    )
    assert res2["ok"] is True
    snap = inspect_graph(fg2)
    reused = next(b for b in snap["graph"]["blocks"] if b["instance_name"] == "reused")
    assert reused["params"]["scale"] == "5.0"
    assert reused["inputs"] == [{"port_id": "0", "dtype": "float"}]
    assert reused["outputs"] == [{"port_id": "0", "dtype": "float"}]


def test_save_block_generated_codegen_imports_the_saved_module_correctly(
    temp_empty,
    temp_hier_block_lib_dir,  # noqa: ARG001
):
    # The manual smoke-test verification during development (never a pytest
    # test until now) confirmed that a flowgraph using a saved block gets
    # both the sys.path.append(...~/.grc_gnuradio...) hack AND the correct
    # "import <block_id> as <block_id>  # grc-generated hier_block" line in
    # its generated source. This is the actual, load-bearing mechanism that
    # makes the saved block importable at all (see block_library.py's
    # _render_block_yml docstring) -- a regression here would be silent
    # without a real codegen check, since change_graph/inspect_graph alone
    # never render or execute any Python source.
    fg = load_flow_graph(str(temp_empty))
    _add_scale_epy_block(fg)
    result = save_block_to_library(fg, "my_epy", block_id="test_codegen_saved_block")
    assert result["ok"] is True

    from grc_agent.adapter.graph import get_platform

    platform = get_platform()
    fg2 = platform.make_flow_graph()
    fg2.grc_file_path = ""
    fg2.options_block.params["id"].set_value("codegen_reuse_fg")
    res2 = change_graph(
        fg2,
        add_blocks=[
            {
                "block_id": "blocks_null_source",
                "instance_name": "src0",
                "params": {"type": "float"},
            },
            {
                "block_id": "test_codegen_saved_block",
                "instance_name": "reused",
                "params": {"scale": "5.0"},
            },
            {"block_id": "blocks_null_sink", "instance_name": "sink0", "params": {"type": "float"}},
        ],
        add_connections=["src0:0->reused:0", "reused:0->sink0:0"],
    )
    assert res2["ok"] is True

    preview = preview_flowgraph_py(fg2)
    main_script = preview["files"][-1]["source"]
    assert "sys.path.append(os.environ.get('GRC_HIER_PATH'" in main_script
    assert (
        "import test_codegen_saved_block as test_codegen_saved_block  "
        "# grc-generated hier_block" in main_script
    )


def test_save_block_does_not_mutate_original_epy_instance(temp_empty, temp_hier_block_lib_dir):  # noqa: ARG001
    fg = load_flow_graph(str(temp_empty))
    _add_scale_epy_block(fg)
    before = fg.get_block("my_epy").params["_source_code"].get_value()

    result = save_block_to_library(fg, "my_epy", block_id="test_scope_boundary_block")
    assert result["ok"] is True

    after_block = fg.get_block("my_epy")
    assert after_block.key == "epy_block"
    assert after_block.params["_source_code"].get_value() == before


@pytest.mark.parametrize(
    "instance_name, block_id, overwrite, expected_error",
    [
        ("audio_sink", "whatever_block_id", False, "not_an_epy_block"),  # non-epy block
        ("does_not_exist", None, False, "block_not_found"),  # missing instance
        ("my_epy", "not a valid id", False, "invalid_block_id"),  # bad identifier
        # collision with a stock block is rejected even with overwrite=True
        ("my_epy", "blocks_null_sink", True, "block_id_collision"),
    ],
)
def test_save_block_rejects_invalid_inputs(
    temp_empty,
    temp_dial_tone,
    temp_hier_block_lib_dir,  # noqa: ARG001
    instance_name,
    block_id,
    overwrite,
    expected_error,
):
    """The four rejection branches of save_block_to_library under one uniform
    rule: an invalid input is a loud error_type, never a silent skip."""
    fg = load_flow_graph(str(temp_empty if instance_name != "audio_sink" else temp_dial_tone))
    if instance_name == "my_epy":
        _add_scale_epy_block(fg)
    result = save_block_to_library(fg, instance_name, block_id=block_id, overwrite=overwrite)
    assert result["ok"] is False
    assert result["error_type"] == expected_error


def test_save_block_overwrite_gates_a_previously_saved_block_id(
    temp_empty,
    temp_hier_block_lib_dir,  # noqa: ARG001
):
    fg = load_flow_graph(str(temp_empty))
    _add_scale_epy_block(fg)

    first = save_block_to_library(fg, "my_epy", block_id="test_overwrite_block")
    assert first["ok"] is True

    second = save_block_to_library(fg, "my_epy", block_id="test_overwrite_block", overwrite=False)
    assert second["ok"] is False
    assert second["error_type"] == "block_id_collision"

    third = save_block_to_library(
        fg, "my_epy", block_id="test_overwrite_block", label="Renamed", overwrite=True
    )
    assert third["ok"] is True
    assert third["label"] == "Renamed"


def test_validate_block_definition_never_corrupts_shared_platform_registry():
    # Regression test for the core safety bug this feature's validation
    # design had to route around: Platform.block_classes is a single
    # ChainMap shared by EVERY Platform instance (a Platform CLASS
    # attribute) -- calling build_library() on any instance, even a
    # brand-new one, clears and rebuilds that ONE shared registry for the
    # WHOLE PROCESS. _validate_block_definition must never call
    # build_library() at all, on any Platform.
    import collections

    from grc_agent.adapter.graph import get_platform

    platform = get_platform()
    assert "options" in platform.blocks
    assert "blocks_null_sink" in platform.blocks

    good = collections.OrderedDict(
        [
            ("id", "validate_only_ok"),
            ("label", "Validate Only OK"),
            ("category", "[Custom]"),
            ("parameters", []),
            ("inputs", []),
            ("outputs", []),
            (
                "templates",
                collections.OrderedDict([("imports", ""), ("make", ""), ("callbacks", [])]),
            ),
            ("documentation", ""),
            ("grc_source", ""),
            ("file_format", 1),
        ]
    )
    assert _validate_block_definition(good)["ok"] is True

    broken = collections.OrderedDict(good)
    broken["id"] = "validate_only_broken"
    broken["inputs"] = [
        collections.OrderedDict([("id", "0"), ("dtype", "float")]),
        collections.OrderedDict([("id", "0"), ("dtype", "float")]),
    ]
    result = _validate_block_definition(broken)
    assert result["ok"] is False

    assert "options" in platform.blocks
    assert "blocks_null_sink" in platform.blocks
    assert "validate_only_ok" not in platform.blocks
    assert "validate_only_broken" not in platform.blocks


def test_save_block_invalidates_rag_corpus_caches(temp_empty, temp_hier_block_lib_dir):  # noqa: ARG001
    from grc_agent.adapter import rag

    fg = load_flow_graph(str(temp_empty))
    _add_scale_epy_block(fg)

    rag._CORPUS_VERSION_CACHE["catalog"] = "stale-version"
    rag._FRESHNESS_CACHE["catalog"] = ("stale-path", "stale-model")

    result = save_block_to_library(fg, "my_epy", block_id="test_cache_invalidation_block")
    assert result["ok"] is True
    assert "catalog" not in rag._CORPUS_VERSION_CACHE
    assert "catalog" not in rag._FRESHNESS_CACHE


def test_native_canvas_manager_reload_block_library_calls_native_refresh_chain():
    """save_block's live-refresh path mirrors GRC's own native RELOAD_BLOCKS
    action: build_library() then repopulate the block-tree panel, then
    redraw open canvases. Uses mocks (matching this file's existing
    NativeCanvasManager test convention, e.g. test_scroll_to_block) rather
    than a real BlockTreeWindow/MainWindow, since constructing GNU Radio's
    real GUI package tree standalone hits a reproducible circular-import
    ordering issue outside the app's own controlled startup sequence."""
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm.platform = MagicMock()
    cm.window = MagicMock()

    cm.reload_block_library()

    cm.platform.build_library.assert_called_once()
    cm.window.btwin.repopulate.assert_called_once()
    cm.window.update_pages.assert_called_once()


def test_native_flowgraph_proxy_save_block_calls_reload_only_on_success(monkeypatch):
    """Exercises NativeFlowgraphProxy.save_block() end-to-end (never tested
    before this pass): must call save_block_to_library with the resolved
    target flowgraph and args, then call NativeCanvasManager.
    reload_block_library() only when the result is ok=True -- also confirms
    save_block_to_library is called WITHOUT a gui_platform kwarg (removed
    after this test's own design surfaced that it made every successful
    live save_block call rebuild the block registry twice)."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeFlowgraphProxy

    fake_fg = SimpleNamespace(name="fake_fg")
    cm = MagicMock()
    cm.current_flow_graph = fake_fg

    proxy = NativeFlowgraphProxy(cm)
    seen_calls = []

    def fake_save_block_to_library(flow_graph, instance_name, **kwargs):
        seen_calls.append((flow_graph, instance_name, kwargs))
        return {"ok": True, "block_id": instance_name}

    monkeypatch.setattr(
        "grc_agent.adapter.block_library.save_block_to_library", fake_save_block_to_library
    )
    result = asyncio.run(proxy.save_block("my_epy", block_id="saved_id"))
    assert result["ok"] is True
    assert seen_calls == [
        (
            fake_fg,
            "my_epy",
            {"block_id": "saved_id", "label": None, "category": None, "overwrite": False},
        )
    ]
    cm.reload_block_library.assert_called_once()

    cm.reload_block_library.reset_mock()

    def failing_save_block_to_library(flow_graph, instance_name, **kwargs):  # noqa: ARG001
        return {"ok": False, "error_type": "block_id_collision", "errors": ["boom"]}

    monkeypatch.setattr(
        "grc_agent.adapter.block_library.save_block_to_library", failing_save_block_to_library
    )
    result2 = asyncio.run(proxy.save_block("my_epy"))
    assert result2["ok"] is False
    cm.reload_block_library.assert_not_called()
