"""Session snapshot/rollback for the ``change_graph`` batch engine.

``capture_session_state`` / ``restore_session_state`` use GRC-native
``export_data`` / ``import_data`` to snapshot and restore the live
flowgraph without a file reload, preserving unsaved dirty edits on
rollback. Used by ``change_graph._restore_snapshot`` for atomic rollback.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from grc_agent.flowgraph_session import FlowgraphSession

__all__ = [
    "SessionStateSnapshot",
    "capture_session_state",
    "restore_session_state",
]


# -- snapshot / rollback --


@dataclass(frozen=True)
class SessionStateSnapshot:
    raw_data: dict[str, Any] | None
    path: Any
    is_dirty: bool
    state_revision: int
    persisted_file_sha256: str | None


def capture_session_state(session: FlowgraphSession) -> SessionStateSnapshot:
    raw_data = session.flowgraph.export_data() if session.flowgraph is not None else None
    return SessionStateSnapshot(
        raw_data=copy.deepcopy(raw_data) if raw_data is not None else None,
        path=session.path,
        is_dirty=session.is_dirty,
        state_revision=session.state_revision,
        persisted_file_sha256=session.persisted_file_sha256,
    )


def restore_session_state(
    session: FlowgraphSession,
    snapshot: SessionStateSnapshot,
) -> FlowgraphSession:
    session.path = snapshot.path
    session.is_dirty = snapshot.is_dirty
    session.set_state_revision(snapshot.state_revision)
    session.set_persisted_sha256(snapshot.persisted_file_sha256)
    if snapshot.raw_data is not None:
        from grc_agent.grc_native_adapter import get_platform

        fg = get_platform().make_flow_graph()
        fg.import_data(snapshot.raw_data)
        fg.rewrite()
        session.flowgraph = fg
    else:
        session.flowgraph = None
    return session
