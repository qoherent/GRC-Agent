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
            "semantic_search_grc",
            "Search the local read-only vector index for semantically similar GNU Radio catalog blocks or documentation chunks. "
            "Use only for read-only discovery or explanation when lexical search is likely insufficient. "
            "This tool never authorizes graph mutation; any edit still requires exact TurnPlan intent, verified tools, and grcc validation. "
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
    mvp_schemas = [
        _schema(
            "inspect_graph",
            "Single model-facing graph inspection surface. "
            "Use this for summary, block/connection context, validate, list blocks/connections/variables, "
            "or compact checkpoint/history overview. Returns bounded compact output only.",
            {
                "operation": {
                    "type": "string",
                    "enum": [
                        "summarize",
                        "context",
                        "validate",
                        "list_blocks",
                        "list_connections",
                        "list_variables",
                        "history_summary",
                    ],
                    "description": "Inspection operation.",
                },
                "target": {
                    "type": "string",
                    "description": "Optional target block/variable name for context operations.",
                },
                "max_items": {
                    "type": "integer",
                    "description": "Optional compact output bound.",
                },
                "debug": {
                    "type": "boolean",
                    "description": "When true, include wrapper dispatch telemetry for eval/debug.",
                },
            },
            required=["operation"],
        ),
        _schema(
            "search_blocks",
            "Single model-facing block search. Returns compact block candidates only: block_id, name, summary. "
            "Internally combines semantic and lexical retrieval with exact-ID boost.",
            {
                "query": {
                    "type": "string",
                    "description": "Block capability or block-id query.",
                },
                "k": {
                    "type": "integer",
                    "description": "Optional maximum candidates (default 5).",
                },
                "debug": {
                    "type": "boolean",
                    "description": "When true, include internal ranking/debug metadata.",
                },
                "enrich": {
                    "type": "boolean",
                    "description": "When true, optionally enrich missing summaries via internal block descriptions.",
                },
            },
            required=["query"],
        ),
        _schema(
            "ask_grc_docs",
            "Single model-facing GNU Radio docs answer helper. Retrieves local docs/tutorial/manual snippets, "
            "then returns a concise grounded answer with sources. Explanation-only and never mutation authority.",
            {
                "question": {
                    "type": "string",
                    "description": "GNU Radio concept or troubleshooting question.",
                },
                "k": {
                    "type": "integer",
                    "description": "Optional maximum sources (default 3).",
                },
                "focus": {
                    "type": "string",
                    "description": "Optional short topic focus.",
                },
                "debug": {
                    "type": "boolean",
                    "description": "When true, include wrapper dispatch telemetry for eval/debug.",
                },
            },
            required=["question"],
        ),
        _schema(
            "change_graph",
            "Single model-facing graph change surface. "
            "Set dry_run=true for preview and dry_run=false for committed mutation. "
            "Internally routes through verified mutation handlers with preflight, grcc validation, and rollback. "
            "Every mutation call must include dry_run, user_goal, and operation_kind. "
            "Example rewire commit: dry_run=false, operation_kind='rewire', user_goal='rewire tag debug input', "
            "connection_id='old_src:0->dst:0', new_src_block='new_src', new_src_port=0, new_dst_block='dst', new_dst_port=0. "
            "Example insert commit: dry_run=false, operation_kind='insert_block', user_goal='insert throttle', "
            "connection_id='src:0->dst:0', block_id='blocks_throttle2', instance_name='blocks_throttle2_0', "
            "insert_params={'type':'float','samples_per_second':'samp_rate'}.",
            {
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview only when true; live mutation when false.",
                },
                "user_goal": {
                    "type": "string",
                    "description": (
                        "Required on every call: short natural-language restatement of "
                        "the requested graph change. Evidence only; not routing authority."
                    ),
                },
                "operation_kind": {
                    "type": "string",
                    "enum": [
                        "set_param",
                        "set_state",
                        "add_variable",
                        "disconnect",
                        "rewire",
                        "insert_block",
                        "remove_block",
                        "auto_insert",
                        "clarify",
                        "unsupported",
                    ],
                    "description": (
                        "Structured operation selector. The runtime dispatches from this "
                        "when present; user_goal does not override it."
                    ),
                },
                "target_ref": {
                    "type": "object",
                    "description": "Optional guarded duplicate-target reference.",
                    "properties": {
                        "block_uid": {"type": "string"},
                        "expected_instance_name": {"type": "string"},
                        "expected_block_type": {"type": "string"},
                        "base_state_revision": {"type": "integer"},
                    },
                    "required": [
                        "block_uid",
                        "expected_instance_name",
                        "expected_block_type",
                        "base_state_revision",
                    ],
                    "additionalProperties": False,
                },
                "block_id": {
                    "type": "string",
                    "description": (
                        "Catalog block id for insert_block operations (for example "
                        "`blocks_throttle2` or `blocks_head`), not the new instance name. "
                        "Use with a connection_id; runtime validates compatibility and refuses invalid candidates."
                    ),
                },
                "candidate_id": {
                    "type": "string",
                    "description": (
                        "Optional alias for a previously selected insert candidate block id. "
                        "For insert_block, provide block_id or candidate_id."
                    ),
                },
                "insert_block": {
                    "type": "string",
                    "description": (
                        "Legacy alias for insert candidate id used by some models. "
                        "Equivalent to block_id for insert_block operations; pass a catalog "
                        "block id here, not the new instance name."
                    ),
                },
                "instance_name": {
                    "type": "string",
                    "description": (
                        "Exact loaded block/variable instance name. Required for "
                        "set_param and set_state. For remove_block, provide instance_name "
                        "or guarded target_ref."
                    ),
                },
                "connection_id": {
                    "type": "string",
                    "description": (
                        "Exact connection id `src_block:src_port->dst_block:dst_port`. "
                        "Primary path for disconnect and required old edge for rewire. "
                        "Required anchor for insert_block. If the user says between source "
                        "output N and destination input M, construct `source:N->destination:M`."
                    ),
                },
                "state_revision": {
                    "type": "integer",
                    "description": (
                        "Optional optimistic revision guard. When provided, it must "
                        "match the active graph state_revision or the call is refused. "
                        "Required for rewire operations."
                    ),
                },
                "src_block": {"type": "string"},
                "src_port": {"type": ["integer", "string"]},
                "dst_block": {"type": "string"},
                "dst_port": {"type": ["integer", "string"]},
                "new_src_block": {
                    "type": "string",
                    "description": (
                        "Rewire new source block. Exact path provides all new_* fields; "
                        "partial hints are allowed only when they resolve to one executable candidate."
                    ),
                },
                "new_src_port": {
                    "type": ["integer", "string"],
                    "description": "Rewire new source port (stream index or message port id).",
                },
                "new_dst_block": {
                    "type": "string",
                    "description": (
                        "Rewire new destination block. Exact path provides all new_* fields; "
                        "partial hints are allowed only when they resolve to one executable candidate."
                    ),
                },
                "new_dst_port": {
                    "type": ["integer", "string"],
                    "description": "Rewire new destination port (stream index or message port id).",
                },
                "insert_params": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "description": (
                                "GNU item type for type-polymorphic inserted blocks. "
                                "If the user says 'with type float', pass 'float'."
                            ),
                        },
                        "samples_per_second": {
                            "type": ["string", "number", "integer"],
                            "description": (
                                "Sample rate expression for throttle-like inserted blocks."
                            ),
                        },
                    },
                    "description": (
                        "Optional parameter overrides for insert_block candidates. "
                        "Used when the selected block needs explicit compatible params "
                        "(for example stream type or length parameters). If the prompt "
                        "says 'with type float', include {'type': 'float'}."
                    ),
                },
                "detach_connections": {
                    "type": "boolean",
                    "description": (
                        "remove_block only. When true, explicitly allow removing all "
                        "connections attached to the target block in the same ordered "
                        "transaction before the remove_block step. When false or omitted, "
                        "attached targets are refused with a clarification payload."
                    ),
                },
                "detach_connection_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "remove_block only. Optional explicit connection ids expected to be "
                        "removed before block removal. When provided, they must match the "
                        "current attached connections exactly or the call is refused."
                    ),
                },
                "param_key": {
                    "type": "string",
                    "description": "Parameter name for set_param (for example `value`). Not a block name.",
                },
                "param_value": {"type": ["string", "number", "integer", "boolean"]},
                "state": {"type": "string", "enum": ["enabled", "disabled"]},
                "variable_name": {
                    "type": "string",
                    "description": "New variable instance name for operation_kind=add_variable.",
                },
                "variable_value": {
                    "type": ["string", "number", "integer", "boolean"],
                    "description": "Initial value/expression for operation_kind=add_variable.",
                },
                "debug": {
                    "type": "boolean",
                    "description": "When true, include wrapper dispatch telemetry for eval/debug.",
                },
            },
            required=["dry_run", "user_goal"],
        ),
        _schema(
            "save_graph_explicit",
            "Explicit lifecycle save wrapper for model-facing MVP chat. "
            "Use only when the user explicitly asks to save, persist, or write a graph copy. "
            "This wrapper always validates the current graph before writing and refuses unsafe writes.",
            {
                "path": {
                    "type": "string",
                    "description": (
                        "Optional destination .grc path. Omit only to save in-place to the "
                        "currently loaded graph path."
                    ),
                },
                "overwrite": {
                    "type": "boolean",
                    "description": (
                        "When true, allow overwrite of an existing explicit destination path. "
                        "Ignored for in-place save to the active session path."
                    ),
                },
                "debug": {
                    "type": "boolean",
                    "description": "When true, include wrapper dispatch telemetry for eval/debug.",
                },
            },
        ),
        _schema(
            "load_graph_explicit",
            "Explicit lifecycle load wrapper for model-facing MVP chat. "
            "Use only when the user explicitly asks to load/open/switch to a graph file. "
            "This wrapper enforces copied-graph safety policy and validates after load.",
            {
                "path": {
                    "type": "string",
                    "description": "Path to the .grc file to load into the active session.",
                },
                "debug": {
                    "type": "boolean",
                    "description": "When true, include wrapper dispatch telemetry for eval/debug.",
                },
            },
            required=["path"],
        ),
    ]
    all_schemas = [*internal_schemas, *mvp_schemas]
    if tool_names is None:
        return all_schemas

    requested = set(tool_names)
    schema_by_name = {schema["function"]["name"]: schema for schema in all_schemas}
    ordered_names = [name for name in MODEL_TOOL_NAMES_ORDERED if name in requested]
    return [schema_by_name[name] for name in ordered_names if name in schema_by_name]
