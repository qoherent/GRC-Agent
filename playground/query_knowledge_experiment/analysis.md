# Phase 3 — `query_knowledge` Native Experiment: Analysis

**Decision: NO REFACTOR for `query_knowledge`.** Confirmed by experiment.

`query_knowledge` (`src/grc_agent/runtime/inspect_graph.py`) is a router over
two backends that read **static data**, never the live `FlowGraph`:
- `search_blocks` — GNU Radio block catalog (vector search).
- `ask_grc_docs` — GRC documentation RAG.

Neither is affected by the YAML-dict-crawling Phases 1/2 replace, so there is
nothing to cut over to a native GRC API.

Scripts: [verify_native_api.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/query_knowledge_experiment/verify_native_api.py),
[run_experiment.py](file:///home/mahmoud/Desktop/AI_Projects/qoherent/GRC_Agent/playground/query_knowledge_experiment/run_experiment.py).

---

## 1. Proof: no native catalog/docs API exists

`verify_native_api.py` introspected the public surface of `Platform`,
`FlowGraph`, `Block`, `Param`, `Connection`, and `Constants` (372 members
total). Keyword matches for `search`/`catalog`/`doc`/`query`/`find` resolved
exclusively to **data fields and registries**, never a query function:

| Match | What it actually is |
|---|---|
| `Platform.build_library`, `Platform.search` | catalog **loading**, not search |
| `Platform.block_docstrings`, `block_docstrings_loaded_callback` | docstring registry (a `dict`) |
| `Block.doc_url`, `Block.documentation` | per-block metadata fields |
| `Connection.documentation` | per-connection metadata field |

No callable takes a query string and returns catalog/docs hits. `assert not has_catalog_or_docs_api(platform)` → **PASS**. The "no refactor" decision is therefore correct, not lazy.

## 2. Smoke test (5 queries)

| Domain | Query | Result |
|---|---|---|
| catalog | "throttle", "FM demodulator", "center frequency" | **degraded** — `Catalog vector index not ready` |
| docs | "save a flowgraph in headless mode", "What is a hier block?" | **OK** — grounded answers returned |

- The tool **routes correctly** to both backends.
- It **degrades gracefully** when a backend is unavailable (`vec1.so` native lib + embedding model absent on this box → catalog search returns `degraded_retrieval=True` with a build hint, never crashes). Per `AGENTS.md` §Non-blocking flow.
- Docs RAG is fully functional.

The catalog "degraded" is a **data/ops dependency** (the `vec1` vector library and an embedding backend), not an architectural concern and not a reason to refactor `query_knowledge`.

## 3. Corrections to the plan-of-record

- `phase_3_query_knowledge.md §2.1` specifies `domain="blocks"`. The actual `SearchDomain` enum values are **`catalog`** and **`docs`** (`src/grc_agent/runtime/enums.py`). `domain="blocks"` is rejected by input validation. The experiment uses the correct `catalog`.
- `plan_context.md §8.3` speculates legacy validation runs the `grcc` subprocess; the Phase 2 experiment already corrected this (legacy uses `FlowGraph.validate()`). No impact on Phase 3.

## 4. Verification gate

- [x] `analysis.md` answers the decision questions (§1 proof, §2 smoke).
- [x] `verify_native_api.py` proves no native catalog/docs API exists.
- [x] Docs queries return sensible results; catalog degrades on a documented data dependency.
- [x] `pytest -m "not grc_native and not gui and not llama_eval"` passes (356); `src/grc_agent/` untouched this phase.
- [x] No deviation — no `phase_3_deviation.md` required.

**Outcome:** Phase 3 complete. `query_knowledge` is out of scope for the native
refactor. Proceeding to Phase 4 (domain models).
