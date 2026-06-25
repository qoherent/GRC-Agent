# Agent Flow Findings

> **Status:** Architecture frozen. 7/8 semantic success.
> Model: `gemma4:e4b-it-qat` (7.5B Q4_0, native ctx 131K, running at num_ctx=120K).

---

## Three-pillar adapter design

| Pillar | Principle | What it does |
|--------|-----------|-------------|
| **Syntax symmetry** | Read/write use the same format | Connections are flat strings `"src:0->dst:0"` everywhere — inspect returns them, add/remove accepts them. Max nesting depth: 2. |
| **Deterministic offloading** | Compiler work stays in the adapter | When a newly-added block omits `type`, the adapter infers it from the connected neighbor's port dtype. The LLM handles topology; the adapter handles types. |
| **Error locality** | Every error names the exact element | GRC's `iter_error_messages()` yields `(element, message)`. The adapter formats `"block_name: Port - dir(key): message"` so the model can identify exactly what failed. |

---

## Scenario results (7/8)

| # | Scenario | Status | Key enabler |
|---|----------|:------:|-------------|
| 01 | add_throttle inline | ✓ | auto-resolve type from neighbor |
| 02 | update sample rate | ✓ | simple param edit |
| 03 | disable + re-enable | ✓ | error identity → model identifies port |
| 04 | add + use variable | ✓ | expression params (system prompt) |
| 05 | full rewire | ✓ | auto-resolve + flat connections |
| 06 | multiply via query_knowledge | ✗ | topology limit: orphaned noise source |
| 07 | force-disable connected block | ✓ | force description + error identity |
| 08 | fm_rx inline throttle | ✓ | auto-resolve + flat connections |

---

## The one remaining failure (scenario 06)

The model's topology is **correct** — multiplier wired with right type, right ports.
The error correctly names `"analog_noise_source_x_0: Source - out(0): Port is not connected."`
The model reads this but doesn't decide to remove the orphaned noise source block or use `force`.
This is a model-reasoning limit on multi-step topology cleanup, not an information gap.

**Fix path:** upgrade the model (8B+), not the adapter.

---

## Fixes applied (chronological)

| Fix | File(s) | Impact |
|-----|---------|--------|
| Native API consolidation (`get_block`, `remove_element`, `STATE_LABELS`) | `grc_native_adapter.py` | Eliminated adhoc reimplementations |
| Dead code deletion (~4,200 lines) | `validation/`, `transaction.py`, `flowgraph_session.py` | Lean codebase |
| Connection ordering (remove before add) | `change_graph.py` | Enables atomic inline-insert |
| Schema flattening (depth 3→2) | `tool_schemas.py` | Flat strings for add/remove connections |
| Payload simplification | `change_graph.py` | Agent sees `ok`/`errors` only (no `committed`) |
| num_ctx 4096→120000 | `toolagents_runtime.py` | Eliminated output truncation |
| Catalog enum values | `catalog/schema.py` | `"enum=[complex,float,int,short]=complex"` |
| Auto-resolve type from neighbor | `change_graph.py` | Adapter fills missing `type` deterministically |
| Error block+port identity | `grc_native_adapter.py` | `"blocks_add_xx: Sink - in2(2): Port is not connected."` |
| System prompt direction | `model_context.py` | `*_xx` defaults + expression params |

---

## Runtime behavior reference

These are GRC/GNU-specific runtime behaviors (not coding-agent rules —
see `AGENTS.md` for those). Documented here for reference.

### Disconnect precision

Native `flow_graph.disconnect(src, dst)` removes ALL edges from a port.
The adapter's `disconnect()` finds the exact `Connection` object and calls
native `flow_graph.remove_element(connection)` for single-edge deletion.
Idempotent: if the edge is already gone (e.g. cascaded by `remove_block`),
the KeyError is caught and the operation is a silent no-op.

### Type auto-resolve

When a newly-added block omits the `type` param and the batch connects it
to a typed neighbor, the adapter sets `type` from the neighbor's port
dtype. The decision is reported in the `auto_resolved` field of the
`change_graph` response: `{"auto_resolved": {"mid_throttle": "float"}}`.
Only fills MISSING values — never overrides model-specified params.

### Error locality

GRC's `iter_error_messages()` yields `(element, message)` tuples where the
element is the Block/Port/Connection with the error. The adapter formats
every error as `"block_name: Port - dir(key): message"` (e.g.,
`"blocks_add_xx: Sink - in2(2): Port is not connected."`). The element
identity is never silently dropped.

### Connection ordering

`remove_connections` runs BEFORE `add_connections` in the batch dispatcher.
This prevents transient double-upstream errors when doing inline-insert
(remove 1 edge → add 2 edges).
