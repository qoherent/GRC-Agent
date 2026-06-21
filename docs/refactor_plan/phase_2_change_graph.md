# Phase 2 — `change_graph` (Experiment Only)

> **Predecessor:** Phase 1 (`inspect_graph` experiment done; `wire_shape_proposal.md` available).
> **Successor:** Phase 3 (`query_knowledge` experiment).
> **Goal:** **Experiment only.** Create `playground/change_graph_experiment/`, exercise the 5 canonical mutations against the 9 fixtures, run the same mutations natively via the GRC platform, and write `analysis.md` with the legacy-vs-native behavior gaps. **Do NOT rewrite `change_graph.py` and do NOT create the native adapter.** Both happen in Phases 5 and 6 as part of the single cutover.

> **Why "experiment only":** per the consultant's architectural review, building a working `change_graph` on top of a thin adapter before the adapter's mutation methods exist creates a split-brain state. The validation must happen on the same native `FlowGraph` object the mutation operates on. The experiment proves the native API can do the 5 mutations; the actual code that uses the API is built in Phase 5 (adapter) and applied in Phase 6 (single cutover).

---

## 0. Cross-Phase Context

Before starting, read `plan_context.md` in full. Inherit:
- §3 (MVP surface)
- §4 (aggressive redesign rules)
- §5 (verified environment facts)
- §6.1 (legacy surface)
- §8.1, §8.2 (env, load-path edge cases)
- §10 (commit cadence)

Also re-read:
- `playground/inspect_experiment/wire_shape_proposal.md` (the wire shape Phase 1 proved out)
- `docs/GNU_NATIVE_METHODS.md` §1.3 (`FlowGraph` — `connect`, `disconnect`, `new_block`, `import_data`, `export_data`, `rewrite`, `validate`)
- `docs/GNU_NATIVE_METHODS.md` §5 (validation, error bubbling, suppression of disabled/bypassed children)

---

## 1. The Experiment (Create New)

**Create directory:** `playground/change_graph_experiment/`

```
playground/change_graph_experiment/
├── run_experiment.py          # runs OLD change_graph on canonical mutations
├── verify_native_api.py        # uses native GRC Platform to apply the same mutations
├── fixtures/                   # symlink or copy from tests/data/
├── results/                    # 5 mutations × 9 fixtures = 45 markdown files
└── analysis.md                 # the gap report
```

### 1.1 `run_experiment.py` (legacy)

Pick **5 canonical mutations** that exercise the six `op_type`s in `_FLAT_BATCH_FIELDS` (the `change_graph` input shape):

1. **add_blocks**: add a new `analog_sig_source_x` block with parameters and a state.
2. **remove_blocks**: remove an existing block (one with no incoming/outgoing connections).
3. **update_params**: change `samp_rate` from `32000` to `48000`.
4. **update_states**: set an existing block to `disabled`.
5. **add_connections** + **remove_connections**: rewire a throttle to a different sink.

For each mutation, against each of the 9 fixtures in `tests/data/`:
- Load the fixture via the agent's `load_grc`.
- Apply the mutation via `agent._change_graph(**payload)`.
- Capture the tool result.
- Capture the post-mutation `session.validation_state()` (which currently runs `grcc`).
- Write a markdown report to `results/<fixture>_<mutation>.md` showing: input `.grc`, payload, tool result, validation state, rollback or error paths.

### 1.2 `verify_native_api.py` (native)

For each of the 5 mutations, against each of the 9 fixtures:
- Use the GRC platform directly to load the fixture.
- Apply the same logical mutation against the **native `FlowGraph` object**:
  - `add_blocks` → `flow_graph.new_block(block_type)` + param assignment via `block.params['k'].set_value(v)` + state via `block.state = ...`
  - `remove_blocks` → find the block by name; remove it from the flow graph's block list (use GRC's removal API; if it doesn't have one, document and propose a workaround)
  - `update_params` → `block.params['k'].set_value(v)`
  - `update_states` → `block.state = 'disabled' | 'enabled' | 'bypassed'`
  - `add_connections` → `flow_graph.connect(src_port, dst_port)`
  - `remove_connections` → `flow_graph.disconnect(src_port, dst_port)`
- Call `flow_graph.rewrite()` to refresh namespaces.
- Call `flow_graph.validate()` and capture `flow_graph.is_valid()` and `flow_graph.iter_error_messages()`.
- Write a JSON to `results_native/<fixture>_<mutation>.json` showing: the same payload (translated to native calls), the new `flow_graph` state, the validation result.

### 1.3 `analysis.md`

Document the diff:

- Which mutations required new GRC API calls that the legacy YAML path didn't have?
- Which validation errors does `flow_graph.validate()` catch that the legacy `grcc` didn't? (E.g., a malformed parameter type that `grcc` accepted.)
- Which validation errors does the legacy `grcc` catch that `flow_graph.validate()` doesn't? (E.g., Python compile-time errors from `grcc` that the GRC runtime validator skips.)
- Are there mutations the native path **cannot** do that the legacy can? (E.g., removing a block — does GRC's `Block` have a removal method, or do we have to mutate `flow_graph.blocks` directly?)
- What is the latency difference per mutation between legacy (YAML round-trip + `grcc` subprocess) and native (in-process `FlowGraph` + `validate()`)?

This is the input to Phase 5's adapter design.

---

## 2. What This Phase Does (and Does NOT Do)

### 2.1 In scope

1. Create `playground/change_graph_experiment/`.
2. Run the 5 mutations × 9 fixtures through the legacy `change_graph`.
3. Run the same mutations natively through the GRC platform.
4. Write `analysis.md` with the diff and any behavior gaps.
5. If a behavior gap appears, **stop and ask the maintainer**. Do not silently change behavior.

### 2.2 Out of scope (deferred to later phases)

- **Do NOT touch `src/grc_agent/runtime/change_graph.py`.** The legacy `change_graph` continues to operate exactly as it does today.
- **Do NOT create `src/grc_agent/grc_native_adapter.py`.** That's Phase 5.
- **Do NOT create `src/grc_agent/domain_models.py`.** That's Phase 4.
- **Do NOT touch `src/grc_agent/flowgraph_session.py`.** Phase 6.
- **Do NOT touch `src/grc_agent/transaction.py`.** Phase 6.
- **Do NOT touch the mutation pipeline.** Phase 6.
- **Do NOT touch any test file.**
- **Do NOT touch the Qt GUI.** Phase 7.

The only files modified by this phase live under `playground/change_graph_experiment/`. The agent's source tree is unchanged.

---

## 3. The Mutation Set (Proposal — to be codified in Phase 5)

The experiment proves out whether the native API supports each of these mutations cleanly. If any mutation requires a workaround, the subagent must document it in `analysis.md` and flag for the maintainer. Do not assume the answer.

| op_type | Native API call | Question for the experiment |
|---|---|---|
| `add_block` | `flow_graph.new_block(block_type)` + `block.params[k].set_value(v)` | Does `new_block` register the block in `flow_graph.blocks`? Is the auto-generated name unique? |
| `remove_block` | TBD — possibly mutation of `flow_graph.blocks` | Does GRC expose a `remove_block` method? If not, what is the canonical workaround? |
| `update_params` | `block.params[k].set_value(v)` | Does `set_value` invalidate caches and force re-evaluation on next `rewrite()`? |
| `update_states` | `block.state = 'enabled' | 'disabled' | 'bypassed'` | Is the property settable? Does setting it trigger rewrite? |
| `add_connection` | `flow_graph.connect(src_port, dst_port)` | Does `connect` validate port types? Does it raise on port-type mismatch? |
| `remove_connection` | `flow_graph.disconnect(src_port, dst_port)` | Does `disconnect` accept a port, or does it need both endpoints? |

If any of these answers is "no, the API doesn't support this cleanly," the experiment documents the gap and the maintainer decides:
- Add a workaround to Phase 5's adapter (e.g., mutate `flow_graph.blocks` directly).
- Refuse the mutation in the agent (return `ok=False`, `errors=[{"code": "UNSUPPORTED_OP", "message": "..."}]`).
- Defer to a future phase.

---

## 4. Step-by-Step

### 4.1 Experiment (Days 1–3)

- [ ] **Step 1:** Create `playground/change_graph_experiment/`. Copy `run_experiment.py` and `verify_native_api.py` from `playground/inspect_experiment/` as a starting template.
- [ ] **Step 2:** Write `run_experiment.py` to apply the 5 canonical mutations against the 9 fixtures. Capture tool result + validation state for each.
- [ ] **Step 3:** Write `verify_native_api.py` to apply the same 5 mutations natively. Capture the new `flow_graph` state + validation result for each.
- [ ] **Step 4:** Write `playground/change_graph_experiment/analysis.md`. Document the diff per the questions in §1.3.
- [ ] **Step 5:** For each behavior gap, classify it as: (a) **drop** (native is correct, legacy was lenient), (b) **keep** (legacy is correct, native is too strict), (c) **deviation** (neither is correct, maintainer decides). For (c), stop and ask.
- [ ] **Step 6:** Measure per-mutation latency (legacy vs native). The consultant's concern is that round-tripping a legacy dict to a native object just to validate it adds latency. The experiment must prove that the native path (in-process) is faster than the legacy path (YAML + `grcc` subprocess). Save the numbers in `analysis.md` §"Latency".

### 4.2 Commit (Day 3)

- [ ] **Step 7:** Commit the experiment + analysis. Convention: `chore(phase-2/change): refresh mutation experiment and document legacy-vs-native gaps`.
- [ ] **Step 8:** Verify the agent's source tree is unchanged: `git diff --stat src/grc_agent/`. Zero matches.
- [ ] **Step 9:** Verify the legacy `change_graph` still passes its tests: `pytest -m "not grc_native and not gui and not llama_eval" -x`. All pass.

---

## 5. Files to Touch

### 5.1 Creates

- `playground/change_graph_experiment/run_experiment.py`
- `playground/change_graph_experiment/verify_native_api.py`
- `playground/change_graph_experiment/analysis.md`
- `playground/change_graph_experiment/fixtures/` (or use `tests/data/` via symlink)
- `playground/change_graph_experiment/results/*.md` (5 mutations × 9 fixtures)
- `playground/change_graph_experiment/results_native/*.json` (same)

### 5.2 Modifies

Nothing.

### 5.3 Deletes

Nothing.

### 5.4 Untouched (deferred to later phases)

- `src/grc_agent/runtime/change_graph.py` (Phase 6)
- `src/grc_agent/flowgraph_session.py` (Phase 6)
- `src/grc_agent/transaction.py` (Phase 6)
- `src/grc_agent/grc_native_adapter.py` (Phase 5 — does not exist yet)
- `src/grc_agent/domain_models.py` (Phase 4 — does not exist yet)
- All other source files
- All test files
- The Qt GUI and its tests (Phase 7)

---

## 6. Verification Gate

The phase is done when **all** of the following hold:

- [ ] Both experiment scripts run cleanly and produce 9 fixture outputs each.
- [ ] `playground/change_graph_experiment/analysis.md` exists and documents:
  - The legacy-vs-native diff for all 5 mutations × 9 fixtures.
  - The per-mutation latency comparison.
  - The list of mutations the native API can do cleanly vs requires a workaround.
  - The list of behavior gaps classified as (a) drop / (b) keep / (c) deviation.
- [ ] `git diff --stat src/grc_agent/` returns zero matches. The source tree is unchanged.
- [ ] `pytest -m "not grc_native and not gui and not llama_eval"` passes. The legacy `change_graph` is unchanged.
- [ ] `tests/test_change_graph_flat_batch.py` and `tests/test_graph_safety_regressions.py` pass.
- [ ] If a (c) deviation was required, `phase_2_deviation.md` exists and the maintainer has signed off.

---

## 7. Edge Cases Specific to This Phase

| Edge case | Symptom | Mitigation |
|---|---|---|
| `flow_graph.new_block(block_type)` raises because the block is not in the catalog | The mutation pipeline crashes | Document in `analysis.md`. Phase 5 will codify the error path. |
| The native API has no `remove_block` method; we have to mutate `flow_graph.blocks` directly | The mutation requires a workaround | Document in `analysis.md`. Phase 5 will codify the workaround (e.g., `flow_graph.blocks.remove(block)`). The maintainer may push back. |
| The native API has no `set_value` on a Param | The mutation requires a workaround | Document in `analysis.md`. Per `docs/GNU_NATIVE_METHODS.md` §2.4, `Param` has the `Evaluated` descriptor, but `set_value` may not be a public method. The experiment must determine the actual API. |
| `flow_graph.validate()` accepts a mutation that `grcc` rejects (e.g., a Mako template that compiles but produces wrong DSP) | The new validation is more lenient | Document in `analysis.md`. The native evaluation is more accurate (it evaluates the namespace, not the syntax). The maintainer decides whether to expose `flow_graph.generate()` for compile-time confidence. |
| `flow_graph.validate()` rejects a mutation that `grcc` accepts (e.g., a malformed parameter type) | The new validation is stricter | Document in `analysis.md`. Native is correct; legacy was lenient. The maintainer approves. |
| The latency comparison shows the native path is **slower** than the legacy path | The consultant's concern is wrong for this case | Document in `analysis.md`. The native path may be slower for tiny graphs (process startup dominates), but faster for large graphs (no subprocess). The agent should still prefer native (no daemon, no stale state). |
| The `connect` API requires both `Port` objects, not port names | The mutation requires a lookup | Document. The agent's adapter will need a `find_port(block, port_name)` helper. |
| The `state` property is read-only (some versions of GRC) | The mutation requires a workaround | Document. Phase 5 will codify the workaround (e.g., `block._state = 'disabled'`, or refuse the mutation). |
| The experiment reveals a mutation that is impossible in the native API | The agent cannot support that mutation | Document. The maintainer decides: drop the mutation, or keep the legacy code path for that one op_type. **Do not silently keep the legacy code path.** |

---

## 8. Handoff

When this phase finishes:

1. The implementing agent commits with the convention `chore(phase-2/change): refresh mutation experiment and document legacy-vs-native gaps`.
2. The implementing agent writes `docs/refactor_plan/phase_2_handoff.md` with:
   - The `analysis.md` summary (or a copy of it)
   - The legacy-vs-native validation diff highlights
   - The per-mutation latency numbers
   - Any (c) deviation documentation
   - Confirmation that the agent's source tree is unchanged
3. The next phase is Phase 3 (`query_knowledge` experiment). The Phase 3 subagent creates `playground/query_knowledge_experiment/` following the same pattern. **No source-code changes.**
