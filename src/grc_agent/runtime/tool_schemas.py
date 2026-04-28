"""Model-facing tool schemas for the GRC Agent runtime."""

from typing import Any

PUBLIC_TOOL_NAMES: tuple[str, ...] = (
    "new_grc",
    "load_grc",
    "summarize_graph",
    "search_grc",
    "get_grc_context",
    "describe_block",
    "search_manual",
    "suggest_compatible_insertions",
    "insert_block_on_connection",
    "auto_insert_block",
    "remove_connection",
    "apply_edit",
    "propose_edit",
    "validate_graph",
    "save_graph",
)


def _schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    *,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
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


def build_tool_schemas() -> list[dict[str, Any]]:
    """Return the fixed tool schemas exposed to a chat-completions client.

    Tool order matters — models prefer earlier tools.
    `suggest_compatible_insertions` must appear before `apply_edit`.
    """
    return [
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
            "Use it even when a previous tool result already showed the state; do not answer those questions from memory alone. "
            "Do NOT use this when the user explicitly says to search or look through the current graph "
            "for a class of blocks like sinks or sources; use `search_grc` with `scope=\"session\"` instead.",
            {
                "max_blocks": {
                    "type": "integer",
                    "description": "Optional maximum number of blocks to preview.",
                }
            },
        ),
        _schema(
            "search_grc",
            "Search GNU Radio blocks by name or function. Use `scope=\"catalog\"` for discovery and `scope=\"session\"` only for the loaded graph. "
            "If the user said find / search / look up first, use this before `describe_block`. "
            "Example: `Find the Head block` => `search_grc(query=\"Head\", scope=\"catalog\")`.",
            {
                "query": {
                    "type": "string",
                    "description": "Search text to look up.",
                },
                "scope": {
                    "type": "string",
                    "enum": ["catalog", "session"],
                    "description": "Whether to search the installed GNU catalog or the active session.",
                },
                "k": {
                    "type": "integer",
                    "description": "Optional maximum number of results to return.",
                },
            },
            required=["query"],
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
            "search_manual",
            "Search bundled GNU Radio tutorial/manual pages for explanation-only context with citations. "
            "Use this for how/why/conceptual GNU Radio questions. "
            "Do NOT use manual results as mutation authority; graph changes must use catalog/session tools and grcc validation.",
            {
                "query": {
                    "type": "string",
                    "description": "Conceptual GNU Radio question or search text.",
                },
                "k": {
                    "type": "integer",
                    "description": "Optional maximum number of cited excerpts.",
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
            "Do NOT call search_grc or describe_block first for insertion requests; this tool already searches the catalog for compatible candidates.",
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
            "Use only when the user provided or you inspected an exact connection_id. "
            "If endpoints are vague, inspect or ask for exact endpoints first.",
            {
                "connection_id": {
                    "type": "string",
                    "description": "Exact connection id in form src_block:src_port->dst_block:dst_port.",
                },
            },
            required=["connection_id"],
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
            "An explicit save request still requires this tool even if the graph is already clean. "
            "Do NOT use this for `export as Python`, `standalone Python script`, or code generation requests; those are unsupported. "
            "Allowed only after the latest dirty state has validated successfully. "
            "When the user asks to save a copy, save to a specific path, or save a new graph, pass the explicit `path` parameter. "
            "Omit `path` only when saving an existing loaded graph directly to its current file location.",
            {
                "path": {
                    "type": "string",
                    "description": "Destination path for the saved .grc file. Required when saving a copy, a new graph, or to a specific location.",
                }
            },
        ),
    ]
