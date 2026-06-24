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
        "Apply a batch of structural graph edits. Can add/remove blocks, update parameters/states, and add/remove connections in a single transaction.",
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
                            "description": "Installed GNU Radio catalog block ID.",
                        },
                        "instance_name": {
                            "type": "string",
                            "description": "New unique graph instance name.",
                        },
                        "params": {
                            "type": "object",
                            "description": "Initial parameter values keyed by GNU parameter ID.",
                        },
                        "state": {
                            "type": "string",
                            "enum": ["enabled", "disabled", "bypass"],
                            "description": "Optional initial block state.",
                        },
                    },
                    "required": ["block_id", "instance_name"],
                },
            },
            "remove_blocks": {
                "type": "array",
                "description": "Remove/delete existing blocks from the graph.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "instance_name": {"type": "string"},
                    },
                },
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
                            "description": "Target block instance name.",
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
                            "description": "Target block instance name.",
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
                "description": "Add exact src/dst endpoints by instance_name and port.",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "src": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "block": {"type": "string"},
                                "port": {"type": ["integer", "string"]},
                            },
                            "required": ["block", "port"],
                        },
                        "dst": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "block": {"type": "string"},
                                "port": {"type": ["integer", "string"]},
                            },
                            "required": ["block", "port"],
                        },
                    },
                    "required": ["src", "dst"],
                },
            },
            "remove_connections": {
                "type": "array",
                "description": "Exact connection_id strings to remove.",
                "items": {"type": "string"},
            },
            "force": {
                "type": "boolean",
                "description": "Bypass final validation compilation check to force apply intermediate graph state.",
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
