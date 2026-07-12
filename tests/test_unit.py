import os
import shutil
import socket
import tempfile
from pathlib import Path

import pytest

from grc_agent.adapter import (
    _footprints_overlap,
    change_graph,
    inspect_graph,
    lite_web_search,
    load_flow_graph,
    query_catalog,
    query_docs,
    redo_flowgraph,
    undo_flowgraph,
    undo_status,
)

FIXTURES_DIR = Path("tests/data")


@pytest.fixture
def temp_dial_tone():
    tmp_dir = tempfile.mkdtemp()
    src = FIXTURES_DIR / "dial_tone.grc"
    dst = Path(tmp_dir) / "dial_tone.grc"
    shutil.copy2(src, dst)
    yield dst
    shutil.rmtree(tmp_dir)


@pytest.fixture
def temp_empty():
    tmp_dir = tempfile.mkdtemp()
    src = FIXTURES_DIR / "empty.grc"
    dst = Path(tmp_dir) / "empty.grc"
    shutil.copy2(src, dst)
    yield dst
    shutil.rmtree(tmp_dir)


# ==========================================
# inspect_graph Unit Tests (2 tests)
# ==========================================


def test_inspect_graph_overview(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    res = inspect_graph(fg)
    assert res["ok"] is True
    graph = res["graph"]
    assert graph["validation"]["status"] == "valid"
    block_names = {b["instance_name"] for b in graph["blocks"]}
    assert "samp_rate" in block_names
    assert "analog_sig_source_x_0" in block_names
    assert len(graph["connections"]) > 0


def test_inspect_graph_scoped(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    res = inspect_graph(fg, targets=["samp_rate", "analog_sig_source_x_0"])
    assert res["ok"] is True
    graph = res["graph"]
    block_names = {b["instance_name"] for b in graph["blocks"]}
    assert "samp_rate" in block_names
    assert "analog_sig_source_x_0" in block_names
    assert len(block_names) == 2


# ==========================================
# change_graph Unit Tests (11 tests)
# ==========================================


def test_change_graph_add_block(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    # force=True: this test is only checking add_blocks' own mechanics, not
    # overall graph validity — an unwired throttle leaves its ports
    # unconnected, which is correctly rejected without force (see
    # test_change_graph_force_bypasses_validation).
    res = change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "blocks_throttle2",
                "instance_name": "my_throttle",
                "params": {"type": "float"},
            }
        ],
        force=True,
    )
    assert res["ok"] is True
    snap = inspect_graph(fg)
    block_names = {b["instance_name"] for b in snap["graph"]["blocks"]}
    assert "my_throttle" in block_names


def test_change_graph_add_block_no_overlap_with_existing(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    existing_coords = [
        tuple(b.states["coordinate"])
        for b in fg.blocks
        if isinstance(b.states.get("coordinate"), (list, tuple))
    ]
    res = change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "blocks_throttle2",
                "instance_name": "my_throttle",
                "params": {"type": "float"},
            }
        ],
        force=True,
    )
    assert res["ok"] is True
    new_coord = tuple(fg.get_block("my_throttle").states["coordinate"])
    assert not any(_footprints_overlap(new_coord, other) for other in existing_coords)


def test_change_graph_add_blocks_batch_no_overlap(temp_empty):
    fg = load_flow_graph(str(temp_empty))
    res = change_graph(
        fg,
        add_blocks=[
            {"block_id": "blocks_null_sink", "instance_name": f"sink_{i}", "params": {"type": "float"}}
            for i in range(5)
        ],
        force=True,
    )
    assert res["ok"] is True
    coords = [tuple(fg.get_block(f"sink_{i}").states["coordinate"]) for i in range(5)]
    for i, a in enumerate(coords):
        for b in coords[i + 1 :]:
            assert not _footprints_overlap(a, b)


def test_change_graph_add_block_across_calls_no_overlap(temp_empty):
    # Regression test: the agent adds blocks one at a time across separate
    # tool calls far more often than in one batch, and each call only sees
    # the graph state on disk — not any in-flight positioning decision from
    # a prior call — so this is the scenario that actually triggered the
    # reported "added on top of another block" bug.
    fg = load_flow_graph(str(temp_empty))
    for i in range(4):
        res = change_graph(
            fg,
            add_blocks=[
                {
                    "block_id": "blocks_null_sink",
                    "instance_name": f"call_sink_{i}",
                    "params": {"type": "float"},
                }
            ],
            force=True,
        )
        assert res["ok"] is True
    coords = [tuple(fg.get_block(f"call_sink_{i}").states["coordinate"]) for i in range(4)]
    for i, a in enumerate(coords):
        for b in coords[i + 1 :]:
            assert not _footprints_overlap(a, b)


def test_change_graph_remove_block(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    # force=True: removing this source leaves blocks_add_xx with one fewer
    # connected input than its num_inputs param expects (remove_element
    # cascades the connection removal but not the param) — a genuine
    # validation error this test isn't concerned with checking.
    res = change_graph(fg, remove_blocks=["analog_noise_source_x_0"], force=True)
    assert res["ok"] is True
    snap = inspect_graph(fg)
    block_names = {b["instance_name"] for b in snap["graph"]["blocks"]}
    assert "analog_noise_source_x_0" not in block_names


def test_change_graph_update_params(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(
        fg, update_params=[{"instance_name": "samp_rate", "params": {"value": "96000"}}]
    )
    assert res["ok"] is True
    snap = inspect_graph(fg)
    params = {b["instance_name"]: b["params"] for b in snap["graph"]["blocks"]}
    assert params["samp_rate"]["value"] == "96000"


def test_change_graph_update_states(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(fg, update_states=[{"instance_name": "blocks_add_xx", "state": "bypass"}])
    assert res["ok"] is True
    snap = inspect_graph(fg)
    states = {b["instance_name"]: b["state"] for b in snap["graph"]["blocks"]}
    assert states["blocks_add_xx"] == "bypass"


def test_change_graph_add_connection(temp_empty):
    fg = load_flow_graph(str(temp_empty))
    # Add two blocks first — force=True since they're deliberately left
    # unconnected between this call and the next (a genuine validation
    # error this test isn't concerned with checking).
    change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "analog_sig_source_x",
                "instance_name": "sig",
                "params": {"type": "float"},
            },
            {"block_id": "blocks_null_sink", "instance_name": "sink", "params": {"type": "float"}},
        ],
        force=True,
    )
    res = change_graph(fg, add_connections=["sig:0->sink:0"])
    assert res["ok"] is True
    snap = inspect_graph(fg)
    conns = snap["graph"]["connections"]
    assert "sig:0->sink:0" in conns


def test_change_graph_remove_connection(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    # force=True: leaves blocks_add_xx's in0 port unconnected — a genuine
    # validation error this test isn't concerned with checking.
    res = change_graph(
        fg, remove_connections=["analog_sig_source_x_0:0->blocks_add_xx:0"], force=True
    )
    assert res["ok"] is True
    snap = inspect_graph(fg)
    conns = snap["graph"]["connections"]
    assert "analog_sig_source_x_0:0->blocks_add_xx:0" not in conns


def test_change_graph_complex_batch(temp_empty):
    fg = load_flow_graph(str(temp_empty))
    res = change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "analog_sig_source_x",
                "instance_name": "sig",
                "params": {"type": "float"},
            },
            {"block_id": "blocks_throttle2", "instance_name": "thr", "params": {"type": "float"}},
            {"block_id": "blocks_null_sink", "instance_name": "sink", "params": {"type": "float"}},
        ],
        add_connections=["sig:0->thr:0", "thr:0->sink:0"],
    )
    assert res["ok"] is True
    snap = inspect_graph(fg)
    assert snap["graph"]["validation"]["status"] == "valid"


def test_change_graph_rollback_failure(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    # Make a change that is invalid (connecting to a non-existent port)
    res = change_graph(fg, add_connections=["analog_sig_source_x_0:0->blocks_add_xx:99"])
    assert res["ok"] is False
    assert len(res["errors"]) > 0
    # Confirm that the file is not corrupted and original state is valid
    snap = inspect_graph(fg)
    assert snap["graph"]["validation"]["status"] == "valid"


def test_change_graph_auto_resolve_type(temp_dial_tone):
    # blocks_add_xx is already live-connected (type=float); setting it to
    # "auto" should resolve back to "float" from its connected neighbors.
    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(
        fg, update_params=[{"instance_name": "blocks_add_xx", "params": {"type": "auto"}}]
    )
    assert res["ok"] is True
    snap = inspect_graph(fg)
    params = {b["instance_name"]: b["params"] for b in snap["graph"]["blocks"]}
    assert params["blocks_add_xx"]["type"] == "float"


def test_change_graph_force_bypasses_validation(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    # An unresolvable variable reference is invalid without force...
    res = change_graph(
        fg, update_params=[{"instance_name": "samp_rate", "params": {"value": "undefined_var_xyz"}}]
    )
    assert res["ok"] is False
    assert res["error_type"] == "validation_failed"
    snap = inspect_graph(fg)
    assert snap["graph"]["validation"]["status"] == "valid"  # rolled back

    # ...but commits anyway with force=True.
    res = change_graph(
        fg,
        update_params=[{"instance_name": "samp_rate", "params": {"value": "undefined_var_xyz"}}],
        force=True,
    )
    assert res["ok"] is True
    snap = inspect_graph(fg)
    assert snap["graph"]["validation"]["status"] == "invalid"


# ==========================================
# Undo/Redo Unit Tests (6 tests)
# ==========================================


def test_undo_status_no_history(temp_dial_tone):
    # No edit has been made yet, so no undo dir exists at all.
    status = undo_status(temp_dial_tone)
    assert status == {"can_undo": False, "can_redo": False}


def test_undo_redo_round_trip(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(
        fg, update_params=[{"instance_name": "samp_rate", "params": {"value": "48000"}}]
    )
    assert res["ok"] is True

    status = undo_status(temp_dial_tone)
    assert status == {"can_undo": True, "can_redo": False}

    undo_res = undo_flowgraph(temp_dial_tone)
    assert undo_res["ok"] is True
    reverted = load_flow_graph(str(temp_dial_tone))
    assert reverted.get_block("samp_rate").params["value"].get_value() == "32000"
    status = undo_status(temp_dial_tone)
    assert status == {"can_undo": False, "can_redo": True}

    redo_res = redo_flowgraph(temp_dial_tone)
    assert redo_res["ok"] is True
    reapplied = load_flow_graph(str(temp_dial_tone))
    assert reapplied.get_block("samp_rate").params["value"].get_value() == "48000"
    status = undo_status(temp_dial_tone)
    assert status == {"can_undo": True, "can_redo": False}


def test_undo_with_nothing_to_undo(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    change_graph(fg, update_params=[{"instance_name": "samp_rate", "params": {"value": "48000"}}])
    undo_flowgraph(temp_dial_tone)  # back to the baseline (index 0)

    res = undo_flowgraph(temp_dial_tone)
    assert res["ok"] is False


def test_redo_with_nothing_to_redo(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    change_graph(fg, update_params=[{"instance_name": "samp_rate", "params": {"value": "48000"}}])

    res = redo_flowgraph(temp_dial_tone)
    assert res["ok"] is False


def test_new_edit_discards_redo_branch(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    change_graph(fg, update_params=[{"instance_name": "samp_rate", "params": {"value": "48000"}}])
    undo_flowgraph(temp_dial_tone)  # back to baseline; a redo branch to 48000 now exists
    assert undo_status(temp_dial_tone) == {"can_undo": False, "can_redo": True}

    fg = load_flow_graph(str(temp_dial_tone))
    change_graph(fg, update_params=[{"instance_name": "samp_rate", "params": {"value": "64000"}}])

    status = undo_status(temp_dial_tone)
    assert status["can_undo"] is True
    assert status["can_redo"] is False  # the 48000 branch was discarded, not just hidden

    undo_flowgraph(temp_dial_tone)
    reverted = load_flow_graph(str(temp_dial_tone))
    assert reverted.get_block("samp_rate").params["value"].get_value() == "32000"


def test_no_op_edit_is_not_pushed(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    # Re-setting a param to its current value produces identical exported
    # content, so push_undo_snapshot's content-hash dedup should skip it.
    res = change_graph(
        fg, update_params=[{"instance_name": "samp_rate", "params": {"value": "32000"}}]
    )
    assert res["ok"] is True
    assert undo_status(temp_dial_tone) == {"can_undo": False, "can_redo": False}


# ==========================================
# Vector Search and Knowledge Unit Tests (3 tests)
# ==========================================


def has_llm_backend():
    if os.getenv("OPENROUTER_API_KEY"):
        return True
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=0.5):
            return True
    except OSError:
        return False


requires_llm = pytest.mark.skipif(
    not has_llm_backend(),
    reason="Requires a running local Ollama server or an OPENROUTER_API_KEY set in the environment",
)


@requires_llm
def test_query_catalog_vector_search():
    res = query_catalog("sine wave source")
    assert res["ok"] is True
    assert "results" in res
    assert len(res["results"]) > 0
    match = res["results"][0]
    assert "block_id" in match
    assert "analog_sig_source_x" in match["block_id"]


@requires_llm
def test_query_docs_rag():
    res = query_docs("what is a stream tag")
    assert res["ok"] is True
    assert "answer" in res
    # A relevance check, not just non-emptiness: an irrelevant retrieval of
    # equal length would otherwise still pass this test.
    assert "tag" in res["answer"].lower()


# ==========================================
# Web Tools Unit Tests (1 test)
# ==========================================
# Only lite_web_search is our own code — the WebFetch(local=True) markdownify
# fallback is upstream pydantic-ai, so it isn't re-tested here.


def test_web_search_success():
    # lite_web_search hits lite.duckduckgo.com directly (no LLM), and must NOT
    # silently return "No results" like the old adapter.web_search did.
    res = lite_web_search("python programming language")
    assert isinstance(res, str)
    assert len(res) > 0
    assert "No web results" not in res
    assert "python.org" in res
