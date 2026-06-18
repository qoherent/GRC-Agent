# Model Context Bible

<!-- GENERATED: do not edit by hand. -->

This file is generated from the runtime prompt and model-facing tool schemas. To update it after changing `src/grc_agent/runtime/model_context.py` or `src/grc_agent/runtime/tool_schemas.py`, run:

```bash
UPDATE_MODEL_CONTEXT_BIBLE=1 uv run python -m unittest tests.test_model_context_bible
```

Normal test mode fails when this file is stale.

Prompt version: `2026-06-18-declarative-prompt`

## Model-Facing Surface

The default MVP chat surface exposes these wrapper tools, in order:

- `inspect_graph`
- `query_knowledge`
- `change_graph`

The model does not see lifecycle tools, shell/filesystem tools, raw YAML tools, direct transaction primitives, or low-level graph APIs.

## Injected System Prompt

```text
Role: GNU Radio graph editing assistant.
Routing contract:
- Questions about the active flowgraph (blocks, connections, parameter values, variable references): inspect_graph.
- GNU Radio documentation or concept questions (PMT, data types, stream tags, 'how do I'): query_knowledge with domain='docs'.
Structural contract:
- Variables are blocks; their lifecycle tools are add_blocks / update_params / remove_blocks.
- A block inserted on an existing wire requires a single change_graph payload containing remove_connections + add_blocks + add_connections.
- An input port accepts at most one connection.
- A block is deactivated without severing paths by update_states with state='bypass'.
- force=true is valid only for committing an invalid intermediate graph state required to progress.

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
            "description": "Block, connection, or parameter target identifiers, or ['all']/['*'] for an overview."
          },
          "params": {
            "type": "array",
            "maxItems": 12,
            "items": {
              "type": "string"
            },
            "description": "Filter to specific parameter keys or ['all']."
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
      "name": "change_graph",
      "description": "Apply a batch of structural graph edits. Can add/remove blocks, update parameters/states, and add/remove connections in a single transaction.",
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
            "description": "Remove/delete existing blocks from the graph.",
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
            "description": "Update parameters on existing blocks keyed by parameter ID.",
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
            "description": "Modify target block enablement state.",
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
            "description": "Exact connection_id strings to remove.",
            "items": {
              "type": "string"
            }
          },
          "force": {
            "type": "boolean",
            "description": "Bypass final validation compilation check to force apply intermediate graph state."
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
