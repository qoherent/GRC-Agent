# Current Release Evidence

Evidence bundle for the current MVP wrapper runtime. This file is the concise tracked summary; transient dashboard JSON and local reports should stay outside git.

## Classification

| Area | Status | Evidence | Caveat |
|---|---|---|---|
| R0_READ_ONLY | Release-validated | Local dashboard n=3 passes | Read/search/docs safety and routing only |
| R1_SET_PARAM_ONLY | Release-validated | Local dashboard n=3 passes | Scoped to set_param release contract |
| R1_SET_STATE | Beta-validated | Local dashboard n=3 passes | Needs broader independent fixtures before release promotion |
| R2_DISCONNECT | Beta-validated | Local dashboard n=3 passes | Exact/clarification-backed disconnect only |
| R3_REWIRE | Beta-validated | Local dashboard n=3 passes | Not release-validated; broader external/adversarial depth still needed |
| R4A_INSERT_BLOCK_ON_CONNECTION | Beta-validated | Local dashboard n=3 passes | Connection-anchored insert only |
| R4B_REMOVE_BLOCK | Beta-validated | Local dashboard n=3 passes | Explicit detach required for attached blocks |
| R4C_ADD_VARIABLE | Beta-validated | Local dashboard n=3 passes | Add-only variable path |
| R5_SAVE_LOAD | Beta-validated | Local dashboard n=3 passes | Explicit lifecycle wrappers only; not release-validated |
| R7_EXACT_EXTERNAL | Diagnostic-clean | External exact dashboard n=3 passes | Diagnostic, not release-gating |
| Tier5_ADVERSARIAL | Diagnostic-clean | Adversarial dashboard n=3 passes | Diagnostic, not release-gating |
| R7_NATURAL_EXTERNAL | Diagnostic-partial | Natural external dashboard n=3 is tracked as diagnostic evidence | Natural-language ergonomics remains incomplete |

## Gate Summary

Required clean gate before claiming this evidence:

- `uv run ruff check src/ tests/`
- `uv run ruff check`
- `uv run python -m unittest`
- `uv run python -m tests.retrieval_eval.vector_regression`
- `uv run python -m tests.retrieval_eval.grc_docs_answer_eval`
- `uv run grc-agent doctor`
- `uv run grc-agent health`
- `uv run grc-agent release-manifest`

## Model-Facing Surface

The only model-facing tools are:

- `inspect_graph`
- `search_blocks`
- `ask_grc_docs`
- `change_graph`
- `save_graph_explicit`
- `load_graph_explicit`

Low-level tools such as `apply_edit`, `propose_edit`, `validate_graph`, `save_graph`, `load_grc`, `remove_connection`, `rewire_connection`, `insert_block_on_connection`, and `auto_insert_block` remain internal implementation primitives and must be rejected when requested as model-facing calls.

## STOP_THE_LINE Status

No current STOP_THE_LINE issue is accepted in this evidence bundle. Required invariants:

- Raw legacy/internal model calls are rejected.
- Fallback text parsing cannot execute legacy tools.
- Preview never mutates.
- Failed preflight or `grcc` validation never commits.
- Save requires explicit user intent.
- Unsafe original/example paths are protected.
- Docs/RAG has no mutation authority.

## Docs QA Caveat

`grc_docs_answer_eval` is a safety/baseline gate. It checks no misleading answers and no mutation leakage. It does not validate production-grade docs QA; relevance and groundedness gaps remain.

## Production Blockers

- Runtime is not production-ready.
- Only R0_READ_ONLY and R1_SET_PARAM_ONLY are release-validated.
- Broader mutation operations are beta-validated and require broader independent/external coverage before release promotion.
- R7_NATURAL_EXTERNAL remains diagnostic-partial, so natural-language ergonomics is incomplete.
- Docs QA remains safety-baseline only.
- Model behavior remains a dependency; live evals are evidence, not a guarantee of autonomy.
