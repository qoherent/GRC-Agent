"""Shared retrieval schema types and payload helpers."""

from dataclasses import dataclass, field
from typing import Any, Literal

import networkx as nx

from .provenance import Provenance

SearchScope = Literal["catalog", "session"]

DEFAULT_RESULT_LIMIT = 5
MAX_RESULT_LIMIT = 25
VALID_SCOPES = frozenset({"catalog", "session"})
GRAPHIFY_NODE_FILE_TYPE = "document"
GRAPHIFY_EDGE_CONFIDENCE = "EXTRACTED"


@dataclass(frozen=True)
class PreparedSearchNode:
    """Precomputed normalized field text and term sets for one indexed node."""

    normalized_fields: dict[str, str]
    field_terms: dict[str, frozenset[str]]


@dataclass
class IndexedNode:
    """One indexed retrieval entity stored alongside the graph."""

    node_id: str
    node_type: str
    label: str
    source_scope: SearchScope
    provenance: Provenance
    search_fields: dict[str, str]
    block_id: str | None = None
    summary: str | None = None
    block_description: str | None = None
    field_summary: str | None = None
    adjacency_summary: str | None = None
    related_node_labels: list[str] = field(default_factory=list)

    def to_result(self, *, score: float, reason: str) -> dict[str, Any]:
        """Render this node into the public `search_grc` result shape."""
        result: dict[str, Any] = {
            "node_id": self.node_id,
            "label": self.label,
        }
        if self.block_id:
            result["block_id"] = self.block_id
        if self.summary:
            result["summary"] = self.summary
        return result


@dataclass
class RetrievalIndex:
    """One built retrieval graph plus its node records."""

    scope: SearchScope
    graph: nx.DiGraph
    node_records: dict[str, IndexedNode]
    prepared_records: dict[str, PreparedSearchNode] = field(default_factory=dict)
    token_index: dict[str, frozenset[str]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def build_success_payload(
    *,
    scope: SearchScope,
    query: str,
    results: list[dict[str, Any]],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Build the public success payload returned by `search_grc`."""
    payload: dict[str, Any] = {
        "ok": True,
        "scope": scope,
        "query": query,
        "results": results,
    }
    if warnings:
        payload["warnings"] = warnings
    return payload
