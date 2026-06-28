"""Tool schemas for the three MVP wrapper tools.

The MVP model surface is three flat batch tools:

- ``inspect_graph`` (read)
- ``query_knowledge`` (read — routes to ``search_blocks`` or ``ask_grc_docs``)
- ``change_graph`` (write — flat batch mutations)

Internal engines (e.g. ``search_blocks``, ``ask_grc_docs``) are NOT surfaced
to the model. They are called only by ``query_knowledge`` itself.
"""

from typing import Any


def _schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    schema = {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }
    if strict:
        schema["function"]["strict"] = True
    return schema


_MVP_SCHEMAS: tuple[dict[str, Any], ...] = (
    _schema(
        "inspect_graph",
        "Read-only inspection of the active graph. Returns topology, block instances, connections, parameter values, and validation status.",
        {
            "targets": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional block instance_names to inspect. Empty/omitted (or ['all']) returns the whole-graph overview; a non-empty list scopes the result to those blocks plus connections touching them.",
            },
        },
        required=[],
        strict=True,
    ),
    _schema(
        "query_knowledge",
        "Answer GNU Radio knowledge questions from two domains: catalog (block IDs, port names, parameter keys) or docs (GNU Radio documentation and concepts such as PMT, sample rate, stream tags, and 'how do I' questions).",
        {
            "query": {
                "type": "string",
                "description": "Block capability, block-id, or concept question.",
            },
            "domain": {
                "type": "string",
                "enum": ["catalog", "docs"],
                "description": "'catalog' for block types/params; 'docs' for concepts.",
            },
        },
        required=["query", "domain"],
        strict=True,
    ),
    _schema(
        "change_graph",
        "Apply a batch of structural graph edits. Can add/remove blocks, update parameters/states, and add/remove connections in a single transaction. At least one array must be provided.",
        {
            "add_blocks": {
                "type": "array",
                "description": "Add blocks with optional initial params/states using installed catalog block_ids.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "block_id": {
                            "type": "string",
                            "description": "Installed GNU Radio catalog block ID (e.g. 'analog_sig_source_x').",
                        },
                        "instance_name": {
                            "type": "string",
                            "description": "New unique graph instance name (e.g. 'my_source').",
                        },
                        "params": {
                            "type": "object",
                            "description": "Initial parameter values keyed by GNU parameter ID.",
                        },
                        "state": {
                            "type": "string",
                            "enum": ["enabled", "disabled", "bypass"],
                            "description": "Initial block state; defaults to 'enabled'.",
                        },
                    },
                    "required": ["block_id", "instance_name"],
                },
            },
            "remove_blocks": {
                "type": "array",
                "description": "Remove existing blocks from the graph by instance name.",
                "items": {"type": "string"},
            },
            "update_params": {
                "type": "array",
                "description": "Update parameters on existing blocks keyed by parameter ID.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "instance_name": {
                            "type": "string",
                            "description": "Target block instance name (e.g. 'my_source').",
                        },
                        "params": {
                            "type": "object",
                            "description": "Param updates keyed by exact GNU param_id.",
                        },
                    },
                    "required": ["instance_name", "params"],
                },
            },
            "update_states": {
                "type": "array",
                "description": "Modify target block enablement state.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "instance_name": {
                            "type": "string",
                            "description": "Target block instance name (e.g. 'my_source').",
                        },
                        "state": {
                            "type": "string",
                            "enum": ["enabled", "disabled", "bypass"],
                            "description": "New block state.",
                        },
                    },
                    "required": ["instance_name", "state"],
                },
            },
            "add_connections": {
                "type": "array",
                "description": "Connection strings to add, format 'src_block:port->dst_block:port' (e.g. 'sig_source:0->throttle:0').",
                "items": {"type": "string"},
            },
            "remove_connections": {
                "type": "array",
                "description": "Connection strings to remove, format 'src_block:port->dst_block:port' (e.g. 'sig_source:0->throttle:0').",
                "items": {"type": "string"},
            },
            "force": {
                "type": "boolean",
                "description": "When true, edits are committed even if validation fails (e.g. 'Port is not connected'). Default false.",
            },
        },
        required=[],
        strict=True,
    ),
)


def build_tool_schemas(
    tool_names: tuple[str, ...] | list[str] | set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return the MVP tool schemas, filtered to the requested names if any.

    Tool order matters — models prefer earlier tools. The MVP order is:
    inspect_graph, query_knowledge, change_graph.
    """
    if tool_names is None:
        return list(_MVP_SCHEMAS)
    requested = set(tool_names)
    return [s for s in _MVP_SCHEMAS if s["function"]["name"] in requested]


__all__ = ["build_tool_schemas"]
