"""Tool schemas for MVP wrappers and internal runtime primitives."""

from typing import Any

from grc_agent.runtime.model_context import (
    MODEL_TOOL_NAMES_ORDERED,
    MVP_MODEL_TOOL_NAMES,
    PUBLIC_TOOL_NAMES,
)

__all__ = [
    "PUBLIC_TOOL_NAMES",
    "MVP_MODEL_TOOL_NAMES",
    "MODEL_TOOL_NAMES_ORDERED",
    "build_tool_schemas",
]


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


def _strip_debug_properties(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a model-facing copy without dev-only debug parameters."""
    copied = {
        **schema,
        "function": {
            **schema["function"],
            "parameters": {
                **schema["function"]["parameters"],
                "properties": dict(schema["function"]["parameters"]["properties"]),
            },
        },
    }
    copied["function"]["parameters"]["properties"].pop("debug", None)
    required = copied["function"]["parameters"].get("required")
    if isinstance(required, list):
        copied["function"]["parameters"]["required"] = [
            item for item in required if item != "debug"
        ]
    return copied


def build_tool_schemas(
    tool_names: tuple[str, ...] | list[str] | set[str] | None = None
) -> list[dict[str, Any]]:
    """Return fixed tool schemas for the requested runtime surface.

    Tool order matters — models prefer earlier tools.
    Model-backed chat must request the MVP wrapper names explicitly.
    """
    internal_schemas = [
        _schema(
            "new_grc",
            "Create a new empty GRC flowgraph session.",
            {
                "profile": {
                    "type": "string",
                    "description": "Creation profile. Only 'minimal' (bare empty skeleton) is supported.",
                },
                "graph_id": {
                    "type": "string",
                    "description": "Optional identifier for the new flowgraph. Auto-generated if omitted.",
                },
            },
        ),
        _schema(
            "load_grc",
            "Load a GNU Radio Companion .grc file into the active session.",
            {
                "file_path": {
                    "type": "string",
                    "description": "Path to the .grc file to load.",
                }
            },
            required=["file_path"],
        ),
        _schema(
            "summarize_graph",
            "Return a summary of the loaded GNU Radio graph: blocks, connections, variables, and current state.",
            {
                "max_blocks": {
                    "type": "integer",
                    "description": "Optional maximum number of blocks to preview.",
                }
            },
        ),
        _schema(
            "get_grc_context",
            "Show the local wiring around one loaded session block or variable.",
            {
                "node_id": {
                    "type": "string",
                    "description": "Loaded session block instance name.",
                },
                "hops": {
                    "type": "integer",
                    "description": "Optional neighborhood depth.",
                },
                "max_nodes": {
                    "type": "integer",
                    "description": "Optional maximum number of nodes to include.",
                },
            },
            required=["node_id"],
        ),
        _schema(
            "describe_block",
            "Return the GNU catalog description for one block ID.",
            {
                "block_id": {
                    "type": "string",
                    "description": "GNU Radio block id to describe.",
                }
            },
            required=["block_id"],
        ),
        _schema(
            "suggest_compatible_insertions",
            "Return catalog-backed block candidates compatible with an existing connection.",
            {
                "connection_id": {
                    "type": "string",
                    "description": "Connection identifier in form 'src_block:src_port->dst_block:dst_port'.",
                },
                "k": {
                    "type": "integer",
                    "description": "Maximum number of candidates to return (default 5).",
                },
            },
            required=["connection_id"],
        ),
        _schema(
            "insert_block_on_connection",
            "Insert one catalog block into an existing stream connection.",
            {
                "connection_id": {
                    "type": "string",
                    "description": "Connection identifier in form 'src_block:src_port->dst_block:dst_port'.",
                },
                "block_type": {
                    "type": "string",
                    "description": "GNU Radio block type from the catalog (e.g., `blocks_throttle2`, `blocks_head`).",
                },
                "instance_name": {
                    "type": "string",
                    "description": "Unique name for the new block instance.",
                },
                "params": {
                    "type": "object",
                    "description": "Parameter overrides. Missing parameters are filled from catalog defaults.",
                },
            },
            required=["connection_id", "block_type", "instance_name"],
        ),
        _schema(
            "auto_insert_block",
            "Search, score, and commit one compatible block insertion into a stream connection.",
            {
                "goal": {
                    "type": "string",
                    "description": "Natural-language insertion goal. Examples: 'insert a head block', 'add a filter', 'insert a compatible block into the main path'.",
                },
                "preferred_block_type": {
                    "type": "string",
                    "description": "Optional literal block type to prefer if mentioned by user or known. Example: 'blocks_head'.",
                },
                "target_hint": {
                    "type": "string",
                    "description": "Optional hint about which connection to target. Example: 'main path', 'between source and sink'.",
                },
                "max_candidates": {
                    "type": "integer",
                    "description": "Maximum number of candidate insertion attempts. Default 10.",
                },
            },
            required=["goal"],
        ),
        _schema(
            "remove_connection",
            "Remove one existing connection by connection_id.",
            {
                "connection_id": {
                    "type": "string",
                    "description": "Exact connection id in form src_block:src_port->dst_block:dst_port.",
                },
                "src_block": {
                    "type": "string",
                    "description": "Optional source block name used only to resolve an exact connection_id.",
                },
                "src_port": {
                    "type": ["integer", "string"],
                    "description": "Optional source port used only to resolve an exact connection_id.",
                },
                "dst_block": {
                    "type": "string",
                    "description": "Optional destination block name used only to resolve an exact connection_id.",
                },
                "dst_port": {
                    "type": ["integer", "string"],
                    "description": "Optional destination port used only to resolve an exact connection_id.",
                },
            },
            required=[],
        ),
        _schema(
            "rewire_connection",
            "Atomically replace one existing connection with a new connection.",
            {
                "old_connection_id": {
                    "type": "string",
                    "description": "Optional exact old connection id in form src_block:src_port->dst_block:dst_port.",
                },
                "old_src_block": {
                    "type": "string",
                    "description": "Optional old source block name used only to resolve one existing old connection.",
                },
                "old_src_port": {
                    "type": ["integer", "string"],
                    "description": "Optional old source port used only to resolve one existing old connection.",
                },
                "old_dst_block": {
                    "type": "string",
                    "description": "Optional old destination block name used only to resolve one existing old connection.",
                },
                "old_dst_port": {
                    "type": ["integer", "string"],
                    "description": "Optional old destination port used only to resolve one existing old connection.",
                },
                "new_src_block": {
                    "type": "string",
                    "description": "New source block name. Omit only when endpoint hints are intended to resolve candidates.",
                },
                "new_src_port": {
                    "type": ["integer", "string"],
                    "description": "New source port. Omit only when endpoint hints are intended to resolve candidates.",
                },
                "new_dst_block": {
                    "type": "string",
                    "description": "New destination block name. Omit only when endpoint hints are intended to resolve candidates.",
                },
                "new_dst_port": {
                    "type": ["integer", "string"],
                    "description": "New destination port. Omit only when endpoint hints are intended to resolve candidates.",
                },
            },
            required=[],
        ),
        _schema(
            "apply_edit",
            "Apply a supported transaction to the live graph.",
            {
                "transaction": {
                    "type": ["object", "array"],
                    "description": "One supported operation object or an ordered list of operation objects. Every operation object requires `op_type`.",
                }
            },
            required=["transaction"],
        ),
        _schema(
            "propose_edit",
            "Preview whether a supported transaction would succeed without modifying the graph.",
            {
                "transaction": {
                    "type": ["object", "array"],
                    "description": "One supported operation object or an ordered list of operation objects. Every operation object requires `op_type`.",
                }
            },
            required=["transaction"],
        ),
        _schema(
            "validate_graph",
            "Compile-check and validate the current graph. Returns validation status and any errors.",
            {},
        ),
        _schema(
            "save_graph",
            "Write the current graph to disk at the specified path.",
            {
                "path": {
                    "type": "string",
                    "description": "Destination path for the saved .grc file. Required when saving a copy, a new graph, or to a specific location.",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "Allow overwriting an explicit destination path.",
                },
            },
        ),
        _schema(
            "search_blocks",
            "Search installed GNU Radio catalog for block IDs and parameters.",
            {
                "query": {"type": "string"},
                "k": {"type": "integer"},
                "debug": {"type": "boolean"},
                "enrich": {"type": "boolean"},
            },
            required=["query"],
        ),
        _schema(
            "ask_grc_docs",
            "Answer GNU Radio documentation questions with local knowledge.",
            {
                "question": {"type": "string"},
                "k": {"type": "integer"},
                "debug": {"type": "boolean"},
            },
            required=["question"],
        ),
    ]
    mvp_schemas = [
        _schema(
            "inspect_graph",
            "Read-only inspection of the active graph. Returns topology, block instances, connections, parameter values, and validation status.",
            {
                "targets": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {"type": "string"},
                    "description": (
                        "Block, connection, or parameter target identifiers, or ['all']/['*'] for an overview."
                    ),
                },
                "params": {
                    "type": "array",
                    "maxItems": 12,
                    "items": {"type": "string"},
                    "description": "Filter to specific parameter keys or ['all'].",
                },
                "debug": {
                    "type": "boolean",
                    "description": "When true, include wrapper dispatch telemetry for eval/debug.",
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
                "debug": {
                    "type": "boolean",
                    "description": "When true, include internal ranking/debug metadata.",
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
    ]
    all_schemas = [*internal_schemas, *mvp_schemas]
    if tool_names is None:
        return all_schemas

    requested = set(tool_names)
    schema_by_name = {schema["function"]["name"]: schema for schema in all_schemas}
    ordered_names = [name for name in MODEL_TOOL_NAMES_ORDERED if name in requested]
    selected = [schema_by_name[name] for name in ordered_names if name in schema_by_name]
    if requested and requested.issubset(set(MVP_MODEL_TOOL_NAMES)):
        return [_strip_debug_properties(schema) for schema in selected]
    return selected
