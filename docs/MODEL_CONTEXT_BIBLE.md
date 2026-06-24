# Model Context Bible

<!-- GENERATED: do not edit by hand. -->

This file is generated from the runtime prompt and model-facing tool schemas. To update it after changing `src/grc_agent/runtime/model_context.py` or `src/grc_agent/runtime/tool_schemas.py`, run:

```bash
UPDATE_MODEL_CONTEXT_BIBLE=1 uv run python -m unittest tests.test_model_context_bible
```

Normal test mode fails when this file is stale.

Prompt version: `2026-06-24-expression-params`

## Model-Facing Surface

The default MVP chat surface exposes these wrapper tools, in order:

- `inspect_graph`
- `query_knowledge`
- `change_graph`

The model does not see lifecycle tools, shell/filesystem tools, raw YAML tools, direct transaction primitives, or low-level graph APIs.

## Injected System Prompt

```text
Role: GNU Radio graph editing assistant.
inspect_graph: read topology, blocks, connections, field values, and validation status.
query_knowledge: search catalog blocks or GNU Radio documentation.
change_graph: add/remove blocks, edit field values, add/remove connections.
Parameter values are string expressions; a variable reference is the variable's name.
Variables are blocks; use block_id "variable" (not "parameter") to add one.
Every GNU Radio fact must be grounded in query_knowledge, not memory.

```

## Tool Schemas

These are the exact schemas returned by `build_tool_schemas(MVP_MODEL_TOOL_NAMES)`. Developer-only `debug` fields are stripped before model use.

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
