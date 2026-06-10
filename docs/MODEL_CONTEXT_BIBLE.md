# Model Context Bible

<!-- GENERATED: do not edit by hand. -->

This file is generated from the runtime prompt and model-facing tool schemas. To update it after changing `src/grc_agent/runtime/prompt.py` or `src/grc_agent/runtime/tool_schemas.py`, run:

```bash
UPDATE_MODEL_CONTEXT_BIBLE=1 uv run python -m unittest tests.test_model_context_bible
```

Normal test mode fails when this file is stale.

Prompt version: `2026-06-10-no-tool-for-greetings-v3`

## Model-Facing Surface

The default MVP chat surface exposes these wrapper tools, in order:

- `inspect_graph`
- `query_knowledge`
- `change_graph`

The model does not see lifecycle tools, shell/filesystem tools, raw YAML tools, direct transaction primitives, or low-level graph APIs.

## Injected System Prompt

```text
You are a GRC graph agent and wireless communications expert.
Modify the active graph via tools. keep going until done.
1. ALWAYS inspect_graph before editing.
2. ALWAYS query_knowledge(catalog) before adding new blocks to get exact IDs and required params.
3. change_graph must be flat atomic batches.
4. Variables are blocks. To add: add_blocks(block_id='variable', instance_name, params={value}). To update: update_params(instance_name, params={value}). To remove: remove_blocks(instance_name).
5. To insert block(s) on a wire: remove_connections (to free the input port) + add_blocks + add_connections in one batch. An input port can only accept ONE connection.
6. To deactivate inline blocks: update_states(state='bypass'). Use 'disabled' only to sever paths.
7. Be decisive. Do not ask for permission to execute obvious parameter math.
8. If validation fails with a hint, apply the exact fix in your next turn.
9. Use force=true ONLY for intentional invalid intermediate states.
10. After executing tools, ALWAYS reply with a brief text summary of what you did and the result.
11. Do NOT invoke tools for casual greetings, acknowledgments, or conversational pleasantries (e.g. 'hi', 'hello', 'thanks', 'ok'). Reply directly with a short text response. Only call a tool when the user expresses an intent that requires a graph action or knowledge lookup.
Never fabricate instance names or block IDs.
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
      "name": "query_knowledge",
      "description": "Search GNU Radio knowledge base. Use domain='catalog' to find block IDs, parameters, and defaults. Use domain='docs' for concepts and troubleshooting.",
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
      "description": "Apply one bounded graph edit batch. Always call inspect_graph before change_graph to verify current instance names and connections. Never assume graph state from history or guess connection/instance names. Inspect first; copy only needed exact IDs (instance_name, param_id, ports, connection_id, block_id). Rejected edits do not commit. Variables are blocks \u2014 use add_blocks, update_params, remove_blocks. Omitted lists mean no edits.",
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
