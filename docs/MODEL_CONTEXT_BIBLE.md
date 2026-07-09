# Model Context Bible

<!-- GENERATED: do not edit by hand. -->

This file is generated from the runtime prompt and model-facing tool schemas. To update it after changing `src/grc_agent/runtime/model_context.py` or `src/grc_agent/runtime/tool_schemas.py`, run:

```bash
UPDATE_MODEL_CONTEXT_BIBLE=1 uv run python -m unittest tests.test_model_context_bible
```

Normal test mode fails when this file is stale.

Prompt version: `2026-07-07-explicit-update-states`

## Model-Facing Surface

The default MVP chat surface exposes these wrapper tools, in order:

- `inspect_graph`
- `query_knowledge`
- `web_search`
- `web_fetch`
- `change_graph`

The model does not see lifecycle tools, shell/filesystem tools, raw YAML tools, direct transaction primitives, or low-level graph APIs.

## Injected System Prompt

```text
Role: GNU Radio graph editing assistant.
inspect_graph: read topology, blocks, connections, field values, and validation status. Pass a targets list of block instance names to scope it to those blocks instead of the whole graph.
query_knowledge: search catalog blocks or GNU Radio documentation.
web_search: search the live web. web_fetch: fetch a specific page by URL.
change_graph: add/remove blocks, edit field values, add/remove connections.
Parameter values are string expressions; a variable reference is simply the variable's name (e.g. use 'base_freq * 1.5', NOT 'vars.base_freq * 1.5').
Set a type-controlling parameter (e.g. 'type', 'itype', 'otype') to the literal value 'auto' to resolve it from a connected neighbor's dtype instead of guessing a value.
Stream-port connections use numeric port keys (e.g. '0', '1', '2'), not names like 'out', 'in(0)', or 'in0'. GRC error messages like 'in(0)' refer to port index '0'. Message ports are the exception: they use their exact declared string identifier (e.g. 'pdus', 'msg') instead of a numeric index.
Do not attempt to rename blocks by changing the 'id' parameter in update_params; changing a block's ID is not supported and will be ignored. To rename a block, you must remove it and add a new one.
Variables are blocks; use block_id "variable" (not "parameter") to add one.
Every GNU Radio fact must be grounded in query_knowledge, not memory.
Ensure the final state of the flowgraph is valid: run inspect_graph before finishing and verify that validation.status is 'valid'.
A change_graph call that returns ok=false applied nothing — the batch was rolled back. Read the errors, adjust the call, and retry; do not resubmit identical arguments.
Describing a change_graph call in your reply text does not execute it; only an actual tool call applies changes to the graph.
The force=True flag in change_graph commits edits but does not resolve errors; you must still fix any unconnected ports or blocks to make the graph valid.
To change a block's enablement, use the update_states batch field: {instance_name, state}, where state is enabled, disabled, or bypass.
'Port is not connected' means a required port has zero active connections — this includes a newly added block that was never wired up, not only a block being disabled. Disabling a block that is part of a connection also fails this same validation; use state=bypass to take a connected block out of service without breaking the graph, or force=true to commit the disabled state anyway.
When removing blocks, also update_states (disabled/bypass) or remove any source blocks that become unconnected.
Never use hallucinated block IDs; if query_knowledge does not return a block ID, it does not exist.
When the user asks a question, answer concisely: lead with the direct answer, then add only the context needed to act on it.
Do not use LaTeX or TeX math notation in chat replies; write math inline in plain text (e.g. `350 microHz`, `f^2`, `x_i`).

```

## Tool Schemas

These are the exact schemas returned by `build_tool_schemas(MVP_MODEL_TOOL_NAMES)`.

```json
[
  {
    "type": "function",
    "function": {
      "name": "inspect_graph",
      "description": "Read-only inspection of the active graph. Returns topology, block instances, connections, parameter values, and validation status.",
      "parameters": {
        "type": "object",
        "properties": {
          "view": {
            "type": "string",
            "enum": [
              "overview"
            ],
            "description": "The view mode. Defaults to 'overview'."
          },
          "targets": {
            "type": "array",
            "items": {
              "type": "string"
            },
            "description": "Optional block instance_names to inspect. Empty/omitted (or ['all']) returns the whole-graph overview; a non-empty list scopes the result to those blocks plus connections touching them."
          }
        },
        "required": [],
        "additionalProperties": false
      },
      "strict": true
    }
  },
  {
    "type": "function",
    "function": {
      "name": "query_knowledge",
      "description": "Answer GNU Radio knowledge questions from two domains: catalog (block IDs, port names, parameter keys) or docs (GNU Radio documentation and concepts such as PMT, sample rate, stream tags, and 'how do I' questions).",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {
            "type": "string",
            "description": "Block capability, block-id, or concept question."
          },
          "domain": {
            "type": "string",
            "enum": [
              "catalog",
              "docs"
            ],
            "description": "'catalog' for block types/params; 'docs' for concepts."
          }
        },
        "required": [
          "query",
          "domain"
        ],
        "additionalProperties": false
      },
      "strict": true
    }
  },
  {
    "type": "function",
    "function": {
      "name": "web_search",
      "description": "Search the live web. Returns up to 10 result snippets (title, url, content).",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {
            "type": "string",
            "description": "The web search query."
          },
          "max_results": {
            "type": "integer",
            "description": "Maximum number of results to return (1-10, default 5).",
            "minimum": 1,
            "maximum": 10
          }
        },
        "required": [
          "query"
        ],
        "additionalProperties": false
      },
      "strict": true
    }
  },
  {
    "type": "function",
    "function": {
      "name": "web_fetch",
      "description": "Fetch a single web page by URL. Returns the page title, the main content as markdown, and the list of links on the page.",
      "parameters": {
        "type": "object",
        "properties": {
          "url": {
            "type": "string",
            "description": "The URL of the web page to fetch."
          }
        },
        "required": [
          "url"
        ],
        "additionalProperties": false
      },
      "strict": true
    }
  },
  {
    "type": "function",
    "function": {
      "name": "change_graph",
      "description": "Apply a batch of structural graph edits. Can add/remove blocks, update parameters/states, and add/remove connections in a single transaction. At least one array must be provided.",
      "parameters": {
        "type": "object",
        "properties": {
          "add_blocks": {
            "type": "array",
            "description": "Add blocks with optional initial params/states using installed catalog block_ids.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "block_id": {
                  "type": "string",
                  "description": "Installed GNU Radio catalog block ID (e.g. 'analog_sig_source_x')."
                },
                "instance_name": {
                  "type": "string",
                  "description": "New unique graph instance name (e.g. 'my_source')."
                },
                "params": {
                  "type": "object",
                  "additionalProperties": {
                    "type": "string"
                  },
                  "description": "Initial parameter values keyed by GNU parameter ID. Every value is a string expression, even for numeric or boolean parameters (e.g. '1000', not 1000)."
                },
                "state": {
                  "type": "string",
                  "enum": [
                    "enabled",
                    "disabled",
                    "bypass"
                  ],
                  "description": "Initial block state; defaults to 'enabled'."
                }
              },
              "required": [
                "block_id",
                "instance_name"
              ]
            }
          },
          "remove_blocks": {
            "type": "array",
            "description": "Remove existing blocks from the graph by instance name.",
            "items": {
              "type": "string"
            }
          },
          "update_params": {
            "type": "array",
            "description": "Update parameters on existing blocks keyed by parameter ID.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "instance_name": {
                  "type": "string",
                  "description": "Target block instance name (e.g. 'my_source')."
                },
                "params": {
                  "type": "object",
                  "additionalProperties": {
                    "type": "string"
                  },
                  "description": "Param updates keyed by GNU parameter ID. Every value is a string expression, even for numeric or boolean parameters (e.g. '1000', not 1000). Changing the 'id' parameter to rename a block is not allowed and will be ignored."
                }
              },
              "required": [
                "instance_name",
                "params"
              ]
            }
          },
          "update_states": {
            "type": "array",
            "description": "Modify target block enablement state.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "instance_name": {
                  "type": "string",
                  "description": "Target block instance name (e.g. 'my_source')."
                },
                "state": {
                  "type": "string",
                  "enum": [
                    "enabled",
                    "disabled",
                    "bypass"
                  ],
                  "description": "New block state."
                }
              },
              "required": [
                "instance_name",
                "state"
              ]
            }
          },
          "add_connections": {
            "type": "array",
            "description": "Connection strings to add, format 'src_instance_name:port->dst_instance_name:port' (e.g. 'sig_source:0->throttle:0'). Stream ports use numeric index strings (e.g. '0', '1'), whereas message ports use their exact string identifiers (e.g. 'pdus', 'msg').",
            "items": {
              "type": "string"
            }
          },
          "remove_connections": {
            "type": "array",
            "description": "Connection strings to remove, format 'src_instance_name:port->dst_instance_name:port' (e.g. 'sig_source:0->throttle:0'). Stream ports use numeric index strings (e.g. '0', '1'), whereas message ports use their exact string identifiers (e.g. 'pdus', 'msg').",
            "items": {
              "type": "string"
            }
          },
          "force": {
            "type": "boolean",
            "description": "When true, edits are committed even if validation fails (e.g. 'Port is not connected'). Default false."
          }
        },
        "required": [],
        "additionalProperties": false
      },
      "strict": true
    }
  }
]
```
