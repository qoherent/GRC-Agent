# Phase 6 — Single Cutover: Tools + Session → Native Adapter

> **Predecessor:** Phase 5 (complete native adapter shipped; mutations, validation, identity all present).
> **Successor:** Phase 7 (GUI burn).
> **Goal:** In a **single coordinated cutover**, rewrite `inspect_graph`, `change_graph`, and gut `flowgraph_session.py` so that everything cuts over to the native adapter cleanly at the same time. **No blue/green flag. No "old implementation + new implementation" co-existing.** This phase ships the new architecture as the only architecture.

> **Why a single cutover:** per the consultant's architectural review, blue/green with a `_USE_NATIVE` flag is dangerous because it requires the old and new paths to be kept consistent for the duration of the migration, and any inconsistency creates a split-brain state. The correct pattern is: at the start of this phase, the agent uses the legacy `flowgraph_session.py`. At the end of this phase, the agent uses the native adapter exclusively. The two states are tested, but never co-exist in production.

> **Why this is one phase, not two:** per the consultant, the tool rewrite and the session gut **must** happen together. The tools read from the session; the session's mutations are the tools' source of truth. Splitting them across two phases forces the second phase to do a split-brain round-trip (read native, write legacy dict, validate by round-tripping the dict back to native). The single cutover avoids that.

---

## 0. Cross-Phase Context

Before starting, read `plan_context.md` in full. Inherit:
- §3 (MVP surface)
- §4 (aggressive redesign rules — **no backward compatibility** is critical here)
- §5 (verified environment facts)
- §6.1 (legacy surface) and §6.3 (29 importers of `FlowgraphSession`)
- §8.3 (runtime edge cases — the most important section for this phase)
- §10 (commit cadence)

Also re-read:
- The handoff docs from Phases 1, 2, 3, 4, 5.
- `src/grc_agent/grc_native_adapter.py` (the complete adapter from Phase 5).
- `src/grc_agent/domain_models.py` (the Pydantic models from Phase 4).
- `docs/GNU_NATIVE_METHODS.md` (the single source of truth for the GRC native API).

---

## 1. The Cutover Plan

### 1.1 What gets rewritten in this phase

**Tools (the model-facing surface):**

| File | Action | Why |
|---|---|---|
| `src/grc_agent/runtime/inspect_graph.py` | Rewrite to call the adapter | Native reads replace dict-crawling. The wire shape becomes the new flat payload (per `playground/inspect_experiment/wire_shape_proposal.md`). |
| `src/grc_agent/runtime/change_graph.py` | Rewrite to call the adapter | Native validation replaces `grcc` subprocess. Mutations are applied to a candidate `FlowGraph` via `apply_mutation`. |

**Session (the legacy core):**

| File | Action | Why |
|---|---|---|
| `src/grc_agent/flowgraph_session.py` | Gut | YAML parsing, `grcc` subprocess, dict-crawling inspection, and the dual-write invariant (parsed model + raw dict) all die. The class retains only: constructor, `path`, atomic save, file integrity, state revision. |
| `src/grc_agent/session_ops.py` | Delete entirely | The 8 `shared_*` helpers have no remaining callers. |
| `src/grc_agent/_payload.py` | Delete entirely | The legacy `Block` / `Connection` / `Flowgraph` types have no remaining callers. |
| `src/grc_agent/transaction.py` | Rewrite to call the adapter | The mutation pipeline operates on native `FlowGraph` objects, not the legacy `raw_data` dict. |

**Imports:**

| File | Action | Why |
|---|---|---|
| `src/grc_agent/agent.py` | Update the 5 tool handlers (`_inspect_graph`, `_change_graph`, `_summarize_graph`, `_get_grc_context`, `_apply_edit`) | They are thin wrappers; they delegate to the runtime. |
| `src/grc_agent/history.py` | Rewrite `snapshot_session` to use the adapter | The history journal reads native `GrcFlowgraph` snapshots, not legacy dicts. |
| `src/grc_agent/validation/checks.py` | Rewrite `SessionSnapshot` and `validate_and_apply_operation` to use the adapter | The validation pipeline operates on native `FlowGraph` objects. |
| `src/grc_agent/validation/errors.py` | Verify (likely no change) | The error types are dict-shaped; they don't care about the source. |
| `src/grc_agent_gui/app.py` | Update the GUI bootstrap | The GUI reads the new flat shape. (Phase 7 handles the inspector widget; this is the bootstrap.) |

**Tests (10 test files + 3 eval harnesses):**

| File | Action |
|---|---|
| `tests/test_save_integrity.py` | Update assertions on the legacy `validation_state()` shape to `GrcValidation.model_dump()`. |
| `tests/test_change_graph_flat_batch.py` | Update assertions on `validation_result` shape (the legacy `grcc` stdout/stderr is gone). |
| `tests/transaction/test_commit.py` | Update assertions on the legacy `raw_data` shape. |
| `tests/test_history_journal.py` | Update assertions on the legacy `flowgraph.blocks` iteration. |
| `tests/test_graph_safety_regressions.py` | Update assertions on `grcc` output. |
| `tests/test_agent_loop_fixes.py` | Update assertions on legacy payload fields. |
| `tests/test_yaml_refusal_and_save_path.py` | Update assertions on the new `errors[].code` field. |
| `tests/test_maintenance_watch_guards.py` | Update assertions on the new wire shape. |
| `tests/test_reliability_hardening.py` | Update the 4 inspect-shape tests. |
| `tests/session/test_*.py` (4 files) | Update assertions on the legacy `summarize_graph` / `get_grc_context` / `load_grc` shapes. |
| `tests/llama_eval/harness.py`, `r2_fixer_eval.py`, `r2_pivot_trace.py` | Update to call the adapter. |

### 1.2 What stays in `flowgraph_session.py` (after the gut)

The class retains **only**:

- `__init__(self, path: str | Path | None = None)` — initializes `self.path`, `self._state_revision`, `self._persisted_file_sha256`, and a reference to the native `FlowGraph` object (built via the adapter).
- `path` (property) — the file path.
- `load(path)` — calls `grc_native_adapter.load_flow_graph(path)`. Returns the native `FlowGraph` (or raises with a structured error).
- `save(path, *, validate)` — calls `grc_native_adapter.write_flow_graph_atomic(flow_graph, path)`. Atomic-write semantics preserved.
- `file_integrity_state()` — uses the file-bytes SHA-256, not the YAML canonical form.
- `persisted_file_sha256` (property) — the file-bytes SHA.
- `state_revision` (property) — bumped on every mutation via `grc_native_adapter.bump_revision`.
- The atomic-save plumbing (`_save_file_lock`, `_atomic_write_text`, `_fsync_directory`, `_write_save_backup`).
- The 4 deprecated public constants (`DEFAULT_SUMMARY_BLOCK_LIMIT`, `DEFAULT_CONTEXT_MAX_NODES`, `MAX_CONTEXT_HOPS`, `MAX_CONTEXT_MAX_NODES`) — **deleted**, not retained. They are not imported externally per the Phase 0 audit.

The class becomes a thin shim that holds a `FlowGraph` and a path, with atomic-save and integrity logic. ~300 lines, down from 1596.

### 1.3 The cutover order (one atomic operation)

The cutover is a **single coordinated commit** that touches all the files in §1.1 simultaneously. The order within the commit is:

1. **Update `flowgraph_session.py`** — gut the legacy, retain only the shim.
2. **Update `session_ops.py`** — delete.
3. **Update `_payload.py`** — delete.
4. **Update `transaction.py`** — delegate to the adapter.
5. **Update `history.py`** — delegate to the adapter.
6. **Update `validation/checks.py`** — delegate to the adapter.
7. **Update `agent.py`** — the 5 tool handlers.
8. **Rewrite `runtime/inspect_graph.py`** — call the adapter.
9. **Rewrite `runtime/change_graph.py`** — call the adapter.
10. **Update `grc_agent_gui/app.py`** — the bootstrap.
11. **Update 10 test files + 3 eval harnesses** — one at a time, test after each.
12. **Run the full default CI suite** — all pass.
13. **Run the GRC-native suite** — all pass.
14. **Commit the cutover** as a single commit: `refactor(phase-6/cutover): single cutover to native adapter`.
15. **Tag the commit**: `git tag phase-6-cutover`.

**No `_USE_NATIVE` flag. No legacy shim alongside the new code. The cutover is the commit.**

### 1.4 What dies

The following methods / functions / files die in this phase:

- `flowgraph_session.py:load` (old YAML version) — replaced by adapter call.
- `flowgraph_session.py:from_raw_data` — no native equivalent.
- `flowgraph_session.py:create` — no native equivalent.
- `flowgraph_session.py:save` (old YAML dump version) — replaced by adapter call.
- `flowgraph_session.py:_serialize_raw_data` — `yaml.safe_dump` is gone.
- `flowgraph_session.py:_parse_blocks`, `_parse_connections` — delegates to deleted `session_ops.py`.
- `flowgraph_session.py:_run_grcc_validation`, `_validate_candidate_raw_data_or_raise`, `_grcc_result_is_valid`, `_grcc_failure_message`, `DEFAULT_GRCC_TIMEOUT_SECONDS` — the `grcc` subprocess is gone.
- `flowgraph_session.py:summary_payload`, `context_payload`, `active_session_snapshot`, `validation_state`, `graph_id` (the legacy versions) — replaced by adapter calls.
- `flowgraph_session.py:_ensure_inspection_cache` and its 5 helpers — caches are gone; the adapter reads native objects on demand.
- `flowgraph_session.py:resolve_block_reference`, `find_connection_candidates`, `_block_identity_payload`, `_connection_payload`, `_context_node_payload`, `_parameter_sample` — replaced by adapter helpers.
- `import yaml` at the top of `flowgraph_session.py` — gone.
- `import subprocess` at the top of `flowgraph_session.py` — gone.
- `import tempfile` at the top of `flowgraph_session.py` — gone.
- `from .session_ops import (...)` — gone.
- `from ._payload import Block, Connection, Flowgraph` — gone.
- `flowgraph_session.py:DEFAULT_SUMMARY_BLOCK_LIMIT`, `DEFAULT_CONTEXT_MAX_NODES`, `MAX_CONTEXT_HOPS`, `MAX_CONTEXT_MAX_NODES` — gone (no external importers per Phase 0 audit).
- `src/grc_agent/session_ops.py` — deleted.
- `src/grc_agent/_payload.py` — deleted.
- The dict-crawling helpers in `runtime/inspect_graph.py` (`_param_keys_by_block`, `_platform_param_categories`, `_graph_variable_values`, `_all_variable_references`, `_variable_reference_map`, `_overview_block_rows`, `_target_score`, `_resolve_target` helpers).
- The `_bypass_hint`, `_incident_connection_ids`, `_flat_block_names_snapshot`, `_flat_connection_ids_snapshot`, `_synthesized_flat_delta`, `connection_endpoint_candidates`, `loaded_block_by_name` helpers in `runtime/change_graph.py`.

---

## 2. Step-by-Step

### 2.1 Pre-work (Day 1)

- [ ] **Step 1:** Create `docs/refactor_plan/phase_6_migration_checklist.md` listing all 29 importers of `FlowgraphSession` with the current method calls they use. This is the punch list.
- [ ] **Step 2:** Run the full default CI suite to capture the baseline pass count. Save to `docs/refactor_plan/phase_6_baseline.txt`.
- [ ] **Step 3:** Tag the commit: `git tag phase-6-baseline`. This is the rollback point.

### 2.2 Cutover (Days 2–10)

The cutover is a sequence of file rewrites. **Each rewrite is a single commit that includes a working test run** — the legacy code may be temporarily broken, but the test run catches it.

- [ ] **Step 4:** Rewrite `src/grc_agent/flowgraph_session.py` to the shim per §1.2. The shim delegates to the adapter for everything except atomic save, integrity, and state revision. Commit `refactor(phase-6/flowgraph_session): gut legacy YAML/grcc/dict-crawl paths`. Run `pytest -x` and accept that downstream tests break (the shim is incomplete; downstream files haven't migrated yet).
- [ ] **Step 5:** Delete `src/grc_agent/session_ops.py`. Commit `chore(phase-6): delete session_ops.py`. Run `pytest -x`. Downstream breaks.
- [ ] **Step 6:** Delete `src/grc_agent/_payload.py`. Commit `chore(phase-6): delete _payload.py`. Run `pytest -x`. Downstream breaks.
- [ ] **Step 7:** Rewrite `src/grc_agent/transaction.py` to operate on native `FlowGraph` objects. The mutation pipeline uses `apply_mutation(flow_graph, op_type, **kwargs)` from the adapter, then `validate_and_finalize(flow_graph)`. Commit `refactor(phase-6/transaction): delegate to native adapter`. Run `pytest -x`. Transaction tests pass; tool tests still break.
- [ ] **Step 8:** Rewrite `src/grc_agent/history.py` to use the adapter. `snapshot_session` reads `grc_native_adapter.render_flow_graph(flow_graph)` and stores the `GrcFlowgraph` snapshot. Commit `refactor(phase-6/history): delegate to native adapter`. Run `pytest -x`.
- [ ] **Step 9:** Rewrite `src/grc_agent/validation/checks.py` to use the adapter. Commit `refactor(phase-6/validation): delegate to native adapter`. Run `pytest -x`.
- [ ] **Step 10:** Update `src/grc_agent/agent.py` — the 5 tool handlers (`_inspect_graph`, `_change_graph`, `_summarize_graph`, `_get_grc_context`, `_apply_edit`). They are thin wrappers; update their calls to the new tool handlers. Commit `refactor(phase-6/agent): update tool handlers`. Run `pytest -x`.
- [ ] **Step 11:** Rewrite `src/grc_agent/runtime/inspect_graph.py`. The new body is ~150 lines: validate `view`, normalize targets/params, call the adapter, wrap in the model-visible payload (per `playground/inspect_experiment/wire_shape_proposal.md`). Commit `refactor(phase-6/inspect_graph): rewrite to use native adapter`. Run `pytest -x`. The 4 `test_reliability_hardening.py` tests pass.
- [ ] **Step 12:** Rewrite `src/grc_agent/runtime/change_graph.py`. The new body uses the adapter's `apply_mutation` and `validate_and_finalize`. Commit `refactor(phase-6/change_graph): rewrite to use native adapter`. Run `pytest -x`. The mutation tests pass.
- [ ] **Step 13:** Update `src/grc_agent_gui/app.py` (the GUI bootstrap). Commit `refactor(phase-6/gui-bootstrap): update for new shape`. Run `pytest -x`.

### 2.3 Test updates (Days 11–13)

- [ ] **Step 14:** For each of the 10 test files in §1.1, update the assertions to the new shape. One file per commit. Run the test suite after each.
- [ ] **Step 15:** Update the 3 eval harnesses. One file per commit. Run the harness after each (if eval data is available).
- [ ] **Step 16:** Update the chat-eval harness (`tests/eval_chat/harness.py:28–43`) and any fixtures that reference the legacy `summary.blocks` shape. (Phase 7 handles the GUI; this is the chat-eval side.)
- [ ] **Step 17:** Run the full default CI suite: `pytest -m "not grc_native and not gui and not llama_eval" -x`. All pass.
- [ ] **Step 18:** Run the GRC-native suite: `pytest -m grc_native -x`. All pass.
- [ ] **Step 19:** Run the GUI suite under `xvfb-run`: `pytest -m gui -x`. The 8 `test_inspector_widget.py` tests are expected to fail at this point (the inspector still consumes the old shape; Phase 7 fixes it). Confirm the failure is the expected one (shape mismatch, not a crash).

### 2.4 Final hygiene (Day 14)

- [ ] **Step 20:** `rg -n 'yaml\.safe_load\|yaml\.safe_dump\|subprocess' src/grc_agent/`. Zero matches.
- [ ] **Step 21:** `rg -n 'gnuradio' src/grc_agent/`. Matches only in `src/grc_agent/grc_native_adapter.py`.
- [ ] **Step 22:** `rg -n 'compute_graph_id\|hashing' src/grc_agent/`. Zero matches (no deep-JSON-hash functions).
- [ ] **Step 23:** `git diff --stat HEAD~N..HEAD -- src/grc_agent/flowgraph_session.py`. Net reduction ≥ 1000 lines.
- [ ] **Step 24:** `git diff --stat HEAD~N..HEAD -- src/grc_agent/session_ops.py src/grc_agent/_payload.py`. Both deleted.
- [ ] **Step 25:** `git tag phase-6-cutover`. The cutover is sealed.
- [ ] **Step 26:** `UPDATE_MODEL_CONTEXT_BIBLE=1 pytest tests/test_model_context_bible.py -v`. Diff should reflect the new tool shapes.

### 2.5 Squash or keep separate commits?

The 14+ commits in §2.2 and §2.3 are an **audit trail** — each commit is one logical change, with its own test run. **Do NOT squash.** The maintainer wants to review the cutover step by step.

If the cutover is so large that 14+ commits is unwieldy, the subagent may **rebase them into 3–5 logical groups** (e.g., "session gut", "tool rewrite", "test updates") using `git rebase -i`. But the test suite must pass at every step of the rebase.

---

## 3. Files to Touch

### 3.1 Creates

- `docs/refactor_plan/phase_6_migration_checklist.md`
- `docs/refactor_plan/phase_6_baseline.txt`

### 3.2 Modifies

- `src/grc_agent/flowgraph_session.py` (1596 → ~300 lines; the shim)
- `src/grc_agent/transaction.py` (rewrite for native mutations)
- `src/grc_agent/history.py` (rewrite for native snapshots)
- `src/grc_agent/validation/checks.py` (rewrite for native validation)
- `src/grc_agent/agent.py` (5 tool handlers updated)
- `src/grc_agent/runtime/inspect_graph.py` (rewrite; ~150 lines)
- `src/grc_agent/runtime/change_graph.py` (rewrite; ~150 lines)
- `src/grc_agent_gui/app.py` (the GUI bootstrap)
- 10 test files (one at a time, per §2.3)
- 3 eval harnesses (one at a time, per §2.3)
- `tests/eval_chat/harness.py` (chat-eval stub update)
- `docs/MODEL_CONTEXT_BIBLE.md` (regenerated)

### 3.3 Deletes

- `src/grc_agent/session_ops.py` (the 8 `shared_*` helpers)
- `src/grc_agent/_payload.py` (the legacy `Block` / `Connection` / `Flowgraph` types)
- The dict-crawling helpers in `runtime/inspect_graph.py`
- The `_bypass_hint`, `_incident_connection_ids`, `_flat_block_names_snapshot`, `_flat_connection_ids_snapshot`, `_synthesized_flat_delta`, `connection_endpoint_candidates`, `loaded_block_by_name` helpers in `runtime/change_graph.py`
- The 4 deprecated public constants in `flowgraph_session.py` (`DEFAULT_SUMMARY_BLOCK_LIMIT`, `DEFAULT_CONTEXT_MAX_NODES`, `MAX_CONTEXT_HOPS`, `MAX_CONTEXT_MAX_NODES`)

### 3.4 Untouched (Phase 7's job)

- `src/grc_agent_gui/inspector.py`
- `tests/gui/test_inspector_widget.py`

The GUI inspector still consumes the old shape at the end of this phase. The 8 GUI tests fail. Phase 7 fixes them. The cutover is still correct because the model-facing wire shape is correct; only the local GUI consumer is out of date.

---

## 4. Verification Gate

The phase is done when **all** of the following hold:

- [ ] `pytest -m "not grc_native and not gui and not llama_eval"` passes.
- [ ] `pytest -m grc_native` passes on the dev box.
- [ ] `pytest -m gui` is expected to fail (8 inspector-shape failures); the failure is the trigger to dispatch Phase 7.
- [ ] `git diff --stat` on `flowgraph_session.py` shows a net reduction of ≥ 1000 lines.
- [ ] `session_ops.py` and `_payload.py` are deleted.
- [ ] `rg -n 'yaml\.safe_load\|yaml\.safe_dump\|subprocess' src/grc_agent/` returns zero matches.
- [ ] `rg -n 'gnuradio' src/grc_agent/` returns matches **only** in `src/grc_agent/grc_native_adapter.py`.
- [ ] `rg -n 'compute_graph_id\|hashing' src/grc_agent/` returns zero matches.
- [ ] The 29 importers of `FlowgraphSession` are all updated to the new path.
- [ ] `docs/MODEL_CONTEXT_BIBLE.md` regenerates cleanly.
- [ ] `git tag phase-6-cutover` is set.

---

## 5. Edge Cases Specific to This Phase

| Edge case | Symptom | Mitigation |
|---|---|---|
| A test asserts on `validation_state()` returning `{"status", "returncode", "state_revision", "stdout", "stderr"}` | Test fails | The new shape is `GrcValidation.model_dump()` — `status`, `errors`, `native_ok`. Update the assertion. |
| A test asserts on `graph_id()` returning the same value before and after a mutation that the legacy `graph_id` ignored | Test fails because the new `graph_id` is content-derived | Document the behavior change. The new `graph_id` changes when the content changes (file-bytes SHA). The integrity check still works because the file-bytes SHA is separate from the in-session revision counter. |
| The mutation pipeline in `transaction.py` calls `commit_candidate_session` which `copy.deepcopy`s the candidate's `__dict__` | The `__dict__` contains a native `FlowGraph` object that doesn't survive `deepcopy` cleanly | Rewrite `commit_candidate_session` to deep-copy the native `FlowGraph` explicitly (or re-load from the serialized form). |
| The `grcc` subprocess was the **only** path catching a Python compile-time error | Mutations that compile-fail are now silent | Per `docs/GNU_NATIVE_METHODS.md` §5, the native `flow_graph.validate()` does not run `python -c "compile(open(...).read())"`. If the eval-harness shows a regression, expose `flow_graph.generate()` in a follow-up (not in scope for this phase). |
| The new `serialize_flow_graph` output is not byte-identical to the input `.grc` | A `git diff` after a save shows many lines | This is GRC's normal behavior. Document. Add a config option to suppress the diff if the maintainer wants. |
| The `persisted_file_sha256` differs from the SHA of `serialize_flow_graph(load(file)).read()` | The integrity check trips on a "no-op" save | Document the rule: the integrity check is the file-bytes SHA, not the YAML canonical form. |
| A test file at `tests/test_save_integrity.py` asserts on the `_save_file_lock` fcntl behavior | Test fails on Windows | The test is already POSIX-only; no change. |
| The chat-eval harness has a `change_graph` stub that asserts on the legacy shape | Eval chat test fails | Update the stub. The stub-level `expect` field in `tests/eval_chat/fixtures/*.json` may need updating. |
| A user has saved a chat history with `inspect_graph` results in the old shape | The chat widget shows stale data | The chat history is JSON-serialized; the new agent reads it as the new shape. The old fields are ignored. Verify in the smoke test (Phase 7). |
| A test patches `inspect_graph` to return the legacy shape | The patch is a no-op | Update the patch to return the new shape. |

---

## 6. Handoff

When this phase finishes:

1. The implementing agent has committed the cutover as a series of atomic commits (one per file, with test runs after each).
2. The implementing agent writes `docs/refactor_plan/phase_6_handoff.md` with:
   - The net line reduction in `flowgraph_session.py`
   - The list of files modified (the 29 importers + 2 deletions + the new test updates)
   - The test pass count for the default CI suite and the GRC-native suite
   - The `rg` outputs showing the gut is complete
   - The tag `phase-6-cutover` confirmation
   - The expected GUI test failures (8 inspector-shape tests) that trigger Phase 7
3. The next phase is Phase 7 (GUI burn). The Phase 7 subagent starts by reading `src/grc_agent_gui/inspector.py` and the 8 failing GUI tests.
