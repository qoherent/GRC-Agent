# Maintenance Status - 2026-05-03

## Copied Graph Availability

- Intentionally copied user/workspace `.grc` graphs available: `0` (required minimum for controlled user-graph dogfood: `5`)
- Action taken: maintenance verification only
- User-graph controlled dogfood: not run
- Installed-example expansion: not run

## Verification Commands

- `uv run ruff check src/ tests/` -> PASS
- `uv run ruff check` -> PASS
- `uv run python -m unittest` -> PASS (`Ran 1008 tests`, `OK`, `skipped=9`)
- `uv run python -m tests.retrieval_eval.vector_regression` -> PASS
  - Summary: `vector_top_k_hits=276/290`, `safety_passes=290/290`, `provenance_passes=290/290`
- `uv run grc-agent doctor` -> PASS
- `uv run grc-agent health` -> PASS (`tool_count=17`)
- `uv run grc-agent fake tests/data/random_bit_generator.grc` -> PASS

Note:
- Deterministic gate initially failed due missing baseline artifact `reports/retrieval/vector_eval_governed_metadata.json`.
- Restored the governed metadata baseline file (artifact-only fix, no runtime behavior change), then reran `unittest` to green.

## Patch Decision

- Runtime patch justified: no
- Maintenance artifact patch applied: yes (restored governed vector baseline report file)
- Safety STOP_THE_LINE issues observed: none
- Repeated generic failure cluster across unrelated graphs: none observed in this maintenance run
- Current blocker: none in this maintenance run.

## Known Limits (Unchanged)

- Local-alpha scope only (bounded inspect/edit/validate/save workflows)
- Default model-facing surface remains MVP wrappers:
  - `inspect_graph`
  - `search_blocks`
  - `search_help`
  - `change_graph`
- Legacy low-level tools remain internal/compatibility only
- Advisor remains shadow-only (not promoted)
- Vector retrieval policy remains frozen/read-only
- No planner
