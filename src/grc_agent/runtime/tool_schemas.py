"""Tool schemas for the MVP wrapper tools.

The MVP model surface is:

- ``inspect_graph`` (read) — flowgraph topology / blocks / connections
- ``query_knowledge`` (read) — routes to ``search_blocks`` or ``ask_grc_docs``
- ``web_search`` (read) — live web search (Ollama backend only; surfaced via
  the `web` plugin on OpenRouter)
- ``web_fetch`` (read) — fetch a web page by URL (Ollama backend only)
- ``change_graph`` (write) — flat batch graph mutations

Internal engines (e.g. ``search_blocks``, ``ask_grc_docs``) are NOT surfaced
to the model. They are called only by ``query_knowledge`` itself.
"""

from typing import Any

from grc_agent.domain_models import BlockState
from grc_agent.runtime.enums import SearchDomain

VALID_INSPECT_VIEWS: frozenset[str] = frozenset({"overview"})


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
            "view": {
                "type": "string",
                "enum": sorted(VALID_INSPECT_VIEWS),
                "description": "The view mode. Defaults to 'overview'.",
            },
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
                "enum": [d.value for d in SearchDomain],
                "description": "'catalog' for block types/params; 'docs' for concepts.",
            },
        },
        required=["query", "domain"],
        strict=True,
    ),
    _schema(
        "web_search",
        "Search the live web. Returns up to 10 result snippets (title, url, content). Use this for current events, recent releases, or any question that requires information not in the local catalog or docs.",
        {
            "query": {
                "type": "string",
                "description": "The web search query.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (1-10, default 5).",
                "minimum": 1,
                "maximum": 10,
            },
        },
        required=["query"],
        strict=True,
    ),
    _schema(
        "web_fetch",
        "Fetch a single web page by URL. Returns the page title, the main content as markdown, and the list of links on the page. Use this after web_search to read a specific result in full.",
        {
            "url": {
                "type": "string",
                "description": "The URL of the web page to fetch.",
            },
        },
        required=["url"],
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
                            "enum": [s.value for s in BlockState],
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
                            "description": "Param updates keyed by exact GNU param_id. Changing the 'id' parameter to rename a block is not allowed and will be ignored.",
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
                            "enum": [s.value for s in BlockState],
                            "description": "New block state.",
                        },
                    },
                    "required": ["instance_name", "state"],
                },
            },
            "add_connections": {
                "type": "array",
                "description": "Connection strings to add, format 'src_instance_name:port->dst_instance_name:port' (e.g. 'sig_source:0->throttle:0'). Stream ports use numeric index strings (e.g. '0', '1'), whereas message ports use their exact string identifiers (e.g. 'pdus', 'msg').",
                "items": {"type": "string"},
            },
            "remove_connections": {
                "type": "array",
                "description": "Connection strings to remove, format 'src_instance_name:port->dst_instance_name:port' (e.g. 'sig_source:0->throttle:0'). Stream ports use numeric index strings (e.g. '0', '1'), whereas message ports use their exact string identifiers (e.g. 'pdus', 'msg').",
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
    inspect_graph, query_knowledge, web_search, web_fetch, change_graph.
    """
    if tool_names is None:
        return list(_MVP_SCHEMAS)
    requested = set(tool_names)
    return [s for s in _MVP_SCHEMAS if s["function"]["name"] in requested]


__all__ = ["build_tool_schemas"]
