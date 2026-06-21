# Phase 3 — `query_knowledge` (Experiment Only)

> **Predecessor:** Phase 2 (`change_graph` experiment done).
> **Successor:** Phase 4 (domain models).
> **Goal:** **Experiment only.** Confirm via experiment whether `query_knowledge` needs any refactor at all. It routes to `search_blocks` (catalog) and `ask_grc_docs` (docs RAG). Neither touches the live flowgraph. The expected outcome is **no refactor** — but the experiment is the proof. **Do NOT touch `query_knowledge` or its backends; do NOT create the native adapter.** Phase 5 builds the adapter only if Phase 3 proves it's needed; Phase 6 applies the cutover.

> **Why "experiment only":** per the consultant's architectural review, no code change is shipped until the experiment proves the change is needed. For `query_knowledge`, the expected outcome is "no change." The experiment confirms or refutes.

---

## 0. Cross-Phase Context

Before starting, read `plan_context.md` in full. Inherit:
- §3 (MVP surface — `query_knowledge` is the read-side tool that doesn't touch the flowgraph)
- §4 (aggressive redesign rules)
- §8.5 (test edge cases for catalog/docs)
- §10 (commit cadence)

---

## 1. Background

`query_knowledge` is defined at `src/grc_agent/runtime/inspect_graph.py:1130–1157`. It is a router with two backends:

- `search_blocks(agent, query, ...)` — searches the GNU Radio block catalog. Lives in `src/grc_agent/catalog/`.
- `ask_grc_docs(agent, query, ...)` — searches the GRC documentation RAG index. Lives in `src/grc_agent/retrieval/`.

Both backends read from static data (the catalog YAML files, the docs vector index). Neither reads from the live `agent.session.flowgraph` object. Neither is affected by the YAML-dict-crawling that Phases 1 and 2 are replacing.

**Hypothesis:** `query_knowledge` does not need a native-GRC refactor. The experiment is the proof.

---

## 2. The Experiment (Create New)

**Create directory:** `playground/query_knowledge_experiment/`

```
playground/query_knowledge_experiment/
├── run_experiment.py          # exercises query_knowledge via the agent
├── verify_native_api.py        # (proves there's nothing to refactor)
├── results/                    # 5–10 query results
└── analysis.md                 # documents the decision
```

### 2.1 `run_experiment.py`

Pick **5 representative queries** that span the catalog/docs boundary:

1. **Catalog block lookup by name**: `query="throttle"`, `domain="blocks"`.
2. **Catalog block lookup by fuzzy description**: `query="FM demodulator"`, `domain="blocks"`.
3. **Catalog block parameter search**: `query="center frequency"`, `domain="blocks"`.
4. **Docs RAG query**: `query="How do I save a flowgraph in headless mode?"`, `domain="docs"`.
5. **Docs RAG conceptual query**: `query="What is a hier block?"`, `domain="docs"`.

For each query:
- Call `agent._query_knowledge(query, domain)`.
- Capture the result.
- Write a markdown report to `results/<query_slug>.md` showing: the query, the domain, the tool result, the elapsed time, and any error paths.

### 2.2 `verify_native_api.py`

The purpose of this script is to **prove there is no native GRC API for catalog/docs queries** — i.e., the experiment demonstrates that the absence of a refactor is correct, not lazy.

The script should:

- Attempt to import every relevant module from `gnuradio.grc.core` and assert that none of them have a "search blocks" or "ask docs" function.
- List the public attributes of `Platform`, `FlowGraph`, `Block`, `Param`, `Port`, `Connection`, and `Constants`. Quote the docstrings for any that look even vaguely catalog-related.
- Conclude with a one-line assertion: `assert not has_catalog_or_docs_api(platform)`.

If this script finds that there **is** a native GRC API for catalog/docs queries (highly unlikely), the subagent must stop and re-design Phase 3.

### 2.3 `analysis.md`

Document the decision tree:

- If `run_experiment.py` succeeds and `verify_native_api.py` shows no native API → **decision: no refactor for `query_knowledge`.** The phase is a 1-commit "verify and document."
- If `run_experiment.py` fails for any reason (e.g., catalog index missing, RAG index stale) → **fix the data dependency, then re-run.** Do not skip the experiment.
- If `verify_native_api.py` finds a native API → **stop and re-design.**

---

## 3. The Build Decision (Likely Minimal)

### 3.1 If the experiment confirms "no refactor needed"

- [ ] **Step 1:** Run both scripts.
- [ ] **Step 2:** Write `analysis.md` with the decision.
- [ ] **Step 3:** Verify `tests/test_catalog_vector_live.py` passes (or skip if it requires live data).
- [ ] **Step 4:** Verify `tests/test_catalog_vector.py` and `tests/test_catalog_vector_unit.py` pass.
- [ ] **Step 5:** Run `pytest -m "not grc_native and not gui and not llama_eval"`. All pass.
- [ ] **Step 6:** Commit `chore(phase-3/query): verify query_knowledge needs no native refactor`. Include `analysis.md` in the commit.
- [ ] **Step 7:** Verify the agent's source tree is unchanged: `git diff --stat src/grc_agent/`. Zero matches (or only the small fix from §3.2).
- [ ] **Step 8:** Done. Phase 4 starts.

### 3.2 If the experiment reveals a small fix is needed

Possible small fixes (data-only, not architectural):
- A catalog YAML file is missing or stale.
- The docs RAG index is missing or stale.
- The `query_knowledge` dispatcher has a typo in a backend call.
- A test fixture references a removed backend.

For each:
- Fix it.
- Re-run the experiment.
- Document the fix in `analysis.md`.
- Commit `fix(phase-3/query): <fix>` per fix.

These are data fixes, not architectural changes. They do not introduce new dependencies on the native adapter or domain models.

### 3.3 If the experiment reveals a large refactor is needed

- **Stop.** This is a deviation from the plan-of-record. The subagent must write a `phase_3_deviation.md` and ask the maintainer to approve the deviation. Do not proceed with the refactor without explicit approval.

---

## 4. Files to Touch

### 4.1 Creates

- `playground/query_knowledge_experiment/run_experiment.py`
- `playground/query_knowledge_experiment/verify_native_api.py`
- `playground/query_knowledge_experiment/results/*.md` (5 queries)
- `playground/query_knowledge_experiment/analysis.md`

### 4.2 Modifies

- Possibly `src/grc_agent/catalog/*` (if catalog data is stale).
- Possibly `src/grc_agent/retrieval/*` (if RAG index is stale).
- Possibly a small fix to `src/grc_agent/runtime/inspect_graph.py:1130–1157` (if a typo is found).

### 4.3 Deletes

Nothing.

### 4.4 Untouched

- `src/grc_agent/runtime/inspect_graph.py` (the `inspect_graph` and `query_knowledge` parts are decoupled)
- `src/grc_agent/runtime/change_graph.py` (Phase 2's output)
- `src/grc_agent/grc_native_adapter.py` (Phase 1's output)
- `src/grc_agent/flowgraph_session.py` (Phase 6's job)

---

## 5. Verification Gate

The phase is done when **all** of the following hold:

- [ ] `playground/query_knowledge_experiment/analysis.md` exists and answers the three questions in §2.3.
- [ ] `pytest -m "not grc_native and not gui and not llama_eval"` passes.
- [ ] If a deviation was required, `phase_3_deviation.md` exists and the maintainer has signed off.
- [ ] The 5 representative queries all return sensible results (catalog hits, docs answers).
- [ ] `verify_native_api.py` proves no native GRC catalog/docs API exists (or the deviation is documented).

If the experiment confirms "no refactor needed", this phase may be the shortest in the plan. **That is correct.** The plan-of-record is honest about this.

---

## 6. Edge Cases Specific to This Phase

| Edge case | Symptom | Mitigation |
|---|---|---|
| The catalog vector index is missing (`tests/data/retrieval/` is empty) | `search_blocks` raises `FileNotFoundError` | Run the catalog build script. If it doesn't exist, escalate. |
| The docs RAG index is missing or stale | `ask_grc_docs` returns empty results | Run the docs index build. If it doesn't exist, escalate. |
| `query_knowledge` is incorrectly listed in `AGENTS.md` as needing a native refactor | The plan is wrong | Update the plan based on the experiment. Document the correction in `analysis.md`. |
| The 5 queries all return empty results | The catalog/docs data is empty | Investigate the data source. Do not declare "no refactor needed" just because the experiment is silent. |
| The eval chat harness uses `query_knowledge` and the stub returns a fake result | The stub is wrong for the new data shape | Audit the stub. Update if the shape has changed. Phase 7 covers GUI stubs. |

---

## 7. Handoff

When this phase finishes:

1. The implementing agent commits with the convention `chore(phase-3/query): <summary>` (or `fix` if a small fix was needed).
2. The implementing agent writes `docs/refactor_plan/phase_3_handoff.md` with:
   - The decision (no refactor / small fix / large deviation)
   - A copy of `analysis.md`
   - The new test pass count
   - Any deviation documentation
3. The next phase is Phase 4 (domain models). The Phase 4 subagent starts by reading the Phase 1 and Phase 2 handoff docs to understand the shape proven out by the experiments.
