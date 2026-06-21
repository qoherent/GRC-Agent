# Phase 7 — GUI Burn

> **Predecessor:** Phase 6 (`flowgraph_session.py` gutted; `inspect_graph` emits the new flat shape).
> **Successor:** none (refactor complete).
> **Goal:** Update the Qt GUI inspector and its 8 tests to consume the new flat payload. Delete the `_block_params` sidecar. Update the eval chat harness stub. The 8 GUI tests pass against the new shape; the GUI renders `random_bit_generator.grc` correctly.

---

## 0. Cross-Phase Context

Before starting, read `plan_context.md` in full. Inherit:
- §3 (MVP surface)
- §4 (aggressive redesign rules)
- §6.2 (GUI files)
- §8.4 (GUI/display edge cases — the most important section for this phase)
- §10 (commit cadence)

Also re-read:
- The handoff doc from Phase 6 to understand the new `inspect_graph` wire shape.
- `playground/inspect_experiment/wire_shape_proposal.md` (the model-visible payload shape proved out in Phase 1's experiment).
- `src/grc_agent_gui/inspector.py` (the Qt widget to update).
- `src/grc_agent_gui/main_window.py:233–244` (the `_block_params` sidecar producer).
- `tests/gui/test_inspector_widget.py` (the 8 tests to rewrite).

---

## 1. The Burn List

### 1.1 `src/grc_agent_gui/inspector.py`

The `InspectorWidget.update_state` method (lines 140–252) currently reads:

```python
summary = inspect_graph_data.get("summary", {}) or {}
blocks = summary.get("blocks", []) or []
connections = summary.get("connections", []) or []
block_params = inspect_graph_data.get("_block_params", {}) or {}
```

After the burn, it reads:

```python
graph = inspect_graph_data.get("graph", inspect_graph_data.get("summary", {}))  # defensive
blocks = graph.get("blocks", []) or []
connections = graph.get("connections", []) or []
# No more block_params sidecar.
```

The inner loop that builds the per-block parameter tree (currently `for pkey, pval in params.items()` on the sidecar dict) becomes:

```python
for param in block.get("parameters", []):
    key = param.get("name", "")
    val = param.get("evaluated_value", param.get("value", ""))
    # ... add to the tree node
```

The `category_for_role` mapping at lines 201–205 is updated to use the new `BlockRole` enum values:

| Old string | New enum value |
|---|---|
| `"variable_or_control"` | `"variable"` |
| `"source"` | `"source"` |
| `"sink"` | `"sink"` |
| (others fall through) | `"transform"`, `"virtual_or_pad"`, `"import"`, `"snippet"`, `"options"`, `"other"` |

```python
category_for_role = {
    "variable": "variables",
    "source": "sources",
    "sink": "sinks",
}
# "transform", "virtual_or_pad", "import", "snippet", "options", "other" all fold into "other_blocks"
```

The `validation_result` access at `main_window.py:1009` becomes `overview_data.get("graph", {}).get("validation", {}).get("status", "unknown")`.

### 1.2 `src/grc_agent_gui/main_window.py`

The `InspectorRunnable.run` body (lines 233–244) currently constructs the `_block_params` sidecar:

```python
overview_data = inspect_graph(self.agent, view="overview", targets=[], params=[])
if self.agent.session and self.agent.session.flowgraph:
    params_map = {}
    for b in self.agent.session.flowgraph.blocks:
        p = b.params.get("parameters", None)
        if p:
            params_map[b.instance_name] = p
    overview_data["_block_params"] = params_map
self.signals.finished.emit(overview_data)
```

After the burn:

```python
overview_data = inspect_graph(self.agent, view="overview", targets=[], params=[])
self.signals.finished.emit(overview_data)
```

**No sidecar.** The parameters are already inlined into each block dict by `inspect_graph` (per Phase 1's wire shape).

### 1.3 `tests/gui/test_inspector_widget.py`

All 8 tests construct mock payloads in the legacy shape. Each must be rewritten.

For example, `test_variables_table_mapping` (line 19) currently builds:

```python
mock_payload = {
    "ok": True,
    "view": "overview",
    "state_revision": 1,
    "summary": {
        "blocks": [
            {"instance_name": "samp_rate", "block_type": "variable", "role": "variable_or_control", "value": "32000"},
            ...
        ]
    }
}
```

It becomes:

```python
mock_payload = {
    "ok": True,
    "view": "overview",
    "state_revision": 1,
    "graph": {
        "blocks": [
            {
                "instance_name": "samp_rate",
                "block_type": "variable",
                "block_uid": "samp_rate_0",
                "role": "variable",
                "state": "enabled",
                "parameters": [
                    {"name": "value", "dtype": "int", "value": "32000",
                     "category": "General", "hide": "none"},
                ],
            },
            ...
        ],
        "connections": [],
        "validation": {"status": "valid", "errors": []},
    },
}
```

The other 7 tests follow the same pattern.

### 1.4 `tests/eval_chat/harness.py`

The `tool_stubs["inspect_graph"]` fixture at lines 28–43 currently uses the legacy shape. Update to the new shape. One-line change per fixture.

### 1.5 `tests/eval_chat/fixtures/*.json`

Audit each fixture for `summary.blocks` references. Update to `graph.blocks` if found. Per Phase 0's audit, only 1–2 fixtures are likely affected.

### 1.6 `src/grc_agent_gui/process_manager.py`

`process_manager.py:45` reads `session.flowgraph.metadata["options"]["parameters"]["id"]`. The new `flow_graph` object does not have a `metadata` attribute. **Update to read `grc_native_adapter.load_and_inspect(path).blocks` and find the `options` block, then read its `id` parameter.** This is a small, isolated change.

---

## 2. Step-by-Step

### 2.1 Inspector widget (Day 1)

- [ ] **Step 1:** In `src/grc_agent_gui/inspector.py`, update the data-access block in `InspectorWidget.update_state` per §1.1.
- [ ] **Step 2:** Update the inner parameter-tree loop to iterate `block["parameters"]` (a list) instead of `block_params[name]` (a dict).
- [ ] **Step 3:** Update the `category_for_role` mapping to the new `BlockRole` enum values.
- [ ] **Step 4:** Add a defensive fallback: `graph = data.get("graph", data.get("summary", {}))`. This makes the inspector forward-compatible with any tool that hasn't yet migrated.
- [ ] **Step 5:** Run `pytest -m gui tests/gui/test_inspector_widget.py -x` under `xvfb-run`. **Expected to fail** because the tests still use the old shape. That's the next step.

### 2.2 Test rewrites (Day 1)

- [ ] **Step 6:** Rewrite each of the 8 test mocks in `tests/gui/test_inspector_widget.py` per §1.3. The test **names** stay the same. The mock dicts change.
- [ ] **Step 7:** Run `pytest -m gui tests/gui/test_inspector_widget.py -x` under `xvfb-run`. All 8 pass.
- [ ] **Step 8:** If any test asserts on a legacy field that's gone (e.g., `block["value"]` for non-variable blocks), update the assertion.

### 2.3 `main_window.py` and process_manager (Day 2)

- [ ] **Step 9:** Update `InspectorRunnable.run` per §1.2. Delete the `_block_params` sidecar block.
- [ ] **Step 10:** Update `on_inspector_refreshed` at line 1009 per §1.1.
- [ ] **Step 11:** Update `process_manager.py:45` per §1.6.
- [ ] **Step 12:** Run `pytest -m gui -x` under `xvfb-run`. All pass.

### 2.4 Eval chat harness (Day 2)

- [ ] **Step 13:** Update `tests/eval_chat/harness.py:28–43` to use the new shape.
- [ ] **Step 14:** Audit `tests/eval_chat/fixtures/*.json` for `summary.blocks` references. Update if found.
- [ ] **Step 15:** Run `pytest tests/eval_chat/ -x`. All pass.

### 2.5 Manual smoke (Day 3)

- [ ] **Step 16:** Launch the GUI: `python -m grc_agent_gui`.
- [ ] **Step 17:** Open `examples/random_bit_generator.grc`. Wait for the inspector to populate.
- [ ] **Step 18:** Verify the Variables table shows `samp_rate = 32000`.
- [ ] **Step 19:** Expand the Sources category. Verify `analog_random_source_x_0` and `blocks_throttle2_0` (or similar) appear.
- [ ] **Step 20:** Expand any block's parameter tree. Verify no `Advanced` or `Config` parameters appear.
- [ ] **Step 21:** Verify the validation status indicator (green/red dot) is green.
- [ ] **Step 22:** Open a second file (`examples/dial_tone.grc`). Verify the inspector refreshes correctly.
- [ ] **Step 23:** Close and reopen the GUI. Verify the inspector state is restored correctly.

### 2.6 Doc regen (Day 3)

- [ ] **Step 24:** `UPDATE_MODEL_CONTEXT_BIBLE=1 pytest tests/test_model_context_bible.py -v`. Inspect the diff. The model context bible doesn't change in this phase; the diff should be empty.
- [ ] **Step 25:** Commit `chore(phase-7/gui): burn GUI to new flat shape, delete _block_params sidecar`.

### 2.7 Final hygiene (Day 3)

- [ ] **Step 26:** `rg -n '_block_params\|summary\["blocks"\]' src/grc_agent_gui/ tests/gui/ tests/eval_chat/`. Zero matches.
- [ ] **Step 27:** `rg -n 'category_for_role' src/grc_agent_gui/`. The new mapping is in place.
- [ ] **Step 28:** `git tag phase-7-complete`.

---

## 3. Files to Touch

### 3.1 Creates

Nothing.

### 3.2 Modifies

- `src/grc_agent_gui/inspector.py` (data-access block, parameter-tree loop, `category_for_role` mapping)
- `src/grc_agent_gui/main_window.py:233–244, 1009` (delete sidecar, update validation path)
- `src/grc_agent_gui/process_manager.py:45` (read options block via adapter)
- `tests/gui/test_inspector_widget.py` (rewrite 8 mock payloads)
- `tests/eval_chat/harness.py:28–43` (rewrite 1 stub)
- `tests/eval_chat/fixtures/*.json` (audit and update any `summary.blocks` references)
- `docs/MODEL_CONTEXT_BIBLE.md` (regenerated, no expected change)

### 3.3 Deletes

- The `_block_params` sidecar block in `main_window.py:233–244`.
- The `block_params.get(name, {})` lookup in `inspector.py:226–230`.
- Any test assertion on a legacy field (e.g., `block["value"]` for non-variable blocks).

### 3.4 Untouched

- All files in `src/grc_agent/` (Phases 1–6 are complete).
- All other GUI files (`chat_widget.py`, `sidebar_widget.py`, `workers.py`, `model_toolbar.py`, `model_dialog.py`, `__main__.py`).

---

## 4. Verification Gate

The phase is done when **all** of the following hold:

- [ ] `pytest -m "not grc_native and not gui and not llama_eval"` passes.
- [ ] `pytest -m grc_native` passes on the dev box.
- [ ] `pytest -m gui` passes under `xvfb-run`. All 8 `test_inspector_widget.py` tests pass.
- [ ] `pytest tests/eval_chat/` passes. All fixture files use the new shape (or are unchanged because they don't reference `summary.blocks`).
- [ ] The manual smoke run in §2.5 succeeds: `random_bit_generator.grc` renders correctly with the 5 blocks in the right categories, no `Advanced`/`Config` parameters in any tree, validation dot is green.
- [ ] `rg -n '_block_params' src/grc_agent_gui/ tests/gui/ tests/eval_chat/` returns zero matches.
- [ ] `rg -n 'summary\["blocks"\]\|summary\.get\("blocks"\)' src/grc_agent_gui/ tests/` returns zero matches.
- [ ] `git tag phase-7-complete` is set.
- [ ] `docs/MODEL_CONTEXT_BIBLE.md` regenerates cleanly.

---

## 5. Edge Cases Specific to This Phase

| Edge case | Symptom | Mitigation |
|---|---|---|
| A test asserts on `block["value"]` for a non-variable block | The field is gone | Update the assertion. Variable blocks have `value` in their parameters; non-variable blocks have their DSP parameters. |
| A test asserts on a `BlockRole` string that's been renamed (e.g., `"variable_or_control"`) | The test fails | Update the assertion to the new enum value. |
| The GUI launches but the inspector is empty | The data flow is broken | Check the `signals.finished` connection in `main_window.py`. Verify the adapter is returning the new shape. |
| The validation dot is permanently red | The new `validation.status` path is wrong | Check `main_window.py:1009` reads `graph.validation.status`, not `validation_result.status`. |
| The parameter tree shows `Advanced` or `Config` parameters | The visibility filter is broken in the adapter | The filter is in `grc_native_adapter.render_parameter`. Verify it returns `None` for those categories. |
| A user has 30+ parameters in one block | The tree explodes | The existing `_parameter_sample` cap (6 keys) applies. Phase 7 doesn't change the cap. |
| A user opens a `.grc` file with a non-`General` category tab (e.g., `"RF Options"`) | The parameters are visible | The filter excludes only `"Advanced"` and `"Config"`. `"RF Options"` is preserved. |
| The eval chat harness's stub returns the new shape, but the chat-eval assertion is on the old shape | The test fails | Update the assertion. The fixture-level `expect` field in `tests/eval_chat/fixtures/*.json` may need updating. |
| A test patches `inspect_graph` to return the legacy shape | The patch is a no-op | Update the patch to return the new shape. |
| A user has a saved chat history with `inspect_graph` results in the old shape | The chat widget shows stale data | The chat history is JSON-serialized; the new agent reads it as the new shape. The old fields are ignored (Pydantic `extra="forbid"` would reject them, but the chat history loader is not a Pydantic model — it just renders the dict). Verify in the smoke test. |

---

## 6. Handoff — The Refactor Is Complete

When this phase finishes:

1. The implementing agent commits with the convention `refactor(phase-7/gui): <summary>` per commit.
2. The implementing agent writes `docs/refactor_plan/phase_7_handoff.md` with:
   - The list of files modified in this phase
   - The 8 GUI tests' new state
   - The manual smoke test results
   - The tag `phase-7-complete` confirmation
3. The implementing agent writes `docs/refactor_plan/completion_report.md` with:
   - The full Definition of Done checklist (from `plan_context.md` §11) ticked off
   - The total lines deleted from `flowgraph_session.py`
   - The total tests passing across all suites
   - The final `rg` outputs
   - Any deviation from the plan and the maintainer's approval
4. The implementing agent tags the final commit: `git tag refactor-complete`.

---

## 7. The Cross-Phase Definition of Done (Final Check)

This list is the final gate for the entire refactor. **Every box must be ticked.**

- [ ] `pytest -m "not grc_native and not gui and not llama_eval"` passes.
- [ ] `pytest -m grc_native` passes on the dev box.
- [ ] `pytest -m gui` passes under `xvfb-run`.
- [ ] `git grep -n 'yaml\.safe_load\|yaml\.safe_dump\|subprocess.*grcc' src/grc_agent/` returns zero matches.
- [ ] `git grep -n 'gnuradio' src/grc_agent/` returns matches only in `grc_agent/grc_native_adapter.py`.
- [ ] `git diff --stat` on `flowgraph_session.py` shows net reduction of ≥ 500 lines.
- [ ] The 4 `tests/test_reliability_hardening.py` inspect-shape tests pass.
- [ ] The 8 `tests/gui/test_inspector_widget.py` tests pass.
- [ ] `docs/MODEL_CONTEXT_BIBLE.md` regenerates clean.
- [ ] The Qt GUI opens `examples/random_bit_generator.grc` and renders the 5 blocks across the correct categories with no `Advanced` or `Config` parameters in any tree.
- [ ] `flowgraph_session.py` still owns mutation, integrity, and atomic save; it no longer owns YAML parsing, `grcc` subprocess, or dict-crawling inspection.
- [ ] `git tag refactor-complete` is set.
- [ ] The `playground/` experiments are preserved as the historical record of the design decisions.

The refactor is **not done** if any of these is false. Per the maintainer's aggressive-redesign instruction: we do not paper over regressions with a shim, we fix them at the source.
