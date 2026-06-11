"""Tool schemas for MVP wrappers and internal runtime primitives."""

from typing import Any

from grc_agent.runtime.tool_surface import (
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
            "Create a new empty GRC flowgraph session. "
            "Use this when the user wants to build a new flowgraph from scratch. "
            "After creating, use apply_edit with add_block, add_connection, update_params to build the graph, "
            "then validate_graph and save_graph to persist it. "
            "Only profile='minimal' is supported.",
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
            "Return a bounded summary or overview of the loaded GNU Radio graph. "
            "Use this when the user asks for a summary or overview, "
            "or for vague whole-graph questions like what am I looking at, what is generating the signal here, "
            "give me a quick overview, what variables are in the graph, what blocks are loaded, "
            "is the graph dirty, show me the current state, or what changed. "
            "Use it even when a previous tool result already showed the state; do not answer those questions from memory alone.",
            {
                "max_blocks": {
                    "type": "integer",
                    "description": "Optional maximum number of blocks to preview.",
                }
            },
        ),
        _schema(
            "get_grc_context",
            "Show the local wiring around one loaded session block or variable. "
            "Use an exact session instance name when possible; close matches may be suggested on failure.",
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
            "Return the normalized GNU catalog description for one block id such as `blocks_throttle2` or `qtgui_time_sink_x`. "
            "If you searched first, use the result's `block_id` field. Use this directly for explicit block-type requests like `Describe the variable block type.` with `block_id=\"variable\"`.",
            {
                "block_id": {
                    "type": "string",
                    "description": "GNU Radio block id to describe.",
                }
            },
            required=["block_id"],
        ),
        _schema(
            "semantic_search_grc",
            "Search the local read-only vector index for semantically similar GNU Radio catalog blocks or documentation chunks. "
            "Use only for read-only discovery or explanation. "
            "This tool never authorizes graph mutation; edits require graph-local evidence, verified tools, and grcc validation. "
            "If the index is missing, tell the user to run `grc-agent vector build`.",
            {
                "query": {
                    "type": "string",
                    "description": "Semantic search text. Maximum 800 characters.",
                    "maxLength": 800,
                },
                "scope": {
                    "type": "string",
                    "enum": ["all", "catalog", "manual", "tutorial"],
                    "description": "Which read-only vector records to search.",
                },
                "k": {
                    "type": "integer",
                    "description": "Optional maximum results. Default 5, capped at 10.",
                },
            },
            required=["query"],
        ),
        _schema(
            "suggest_compatible_insertions",
            "Suggest catalog-backed blocks that can be inserted into an existing connection. "
            "Use this BEFORE insert_block_on_connection when the user asks to add/insert a compatible block into an existing connection or path. "
            "First inspect the graph (summarize_graph, get_grc_context) to identify the connection_id, "
            "then call this tool, then use insert_block_on_connection with one suggested candidate. "
            "Each candidate includes insert_tool_args that can be passed directly to insert_block_on_connection. "
            "Do NOT call describe_block first for insertion requests; this tool already searches the catalog for compatible candidates.",
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
            "Thin wrapper around `apply_edit` with `op_type=insert_block_on_connection`. "
            "Use this to insert one catalog block into an existing stream connection. "
            "Does not support message connections or multi-port inserted blocks. "
            "You may copy insert_tool_args from suggest_compatible_insertions directly into this tool. "
            "This tool builds a transaction and delegates to `apply_edit`; mutation, validation, rollback, and errors are identical.",
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
            "Autonomously search, score, try, and commit one compatible block insertion into a stream connection. "
            "Use this for natural-language requests like 'add a filter' or 'insert a head block' "
            "when the user does not provide exact connection_id, block_type, or params. "
            "The tool performs bounded candidate search internally and commits only the first candidate that passes grcc validation. "
            "If no candidate validates, it returns the attempted candidates with failure reasons. "
            "Never mutates the live graph unless grcc passes.",
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
            "Remove one existing connection by exact connection_id through the verified edit pipeline. "
            "Use connection_id when available. Endpoint fields are accepted only so the runtime can resolve "
            "one exact existing connection_id or ask for clarification; endpoint fields are never a separate "
            "mutation path.",
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
            "Atomically replace one existing connection with one new connection through the verified edit pipeline. "
            "The old connection may be given as old_connection_id or endpoint hints; endpoint hints are used only "
            "to resolve one exact old connection_id or ask for clarification. New endpoint hints may be partial only "
            "when they resolve to one executable candidate or a bounded clarification choice; never guess placement. "
            "This wrapper internally applies one ordered remove_connection + add_connection transaction and never commits a partial disconnect.",
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
            "Apply a supported transaction to the live graph. Use this for normal edit requests. "
            "Supported `op_type`: `update_params`, `update_states`, `add_connection`, `remove_connection`, `remove_block`, `add_block`, `insert_block_on_connection`. "
            "For inserting a block into an existing connection, use `insert_block_on_connection` instead of separate add_block/remove_connection/add_connection operations. "
            "For `add_block`, use `block_type` from the GNU catalog (e.g., `blocks_throttle2`, `variable`). "
            "Parameters not provided will be filled with catalog defaults. "
            'Example: `{"transaction": {"op_type": "update_params", "instance_name": "samp_rate", "params": {"value": "48000"}}}`.',
            {
                "transaction": {
                    "type": ["object", "array"],
                    "description": "One supported operation object or an ordered list of operation objects. Every operation object must include `op_type`.",
                }
            },
            required=["transaction"],
        ),
        _schema(
            "propose_edit",
            "Preview whether a supported transaction would succeed. This does not modify the graph. "
            "Use it only for explicit preview / dry-run / what-if requests. "
            "Supported `op_type`: `update_params`, `update_states`, `add_connection`, `remove_connection`, `remove_block`, `add_block`, `insert_block_on_connection`. "
            "For inserting a block into an existing connection, use `insert_block_on_connection` instead of separate add_block/remove_connection/add_connection operations. "
            'Example: `{"transaction": {"op_type": "update_params", "instance_name": "samp_rate", "params": {"value": "48000"}}}`.',
            {
                "transaction": {
                    "type": ["object", "array"],
                    "description": "One supported operation object or an ordered list of operation objects. Every operation object must include `op_type`.",
                }
            },
            required=["transaction"],
        ),
        _schema(
            "validate_graph",
            "Compile-check and validate the current graph with GNU Radio. "
            "Use this when the user asks to validate or check the graph, or to verify structural validity and compile success. "
            "If the same user turn also asked to save or summarize after validation, emit those tool calls after `validate_graph` in the same assistant message; do not stop after `validate_graph`.",
            {},
        ),
        _schema(
            "save_graph",
            "Write the current graph to disk. Use this to save, persist, write out, or write a copy to a path. "
            "Phrases like `write it out`, `write this out`, `save a copy to path` mean the current loaded graph. "
            "Manual save requests still require this internal tool even if the graph is already clean. "
            "Do NOT use this for `export as Python`, `standalone Python script`, or code generation requests; those are unsupported. "
            "Allowed only after the latest dirty state has validated successfully. "
            "When the user asks to save a copy, save to a specific path, or save a new graph, pass the explicit `path` parameter. "
            "Omit `path` only when saving an existing loaded graph directly to its current file location.",
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
            "Internal: search installed GNU Radio catalog for block IDs and params.",
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
            "Internal: answer GNU Radio docs questions with local sources.",
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
            "Read-only inspection of the active graph. Returns topology, block instances, connections, and parameter values.",
            {
                "targets": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {"type": "string"},
                    "description": (
                        "Block/conn/handle/exact block.param. No bare param or '.param'."
                    ),
                },
                "params": {
                    "type": "array",
                    "maxItems": 12,
                    "items": {"type": "string"},
                    "description": "Param keys or ['all']. For X on Y: targets=['Y'], params=['X'].",
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
            "Search the GNU Radio catalog for accurate block IDs, port names, and parameter keys.",
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
                "reasoning": {
                    "type": "string",
                    "description": "Briefly explain the current graph state and your step-by-step plan for this batch.",
                },
                "add_blocks": {
                    "type": "array",
                    "description": "Add blocks with optional initial params/states. Use installed block_id.",
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
                    "description": "Remove/delete existing blocks; for disable/turn off use update_states.",
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
                    "description": "Update params on existing blocks. Use exact GNU param_id. For variables, use update_params on their instance_name with params={value}.",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "instance_name": {
                                "type": "string",
                                "description": "Existing instance_name from inspect_graph.",
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
                    "description": "Enable/disable/bypass existing blocks; invalid candidates require force=true.",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "instance_name": {
                                "type": "string",
                                "description": "Existing instance_name from inspect_graph.",
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
                    "description": "Exact connection_id strings from inspect_graph to remove.",
                    "items": {"type": "string"},
                },
                "force": {
                    "type": "boolean",
                    "description": "Commit a GNU-grounded candidate despite final validation failure when an invalid intermediate graph fits the user goal.",
                },
            },
            required=["reasoning"],
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
