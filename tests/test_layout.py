"""Unit tests for layout — split from the former test_unit.py god file.

Minimal set per the clustered test plan; shared fixtures/helpers live in conftest.py.
"""

from conftest import _DIAL_TONE_FLOW_BLOCKS

from grc_agent.adapter import (
    GRID_H,
    GRID_W,
    _compute_ranks,
    _order_flow_band,
    change_graph,
    load_flow_graph,
)


def _rects_overlap(ax: float, ay: float, bx: float, by: float) -> bool:
    """AABB collision check with spacing gap (local copy of the deleted
    layout._rects_overlap — the tests were its only caller, and the
    production layout assigns unique grid cells instead of searching).
    Grid math is identical: GRID_* = BLOCK_FOOTPRINT_* + BLOCK_SPACING."""
    return (
        ax < bx + GRID_W
        and ax + GRID_W > bx
        and ay < by + GRID_H
        and ay + GRID_H > by
    )


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


def test_change_graph_new_variable_lands_in_header_band_not_centroid(temp_dial_tone):
    # Direct regression test for the reported bug: a new variable block used
    # to land at the graph's bounding-box centroid (mid signal-flow), since
    # variables never appear in add_connections and so always had
    # neighbor_coords == []. It must now land in the header band, strictly
    # above every signal-flow block.
    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(
        fg,
        add_blocks=[
            {"block_id": "variable", "instance_name": "new_var", "params": {"value": "1.0"}}
        ],
    )
    assert res["ok"] is True
    new_y = fg.get_block("new_var").states["coordinate"][1]
    flow_min_y = min(fg.get_block(n).states["coordinate"][1] for n in _DIAL_TONE_FLOW_BLOCKS)
    assert new_y < flow_min_y


def test_change_graph_new_variable_alphabetically_packed_among_existing_variables(temp_dial_tone):
    # Existing variables sort: ampl, freq_350, freq_440, noise, samp_rate.
    # avg_level sorts between ampl and freq_350 -> header order becomes:
    # options, ampl, avg_level, freq_350, freq_440, noise, samp_rate (7
    # blocks, 6 cols/row -> 2 rows: the six variables pack into row 0 and
    # options stays pinned at (0, 12.0); the flow band needs only 4 columns).
    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(
        fg,
        add_blocks=[
            {"block_id": "variable", "instance_name": "avg_level", "params": {"value": "1.0"}}
        ],
    )
    assert res["ok"] is True

    def coord(name):
        return tuple(fg.get_block(name).states["coordinate"])

    assert coord("ampl") == (1 * GRID_W, 12.0)
    assert coord("avg_level") == (2 * GRID_W, 12.0)
    assert coord("freq_350") == (3 * GRID_W, 12.0)
    assert coord("freq_440") == (4 * GRID_W, 12.0)
    assert coord("noise") == (5 * GRID_W, 12.0)
    assert coord("samp_rate") == (0.0, 12.0 + GRID_H)


def test_change_graph_options_block_pinned_first_in_header_band(temp_dial_tone):
    # "aaa_first" sorts alphabetically before every existing variable, but
    # options must still occupy column 0 regardless.
    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(
        fg,
        add_blocks=[
            {"block_id": "variable", "instance_name": "aaa_first", "params": {"value": "1.0"}}
        ],
    )
    assert res["ok"] is True
    options_block = next(b for b in fg.blocks if b.key == "options")
    assert tuple(options_block.states["coordinate"]) == (0.0, 12.0)
    assert tuple(fg.get_block("aaa_first").states["coordinate"]) == (1 * GRID_W, 12.0)


def test_change_graph_full_relayout_preserves_flow_band_rank_order(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(
        fg,
        add_blocks=[
            {"block_id": "variable", "instance_name": "new_var", "params": {"value": "1.0"}}
        ],
    )
    assert res["ok"] is True

    def x(name):
        return fg.get_block(name).states["coordinate"][0]

    assert x("analog_sig_source_x_0") < x("blocks_add_xx") < x("audio_sink")
    assert x("blocks_add_xx") < x("lpf_0") < x("waterfall_sink_0")


def test_change_graph_header_band_wraps_when_many_variables(temp_empty):
    fg = load_flow_graph(str(temp_empty))
    count = 14
    res = change_graph(
        fg,
        add_blocks=[
            {"block_id": "variable", "instance_name": f"v_{i:02d}", "params": {"value": "1.0"}}
            for i in range(count)
        ],
    )
    assert res["ok"] is True
    coords = [tuple(fg.get_block(f"v_{i:02d}").states["coordinate"]) for i in range(count)]
    assert len(set(coords)) == count
    for i, a in enumerate(coords):
        for b in coords[i + 1 :]:
            assert not _rects_overlap(*a, *b)
    # 16 header blocks total (options + samp_rate + 14 new) at 6 cols/row -> 3 rows.
    all_header_ys = {
        fg.get_block(n).states["coordinate"][1]
        for n in ["samp_rate", *[f"v_{i:02d}" for i in range(count)]]
    }
    assert len(all_header_ys) == 3


def test_change_graph_disconnected_flow_components_share_rank_columns_without_overlap(temp_empty):
    fg = load_flow_graph(str(temp_empty))
    res = change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "blocks_null_source",
                "instance_name": "src_a",
                "params": {"type": "float"},
            },
            {
                "block_id": "blocks_null_sink",
                "instance_name": "sink_a",
                "params": {"type": "float"},
            },
            {
                "block_id": "blocks_null_source",
                "instance_name": "src_b",
                "params": {"type": "float"},
            },
            {
                "block_id": "blocks_null_sink",
                "instance_name": "sink_b",
                "params": {"type": "float"},
            },
        ],
        add_connections=["src_a:0->sink_a:0", "src_b:0->sink_b:0"],
        force=True,
    )
    assert res["ok"] is True
    coords = {
        n: tuple(fg.get_block(n).states["coordinate"])
        for n in ["src_a", "sink_a", "src_b", "sink_b"]
    }
    # Both sources are independent rank-0 components -> same column, different rows.
    assert coords["src_a"][0] == coords["src_b"][0]
    assert coords["src_a"][1] != coords["src_b"][1]
    values = list(coords.values())
    for i, a in enumerate(values):
        for b in values[i + 1 :]:
            assert not _rects_overlap(*a, *b)


def test_change_graph_remove_only_relays_out_post_removal_state(temp_dial_tone):
    # One uniform rule (approved rule change): any batch that changes
    # topology — including remove_blocks-only — re-ranks and relayouts, so
    # the layout always reflects the current topology. The old gate (relayout
    # only on non-empty add_blocks) left stale holes after removals.
    fg = load_flow_graph(str(temp_dial_tone))
    before = {
        b.name: tuple(b.states["coordinate"]) for b in fg.blocks
    }
    res = change_graph(fg, remove_blocks=["analog_noise_source_x_0"], force=True)
    assert res["ok"] is True
    after = {b.name: tuple(b.states["coordinate"]) for b in fg.blocks}
    # The removal changed the topology, so the surviving blocks must have
    # been re-ranked — at least one coordinate must have moved.
    moved = [n for n in after if before.get(n) is not None and after[n] != before[n]]
    assert moved, "remove-only batch must relayout the remaining blocks"
    # Grid guarantees still hold and the layout stays monotone with the
    # connection topology (sources left of sinks).
    values = list(after.values())
    for i, a in enumerate(values):
        for b in values[i + 1 :]:
            assert not _rects_overlap(*a, *b)
    for conn in fg.connections:
        src = tuple(fg.get_block(conn.source_block.name).states["coordinate"])
        dst = tuple(fg.get_block(conn.sink_block.name).states["coordinate"])
        assert src[0] < dst[0], f"flow not left-to-right: {conn}"


def test_change_graph_remove_and_add_in_same_batch_relays_out_post_removal_state(temp_dial_tone):
    # remove_blocks + add_blocks in the same call is exactly the scenario
    # most likely to expose a stale-coordinate bug: the relayout must use
    # the POST-removal set of blocks, not a snapshot that still includes the
    # just-removed one.
    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(
        fg,
        remove_blocks=["waterfall_sink_0"],
        add_blocks=[
            {"block_id": "variable", "instance_name": "new_var", "params": {"value": "1.0"}}
        ],
        force=True,
    )
    assert res["ok"] is True
    names = {b.name for b in fg.blocks}
    assert "waterfall_sink_0" not in names
    assert "new_var" in names

    coords = [
        tuple(b.states["coordinate"])
        for b in fg.blocks
        if isinstance(b.states.get("coordinate"), (list, tuple))
    ]
    assert len(coords) == len(names)  # every remaining block got a real coordinate
    for i, a in enumerate(coords):
        for b in coords[i + 1 :]:
            assert not _rects_overlap(*a, *b)


def test_change_graph_rejected_add_blocks_batch_does_not_leak_relayout(temp_dial_tone):
    # A batch that fails entirely (here: a duplicate instance name) rolls
    # back via flow_graph.import_data(initial_data) -- the full relayout
    # computed during Phase 3 must not survive that rollback. Captures every
    # coordinate before the call and asserts byte-identical after a failure,
    # the same invariant test_change_graph_remove_only_does_not_relayout_or_
    # move_anything checks for a remove-only call.
    fg = load_flow_graph(str(temp_dial_tone))
    before = {b.name: tuple(b.states["coordinate"]) for b in fg.blocks}
    res = change_graph(
        fg,
        add_blocks=[
            {"block_id": "variable", "instance_name": "audio_sink", "params": {"value": "1.0"}}
        ],
    )
    assert res["ok"] is False
    assert any(e.get("code") == "duplicate_block_name" for e in res.get("errors", []))
    after = {b.name: tuple(b.states["coordinate"]) for b in fg.blocks}
    assert after == before


def test_change_graph_flow_band_never_starts_above_a_wrapped_header_band(temp_empty):
    # Dedicated boundary assertion (not just incidental all-pairs overlap):
    # force the header band to wrap to 2 rows, add one flow-band block in
    # the SAME call, and assert its y sits at or below the real computed
    # header-band bottom edge -- not just "doesn't overlap by luck".
    fg = load_flow_graph(str(temp_empty))
    # temp_empty already has 2 header blocks (options, samp_rate); adding 5
    # more variables makes 7 -> ceil(7/6) = 2 header rows at _DEFAULT_HEADER_COLS=6.
    res = change_graph(
        fg,
        add_blocks=[
            *(
                {"block_id": "variable", "instance_name": f"v_{i}", "params": {"value": "1.0"}}
                for i in range(5)
            ),
            {
                "block_id": "blocks_null_sink",
                "instance_name": "flow_block",
                "params": {"type": "float"},
            },
        ],
        force=True,
    )
    assert res["ok"] is True
    options_name = next(b.name for b in fg.blocks if b.key == "options")
    header_names = [options_name, "samp_rate", *[f"v_{i}" for i in range(5)]]
    header_ys = {fg.get_block(n).states["coordinate"][1] for n in header_names}
    assert len(header_ys) == 2, f"expected the header band to wrap to 2 rows, got {header_ys}"

    header_bottom_edge = 12.0 + 2 * GRID_H
    flow_y = fg.get_block("flow_block").states["coordinate"][1]
    assert flow_y >= header_bottom_edge


def test_order_flow_band_barycenter_orders_by_upstream_position():
    from types import SimpleNamespace

    def blk(name):
        return SimpleNamespace(name=name)

    # rank0: three sources, unresolved (no predecessors) -> alphabetical: s_a, s_b, s_c -> rows 0,1,2.
    # rank1, given in an adversarial (non-barycenter) order: "high" anchors to
    # s_c (row 2), "low" anchors to s_a (row 0), "mid" anchors to BOTH s_a and
    # s_c (barycenter (0+2)/2 = 1) -> must land strictly between low and high.
    flow_blocks = [blk("s_a"), blk("s_b"), blk("s_c"), blk("high"), blk("low"), blk("mid")]
    ranks = {"s_a": 0, "s_b": 0, "s_c": 0, "high": 1, "low": 1, "mid": 1}
    predecessors = {"high": {"s_c"}, "low": {"s_a"}, "mid": {"s_a", "s_c"}}

    positions = _order_flow_band(flow_blocks, ranks, predecessors, y_origin=0.0)

    assert positions["high"][0] == positions["low"][0] == positions["mid"][0] == 1 * GRID_W
    assert positions["low"][1] == 0 * GRID_H
    assert positions["mid"][1] == 1 * GRID_H
    assert positions["high"][1] == 2 * GRID_H


def test_change_graph_add_block_no_overlap_with_existing(temp_dial_tone):
    # change_graph relays out the WHOLE graph on every add_blocks call (see
    # compute_full_layout), not just the new block — so "existing" blocks'
    # coordinates change too. Assert non-overlap across every block's
    # POST-call coordinate, not against a pre-call snapshot.
    fg = load_flow_graph(str(temp_dial_tone))
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
    coords = [
        tuple(b.states["coordinate"])
        for b in fg.blocks
        if isinstance(b.states.get("coordinate"), (list, tuple))
    ]
    for i, a in enumerate(coords):
        for b in coords[i + 1 :]:
            assert not _rects_overlap(*a, *b)


def test_change_graph_add_blocks_no_visual_overlap_for_busy_block(temp_empty):
    # Regression test for a live-reported bug: a param-heavy block (Signal
    # Source shows 6 visible rows: samp_rate/waveform/freq/amp/offset/phase)
    # rendered taller than the OLD BLOCK_FOOTPRINT_H=100 estimate, so a sink
    # placed exactly 100 below it visibly overlapped despite passing a
    # same-point collision check. Two invariants guard that: the footprint
    # constant stays above the empirically-observed tall-block height, and
    # the newly-placed pair satisfies the same AABB guarantee as the batch
    # tests below.
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

    # The placed pair itself must satisfy the AABB guarantee too.
    src = tuple(fg.get_block("busy_source").states["coordinate"])
    sink = tuple(fg.get_block("busy_sink").states["coordinate"])
    assert not _rects_overlap(*src, *sink)


def test_change_graph_add_blocks_batch_no_overlap_large(temp_empty):
    # Regression test: adding a large batch of blocks used to stack them all in
    # one endlessly-tall column (old column-layout) or place later ones on top
    # of earlier ones (pre-AABB-check). Under compute_full_layout, these 12
    # disconnected blocks_null_sink instances all share grandalf rank 0 (no
    # connections between them), so they land in one column, multiple rows —
    # still guaranteed unique and non-overlapping regardless of batch size.
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
    assert len(set(coords)) == count, "grid placement produced duplicate coordinates"

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


def test_change_graph_wire_only_call_re_ranks_unwired_adds(temp_empty):
    # The taught incremental strategy adds blocks unwired (force=True) and
    # wires them in a LATER call. The old gate (relayout only on non-empty
    # add_blocks) froze the unwired alphabetical stack in column 0 — sink
    # above source — and the wire-only call never healed it. The uniform
    # topology gate must re-rank on the wiring call so the final graph
    # reads left-to-right.
    fg = load_flow_graph(str(temp_empty))
    res = change_graph(
        fg,
        add_blocks=[
            {"block_id": "analog_sig_source_x", "instance_name": "src", "params": {"type": "float"}},
            {"block_id": "analog_quadrature_demod_cf", "instance_name": "demod"},
            {"block_id": "audio_sink", "instance_name": "snd"},
        ],
        force=True,
    )
    assert res["ok"] is True
    res = change_graph(
        fg,
        add_connections=["src:0->demod:0", "demod:0->snd:0"],
        force=True,
    )
    assert res["ok"] is True
    coords = {
        name: tuple(fg.get_block(name).states["coordinate"]) for name in ("src", "demod", "snd")
    }
    # The wiring call must have re-ranked the graph: flow reads left-to-right.
    assert coords["src"][0] < coords["demod"][0] < coords["snd"][0], coords
    # And no two blocks overlap.
    values = list(coords.values())
    for i, a in enumerate(values):
        for b in values[i + 1 :]:
            assert not _rects_overlap(*a, *b)
