# Model Context Bible

<!-- GENERATED: do not edit by hand. -->

This file is generated from the runtime prompt and model-facing tool schemas. To update it after changing `src/grc_agent/runtime/prompt.py` or `src/grc_agent/runtime/tool_schemas.py`, run:

```bash
UPDATE_MODEL_CONTEXT_BIBLE=1 uv run python -m unittest tests.test_model_context_bible
```

Normal test mode fails when this file is stale.

Prompt version: `2026-05-27-invalid-intermediate-v1`

## Model-Facing Surface

The default MVP chat surface exposes these wrapper tools, in order:

- `inspect_graph`
- `search_blocks`
- `ask_grc_docs`
- `change_graph`

The model does not see lifecycle tools, shell/filesystem tools, raw YAML tools, direct transaction primitives, or low-level graph APIs.

## Injected System Prompt

```text
You are a wireless communications expert and GRC graph agent.
Work one loaded graph through tools; keep going until done.
Use inspect_graph before edits. For blocks, search_blocks first. ask_grc_docs is concepts.
Use remove_connections + add_blocks + add_connections in one batch to insert a block on a wire.
Use update_variables for variables; update_params for block params; change_graph flat batches.
Use force=true for invalid intermediate. If rejected, quote error.
Never fabricate targets. Never claim edit unless change_graph committed=true.
```

## Tool Schemas

These are the exact schemas returned by `build_tool_schemas(MVP_MODEL_TOOL_NAMES)`. Developer-only `debug` fields are stripped before model use.

```json
[
  {
    "type": "function",
    "function": {
      "name": "inspect_graph",
      "description": "Inspect the live, currently active graph. Use this to see existing block instances, connections, and variables. Do NOT use this to discover new block types or parameter names. Give targets for details; omit for overview. Params filters keys.",
      "parameters": {
        "type": "object",
        "properties": {
          "targets": {
            "type": "array",
            "maxItems": 5,
            "items": {
              "type": "string"
            },
            "description": "Block/conn/handle/exact block.param. No bare param or '.param'."
          },
          "params": {
            "type": "array",
            "maxItems": 12,
            "items": {
              "type": "string"
            },
            "description": "Param keys or ['all']. For X on Y: targets=['Y'], params=['X']."
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
      "name": "search_blocks",
      "description": "Search the installed GNU Radio catalog. Use this to find block types (e.g., 'analog_agc_cc'), default values, and exact parameter IDs. Do NOT use this to check what is in the current graph.",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {
            "type": "string",
            "description": "Block capability or block-id query."
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
      "name": "ask_grc_docs",
      "description": "Answer GNU Radio docs questions with local sources. Explanation-only.",
      "parameters": {
        "type": "object",
        "properties": {
          "question": {
            "type": "string",
            "description": "GNU Radio concept or troubleshooting question."
          }
        },
        "required": [
          "question"
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
      "description": "Apply one bounded graph edit batch. Always call inspect_graph before change_graph to verify current instance names and connections. Never assume graph state from history or guess connection/instance names. Inspect first; copy only needed exact IDs (instance_name, param_id, ports, connection_id, block_id). Rejected edits do not commit. Prefer update_variables for variables. Omitted lists mean no edits.",
      "parameters": {
        "type": "object",
        "properties": {
          "add_blocks": {
            "type": "array",
            "description": "Add blocks with optional initial params/states. Use installed block_id.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "block_id": {
                  "type": "string",
                  "description": "Installed GNU Radio catalog block ID."
                },
                "instance_name": {
                  "type": "string",
                  "description": "New unique graph instance name."
                },
                "params": {
                  "type": "object",
                  "description": "Initial parameter values keyed by GNU parameter ID."
                },
                "states": {
                  "type": "object",
                  "description": "Initial states, e.g. {'state':'enabled'}."
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
            "description": "Remove/delete existing blocks; for disable/turn off use update_states.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "instance_name": {
                  "type": "string"
                },
                "block_id": {
                  "type": "string",
                  "description": "Optional block ID disambiguator."
                }
              }
            }
          },
          "update_params": {
            "type": "array",
            "description": "Update params on existing blocks. Use exact param_id; use update_variables for variables.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "instance_name": {
                  "type": "string",
                  "description": "Existing instance_name from inspect_graph."
                },
                "params": {
                  "type": "object",
                  "description": "Param updates keyed by exact GNU param_id."
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
            "description": "Enable/disable/bypass existing blocks; invalid candidates require force=true.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "instance_name": {
                  "type": "string",
                  "description": "Existing instance_name from inspect_graph."
                },
                "block_id": {
                  "type": "string",
                  "description": "Optional block ID disambiguator."
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
            "description": "Add exact src/dst endpoints by instance_name and port.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "src": {
                  "type": "object",
                  "additionalProperties": false,
                  "properties": {
                    "block": {
                      "type": "string"
                    },
                    "port": {
                      "type": [
                        "integer",
                        "string"
                      ]
                    }
                  },
                  "required": [
                    "block",
                    "port"
                  ]
                },
                "dst": {
                  "type": "object",
                  "additionalProperties": false,
                  "properties": {
                    "block": {
                      "type": "string"
                    },
                    "port": {
                      "type": [
                        "integer",
                        "string"
                      ]
                    }
                  },
                  "required": [
                    "block",
                    "port"
                  ]
                }
              },
              "required": [
                "src",
                "dst"
              ]
            }
          },
          "remove_connections": {
            "type": "array",
            "description": "Exact connection_id strings from inspect_graph to remove.",
            "items": {
              "type": "string"
            }
          },
          "add_variables": {
            "type": "array",
            "description": "Variables to add by new instance_name and value.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "instance_name": {
                  "type": "string"
                },
                "value": {
                  "type": [
                    "string",
                    "number",
                    "boolean"
                  ]
                }
              },
              "required": [
                "instance_name",
                "value"
              ]
            }
          },
          "update_variables": {
            "type": "array",
            "description": "Update existing variables shown by inspect_graph, e.g. samp_rate.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "instance_name": {
                  "type": "string",
                  "description": "Existing variable instance_name."
                },
                "value": {
                  "type": [
                    "string",
                    "number",
                    "boolean"
                  ]
                }
              },
              "required": [
                "instance_name",
                "value"
              ]
            }
          },
          "remove_variables": {
            "type": "array",
            "description": "Variable instance_name strings to remove.",
            "items": {
              "type": "string"
            }
          },
          "force": {
            "type": "boolean",
            "description": "Commit a GNU-grounded candidate despite final validation failure when an invalid intermediate graph fits the user goal."
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
