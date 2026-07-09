"""Round-trip tests for ``transaction.capture_session_state`` /
``restore_session_state``.

The capture path is exported by ``grc_native_adapter.export_data``; the
restore path constructs a brand-new ``FlowGraph`` via
``platform.make_flow_graph`` + ``import_data`` — by design, the restored
object is NOT identity-equal to the captured one. The tests below lock the
public contract: round-trip preserves block/connection/state counts (and
per-block state + param values) but the post-restore ``session.flowgraph``
is a new instance.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.grc_native_adapter import apply_mutation
from grc_agent.transaction import (
    SessionStateSnapshot,
    capture_session_state,
    restore_session_state,
)

FIXTURE = Path(__file__).resolve().parent / "data" / "dial_tone.grc"
pytestmark = pytest.mark.grc_native


def _fresh_session() -> FlowgraphSession:
    """Load a private copy of dial_tone.grc so back-to-back tests are isolated."""
    tmp = Path(tempfile.mkdtemp(prefix="tx_roundtrip_")) / "graph.grc"
    shutil.copy2(FIXTURE, tmp)
    session = FlowgraphSession()
    session.load(tmp)
    return session


def test_capture_returns_frozen_snapshot_with_raw_data():
    session = _fresh_session()
    snap = capture_session_state(session)
    assert isinstance(snap.raw_data, dict)
    assert "blocks" in snap.raw_data
    assert len(snap.raw_data["blocks"]) > 0


def test_capture_is_decoupled_from_mutation():
    """Mutating after capture must not affect the snapshot's raw_data."""
    session = _fresh_session()
    snap_before = capture_session_state(session)
    apply_mutation(
        session.flowgraph,
        "update_params",
        instance_name="samp_rate",
        params={"value": "96000"},
    )
    snap_after = capture_session_state(session)
    assert snap_before.raw_data != snap_after.raw_data


def test_capture_restores_path_dirty_revision_metadata():
    session = _fresh_session()
    session.is_dirty = True
    session.bump_revision()
    snap = capture_session_state(session)
    assert snap.is_dirty is True
    assert snap.state_revision == session.state_revision
    assert snap.path == session.path


def test_restore_round_trip_preserves_block_and_connection_counts():
    session = _fresh_session()
    blocks_before = len(session.flowgraph.blocks)
    conns_before = len(session.flowgraph.connections)
    snap = capture_session_state(session)
    apply_mutation(
        session.flowgraph,
        "add_block",
        block_id="analog_const_source_x",
        instance_name="dc_added",
        parameters={"const": "0.0"},
    )
    assert len(session.flowgraph.blocks) == blocks_before + 1
    restore_session_state(session, snap)
    assert len(session.flowgraph.blocks) == blocks_before
    assert len(session.flowgraph.connections) == conns_before


def test_restore_replaces_flow_graph_instance_intentionally():
    """Identity loss is intentional (export_data → import_data round-trip).

    The post-restore session.flowgraph MUST be a different Python object
    from the pre-restore one.  Block count matches the pre-mutation
    count (export_data excludes the implicit ``options`` top_block that
    ``flowgraph.blocks`` includes, so we compare against the live
    pre-mutation count, not the raw_data length).
    """
    session = _fresh_session()
    blocks_before = len(session.flowgraph.blocks)
    original_fg = session.flowgraph
    snap = capture_session_state(session)
    apply_mutation(
        session.flowgraph,
        "update_params",
        instance_name="samp_rate",
        params={"value": "96000"},
    )
    restore_session_state(session, snap)
    assert session.flowgraph is not original_fg
    assert len(session.flowgraph.blocks) == blocks_before


def test_restore_restores_dirty_revision_and_sha():
    session = _fresh_session()
    session.is_dirty = True
    session.bump_revision()
    snap = capture_session_state(session)
    session.is_dirty = False
    session.set_state_revision(0)
    restore_session_state(session, snap)
    assert session.is_dirty is True
    assert session.state_revision == snap.state_revision
    assert session.persisted_file_sha256 == snap.persisted_file_sha256


def test_capture_with_no_flowgraph_returns_none_raw_data():
    session = FlowgraphSession()
    snap = capture_session_state(session)
    assert snap.raw_data is None


def test_restore_with_none_raw_data_clears_flowgraph():
    session = _fresh_session()
    snap = capture_session_state(session)
    snap = SessionStateSnapshot(
        raw_data=None,
        path=snap.path,
        is_dirty=snap.is_dirty,
        state_revision=snap.state_revision,
        persisted_file_sha256=snap.persisted_file_sha256,
    )
    restore_session_state(session, snap)
    assert session.flowgraph is None
