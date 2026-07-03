"""Unit tests for the per-phase structure of ``dispatch_flat_change_graph_batch``.

Each phase is exercised in isolation against a ``ChangeGraphContext`` so the
public ``dispatch_flat_change_graph_batch`` keeps its wire-format contract
(``payload['ok']``, ``payload['errors']``) while the internals become a flat
list of single-responsibility methods.

These tests require the ``grc_native`` marker (GNU Radio installed at
runtime — ``apt install gnuradio``).  They load the canonical
``examples/dial_tone.grc`` fixture that the rest of the grc_native suite
uses, not a fabricated inline YAML, because GRC's native loader rejects
blank handwritten stubs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.grc_native

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.change_graph import ChangeGraphContext


GRC_FIXTURE = (
    Path(__file__).resolve().parents[1] / "examples" / "dial_tone.grc"
)


@pytest.fixture
def ctx_factory(tmp_path):
    """Build a context from a copy of the shared GRC fixture."""
    fixture = tmp_path / "dial_tone.grc"
    fixture.write_bytes(GRC_FIXTURE.read_bytes())
    session = FlowgraphSession()
    session.load(fixture)
    errors: list[dict[str, str]] = []
    ctx = ChangeGraphContext(
        agent=session,
        fg=session.flowgraph,
        errors=errors,
    )
    return session, ctx


def test_context_precomputes_add_blocks_list(ctx_factory):
    """``add_blocks_list`` is computed once; ``new_block_names`` is filled."""
    session, ctx = ctx_factory
    assert ctx.add_blocks_list == []
    assert ctx.new_block_names == set()
    raw = [{"block_id": "blocks_add_xx", "instance_name": "blk_x"}]
    populated = ChangeGraphContext(
        agent=session, fg=session.flowgraph, errors=[],
        raw_add_blocks=raw,
    )
    assert populated.add_blocks_list == raw
    assert "blk_x" in populated.new_block_names


def test_context_ops_applied_is_plain_int(ctx_factory):
    """``ops_applied`` is a plain int field — no list-reference hack."""
    _session, ctx = ctx_factory
    assert ctx.ops_applied == 0
    assert isinstance(ctx.ops_applied, int)
    ctx.ops_applied += 1
    assert ctx.ops_applied == 1
    assert isinstance(ctx.ops_applied, int)


def test_context_accumulates_errors(ctx_factory):
    """``errors`` is a list that phases append to in place."""
    _session, ctx = ctx_factory
    assert ctx.errors == []
    ctx.errors.append({"code": "test", "message": "x"})
    assert ctx.errors == [{"code": "test", "message": "x"}]


# --- Task 2: phase methods 1-3 (add_blocks / remove_blocks / update_params) ---


def test_phase_add_blocks_applies_one_entry(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_add_blocks

    session, ctx = ctx_factory
    ctx.add_blocks_list = [
        {"block_id": "analog_const_source_x", "instance_name": "dc",
         "params": {"const": "0.0", "type": "float"}}
    ]
    ctx.new_block_names = {"dc"}
    _phase_add_blocks(ctx)
    assert "dc" in [b.name for b in session.flowgraph.blocks]
    assert ctx.ops_applied == 1


def test_phase_add_blocks_records_duplicate_name_error(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_add_blocks

    session, ctx = ctx_factory
    ctx.add_blocks_list = [
        {"block_id": "analog_const_source_x", "instance_name": "dc"}
    ]
    ctx.new_block_names = {"dc"}
    _phase_add_blocks(ctx)
    assert ctx.ops_applied == 1
    ctx.errors.clear()
    _phase_add_blocks(ctx)
    assert any(e["code"] == "duplicate_block_name" for e in ctx.errors)
    assert ctx.ops_applied == 1  # second add did not increment


def test_phase_remove_blocks_rejects_connection_id(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_remove_blocks

    _session, ctx = ctx_factory
    ctx.remove_blocks_list = ["src:0->dst:0"]
    _phase_remove_blocks(ctx)
    assert any(
        e["code"] == "remove_block_failed"
        and "connection" in e["message"].lower()
        for e in ctx.errors
    )


def test_phase_update_params_missing_instance_name_records_error(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_update_params

    _session, ctx = ctx_factory
    ctx.update_params_list = [{"params": {"value": "1"}}]
    _phase_update_params(ctx)
    assert any(e["code"] == "invalid_update" for e in ctx.errors)


# --- Task 3: phase methods 4-5 (update_states / auto_resolve_types) ---


def test_phase_update_states_validates_presence(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_update_states

    _session, ctx = ctx_factory
    ctx.update_states_list = [
        {"instance_name": "samp_rate", "state": "disabled"}
    ]
    _phase_update_states(ctx)
    assert ctx.ops_applied == 1
    assert ctx.errors == []


def test_phase_update_states_missing_keys_records_error(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_update_states

    _session, ctx = ctx_factory
    ctx.update_states_list = [{"state": "disabled"}]  # no instance_name
    _phase_update_states(ctx)
    assert any(e["code"] == "invalid_state" for e in ctx.errors)


def test_phase_auto_resolve_types_no_op_when_type_already_set(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_auto_resolve_types

    _session, ctx = ctx_factory
    ctx.new_block_names = {"dc"}
    ctx.type_already_set = {"dc"}  # batch already set the type
    _phase_auto_resolve_types(ctx)
    assert ctx.errors == []


# --- Task 4: phase methods 6-7 (remove_connections / add_connections) ---


def test_phase_remove_connections_unparseable_records_error(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_remove_connections

    _session, ctx = ctx_factory
    ctx.remove_connections_list = ["garbage_no_arrow_here"]
    _phase_remove_connections(ctx)
    assert any(e["code"] == "invalid_connection" for e in ctx.errors)


def test_phase_remove_connections_missing_arrow_suggests_remove_blocks(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_remove_connections

    _session, ctx = ctx_factory
    ctx.remove_connections_list = ["my_block"]  # no "->"
    _phase_remove_connections(ctx)
    err = next(e for e in ctx.errors if e["code"] == "invalid_connection")
    assert "Did you mean to pass" in err["message"]


def test_phase_add_connections_unparseable_records_error(ctx_factory):
    from grc_agent.runtime.change_graph import _phase_add_connections

    _session, ctx = ctx_factory
    ctx.add_connections_list = ["garbage"]
    _phase_add_connections(ctx)
    assert any(e["code"] == "invalid_connection" for e in ctx.errors)
