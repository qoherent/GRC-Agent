# Model Context Bible

<!-- GENERATED: do not edit by hand. -->

This file is generated from the runtime prompt and model-facing tool schemas. To update it after changing `src/grc_agent/runtime/prompt.py` or `src/grc_agent/runtime/tool_schemas.py`, run:

```bash
UPDATE_MODEL_CONTEXT_BIBLE=1 uv run python -m unittest tests.test_model_context_bible
```

Normal test mode fails when this file is stale.

Prompt version: `2026-05-25-agentic-direct-edit-v2`

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
Work on one loaded graph through tools; keep going until done or blocked.
Use inspect_graph first; copy instance_name, param_id, ports, and connection_id exactly.
Use search_blocks for new GNU block_id/params; ask_grc_docs is concepts only.
Use update_variables with instance_name/value for variables; use update_params with instance_name and params keyed by param_id.
Use change_graph flat batches; add blocks with initial params/states and connections together.
Never fabricate targets, params, ports, connection_id, block_id, or target_ref.
Answer briefly from tool evidence; never claim an edit unless change_graph committed=true.
```

## Tool Schemas

These are the exact schemas returned by `build_tool_schemas(MVP_MODEL_TOOL_NAMES)`. Developer-only `debug` fields are stripped before model use.

```json
[
  {
    "type": "function",
    "function": {
      "name": "inspect_graph",
      "description": "Read graph. No args=overview. targets=details. params filters keys.",
      "parameters": {
        "type": "object",
        "properties": {
          "view": {
            "type": "string",
            "enum": [
              "overview",
              "details"
            ],
            "description": "Optional."
          },
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
      "description": "Search installed block catalog. Returns block_id, name, match, why.",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {
            "type": "string",
            "description": "Block capability or block-id query."
          },
          "k": {
            "type": "integer",
            "description": "Optional maximum candidates (default 5)."
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
          },
          "k": {
            "type": "integer",
            "description": "Optional maximum sources (default 3)."
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
      "description": "Apply one bounded batch of GNU Radio graph edits. Use inspect_graph/search_blocks first. For existing graph blocks copy instance_name and param_id/port/connection_id from inspect_graph. For new blocks copy block_id and params from search_blocks. For existing variables prefer update_variables. Omitted lists mean no edits of that kind.",
      "parameters": {
        "type": "object",
        "properties": {
          "add_blocks": {
            "type": "array",
            "description": "Blocks to add, with initial params/states.",
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
                  "description": "Initial state values such as {'state':'enabled'}."
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
            "description": "Existing blocks to remove; incident edges are detached by the runtime.",
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
                },
                "target_ref": {
                  "type": "object",
                  "description": "Optional guarded ref copied from inspect_graph."
                }
              }
            }
          },
          "update_params": {
            "type": "array",
            "description": "Batch parameter updates, one existing block per item. Use instance_name from inspect_graph and params keyed by param_id. For variable values such as samp_rate, prefer update_variables.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "instance_name": {
                  "type": "string",
                  "description": "Existing graph instance_name copied from inspect_graph."
                },
                "block_id": {
                  "type": "string",
                  "description": "Optional block ID disambiguator."
                },
                "target_ref": {
                  "type": "object",
                  "description": "Optional guarded ref copied from inspect_graph."
                },
                "params": {
                  "type": "object",
                  "description": "Parameter updates keyed by GNU param_id from inspect_graph/search_blocks."
                },
                "expected_params": {
                  "type": "object",
                  "description": "Optional old-value guards keyed by parameter ID."
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
            "description": "Batch state updates, one existing block per item.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "instance_name": {
                  "type": "string",
                  "description": "Existing graph instance_name copied from inspect_graph."
                },
                "block_id": {
                  "type": "string",
                  "description": "Optional block ID disambiguator."
                },
                "target_ref": {
                  "type": "object",
                  "description": "Optional guarded ref copied from inspect_graph."
                },
                "states": {
                  "type": "object",
                  "description": "State updates such as {'state':'disabled'} or {'enabled':false}."
                }
              },
              "required": [
                "instance_name",
                "states"
              ]
            }
          },
          "add_connections": {
            "type": "array",
            "description": "Connections to add with exact source/destination endpoints.",
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
          "rewire_connections": {
            "type": "array",
            "description": "Generic endpoint rewires by exact old connection_id.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "connection_id": {
                  "type": "string"
                },
                "new_src": {
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
                "new_dst": {
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
                "connection_id"
              ]
            }
          },
          "insert_blocks_on_connections": {
            "type": "array",
            "description": "Generic insertion of one block into an exact existing connection.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "connection_id": {
                  "type": "string"
                },
                "block_id": {
                  "type": "string"
                },
                "instance_name": {
                  "type": "string"
                },
                "params": {
                  "type": "object"
                },
                "states": {
                  "type": "object"
                }
              },
              "required": [
                "connection_id",
                "block_id",
                "instance_name"
              ]
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
            "description": "Existing variable values to update. Use this for graph variables shown by inspect_graph, for example samp_rate.",
            "items": {
              "type": "object",
              "additionalProperties": false,
              "properties": {
                "instance_name": {
                  "type": "string",
                  "description": "Existing variable instance_name copied from inspect_graph."
                },
                "value": {
                  "type": [
                    "string",
                    "number",
                    "boolean"
                  ]
                },
                "expected_value": {
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
            "description": "Only bypass final validation failure after a GNU-grounded candidate applies; never bypass unknown blocks, params, ports, stale files, or save failures."
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
