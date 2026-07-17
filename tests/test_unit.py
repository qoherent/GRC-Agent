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
    query_catalog,
    query_docs,
    redo_flowgraph,
    set_param,
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
    conn.execute("CREATE TABLE catalog_chunks(block_id TEXT);")
    conn.execute("CREATE VIRTUAL TABLE catalog_idx USING vec0(embedding float[3]);")
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


def test_sidebar_session_tab_switching_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import save_session

    sidebar = ChatSidebar()

    # Prepare files and sessions
    f1 = tmp_path / "flow1.grc"
    f1.touch()
    f2 = tmp_path / "flow2.grc"
    f2.touch()

    sid1 = save_session(None, str(f1), [])
    sid2 = save_session(None, str(f2), [])

    # Set up mocks for current_page and flowgraph_proxy
    class DummyPage:
        def __init__(self, file_path):
            self.file_path = file_path

    page1 = DummyPage(str(f1))
    page2 = DummyPage(str(f2))

    proxy = object()
    sidebar._flowgraph_proxy = proxy

    # We patch current_page property on the sidebar dynamically
    current_page_val = page1
    monkeypatch.setattr(ChatSidebar, "current_page", property(lambda _self: current_page_val))

    # Simulating switching to page1 (flow1.grc)
    sidebar._active_session_id = None
    sidebar.sync_to_file(str(f1))
    assert sidebar._active_session_id == sid1
    assert page1._grc_agent_session_id == sid1

    # Simulate creating a new session for page1
    sidebar.clear_messages()
    assert sidebar._active_session_id is None
    assert page1._grc_agent_session_id is None

    # Simulate sending a message to start a new session (sets page1._grc_agent_session_id to new ID)
    new_sid1 = save_session(None, str(f1), [])
    page1._grc_agent_session_id = new_sid1
    sidebar.sync_to_file(str(f1))
    assert sidebar._active_session_id == new_sid1

    # Simulating switching to page2 (flow2.grc)
    current_page_val = page2
    sidebar.sync_to_file(str(f2))
    assert sidebar._active_session_id == sid2
    assert page2._grc_agent_session_id == sid2

    # Simulating switching back to page1 (flow1.grc)
    current_page_val = page1
    sidebar.sync_to_file(str(f1))
    # It should restore our new_sid1 (the custom session we started on page1)
    assert sidebar._active_session_id == new_sid1


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
    sidebar.set_status.assert_called_once_with("Chat history cleared.")


def test_clear_history_deletes_active_session(monkeypatch):
    """UI-2 regression: 'Clear History' must delete the persisted DB row, not
    just blank in-memory state (the dialog copy promises irreversible deletion)."""
    from unittest.mock import MagicMock

    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar._active_session_id = 42
    sidebar._render_history = MagicMock()

    deleted = []
    monkeypatch.setattr("grc_agent.chat_sidebar.delete_session", lambda sid: deleted.append(sid))

    mock_dialog = MagicMock()
    monkeypatch.setattr(Gtk, "MessageDialog", MagicMock(return_value=mock_dialog))

    sidebar._on_clear_history_clicked(None)

    handler = mock_dialog.connect.call_args.args[1]
    handler(mock_dialog, Gtk.ResponseType.YES)

    assert deleted == [42]
    assert sidebar._active_session_id is None


def test_clear_history_deletes_by_path(monkeypatch):
    from unittest.mock import MagicMock

    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()
    sidebar._active_session_id = 42
    sidebar._render_history = MagicMock()

    proxy = MagicMock()
    cm = MagicMock()
    proxy._canvas_manager = cm
    cm.path = "/path/to/my_flowgraph.grc"
    sidebar._flowgraph_proxy = proxy

    deleted_paths = []
    monkeypatch.setattr("grc_agent.chat_sidebar.delete_sessions_for_path", lambda path: deleted_paths.append(path))

    mock_dialog = MagicMock()
    monkeypatch.setattr(Gtk, "MessageDialog", MagicMock(return_value=mock_dialog))

    sidebar._on_clear_history_clicked(None)

    handler = mock_dialog.connect.call_args.args[1]
    handler(mock_dialog, Gtk.ResponseType.YES)

    assert deleted_paths == ["/path/to/my_flowgraph.grc"]
    assert sidebar._active_session_id is None


def test_clear_history_dialog_survives_gc_and_responds():
    """Regression: a non-blocking dialog shown via .show() must be anchored on
    self, otherwise PyGObject garbage-collects the toplevel once the
    constructing method returns and the 'response' signal never fires."""
    import gc
    from unittest.mock import MagicMock

    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

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


def test_sync_to_file_restores_session_for_path(tmp_path, monkeypatch):
    """UI-3 regression: opening a file must restore that file's own prior chat
    session instead of blanking it."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))

    from unittest.mock import MagicMock

    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from grc_agent.chat_sidebar import ChatSidebar
    from grc_agent.db import get_session_for_path, save_session

    f = tmp_path / "flow.grc"
    f.touch()
    save_session(None, str(f), [ModelRequest(parts=[UserPromptPart(content="hello")])])

    row = get_session_for_path(str(f))
    assert row is not None
    assert row["grc_file_path"] == str(f.resolve())

    sidebar = ChatSidebar()
    sidebar._render_history = MagicMock()
    sidebar.sync_to_file(str(f))

    assert sidebar._active_session_id == row["id"]
    assert len(sidebar._message_history) == 1


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

    def boom(_):
        raise RuntimeError("hash failed")

    monkeypatch.setattr("grc_agent.native_canvas.flow_graph_content_hash", boom)

    with caplog.at_level(logging.WARNING, logger="grc_agent.native_canvas"):
        assert cm._check_for_unsynced_edit() is True
    assert "hash failed" in caplog.text


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


def test_lite_web_search_logs_selector_drift(monkeypatch, caplog):
    """ADPT-8: a 200 response that parses zero result selectors must be logged
    (so selector drift is diagnosable, not masked as a plain 'no results'), and
    the empty/ mismatched parse must not be silently truncated."""
    import asyncio
    import logging

    import httpx

    from grc_agent.adapter import search

    class FakeResp:
        status_code = 200
        text = "<html><body>page loaded, no result-link anchors here</body></html>"

        def raise_for_status(self) -> None:
            pass

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url, **_kwargs):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *_args, **_kwargs: FakeClient())
    with caplog.at_level(logging.WARNING):
        result = asyncio.run(search.lite_web_search("anything"))
    assert "No web results" in result
    assert any(
        "drift" in r.message.lower() or "selector" in r.message.lower()
        for r in caplog.records
    )


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




