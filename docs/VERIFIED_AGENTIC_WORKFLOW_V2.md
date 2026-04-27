# Verified Agentic Workflow v2

## Current workflow

1. Inspect graph (summarize, context)
2. Call `auto_insert_block(goal)` or `insert_block_on_connection(...)`
3. If ambiguous, receive `clarification_required` MCQ payload
4. User selects A/B/C or D/custom
5. Tool executes verified option
6. Optional: `validate_graph` if user asked to confirm

## Safety invariants

- All graph mutations go through `apply_edit` or `insert_block_on_connection`
- `auto_insert_block` never mutates unless `grcc` validates
- Clarification options are pre-validated on cloned sessions
- No raw YAML editing path
- No prompt-regex transaction rewriting
- No fixture-specific logic

## Clarification Contract v1

When `auto_insert_block` finds >=2 validated candidates, it returns a data-driven MCQ.
Options come from real candidate tool args. D is always custom free text.
See `docs/CLARIFICATION_CONTRACT_V1.md`.

## Dtype inference + preferred_type recall

- Template port dtypes now resolve from endpoint instance params (not `options[0]`).
- `preferred_block_type` uses suggest_k=500 to reach deep-ranked blocks.
- See `docs/AUTO_INSERT_RANKING_PARAM_AUDIT_V1.md` and `docs/PREFERRED_TYPE_RECALL_FIX_V1.md`.

## Latest test gate

- `uv run ruff check src/ tests/` — clean
- `uv run python -m unittest` — 705 tests, 0 FAIL
- `tests/test_clarification_contract.py` — 27 tests covering contract invariants
- `tests/test_agentic_workflow_insert.py` — 19 tests covering dtype inference + preferred_type recall
