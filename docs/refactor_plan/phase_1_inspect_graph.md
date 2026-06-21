# Phase 1 — `inspect_graph` (Experiment Only)

> **Predecessor:** Phase 0 (cleanup done).
> **Successor:** Phase 2 (`change_graph` experiment).
> **Goal:** **Experiment only.** Re-run the existing `playground/inspect_experiment/` scripts, capture the legacy-vs-native diff, and write the proposed wire shape and filtering rules into `analysis.md`. **Do NOT rewrite `inspect_graph.py` and do NOT create the native adapter module.** Both happen in Phases 5 and 6 as part of the single cutover.

> **Why "experiment only":** per the consultant's architectural review, building a working tool on top of a thin adapter before the adapter's mutation methods exist creates a split-brain state (the new tool reads from native, but the rest of the system writes to the legacy dict). The correct order is: prove the API works (Phases 1–3), build the complete adapter (Phases 4–5), cut over everything at once (Phase 6).

---

## 0. Cross-Phase Context

Before starting, read `plan_context.md` in full. Inherit:
- §3 (MVP surface)
- §4 (aggressive redesign rules)
- §5 (verified environment facts)
- §8.1, §8.2 (env, load-path edge cases)
- §10 (commit cadence)

Also re-read `docs/GNU_NATIVE_METHODS.md` (the single source of truth for the GRC native API). Specifically:
- §1.5 (`Param` — `hide`, `category`, `dtype`)
- §1.4 (`Block` — `is_variable`, `is_import`, etc.)
- §3 (parameter filtering, tab census, combined recipe)
- §5 (validation & error bubbling)

---

## 1. The Experiment (Already Exists)

**Input directory:** `playground/inspect_experiment/`

```
playground/inspect_experiment/
├── run_experiment.py          # runs OLD agent's inspect_graph against all .grc fixtures
├── verify_native_api.py        # uses native GRC Platform directly, produces canonical output
├── results/                    # 9 markdown files (legacy output)
└── results_native/             # 9 json files (native output)
```

The two scripts form the before/after comparison.

---

## 2. What This Phase Does (and Does NOT Do)

### 2.1 In scope

1. Re-run both scripts to capture a fresh baseline.
2. Diff the two output sets for one fixture (e.g., `random_bit_generator.grc`).
3. Write `playground/inspect_experiment/analysis.md` with the diff and the legacy-vs-native behavior gaps.
4. Write `playground/inspect_experiment/wire_shape_proposal.md` with the proposed model-visible payload shape, the visibility filter rule, and the role classification rule.
5. If the diff reveals a non-`Advanced`/`Config` parameter that legacy includes but native drops, **stop and ask the maintainer** whether to keep or drop it. Do not silently change behavior.
6. Update `playground/inspect_experiment/results/` and `playground/inspect_experiment/results_native/` with the fresh run output.

### 2.2 Out of scope (deferred to later phases)

- **Do NOT touch `src/grc_agent/runtime/inspect_graph.py`.** The legacy `inspect_graph` continues to operate exactly as it does today. Behavior is unchanged at the end of this phase.
- **Do NOT create `src/grc_agent/grc_native_adapter.py`.** That's Phase 5.
- **Do NOT create `src/grc_agent/domain_models.py`.** That's Phase 4.
- **Do NOT touch any other source file in `src/grc_agent/`.**
- **Do NOT touch any test file.**
- **Do NOT touch the Qt GUI.** Phase 7.

The only files modified by this phase live under `playground/inspect_experiment/`. The agent's source tree is unchanged.

---

## 3. The Wire Shape (Proposal — to be codified in Phase 4)

This section documents the wire shape the experiment proves out. The Pydantic model in Phase 4 will codify this exact shape. Do not add or remove fields based on speculation — only fields the experiment shows are needed.

```python
{
    "ok": bool,
    "view": "overview" | "details",
    "state_revision": int,
    "graph": {
        "graph_name": str,
        "counts": {"blocks": int, "connections": int, "variables": int},
        "blocks": [            # FLAT list, not dict-of-dicts
            {
                "instance_name": str,
                "block_type": str,
                "block_uid": str,
                "role": "variable" | "source" | "sink" | "transform"
                       | "virtual_or_pad" | "import" | "snippet"
                       | "options" | "other",
                "state": "enabled" | "disabled" | "bypass",
                "parameters": [
                    {"name": str, "dtype": str, "value": str,
                     "category": str, "hide": "none" | "part" | "all"},
                    ...
                ],
            },
            ...
        ],
        "connections": [
            {"connection_id": str, "src_block": str, "src_port": str,
             "dst_block": str, "dst_port": str, "dtype": str | None},
            ...
        ],
        "validation": {"status": "valid" | "invalid" | "unknown", "errors": [str, ...]},
    },
    "params": [str, ...],          # flat list of all visible param names (sorted)
    "targets": [...],              # details view only
    "omitted": {"blocks": int, "connections": int, "parameters": int},  # optional
    "errors": [{"code": str, "message": str}, ...],                     # optional
}
```

**Dropped from the legacy shape** (per the experiment's diff):
- `summary.blocks` (becomes `graph.blocks`)
- `summary.connections` (becomes `graph.connections`)
- `summary.validation` (becomes `graph.validation`)
- `_block_params` sidecar (parameters inlined into each block)

**Preserved from the legacy shape:**
- Top-level `ok`, `view`, `state_revision`, `params`, `errors`, `omitted`, `targets`
- All block fields: `instance_name`, `block_type`, `block_uid`, `role`, `state`
- All parameter fields: `name`, `dtype`, `value`, `category`, `hide`

### 3.1 The visibility filter (one uniform rule)

```python
EXCLUDED_CATEGORIES = {ADVANCED_PARAM_TAB, "Config"}

for k, p in block.params.items():
    if p.hide == "all":
        continue
    if p.category in EXCLUDED_CATEGORIES:
        continue
    parameters.append({...})
```

No per-block allowlist. No per-param regex. The native `param.hide` and `param.category` are the single source of truth. Per `docs/GNU_NATIVE_METHODS.md` §3, this drops only generic GRC metadata and 100%-styling `Config` parameters. Everything else survives.

**Note about `verify_native_api.py`:** the existing experiment script has a `is_gui_or_style_param` regex filter on top of the category filter. **Do NOT port that regex.** The native `param.category == "Config"` already covers the 100% styling parameters. Adding the regex back is a regression to the per-scenario branching the maintainer forbade. If the experiment shows a non-`Config` parameter that is clearly GUI styling, document it in `analysis.md` and flag for the maintainer.

### 3.2 The role classification (one uniform rule)

```python
def classify_role(block) -> str:
    if block.is_variable:
        return "variable"
    if block.is_import:
        return "import"
    if block.is_snippet:
        return "snippet"
    if block.is_virtual_or_pad:
        return "virtual_or_pad"
    if block.key == "options":
        return "options"
    has_out = len(block.active_sources) > 0
    has_in = len(block.active_sinks) > 0
    if has_out and not has_in: return "source"
    if has_in and not has_out: return "sink"
    if has_in and has_out:    return "transform"
    return "other"
```

No per-block per-port branches. The native booleans first, then port topology, then fallback.

---

## 4. Step-by-Step

### 4.1 Experiment (Days 1–2)

- [ ] **Step 1:** Read `playground/inspect_experiment/run_experiment.py` and `playground/inspect_experiment/verify_native_api.py` in full. Understand what each produces.
- [ ] **Step 2:** Re-run both scripts. Verify they still work and that the output count matches (9 fixtures × 2 scripts = 18 output files). If any fixture fails, document and stop.
- [ ] **Step 3:** Diff the two output sets for `random_bit_generator.grc`. List:
  - Every top-level key that appears in legacy but not native.
  - Every top-level key that appears in native but not legacy.
  - Every block that appears in legacy but not in native.
  - Every block that appears in native but not in legacy.
  - Every connection that appears in legacy but not in native.
  - Every connection that appears in native but not in legacy.
  - Every parameter that appears in legacy but not in native, **categorized as**: `Advanced` tab / `Config` tab / `hide=all` / other.
  - Every parameter that appears in native but not in legacy.
- [ ] **Step 4:** Repeat Step 3 for the other 8 fixtures in `tests/data/`. If a behavior gap appears in a non-`Advanced`/`Config` case, **stop and ask the maintainer** whether to keep or drop the legacy behavior.
- [ ] **Step 5:** Write `playground/inspect_experiment/analysis.md` with the diff and the gaps. Each gap is a line item: "parameter X of block Y appears in legacy but native drops it. **Reason**: `category == "Config"`. **Decision**: drop (matches maintainer's rule)." or "**Decision**: keep (deviation requested)".

### 4.2 Wire shape proposal (Day 2)

- [ ] **Step 6:** Write `playground/inspect_experiment/wire_shape_proposal.md` with:
  - The wire shape from §3 of this phase (verbatim).
  - The visibility filter from §3.1 (verbatim).
  - The role classification from §3.2 (verbatim).
  - For each field in the wire shape, cite the `verify_native_api.py` line that produces it. If a field is not produced by `verify_native_api.py`, **do not include it in the proposal**.
  - For each dropped field, cite the experiment's diff that justifies the drop.
- [ ] **Step 7:** Hand the proposal to Phase 4. Phase 4 will codify the shape as a Pydantic model. If Phase 4 finds a field that the experiment did not exercise, Phase 4 will flag it back to the maintainer.

### 4.3 Commit (Day 2)

- [ ] **Step 8:** Commit the experiment refresh + the two markdown deliverables. Convention: `chore(phase-1/inspect): refresh experiment and document wire shape`. Include the updated `results/` and `results_native/` files in the commit (they're auto-generated but the experiment is the historical record).
- [ ] **Step 9:** Verify the agent's source tree is unchanged: `git diff --stat src/grc_agent/`. Zero matches.
- [ ] **Step 10:** Verify the legacy `inspect_graph` still passes its tests: `pytest -m "not grc_native and not gui and not llama_eval" -x`. All pass.

---

## 5. Files to Touch

### 5.1 Creates

- `playground/inspect_experiment/analysis.md` (or updated if it already exists)
- `playground/inspect_experiment/wire_shape_proposal.md`

### 5.2 Modifies

- `playground/inspect_experiment/results/*.md` (refreshed)
- `playground/inspect_experiment/results_native/*.json` (refreshed)

### 5.3 Deletes

Nothing.

### 5.4 Untouched (deferred to later phases)

- `src/grc_agent/runtime/inspect_graph.py` (Phase 6)
- `src/grc_agent/grc_native_adapter.py` (Phase 5 — does not exist yet)
- `src/grc_agent/domain_models.py` (Phase 4 — does not exist yet)
- All other source files in `src/grc_agent/`
- All test files
- The Qt GUI and its tests (Phase 7)

---

## 6. Verification Gate

The phase is done when **all** of the following hold:

- [ ] Both experiment scripts run cleanly and produce 9 markdown + 9 JSON outputs.
- [ ] `playground/inspect_experiment/analysis.md` exists and documents the legacy-vs-native diff for all 9 fixtures.
- [ ] `playground/inspect_experiment/wire_shape_proposal.md` exists and contains the wire shape, the visibility filter, and the role classification, with line-citations to `verify_native_api.py`.
- [ ] `git diff --stat src/grc_agent/` returns zero matches. The source tree is unchanged.
- [ ] `pytest -m "not grc_native and not gui and not llama_eval"` passes. The legacy `inspect_graph` is unchanged.
- [ ] The 4 `tests/test_reliability_hardening.py` tests pass. They consume the legacy shape; the new shape is not yet wired in.
- [ ] If a behavior gap was found in a non-`Advanced`/`Config` case, the maintainer has signed off on the decision.

If the analysis reveals a behavior gap that requires a non-trivial decision (e.g., a parameter that legacy includes but native drops, where the maintainer wants neither "drop" nor "keep" but "transform in a specific way"), the subagent must stop and write a `phase_1_deviation.md` for the maintainer. Do not proceed with a speculative transformation.

---

## 7. Edge Cases Specific to This Phase

| Edge case | Symptom | Mitigation |
|---|---|---|
| `verify_native_api.py` doesn't produce a field that the legacy output has | The wire shape is missing a field | **Stop and document.** Phase 4 will need to add a native source for that field, or the maintainer needs to accept the drop. |
| `verify_native_api.py` produces a field the legacy doesn't have | The wire shape has a new field | Document in the proposal as "new field, native-only." Phase 4 will codify. |
| The `is_gui_or_style_param` regex in `verify_native_api.py` catches something the category filter misses | The category filter is "less aggressive" than the regex | Document the gap in `analysis.md`. **Do NOT port the regex.** Per the maintainer's rule, no per-param regex. The maintainer can decide in a follow-up whether to add a separate cosmetic filter (out of scope here). |
| A fixture fails to load via `verify_native_api.py` (e.g., a circular variable) | The native output is missing for that fixture | Document the failure in `analysis.md`. Phase 4 will codify the error path (`ok=False`, `errors=[{"code": "REWRITE_FAILED", "message": "..."}]`). |
| The `run_experiment.py` (legacy) is now broken because of unrelated changes to the agent | The baseline is missing | Fix the legacy handler in a separate commit **before** the experiment. Do NOT include the legacy fix in this phase. |
| A test asserts on the legacy shape and starts failing because of unrelated changes | The baseline is broken | Same as above: fix the test in a separate commit before the experiment. |
| The experiment shows 0% behavior gap (legacy == native for all 9 fixtures) | No diff to document | That is a valid result. `analysis.md` says "0% gap; the experiment confirms the native API is sufficient." |

---

## 8. Handoff

When this phase finishes:

1. The implementing agent commits with the convention `chore(phase-1/inspect): refresh experiment and document wire shape`.
2. The implementing agent writes `docs/refactor_plan/phase_1_handoff.md` with:
   - The `analysis.md` summary (or a copy of it)
   - The `wire_shape_proposal.md` summary (or a copy of it)
   - The number of fixtures with non-zero behavior gaps
   - The list of behavior gaps that required a maintainer decision
   - Confirmation that the agent's source tree is unchanged
3. The next phase is Phase 2 (`change_graph` experiment). The Phase 2 subagent creates `playground/change_graph_experiment/` following the same pattern. **No source-code changes.**
