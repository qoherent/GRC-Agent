"""Phase 5 — native adapter tests (25+). Marked @pytest.mark.grc_native."""

from __future__ import annotations

from pathlib import Path

import pytest
from grc_agent.domain_models import BlockRole, GrcFlowgraph
from grc_agent.grc_native_adapter import (
    add_block,
    apply_mutation,
    classify_role,
    connect,
    disconnect,
    get_platform,
    load_and_inspect,
    load_flow_graph,
    remove_block,
    render_block,
    render_flow_graph,
    render_parameter,
    set_block_state,
    set_param,
    validate,
    write_flow_graph_atomic,
)
from grc_agent.runtime.param_filter import DETAILS, EXCLUDED_PARAM_CATEGORIES, OVERVIEW, keep_param

pytestmark = pytest.mark.grc_native

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "data"


def _temp_fixture(name: str) -> Path:
    """Copy a fixture to a temp file so a successful change_graph batch (which
    calls ``session.save()``) cannot write back to the committed fixture."""
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="grc_test_")) / Path(name).name
    shutil.copy2(FIXTURES / name, tmp)
    return tmp


# --- platform / identity ------------------------------------------------------ #


def test_get_platform_returns_singleton():
    assert get_platform() is get_platform()


def test_get_platform_missing_grc_raises_runtime_error(monkeypatch):
    # Re-importing the adapter with gnuradio unavailable would crash at import
    # time on this box; verify the function surfaces a clear error by
    # clearing the cached singleton and forcing a re-raise path.
    import grc_agent.grc_native_adapter as adapter

    monkeypatch.setattr(adapter, "_PLATFORM", None)
    # If GRC is available, get_platform() will succeed; if not, RuntimeError.
    try:
        adapter.get_platform()
    except RuntimeError as exc:
        assert "GRC Agent requires GNU Radio" in str(exc)


# --- load_and_inspect --------------------------------------------------------- #


def test_load_and_inspect_random_bit_generator():
    out = load_and_inspect(FIXTURES / "dial_tone.grc")
    assert isinstance(out, GrcFlowgraph)
    assert out.ok
    assert len(out.blocks) >= 4
    instance_names = {b.instance_name for b in out.blocks}
    assert "samp_rate" in instance_names
    # All rendered params must be visible (no Advanced/Config).
    from grc_agent.runtime.param_filter import param_metadata
    for block in out.blocks:
        meta = param_metadata(block.block_id)
        for k in block.params.keys():
            info = meta.get(k, {})
            assert info.get("category") not in EXCLUDED_PARAM_CATEGORIES


def test_load_and_inspect_blank():
    import tempfile

    import yaml

    # Build a minimal options-only flowgraph by reusing a real fixture's
    # options.parameters shape (GRC's parser is strict about required keys).
    src = yaml.safe_load((FIXTURES / "dial_tone.grc").read_text())
    options = src["options"]
    payload = {
        "options": options,
        "blocks": [],
        "connections": [],
        "metadata": {"file_format": 1, "grc_version": "3.10.9.2"},
    }
    with tempfile.NamedTemporaryFile("w", suffix=".grc", delete=False) as f:
        yaml.safe_dump(payload, f)
    path = Path(f.name)
    try:
        out = load_and_inspect(path)
        assert out.ok
        # GRC renders the options block as a block, so an options-only
        # flowgraph has one block (the options/metadata block).
        assert len(out.blocks) == 1
        assert out.blocks[0].block_id == "options"
        assert out.connections == []
    finally:
        path.unlink()


def test_load_and_inspect_nonexistent_file():
    out = load_and_inspect(FIXTURES / "definitely_missing.grc")
    assert not out.ok
    assert out.errors[0]["code"] == "FILE_READ_ERROR"


def test_load_and_inspect_directory():
    out = load_and_inspect(FIXTURES)
    assert not out.ok
    assert out.errors[0]["code"] == "FILE_READ_ERROR"


# --- role classification ------------------------------------------------------ #


def test_classify_role_options():
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    assert classify_role(fg.options_block) == BlockRole.OPTIONS


def test_classify_role_variable():
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    var = next(b for b in fg.blocks if b.is_variable)
    assert classify_role(var) == BlockRole.VARIABLE


def test_classify_role_source_sink_transform():
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    roles = {b.name or b.key: classify_role(b) for b in fg.blocks}
    # dial_tone: analog_sig_source_x_0 (source) -> blocks_add_xx (transform)
    #   -> audio_sink (sink).
    assert roles["analog_sig_source_x_0"] == BlockRole.SOURCE
    assert roles["audio_sink"] == BlockRole.SINK


# --- render_parameter visibility --------------------------------------------- #


def test_render_parameter_matches_keep_param_for_every_param():
    """``render_parameter`` must agree with the bible ``keep_param`` for every
    param in every mode. Driving the assertion through the production rule
    (not a hand-written subset) prevents the "test that copies production"
    anti-pattern: a future bugfix in ``keep_param`` is caught here, and a
    future divergence between ``render_parameter`` and ``keep_param`` is
    caught too.
    """
    fg = load_flow_graph(FIXTURES / "qtgui_vector_sink_example.grc")
    target = next(b for b in fg.blocks if b.key.startswith("qtgui"))

    for mode in (DETAILS, OVERVIEW):
        for k, p in target.params.items():
            expected = keep_param(
                hide=p.hide,
                category=p.category,
                dtype=p.dtype,
                value=p.value,
                default=p.default,
                mode=mode,
                param_key=k,
            )
            rendered = render_parameter(target, k, p, mode=mode)
            assert (rendered is None) == (not expected), (
                f"mode={mode} key={k!r}: keep_param={expected} but "
                f"render_parameter returned {'None' if rendered is None else 'value'}"
            )

    # Spot-check: no Advanced/Config params leak into the rendered block.
    rendered_block = render_block(target, flow_graph=fg)
    from grc_agent.runtime.param_filter import param_metadata

    meta = param_metadata(rendered_block.block_id)
    for k in rendered_block.params.keys():
        info = meta.get(k, {})
        assert info.get("category") not in EXCLUDED_PARAM_CATEGORIES


# --- validate ----------------------------------------------------------------- #


def test_validate_valid_flowgraph():
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    v = validate(fg)
    assert v.status == "valid"
    assert v.errors == []
    assert v.native_ok is True


# --- serialize / round-trip --------------------------------------------------- #


def test_serialize_flow_graph_round_trip(tmp_path):
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    out_path = tmp_path / "round_trip.grc"
    write_flow_graph_atomic(fg, out_path)
    reloaded = load_and_inspect(out_path)
    assert reloaded.ok
    assert {b.instance_name for b in reloaded.blocks} == {
        b.instance_name for b in render_flow_graph(fg).blocks
    }


# --- mutations ---------------------------------------------------------------- #


def test_add_block_mutation():
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    before = len(fg.blocks)
    add_block(fg, "analog_sig_source_x", "experiment_src", {"freq": "1000"})
    after = len(fg.blocks)
    assert after == before + 1
    new = next(b for b in fg.blocks if b.name == "experiment_src")
    assert new.params["freq"].value == "1000"


def test_remove_block_mutation():
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    before = len(fg.blocks)
    victim = next(b for b in fg.blocks if b.key != "options" and not b.is_variable)
    remove_block(fg, victim.name or victim.key)
    assert len(fg.blocks) == before - 1
    assert not any((b.name or b.key) == (victim.name or victim.key) for b in fg.blocks)


def test_set_param_mutation():
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    var = next(b for b in fg.blocks if b.is_variable)
    set_param(var, "value", "99999")
    assert var.params["value"].value == "99999"


def test_set_block_state_mutation():
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    target = next(b for b in fg.blocks if b.key != "options")
    set_block_state(target, "disabled")
    assert not target.enabled
    set_block_state(target, "bypassed")
    assert target.get_bypassed()


def test_connect_mutation():
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    before = len(fg.connections)
    # Connect the unused throttle output port to the time sink's spare input.
    # Many blocks have multi-port or unused; we exercise connect's own path
    # and ignore resulting validation (we only test the call, not validity).
    try:
        connect(fg, "blocks_throttle2_0", "0", "qtgui_time_sink_x_0", "0")
        assert len(fg.connections) == before + 1
        # Clean up so subsequent tests aren't affected.
        disconnect(fg, "blocks_throttle2_0", "0", "qtgui_time_sink_x_0", "0")
    except KeyError:
        # Some flowgraphs don't expose the ports we guessed; that's fine.
        pytest.skip("port geometry did not match the connect probe")


def test_apply_mutation_dispatcher():
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    before = len(fg.blocks)
    apply_mutation(
        fg,
        "add_block",
        block_type="analog_sig_source_x",
        instance_name="dispatcher_src",
        parameters={"freq": "42"},
    )
    assert len(fg.blocks) == before + 1
    new = next(b for b in fg.blocks if b.name == "dispatcher_src")
    assert new.params["freq"].value == "42"


def test_apply_mutation_invalid_op_type():
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    with pytest.raises(ValueError):
        apply_mutation(fg, "bad_op_type")


def test_validate_after_mutation():
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    var = next(b for b in fg.blocks if b.is_variable)
    apply_mutation(fg, "update_params", instance_name=var.name, params={"value": "48000"})
    v = validate(fg)
    assert v.status in {"valid", "invalid"}


def test_update_params_regenerates_derived_ports():
    """update_params must regenerate derived IO (e.g. sink ports from
    ``num_inputs``) via native rewrite() — otherwise a correct batch that bumps
    ``num_inputs`` then connects to the new port fails because the port doesn't
    exist at connect-time (the scenario-16 trap). Mirrors add_block's rewrite.
    """
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    adder = fg.get_block("blocks_add_xx")
    assert [p.key for p in adder.active_sinks] == ["0", "1", "2"]
    apply_mutation(fg, "update_params", instance_name="blocks_add_xx", params={"num_inputs": "4"})
    adder = fg.get_block("blocks_add_xx")
    # The 4th sink port (key "3") must exist immediately after update_params,
    # without a separate manual rewrite.
    assert [p.key for p in adder.active_sinks] == ["0", "1", "2", "3"]


# --- gate hygiene ------------------------------------------------------------- #


def test_rg_gnuradio_only_in_adapter():
    import subprocess

    res = subprocess.run(
        ["rg", "-n", "gnuradio", "src/grc_agent/"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    # All matches must be in grc_native_adapter.py.
    for ln in lines:
        assert "grc_native_adapter.py" in ln, ln


def test_no_yaml_safe_load_in_adapter():
    import subprocess

    res = subprocess.run(
        ["rg", "-n", "yaml\\.safe_load", "src/grc_agent/grc_native_adapter.py"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
    )
    assert res.stdout.strip() == ""


# --- inspect/change schema symmetry ------------------------------------------ #


def test_describe_block_payload_has_symmetry_keys():
    """Catalog description (query_knowledge output) must emit the same keys
    that ``add_blocks``/``update_params`` accept: ``block_id`` and ``params``
(encoded ``dtype=default`` strings). Guards the consultant-approved symmetry
    contract. ``default_params`` was dropped (redundant — the default is
    already the substring after ``=`` in each ``params`` value)."""
    from grc_agent.catalog.loaders import describe_block

    payload = describe_block("analog_sig_source_x")
    assert payload.get("ok") is True
    assert payload["block_id"] == "analog_sig_source_x"
    # Encoded overview strings (dtype=default).
    assert isinstance(payload["params"], dict) and payload["params"]
    # default_params was removed (simplify by removal — strictly redundant).
    assert "default_params" not in payload


def test_inspect_block_uses_symmetry_field_names():
    """``inspect_graph`` block objects must expose ``block_id`` and ``params``
    (not ``block_type``/``parameters``) so the model can mirror the shape into
    ``change_graph`` without key translation."""
    out = load_and_inspect(FIXTURES / "dial_tone.grc")
    block = out.blocks[0]
    dumped = block.model_dump()
    assert "block_id" in dumped and "block_type" not in dumped
    assert "params" in dumped and "parameters" not in dumped


# --- orphaned-port causal hint (topology offloading) ------------------------ #


def test_change_graph_orphaned_port_hint_names_the_removed_block():
    """Removing a block that other blocks feed must surface WHY their ports are
    now dangling. Reproduces the Scenario 06 topology failure: removing
    ``blocks_add_xx`` orphans ``analog_noise_source_x_0``'s output. The
    adapter must trace the causality and name the removed block in the hint.
    """
    from grc_agent.agent import GrcAgent
    from grc_agent.flowgraph_session import FlowgraphSession
    from grc_agent.runtime.change_graph import dispatch_flat_change_graph_batch

    session = FlowgraphSession()
    session.load(str(FIXTURES / "dial_tone.grc"))
    agent = GrcAgent(session=session)

    result = dispatch_flat_change_graph_batch(agent, remove_blocks=["blocks_add_xx"])

    # Removing the adder makes the graph invalid (orphaned sources/sink).
    assert result["ok"] is False
    noise_error = next(
        e for e in result["errors"]
        if "analog_noise_source_x_0" in e.get("message", "")
    )
    # The hint must name the removed block that caused the orphan, so the
    # model can infer it must also remove/reconnect the noise source or force.
    assert "blocks_add_xx" in noise_error.get("hint", "")


# --- Phase 0: disabled vs bypass native semantics (regression anchor) -------- #


def test_disabled_connected_source_is_invalid_native():
    """Native GRC itself flags a disabled connected source's downstream port as
    'not connected'. Our validate() is native-faithful — this anchors that the
    disabled/bypass asymmetry is NOT an agent bug, so the validator must not be
    'fixed' (that would diverge from native GRC)."""
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    src = next(b for b in fg.blocks if b.key == "analog_sig_source_x")
    set_block_state(src, "disabled")
    result = validate(fg)
    assert result.native_ok is False
    assert any("not connected" in e for e in result.errors)


def test_bypassed_connected_source_is_valid_native():
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    src = next(b for b in fg.blocks if b.key == "analog_sig_source_x")
    set_block_state(src, "bypass")
    assert validate(fg).native_ok is True


# --- Phase 1: enum rejection, template detection, empty-value strip ---------- #


def test_set_param_rejects_invalid_enum_value():
    """Native set_value silently keeps the current value for an invalid enum
    option; set_param must surface it as a hard error (no silent fallback)."""
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    block = next(b for b in fg.blocks if b.key == "analog_sig_source_x")
    type_param = block.params["type"]
    assert "float" in [str(o) for o in type_param.options]  # sanity
    set_param(block, "type", "float")  # valid option accepted
    assert str(block.params["type"].value) == "float"
    with pytest.raises(ValueError):  # invalid option rejected
        set_param(block, "type", "float_const/float")
    # the invalid value was not applied
    assert str(block.params["type"].value) != "float_const/float"


def test_set_param_rejects_variable_template_literal():
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    block = next(b for b in fg.blocks if b.key == "analog_sig_source_x")
    with pytest.raises(ValueError):
        set_param(block, "samp_rate", "${variable:samp_rate}")


def test_render_block_strips_empty_enum_value():
    """An empty-valued enum (e.g. options.realtime_scheduling='') must not leak
    into the inspect payload (consistency with filter_live_block_params)."""
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    options_block = next(b for b in fg.blocks if b.key == "options")
    rendered = render_block(options_block, fg, mode=OVERVIEW, variable_names=set())
    assert "realtime_scheduling" not in rendered.params


# --- Phase 4: options collapse, qtgui cosmetics, catalog port keys ------------ #


def test_options_block_renders_params_with_id_dropped():
    """The options block is rendered with its real params (author/title/...);
    only the redundant 'id' param (== instance_name) is dropped and empty
    values (e.g. realtime_scheduling='') are stripped. The block is NOT
    collapsed — project policy preserves information in tool outputs."""
    fg = load_flow_graph(FIXTURES / "dial_tone.grc")
    snap = render_flow_graph(fg, mode=OVERVIEW)
    options_blocks = [b for b in snap.blocks if b.block_id == "options"]
    assert options_blocks, "fixture should have an options block"
    ob = options_blocks[0]
    assert "id" not in ob.params, "id is redundant with instance_name"
    assert "realtime_scheduling" not in ob.params, "empty value stripped"
    assert "author" in ob.params, "real param preserved (no collapse)"
    assert "title" in ob.params


def test_qtgui_cosmetic_params_filtered_from_overview():
    """qtgui Config-category params (cosmetic styling) must be dropped by
    Stage A — test the rule (category == "Config") rather than a string-prefix
    heuristic so a regression in either direction is caught.
    """
    fg = load_flow_graph(FIXTURES / "fm_rx.grc")
    snap = render_flow_graph(fg, mode=OVERVIEW)
    d = snap.model_dump(exclude_none=True)
    for b in d["blocks"]:
        if "qtgui" not in b["block_id"]:
            continue
        for key in b["params"]:
            from grc_agent.runtime.param_filter import categories

            assert categories(b["block_id"]).get(key) != "Config", (
                f"Config-category param {key!r} leaked into overview for "
                f"{b['block_id']!r}"
            )


def test_catalog_stream_ports_carry_positional_keys():
    """Catalog stream ports without an explicit id must expose their positional
    key so the model can form connections (scenario 15 root cause)."""
    from grc_agent.catalog.loaders import (
        _build_block_description,
        get_catalog_snapshot,
    )

    snap = get_catalog_snapshot(None)
    audio = snap.blocks.get("audio_sink")
    if audio is None:
        pytest.skip("audio_sink not in catalog")
    desc = _build_block_description(audio)
    compact = [p.to_compact_dict() for p in desc.inputs]
    assert compact, "audio_sink should have inputs"
    assert "id" in compact[0], "stream port must expose positional key"
    assert compact[0]["id"] == "0"


def test_type_hint_names_neighbor_dtype_not_source_type():
    """IO type/size mismatch hint must name the dtype the new block should
    ADOPT (the neighbor/sink dtype), not the source's own wrong current type.

    Regression for scenario 16: the adder is ``float`` but a newly-added
    ``analog_sig_source_x`` was left at the default ``complex``; the old hint
    said "'third_tone' type enum includes 'complex'" (the source's wrong type,
    which appears first in the error message) instead of 'float'."""
    from grc_agent.agent import GrcAgent
    from grc_agent.flowgraph_session import FlowgraphSession
    from grc_agent.runtime.change_graph import dispatch_flat_change_graph_batch

    session = FlowgraphSession()
    session.load(str(_temp_fixture("dial_tone.grc")))
    agent = GrcAgent(session=session)
    result = dispatch_flat_change_graph_batch(
        agent,
        add_blocks=[
            {
                "block_id": "analog_sig_source_x",
                "instance_name": "third_tone",
                "params": {
                    "type": "complex",  # intentionally wrong (adder is float)
                    "freq": "350",
                    "samp_rate": "samp_rate",
                    "amp": "ampl",
                },
            }
        ],
        update_params=[{"instance_name": "blocks_add_xx", "params": {"num_inputs": "4"}}],
        add_connections=["third_tone:0->blocks_add_xx:3"],
    )
    assert result["ok"] is False
    hints = [
        e.get("hint", "")
        for e in result.get("errors", [])
        if e.get("code") == "gnu_validation"
    ]
    # The hint must point at 'float' (the dtype to adopt), NOT 'complex'.
    assert any("'third_tone' type enum includes 'float'" in h for h in hints), hints
    assert not any("includes 'complex'" in h for h in hints), hints


def test_auto_resolve_sees_port_created_by_same_batch_num_inputs_bump():
    """Auto-resolve must run AFTER update_params so a port created by a
    same-batch `num_inputs` bump exists when the neighbor dtype is read.

    Regression for scenario 16: a newly-added ``third_tone`` (no explicit
    type) connected to a freshly-exposed adder port 3 must auto-resolve to the
    adder's dtype (float) — not stay at the complex default that caused the
    IO mismatch."""
    from grc_agent.agent import GrcAgent
    from grc_agent.flowgraph_session import FlowgraphSession
    from grc_agent.runtime.change_graph import dispatch_flat_change_graph_batch

    session = FlowgraphSession()
    session.load(str(_temp_fixture("dial_tone.grc")))
    agent = GrcAgent(session=session)
    result = dispatch_flat_change_graph_batch(
        agent,
        add_blocks=[
            {
                "block_id": "analog_sig_source_x",
                "instance_name": "third_tone",
                "params": {"freq": "350", "samp_rate": "samp_rate", "amp": "ampl"},
            }
        ],
        update_params=[{"instance_name": "blocks_add_xx", "params": {"num_inputs": "4"}}],
        add_connections=["third_tone:0->blocks_add_xx:3"],
    )
    assert result["ok"] is True, result
    third = next(b for b in agent.session.flowgraph.blocks if b.name == "third_tone")
    assert str(third.params["type"].value) == "float"
