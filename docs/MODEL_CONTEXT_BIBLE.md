# Model Context Bible

<!-- GENERATED: do not edit by hand. -->

This file is generated from the runtime prompt and model-facing tool schemas. To update it after changing `src/grc_agent/runtime/prompt.py` or `src/grc_agent/runtime/tool_schemas.py`, run:

```bash
UPDATE_MODEL_CONTEXT_BIBLE=1 uv run python -m unittest tests.test_model_context_bible
```

Normal test mode fails when this file is stale.

Prompt version: `2026-06-11-seamless-v1`

## Model-Facing Surface

The default MVP chat surface exposes these wrapper tools, in order:

- `inspect_graph`
- `query_knowledge`
- `change_graph`

The model does not see lifecycle tools, shell/filesystem tools, raw YAML tools, direct transaction primitives, or low-level graph APIs.

## Injected System Prompt

```text
You are a GNU Radio graph editing assistant. First, echo the user's complete request in your own words by explicitly listing every block and connection required, and then immediately execute the necessary tools to fulfill it. Keep these structural rules in mind while editing: variables are blocks (use add_blocks, update_params, remove_blocks). To insert a block on an existing wire, you must batch remove_connections, add_blocks, and add_connections together in a single payload. An input port can only accept one connection. To deactivate a block without severing paths, use update_states with 'bypass'. Use force=true only if you must commit an invalid intermediate graph state to progress.
```

## Tool Schemas

These are the exact schemas returned by `build_tool_schemas(MVP_MODEL_TOOL_NAMES)`. Developer-only `debug` fields are stripped before model use.

```json
[
  {
    "type": "function",
    "function": {
      "name": "inspect_graph",
      "description": "Read-only inspection of the active graph. Returns topology, block instances, connections, and parameter values.",
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
      "name": "query_knowledge",
      "description": "Search the GNU Radio catalog for accurate block IDs, port names, and parameter keys.",
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
      "name": "change_graph",
      "description": "Apply a batch of structural graph edits. Can add/remove blocks, update parameters/states, and add/remove connections in a single transaction.",
      "parameters": {
        "type": "object",
        "properties": {
          "reasoning": {
            "type": "string",
            "description": "Briefly explain the current graph state and your step-by-step plan for this batch."
          },
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
                "state": {
                  "type": "string",
                  "enum": [
                    "enabled",
                    "disabled",
                    "bypass"
                  ],
                  "description": "Optional initial block state."
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
                }
              }
            }
          },
          "update_params": {
            "type": "array",
            "description": "Update params on existing blocks. Use exact GNU param_id. For variables, use update_params on their instance_name with params={value}.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "instance_name": {
                  "type": "string",
                  "description": "Target block instance name."
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
                  "description": "Target block instance name."
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
          "force": {
            "type": "boolean",
            "description": "Commit a GNU-grounded candidate despite final validation failure when an invalid intermediate graph fits the user goal."
          }
        },
        "required": [
          "reasoning"
        ],
        "additionalProperties": false
      },
      "strict": true
    }
  }
]
```
