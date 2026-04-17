"""Snapshot helpers for atomic transaction apply/rollback."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from grc_agent.flowgraph_session import FlowgraphSession


@dataclass(frozen=True)
class SessionStateSnapshot:
    """One deep-copied FlowgraphSession state bundle."""

    state: dict[str, Any]


def capture_session_state(session: FlowgraphSession) -> SessionStateSnapshot:
    """Capture the full session state for candidate staging or rollback."""
    return SessionStateSnapshot(state=copy.deepcopy(session.__dict__))


def restore_session_state(
    session: FlowgraphSession,
    snapshot: SessionStateSnapshot,
) -> FlowgraphSession:
    """Replace one session's state with a previously captured snapshot."""
    session.__dict__.clear()
    session.__dict__.update(copy.deepcopy(snapshot.state))
    return session


def clone_session(session: FlowgraphSession) -> FlowgraphSession:
    """Clone the full loaded session for candidate transaction work."""
    clone = FlowgraphSession()
    return restore_session_state(clone, capture_session_state(session))


def commit_candidate_session(
    session: FlowgraphSession,
    candidate: FlowgraphSession,
) -> FlowgraphSession:
    """Swap a validated candidate session into the live session object."""
    return restore_session_state(session, capture_session_state(candidate))
