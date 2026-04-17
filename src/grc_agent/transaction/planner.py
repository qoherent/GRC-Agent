"""Proposal helpers for transaction edits."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.validation import preflight_transaction


def propose_edit(
    session: FlowgraphSession,
    transaction: Any,
    catalog_root: str | Path | None = None,
) -> dict[str, Any]:
    """Run Phase 4 preflight validation and shape a Phase 5 edit proposal."""
    preflight = preflight_transaction(session, transaction, catalog_root)
    normalized_operations = preflight.get("normalized_operations", [])
    return {
        "ok": preflight["ok"],
        "message": (
            "Transaction passed preflight validation."
            if preflight["ok"]
            else "Transaction failed preflight validation."
        ),
        "commit_eligible": False,
        "planned_operations": copy.deepcopy(normalized_operations),
        "normalized_operations": copy.deepcopy(normalized_operations),
        "errors": copy.deepcopy(preflight["errors"]),
        "warnings": copy.deepcopy(preflight["warnings"]),
        "error_count": preflight["error_count"],
        "warning_count": preflight["warning_count"],
        "dirty": session.is_dirty,
        "state_revision": session.state_revision,
    }
