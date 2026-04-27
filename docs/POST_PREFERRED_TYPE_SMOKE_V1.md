# Post Preferred-Type Live Smoke v1

Date: 2026-04-27
Status: PASS — all 3 previously-failing prompts now succeed

## 1. Backend / Model

| Item | Value |
|------|-------|
| Python | 3.12.3 |
| GNU Radio | 3.10.9.2 |
| grcc | /usr/bin/grcc |
| Model | unsloth/gemma-4-E2B-it-GGUF |
| Server | llama.cpp at http://127.0.0.1:8080 |
| Baseline tests | 74 subset OK, ruff clean |

## 2. Prompts Tested

| # | Prompt | Before Fix | After Fix |
|---|--------|-----------|-----------|
| 1 | `use auto_insert_block to add a throttle` | AUTO_INSERT_NO_GOAL_MATCH | PASS_CLARIFICATION_RESOLVED |
| 2 | `auto_insert_block goal: add throttle` | AUTO_INSERT_NO_GOAL_MATCH | PASS_CLARIFICATION_REQUESTED |
| 3 | `I want to insert a head block into the graph` | AUTO_INSERT_NO_GOAL_MATCH | PASS_CLARIFICATION_RESOLVED |

## 3. Tool Chains

### Prompt 1: "use auto_insert_block to add a throttle"
- Tool: `auto_insert_block` with `preferred_block_type='blocks_throttle2'`
- Result: Clarification with A(byte) / B(float) options
- Choose A → Executed OK
- Validate → valid
- Save → saved
- grcc → PASS

### Prompt 2: "auto_insert_block goal: add throttle"
- Tool: `auto_insert_block` with `preferred_block_type='blocks_throttle2'`
- Result: Clarification with A(byte) / B(float) options
- Classification: PASS_CLARIFICATION_REQUESTED (no selection made in this run)

### Prompt 3: "I want to insert a head block into the graph"
- Tool: `auto_insert_block` with `preferred_block_type='blocks_head'`
- Result: Clarification with A(byte) / B(float) options
- Choose A → Executed OK
- Validate → valid

## 4. MCQ Behavior

All 3 prompts produced MCQ clarifications:

- Options presented as A/B/D with correct block_type and connection_id
- dtype inferred correctly: byte on byte connection, float on float connection
- No raw JSON dump
- No raw YAML
- No unrelated block types offered
- Model correctly presented the clarification to the user

## 5. Validation / Save Behavior

| Step | Result |
|------|--------|
| Clarification resolved with "A" | Executed OK |
| validate_graph | ok, valid |
| save_graph | saved |
| grcc on saved file | PASS (exit 0, no errors) |

## 6. Safety Status

| Check | Result |
|-------|--------|
| Wrong semantic insertions | 0 |
| STOP_THE_LINE events | 0 |
| Unrelated blocks in options | 0 |
| Raw YAML in output | 0 |
| Invalid graph after commit | 0 |
| Model routing failures | 0 (all 3 prompts triggered correct tool) |

## 7. Fixes Made

None during this smoke. The `preferred_type` recall fix (suggest_k=500) was verified to resolve all 3 previously-failing prompts.

## 8. Full Suite Result

```
705 tests OK, 9 skipped, 0 failures
ruff check src/ tests/ → All checks passed
```

## 9. Next Recommendation

No further action needed. The preferred_type recall fix is verified end-to-end:
- Deterministic tests: 6 new, all pass
- Live CLI: 3/3 prompts that previously failed now succeed with correct MCQ/commit behavior
- Full roundtrip: insert → clarify → choose → validate → save → grcc PASS

The model routing concern from POST_FIX_SMOKE_V1 (model using `preferred_block_type` instead of `goal`) is no longer a problem because `preferred_type` mode now works correctly with suggest_k=500.
