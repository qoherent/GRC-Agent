"""Merged query_knowledge tool — dispatches to search_blocks or ask_grc_docs."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent

ToolResult = dict[str, Any]


def query_knowledge(
    agent: "GrcAgent",
    query: str,
    domain: str,
    debug: bool = False,
) -> ToolResult:
    """Query GNU Radio knowledge — catalog (block IDs/params) or docs (concepts)."""
    started = time.monotonic()

    if domain not in {"catalog", "docs"}:
        return agent._tool_result(
            "query_knowledge",
            ok=False,
            message=f"Invalid domain '{domain}'. Use 'catalog' or 'docs'.",
            error_type="invalid_request",
        )

    if domain == "catalog":
        from grc_agent.runtime.wrappers.search_blocks import search_blocks as _search
        result = _search(agent, query=query, debug=debug)
    else:
        from grc_agent.runtime.wrappers.ask_grc_docs import ask_grc_docs as _docs
        result = _docs(agent, question=query, debug=debug)

    if isinstance(result, dict):
        result["domain"] = domain
        result["query_knowledge_time"] = round(time.monotonic() - started, 3)
    return result
