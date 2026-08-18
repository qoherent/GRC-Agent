"""Unit tests for adapter_graph — split from the former test_unit.py god file.

Minimal set per the clustered test plan; shared fixtures/helpers live in conftest.py.
"""

import json
import tempfile
from pathlib import Path

import pytest
from conftest import (
    _EPY_COMPLEX_IO_SOURCE,
    _EPY_FLOAT_INPUT_SOURCE,
    _EPY_FLOAT_IO_SOURCE,
    _add_epy_blocks,
)

from grc_agent.adapter import (
    change_graph,
    generate_flowgraph_py,
    inspect_graph,
    load_flow_graph,
    preview_flowgraph_py,
    set_param,
)
from grc_agent.agent import inspect_graph_func


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


def test_inspect_graph_target_formats(temp_dial_tone):
    fg = load_flow_graph(str(temp_dial_tone))
    full = inspect_graph(fg, targets=None)
    full_count = len(full["graph"]["blocks"])

    # All string/list wildcard and empty targets inspect full graph
    for target in ["all", "*", "", ["all"], ["*"], [""]]:
        res = inspect_graph(fg, targets=target)
        assert res["ok"] is True
        assert len(res["graph"]["blocks"]) == full_count

    # Single string target normalizes to single block list
    res_str = inspect_graph(fg, targets="samp_rate")
    assert res_str["ok"] is True
    assert len(res_str["graph"]["blocks"]) == 1
    assert res_str["graph"]["blocks"][0]["instance_name"] == "samp_rate"

    # A 2-name target list inspects exactly those blocks (no wildcard, no
    # full-graph fallback).
    res_scoped = inspect_graph(fg, targets=["samp_rate", "analog_sig_source_x_0"])
    assert res_scoped["ok"] is True
    assert len(res_scoped["graph"]["blocks"]) == 2


@pytest.mark.asyncio
async def test_inspect_graph_func_wrapper(temp_dial_tone):
    from unittest.mock import MagicMock

    fg = load_flow_graph(str(temp_dial_tone))
    ctx = MagicMock()
    ctx.deps = fg

    # Test passing a string "all"
    res_raw = await inspect_graph_func(ctx, targets="all")
    res = json.loads(res_raw)
    assert res["ok"] is True

    # Test passing a string "samp_rate"
    res_raw_scoped = await inspect_graph_func(ctx, targets="samp_rate")
    res_scoped = json.loads(res_raw_scoped)
    assert res_scoped["ok"] is True
    assert len(res_scoped["graph"]["blocks"]) == 1


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

    # Wiring in a follow-up call (the incremental editing strategy) lands the
    # same connection-string form in the inspected graph. A second sink is
    # legal (a source port may fan out; only one upstream per sink).
    res2 = change_graph(
        fg,
        add_blocks=[
            {"block_id": "blocks_null_sink", "instance_name": "sink2", "params": {"type": "float"}}
        ],
        add_connections=["sig:0->sink2:0"],
    )
    assert res2["ok"] is True
    conns = inspect_graph(fg)["graph"]["connections"]
    assert "sig:0->sink2:0" in conns


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

    # Raises once, on the mutated graph, then behaves. That models the real
    # failure — validation blowing up on the *content* the mutation produced —
    # and is the only stub that can work on GNU Radio >= 3.10.12, whose
    # `import_data` calls `validate()` itself (core/blocks/options.py's
    # `insert_grc_parameters`). An always-raising stub also poisons the
    # rollback, so the pre-mutation state could never be restored by
    # construction, and the test would assert something unachievable rather
    # than the contract it documents.
    real_validate = fg.validate
    calls = []

    def boom():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("validate blew up")
        return real_validate()

    fg.validate = boom

    res = change_graph(
        fg,
        add_blocks=[
            {"block_id": "variable", "instance_name": "v1", "params": {"value": "2"}},
        ],
    )
    assert res["ok"] is False
    assert res["errors"][0]["code"] == "mutation_failed"
    # The revert itself must have succeeded — no rollback_failed alongside it.
    assert not any(e["code"] == "rollback_failed" for e in res["errors"])
    assert len(fg.blocks) == initial_block_count


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
        update_params=[
            {"instance_name": "my_epy", "params": {"_source_code": _EPY_FLOAT_INPUT_SOURCE}}
        ],
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
        update_params=[
            {"instance_name": "my_epy", "params": {"_source_code": _EPY_FLOAT_INPUT_SOURCE}}
        ],
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
        update_params=[
            {"instance_name": "my_epy", "params": {"_source_code": _EPY_FLOAT_INPUT_SOURCE}}
        ],
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
    assert fg.get_option("run_options") == "prompt"
    output_dir = Path(temp_run_null_sink).parent / "run"
    file_path = generate_flowgraph_py(fg, output_dir)
    content = file_path.read_text()
    assert "Press Enter to quit" not in content
    # The 'run' override is transient — the flowgraph's configured option is
    # restored after generation.
    assert fg.get_option("run_options") == "prompt"


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


def test_preview_flowgraph_py_k_below_total_keeps_everything(temp_empty):
    fg = load_flow_graph(str(temp_empty))
    _add_epy_blocks(fg, 2)  # 2 epy_block files + 1 main script = 3 total
    result = preview_flowgraph_py(fg, k=5)
    assert len(result["files"]) == 3
    assert result["omitted_files"] == 0
