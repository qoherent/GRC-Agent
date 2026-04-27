# Release Validation v1

Date: 2026-04-27

## 1. Baseline Gates

| Gate | Result |
|---|---|
| Backend | unsloth/gemma-4-E2B-it-GGUF (llama.cpp) |
| GNU Radio | 3.10.9.2 |
| Ruff | All checks passed |
| Unittest | 681 OK, 9 skipped, 0 failures |
| Tier 1 live | 15/15 PASS |
| Tier 2 full | 35/36 PASS |
| STOP_THE_LINE | 0 |
| INFRA_FAIL | 0 |

## 2. Tier 2 Full Result

36 cases, 35 passed, 1 failed.

By category:

| Category | Passed | Total |
|---|---|---|
| summarize | 2 | 2 |
| load | 1 | 1 |
| search | 2 | 2 |
| context | 2 | 2 |
| describe | 2 | 2 |
| validate | 2 | 2 |
| save | 2 | 2 |
| edit | 4 | 4 |
| propose | 1 | 1 |
| chain | 7 | 7 |
| natural | 3 | 3 |
| domain | 1 | 2 |
| negative | 2 | 2 |
| expert | 2 | 2 |
| rewire | 2 | 2 |

## 3. Tier 2 Failure Triage

### `domain/want_to_see_spectrum`

| Field | Value |
|---|---|
| Prompt | "I want to see the spectrum of my signal." |
| Expected tool | `search_grc` |
| Actual tool | `summarize_graph` |
| Graph state before | `random_bit_generator.grc`: 5 blocks, 3 connections |
| Graph state after | Unchanged (summarize_graph is read-only) |
| Answer useful? | Partially — showed current blocks but did not search for spectrum block |
| Safety issue? | No — no mutation, no invalid save |

### Diagnosis: Case B — Model routing limitation

`search_grc` is clearly the correct tool: user wants to find a spectrum analysis block. The model chose `summarize_graph` instead, interpreting "see the spectrum" as "show me what I have." The sibling case `domain/need_carrier_recovery` (same category, same expected tool) passed correctly, confirming the eval expectation is right. No tool description ambiguity. No runtime change warranted.

## 4. Stale Expectation Audit

Audited all 36 Tier 2 cases against the current tool contract (13 tools):

- All edit cases use `apply_edit` (no stale manual add/remove/connect expectations)
- No exact wording assertions — matching is tool-name based
- No hidden cheat-era expectations — cases use the standard harness
- No stale `insert_block_on_connection` standalone expectations
- Negative/expert cases correctly expect no tools (`[]`)

**Result: No stale expectations. All 36 cases reflect the current tool contract.**

## 5. Manual External Checklist Results

### Graph 1: dial_tone.grc

| Field | Value |
|---|---|
| Graph path | `/usr/share/gnuradio/examples/audio/dial_tone.grc` |
| Original grcc result | Exit 0 (valid) |
| Step 1: Summarize | `summarize_graph` called, correct |
| Step 2: Validate | `validate_graph` called, valid |
| Step 3: Insert throttle | `auto_insert_block` called; safe rejection (vague goal, asked for clarification) |
| MCQ shown | No |
| Step 5: Edit samp_rate | `apply_edit` called, changed to 48000 |
| Step 6: Save | `save_graph` called |
| Saved path | `/tmp/grc_agent_external_test/dial_tone_output.grc` |
| Saved grcc result | Exit 0 (valid) |
| Classification | PASS |
| Notes | Insert was safe rejection (asked for connection details) — correct behavior for vague insert on multi-connection graph |

### Graph 2: selector.grc

| Field | Value |
|---|---|
| Graph path | `/usr/share/gnuradio/examples/blocks/selector.grc` |
| Original grcc result | Exit 0 (valid, throttle warning — same as original) |
| Step 1: Summarize | `summarize_graph` called, correct |
| Step 2: Validate | `validate_graph` called, valid |
| Step 3: Insert throttle | `auto_insert_block` called; safe rejection (vague goal) |
| MCQ shown | No |
| Step 5: Edit samp_rate | `apply_edit` called, changed to 48000 |
| Step 6: Save | `save_graph` called |
| Saved path | `/tmp/grc_agent_external_test/selector_output.grc` |
| Saved grcc result | Exit 0 (valid, same throttle warning as original) |
| Classification | PASS |
| Notes | Throttle warning exists in original — not agent regression |

## 6. Fixes Made

None. No code or eval changes required.

- Tier 2 failure is justified model routing limitation (Case B)
- No stale eval expectations found
- No safety issues found
- Manual external checklist passed on both graphs

## 7. Safety Status

- 0 STOP_THE_LINE across all evals
- 0 INFRA_FAIL across all evals
- No unsafe mutations in manual external tests
- No raw YAML exposed to users
- grcc validates clean on all saved outputs
- Original graphs not modified (temp copies used)

## 8. Final Release Readiness

| Criterion | Status |
|---|---|
| Deterministic suite green | 681 OK, 9 skipped |
| Ruff clean | All checks passed |
| Tier 1 green | 15/15 PASS |
| Tier 2 no STOP_THE_LINE | 0 |
| Tier 2 failures justified | 1/36 MODEL_ROUTING (want_to_see_spectrum) |
| External checklist passes | 2/2 graphs PASS |
| No unresolved docs/test contradictions | Confirmed |

**Release ready.**

## 9. Next Recommendation

No further action required for release. The single Tier 2 MODEL_ROUTING failure is a known model limitation, not a product defect. If model upgrades improve routing accuracy, this case may pass naturally on re-evaluation.
