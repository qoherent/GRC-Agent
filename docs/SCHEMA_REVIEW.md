# Tool Schema Review — Consultant Brief

> **You are an independent schema-design consultant.** Below are three JSON
> schemas for function-calling tools available to an LLM agent. Your task is
> to review them in isolation and provide recommendations on simplicity,
> consistency, nesting, and model-friendliness.
>
> **Context you have:**
> - The agent uses these tools to inspect, query, and edit signal-processing
>   flowgraphs (blocks connected by typed data ports).
> - The model is a **4B parameter local model** (not GPT-4 class). It
>   struggles with deeply nested JSON, ambiguous descriptions, and
>   undiscoverable features.
> - The agent operates in a tool-calling loop: it calls tools, reads results,
>   reasons, and calls more tools until the task is done.
> - `inspect_graph` returns connections as flat strings in the format
>   `"block_name:port_index->block_name:port_index"` (e.g.
>   `"sig_source:0->throttle:0"`).
> - All three schemas use `"strict": true` and
>   `"additionalProperties": false`.
>
> **What we want from you:**
> 1. Score each schema (1–10) on simplicity, consistency, model-friendliness.
> 2. List every issue you find (structural, semantic, description quality,
>    nesting depth, type inconsistencies across operations).
> 3. For each issue, provide a concrete recommendation (show before/after).
> 4. Identify the single highest-impact change.
> 5. Comment on whether 3 tools is the right number, or whether splitting or
>    merging would help this model.
>
> Do not assume any knowledge of the codebase beyond what is written here.

---

## Schemas

### 1. inspect_graph

```json
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
          "items": {"type": "string"},
          "description": "Optional block instance_names to inspect. Empty/omitted (or ['all']) returns the whole-graph overview; a non-empty list scopes the result to those blocks plus connections touching them."
        }
      },
      "required": [],
      "additionalProperties": false
    },
    "strict": true
  }
}
```

### 2. query_knowledge

```json
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
          "enum": ["catalog", "docs"],
          "description": "'catalog' for block types/params; 'docs' for concepts."
        }
      },
      "required": ["query", "domain"],
      "additionalProperties": false
    },
    "strict": true
  }
}
```

### 3. change_graph

```json
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
                "enum": ["enabled", "disabled", "bypass"],
                "description": "Optional initial block state."
              }
            },
            "required": ["block_id", "instance_name"]
          }
        },
        "remove_blocks": {
          "type": "array",
          "description": "Remove/delete existing blocks from the graph.",
          "items": {
            "type": "object",
            "additionalProperties": false,
            "properties": {
              "instance_name": {"type": "string"}
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
            "required": ["instance_name", "params"]
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
                "enum": ["enabled", "disabled", "bypass"],
                "description": "New block state."
              }
            },
            "required": ["instance_name", "state"]
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
                  "block": {"type": "string"},
                  "port": {"type": ["integer", "string"]}
                },
                "required": ["block", "port"]
              },
              "dst": {
                "type": "object",
                "additionalProperties": false,
                "properties": {
                  "block": {"type": "string"},
                  "port": {"type": ["integer", "string"]}
                },
                "required": ["block", "port"]
              }
            },
            "required": ["src", "dst"]
          }
        },
        "remove_connections": {
          "type": "array",
          "description": "Exact connection_id strings to remove.",
          "items": {"type": "string"}
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
```

---

## Observed Issues

### Issue 1: `add_connections` vs `remove_connections` — asymmetric formats

`add_connections` requires depth-3 nested objects:

```json
{"src": {"block": "sig_source", "port": 0}, "dst": {"block": "throttle", "port": 0}}
```

`remove_connections` accepts flat strings:

```json
"sig_source:0->throttle:0"
```

The read tool (`inspect_graph`) returns connections in the flat string format.
So the model reads a connection as `"sig_source:0->throttle:0"` but must
decompose it into 4 nested fields to add a similar one.

**Observed failure:** In one transcript, the model reversed `src` and `dst`
inside the nested structure (connected output port to output port instead of
output to input), causing a validation error. The flat `->` arrow format makes
direction obvious; the nested objects do not.

### Issue 2: `remove_blocks` vs `remove_connections` — inconsistent item shapes

`remove_blocks` requires an object wrapping one string:

```json
[{"instance_name": "noise_source"}]
```

`remove_connections` accepts bare strings:

```json
["sig_source:0->throttle:0"]
```

**Observed failure:** In a controlled experiment, the model naturally produced
`["noise_source"]` (bare string) for `remove_blocks` and was hard-rejected by
the schema validator.

### Issue 3: `force` description is unclear

Current description: `"Bypass final validation compilation check to force apply
intermediate graph state."`

**Observed failure:** The model hit a validation error ("Port is not
connected") when trying to disable a block. It retried the exact same payload
3 times without ever adding `force: true`. When explicitly told to use `force`
in a different scenario, it did so correctly — so the model *can* use the
field, it just cannot *discover* it from the description.

### Issue 4: `port` type is `["integer", "string"]` with no guidance

Ports can be integer-indexed (`0`, `1`) or named (`"cmd"`, `"set"`) for
message ports. The schema declares `{"type": ["integer", "string"]}` with no
description explaining when to use which form. The model consistently used
integers in all observed calls.

### Issue 5: `add_blocks.state` overlaps with `update_states`

`add_blocks` has an optional `state` enum field. A separate top-level
`update_states` array does the same thing. The model occasionally emits
redundant operations (adds a block with `state: "disabled"` AND sends a
separate `update_states` entry for the same block).

### Issue 6: All 7 top-level fields in `change_graph` are optional

Every array (`add_blocks`, `remove_blocks`, `update_params`, `update_states`,
`add_connections`, `remove_connections`) and the `force` boolean are all
optional with no minimum requirement. An empty `{}` call is schema-valid.

### Issue 7: No field describes what a "block_id" vs "instance_name" is

`add_blocks` requires both `block_id` (catalog key like
`"analog_sig_source_x"`) and `instance_name` (user-chosen name like
`"my_source"`). Other operations reference blocks only by `instance_name`. The
schema descriptions are brief and may not make this distinction clear enough.

---

## Transcript Statistics

Across 8 autonomous agent sessions (30 total tool calls):

| Metric | Value |
|--------|-------|
| Total tool calls | 30 |
| Hard schema-validation failures | **0** (all JSON was syntactically valid) |
| `inspect_graph` calls | 13 (all correct) |
| `query_knowledge` calls | 6 (all correct) |
| `change_graph` calls | 11 (all syntactically valid) |
| Semantic failures traceable to schema design | **3** |
| Tasks completed successfully | 5 / 8 |

The model can *mechanically generate* all schema shapes. The failures are
*semantic* — wrong direction, undiscovered features, redundant operations —
not syntax errors.

---

## Nesting Depth Summary

| Operation | Max depth | Shape |
|-----------|:---------:|-------|
| `inspect_graph.targets` | 1 | `["string"]` |
| `query_knowledge.*` | 1 | flat fields |
| `change_graph.add_blocks` | 2 | `{block_id, instance_name, params:{}, state}` |
| `change_graph.remove_blocks` | 2 | `{instance_name}` |
| `change_graph.update_params` | 2 | `{instance_name, params:{}}` |
| `change_graph.update_states` | 2 | `{instance_name, state}` |
| `change_graph.add_connections` | **3** | `{src:{block,port}, dst:{block,port}}` |
| `change_graph.remove_connections` | 1 | `"string"` |
| `change_graph.force` | 1 | `boolean` |

---

## Your Review

*(Provide your analysis below)*
