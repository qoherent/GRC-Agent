"""Metadata for the flat model-facing change_graph batch surface."""

from __future__ import annotations

from functools import lru_cache

from grc_agent.runtime.tool_schemas import build_tool_schemas
from grc_agent.runtime.tool_surface import MVP_MODEL_TOOL_NAMES


@lru_cache(maxsize=1)
def change_graph_batch_fields() -> tuple[str, ...]:
    """Return editable batch fields declared by the change_graph schema."""

    schemas = build_tool_schemas(MVP_MODEL_TOOL_NAMES)
    change_graph_schema = next(
        schema
        for schema in schemas
        if schema.get("function", {}).get("name") == "change_graph"
    )
    properties = (
        change_graph_schema.get("function", {})
        .get("parameters", {})
        .get("properties", {})
    )
    return tuple(
        key
        for key in properties
        if key
        not in {
            "force",
            "debug",
        }
    )


def change_graph_operation_kinds() -> tuple[str, ...]:
    """Compatibility helper for callers migrating away from op kinds."""

    return change_graph_batch_fields()
