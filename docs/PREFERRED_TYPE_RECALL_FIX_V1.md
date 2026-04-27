# Preferred Type Recall Fix v1

Date: 2026-04-27
Status: COMPLETE — 1-line fix, 6 tests, 705 tests green

## Root Cause

`auto_insert_block` line 83:

```python
suggest_k = 500 if intent["mode"] == "explicit_family" else 5
```

When `preferred_block_type` is provided, `_classify_goal` returns `mode="preferred_type"`, which fell into the `else` branch — using `suggest_k=5`. The preferred_block_type filter at lines 115-127 then searched only the top 5 candidates per connection. Since `blocks_head` ranks ~82 alphabetically, it was never in the pool.

The result: `AUTO_INSERT_NO_GOAL_MATCH` despite the block existing in the catalog and being compatible.

## Exact Fix

**File**: `src/grc_agent/session/auto_insert.py`, line 83

```python
# Before
suggest_k = 500 if intent["mode"] == "explicit_family" else 5

# After
suggest_k = 500 if intent["mode"] in ("explicit_family", "preferred_type") else 5
```

One condition change. No architecture, schema, or prompt changes.

## Tests Added

6 tests in `tests/test_agentic_workflow_insert.py` under `PreferredTypeRecallTests`:

| Test | Verifies |
|------|----------|
| `test_preferred_head_reaches_candidates` | blocks_head found and validates despite deep rank |
| `test_preferred_throttle2_infers_dtype` | throttle2 infers correct dtype, no unrelated fallback |
| `test_preferred_type_never_commits_unrelated` | only preferred type in attempted/options |
| `test_preferred_type_safe_rejection_for_impossible` | nonexistent type returns AUTO_INSERT_NO_GOAL_MATCH |
| `test_preferred_type_multiple_placements_clarify` | 2+ valid placements → clarification |
| `test_preferred_head_regression` | goal + preferred_block_type regression from k=5 bug |

## Before/After

| Case | Before | After |
|------|--------|-------|
| `preferred_block_type="blocks_head"` | AUTO_INSERT_NO_GOAL_MATCH (k=5 truncation) | CLARIFY: byte + float placements, both validate |
| `preferred_block_type="blocks_throttle2"` | AUTO_INSERT_NO_GOAL_MATCH | CLARIFY: byte + float placements, both validate |
| `preferred_block_type="nonexistent_xyz"` | AUTO_INSERT_NO_GOAL_MATCH | AUTO_INSERT_NO_GOAL_MATCH (same, correct) |
| `preferred_block_type="blocks_head"` + `goal="insert head"` | AUTO_INSERT_NO_GOAL_MATCH | CLARIFY with options |

## Full Suite Result

```
705 tests OK, 9 skipped, 0 failures
ruff check src/ tests/ → All checks passed
```

## Remaining Watchlist

- `_confidence` scoring is coarse (all "high") — finer granularity would improve suggest tool (k=5 default) UX. Low priority since auto_insert uses k=500.
- `preferred_type` mode still runs `_score_candidates` which adds goal-word match bonuses. If `goal` is empty/generic, all preferred_type candidates tie at score 2. Harmless — ranked by connection_id, then first valid commits.
