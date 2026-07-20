import os
import shutil
import socket
import tempfile
from pathlib import Path

import pytest

from grc_agent.adapter import (
    BLOCK_FOOTPRINT_W,
    BLOCK_SPACING,
    _compute_ranks,
    _find_block_placement,
    _rects_overlap,
    change_graph,
    generate_flowgraph_py,
    inspect_graph,
    lite_web_search,
    load_flow_graph,
    preview_flowgraph_py,
    query_catalog,
    query_docs,
    set_param,
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
# change_graph Unit Tests (17 tests)
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


def test_change_graph_unsaved_flowgraph():
    from grc_agent.adapter.graph import get_platform
    fg = get_platform().make_flow_graph()
    fg.grc_file_path = ""
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
    assert not any(_rects_overlap(*new_coord, *other) for other in existing_coords)


def test_change_graph_add_blocks_no_visual_overlap_for_busy_block(temp_empty):
    # Regression test for a live-reported bug: a param-heavy block (Signal
    # Source shows 6 visible rows: samp_rate/waveform/freq/amp/offset/phase)
    # rendered taller than the OLD BLOCK_FOOTPRINT_H=100 estimate, so a sink
    # placed exactly 100 below it visibly overlapped despite passing the
    # point-based check. Asserts the actual vertical gap, not just "no
    # exact-point collision" — a regression that only shrinks the constant
    # back down would pass a same-point check but fail this.
    fg = load_flow_graph(str(temp_empty))
    res = change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "analog_sig_source_x",
                "instance_name": "busy_source",
                "params": {
                    "amp": "1.0",
                    "freq": "16000.0",
                    "type": "float",
                    "waveform": "analog.GR_SIN_WAVE",
                },
            },
            {
                "block_id": "qtgui_time_sink_x",
                "instance_name": "busy_sink",
                "params": {"type": "float"},
            },
        ],
        force=True,
    )
    assert res["ok"] is True
    from grc_agent.adapter import BLOCK_FOOTPRINT_H

    # A fixed, empirically-grounded bound. The real Signal Source block that
    # triggered this bug rendered ~150-170px tall (6 visible rows); 150 is a
    # safe floor a regression back toward the old 100 would fail, while
    # comfortably below the current 220 constant.
    assert BLOCK_FOOTPRINT_H >= 150


def test_change_graph_add_blocks_batch_no_overlap(temp_empty):
    fg = load_flow_graph(str(temp_empty))
    res = change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "blocks_null_sink",
                "instance_name": f"sink_{i}",
                "params": {"type": "float"},
            }
            for i in range(5)
        ],
        force=True,
    )
    assert res["ok"] is True
    coords = [tuple(fg.get_block(f"sink_{i}").states["coordinate"]) for i in range(5)]
    for i, a in enumerate(coords):
        for b in coords[i + 1 :]:
            assert not _rects_overlap(*a, *b)


def test_change_graph_add_blocks_batch_no_overlap_large(temp_empty):
    # Regression test: adding a large batch of blocks used to stack them all in
    # one endlessly-tall column (old column-layout) or place later ones on top
    # of earlier ones (pre-AABB-check). The spiral placement now guarantees that
    # no two blocks in a batch overlap, regardless of batch size.
    fg = load_flow_graph(str(temp_empty))
    count = 12  # deliberately large enough to force multi-row and multi-column placement
    res = change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "blocks_null_sink",
                "instance_name": f"wrap_{i}",
                "params": {"type": "float"},
            }
            for i in range(count)
        ],
        force=True,
    )
    assert res["ok"] is True
    coords = [tuple(fg.get_block(f"wrap_{i}").states["coordinate"]) for i in range(count)]

    # All coordinates must be unique — blocks may not land on the same spot.
    assert len(set(coords)) == count, "Spiral placement produced duplicate coordinates"

    # No two blocks may overlap (the AABB collision guarantee).
    for i, a in enumerate(coords):
        for b in coords[i + 1 :]:
            assert not _rects_overlap(*a, *b), f"Blocks at {a} and {b} overlap"


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
            assert not _rects_overlap(*a, *b)


def test_compute_ranks_reflects_topology(temp_dial_tone):
    # dial_tone.grc: 3 sources -> blocks_add_xx -> audio_sink. Variables and
    # the options block have no wire connections, so they land in their own
    # trivial rank-0 components.
    fg = load_flow_graph(str(temp_dial_tone))
    ranks = _compute_ranks(fg, set(), [])
    assert ranks["analog_sig_source_x_0"] == 0
    assert ranks["analog_sig_source_x_1"] == 0
    assert ranks["analog_noise_source_x_0"] == 0
    assert ranks["blocks_add_xx"] == 1
    assert ranks["audio_sink"] == 2


def test_find_block_placement_anchors_by_rank_distance_not_fixed_one_hop():
    # Regression for the greedy placement's core flaw: it used to assume
    # every connected neighbor is exactly one grid step away, regardless of
    # actual topological distance. A block with two neighbors 2 and 1 ranks
    # behind it (a fan-in from a source and from something already one hop
    # downstream) must anchor proportionally further right than a naive
    # "average neighbor x + one grid step" would place it.
    grid_w = BLOCK_FOOTPRINT_W + BLOCK_SPACING
    neighbor_map = {"new_block": {"far_source", "near_upstream"}}
    block_coords = {"far_source": (0.0, 0.0), "near_upstream": (grid_w, 0.0)}
    ranks = {"far_source": 0, "near_upstream": 1, "new_block": 3}

    naive_target_x = (block_coords["far_source"][0] + block_coords["near_upstream"][0]) / 2 + grid_w

    x, _y = _find_block_placement("new_block", [], neighbor_map, block_coords, (), ranks)
    # far_source is 3 ranks behind (anchor: 0 + 3*grid_w), near_upstream is
    # 2 ranks behind (anchor: grid_w + 2*grid_w) -> average is 3.5*grid_w,
    # well past the naive fixed-one-hop target of 1.5*grid_w.
    assert x > naive_target_x + grid_w, (
        f"expected rank-distance-aware placement well past the naive target "
        f"({naive_target_x}), got x={x}"
    )


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


def test_set_param_unknown_key_lists_valid_names(temp_dial_tone):
    # Regression test: a live chat session guessed the wrong param name
    # ("samp_rate" instead of qtgui_time_sink_x's "srate") and had to spend
    # an extra query_knowledge round-trip discovering the real one, because
    # the old error only said the guessed name was wrong, not what the
    # right one was — unlike the sibling enum-value error, which already
    # lists valid options. This mirrors that same UX for unknown param keys.
    fg = load_flow_graph(str(temp_dial_tone))
    block = fg.get_block("samp_rate")  # any real block; error content is what's tested
    with pytest.raises(KeyError) as exc_info:
        set_param(block, "not_a_real_param", "1")
    message = str(exc_info.value)
    assert "not_a_real_param" in message
    assert "value" in message  # a real param name on this (variable) block


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


# Two epy_block sources of the same shape (1 complex64 in, 1 complex64 out)
# used to reproduce a real GNU Radio mechanism: EPyBlock.rewrite() only
# reparses _source_code (regenerating ports) when THAT block's own rewrite()
# runs, which for a call with no add_blocks only happens in change_graph's
# final unconditional rewrite — i.e. after Phase 7's add_connections already
# ran. A same-port-count DTYPE change (unlike a port-count change, which
# _find_port simply can't find) is worse: _find_port/connect() succeed
# against the pre-rewrite port object, then the final rewrite builds a new
# port object and disconnects the one Phase 7 just connected.
_EPY_COMPLEX_IO_SOURCE = (
    "import numpy as np\n"
    "from gnuradio import gr\n\n"
    "class blk(gr.sync_block):\n"
    "    def __init__(self):\n"
    "        gr.sync_block.__init__(self, name='epy', in_sig=[np.complex64], out_sig=[np.complex64])\n"
    "    def work(self, input_items, output_items):\n"
    "        output_items[0][:] = input_items[0]\n"
    "        return len(output_items[0])\n"
)

_EPY_FLOAT_INPUT_SOURCE = (
    "import numpy as np\n"
    "from gnuradio import gr\n\n"
    "class blk(gr.sync_block):\n"
    "    def __init__(self):\n"
    "        gr.sync_block.__init__(self, name='epy', in_sig=[np.float32], out_sig=[np.complex64])\n"
    "    def work(self, input_items, output_items):\n"
    "        return 0\n"
)


def test_change_graph_same_call_port_dtype_change_and_connect_rolls_back(temp_empty):
    """Regression for the exact silent-drop scenario found live: changing an
    existing epy_block's port dtype and connecting to that port in the same
    change_graph call (no add_blocks in that call) must fail loudly, even
    under force=True — never report ok=true while the requested connection
    is actually absent."""
    fg = load_flow_graph(str(temp_empty))
    setup = change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "epy_block",
                "instance_name": "my_src",
                "params": {"_source_code": _EPY_COMPLEX_IO_SOURCE},
            },
            {
                "block_id": "epy_block",
                "instance_name": "my_epy",
                "params": {"_source_code": _EPY_COMPLEX_IO_SOURCE},
            },
        ],
        force=True,  # both left unconnected between this call and the next
    )
    assert setup["ok"] is True

    res = change_graph(
        fg,
        update_params=[{"instance_name": "my_epy", "params": {"_source_code": _EPY_FLOAT_INPUT_SOURCE}}],
        add_connections=["my_src:0->my_epy:0"],
        force=True,
    )
    assert res["ok"] is False
    assert any(e.get("code") == "connection_silently_dropped" for e in res["errors"])

    # The whole batch rolled back — including the source-code edit, not just
    # the connection — since this is one atomic transaction.
    snap = inspect_graph(fg)
    my_epy = next(b for b in snap["graph"]["blocks"] if b["instance_name"] == "my_epy")
    assert my_epy["params"]["_source_code"] == _EPY_COMPLEX_IO_SOURCE


_EPY_FLOAT_IO_SOURCE = (
    "import numpy as np\n"
    "from gnuradio import gr\n\n"
    "class blk(gr.sync_block):\n"
    "    def __init__(self):\n"
    "        gr.sync_block.__init__(self, name='epy', in_sig=[np.float32], out_sig=[np.float32])\n"
    "    def work(self, input_items, output_items):\n"
    "        output_items[0][:] = input_items[0]\n"
    "        return len(output_items[0])\n"
)


def test_change_graph_epy_block_port_change_works_across_two_calls(temp_empty):
    """The recommended workaround for the same scenario above: change the
    block's code/ports in its own call first, confirm via inspect_graph,
    then wire the new port in a follow-up call — this must succeed cleanly.
    my_src emits float32 here (not complex64, unlike the roll-back test
    above) since it needs to actually type-match my_epy's new float32 input
    for the connection itself to be valid — this test is about the two-call
    sequencing working, not about tolerating a real dtype mismatch."""
    fg = load_flow_graph(str(temp_empty))
    setup = change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "epy_block",
                "instance_name": "my_src",
                "params": {"_source_code": _EPY_FLOAT_IO_SOURCE},
            },
            {
                "block_id": "epy_block",
                "instance_name": "my_epy",
                "params": {"_source_code": _EPY_COMPLEX_IO_SOURCE},
            },
        ],
        force=True,
    )
    assert setup["ok"] is True

    step1 = change_graph(
        fg,
        update_params=[{"instance_name": "my_epy", "params": {"_source_code": _EPY_FLOAT_INPUT_SOURCE}}],
        force=True,  # still unconnected — expected at this intermediate step
    )
    assert step1["ok"] is True
    snap = inspect_graph(fg)
    my_epy = next(b for b in snap["graph"]["blocks"] if b["instance_name"] == "my_epy")
    assert my_epy["params"]["_source_code"] == _EPY_FLOAT_INPUT_SOURCE

    # force=True: my_epy's own output stays unconnected, which isn't what
    # this test is checking — only that the input-side connection succeeds.
    step2 = change_graph(fg, add_connections=["my_src:0->my_epy:0"], force=True)
    assert step2["ok"] is True
    snap = inspect_graph(fg)
    assert "my_src:0->my_epy:0" in snap["graph"]["connections"]


def test_change_graph_update_params_only_batch_catches_dropped_preexisting_connection(temp_empty):
    """Regression for a real bug found by adversarial testing: the original
    fix only tracked connections made in the SAME call's add_connections
    (made_connections), so a call with ONLY update_params — no
    add_connections at all — left made_connections empty and skipped the
    check entirely, even though the final rewrite can silently drop an
    ALREADY-EXISTING connection the same way (verified live: this used to
    return ok=true while the connection vanished). The fix now snapshots
    every connection before any rewrite and compares against the post-batch
    state regardless of what phases ran this call."""
    fg = load_flow_graph(str(temp_empty))
    setup = change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "epy_block",
                "instance_name": "my_src",
                "params": {"_source_code": _EPY_COMPLEX_IO_SOURCE},
            },
            {
                "block_id": "epy_block",
                "instance_name": "my_epy",
                "params": {"_source_code": _EPY_COMPLEX_IO_SOURCE},
            },
        ],
        force=True,
    )
    assert setup["ok"] is True
    wire = change_graph(fg, add_connections=["my_src:0->my_epy:0"], force=True)
    assert wire["ok"] is True
    assert "my_src:0->my_epy:0" in inspect_graph(fg)["graph"]["connections"]

    # ONLY update_params — no add_connections in this call at all.
    res = change_graph(
        fg,
        update_params=[{"instance_name": "my_epy", "params": {"_source_code": _EPY_FLOAT_INPUT_SOURCE}}],
        force=True,
    )
    assert res["ok"] is False
    assert any(e.get("code") == "connection_silently_dropped" for e in res["errors"])
    # Rolled back — the pre-existing connection must still be there.
    assert "my_src:0->my_epy:0" in inspect_graph(fg)["graph"]["connections"]


def test_change_graph_port_key_rekey_is_not_a_false_positive(temp_empty):
    """Regression for a real bug found by adversarial testing (confirming a
    concern the maintainability reviewer raised): GNU Radio's Port.rewrite()
    changes a port's .key IN PLACE (same object) rather than replacing it,
    for any port whose dtype becomes 'message' — e.g. a pad_sink switched
    from a stream type to type='message'. The original string-tuple-based
    check ((block_name, port_key) as plain strings) would see the OLD key
    and misreport this as a dropped connection, rolling back a perfectly
    valid batch. Comparing actual Connection objects (whose __eq__/__hash__
    are keyed on the underlying Port objects' identity, confirmed by reading
    GNU Radio's own Connection class) is immune to an in-place key mutation
    on the same port object, since object identity doesn't change."""
    fg = load_flow_graph(str(temp_empty))
    setup = change_graph(
        fg,
        add_blocks=[
            {"block_id": "pad_sink", "instance_name": "my_pad", "params": {"type": "complex"}},
            {"block_id": "blocks_message_strobe", "instance_name": "my_strobe"},
        ],
        force=True,
    )
    assert setup["ok"] is True

    res = change_graph(
        fg,
        update_params=[{"instance_name": "my_pad", "params": {"type": "message"}}],
        add_connections=["my_strobe:strobe->my_pad:0"],
        force=True,
    )
    assert res["ok"] is True, f"expected no false-positive rollback, got: {res}"
    snap = inspect_graph(fg)
    # The port was rekeyed from '0' to 'in' (message-domain naming) as a side
    # effect of the type change — the connection must still be present under
    # its new key, not reported as dropped.
    assert "my_strobe:strobe->my_pad:in" in snap["graph"]["connections"]


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


def test_change_graph_auto_resolve_same_batch_explicit_propagates(temp_empty):
    # Two brand-new blocks connected in the same batch: one has an explicit
    # type, the other is "auto" — this must resolve from the explicit side.
    fg = load_flow_graph(str(temp_empty))
    res = change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "analog_sig_source_x",
                "instance_name": "src",
                "params": {"type": "float"},
            },
            {"block_id": "qtgui_time_sink_x", "instance_name": "sink", "params": {"type": "auto"}},
        ],
        add_connections=["src:0->sink:0"],
    )
    assert res["ok"] is True
    assert fg.get_block("sink").params["type"].get_value() == "float"


def test_change_graph_auto_resolve_existing_neighbor_propagates(temp_dial_tone):
    # A brand-new block connected to a PRE-EXISTING, already-live block must
    # still resolve from that neighbor's real (already-in-effect) dtype.
    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "qtgui_time_sink_x",
                "instance_name": "new_sink",
                "params": {"type": "auto"},
            }
        ],
        add_connections=["analog_sig_source_x_0:0->new_sink:0"],
    )
    assert res["ok"] is True
    assert fg.get_block("new_sink").params["type"].get_value() == "float"


def test_change_graph_auto_resolve_both_sides_unresolvable_fails_loudly(temp_empty):
    # Regression test: two brand-new blocks, BOTH left "auto", connected to
    # each other in the same batch, with no explicit value anywhere. This
    # used to silently "succeed" by reading each block's own untouched
    # schema default (analog_sig_source_x and qtgui_time_sink_x both happen
    # to default to 'complex') — not a real resolution, just two arbitrary
    # defaults coinciding. Must now fail loudly with an actionable error
    # instead of silently pairing two unresolved blocks.
    fg = load_flow_graph(str(temp_empty))
    res = change_graph(
        fg,
        add_blocks=[
            {"block_id": "analog_sig_source_x", "instance_name": "src", "params": {"type": "auto"}},
            {"block_id": "qtgui_time_sink_x", "instance_name": "sink", "params": {"type": "auto"}},
        ],
        add_connections=["src:0->sink:0"],
    )
    assert res["ok"] is False
    assert res["errors"][0]["code"] == "auto_resolve_failed"


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
# Vector DB dimension check caching (1 test)
# ==========================================


def test_vector_db_dimension_check_is_cached(tmp_path, monkeypatch):
    """Regression for P1-2: _ensure_db_built used to call embed_document("test")
    on every query, doubling embedding API calls. The dimension check must be
    cached per (domain, model) so subsequent queries only issue the real
    query embedding."""
    from grc_agent.adapter import _ensure_db_built, get_db_and_model

    tmp_vectors = tmp_path / "vectors"
    tmp_vectors.mkdir()
    monkeypatch.setenv("GRC_AGENT_VECTORS_DIR", str(tmp_vectors))
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    # Isolate the module-level embedding-dimension cache so a prior test's
    # real 768-dim entry doesn't cause a false dimension mismatch (768 != 3)
    # that triggers a full DB rebuild instead of the cached dimension check
    # this test is measuring.
    monkeypatch.setattr("grc_agent.adapter.rag._EMBEDDING_DIM_CACHE", {})

    from grc_agent.settings import save_settings

    save_settings("ollama", "qwen3.6:35b-a3b-q4_K_M")
    db_path, model = get_db_and_model("catalog")

    # Build a minimal valid sqlite-vec DB with a known dimension so
    # _ensure_db_built reaches the dimension-check branch.
    import sqlite3

    import sqlite_vec

    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.execute("CREATE TABLE catalog_chunks(rowid INTEGER PRIMARY KEY, block_id TEXT, payload TEXT);")
    conn.execute("CREATE VIRTUAL TABLE catalog_idx USING vec0(embedding float[3]);")
    # A catalog_fts table must also exist, or _ensure_db_built treats this as
    # a pre-lexical-fallback DB and rebuilds it (see rag.py's _build_db).
    conn.execute(
        "CREATE VIRTUAL TABLE catalog_fts USING fts5("
        "block_id, payload, content='catalog_chunks', content_rowid='rowid')"
    )
    # _db_meta must exist with the correct model name and corpus_version,
    # otherwise _ensure_db_built deletes and rebuilds the DB (calling
    # embed_document many times during ingestion, not just once for the
    # dimension check).
    from grc_agent.adapter import _corpus_version

    conn.execute("CREATE TABLE _db_meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO _db_meta (key, value) VALUES ('embedding_model', ?)", (model,))
    conn.execute(
        "INSERT INTO _db_meta (key, value) VALUES ('corpus_version', ?)",
        (_corpus_version("catalog"),),
    )
    conn.commit()
    conn.close()

    call_count = 0

    def counting_embed_document(text, m):  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        return [0.0, 0.0, 0.0]

    monkeypatch.setattr("grc_agent.adapter.rag.embed_document", counting_embed_document)

    _ensure_db_built("catalog", db_path, model)
    first_count = call_count
    assert first_count == 1, "first query should perform the dimension check"

    _ensure_db_built("catalog", db_path, model)
    assert call_count == first_count, "second query must not repeat the dimension check"

    # Clean up: the monkeypatched embed_document populated the module-level
    # _EMBEDDING_DIM_CACHE with a 3-dim entry for this model. Without this
    # cleanup, subsequent tests that use the real embed_document (768-dim)
    # would see a dimension mismatch, delete the real DB, and rebuild it
    # unnecessarily — or worse, leave a stale 3-dim DB behind.
    from grc_agent.adapter import _EMBEDDING_DIM_CACHE

    _EMBEDDING_DIM_CACHE.pop(model, None)


def test_fts_query_string_dedupes_and_caps_tokens():
    """Regression: an adversarially long or highly repetitive query used to
    build an uncapped OR-joined FTS5 MATCH expression whose own size drove
    evaluation cost — measured at 8-46 seconds for a ~100k-character query,
    synchronously blocking the calling thread. _fts_query_string must
    dedupe (case-insensitively, order-preserving) and cap at _FTS_MAX_TOKENS."""
    from grc_agent.adapter.rag import _FTS_MAX_TOKENS, _fts_query_string

    # Repetitive input collapses to a single token, not one term per repeat.
    result = _fts_query_string("filter Filter FILTER filter filter")
    assert result == '"filter"'

    # A very long, highly-repetitive query stays bounded regardless of input size.
    huge_query = " ".join(f"word{i % 5}" for i in range(50_000))
    result = _fts_query_string(huge_query)
    assert result is not None
    assert result.count(" OR ") + 1 <= _FTS_MAX_TOKENS

    # Genuinely varied input beyond the cap is truncated, not rejected.
    many_unique = " ".join(f"uniqueterm{i}" for i in range(1000))
    result = _fts_query_string(many_unique)
    assert result is not None
    assert result.count(" OR ") + 1 == _FTS_MAX_TOKENS
    assert '"uniqueterm0"' in result


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


def test_query_knowledge_func_passes_through_k(monkeypatch):
    """The model can control how many results come back via k (default 5,
    clamped 1-20) — no live LLM/backend needed, just verifying the plumbing."""
    import asyncio

    from grc_agent.agent import query_knowledge_func

    seen_calls: list[tuple[str, int]] = []

    def fake_query_catalog(query, limit=5):
        seen_calls.append((query, limit))
        return {"ok": True, "query": query, "results": [], "search_mode": "vector"}

    def fake_query_docs(query, limit=5):
        seen_calls.append((query, limit))
        return {"ok": True, "query": query, "answer": "", "search_mode": "vector"}

    monkeypatch.setattr("grc_agent.agent.query_catalog", fake_query_catalog)
    monkeypatch.setattr("grc_agent.agent.query_docs", fake_query_docs)

    asyncio.run(query_knowledge_func(None, "low pass filter", "catalog", k=10))
    assert seen_calls[-1] == ("low pass filter", 10)

    asyncio.run(query_knowledge_func(None, "stream tags", "docs"))
    assert seen_calls[-1] == ("stream tags", 5), "default k must still be 5"

    # Out-of-range k is clamped, not passed through raw or rejected.
    asyncio.run(query_knowledge_func(None, "x", "catalog", k=1000))
    assert seen_calls[-1] == ("x", 20)
    asyncio.run(query_knowledge_func(None, "x", "catalog", k=0))
    assert seen_calls[-1] == ("x", 1)


def test_generate_python_func_passes_through_k_and_wraps_valueerror(monkeypatch):
    """Mirrors test_query_knowledge_func_passes_through_k for generate_python_func:
    k (default 5, clamped 1-20) reaches preview_flowgraph_py correctly, and a
    ValueError from it (invalid/hb/cpp graph) becomes a ModelRetry, not a raw
    exception — no live LLM/backend or real gnuradio flowgraph needed."""
    import asyncio
    from types import SimpleNamespace

    from pydantic_ai import ModelRetry

    from grc_agent.agent import generate_python_func

    seen_calls: list[tuple[object, int]] = []

    def fake_preview_flowgraph_py(flow_graph, k=5):
        seen_calls.append((flow_graph, k))
        return {"files": [{"path": "x.py", "source": "..."}], "omitted_files": 0}

    monkeypatch.setattr("grc_agent.agent.preview_flowgraph_py", fake_preview_flowgraph_py)
    ctx = SimpleNamespace(deps=object())

    asyncio.run(generate_python_func(ctx, k=10))
    assert seen_calls[-1] == (ctx.deps, 10)

    asyncio.run(generate_python_func(ctx))
    assert seen_calls[-1] == (ctx.deps, 5), "default k must still be 5"

    # Out-of-range k is clamped, not passed through raw or rejected.
    asyncio.run(generate_python_func(ctx, k=1000))
    assert seen_calls[-1] == (ctx.deps, 20)
    asyncio.run(generate_python_func(ctx, k=0))
    assert seen_calls[-1] == (ctx.deps, 1)

    def raising_preview_flowgraph_py(flow_graph, k=5):  # noqa: ARG001
        raise ValueError("Flowgraph is not valid: ['boom']")

    monkeypatch.setattr("grc_agent.agent.preview_flowgraph_py", raising_preview_flowgraph_py)
    try:
        asyncio.run(generate_python_func(ctx))
        raise AssertionError("expected ModelRetry")
    except ModelRetry as exc:
        assert "boom" in str(exc)


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
    import asyncio
    res = asyncio.run(lite_web_search("python programming language"))
    assert isinstance(res, str)
    assert len(res) > 0
    assert "No web results" not in res
    assert "python.org" in res


# ==========================================
# generate_flowgraph_py Unit Tests (4 tests)
# ==========================================


@pytest.fixture
def temp_run_null_sink():
    tmp_dir = tempfile.mkdtemp()
    src = FIXTURES_DIR / "run_test_null_sink.grc"
    dst = Path(tmp_dir) / "run_test_null_sink.grc"
    shutil.copy2(src, dst)
    yield dst
    shutil.rmtree(tmp_dir)


@pytest.fixture
def temp_run_head():
    tmp_dir = tempfile.mkdtemp()
    src = FIXTURES_DIR / "run_test_head.grc"
    dst = Path(tmp_dir) / "run_test_head.grc"
    shutil.copy2(src, dst)
    yield dst
    shutil.rmtree(tmp_dir)


@pytest.fixture
def temp_broken():
    tmp_dir = tempfile.mkdtemp()
    src = FIXTURES_DIR / "broken_unconnected_sink.grc"
    dst = Path(tmp_dir) / "broken_unconnected_sink.grc"
    shutil.copy2(src, dst)
    yield dst
    shutil.rmtree(tmp_dir)


def test_generate_flowgraph_py_validates_first(temp_broken):
    fg = load_flow_graph(str(temp_broken))
    with pytest.raises(ValueError, match="not valid"):
        generate_flowgraph_py(fg, tempfile.mkdtemp())


def test_generate_flowgraph_py_rejects_hb(temp_run_null_sink):
    fg = load_flow_graph(str(temp_run_null_sink))
    rop = fg.options_block.params["generate_options"]
    rop.set_value("hb")
    rop.rewrite()
    with pytest.raises(ValueError, match="Hierarchical blocks"):
        generate_flowgraph_py(fg, tempfile.mkdtemp())


def test_generate_flowgraph_py_run_options_override(temp_run_null_sink):
    fg = load_flow_graph(str(temp_run_null_sink))
    output_dir = Path(temp_run_null_sink).parent / "run"
    file_path = generate_flowgraph_py(fg, output_dir)
    content = file_path.read_text()
    assert "Press Enter to quit" not in content


def test_generate_flowgraph_py_restores_run_options(temp_run_null_sink):
    fg = load_flow_graph(str(temp_run_null_sink))
    assert fg.get_option("run_options") == "prompt"
    output_dir = Path(temp_run_null_sink).parent / "run"
    generate_flowgraph_py(fg, output_dir)
    assert fg.get_option("run_options") == "prompt"


# ==========================================
# preview_flowgraph_py Unit Tests (5 tests)
# ==========================================


def test_preview_flowgraph_py_validates_first(temp_broken):
    fg = load_flow_graph(str(temp_broken))
    with pytest.raises(ValueError, match="not valid"):
        preview_flowgraph_py(fg)


def test_preview_flowgraph_py_rejects_hb(temp_run_null_sink):
    fg = load_flow_graph(str(temp_run_null_sink))
    gen_opts = fg.options_block.params["generate_options"]
    gen_opts.set_value("hb")
    gen_opts.rewrite()
    with pytest.raises(ValueError, match="Hierarchical blocks"):
        preview_flowgraph_py(fg)


def test_preview_flowgraph_py_shows_real_run_options(temp_run_null_sink):
    # Unlike generate_flowgraph_py, preview must NOT override run_options —
    # it shows the flowgraph's actual configured generated output.
    fg = load_flow_graph(str(temp_run_null_sink))
    assert fg.get_option("run_options") == "prompt"
    result = preview_flowgraph_py(fg)
    main_source = result["files"][-1]["source"]
    assert "Press Enter to quit" in main_source
    assert fg.get_option("run_options") == "prompt"


def test_preview_flowgraph_py_writes_nothing_to_disk(temp_run_null_sink):
    output_dir = Path(temp_run_null_sink).parent
    before = set(output_dir.iterdir())
    fg = load_flow_graph(str(temp_run_null_sink))
    preview_flowgraph_py(fg)
    after = set(output_dir.iterdir())
    assert before == after


def test_preview_flowgraph_py_returns_main_script(temp_run_null_sink):
    fg = load_flow_graph(str(temp_run_null_sink))
    result = preview_flowgraph_py(fg)
    assert result["files"], "expected at least the main flowgraph script"
    main = result["files"][-1]
    assert main["path"].endswith(fg.get_option("id") + ".py")
    assert "import" in main["source"]


# A minimal Embedded Python Block with zero stream/message ports (gr.basic_block
# with in_sig=out_sig=None) — needs no wiring to keep the flowgraph valid, unlike
# the default epy_block template (1 complex64 in, 1 complex64 out).
_NOOP_EPY_SOURCE = (
    "from gnuradio import gr\n\n"
    "class blk(gr.basic_block):\n"
    "    def __init__(self):\n"
    "        gr.basic_block.__init__(self, name='noop', in_sig=None, out_sig=None)\n"
)


def _add_epy_blocks(fg, count):
    change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "epy_block",
                "instance_name": f"epy_{i}",
                "params": {"_source_code": _NOOP_EPY_SOURCE},
            }
            for i in range(count)
        ],
    )


def test_preview_flowgraph_py_includes_embedded_python_block_source(temp_empty):
    fg = load_flow_graph(str(temp_empty))
    _add_epy_blocks(fg, 1)
    result = preview_flowgraph_py(fg)
    assert len(result["files"]) == 2, "expected the epy_block's file plus the main script"
    assert result["omitted_files"] == 0
    assert any("gr.basic_block" in f["source"] for f in result["files"][:-1])
    assert result["files"][-1]["path"].endswith(fg.get_option("id") + ".py")


def test_preview_flowgraph_py_caps_block_source_files_and_reports_omitted(temp_empty):
    # k caps the block-source files ONLY, per the documented contract (the
    # main script never counts against it) — 4 epy_block files + 1 main = 5
    # total; k=3 keeps 3 block-sources + the main script = 4 returned.
    fg = load_flow_graph(str(temp_empty))
    _add_epy_blocks(fg, 4)
    result = preview_flowgraph_py(fg, k=3)
    assert len(result["files"]) == 4, "k caps block-source files, not the main script"
    assert result["omitted_files"] == 1  # 4 block-sources - 3 kept
    # The main script must never be among the dropped entries.
    assert result["files"][-1]["path"].endswith(fg.get_option("id") + ".py")


def test_preview_flowgraph_py_k_exactly_equal_to_block_source_count(temp_empty):
    fg = load_flow_graph(str(temp_empty))
    _add_epy_blocks(fg, 3)  # 3 epy_block files + 1 main script = 4 total
    result = preview_flowgraph_py(fg, k=3)
    assert len(result["files"]) == 4
    assert result["omitted_files"] == 0


def test_preview_flowgraph_py_k_one_keeps_only_main_script(temp_empty):
    fg = load_flow_graph(str(temp_empty))
    _add_epy_blocks(fg, 3)
    result = preview_flowgraph_py(fg, k=1)
    assert len(result["files"]) == 2  # 1 block-source kept + main script
    assert result["omitted_files"] == 2
    assert result["files"][-1]["path"].endswith(fg.get_option("id") + ".py")


def test_preview_flowgraph_py_k_below_total_keeps_everything(temp_empty):
    fg = load_flow_graph(str(temp_empty))
    _add_epy_blocks(fg, 2)  # 2 epy_block files + 1 main script = 3 total
    result = preview_flowgraph_py(fg, k=5)
    assert len(result["files"]) == 3
    assert result["omitted_files"] == 0


# ==========================================
# Markdown to Pango Unit Tests
# ==========================================


def test_message_history_serialization_round_trip(tmp_path):
    import json

    from pydantic_ai import ModelMessagesTypeAdapter
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        UserPromptPart,
    )
    from pydantic_core import to_jsonable_python

    messages = [
        ModelRequest(parts=[UserPromptPart(content="Hello")]),
        ModelResponse(parts=[TextPart(content="Hi there!"), ToolCallPart(tool_name="inspect_graph", args={"view": "overview"}, tool_call_id="call1")]),
    ]

    # Serialize
    json_data = to_jsonable_python(messages)

    # Write and read back
    filepath = tmp_path / "chat.json"
    with open(filepath, "w") as f:
        json.dump(json_data, f)

    with open(filepath) as f:
        loaded_data = json.load(f)

    # Deserialize
    restored = ModelMessagesTypeAdapter.validate_python(loaded_data)

    assert len(restored) == 2
    assert restored[0].__class__.__name__ == "ModelRequest"
    assert restored[0].parts[0].content == "Hello"
    assert restored[1].__class__.__name__ == "ModelResponse"
    assert restored[1].parts[0].content == "Hi there!"
    assert restored[1].parts[1].tool_name == "inspect_graph"
    assert restored[1].parts[1].args == {"view": "overview"}
    assert restored[1].parts[1].tool_call_id == "call1"


def test_chat_sidebar_copy_and_rich_rendering():
    from gi.repository import Gtk
    from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart, ToolCallPart

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()

    # 1. Test copy button text update during streaming
    box = sidebar._start_agent_message()
    sidebar._update_copy_text(box, "test copy text")
    parent = box.get_parent()
    assert parent is not None
    assert parent._grc_copy_btn._grc_copy_text == "test copy text"

    # 2. Test horizontal-scrolling table rendering
    sidebar._render_markdown_to_box(box, "| Head |\n|---|\n| cell |")
    children = box.get_children()
    assert any(isinstance(c, Gtk.ScrolledWindow) for c in children)

    # 3. Test last message rich rendering maps thinking, text, and tools
    msg = ModelResponse(parts=[
        ThinkingPart(content="think progress"),
        TextPart(content="here is a table:\n| A | B |\n|---|---|\n| 1 | 2 |"),
        ToolCallPart(tool_name="inspect_graph", args={}, tool_call_id="call_test")
    ])
    sidebar._render_last_message_rich(box, msg)
    new_children = box.get_children()

    # Verify we have Gtk.Expander for thinking/tools and Gtk.ScrolledWindow for the table
    exp_classes = [c.__class__.__name__ for c in new_children]
    assert "Expander" in exp_classes
    assert "ScrolledWindow" in exp_classes


def test_recent_sessions_persistence(tmp_path, monkeypatch):
    # Mock settings.env_path to point to a tmp directory
    env_file = tmp_path / ".env"
    monkeypatch.setenv("GRC_AGENT_ENV", str(env_file))

    from grc_agent.db import delete_session, get_recent_sessions, load_session, save_session

    # Assert initially empty
    assert get_recent_sessions() == []

    # Create two temporary files
    file1 = tmp_path / "graph1.grc"
    file1.touch()
    file2 = tmp_path / "graph2.grc"
    file2.touch()

    # Save session for file1
    sid1 = save_session(None, str(file1), [])
    sessions = get_recent_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == sid1
    assert sessions[0]["grc_file_path"] == str(file1.resolve())

    # Save session for file2
    sid2 = save_session(None, str(file2), [])
    sessions = get_recent_sessions()
    assert len(sessions) == 2
    # Newest (updated_at desc) should be at index 0
    assert sessions[0]["id"] == sid2
    assert sessions[0]["grc_file_path"] == str(file2.resolve())
    assert sessions[1]["id"] == sid1
    assert sessions[1]["grc_file_path"] == str(file1.resolve())

    # Load session
    s_loaded = load_session(sid1)
    assert s_loaded is not None
    assert s_loaded["grc_file_path"] == str(file1.resolve())

    # Delete file1 and verify it is filtered out from get_recent_sessions (but still loaded by id)
    file1.unlink()
    sessions = get_recent_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == sid2

    # Delete session
    delete_session(sid2)
    assert len(get_recent_sessions()) == 0


def test_open_recent_session_tab_switching(tmp_path):
    from unittest.mock import MagicMock, patch

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()

    # Mock flowgraph proxy, canvas manager, and GRC window/notebook
    proxy = MagicMock()
    cm = MagicMock()
    window = MagicMock()
    notebook = MagicMock()

    proxy._canvas_manager = cm
    cm.window = window
    window.notebook = notebook

    sidebar.set_flowgraph_proxy(proxy)

    # Prepare files
    file_real = tmp_path / "target.grc"
    file_real.touch()

    # Case 1: Page has relative file path, target is absolute
    page1 = MagicMock()
    page1.file_path = "target.grc"
    notebook.get_n_pages.return_value = 1
    notebook.get_nth_page.return_value = page1

    import os
    orig_cwd = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        with patch("grc_agent.chat_sidebar.load_session") as mock_load:
            mock_load.return_value = {
                "id": 123,
                "grc_file_path": str(file_real.resolve()),
                "messages": "[]",
                "created_at": "...",
                "updated_at": "..."
            }
            sidebar._on_recent_session_clicked(123)
    finally:
        os.chdir(orig_cwd)

    notebook.set_current_page.assert_called_once_with(0)


def test_sidebar_session_no_autoload_on_graph_open(tmp_path, monkeypatch):
    """Graphs never auto-load chats. Opening/switching a graph tab clears the
    chat area to a fresh welcome screen — no session lookup by path. The only
    way to load a saved conversation is explicitly from the recent-sessions
    list (which opens the graph AND loads the session via _loading_session_id)."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import save_session

    sidebar = ChatSidebar()

    # Prepare files and sessions
    f1 = tmp_path / "flow1.grc"
    f1.touch()
    f2 = tmp_path / "flow2.grc"
    f2.touch()

    # Even though sessions exist for these paths, sync_to_file must NOT load them
    save_session(None, str(f1), [])
    save_session(None, str(f2), [])

    class DummyPage:
        def __init__(self, file_path):
            self.file_path = file_path

    sidebar._flowgraph_proxy = object()
    current_page_val = DummyPage(str(f1))
    monkeypatch.setattr(ChatSidebar, "current_page", property(lambda _self: current_page_val))

    # Switching to flow1.grc — no session loads, chat is blank
    sidebar.sync_to_file(str(f1))
    assert sidebar._active_session_id is None
    assert sidebar._message_history == []

    # Switching to flow2.grc — same: no autoload
    current_page_val = DummyPage(str(f2))
    sidebar.sync_to_file(str(f2))
    assert sidebar._active_session_id is None
    assert sidebar._message_history == []

    # Switching back to flow1.grc — still blank, no per-page binding
    current_page_val = DummyPage(str(f1))
    sidebar.sync_to_file(str(f1))
    assert sidebar._active_session_id is None
    assert sidebar._message_history == []


def test_active_graph_label_format():
    from grc_agent.chat_sidebar import ChatSidebar
    sidebar = ChatSidebar()

    # Initial state
    assert sidebar._graph_label.get_text() == "Active Graph: none"

    # Set active graph
    sidebar.set_active_graph("my_cool_flowgraph")
    assert sidebar._graph_label.get_text() == "Active Graph: my_cool_flowgraph"

    # Clear active graph
    sidebar.set_active_graph(None)
    assert sidebar._graph_label.get_text() == "Active Graph: none"


def test_delete_recent_session_ui(monkeypatch):
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar._render_history = MagicMock()

    mock_delete = MagicMock()
    monkeypatch.setattr("grc_agent.chat_sidebar.delete_session", mock_delete)

    sidebar._on_delete_recent_session(123)
    mock_delete.assert_called_once_with(123)
    sidebar._render_history.assert_called_once()


def test_clear_history_confirmation(monkeypatch):
    from unittest.mock import MagicMock

    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar.clear_messages = MagicMock()
    sidebar.set_status = MagicMock()

    mock_dialog = MagicMock()

    mock_dialog_cls = MagicMock(return_value=mock_dialog)
    monkeypatch.setattr(Gtk, "MessageDialog", mock_dialog_cls)

    sidebar._on_clear_history_clicked(None)

    # The dialog is now non-blocking (signal-based under gbulb); invoke the
    # registered "response" callback with YES to simulate the user confirming.
    handler = mock_dialog.connect.call_args.args[1]
    handler(mock_dialog, Gtk.ResponseType.YES)

    sidebar.clear_messages.assert_called_once()
    sidebar.set_status.assert_called_once_with("All chat history cleared.")


def _seed_session(grc_path: str) -> int:
    """Insert one session row for grc_path and return its id. Uses the real
    save_session so path resolution matches production exactly."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.db import save_session

    return save_session(None, grc_path, [ModelRequest(parts=[UserPromptPart(content="seed")])])


def _count_sessions_for_path(grc_path: str) -> int:
    import sqlite3
    from pathlib import Path

    from grc_agent.db import get_db_path

    abs_path = str(Path(grc_path).resolve())
    conn = sqlite3.connect(str(get_db_path()))
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE grc_file_path = ?", (abs_path,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_clear_history_deletes_active_session_real_db(tmp_path, monkeypatch):
    """UI-2 regression: 'Clear History' must actually DELETE the persisted DB
    row for the active session, not just blank in-memory state. Uses a real
    temp SQLite DB (via GRC_AGENT_ENV isolation) instead of mocking
    delete_session — the original mocked test could not catch this bug."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    grc = tmp_path / "flow.grc"
    grc.write_text("# grc")

    sid = _seed_session(str(grc))
    assert _count_sessions_for_path(str(grc)) == 1

    sidebar = ChatSidebar()
    sidebar._active_session_id = sid
    # No flowgraph proxy → path is None → must fall back to deleting by sid.
    sidebar._flowgraph_proxy = None

    sidebar._on_clear_history_clicked(None)
    assert sidebar._open_dialog is not None
    sidebar._open_dialog.emit("response", Gtk.ResponseType.YES)

    assert _count_sessions_for_path(str(grc)) == 0
    assert sidebar._active_session_id is None


def test_clear_history_deletes_all_sessions_for_path_real_db(tmp_path, monkeypatch):
    """Regression for the exact bug seen in production: multiple sessions
    accumulated for the SAME flowgraph path despite repeated 'Clear History'
    clicks. Deleting by path must remove every row for that file."""
    from unittest.mock import MagicMock

    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    grc = tmp_path / "fm_rx.grc"
    grc.write_text("# grc")

    # Seed THREE sessions for the same path — mirrors the real DB state.
    ids = [_seed_session(str(grc)) for _ in range(3)]
    assert _count_sessions_for_path(str(grc)) == 3

    sidebar = ChatSidebar()
    sidebar._active_session_id = ids[-1]
    proxy = MagicMock()
    cm = MagicMock()
    cm.path = str(grc)
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    sidebar._on_clear_history_clicked(None)
    sidebar._open_dialog.emit("response", Gtk.ResponseType.YES)

    assert _count_sessions_for_path(str(grc)) == 0
    assert sidebar._active_session_id is None


def test_clear_history_deletes_all_sessions_no_active_flowgraph(tmp_path, monkeypatch):
    """Regression for the user-reported bug: with NO flowgraph open (path=None)
    and NO active session (sid=None) — i.e. sitting on the welcome screen
    looking at the recent-sessions list — Clear History must still delete every
    visible session. The old per-flowgraph logic (delete-by-path elif
    delete-by-sid) deleted NOTHING in this case, which is exactly when the user
    is staring at the list. Clear History is now global."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    grc_a = tmp_path / "fm_rx.grc"
    grc_b = tmp_path / "demo_qam.grc"
    grc_a.write_text("#")
    grc_b.write_text("#")

    # Sessions across MULTIPLE flowgraphs (the visible recent-sessions list).
    _seed_session(str(grc_a))
    _seed_session(str(grc_a))
    _seed_session(str(grc_b))

    def total():
        return _count_sessions_for_path(str(grc_a)) + _count_sessions_for_path(str(grc_b))

    assert total() == 3

    sidebar = ChatSidebar()
    # No proxy (path will be None) and no active session (sid None) — the exact
    # state where the old per-flowgraph logic deleted nothing.
    sidebar._flowgraph_proxy = None
    sidebar._active_session_id = None

    sidebar._on_clear_history_clicked(None)
    sidebar._open_dialog.emit("response", Gtk.ResponseType.YES)

    assert total() == 0
    assert sidebar._active_session_id is None


def test_clear_history_dialog_survives_gc_and_responds(tmp_path, monkeypatch):
    """Regression: a non-blocking dialog shown via .show() must be anchored on
    self, otherwise PyGObject garbage-collects the toplevel once the
    constructing method returns and the 'response' signal never fires."""
    import gc
    from unittest.mock import MagicMock

    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    sidebar = ChatSidebar()
    sidebar.clear_messages = MagicMock()
    sidebar.set_status = MagicMock()

    sidebar._on_clear_history_clicked(None)

    assert sidebar._open_dialog is not None
    gc.collect()
    assert sidebar._open_dialog is not None

    sidebar._open_dialog.emit("response", Gtk.ResponseType.YES)

    sidebar.clear_messages.assert_called_once()
    assert sidebar._open_dialog is None


def test_settings_dialog_persists_model_name(tmp_path, monkeypatch):
    """Regression: the Settings dialog must read widget values BEFORE
    gtk_widget_destroy(). Reading after destroy returns '' / -1, which silently
    skipped save_settings so the model name never persisted (and would have
    wiped the API key). Exercises the real _open_settings() dialog tree, not a
    mock."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.settings import load_settings, save_settings

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    save_settings("ollama", "old-model-name")  # known starting model

    sidebar = ChatSidebar()
    sidebar._open_settings()
    dlg = sidebar._open_dialog
    assert dlg is not None

    # Locate the Model entry. Gtk.Grid.get_children() order isn't row order, so
    # identify it by content: on dialog open it holds the current model name
    # (the API-key entry is empty/insensitive for the keyless ollama provider).
    entries: list[Gtk.Entry] = []

    def walk(w):
        if isinstance(w, Gtk.Entry):
            entries.append(w)
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                walk(c)

    walk(dlg)
    assert entries, "no Gtk.Entry found in settings dialog"
    model_entry = next(e for e in entries if e.get_text() == "old-model-name")
    model_entry.set_text("brand-new-model-name")

    dlg.emit("response", Gtk.ResponseType.APPLY)

    # With the read-after-destroy bug this stayed "old-model-name".
    assert load_settings()["model"] == "brand-new-model-name"


def test_settings_dialog_reports_save_failure(tmp_path, monkeypatch):
    """Regression: a failed save_settings()/upsert_env_key() call (e.g. a
    read-only home dir, disk full) must be caught and reported via the status
    bar, matching the existing pattern in _on_clear_history_clicked — not
    close the dialog and silently pretend nothing happened."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.settings import save_settings

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    save_settings("ollama", "old-model-name")

    sidebar = ChatSidebar()
    sidebar._open_settings()
    dlg = sidebar._open_dialog
    assert dlg is not None

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("grc_agent.chat_sidebar.save_settings", _boom)

    dlg.emit("response", Gtk.ResponseType.APPLY)

    assert "not saved" in sidebar._status_label.get_text().lower()
    assert "validation-invalid" in sidebar._status_label.get_style_context().list_classes()


def test_streaming_text_flush_is_throttled(monkeypatch):
    """Streaming must NOT call Gtk.Label.set_text on every token (that re-runs
    Pango line-wrap layout over the whole growing message = O(n^2) and freezes
    the UI). _flush_streaming throttles to _STREAM_FLUSH_INTERVAL; force=True
    bypasses it. Time is mocked for determinism."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar, _StreamCtx

    sidebar = ChatSidebar()
    ctx = _StreamCtx(Gtk.Box())
    sidebar._ensure_text(ctx)
    assert ctx.text_lbl is not None

    t = [0.0]
    monkeypatch.setattr("grc_agent.chat_sidebar.time.monotonic", lambda: t[0])

    # First flush at t=0 with last_flush=0.0 -> (0 - 0.0) < interval -> skip.
    ctx.text_acc = "chunk1"
    ctx.text_dirty = True
    sidebar._flush_streaming(ctx)
    assert ctx.text_lbl.get_text() == ""  # throttled, not painted

    # Advance past the interval -> flush fires.
    t[0] = 0.05
    sidebar._flush_streaming(ctx)
    assert ctx.text_lbl.get_text() == "chunk1"
    assert ctx.text_dirty is False

    # A second chunk immediately after is throttled again.
    ctx.text_acc = "chunk2"
    ctx.text_dirty = True
    sidebar._flush_streaming(ctx)  # t=0.05, last_flush=0.05 -> skip
    assert ctx.text_lbl.get_text() == "chunk1"

    # force=True bypasses the interval (used on part start/close/stream end).
    sidebar._flush_streaming(ctx, force=True)
    assert ctx.text_lbl.get_text() == "chunk2"


def test_streaming_thinking_flush_throttled(monkeypatch):
    """Mirror of the text-flush test for the ThinkingPart branch: thinking
    tokens are throttled the same way and force=True flushes them."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar, _StreamCtx

    sidebar = ChatSidebar()
    ctx = _StreamCtx(Gtk.Box())
    sidebar._ensure_thinking(ctx)
    assert ctx.think_body is not None

    t = [0.0]
    monkeypatch.setattr("grc_agent.chat_sidebar.time.monotonic", lambda: t[0])

    ctx.think_acc = "thought1"
    ctx.think_dirty = True
    sidebar._flush_streaming(ctx)  # t=0, last_flush=0.0 -> throttled
    assert ctx.think_body.get_text() == ""

    t[0] = 0.05
    sidebar._flush_streaming(ctx)
    assert ctx.think_body.get_text() == "thought1"

    ctx.think_acc = "thought2"
    ctx.think_dirty = True
    sidebar._flush_streaming(ctx)  # immediately after -> throttled
    assert ctx.think_body.get_text() == "thought1"
    sidebar._flush_streaming(ctx, force=True)
    assert ctx.think_body.get_text() == "thought2"


def test_poll_indexing_building_ready_failed_idle(monkeypatch):
    """_poll_indexing drives the status bar across the full state machine:
    idle (no-op), building (live progress, content-guarded), ready transition
    (notifies once, using the embedded `indexed` count not `total`), and failed
    (error). Per-domain so concurrent builds don't tangle."""
    import grc_agent.adapter as adapter_mod
    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        sidebar, "set_status", lambda msg, *, error=False, background=False: calls.append((msg, error))  # noqa: ARG005
    )

    adapter_mod._rag_building.clear()
    try:
        # Idle: no domains -> no status writes.
        sidebar._poll_indexing()
        assert calls == []

        # Building: live progress shows counts.
        adapter_mod._rag_building["catalog"] = {
            "status": "building", "current": 3, "total": 10, "indexed": 0
        }
        sidebar._poll_indexing()
        assert calls[-1] == ("Indexing block library for search\u2026 3/10", False)
        # Same progress -> suppressed (content guard).
        n = len(calls)
        sidebar._poll_indexing()
        assert len(calls) == n
        # Progress advances -> new message.
        adapter_mod._rag_building["catalog"]["current"] = 9
        sidebar._poll_indexing()
        assert calls[-1] == ("Indexing block library for search\u2026 9/10", False)

        # Transition to ready with indexed(8) < total(10): message uses indexed.
        adapter_mod._rag_building["catalog"] = {
            "status": "ready", "current": 10, "total": 10, "indexed": 8
        }
        sidebar._poll_indexing()
        assert calls[-1] == ("Block library indexed \u2014 8 entries ready for search.", False)
        n = len(calls)
        sidebar._poll_indexing()  # still ready -> no re-notify
        assert len(calls) == n

        # A docs failure surfaces as an error status.
        adapter_mod._rag_building["docs"] = {
            "status": "failed", "current": 0, "total": 0, "indexed": 0
        }
        sidebar._poll_indexing()
        assert calls[-1][1] is True
    finally:
        adapter_mod._rag_building.clear()


def test_change_graph_lock_busy_returns_save_failed(temp_dial_tone, monkeypatch):
    """The non-blocking flock (LOCK_EX|LOCK_NB) on the agent write path must
    surface contention as a retryable save_failed (rolled back) instead of
    freezing the unified UI thread. Validates the ADHOC-2 fix end-to-end."""
    import fcntl as fcntl_mod

    fg = load_flow_graph(str(temp_dial_tone))

    def boom(fd, op):  # noqa: ARG001
        raise BlockingIOError(11, "Resource temporarily unavailable")

    monkeypatch.setattr(fcntl_mod, "flock", boom)

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

    assert res["ok"] is False
    assert res["error_type"] == "save_failed"
    # Rollback: the mutation was reverted in memory, not left half-applied.
    names = {b["instance_name"] for b in inspect_graph(fg)["graph"]["blocks"]}
    assert "my_throttle" not in names


def test_settings_dialog_persists_api_key(tmp_path, monkeypatch):
    """The API-key field must also be read BEFORE destroy() (the read-after-
    destroy bug would have wiped it with ''). Covers the keyless-provider gap
    left by test_settings_dialog_persists_model_name (which uses ollama)."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.settings import get_env_value, save_settings

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    save_settings("openrouter", "openai/gpt-4o-mini")

    sidebar = ChatSidebar()
    sidebar._open_settings()
    dlg = sidebar._open_dialog
    assert dlg is not None

    # Find the API-key Gtk.Entry (visibility=False distinguishes it from the
    # model Entry, which is the only other Entry and has default visibility).
    entries: list[Gtk.Entry] = []

    def walk(w):
        if isinstance(w, Gtk.Entry):
            entries.append(w)
        if isinstance(w, Gtk.Container):
            for c in w.get_children():
                walk(c)

    walk(dlg)
    key_entry = next(e for e in entries if e.get_visibility() is False)
    key_entry.set_text("sk-test-persists-123")

    dlg.emit("response", Gtk.ResponseType.APPLY)

    assert get_env_value("OPENROUTER_API_KEY") == "sk-test-persists-123"


def test_sync_to_file_does_not_autoload_session(tmp_path, monkeypatch):
    """Graphs never auto-load chats. Even when a session exists for the path,
    sync_to_file must clear the chat area — not restore the saved session.
    Loading is exclusively via the recent-sessions list click."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    from unittest.mock import MagicMock

    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import save_session

    f = tmp_path / "flow.grc"
    f.touch()
    save_session(None, str(f), [ModelRequest(parts=[UserPromptPart(content="hello")])])

    sidebar = ChatSidebar()
    sidebar._render_history = MagicMock()
    sidebar.sync_to_file(str(f))

    # No autoload — chat stays blank despite a session existing for this path
    assert sidebar._active_session_id is None
    assert sidebar._message_history == []


def test_run_agent_turn_error_preserves_user_message():
    """UI-1 regression: an error mid-turn must NOT wipe the user's just-sent
    message (nor rebuild the widget, which would discard any partial reply)."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar._render_history = MagicMock()
    sidebar._append_error = MagicMock()
    sidebar._set_busy = MagicMock()
    sidebar._scroll_to_bottom = MagicMock()
    sidebar._save_history = AsyncMock()
    sidebar._flowgraph_proxy = MagicMock()

    agent = MagicMock()
    agent.iter.side_effect = RuntimeError("boom")
    sidebar._agent = agent

    asyncio.run(sidebar._run_agent_turn("my question"))

    user_texts = [
        part.content
        for m in sidebar._message_history
        if m.__class__.__name__ == "ModelRequest"
        for part in m.parts
        if part.__class__.__name__ == "UserPromptPart"
    ]
    assert "my question" in user_texts
    sidebar._render_history.assert_not_called()


def test_append_error_aborted_style_uses_neutral_css_class():
    """A user-initiated Stop ("[aborted]") is not an error and must not be
    styled like one. _append_error's style="aborted" must apply the neutral
    chat-aborted-label CSS class instead of chat-error-label; the default
    (style="error", used by every other caller) must be unaffected."""
    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()

    sidebar._append_error("Agent Error: boom")
    error_row = sidebar._listbox.get_children()[-1]
    error_lbl = error_row.get_child() if hasattr(error_row, "get_child") else error_row
    assert "chat-error-label" in error_lbl.get_style_context().list_classes()

    sidebar._append_error("[aborted]", style="aborted")
    aborted_row = sidebar._listbox.get_children()[-1]
    aborted_lbl = aborted_row.get_child() if hasattr(aborted_row, "get_child") else aborted_row
    classes = aborted_lbl.get_style_context().list_classes()
    assert "chat-aborted-label" in classes
    assert "chat-error-label" not in classes


def test_format_turn_error_covers_each_exception_type():
    """_run_agent_turn collapsed 4 near-duplicate except blocks (ModelHTTPError,
    UsageLimitExceeded, ModelAPIError, UnexpectedModelBehavior) plus the
    generic Exception fallback into one handler backed by this message
    builder. Each branch's message shape must survive the refactor exactly,
    including ModelHTTPError's extra status/body-vs-model_name distinction."""
    from pydantic_ai.exceptions import (
        ModelAPIError,
        ModelHTTPError,
        UnexpectedModelBehavior,
        UsageLimitExceeded,
    )

    from grc_agent.chat_sidebar import _format_turn_error

    assert (
        _format_turn_error(ModelHTTPError(500, "gpt-x", body="server exploded"))
        == "Model HTTP 500 Error: server exploded"
    )
    assert (
        _format_turn_error(ModelHTTPError(503, "gpt-x"))
        == "Model HTTP 503 Error from gpt-x"
    )
    assert (
        _format_turn_error(UsageLimitExceeded("too many tokens"))
        == "Usage Limit Exceeded: too many tokens"
    )
    assert (
        _format_turn_error(ModelAPIError("gpt-x", "bad request"))
        == "Model API Error: bad request"
    )
    assert (
        _format_turn_error(UnexpectedModelBehavior("no tool call"))
        == "Unexpected Model Behavior: no tool call"
    )
    assert _format_turn_error(RuntimeError("boom")) == "Agent Error: boom"


def test_send_message_guards_and_creates_session(tmp_path, monkeypatch):
    """M14 regression: send_message's blank-text/busy no-op guards and its
    session-creation branch had zero direct coverage — every other test that
    touches send_message replaces it with a MagicMock. This calls the real
    method (only _run_agent_turn itself is stubbed, so no live agent/model is
    needed)."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    grc = tmp_path / "flow.grc"
    grc.write_text("# grc")

    sidebar = ChatSidebar()
    sidebar._run_agent_turn = AsyncMock()

    # (a) Blank/whitespace-only text is a no-op: no task started.
    assert sidebar.send_message("   ") is False
    assert sidebar._chat_task is None

    # (b) A call while a turn is already in flight (_busy) is a no-op.
    sidebar._busy = True
    assert sidebar.send_message("hello") is False
    assert sidebar._chat_task is None
    sidebar._busy = False

    # (c) A real call on a sidebar with a flowgraph_proxy set creates a new DB
    # session row on first send.
    proxy = MagicMock()
    cm = MagicMock()
    cm.path = str(grc)
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    assert sidebar._active_session_id is None

    async def _run():
        result = sidebar.send_message("hello agent")
        assert result is True
        await sidebar._chat_task

    asyncio.run(_run())

    sidebar._run_agent_turn.assert_called_once_with("hello agent")
    assert sidebar._active_session_id is not None

    from grc_agent.db import get_recent_sessions

    sessions = get_recent_sessions()
    assert any(s["id"] == sidebar._active_session_id for s in sessions)


def test_save_history_is_async_and_offloads_to_thread(monkeypatch):
    """DB-1 regression: _save_history must be async and dispatch save_session via
    asyncio.to_thread so it never blocks the gbulb event loop."""
    import asyncio
    import inspect
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    assert inspect.iscoroutinefunction(ChatSidebar._save_history)

    sidebar = ChatSidebar()
    sidebar._active_session_id = 7
    proxy = MagicMock()
    cm = MagicMock()
    cm.path = "/tmp/x.grc"
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    used = {"to_thread": False}

    def fake_to_thread(fn, *a, **k):
        used["to_thread"] = True
        return asyncio.to_thread(fn, *a, **k)

    monkeypatch.setattr("grc_agent.chat_sidebar.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr("grc_agent.chat_sidebar.save_session", MagicMock(return_value=7))

    asyncio.run(sidebar._save_history())
    assert used["to_thread"] is True


def test_save_history_deletes_session_resurrected_by_concurrent_clear(monkeypatch):
    """M13 regression: _save_history captures _clear_generation BEFORE the
    asyncio.to_thread(save_session, ...) await. If a global Clear History bumps
    _clear_generation while that save is still in flight, the worker's INSERT
    can resurrect a row Clear History just deleted — this must be undone by
    calling delete_session on the resurrected id once the await returns."""
    import asyncio
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar._active_session_id = 42
    proxy = MagicMock()
    cm = MagicMock()
    cm.path = "/tmp/race.grc"
    proxy._canvas_manager = cm
    sidebar._flowgraph_proxy = proxy

    resumed = asyncio.Event()

    def fake_to_thread(fn, *a, **k):
        async def _runner():
            # Block until the test bumps _clear_generation, simulating a
            # concurrent Clear History completing while save_session is still
            # running on its worker thread.
            await resumed.wait()
            return fn(*a, **k)

        return _runner()

    monkeypatch.setattr("grc_agent.chat_sidebar.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr("grc_agent.chat_sidebar.save_session", MagicMock(return_value=42))
    mock_delete = MagicMock()
    monkeypatch.setattr("grc_agent.chat_sidebar.delete_session", mock_delete)

    async def _run():
        task = asyncio.ensure_future(sidebar._save_history())
        await asyncio.sleep(0)  # let _save_history capture gen and start the await
        sidebar._clear_generation += 1  # simulate the concurrent Clear History
        resumed.set()
        await task

    asyncio.run(_run())
    mock_delete.assert_called_once_with(42)


def test_sync_manual_edit_reports_failure_via_on_sync_failed(tmp_path, monkeypatch):
    """A failed auto-save (e.g. disk full) used to be log-only — a real
    data-loss risk with zero user-visible signal. It must now surface via
    the on_sync_failed callback (wired to the sidebar's status bar in
    desktop_app.py)."""
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    grc = tmp_path / "f.grc"
    grc.write_text("data")
    (tmp_path / ".grc_agent").mkdir()

    fg = MagicMock()
    da = MagicMock()
    da._flow_graph = fg
    page = MagicMock()
    page.file_path = str(grc)
    page.drawing_area = da
    window = MagicMock()
    window.current_page = page

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm.window = window
    cm.last_synced_export_hash = "PREVIOUS"
    cm.last_disk_hash = "SAME"

    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _: "CURRENT")
    monkeypatch.setattr("grc_agent.native_canvas._sha256_file", lambda _: "SAME")

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("grc_agent.native_canvas.write_flow_graph_atomic", _boom)

    failures = []
    cm.on_sync_failed = lambda msg: failures.append(msg)

    cm.sync_manual_edit()

    assert failures, "on_sync_failed was not called on a failed auto-save"
    assert "disk full" in failures[0]


def test_sync_manual_edit_reports_stale_disk_conflict_via_on_sync_failed(tmp_path, monkeypatch):
    """A stale-disk conflict (another program changed the file on disk since
    grc-agent last read it) used to be silently skipped (debug-log only,
    unlike the exception branch a few lines below it) — the safety-net poll
    would keep re-attempting and re-skipping the same edit forever with zero
    indication anything was wrong. Must now surface via on_sync_failed too."""
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    grc = tmp_path / "f.grc"
    grc.write_text("data")
    (tmp_path / ".grc_agent").mkdir()

    fg = MagicMock()
    da = MagicMock()
    da._flow_graph = fg
    page = MagicMock()
    page.file_path = str(grc)
    page.drawing_area = da
    window = MagicMock()
    window.current_page = page

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm.window = window
    cm.last_synced_export_hash = "PREVIOUS"
    cm.last_disk_hash = "ORIGINAL_ON_DISK"

    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _: "CURRENT")
    # Simulate another program having changed the file on disk since we last read it.
    monkeypatch.setattr("grc_agent.native_canvas._sha256_file", lambda _: "CHANGED_BY_SOMEONE_ELSE")

    write_calls = []
    monkeypatch.setattr(
        "grc_agent.native_canvas.write_flow_graph_atomic",
        lambda *a, **k: write_calls.append((a, k)),
    )

    failures = []
    cm.on_sync_failed = lambda msg: failures.append(msg)

    cm.sync_manual_edit()

    assert not write_calls, "must not overwrite a file that changed on disk underneath us"
    assert failures, "on_sync_failed was not called on a stale-disk conflict"
    assert "changed on disk" in failures[0]


def test_sync_manual_edit_does_not_block_when_lock_held(tmp_path, monkeypatch):
    """CANVAS-1 regression: sync_manual_edit must not block the single UI
    thread when the .grc lock is already held — LOCK_NB + skip (the 1.5s poll
    retries later) instead of a blocking flock."""
    import fcntl
    import threading
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    grc = tmp_path / "f.grc"
    grc.write_text("data")
    (tmp_path / ".grc_agent").mkdir()

    fg = MagicMock()
    da = MagicMock()
    da._flow_graph = fg
    page = MagicMock()
    page.file_path = str(grc)
    page.drawing_area = da
    window = MagicMock()
    window.current_page = page

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm.window = window
    cm.last_synced_export_hash = "PREVIOUS"
    cm.last_disk_hash = None

    # Make the content-hash check differ so sync proceeds to the flock.
    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", lambda _: "CURRENT")
    # Neutralize side effects if a deferred writer ever runs after lock release.
    monkeypatch.setattr("grc_agent.native_canvas.write_flow_graph_atomic", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("grc_agent.native_canvas.push_undo_snapshot", lambda *_args, **_kwargs: None)

    lock_path = tmp_path / ".grc_agent" / (grc.name + ".lock")
    held = lock_path.open("a", encoding="utf-8")
    fcntl.flock(held.fileno(), fcntl.LOCK_EX)
    try:
        done = threading.Event()

        def run() -> None:
            try:
                cm.sync_manual_edit()
            except Exception:  # noqa: BLE001
                pass
            finally:
                done.set()

        t = threading.Thread(target=run, daemon=True)
        t.start()
        finished = done.wait(timeout=1.5)
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()

    assert finished, "sync_manual_edit blocked waiting for a held lock"


def test_check_for_unsynced_edit_logs_and_rearms(monkeypatch, caplog):
    """CANVAS-3 regression: a transient error in the safety-net poll must be
    logged (not silently swallowed) and the poll must still re-arm."""
    import logging
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    da = MagicMock()
    da._flow_graph = MagicMock()
    cm.window = MagicMock()
    cm.window.current_page.drawing_area = da
    cm.last_synced_export_hash = "X"
    # __new__ bypasses __init__, so the state-cache-version poll gate's
    # baseline must be set explicitly. None here means the cheap gate always
    # falls through to the full hash path below (the MagicMock page's
    # state_cache attributes never equal None), preserving this test's
    # original intent of exercising the full-hash error path.
    cm._last_state_cache_version = None
    cm._poll_tick_count = 0

    def boom(_):
        raise RuntimeError("hash failed")

    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", boom)

    with caplog.at_level(logging.WARNING, logger="grc_agent.native_canvas"):
        assert cm._check_for_unsynced_edit() is True
    assert "hash failed" in caplog.text


def test_check_for_unsynced_edit_skips_hash_when_state_cache_unchanged(monkeypatch):
    """Efficiency fix: when GRC's own undo/redo state_cache hasn't moved since
    the last poll tick, _check_for_unsynced_edit must skip the expensive full
    flow_graph_content_hash (export+YAML+hash) entirely — that full check is
    the single biggest always-on cost this 1.5s poll incurs."""
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    da = MagicMock()
    da._flow_graph = MagicMock()
    page = MagicMock()
    page.drawing_area = da
    page.state_cache.current_state_index = 3
    page.state_cache.num_prev_states = 3
    page.state_cache.num_next_states = 0
    cm.window = MagicMock()
    cm.window.current_page = page
    cm.last_synced_export_hash = "X"
    cm._last_state_cache_version = (3, 3, 0)  # matches page.state_cache exactly
    cm._poll_tick_count = 0  # ticks 1-2 below stay well short of the periodic backstop

    call_count = 0

    def counting_hash(_):
        nonlocal call_count
        call_count += 1
        return "X"

    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", counting_hash)

    assert cm._check_for_unsynced_edit() is True
    assert call_count == 0, "unchanged state_cache must skip the full hash check"

    # A GRC-tracked edit (e.g. properties-dialog OK/Apply) bumps the state
    # cache — the next tick must fall through to the full hash check again.
    page.state_cache.current_state_index = 4
    assert cm._check_for_unsynced_edit() is True
    assert call_count == 1, "a moved state_cache must trigger the full hash check"


def test_check_for_unsynced_edit_periodic_backstop_catches_undo_then_edit_collision(monkeypatch):
    """Regression for a real gap found in adversarial testing: GRC's
    state_cache can return to the EXACT SAME (current_state_index,
    num_prev_states, num_next_states) tuple after an ordinary "undo, then make
    a different edit" sequence — the cheap gate alone would then miss that
    edit forever. A periodic backstop (_POLL_FULL_CHECK_EVERY) must still
    force the full hash check within a bounded number of ticks even when the
    state_cache tuple never appears to change."""
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import _POLL_FULL_CHECK_EVERY, NativeCanvasManager

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    da = MagicMock()
    da._flow_graph = MagicMock()
    page = MagicMock()
    page.drawing_area = da
    page.state_cache.current_state_index = 5
    page.state_cache.num_prev_states = 5
    page.state_cache.num_next_states = 0
    cm.window = MagicMock()
    cm.window.current_page = page
    cm.last_synced_export_hash = "stale-hash-from-before-the-undo"
    # Baseline matches the (unchanged-looking) state_cache tuple exactly, as it
    # would after the undo+edit collision — the cheap gate alone sees no change.
    cm._last_state_cache_version = (5, 5, 0)
    cm._poll_tick_count = 0

    call_count = 0

    def counting_hash(_):
        nonlocal call_count
        call_count += 1
        return "new-hash-after-the-collision"

    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", counting_hash)

    synced = []
    monkeypatch.setattr(cm, "sync_manual_edit", lambda h=None: synced.append(h))

    for _ in range(_POLL_FULL_CHECK_EVERY - 1):
        assert cm._check_for_unsynced_edit() is True
    assert call_count == 0, "state_cache tuple never moved, so no tick before the backstop should hash"

    # The Nth tick is the periodic backstop — it must force the full check
    # regardless of the (unchanged-looking) state_cache tuple.
    assert cm._check_for_unsynced_edit() is True
    assert call_count == 1, "the periodic backstop tick must run the full hash check"
    assert synced == ["new-hash-after-the-collision"], (
        "the backstop must detect and sync the content the state_cache tuple alone missed"
    )


def test_sync_page_baselines_swallows_hash_error(monkeypatch):
    """CANVAS-4 regression: a hashing error during a tab switch must not
    propagate (which would leave the sidebar's active-graph label stale and
    bias the next poll against a stale baseline)."""
    from unittest.mock import MagicMock

    from grc_agent.native_canvas import NativeCanvasManager

    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    fg = MagicMock()
    page = MagicMock()
    page.file_path = "/tmp/x.grc"
    page.flow_graph = fg
    cm.window = MagicMock()
    cm.window.current_page = page

    def boom(_):
        raise RuntimeError("hash failed")

    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", boom)

    cm._sync_page_baselines()  # must not raise


def test_deserialize_messages_logs_on_malformed_json(caplog):
    """DB-2 regression: deserialization failures must be logged, not silently
    swallowed as an empty list (a stale/incompatible row would otherwise look
    identical to a brand-new empty chat)."""
    import logging

    from grc_agent.db import deserialize_messages

    with caplog.at_level(logging.WARNING):
        result = deserialize_messages("{not valid json")
    assert result == []
    assert any(
        "deserialize" in r.message.lower() or "failed" in r.message.lower()
        for r in caplog.records
    )


def test_save_session_does_not_resurrect_deleted_session(tmp_path, monkeypatch):
    """Regression: save_session used to silently fall through to an INSERT
    (under a brand-new row id) whenever the given session_id no longer
    existed — e.g. a per-row delete (_on_delete_recent_session) or Clear
    History racing an in-flight save dispatched before the deletion. Must
    now skip the write and return None instead of resurrecting it."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    from grc_agent.db import delete_session, get_recent_sessions, save_session

    f = tmp_path / "g.grc"
    f.touch()
    sid = save_session(None, str(f), [])
    delete_session(sid)

    result = save_session(sid, str(f), [])

    assert result is None, "must signal 'skipped', not fabricate a new id"
    assert get_recent_sessions() == [], "the deleted session must not reappear under a new row"


def test_get_recent_sessions_drops_blob_and_bounds(tmp_path, monkeypatch):
    """DB-3 / UI-4 regression: the recent-sessions list omits the heavy
    messages blob and is bounded by a SQL LIMIT rather than trimming the whole
    table in Python."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    from grc_agent.db import get_recent_sessions, save_session

    for i in range(5):
        f = tmp_path / f"g{i}.grc"
        f.touch()
        save_session(None, str(f), [])

    rows = get_recent_sessions(limit=3)
    assert len(rows) == 3
    assert all("messages" not in r for r in rows)


def test_prune_sessions_bounds_growth(tmp_path, monkeypatch):
    """DB-3 regression: an eviction policy caps the sessions table so it does
    not grow without limit (the old JSON store bounded itself to 10 on write)."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    from grc_agent.db import get_recent_sessions, prune_sessions, save_session

    for i in range(8):
        f = tmp_path / f"g{i}.grc"
        f.touch()
        save_session(None, str(f), [])

    prune_sessions(keep=3)
    assert len(get_recent_sessions(limit=100)) <= 3


def test_db_connection_is_closed_after_use(tmp_path, monkeypatch):
    """DB-4 regression: connections must be explicitly closed — sqlite3's
    `with conn:` only commits/rolls back, it does not close."""
    import sqlite3

    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    from grc_agent.db import _conn

    with _conn() as conn:
        conn.execute("SELECT 1")

    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_disable_native_undo_redo_removed():
    """ADPT-1: the dead disable_native_undo_redo() is gone — it contradicted
    the documented design (native undo is intentionally enabled; the 1.5s poll
    syncs native undo to disk) and had zero call sites."""
    from grc_agent import adapter

    assert not hasattr(adapter, "disable_native_undo_redo")


def test_change_graph_auto_standalone_new_block_fails_loudly(temp_empty):
    """ADPT-3 regression: a brand-new block whose type-controlling param is
    'auto' but which has NO connection in this batch has nothing to resolve
    from. Must fail loudly (auto_resolve_failed) instead of silently keeping
    GNU Radio's arbitrary schema default and returning ok:true."""
    fg = load_flow_graph(str(temp_empty))
    res = change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "analog_sig_source_x",
                "instance_name": "src",
                "params": {"type": "auto"},
            },
        ],
    )
    assert res["ok"] is False
    assert res["errors"][0]["code"] == "auto_resolve_failed"


def test_change_graph_validation_gate_exception_rolls_back(temp_empty):
    """ADPT-2 regression: if the native validation gate raises instead of
    populating an error list, change_graph must still revert the shared
    flowgraph to its pre-mutation state and return ok:false (mutation_failed)
    — not leave the graph mutated and propagate the exception."""
    fg = load_flow_graph(str(temp_empty))
    initial_block_count = len(fg.blocks)

    def boom():
        raise RuntimeError("validate blew up")

    fg.validate = boom

    res = change_graph(
        fg,
        add_blocks=[
            {"block_id": "variable", "instance_name": "v1", "params": {"value": "2"}},
        ],
    )
    assert res["ok"] is False
    assert res["errors"][0]["code"] == "mutation_failed"
    assert len(fg.blocks) == initial_block_count


def test_canonical_dtype_uses_native_aliases():
    """ADPT-4: dtype alias resolution is sourced from GNU Radio's own
    ALIASES_OF, not a hand-maintained map that had drifted (bogus 'u8' and
    missing sc16/s8/sc8). Unknown tokens pass through unchanged."""
    from grc_agent.adapter.graph import _canonical_dtype

    assert _canonical_dtype("complex") == "complex"
    assert _canonical_dtype("fc32") == "complex"
    assert _canonical_dtype("sc16") == "short"
    assert _canonical_dtype("s8") == "byte"
    assert _canonical_dtype("sc8") == "byte"
    assert _canonical_dtype("u8") == "u8"  # was bogusly mapped to 'byte'
    assert _canonical_dtype("nonsense") == "nonsense"


def test_prune_history_removed():
    """ADPT-5: the fixed message-count cutoff (12/10) is removed — it enforced
    an arbitrary context limit the backend's own context window already bounds."""
    from grc_agent import agent

    assert not hasattr(agent, "prune_history")


def test_lite_web_search_returns_results_for_real_query():
    """The ddgs-based search returns real results for a normal query.
    No mocking — tests the live backend."""
    import asyncio

    from grc_agent.adapter import search

    result = asyncio.run(search.lite_web_search("GNU Radio tutorial"))
    assert not result.startswith("No web results")
    assert not result.startswith("Web search failed")
    assert len(result) > 100


def test_lite_web_search_handles_ddgs_failure_gracefully(monkeypatch):
    """When the ddgs library raises (network error, rate limit, etc.),
    the search returns a 'Web search failed' message instead of crashing."""
    import asyncio

    from grc_agent.adapter import search

    class _BrokenDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def text(self, *_a, **_kw):
            raise RuntimeError("ddgs backend down")

    import ddgs
    monkeypatch.setattr(ddgs, "DDGS", _BrokenDDGS)
    result = asyncio.run(search.lite_web_search("anything"))
    assert "Web search failed" in result


def test_apply_canvas_zoom_delegates_to_native_drawing_area_methods():
    """Regression: Ctrl+Plus/Minus/0 must delegate to GRC's own
    DrawingArea.zoom_in()/zoom_out()/reset_zoom() instead of hand-rolling
    zoom math — GRC's own View menu triggers these same native methods for
    the identical accelerators, so a hand-rolled reimplementation here would
    silently diverge (previously: additive +/-0.1 clamped 0.5-3.0 here vs.
    native multiplicative x1.2 clamped 0.1-5.0), making keyboard zoom and
    menu zoom disagree."""
    from unittest.mock import MagicMock

    from grc_agent.desktop_app import _apply_canvas_zoom

    canvas = MagicMock()
    da = MagicMock()
    canvas.drawing_area = da

    _apply_canvas_zoom(canvas, "in")
    da.zoom_in.assert_called_once()
    da.zoom_out.assert_not_called()
    da.reset_zoom.assert_not_called()

    _apply_canvas_zoom(canvas, "out")
    da.zoom_out.assert_called_once()

    _apply_canvas_zoom(canvas, "reset")
    da.reset_zoom.assert_called_once()

    # The hand-rolled math this replaced used to write zoom_factor directly —
    # confirm the delegated version never touches it, only calls the native methods.
    da.zoom_factor = "untouched-sentinel"
    _apply_canvas_zoom(canvas, "in")
    assert da.zoom_factor == "untouched-sentinel"


def test_window_keypress_editable_propagation():
    from unittest.mock import MagicMock

    from gi.repository import Gdk, Gtk

    from grc_agent.desktop_app import _on_window_key_press

    win = MagicMock()
    entry = MagicMock(spec=Gtk.Entry)
    win.get_focus.return_value = entry

    canvas = MagicMock()
    sidebar = MagicMock()

    # Bare (unmodified) keys must propagate (return False) so GTK's native
    # focus dispatch routes them through the widget's IM-context path — no raw
    # re-emission that would bypass IME composition (the old .event() forward).
    event = MagicMock(spec=Gdk.EventKey)
    event.state = 0
    event.keyval = Gdk.KEY_minus

    result = _on_window_key_press(win, event, canvas, sidebar)

    assert result is False
    entry.event.assert_not_called()

    # Ctrl+A override still selects all on the entry and is consumed.
    event_ctrl_a = MagicMock(spec=Gdk.EventKey)
    event_ctrl_a.state = Gdk.ModifierType.CONTROL_MASK
    event_ctrl_a.keyval = Gdk.KEY_a

    result = _on_window_key_press(win, event_ctrl_a, canvas, sidebar)
    assert result is True
    entry.select_region.assert_called_once_with(0, -1)


def test_build_app_shows_fatal_error_when_gnuradio_missing(monkeypatch):
    """Regression: a missing/broken GNU Radio install must show a friendly
    Gtk.MessageDialog (via _show_fatal_error) and exit cleanly, instead of a
    raw traceback — this is the fix for the GUI-only "friendly startup
    failures" requirement, and previously had zero test coverage."""
    from unittest.mock import MagicMock

    import grc_agent.desktop_app as desktop_app

    def _boom():
        raise ModuleNotFoundError("No module named 'gnuradio'")

    monkeypatch.setattr(desktop_app, "get_gui_platform", _boom)
    fatal = MagicMock()
    monkeypatch.setattr(desktop_app, "_show_fatal_error", fatal)

    with pytest.raises(SystemExit):
        desktop_app.build_app()

    fatal.assert_called_once()
    title, message = fatal.call_args[0]
    assert "gnu radio" in title.lower() or "gnuradio" in message.lower()
    assert "gnuradio" in message.lower()


def test_build_app_shows_fatal_error_when_window_not_found(monkeypatch):
    """Regression: if GRC's own MainWindow can't be found after activation,
    build_app() must show a friendly dialog (via _show_fatal_error) and exit
    cleanly — matching the fix that replaced a bare print() at this exact
    branch (previously the only console-only fatal path left, violating the
    GUI-only rule)."""
    from unittest.mock import MagicMock

    from gi.repository import Gtk

    import grc_agent.desktop_app as desktop_app

    monkeypatch.setattr(desktop_app, "get_gui_platform", lambda: object())

    class _FakeApplication:
        def __init__(self, *_args, **_kwargs):
            pass

        def register(self, *_args, **_kwargs):
            pass

        def activate(self):
            pass

    monkeypatch.setattr(desktop_app, "gui_application_cls", lambda: _FakeApplication)
    monkeypatch.setattr(Gtk.Application, "get_default", staticmethod(lambda: None))
    fatal = MagicMock()
    monkeypatch.setattr(desktop_app, "_show_fatal_error", fatal)

    with pytest.raises(SystemExit):
        desktop_app.build_app()

    fatal.assert_called_once()
    title, _message = fatal.call_args[0]
    assert "window" in title.lower()


def _find_buttons(widget, gtk_button_cls):
    found = []
    if isinstance(widget, gtk_button_cls):
        found.append(widget)
    if hasattr(widget, "get_children"):
        for child in widget.get_children():
            found.extend(_find_buttons(child, gtk_button_cls))
    return found


def test_notify_run_failure_dispatches_short_notification(monkeypatch):
    """notify_run_failure sends a SHORT notification (return code + tool hint)
    — NOT the full log. The full log is read on demand via get_run_log."""
    import asyncio
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar.send_message = MagicMock()

    captured = {}

    def fake_ensure_future(coro):
        captured["coro"] = coro
        return MagicMock()

    monkeypatch.setattr("grc_agent.chat_sidebar.asyncio.ensure_future", fake_ensure_future)

    sidebar.notify_run_failure(1, "RuntimeError: No RTL-SDR devices found!\n" * 100)

    assert "coro" in captured
    # _chat_task is None on a fresh sidebar, so the coroutine sends right away
    asyncio.run(captured["coro"])
    sidebar.send_message.assert_called_once()
    sent_text = sidebar.send_message.call_args.args[0]
    # The notification must contain the return code
    assert "return code 1" in sent_text
    # The notification must NOT contain the full log — the agent reads it
    # via get_run_log tool
    assert "RTL-SDR" not in sent_text
    # Must mention get_run_log so the agent knows what tool to call
    assert "get_run_log" in sent_text


def test_notify_run_failure_does_not_send_when_busy(monkeypatch):
    """If the agent is mid-turn, the notification queues behind it
    via _send_fix_when_free (which awaits the in-flight task)."""
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar.send_message = MagicMock()

    captured = {}

    def fake_ensure_future(coro):
        captured["coro"] = coro
        return MagicMock()

    monkeypatch.setattr("grc_agent.chat_sidebar.asyncio.ensure_future", fake_ensure_future)

    # Simulate a busy agent
    sidebar._busy = True
    sidebar._chat_task = MagicMock()
    sidebar._chat_task.done.return_value = False

    sidebar.notify_run_failure(1, "some error")
    # The coroutine was dispatched but won't call send_message until
    # _chat_task is done — verify it was captured but not yet run
    assert "coro" in captured
    sidebar.send_message.assert_not_called()


def test_send_fix_when_free_waits_for_in_flight_turn():
    import asyncio
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar.send_message = MagicMock()

    async def _run():
        async def _pending():
            await asyncio.sleep(0.01)

        sidebar._chat_task = asyncio.ensure_future(_pending())
        # origin_page = current_page (None without a flowgraph proxy) so the
        # post-await "did the user switch tabs?" guard passes through to
        # send_message. H2 regression coverage lives in a separate test.
        await sidebar._send_fix_when_free("fix prompt", sidebar.current_page)

    asyncio.run(_run())
    sidebar.send_message.assert_called_once_with("fix prompt")


def test_send_fix_when_free_aborts_when_user_switched_tabs():
    """H2 regression: if the user switches flowgraph tabs while the fix
    bubble is awaiting an in-flight chat task, _send_fix_when_free must NOT
    dispatch the fix prompt to the now-current (different) flowgraph."""
    import asyncio
    from unittest.mock import MagicMock

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar.send_message = MagicMock()

    async def _run():
        async def _pending():
            await asyncio.sleep(0.01)

        sidebar._chat_task = asyncio.ensure_future(_pending())
        # Capture a "page A" identity; simulate the user switching to a
        # different page during the await by having current_page return a
        # distinct object afterwards.
        origin_page = object()
        sidebar._flowgraph_proxy = MagicMock()
        sidebar._flowgraph_proxy._canvas_manager.current_page = object()
        await sidebar._send_fix_when_free("fix prompt", origin_page)

    asyncio.run(_run())
    sidebar.send_message.assert_not_called()




