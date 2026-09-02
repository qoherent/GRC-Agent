"""Unit tests for layout — split from the former test_unit.py god file.

Minimal set per the clustered test plan; shared fixtures/helpers live in conftest.py.
"""

from conftest import _DIAL_TONE_FLOW_BLOCKS

from grc_agent.adapter import change_graph, load_flow_graph
from grc_agent.adapter.layout import GRID_H, GRID_W, _compute_layout_model


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
    ranks = _compute_layout_model(fg, set(), []).ranks
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


def test_change_graph_disconnected_flow_components_get_own_row_bands(temp_empty):
    # Two independent chains (src_a->sink_a, src_b->sink_b) are separate
    # weakly-connected components. Each must get its own row band starting at
    # the left margin — not share columns in one vertical stack (the old
    # behavior, where wires from one chain threaded through the other's
    # blocks). Both sources land at x=0 (rank 0 of their own band), and the
    # second band starts below the first band's full height.
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
    # Each band starts at the left margin; rank 0 sits at x=0, rank 1 at GRID_W.
    assert coords["src_a"][0] == coords["src_b"][0] == 0.0
    assert coords["sink_a"][0] == coords["sink_b"][0] == GRID_W
    # Bands are separated: the whole of chain A sits above the whole of chain B.
    assert coords["src_a"][1] < coords["src_b"][1]
    assert coords["sink_a"][1] < coords["sink_b"][1]
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


def test_layout_model_crossing_minimizer_orders_by_upstream_barycenter():
    # rank0: s_a, s_c, plus an isolated s_b (no edges -> its own component).
    # rank1: "high" anchors to s_c, "low" anchors to s_a, "mid" anchors to
    # BOTH s_a and s_c. The alphabetical initial order + grandalf's barycenter
    # sweeps must put low (row 0) above mid (row 1) above high (row 2), which
    # is exactly the no-crossing order for s_a->low/mid and s_c->mid/high.
    from types import SimpleNamespace

    def blk(name):
        return SimpleNamespace(name=name)

    def conn(src, dst):
        return SimpleNamespace(source_block=blk(src), sink_block=blk(dst))

    fg = SimpleNamespace(
        blocks=[blk(n) for n in ["s_a", "s_b", "s_c", "high", "low", "mid"]],
        connections=[conn("s_a", "low"), conn("s_c", "high"), conn("s_a", "mid"), conn("s_c", "mid")],
    )

    model = _compute_layout_model(fg, set(), None)

    # s_b is disconnected -> its own component; the wired five share one.
    assert sorted(model.components, key=len) == [["s_b"], ["high", "low", "mid", "s_a", "s_c"]]
    wired = next(c for c in model.components if len(c) > 1)
    ordered = model.ordered_ranks[model.components.index(wired)]
    rank1 = ordered[1]
    assert rank1.index("low") < rank1.index("mid") < rank1.index("high")
    # Rows must also be consecutive (no gaps in the crossing-minimized stack).
    assert rank1 == sorted(rank1, key=rank1.index) and len(rank1) == 3


def test_change_graph_crossing_minimizer_removes_crossings(temp_empty):
    # Classic crossing setup: two sources feeding two merges in a crossed
    # pattern (s1->m2, s2->m1, s2->m2). Alphabetical order of rank 1
    # (m1 above m2) crosses s1->m2 over s2->m1; the barycenter sweep must put
    # m2 above m1, making every wire left-to-right with zero crossings.
    fg = load_flow_graph(str(temp_empty))
    res = change_graph(
        fg,
        add_blocks=[
            {"block_id": "blocks_null_source", "instance_name": "s1", "params": {"type": "float"}},
            {"block_id": "blocks_null_source", "instance_name": "s2", "params": {"type": "float"}},
            {"block_id": "blocks_add_xx", "instance_name": "m1", "params": {"type": "float"}},
            {"block_id": "blocks_add_xx", "instance_name": "m2", "params": {"type": "float"}},
        ],
        add_connections=["s1:0->m2:0", "s2:0->m1:0", "s2:0->m2:1"],
        force=True,
    )
    assert res["ok"] is True

    coords = {n: tuple(fg.get_block(n).states["coordinate"]) for n in ["s1", "s2", "m1", "m2"]}
    # Barycenter pulls m2 toward s1/s2's shared column position: m2 above m1.
    assert coords["m2"][1] < coords["m1"][1]
    assert coords["s1"][1] < coords["s2"][1]
    # No pair of edges crosses: sources share columns and sinks share columns,
    # so two edges cross iff their y-order on the source side is the reverse
    # of their y-order on the sink side.
    pairs = [("s1", "m2"), ("s2", "m1"), ("s2", "m2")]
    for i, (sa, da) in enumerate(pairs):
        for sb, db in pairs[i + 1 :]:
            if sa == sb or da == db:
                continue  # shared endpoint: the wires converge, never cross
            src_order = coords[sa][1] < coords[sb][1]
            dst_order = coords[da][1] < coords[db][1]
            assert src_order == dst_order, f"wires cross: {sa}->{da} vs {sb}->{db}"


def test_layout_deterministic_across_runs(temp_empty):
    # The same topology must produce byte-identical coordinates on every run
    # (grandalf's internal set iteration is identity-hash ordered; the layout
    # sorts components/layers alphabetically so the output never depends on
    # it). Two independent flows through change_graph -> identical positions.
    fg1 = load_flow_graph(str(temp_empty))
    fg2 = load_flow_graph(str(temp_empty))
    res = change_graph(
        fg1,
        add_blocks=[
            {"block_id": "blocks_null_source", "instance_name": "src_a", "params": {"type": "float"}},
            {"block_id": "blocks_null_sink", "instance_name": "sink_a", "params": {"type": "float"}},
            {"block_id": "blocks_null_source", "instance_name": "src_b", "params": {"type": "float"}},
            {"block_id": "blocks_null_sink", "instance_name": "sink_b", "params": {"type": "float"}},
        ],
        add_connections=["src_a:0->sink_a:0", "src_b:0->sink_b:0"],
        force=True,
    )
    assert res["ok"] is True
    same = change_graph(
        fg2,
        add_blocks=[
            {"block_id": "blocks_null_source", "instance_name": "src_a", "params": {"type": "float"}},
            {"block_id": "blocks_null_sink", "instance_name": "sink_a", "params": {"type": "float"}},
            {"block_id": "blocks_null_source", "instance_name": "src_b", "params": {"type": "float"}},
            {"block_id": "blocks_null_sink", "instance_name": "sink_b", "params": {"type": "float"}},
        ],
        add_connections=["src_a:0->sink_a:0", "src_b:0->sink_b:0"],
        force=True,
    )
    assert same["ok"] is True
    for n in ["src_a", "sink_a", "src_b", "sink_b"]:
        assert tuple(fg1.get_block(n).states["coordinate"]) == tuple(
            fg2.get_block(n).states["coordinate"]
        )


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
    from grc_agent.adapter.layout import BLOCK_FOOTPRINT_H

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


def test_skip_layer_connection_layout_does_not_crash_on_dummy_vertex(temp_empty):
    # Multi-rank / skip-layer edges (e.g. rank 0 to rank 2) create Grandalf
    # DummyVertex instances in intermediate layers. Layer ordering must handle
    # DummyVertex safely without crashing with AttributeError.
    fg = load_flow_graph(str(temp_empty))
    res = change_graph(
        fg,
        add_blocks=[
            {"block_id": "analog_sig_source_x", "instance_name": "src", "params": {"type": "float"}},
            {"block_id": "blocks_multiply_xx", "instance_name": "mix", "params": {"type": "float"}},
            {
                "block_id": "blocks_add_xx",
                "instance_name": "add",
                "params": {"type": "float", "num_inputs": "2"},
            },
        ],
        add_connections=["src:0->mix:0", "mix:0->add:0", "src:0->add:1"],
        force=True,
    )
    assert res["ok"] is True
    ranks = _compute_layout_model(fg, set(), []).ranks
    assert ranks["src"] == 0
    assert ranks["mix"] == 1
    assert ranks["add"] == 2
    coords = {
        name: tuple(fg.get_block(name).states["coordinate"]) for name in ("src", "mix", "add")
    }
    assert coords["src"][0] < coords["mix"][0] < coords["add"][0]
    values = list(coords.values())
    for i, a in enumerate(values):
        for b in values[i + 1 :]:
            assert not _rects_overlap(*a, *b)


def test_multi_branch_ask_receiver_layout_success(temp_empty):
    # Regression test for Session 95 scenario: multi-branch ASK modulator with
    # AWGN channel adder, noise source, down-mixer receiver, and visualization sinks.
    fg = load_flow_graph(str(temp_empty))
    res = change_graph(
        fg,
        add_blocks=[
            {"block_id": "analog_sig_source_x", "instance_name": "carrier", "params": {"type": "float"}},
            {"block_id": "blocks_vector_source_x", "instance_name": "data", "params": {"type": "float", "vector": "(0, 1, 0, 1)"}},
            {"block_id": "blocks_multiply_xx", "instance_name": "ask_mixer", "params": {"type": "float"}},
            {"block_id": "analog_noise_source_x", "instance_name": "noise_src", "params": {"type": "float"}},
            {"block_id": "blocks_add_xx", "instance_name": "channel_adder", "params": {"type": "float", "num_inputs": "2"}},
            {"block_id": "blocks_multiply_xx", "instance_name": "rx_mixer", "params": {"type": "float"}},
            {"block_id": "qtgui_time_sink_x", "instance_name": "time_sink", "params": {"type": "float"}},
            {"block_id": "qtgui_freq_sink_x", "instance_name": "freq_sink", "params": {"type": "float"}},
        ],
        add_connections=[
            "carrier:0->ask_mixer:0",
            "data:0->ask_mixer:1",
            "ask_mixer:0->channel_adder:0",
            "noise_src:0->channel_adder:1",
            "channel_adder:0->rx_mixer:0",
            "carrier:0->rx_mixer:1",
            "channel_adder:0->time_sink:0",
            "channel_adder:0->freq_sink:0",
        ],
        force=True,
    )
    assert res["ok"] is True
    ranks = _compute_layout_model(fg, set(), []).ranks
    assert ranks["carrier"] == 0
    assert ranks["data"] == 0
    assert ranks["noise_src"] == 0
    assert ranks["ask_mixer"] == 1
    assert ranks["channel_adder"] == 2
    assert ranks["rx_mixer"] == 3
