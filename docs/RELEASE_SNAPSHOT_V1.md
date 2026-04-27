# Release Snapshot v1

Date: 2026-04-27

## Architecture Summary

Local-first GNU Radio Companion assistant. Text-only, tool-bounded, single-process.

| Layer | Component | Purpose |
|-------|-----------|---------|
| Raw | `.grc` YAML | Persistence |
| Session | `flowgraph_session.py` | Load, mutate, validate, save |
| Catalog | `catalog/` | Block metadata, defaults, ports |
| Retrieval | `retrieval/` | Search |
| Validation | `validation/` | Preflight checks |
| Transaction | `transaction/` | Atomic propose/apply |
| Runtime | `agent.py` | Tool registry, schemas, clarification |
| Adapter | `llama_server.py` | llama.cpp transport |
| CLI | `cli.py` | doctor, chat, tool, fake |

## Tool Count: 13

1. `new_grc`
2. `load_grc`
3. `summarize_graph`
4. `search_grc`
5. `get_grc_context`
6. `describe_block`
7. `suggest_compatible_insertions`
8. `insert_block_on_connection`
9. `auto_insert_block`
10. `apply_edit`
11. `propose_edit`
12. `validate_graph`
13. `save_graph`

## Safety Guarantees

- All mutations go through `apply_edit` or `insert_block_on_connection`
- `auto_insert_block` never mutates unless grcc validates
- Clarification options are pre-validated on cloned sessions
- No raw YAML edit path
- No prompt-regex transaction rewriting
- No fixture-specific logic
- No hidden repairs
- Wrong semantic insertion is worse than safe rejection
- 0 unsafe mutations across all eval runs

## Agentic Insertion Status

Working for tested float/byte/complex preferred-type cases, conservative elsewhere.

- `auto_insert_block(goal=...)` — explicit family goals use suggest_k=500
- `auto_insert_block(preferred_block_type=...)` — preferred_type uses suggest_k=500
- Template dtype resolved from endpoint instance params (not options[0])
- 0 wrong semantic insertions across 16 deterministic test cases (4 graphs × 4 goals)

Key fixes applied:
- `docs/AUTO_INSERT_RANKING_PARAM_AUDIT_V1.md` — dtype inference + suggest_k increase
- `docs/PREFERRED_TYPE_RECALL_FIX_V1.md` — preferred_type recall fix

## MCQ Clarification Status

CLI REPL supported. User replies A/B/C/D; no model turn needed for option execution.
Single-turn `--message` renders clarification but cannot wait for reply.

- `docs/CLARIFICATION_CONTRACT_V1.md` — full contract spec
- `tests/test_clarification_contract.py` — 27 tests
- `tests/integration/test_cli_clarification_repl.py` — 5 REPL tests

## Backend Status

- Default: `unsloth/gemma-4-E2B-it-GGUF` (2B Q4) via llama.cpp
- Optional: `unsloth/gemma-4-E4B-it-GGUF` (not default)
- 2B retained as smallest, fastest, most stable
- mmproj disabled via `--no-mmproj` when supported

## Latest Validation Results

```
705 tests OK, 9 skipped, 0 failures
ruff check src/ tests/ → All checks passed
grcc → all saved files pass
```

Live CLI smoke (3 prompts, all previously failing):
- "use auto_insert_block to add a throttle" → PASS_CLARIFICATION_RESOLVED
- "auto_insert_block goal: add throttle" → PASS_CLARIFICATION_REQUESTED
- "I want to insert a head block into the graph" → PASS_CLARIFICATION_RESOLVED

## Known Limitations

1. 2B model cannot synthesize exact insertion args autonomously
2. Model often sets `preferred_block_type` instead of `goal` — now works either way
3. Duplicate instance-name disambiguation not fully editable
4. `SAVE_PATH_REQUIRED` after `new_grc` may require user follow-up
5. Expert GNU/DSP knowledge depends on model quality
6. `suggest_compatible_insertions` top-5 ranking is alphabetical (no semantic ordering)

## Recommended Next Work

- Finer-grained `_confidence` scoring for suggest tool (currently all "high")
- Semantic ranking in `suggest_compatible_insertions` (prefer dtype-matching blocks)
- Stronger backend/model comparison (E4B or larger models)
- Optional `new_grc(path=...)` UX improvement
