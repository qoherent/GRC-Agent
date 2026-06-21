# Phase 0 — Aggressive Cleanup

> **Predecessor:** none.
> **Successor:** Phase 1 (`inspect_graph`).
> **Goal:** Delete obviously dead code and fix obvious dangling imports. **No functional change.** Prepare the workspace so that Phase 1's `inspect_graph` rewrite has a clean ground to land on.

---

## 0. Cross-Phase Context

Before starting, read `plan_context.md` in full. Inherit:
- §3 (MVP surface)
- §4 (aggressive redesign rules)
- §5 (verified environment facts)
- §8 (cross-phase edge cases)
- §10 (commit cadence)

---

## 1. What This Phase Does

The codebase has accumulated 1,596-line `flowgraph_session.py`, an 8-helper `session_ops.py`, a 1,157-line `inspect_graph.py`, and a 1,432-line `change_graph.py` that have grown intertwined. Before refactoring any of them, sweep out the things that are clearly dead:

1. Unused module-level constants.
2. Unused imports in the heavy files.
3. Dead `__all__` entries.
4. Any `print()` calls left in production code paths (model-facing or not).
5. Any commented-out code blocks longer than 3 lines.
6. Any obviously unused private helper functions (start with `_` and no caller in the file or in any imported module).
7. Stale or duplicated docstrings (e.g., the `_base_payload` docstring drift at `inspect_graph.py:854–873`).
8. Any local file in `playground/` that is not part of an active experiment (do **not** touch `playground/inspect_experiment/` — that's Phase 1's input).

This phase must not change behavior. Every deletion is followed by a full test run.

---

## 2. Files to Touch

### 2.1 Reads (audit, no edits)

- `src/grc_agent/flowgraph_session.py`
- `src/grc_agent/session_ops.py`
- `src/grc_agent/agent.py`
- `src/grc_agent/runtime/inspect_graph.py`
- `src/grc_agent/runtime/change_graph.py`
- `src/grc_agent/runtime/model_context.py`
- `src/grc_agent/validation/checks.py`
- `src/grc_agent/validation/errors.py`
- `src/grc_agent/transaction.py`
- `src/grc_agent/history.py`
- `src/grc_agent/sessions_store.py`
- `src/grc_agent/toolagents_runtime.py`
- `src/grc_agent/runtime_tool_validation.py`
- `src/grc_agent/session.py`
- `src/grc_agent/_payload.py`
- `src/grc_agent/config.py`
- `src/grc_agent/doctor.py`
- `src/grc_agent/dogfood.py`
- `src/grc_agent/startup.py`
- `src/grc_agent/retrieval/*`
- `src/grc_agent/catalog/*`
- `src/grc_agent_gui/*.py`

### 2.2 Creates

Nothing. This phase is deletion-only.

### 2.3 Modifies

- Any file where a dead import / dead constant / dead private helper is removed.
- `docs/MODEL_CONTEXT_BIBLE.md` may be re-regenerated if any docstring drift is fixed (run `UPDATE_MODEL_CONTEXT_BIBLE=1 pytest tests/test_model_context_bible.py -v`).

### 2.4 Deletes

- Any private helper that has no caller in the file or in any imported module.
- Any module-level constant that is not referenced anywhere.
- Any commented-out code block > 3 lines.
- Any `playground/` directory other than `playground/inspect_experiment/` (audit first — there might be other active experiments; ask the maintainer before deleting anything in `playground/`).

---

## 3. Step-by-Step

### 3.1 Audit pass

- [ ] **Step 1:** `rg -n 'TODO|FIXME|XXX|HACK' src/grc_agent/ | wc -l` — record the baseline. Goal is to keep this number stable or reduce it.
- [ ] **Step 2:** For each of the 21 files in §2.1, list the module-level imports. Cross-check with `rg` for actual usage in the same file. Any import not used in the file body is a candidate for deletion.
- [ ] **Step 3:** For each of the 21 files, list the module-level constants. Cross-check with `rg` across the whole repo (including tests). Any constant with no external reference is a candidate for deletion.
- [ ] **Step 4:** For each of the 21 files, list the private functions and methods (names starting with `_`). Cross-check with `rg` for callers. Any with no caller is a candidate for deletion.
- [ ] **Step 5:** `rg -n '^\s*#' src/grc_agent/ | wc -l` — record the baseline. Look for any commented-out code block (lines starting with `#` that look like Python, not English).
- [ ] **Step 6:** `rg -n 'print\(' src/grc_agent/` — list all `print()` calls. Production code paths must not use `print()`; they should use `logger`. Each call is a candidate for replacement with `logger.info` / `logger.debug`.
- [ ] **Step 7:** Audit `playground/` for any directory other than `inspect_experiment/`. Do not delete; just record. The maintainer decides.

### 3.2 Deletion pass (one file at a time, test after each)

- [ ] **Step 8:** For each candidate deletion, run the deletion.
- [ ] **Step 9:** After each file, run `pytest -m "not grc_native and not gui and not llama_eval" -x`. If anything fails, **revert** the deletion and document why in a comment in the file.
- [ ] **Step 10:** If a deletion cannot be made because the helper is called from a test or a docstring, leave a one-line `# TODO(phase-6): candidate for deletion — used by <test>` marker.

### 3.3 Docstring fix pass

- [ ] **Step 11:** Fix the `_base_payload` docstring drift at `src/grc_agent/runtime/inspect_graph.py:854–873`. The docstring claims the function emits `variable_references` and `param_keys_by_block` as top-level fields. The actual code only emits `params`. Pick one and make code match docs (recommend: drop both unused fields from the docstring; the code is correct).
- [ ] **Step 12:** Run `UPDATE_MODEL_CONTEXT_BIBLE=1 pytest tests/test_model_context_bible.py -v` to regenerate the bible. Inspect the diff — if it shows only the docstring correction, accept the change.

### 3.4 Final sweep

- [ ] **Step 13:** `rg -n 'TODO|FIXME|XXX|HACK' src/grc_agent/ | wc -l` — compare to the baseline from Step 1. If it went up, revert the additions.
- [ ] **Step 14:** `rg -n '^\s*#' src/grc_agent/ | wc -l` — compare to the baseline from Step 5. If it went up, the deletions touched comment lines by mistake; revert.
- [ ] **Step 15:** `git diff --stat` — confirm the diff is purely deletions + import fixes + the docstring correction. No new files. No new logic.

---

## 4. Verification Gate

The phase is done when **all** of the following hold:

- [ ] `pytest -m "not grc_native and not gui and not llama_eval"` passes.
- [ ] `git diff --stat` is purely deletions and import re-orderings. No new files. No new functions. No new logic.
- [ ] `rg -n 'TODO|FIXME|XXX|HACK' src/grc_agent/` count did not increase.
- [ ] The `_base_payload` docstring matches what the function actually emits.
- [ ] `docs/MODEL_CONTEXT_BIBLE.md` regenerates cleanly (after the docstring fix).
- [ ] The `playground/inspect_experiment/` directory is untouched.

If the test suite was already failing before this phase (e.g., from prior work), document the pre-existing failures in a `docs/refactor_plan/phase_0_baseline_failures.md` file and do not count them against this phase.

---

## 5. Edge Cases Specific to This Phase

| Edge case | Symptom | Mitigation |
|---|---|---|
| A "dead" private helper is actually called via a dynamic attribute (e.g., `getattr(obj, name)`) | Deletion breaks a feature at runtime, tests pass | Run the **eval chat** harness on `tests/eval_chat/fixtures/happy_path_one_tool_call.json` after each file edit. |
| A "dead" constant is referenced in a docstring or comment | `rg` doesn't find the reference; the maintainer wants it kept | When in doubt, keep the constant. Mark with `# TODO(phase-6): verify dead` and move on. |
| A `playground/` directory other than `inspect_experiment/` contains active work | Deleting it loses a maintainer's WIP | Do not delete. Record the name in the handoff report and ask the maintainer. |
| `rg` doesn't find a usage because it's behind a `__getattr__` or `__getattribute__` override | Runtime `AttributeError` after deletion | Run the full test suite after every deletion. Revert and mark if it fails. |
| A commented-out block is actually load-bearing context (e.g., a known-bad YAML example) | Deletion loses documentation | Keep the block. Add a `# Reference: see <issue> or <test>` comment instead. |

---

## 6. Handoff

When this phase finishes:

1. The implementing agent commits with the convention `chore(phase-0): <summary>`.
2. The implementing agent reports: deletions made per file, the new test pass count, the new `rg` baseline counts, the docstring correction, and any candidate deletions deferred to a later phase.
3. The handoff doc is at `docs/refactor_plan/phase_0_handoff.md` (created by the implementing agent, not pre-existing).

The next phase is Phase 1 (`inspect_graph`). The implementing agent for Phase 1 starts by reading `playground/inspect_experiment/verify_native_api.py` and the results already in `playground/inspect_experiment/results_native/`.
