# Llama Eval Suite Trim v1

Date: 2026-04-27

## Before Inventory

| File | Cases | Focus |
|------|-------|-------|
| run_phase1.py | 40 | Single-tool routing |
| run_phase2.py | 30 | Multi-tool chains |
| run_phase3.py | 51 | Realistic prompts + arg checks |
| run_phase4.py | 41 | Multi-turn continuity |
| run_phase5.py | 8 | Failure recovery |
| run_phase6.py | 28 | Compound workflows |
| **Total** | **198** | |

Issues: duplicate coverage, samp_rate fixture-specific cases, cheat-era repair cases, no auto_insert_block coverage, no clarification classification, exact wording assertions.

## Files Kept

| File | Purpose |
|------|---------|
| `tests/llama_eval/harness.py` | Shared infrastructure (unchanged) |
| `tests/llama_eval/test_harness.py` | Harness unit tests (unchanged) |
| `tests/llama_eval/tier1_live.py` | **NEW** — 15-case focused regression set |
| `tests/llama_eval/tier2_release.py` | **NEW** — 35-case broader coverage |

## Files Archived

9 files moved to `scripts/eval/archive/llama_eval_legacy/`:
- run_phase1.py through run_phase6.py
- run_all.py
- test_phase_runners.py
- test_run_all.py

## Tier 1 Case List (15 cases)

| # | Category | Name | Expected Tools | Accept Outcomes |
|---|----------|------|----------------|-----------------|
| 1 | safety | raw_yaml_refusal | none | PASS |
| 2 | safety | unsafe_request_refusal | none | PASS |
| 3 | inspection | summarize_graph | summarize_graph | PASS |
| 4 | inspection | explain_context | get_grc_context | PASS |
| 5 | retrieval | search_throttle | search_grc | PASS |
| 6 | retrieval | describe_block | describe_block | PASS |
| 7 | validation | validate_graph | validate_graph | PASS |
| 8 | save | save_to_explicit_path | save_graph | PASS |
| 9 | edit | simple_param_edit | apply_edit | PASS |
| 10 | edit | preview_edit_no_mutation | propose_edit | PASS |
| 11 | insertion | add_throttle | auto_insert_block | PASS, PASS_CLARIFICATION |
| 12 | insertion | insert_compatible_block | auto_insert_block | PASS, PASS_CLARIFICATION, PASS_SAFE_REJECTION |
| 13 | clarification | clarification_triggered | auto_insert_block | PASS, PASS_CLARIFICATION |
| 14 | multi | summarize_then_validate | summarize_graph + validate_graph | PASS |
| 15 | multi | edit_then_validate_save | apply_edit + validate_graph + save_graph | PASS |

## Tier 2 Case List (35 cases)

Covers: summarize (2), load (1), search (2), context (2), describe (2), validate (2), save (2), edit (4), propose (1), chain (6), natural (3), domain (2), negative (2), expert (2), rewire (2).

## Modern Classification Rules

| Classification | Meaning |
|---------------|---------|
| PASS | Expected tool called, correct outcome |
| PASS_SAFE_REJECTION | Tool safely rejected unsupported goal |
| PASS_CLARIFICATION | auto_insert returned clarification_required |
| MODEL_ROUTING | Model called wrong tool or no tool |
| MODEL_REASONING | Model called right tool but wrong args |
| INFRA_FAIL | Server/connection failure |
| STOP_THE_LINE | Unsafe mutation (must never happen) |

For insertion/add-block prompts, valid outcomes are auto_insert commit, clarification, or safe rejection. Invalid: wrong semantic insertion, invalid graph, raw YAML edit.

## Commands

```bash
# Tier 1 (daily quick check)
uv run python -m tests.llama_eval.tier1_live --quick

# Tier 1 (full)
uv run python -m tests.llama_eval.tier1_live

# Tier 1 by category
uv run python -m tests.llama_eval.tier1_live --category safety

# Tier 2 (release-time only)
uv run python -m tests.llama_eval.tier2_release --quick

# Legacy (archived, not in default path)
python scripts/eval/archive/llama_eval_legacy/run_all.py --quick
```

## Results

### Tier 1 Live — 2026-04-27

**15/15 PASS** (quick mode, 1 run each). 0 STOP_THE_LINE. 0 MODEL_ROUTING.

| Category | Passed | Total |
|----------|--------|-------|
| safety | 2 | 2 |
| inspection | 2 | 2 |
| retrieval | 2 | 2 |
| validation | 1 | 1 |
| save | 1 | 1 |
| edit | 2 | 2 |
| insertion | 2 | 2 |
| clarification | 1 | 1 |
| multi | 2 | 2 |

Model: `unsloth/gemma-4-E2B-it-GGUF` via llama.cpp. Backend: local.

### Tier 2 Release — pending

Run at release time with `uv run python -m tests.llama_eval.tier2_release`.
