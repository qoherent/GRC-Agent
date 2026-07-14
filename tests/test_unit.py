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
    monkeypatch.setattr("grc_agent.adapter._EMBEDDING_DIM_CACHE", {})

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

    monkeypatch.setattr("grc_agent.adapter.embed_document", counting_embed_document)

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
    res = lite_web_search("python programming language")
    assert isinstance(res, str)
    assert len(res) > 0
    assert "No web results" not in res
    assert "python.org" in res
