# change_graph Tool Reference

Visual breakdown of the `change_graph` model-facing tool.

---

## Execution order

All fields are optional (at least one must be provided). Operations run in this order:

1. **add_blocks** — creates blocks
2. **auto-resolve** — fills missing `type` from neighbor (automatic, no input needed)
3. **remove_blocks** — deletes blocks (cascades their connections)
4. **update_params** — changes parameter values
5. **update_states** — changes enabled/disabled/bypass
6. **remove_connections** — removes edges (runs BEFORE add to prevent transient errors)
7. **add_connections** — adds edges
8. **validate** — GRC native rewrite + validate + is_valid

If validation fails and `force` is false → entire batch rolls back.

---

## Fields

### add_blocks

- **Type:** `array[object]`
- **Purpose:** Add new blocks to the graph
- **Each entry:**
  - `block_id` (`string`, **required**) — catalog block ID, e.g. `"analog_sig_source_x"`
  - `instance_name` (`string`, **required**) — unique name for this instance, e.g. `"my_source"`
  - `params` (`object`, optional) — initial param values keyed by GNU param ID, e.g. `{"type": "float", "freq": "350"}`
  - `state` (`enum`, optional) — `"enabled"` (default), `"disabled"`, or `"bypass"`
- **Backend logic:**
  1. Duplicate name check: `flow_graph.get_block(instance_name)` — if it exists, reject (**NATIVE**: `FlowGraph.get_block`)
  2. Create block: `flow_graph.new_block(block_id)` — returns a new Block instance from the platform catalog (**NATIVE**: `FlowGraph.new_block`)
  3. Set name: `block.params["id"].set_value(instance_name)` — GRC's `Block.name` is read-only, so naming goes through the `id` param (**NATIVE**: `Param.set_value`)
  4. Rewrite: `flow_graph.rewrite()` — rebuilds namespace, resolves port types (**NATIVE**: `FlowGraph.rewrite`)
  5. Apply each param: checks `param_key in block.params` then `block.params[key].set_value(value)` — unknown keys raise `KeyError` (**NATIVE**: `Param.set_value`)
  6. Set state if non-default: delegates to `set_block_state` (see update_states)
- **Note:** If `type` is omitted and the block is connected to a typed neighbor in the same batch, the adapter auto-resolves it. The auto-resolved value appears in the response as `"auto_resolved": {"mid_throttle": "float"}`.

**Example:**
```json
"add_blocks": [
  {
    "block_id": "blocks_throttle",
    "instance_name": "mid_throttle",
    "params": {"samples_per_second": "samp_rate", "type": "float"}
  }
]
```

---

### remove_blocks

- **Type:** `array[string]`
- **Purpose:** Remove existing blocks by name
- **Each entry:** bare instance name string
- **Backend logic:**
  1. Look up block: `flow_graph.get_block(instance_name)` (**NATIVE**: `FlowGraph.get_block`)
  2. Remove: `flow_graph.remove_element(block)` — this is GRC's canonical delete. It cascades: disconnects all ports on the block (`self.disconnect(*element.ports())`), then removes from the blocks list. Also guards against removing the options block. (**NATIVE**: `FlowGraph.remove_element`)
- **All native. No adhoc code.**

**Example:**
```json
"remove_blocks": ["old_source", "unused_var"]
```

---

### update_params

- **Type:** `array[object]`
- **Purpose:** Change parameter values on existing blocks
- **Each entry:**
  - `instance_name` (`string`, **required**) — target block name
  - `params` (`object`, **required**) — param updates keyed by GNU param ID
- **Backend logic:**
  1. Look up block: `flow_graph.get_block(instance_name)` (**NATIVE**: `FlowGraph.get_block`)
  2. For each key/value: check `key in block.params` (raises `KeyError` if unknown), then `block.params[key].set_value(str(value))` (**NATIVE**: `Param.set_value`)
- **All native. No adhoc code.**

**Example:**
```json
"update_params": [
  {"instance_name": "samp_rate", "params": {"value": "48000"}},
  {"instance_name": "analog_sig_source_x_0", "params": {"amp": "gain_value"}}
]
```

---

### update_states

- **Type:** `array[object]`
- **Purpose:** Change block enable/disable/bypass state
- **Each entry:**
  - `instance_name` (`string`, **required**) — target block name
  - `state` (`enum`, **required**) — `"enabled"`, `"disabled"`, or `"bypass"`
- **Backend logic:**
  1. Look up block: `flow_graph.get_block(instance_name)` (**NATIVE**)
  2. Alias map: `{"bypass": "bypassed"}` — translates model-friendly `"bypass"` to GRC's canonical `"bypassed"` (**ADHOC**: GRC has no alias system; the `state` setter accepts anything, and the getter silently coerces unknown values to `"enabled"`)
  3. Validate: `canonical not in block.STATE_LABELS` — raises `ValueError` if invalid (**NATIVE**: `Block.STATE_LABELS`)
  4. Set: `block.state = canonical` (**NATIVE**: `Block.state` setter)

**Example:**
```json
"update_states": [
  {"instance_name": "analog_noise_source_x_0", "state": "disabled"}
]
```

---

### add_connections

- **Type:** `array[string]`
- **Purpose:** Add data-flow edges between block ports
- **Format:** `"src_block:port->dst_block:port"`
- **Backend logic:**
  1. Parse string: `parse_connection_id("src:0->dst:1")` → `{src_block, src_port, dst_block, dst_port}` (**ADHOC**: GRC has no string connection format; native form is a 4-tuple)
  2. Find source port: `flow_graph.get_block(src_block)` → scan `block.active_sources` matching `p.key == src_port` (**ADHOC**: GRC's `get_source(key)` searches ALL ports including hidden ones, and has a bug returning `ValueError` instead of raising)
  3. Find sink port: same as above but on `active_sinks` (**ADHOC**: same reason)
  4. Connect: `flow_graph.connect(src_port, dst_port)` — creates a `Connection` object and adds it to the graph (**NATIVE**: `FlowGraph.connect`)
  5. On failure: `_connection_dtype_hint` extracts `port.dtype` from both ends and appends `"Set type='float' on 'block_name'"` if the new block is missing its type (**ADHOC** formatting + **NATIVE** `port.dtype`, `port.parent_block.name`)

**Example:**
```json
"add_connections": [
  "analog_sig_source_x_0:0->mid_throttle:0",
  "mid_throttle:0->blocks_add_xx:0"
]
```

---

### remove_connections

- **Type:** `array[string]`
- **Purpose:** Remove data-flow edges
- **Format:** same as add_connections — `"src_block:port->dst_block:port"`
- **Backend logic:**
  1. Parse string: `parse_connection_id(...)` (**ADHOC**: same parser as add)
  2. Find the exact `Connection` object: scan `flow_graph.connections` matching all four fields (`source_block.name`, `source_port.key`, `sink_block.name`, `sink_port.key`) (**ADHOC**: GRC's `disconnect(*ports)` removes ALL edges touching a port; there's no native single-edge lookup by endpoints)
  3. Remove: `flow_graph.remove_element(connection)` — removes the single edge from the connections set (**NATIVE**: `FlowGraph.remove_element`)
  4. If edge not found: `KeyError` is caught and skipped silently (idempotent — handles cascade from `remove_block`)
- **Note:** idempotent — if the edge is already gone (e.g. the source block was removed), it's skipped silently

**Example:**
```json
"remove_connections": ["analog_sig_source_x_0:0->blocks_add_xx:0"]
```

---

### force

- **Type:** `boolean`
- **Default:** `false`
- **Purpose:** commit edits even when GRC validation fails
- **Backend logic:**
  - After all operations complete, `validate(fg)` runs `rewrite()` + `validate()` + `is_valid()` (**NATIVE**: all three are GRC core methods)
  - If invalid and `force == false`: snapshot rollback via `restore_session_state` (uses `export_data`/`import_data` — **NATIVE**)
  - If invalid and `force == true`: edits stay applied; validation errors are surfaced in `errors[]` with `code: "gnu_validation"` so the model knows the graph is invalid
  - `force` does NOT bypass adapter errors (`parameter_not_found`, `add_block_failed`, etc.) — those always cause rollback
- **When to use:** set to `true` after a failed attempt with a validation error (e.g. "Port is not connected")

**Example:**
```json
"force": true
```

---

## Validation & rollback

After all operations, validation runs automatically (step 8 in execution order):

- **Validate:** `flow_graph.rewrite()` → `flow_graph.validate()` → `flow_graph.is_valid()` (**ALL NATIVE**)
- **Error collection:** `flow_graph.iter_error_messages()` yields `(element, message)` tuples (**NATIVE**)
- **Error formatting:** `_format_error(elem, msg)` prepends `parent_block.name` — e.g. `"blocks_add_xx: Sink - in2(2): Port is not connected."` (**ADHOC**: GRC's native `get_error_messages()` formats `"Sink - in2(2): msg"` but omits the parent block name, making port errors ambiguous)
- **Rollback:** `capture_session_state` snapshots via `flow_graph.export_data()` (**NATIVE**); `restore_session_state` rebuilds via `platform.make_flow_graph()` + `fg.import_data()` + `fg.rewrite()` (**ALL NATIVE**)
- **Type hint:** `_type_hint_for_validation` checks if any new block has a `type` enum matching the neighbor dtype and appends `"Set type='float' on 'block_name'"` (**ADHOC** inference + **NATIVE** `block.params["type"].options`)

---

## Auto-resolve (automatic, runs between steps 1 and 3)

No model input needed. Runs after `add_blocks`, before `remove_blocks`:

- For each newly-added block where the model did NOT specify `type` in `params`:
  1. Check if block has a `type` param: `"type" in block.params` (**NATIVE**)
  2. Scan the batch's `add_connections` list for a connection involving this block (**ADHOC**: scans pending connections, not live graph — GRC's `Port.connections()` only sees already-applied connections)
  3. Look up the neighbor block: `flow_graph.get_block(neighbor_name)` (**NATIVE**)
  4. Get neighbor's port dtype: `block.active_sources`/`active_sinks` → `port.dtype` (**ADHOC** port scan + **NATIVE** `Port.dtype`)
  5. Set type: `block.params["type"].set_value(dtype)` (**NATIVE**: `Param.set_value`)
  6. Report in response: `"auto_resolved": {"block_name": "float"}`

---

## Response shapes

### Success
```json
{"ok": true}
```

### Success with auto-resolved types
```json
{"ok": true, "auto_resolved": {"mid_throttle": "float"}}
```

### Failure — validation
```json
{
  "ok": false,
  "error_type": "gnu_validation_failed",
  "errors": [
    {
      "code": "gnu_validation",
      "message": "blocks_add_xx: Sink - in2(2): Port is not connected.",
      "hint": "Set type='float' on 'mid_throttle'"
    }
  ]
}
```

### Failure — adapter error
```json
{
  "ok": false,
  "error_type": "tool_call_invalid",
  "errors": [
    {"code": "parameter_not_found", "message": "Param 'samp_rate' not in block 'mid_throttle'"}
  ]
}
```

### Success with force (graph invalid but applied)
```json
{
  "ok": true,
  "errors": [
    {"code": "gnu_validation", "message": "blocks_add_xx: Sink - in2(2): Port is not connected."}
  ]
}
```

---

## Complete example

```json
{
  "add_blocks": [
    {
      "block_id": "blocks_throttle",
      "instance_name": "mid_throttle",
      "params": {"samples_per_second": "samp_rate", "type": "float"}
    }
  ],
  "remove_connections": ["analog_sig_source_x_0:0->blocks_add_xx:0"],
  "add_connections": [
    "analog_sig_source_x_0:0->mid_throttle:0",
    "mid_throttle:0->blocks_add_xx:0"
  ]
}
```

This single call:
1. Adds a throttle block with params
2. Removes the original edge
3. Adds two new edges routing through the throttle
4. Validates the result atomically
5. Returns `{"ok": true}` if valid, or rolls back entirely if not
