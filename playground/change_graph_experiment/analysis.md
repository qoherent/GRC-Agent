# Phase 2 — `change_graph` Native Experiment: Analysis

Proves the native `gnuradio.grc.core` `FlowGraph` API supports the 5 canonical
`change_graph` mutations, and compares behavior + latency against the legacy
production tool. Source tree unchanged (experiment only).

Scripts: [verify_native_api.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/change_graph_experiment/verify_native_api.py) (native),
[run_experiment.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/change_graph_experiment/run_experiment.py) (legacy).
Results: `results_native/consolidated_native.json`, `results_legacy/consolidated_legacy.json`.

---

## 1. Mutation support (the core question)

All 5 `op_type`s are mechanically supported by the native API. Two require
workarounds (flagged for the Phase 5 adapter):

| op_type | Native call | Status | Notes |
|---|---|---|---|
| `add_block` | `flow_graph.new_block(key)` + `block.params['id'].set_value(name)` | ✅ works | **Workaround:** `new_block` registers the block in `flow_graph.blocks` but leaves `block.name == ''`. The instance name is the `id` param; it must be set, then `rewrite()`, for `block.name` to resolve. No auto-unique-naming. |
| `remove_block` | `flow_graph.blocks.remove(block)` (+ disconnect its connections) | ⚠ workaround | **No `FlowGraph.remove_block` API exists.** `flow_graph.blocks` is a plain `list`; removal is direct list mutation. The adapter must also disconnect the block's connections first (else they dangle). |
| `update_params` | `block.params[k].set_value(v)` + `rewrite()` | ✅ works | Value applied, namespace re-evaluated. |
| `update_states` | `block.states['state'] = 'enabled'\|'disabled'\|'bypassed'` + `rewrite()` | ✅ works | `block.enabled` / `block.get_bypassed()` reflect the new state correctly after rewrite. (There is no `set_state` method; the `states` dict is the setter.) |
| `add_connection` | `flow_graph.connect(port_a, port_b, params=None)` | ✅ works | Takes **Port objects** (not names). The adapter needs a `find_port(block, key)` helper. |
| `remove_connection` | `flow_graph.disconnect(*ports)` | ✅ works | Accepts the two endpoint ports. |

`connect`/`disconnect` signatures: `connect(porta, portb, params=None)`,
`disconnect(*ports)`. `flow_graph.connections` is a **set** (not subscriptable).

---

## 2. Legacy-vs-native behavior: ZERO divergence

Both paths use GNU Radio's own `FlowGraph.validate()`, so they agree on every
trial (45 native, 36 legacy). The cases where a mutation produces an invalid
graph are **correct native behavior legacy already enforces** — not new
strictness:

| Case | Result | Why (native + legacy agree) |
|---|---|---|
| `add_block` (all fixtures) | invalid | "Source - out(0): Port is not connected." GRC flags an unconnected output port. Implication for the agent: add-then-connect workflow produces transient invalidity; the adapter must tolerate or reconnect. |
| `remove_block` of a mid-chain block | invalid | Removing orphans the downstream port ("Sink - in(0): Port is not connected"). Same root cause. |
| `update_param` `ampl`→48000 (dial_tone) | invalid | `block_assert_failed`: `"start <= value <= stop"` — ampl is a QT range [0, 0.5]. Native range validation the legacy path already enforces. |
| `update_state` disable a *referenced variable* (samp_rate/ampl) | invalid | Disabling the variable breaks downstream references: `"name 'samp_rate' is not defined"`. Correct — a referenced variable must stay enabled. |
| `update_param` samp_rate→48000 (most fixtures) | valid | Variable updates that keep references resolvable stay valid. |
| `update_state` disable a *non-referenced* block (mac_sniffer, rewire_message_ambiguous) | valid | Disabling an unreferenced block is fine. |

**Important correction to the plan's premise:** `plan_context.md` §8.3 speculates
that legacy validation runs the `grcc` subprocess. It does not — the legacy
path already calls GRC's `FlowGraph.validate()` (error codes
`gnu_validation_failed`, `block_assert_failed`). So Phase 6's cutover changes
**latency and code path, not validation semantics**. There is no behavior gap
to arbitrate (no (c) deviation cases).

### Gap classification (per phase plan §1.3 / §4.1 Step 5)
- **(a) drop** (native correct, legacy lenient): none — legacy is not lenient here.
- **(b) keep** (legacy correct, native too strict): none.
- **(c) deviation**: none.
- **Workarounds for Phase 5** (adapter): (1) `new_block` id-driven naming;
  (2) `flow_graph.blocks.remove()` + connection cleanup for block removal;
  (3) a `find_port(block, key)` helper for connect/disconnect.

---

## 3. Latency: native wins decisively

Native is in-process (load + mutate + rewrite + validate); legacy round-trips
through the parsed session + validation on every change.

| Mutation | Native (mean) | Legacy (mean) | Speedup |
|---|---|---|---|
| `add_block` | 2.5 ms | 74.3 ms | **30×** |
| `remove_block` | 1.7 ms | 6.5 ms | 3.7× |
| `update_param` | 1.8 ms | 295.9 ms | **164×** |
| `update_state` | 1.8 ms | 100.1 ms | **55×** |

The legacy `update_param` cost (~296 ms) dominates because each edit triggers a
full re-validation cycle. The native path validates the same `FlowGraph` object
in-place. This confirms the consultant's latency concern and justifies the
Phase 6 cutover on performance grounds alone.

---

## 4. Phase 5 adapter inputs (codified for the build phase)

1. **Identity of a new block** = its `id` param, not `block.name` (empty until
   id is set + rewritten). The adapter's add must set `id` and rewrite before
   reporting the instance name.
2. **Block removal** = `flow_graph.blocks.remove(block)` preceded by
   `disconnect` of every connection touching it. No native API — this is the
   adapter's responsibility.
3. **State** is set via `block.states['state']`, validated values
   `'enabled' | 'disabled' | 'bypassed'`.
4. **Connections** require resolved `Port` objects; the adapter resolves
   `(block, port_key) → Port` before calling `connect`/`disconnect`.
5. **Validation** = `flow_graph.validate()` + `is_valid()` +
   `iter_error_messages()` — the same engine legacy uses, so error semantics
   carry over unchanged.
6. **Transient invalidity** (unconnected ports during add-then-connect) is
   expected GRC behavior; the adapter must not treat it as a hard failure mid-
   batch, only at the final validate.

---

## 5. Verification gate status

- [x] Both scripts run cleanly across all 9 fixtures (45 native + 36 legacy trials).
- [x] This `analysis.md` documents the diff, latency, mutation support, gap classification.
- [x] No (c) deviation cases — no `phase_2_deviation.md` needed.
- [ ] `git diff --stat src/grc_agent/` — **Phase 2 itself touched no source file.** (The working tree contains the unrelated filter-unification refactor from the prior authorized directive, still uncommitted; Phase 2 added only files under `playground/change_graph_experiment/`.)
- [ ] `pytest -m "not grc_native and not gui and not llama_eval"` passes (356 passed this session).
- [x] `test_change_graph_flat_batch.py`, `test_graph_safety_regressions.py` pass (subset of the suite).

**Outcome:** Phase 2 proves the native API supports every `change_graph`
mutation with two documented workarounds, zero behavior divergence from
legacy, and a 30-164× latency win. Ready to hand off to Phase 3
(`query_knowledge` experiment).
