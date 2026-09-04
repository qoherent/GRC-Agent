"""Unit tests for adapter_graph — split from the former test_unit.py god file.

Minimal set per the clustered test plan; shared fixtures/helpers live in conftest.py.
"""

import json
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
    inspect_graph,
    load_flow_graph,
    preview_flowgraph_py,
    set_param,
)
from grc_agent.adapter.graph import resolve_save_target, sanitize_id_stem
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
async def test_inspect_graph_func_raises_model_retry_on_unknown_target(temp_dial_tone):
    """One uniform rule: a domain-tool failure raises ModelRetry with the
    actionable payload, rather than returning ok=false as a successful result
    the model has to notice on its own. The retry text must carry the valid
    block names so the model can correct itself without another round trip."""
    from unittest.mock import MagicMock

    from pydantic_ai.exceptions import ModelRetry

    fg = load_flow_graph(str(temp_dial_tone))
    ctx = MagicMock()
    ctx.deps = fg

    with pytest.raises(ModelRetry) as exc:
        await inspect_graph_func(ctx, targets=["no_such_block"])
    msg = str(exc.value)
    assert "no_such_block" in msg
    assert "samp_rate" in msg, "the valid-block list must survive into the retry text"


@pytest.mark.asyncio
async def test_inspect_graph_func_wrapper(temp_dial_tone):
    from unittest.mock import MagicMock

    fg = load_flow_graph(str(temp_dial_tone))
    ctx = MagicMock()
    ctx.deps = fg

    # The magic "everything" target still works through the list form.
    res_raw = await inspect_graph_func(ctx, targets=["all"])
    res = json.loads(res_raw)
    assert res["ok"] is True

    # A single name is a one-element list: the tool no longer accepts a bare
    # string, so the schema has one array branch instead of three.
    res_raw_scoped = await inspect_graph_func(ctx, targets=["samp_rate"])
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
    calls_blown = []

    def boom():
        if any(getattr(b, "name", "") == "v1" for b in getattr(fg, "blocks", [])) and not calls_blown:
            calls_blown.append(1)
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
    # Preview must NOT override run_options — it shows the flowgraph's
    # actual configured generated output.
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


def test_render_port_exposes_vlen_when_vector():
    """A vector port (fft_vxx, vlen=1024) must show its vlen while scalar
    ports stay lean — the structural cause behind item-size mismatch errors
    (the intern's "8 vs 8192" buffer puzzle) is invisible without it."""
    from grc_agent.adapter import get_platform
    from grc_agent.adapter.graph import render_port

    fg = get_platform().make_flow_graph()
    fft = fg.new_block("fft_vxx")
    fg.rewrite()
    info = render_port(fft.active_sinks[0])
    assert info["vlen"] not in (None, 1, "1", "")

    scalar = fg.new_block("qtgui_freq_sink_x")
    fg.rewrite()
    info2 = render_port(scalar.active_sinks[0])
    assert "vlen" not in info2


def test_change_graph_pre_existing_errors_not_penalized(temp_dial_tone):
    """If a flowgraph is already invalid due to a pre-existing error, a subsequent
    edit that does not fix or worsen the error succeeds without rolling back."""
    fg = load_flow_graph(str(temp_dial_tone))
    # 1. Intentionally create an invalid state via force=True
    res_force = change_graph(
        fg,
        update_params=[{"instance_name": "samp_rate", "params": {"value": "undefined_var_xyz"}}],
        force=True,
    )
    assert res_force["ok"] is True

    # 2. Modify an unrelated block (e.g. analog_sig_source_x_0 freq) without force=True
    res_edit = change_graph(
        fg,
        update_params=[{"instance_name": "analog_sig_source_x_0", "params": {"freq": "440"}}],
        force=False,
    )
    assert res_edit["ok"] is True
    assert "pre_existing_errors" in res_edit
    assert any("undefined_var_xyz" in err for err in res_edit["pre_existing_errors"])

    # 3. Introduce a NEW invalid parameter -> must be rejected by validation gate
    res_new_err = change_graph(
        fg,
        update_params=[{"instance_name": "analog_sig_source_x_1", "params": {"freq": "bad_syntax_abc"}}],
        force=False,
    )
    assert res_new_err["ok"] is False
    assert res_new_err["error_type"] == "validation_failed"
    assert any("bad_syntax_abc" in e["message"] for e in res_new_err["errors"])


# ---------------------------------------------------------------------------
# resolve_save_target / sanitize_id_stem — the pure, display-free naming
# authority for flowgraph save targets (R3: derivation never clobbers; R5:
# no project directory -> actionable directive error).
# ---------------------------------------------------------------------------


def test_resolve_save_target_default_id_empty_dir(tmp_path):
    path, stem = resolve_save_target(tmp_path, "default", None)
    assert path == tmp_path / "untitled.grc"
    assert stem == "untitled"


def test_resolve_save_target_counters_avoid_clobbering(tmp_path):
    (tmp_path / "untitled.grc").touch()
    path1, stem1 = resolve_save_target(tmp_path, "default", None)
    assert path1 == tmp_path / "untitled(1).grc"
    assert stem1 == "untitled_1"  # pinned AE2: untitled(1).grc -> id untitled_1
    (tmp_path / "untitled(1).grc").touch()
    path2, stem2 = resolve_save_target(tmp_path, "default", None)
    assert path2 == tmp_path / "untitled(2).grc"
    assert stem2 == "untitled_2"


def test_resolve_save_target_counter_takes_smallest_free_slot(tmp_path):
    # untitled(1) was moved/deleted: the counter must take the smallest
    # untitled(<n>).grc that is not present, not append past the gap.
    (tmp_path / "untitled.grc").touch()
    (tmp_path / "untitled(2).grc").touch()
    path, _ = resolve_save_target(tmp_path, "default", None)
    assert path == tmp_path / "untitled(1).grc"


def test_resolve_save_target_non_default_id(tmp_path):
    path, stem = resolve_save_target(tmp_path, "receiver", None)
    assert path == tmp_path / "receiver.grc"
    assert stem == "receiver"
    # R3: derivation never clobbers — an existing same-stem file must not be
    # overwritten by a derived untitled-page target either.
    (tmp_path / "receiver.grc").touch()
    path2, stem2 = resolve_save_target(tmp_path, "receiver", None)
    assert path2 == tmp_path / "receiver(1).grc"
    assert stem2 == "receiver_1"


def test_resolve_save_target_titled_page_saves_in_place(tmp_path):
    existing = tmp_path / "my_radio.grc"
    existing.touch()
    (tmp_path / "untitled.grc").touch()  # would-be derived name must be ignored
    path, stem = resolve_save_target(tmp_path, "my_radio", existing)
    assert path == existing  # identical path returned, nothing derived
    assert stem is None  # no id rename for titled pages


def test_resolve_save_target_derives_without_writing(tmp_path):
    # Pure derivation: choosing a target must not create or modify any file.
    before = sorted(p.name for p in tmp_path.iterdir())
    resolve_save_target(tmp_path, "default", None)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


@pytest.mark.parametrize("project_dir", [None, ""])
def test_resolve_save_target_without_project_dir_raises_directive_error(project_dir):
    # R5: the same actionable directive the fs tools raise when no project
    # folder is configured (mirrors fs_tools._NO_ACTIVE_GRAPH_MSG).
    with pytest.raises(ValueError, match="Select a Project directory"):
        resolve_save_target(project_dir, "default", None)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("untitled(1)", "untitled_1"),  # pinned AE2: invalid chars collapse at edges
        ("receiver", "receiver"),  # valid id passes through unchanged
        ("samp_rate_0", "samp_rate_0"),  # valid id passes through unchanged
        ("_private_id", "_private_id"),  # edge underscores of a VALID id survive
        ("1st_order", "_1st_order"),  # leading digit prefixed
        ("my-flow", "my_flow"),  # invalid char -> underscore
        ("a((--b", "a_b"),  # runs of invalid chars collapse to one underscore
        ("untitled((2))", "untitled_2"),  # collapse + edge invalid chars dropped
    ],
)
def test_sanitize_id_stem(raw, expected):
    assert sanitize_id_stem(raw) == expected


def test_sanitize_id_stem_is_idempotent():
    for raw in ["untitled(1)", "receiver", "1st_order", "my-flow", "samp_rate_0", "_private_id"]:
        assert sanitize_id_stem(sanitize_id_stem(raw)) == sanitize_id_stem(raw)



def test_save_writes_one_backup_and_no_undo_stack(temp_dial_tone):
    """A committed change_graph leaves exactly two artifacts on disk: the
    updated target and one timestamped backup of the pre-write bytes.

    The undo stack that used to be pushed alongside every save was write-only
    -- nothing in the codebase ever read the numbered .grc files or the
    cursor it maintained -- so it is gone. The backups are NOT: they are
    plain .grc copies a person opens in a file manager, and they are the only
    pre-image of the flowgraph that survives an app restart.
    """
    from pathlib import Path

    target = Path(temp_dial_tone)
    before = target.read_bytes()

    fg = load_flow_graph(str(target))
    res = change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "blocks_throttle2",
                "instance_name": "backup_probe",
                "params": {"type": "float"},
            }
        ],
        force=True,
    )
    assert res["ok"] is True

    assert target.read_bytes() != before, "the save must have rewritten the target"

    backup_dir = target.parent / ".grc_agent" / "backups"
    backups = sorted(backup_dir.iterdir())
    assert len(backups) == 1, f"expected exactly one backup, got {[p.name for p in backups]}"
    assert backups[0].read_bytes() == before, "the backup must hold the pre-write bytes"

    undo_dir = target.parent / ".grc_agent" / (target.name + ".undo")
    assert not undo_dir.exists(), "the write-only undo stack must not be recreated"
    assert not list((target.parent / ".grc_agent").glob("**/cursor.json"))


def test_backup_pruning_bounds_the_directory(tmp_path):
    """_prune_old_backups keeps the newest MAX_BACKUPS_PER_DIR by name.

    Backup filenames lead with a UTC timestamp, so name order is age order.
    """
    from grc_agent.adapter.graph import MAX_BACKUPS_PER_DIR, _prune_old_backups

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    total = MAX_BACKUPS_PER_DIR + 7
    for i in range(total):
        (backup_dir / f"{i:05d}-deadbeef.grc").write_text("x", encoding="utf-8")

    _prune_old_backups(backup_dir)

    remaining = sorted(p.name for p in backup_dir.iterdir())
    assert len(remaining) == MAX_BACKUPS_PER_DIR
    # The oldest 7 went; the newest survived.
    assert remaining[0] == f"{total - MAX_BACKUPS_PER_DIR:05d}-deadbeef.grc"
    assert remaining[-1] == f"{total - 1:05d}-deadbeef.grc"


def test_inspect_graph_connection_order_is_deterministic(temp_dial_tone):
    """Two inspections of an unchanged graph must be byte-identical.

    GRC keeps connections in a set, so raw iteration order varies per call.
    A payload that reshuffles on every inspection defeats prompt caching and
    makes it impossible for the model to diff two inspections of the same
    graph to see what its own edit changed.
    """
    import json

    fg = load_flow_graph(str(temp_dial_tone))
    payloads = {json.dumps(inspect_graph(fg), sort_keys=True) for _ in range(5)}
    assert len(payloads) == 1, "inspect_graph must be stable across repeated calls"

    conns = inspect_graph(fg)["graph"]["connections"]
    assert conns == sorted(conns)


def test_tool_argument_bounds_live_in_the_schema():
    """Out-of-range and malformed arguments are rejected by validation.

    Previously query_knowledge silently clamped k into 1-20 and
    generate_python passed any k straight through, so a model asking for 500
    results got 20 and was never told. A malformed connection string reached
    the mutation engine and cost a domain retry. All three are now schema
    constraints the model can see and validation enforces before the tool
    body runs.
    """
    import pydantic

    from grc_agent.agent import grc_tools

    by_name = {t.name: t for t in grc_tools()}

    k_schema = by_name["query_knowledge"].function_schema.json_schema["properties"]["k"]
    assert k_schema["minimum"] == 1 and k_schema["maximum"] == 20
    gen_k = by_name["generate_python"].function_schema.json_schema["properties"]["k"]
    assert gen_k["minimum"] == 1 and gen_k["maximum"] == 20

    for tool, args in (
        ("query_knowledge", {"query": "x", "domain": "catalog", "k": 500}),
        ("query_knowledge", {"query": "x", "domain": "catalog", "k": 0}),
        ("generate_python", {"k": 500}),
    ):
        validator = by_name[tool].function_schema.validator
        with pytest.raises(pydantic.ValidationError):
            validator.validate_python(args)

    # change_graph's connection strings carry parse_conn's rule as a pattern.
    cg = by_name["change_graph"].function_schema
    conn_schema = cg.json_schema["properties"]["add_connections"]
    assert "pattern" in conn_schema["items"]
    for malformed in ("src_0:0-sink_0:0", "src_0->sink_0", "a:0->b:0->c:0", "a:0:1->b:0"):
        with pytest.raises(pydantic.ValidationError):
            cg.validator.validate_python({"reason": "r", "add_connections": [malformed]})
    # ...and a well-formed one still validates.
    cg.validator.validate_python({"reason": "r", "add_connections": ["src_0:0->sink_0:0"]})


def test_change_graph_list_arguments_carry_no_null_branch():
    """An empty batch means the same as an absent one, so the six list
    arguments are plain arrays rather than array-or-null unions."""
    from grc_agent.agent import grc_tools

    cg = [t for t in grc_tools() if t.name == "change_graph"][0]
    props = cg.function_schema.json_schema["properties"]
    for arg in (
        "add_blocks",
        "remove_blocks",
        "update_params",
        "update_states",
        "add_connections",
        "remove_connections",
    ):
        assert "anyOf" not in props[arg], f"{arg} still widens to a null branch"
        assert props[arg]["type"] == "array"


def test_inspect_graph_omits_zero_counters_and_empty_port_lists(temp_dial_tone):
    """Absence carries the same information as a zero, at no token cost.

    Emitting omitted_*_count: 0 and inputs: [] on every block was ~21% of the
    payload on this repo's own fixtures. A counter now appears only when
    something actually was omitted -- and inspect_graph's description states
    that convention, so a missing key is never ambiguous.
    """
    fg = load_flow_graph(str(temp_dial_tone))
    payload = inspect_graph(fg)
    blocks = payload["graph"]["blocks"]
    assert blocks

    for b in blocks:
        for key in ("omitted_params_count", "omitted_inputs_count", "omitted_outputs_count"):
            assert b.get(key, 1) != 0, f"{b['instance_name']} still emits a zero {key}"
        for key in ("inputs", "outputs"):
            assert b.get(key, ["x"]) != [], f"{b['instance_name']} still emits an empty {key}"

    # A block that genuinely hid parameters still reports the count, so the
    # honesty contract survives the trim.
    assert any(b.get("omitted_params_count", 0) > 0 for b in blocks), (
        "the fixture should hide advanced params on at least one block"
    )
    # ...and ports are still reported where they exist.
    assert any(b.get("inputs") for b in blocks)
    assert any(b.get("outputs") for b in blocks)


def test_tool_description_budget_and_omission_convention():
    """The model-visible surface stays bounded, and states its own conventions."""
    import json

    from grc_agent.agent import grc_tools

    tools = grc_tools()
    descriptions = sum(len(t.description or "") for t in tools)
    schemas = sum(
        len(json.dumps(t.function_schema.json_schema, separators=(",", ":"))) for t in tools
    )
    # Was 4,058 / 5,932 before the schema and description work.
    assert descriptions < 2200, f"description budget regressed to {descriptions}"
    assert schemas < 5932, f"schema bytes regressed to {schemas}"

    inspect_desc = [t for t in tools if t.name == "inspect_graph"][0].description or ""
    assert "omitted_" in inspect_desc, (
        "inspect_graph must state that a missing omission counter means nothing was hidden"
    )


def test_change_graph_reports_whether_it_persisted(temp_dial_tone):
    """ok:true on an unsaved page must not read as "written to disk"."""
    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(fg, update_params=[{"instance_name": "samp_rate", "params": {"value": "48000"}}])
    assert res["ok"] is True and res["persisted"] is True

    # An in-memory-only graph (no file path) mutates but persists nothing.
    from grc_agent.adapter.graph import get_platform

    memfg = get_platform().make_flow_graph()
    memfg.grc_file_path = ""
    res = change_graph(
        memfg,
        add_blocks=[{"block_id": "blocks_null_sink", "instance_name": "sink_0"}],
        force=True,
    )
    assert res["ok"] is True
    assert res["persisted"] is False, "an unwritten mutation must say so"


def test_rollback_reports_a_partial_restore(temp_dial_tone, monkeypatch):
    """import_data reports a partial restore by return value, not by raising.

    GNU Radio's own docstring: "any blocks or connections in error will be
    ignored", returning connection_error. Discarding that made a rollback
    that silently dropped wiring look identical to a clean one.
    """
    from grc_agent.adapter import graph as graph_mod

    fg = load_flow_graph(str(temp_dial_tone))
    initial = fg.export_data()

    def partial_import(self, data):  # noqa: ARG001
        return True  # GNU Radio's "connection_error" signal

    monkeypatch.setattr(type(fg), "import_data", partial_import, raising=False)
    err = graph_mod._revert_flow_graph(fg, initial)
    assert err is not None and "dropped" in err.lower()


def test_rollback_reports_a_disk_reload_substitution(temp_dial_tone, monkeypatch):
    """Falling back to the on-disk file is not a clean revert.

    The file is not initial_data: any unsaved manual canvas edit made before
    the call is destroyed by the reload, and reporting success hid that.
    """
    from grc_agent.adapter import graph as graph_mod

    fg = load_flow_graph(str(temp_dial_tone))
    initial = fg.export_data()

    calls = {"n": 0}

    def flaky_import(self, data):  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("in-memory restore blew up")
        return False

    monkeypatch.setattr(type(fg), "import_data", flaky_import, raising=False)
    err = graph_mod._revert_flow_graph(fg, initial)
    assert err is not None
    assert "reloaded from" in err and "unsaved manual edits" in err


def test_relayout_flag_is_false_when_the_layout_threw(temp_dial_tone, monkeypatch):
    """relayout was derived from the request, so a failed layout still
    claimed one happened and the canvas fitted a view to stale coordinates."""
    from grc_agent.adapter import graph as graph_mod

    def boom(*_a, **_k):
        raise RuntimeError("grandalf exploded")

    monkeypatch.setattr(graph_mod, "compute_full_layout", boom, raising=False)
    import grc_agent.adapter.layout as layout_mod

    monkeypatch.setattr(layout_mod, "compute_full_layout", boom, raising=False)

    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(
        fg,
        add_blocks=[{"block_id": "blocks_null_sink", "instance_name": "layout_probe"}],
        force=True,
    )
    assert res["ok"] is True
    assert res["relayout"] is False, "a layout that threw must not be reported as done"


def test_save_refuses_symlinks_and_hard_links(temp_dial_tone, tmp_path):
    """The two link guards on the save path had no test at all.

    A reordering that moved the symlink check after resolve() -- which the
    code comment explicitly warns against, since resolve() follows symlinks --
    would let an approved change_graph write through a planted link and escape
    the project directory, with a fully green suite.
    """
    import os

    real = Path(temp_dial_tone)

    # Symlink: refused, and the target is left untouched.
    link = real.parent / "alias.grc"
    os.symlink(real, link)
    fg = load_flow_graph(str(real))
    fg.grc_file_path = str(link)
    before = real.read_bytes()
    res = change_graph(
        fg,
        add_blocks=[{"block_id": "blocks_null_sink", "instance_name": "sym_probe"}],
        force=True,
    )
    assert res["ok"] is False and res["error_type"] == "save_failed"
    assert "symlink" in str(res["errors"]).lower()
    assert real.read_bytes() == before, "a refused save must not touch the target"

    # Hard link: refused for the same reason -- another name for these bytes.
    hard = tmp_path / "hard.grc"
    os.link(real, hard)
    assert hard.stat().st_nlink > 1
    fg2 = load_flow_graph(str(real))
    fg2.grc_file_path = str(hard)
    before2 = hard.read_bytes()
    res2 = change_graph(
        fg2,
        add_blocks=[{"block_id": "blocks_null_sink", "instance_name": "hard_probe"}],
        force=True,
    )
    assert res2["ok"] is False and res2["error_type"] == "save_failed"
    assert "hard-linked" in str(res2["errors"]).lower()
    assert hard.read_bytes() == before2


def test_nbsp_is_normalised_in_every_argument_family(temp_dial_tone):
    """One normalisation rule, applied to all five argument families.

    A non-breaking space is a common artefact in model-generated text.
    _sanitize_data turns U+00A0 into a plain space, and parse_conn strips the
    parts -- but it was only applied to add_blocks and update_params, so an
    NBSP in a connection string survived into the parser and surfaced as
    invalid_connection_format or connection_not_found instead.
    """
    from grc_agent.adapter.graph import _sanitize_data

    NBSP = "\u00a0"
    assert _sanitize_data(f"a{NBSP}b") == "a b", "the rule itself is NBSP -> space"

    fg = load_flow_graph(str(temp_dial_tone))
    res = change_graph(
        fg,
        add_blocks=[
            {
                "block_id": "blocks_null_sink",
                "instance_name": "nb_sink",
                "params": {"type": "float"},
            }
        ],
        force=True,
    )
    assert res["ok"] is True

    # A connection string carrying an NBSP either side of the arrow resolves.
    res = change_graph(
        fg,
        add_connections=[f"analog_sig_source_x_0:0{NBSP}->{NBSP}nb_sink:0"],
        force=True,
    )
    assert res["ok"] is True, res.get("errors")
    assert any(
        c.endswith("->nb_sink:0") for c in inspect_graph(fg)["graph"]["connections"]
    ), "the NBSP connection did not land"

    # ...and so does a removal naming the block with one.
    res = change_graph(fg, remove_connections=[f"analog_sig_source_x_0:0->nb_sink:0{NBSP}"], force=True)
    assert res["ok"] is True, res.get("errors")


def test_block_role_and_enum_use_gnu_radio_s_own_apis(temp_dial_tone):
    """classify_role identified the options block by hardcoded key string, and
    enum-ness was a dtype string comparison. Both have native equivalents that
    AGENTS.md section 4 names."""
    fg = load_flow_graph(str(temp_dial_tone))
    payload = inspect_graph(fg)
    roles = {b["instance_name"]: b["role"] for b in payload["graph"]["blocks"]}
    options_name = fg.options_block.name
    assert roles[options_name] == "options"

    from grc_agent.adapter.graph import _is_enum

    opts = fg.options_block
    for key, param in opts.params.items():
        native = param.is_enum() if callable(getattr(param, "is_enum", None)) else None
        if native is not None:
            assert _is_enum(param) == native, f"{key}: disagreed with Param.is_enum()"


def test_block_add_accepts_id_alias_and_numeric_parameters():
    """Models frequently emit 'id' instead of 'block_id' and numeric/boolean parameter values."""
    from grc_agent.agent import BlockAdd, ParamUpdate

    b = BlockAdd.model_validate({
        "id": "analog_random_source_x",
        "instance_name": "random_source",
        "params": {"type": "byte", "min": 0, "max": 3, "repeat": True},
        "label": "Ignored Label",
    })
    assert b.block_id == "analog_random_source_x"
    assert b.instance_name == "random_source"
    assert b.params == {"type": "byte", "min": 0, "max": 3, "repeat": True}

    dumped = b.model_dump(exclude_none=True)
    assert dumped["block_id"] == "analog_random_source_x"
    assert dumped["params"] == {"type": "byte", "min": 0, "max": 3, "repeat": True}

    p = ParamUpdate.model_validate({
        "instance_name": "random_source",
        "params": {"min": 10, "gain": 1.5, "enabled": False},
    })
    assert p.params == {"min": 10, "gain": 1.5, "enabled": False}


def test_change_graph_coerces_stringified_json_arguments():
    """Fast or smaller models serialize nested list arguments as JSON strings."""
    from typing import Annotated

    from pydantic import TypeAdapter

    from grc_agent.agent import BlockAdd, JsonCoercedSequence

    ta = TypeAdapter(Annotated[list[BlockAdd], JsonCoercedSequence])
    raw_str = (
        '[{"id": "analog_random_source_x", "instance_name": "src", "params": {"min": 0}}]'
    )
    items = ta.validate_python(raw_str)
    assert len(items) == 1
    assert items[0].block_id == "analog_random_source_x"
    assert items[0].params == {"min": 0}


def test_json_repair_capability_decodes_composite_parameters():
    """JsonRepairCapability unpacks JSON-stringified arrays and objects while preserving string fields."""
    import asyncio

    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import ToolDefinition

    from grc_agent.agent import JsonRepairCapability

    cap = JsonRepairCapability()
    tool_def = ToolDefinition(
        name="test_tool",
        description="test",
        parameters_json_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "items": {"type": "array"},
                "meta": {"type": "object"},
            },
        },
    )

    raw_args = json.dumps({
        "text": '{"not": "decoded"}',
        "items": json.dumps([1, 2, 3]),
        "meta": json.dumps({"key": "val"}),
    })

    call = ToolCallPart(tool_name="test_tool", args=raw_args)

    repaired = asyncio.run(
        cap.before_tool_validate(
            None,  # pyright: ignore[reportArgumentType]
            call=call,
            tool_def=tool_def,
            args=raw_args,
        )
    )

    assert repaired["text"] == '{"not": "decoded"}'
    assert repaired["items"] == [1, 2, 3]
    assert repaired["meta"] == {"key": "val"}


def test_agent_executes_change_graph_with_stringified_json_args():
    """Simulates real provider tool call with stringified nested JSON arrays in change_graph."""
    import asyncio
    import json
    from unittest.mock import MagicMock

    from pydantic_ai import Agent
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.tools import DeferredToolRequests

    from grc_agent.agent import GrcAgentResponse, grc_tools, json_repair_cap
    from grc_agent.chat.approvals import DeferredToolResults, ToolApproved

    mock_deps = MagicMock()
    mock_fg = MagicMock()
    mock_deps.flow_graph = mock_fg
    mock_deps.current_page.flow_graph = mock_fg

    mock_block = MagicMock()
    mock_block.name = "random_source"
    mock_block.params = {
        "id": MagicMock(),
        "type": MagicMock(),
        "min": MagicMock(),
        "max": MagicMock(),
        "num_samps": MagicMock(),
        "repeat": MagicMock(),
    }
    mock_fg.get_block.side_effect = KeyError("not found")
    mock_fg.new_block.return_value = mock_block
    mock_fg.is_valid.return_value = True

    call_count = 0

    def mock_model(messages, info):  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raw_args = json.dumps({
                "reason": "Add QPSK source",
                "add_blocks": json.dumps([
                    {
                        "id": "analog_random_source_x",
                        "label": "Random Source",
                        "instance_name": "random_source",
                        "params": {"type": "byte", "min": 0, "max": 3, "num_samps": 1000, "repeat": True},
                    }
                ]),
            })
            return ModelResponse(parts=[ToolCallPart(tool_name="change_graph", args=raw_args)])
        return ModelResponse(parts=[TextPart(content="Flowgraph edited successfully")])

    agent = Agent(
        FunctionModel(mock_model),
        deps_type=MagicMock,
        output_type=[GrcAgentResponse, str, DeferredToolRequests],
        tools=grc_tools(),
        capabilities=[json_repair_cap],
    )

    async def run_test():
        res1 = await agent.run("add blocks", deps=mock_deps)
        assert isinstance(res1.output, DeferredToolRequests)
        assert len(res1.output.approvals) == 1
        approval_call = res1.output.approvals[0]

        deferred_results = DeferredToolResults(approvals={approval_call.tool_call_id: ToolApproved()})
        res2 = await agent.run(
            None,
            deps=mock_deps,
            message_history=res1.all_messages(),
            deferred_tool_results=deferred_results,
        )
        assert res2.output == "Flowgraph edited successfully"

    asyncio.run(run_test())


def test_is_composite_schema_detection():
    from grc_agent.agent import _is_composite_schema

    assert _is_composite_schema({"type": "array"}) is True
    assert _is_composite_schema({"type": "object"}) is True
    assert _is_composite_schema({"items": {"type": "string"}}) is True
    assert _is_composite_schema({"$ref": "#/$defs/BlockAdd"}) is True
    # anyOf with array branch (e.g. list[str] | None)
    assert (
        _is_composite_schema(
            {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]}
        )
        is True
    )
    # oneOf with object branch
    assert _is_composite_schema({"oneOf": [{"type": "object"}, {"type": "null"}]}) is True
    # Primitive types
    assert _is_composite_schema({"type": "string"}) is False
    assert _is_composite_schema({"type": "integer"}) is False
    assert _is_composite_schema({"type": "boolean"}) is False
    assert _is_composite_schema({}) is False
    assert _is_composite_schema(None) is False


def test_coercion_helpers():
    from typing import Annotated, Any

    from pydantic import TypeAdapter

    from grc_agent.agent import (
        BlockAdd,
        ConnectionSpec,
        JsonCoercedMapping,
        JsonCoercedSequence,
        ParamUpdate,
        StateUpdate,
    )
    from grc_agent.agent_factory import coerce_plan_items

    # 1. JsonCoercedSequence
    ta_seq = TypeAdapter(Annotated[list[str], JsonCoercedSequence])
    assert ta_seq.validate_python("bare_item") == ["bare_item"]
    assert ta_seq.validate_python('["item1", "item2"]') == ["item1", "item2"]
    assert ta_seq.validate_python(["item1"]) == ["item1"]
    assert ta_seq.validate_python("") == []
    assert ta_seq.validate_python(None) == []

    # 2. JsonCoercedMapping
    ta_map = TypeAdapter(Annotated[dict[str, Any], JsonCoercedMapping])
    assert ta_map.validate_python('{"k": 1, "v": "abc"}') == {"k": 1, "v": "abc"}
    assert ta_map.validate_python({"k": 1}) == {"k": 1}
    assert ta_map.validate_python("") == {}

    # 3. BlockAdd, ParamUpdate, StateUpdate with aliases and stringified params
    b = BlockAdd.model_validate({
        "id": "blocks_null_sink",
        "name": "sink_0",
        "params": '{"bus_structure_sink": "default"}',
    })
    assert b.block_id == "blocks_null_sink"
    assert b.instance_name == "sink_0"
    assert b.params == {"bus_structure_sink": "default"}

    p = ParamUpdate.model_validate({
        "block_name": "source_0",
        "params": '{"freq": 1000}',
    })
    assert p.instance_name == "source_0"
    assert p.params == {"freq": 1000}

    s = StateUpdate.model_validate({
        "name": "source_0",
        "state": "disabled",
    })
    assert s.instance_name == "source_0"
    assert s.state == "disabled"

    # 4. ConnectionSpec coercion
    ta_conn = TypeAdapter(ConnectionSpec)
    assert ta_conn.validate_python("src:0->dst:0") == "src:0->dst:0"
    assert ta_conn.validate_python("  src:0  ->  dst:0  ") == "src:0->dst:0"
    assert ta_conn.validate_python({"src": "src_0", "dst": "dst_0"}) == "src_0:0->dst_0:0"
    assert (
        ta_conn.validate_python({"src": "src_0", "src_port": 0, "dst": "dst_0", "dst_port": 1})
        == "src_0:0->dst_0:1"
    )
    assert (
        ta_conn.validate_python({
            "source": "src_0",
            "source_port": "out",
            "destination": "dst_0",
            "destination_port": "in",
        })
        == "src_0:out->dst_0:in"
    )

    # 5. coerce_plan_items
    assert coerce_plan_items({"content": "Step 1"}) == [{"content": "Step 1"}]
    assert coerce_plan_items('{"content": "Step 1"}') == [{"content": "Step 1"}]
    assert coerce_plan_items('[{"content": "Step 1"}]') == [{"content": "Step 1"}]
    assert coerce_plan_items([{"step": "Step 1", "id": 1}]) == [{"content": "Step 1", "id": "1"}]



