# R2 Release Critique: Chaos Monkey Multi-Turn Mutation Suite

**Model:** gemma4:e4b-it-qat  
**Surface:** MVP (inspect_graph, query_knowledge, change_graph)  
**Fixture:** random_bit_generator.grc  
**Runs:** 2 | **Pass:** 2 | **Fail:** 0 | **Rate:** 100%

---

## Executive Summary

Both scenarios technically PASS the harness checks, but the model exhibits **severe inefficiency and a persistent surface compliance violation** that inflates tool call counts and latency by 350–700%. Every `change_graph` invocation hallucinates a `reasoning` keyword argument that does not exist in the tool schema, causing each mutation attempt to fail at least once before the model retries without it. The dedup cache blocks one redundant `inspect_graph`. The model also fails to learn error patterns across consecutive calls within the same turn.

---

## Per-Scenario Analysis

### Scenario 1: `chaos/pivot_param_and_state` — PASS

**Prompt chain:**
- Turn 1: "Change the sample rate to 48000."
- Turn 2: "Now change the sample rate to 96000 and bypass the blocks_throttle2_0 block."

**Expected tool chain:** `[change_graph]` → `[change_graph]` (2 calls)  
**Actual tool chain:** 9 calls across 2 turns (`inspect_graph`×3, `change_graph`×6)

#### Turn 1 — 57s, 7 tool calls

| # | Tool | Args | Result | Critique |
|---|------|------|--------|----------|
| 1 | inspect_graph | `{}` | OK, full graph | **Redundant.** Full overview already shows `samp_rate` with value `32000`. This single call provides all necessary info. |
| 2 | inspect_graph | `{params: ["samp_rate"]}` | OK, same data | **Redundant.** Second call is a subset of call #1. The model should parse call #1's output directly — the variable block's available params (`comment`, `value`) are already visible. |
| 3 | change_graph | `{reasoning: "…", update_params: [{instance_name: "samp_rate", params: {sample_rate: 48000}}]}` | **internal_error** — `reasoning` kwarg not supported | **Surface violation + wrong param key.** The keyword `reasoning` is not in the tool schema. Even ignoring that, `sample_rate` is not a valid param; the variable block uses `value`. |
| 4 | change_graph | `{update_params: [{instance_name: "samp_rate", params: {sample_rate: 48000}}]}` | **preflight_rejected** — `parameter_not_found` | **Correct retry (no reasoning) but wrong param name.** The error message clearly states available params are `comment, value`. The model should have used `value` from the inspect_graph output. |
| 5 | inspect_graph | `{params: ["samp_rate"]}` | **deduplicated** — identical to call #2 | **Wasted call.** Hit the dedup cache. The model already knew `samp_rate` is a variable with params `comment, value` from calls #1–2. This call was triggered by the parameter_not_found error, but the error itself listed the available params. |
| 6 | change_graph | `{reasoning: "…previous attempt failed…", update_params: [{instance_name: "samp_rate", params: {value: 48000}}]}` | **internal_error** — `reasoning` kwarg not supported | **Surface violation.** Correct param `value` this time, but the `reasoning` kwarg kills it again. |
| 7 | change_graph | `{update_params: [{instance_name: "samp_rate", params: {value: 48000}}]}` | **OK** — graph validated, rev 2 | **Finally succeeds.** Correct args, no reasoning. |

**Model reply:** "The sample rate has been successfully changed to 48000 by updating the 'value' parameter of the `samp_rate` variable block." — Accurate.

**Assessment:**
- **Multi-turn planning:** Not tested in this turn (single-step request).
- **Tool selection:** Correct tool for intent, but the two `inspect_graph` prologue is wasteful.
- **Argument quality:** Wrong param key (`sample_rate`) then correct `value`, but persistently injects `reasoning`.
- **Efficiency: 1 call needed → 7 used.** ~600% overhead.
- **Surface compliance:** Violated — `reasoning` kwarg in every change_graph call.
- **Root cause of thrash:** 1) `reasoning` hallucination, 2) not reading variable block's param names from inspect_graph output (used `sample_rate` instead of `value`), 3) dedup cache hit from redundant re-inspect.

#### Turn 2 — 18s, 2 tool calls

| # | Tool | Args | Result | Critique |
|---|------|------|--------|----------|
| 1 | change_graph | `{reasoning: "…", update_params: [{instance_name: "samp_rate", params: {value: 96000}}], update_states: [{instance_name: "blocks_throttle2_0", state: "bypass"}]}` | **internal_error** — `reasoning` kwarg not supported | **Surface violation.** Good argument structure and correct batching, but blocked by `reasoning`. |
| 2 | change_graph | `{update_params: [..value:96000..], update_states: [..state:"bypass"..]}` | **OK** — rev 2→4, validated | **Excellent.** Correct params, correct batch, correct state value `"bypass"`. |

**Model reply:** "The sample rate has been changed to 96000, and the `blocks_throttle2_0` block is now bypassed." — Accurate.

**Assessment:**
- **Multi-turn planning:** Excellent — correctly remembers `value` (not `sample_rate`) from turn 1's lesson. Batches both mutations into one call.
- **Tool selection:** Correct.
- **Argument quality:** Perfect payload on retry. `update_states` format is correct.
- **Efficiency:** 1 call needed → 2 used (overhead only from `reasoning`).
- **Surface compliance:** Still violated on first attempt.

---

### Scenario 2: `chaos/pivot_add_then_remove_block` — PASS

**Prompt chain:**
- Turn 1: "Add a new variable named 'temp_var' with value 42."
- Turn 2: "Remove the temp_var variable completely. The graph should be clean with no orphans."

**Expected tool chain:** `[change_graph]` → `[change_graph]` (2 calls)  
**Actual tool chain:** 7 calls across 2 turns (`query_knowledge`×1, `change_graph`×6)

#### Turn 1 — 37s, 4 tool calls

| # | Tool | Args | Result | Critique |
|---|------|------|--------|----------|
| 1 | query_knowledge | `{domain: "catalog", query: "constant value block"}` | OK, returns `variable` as top result | **Unnecessary.** The model is told to add "a new variable" — it should know `variable` is the block type without a catalog lookup. Not a violation but wastes 0.77s. |
| 2 | change_graph | `{reasoning: "…", add_blocks: [{block_id: "variable", instance_name: "temp_var", params: {value: 42}}]}` | **internal_error** — `reasoning` kwarg | **Surface violation.** Block payload is correct. |
| 3 | change_graph | `{reasoning: "…", add_blocks: [same as above]}` | **internal_error** — `reasoning` kwarg | **Duplicate reasoning call.** Model tries the same failing payload again. |
| 4 | change_graph | `{add_blocks: [{block_id: "variable", instance_name: "temp_var", params: {value: 42}}]}` | **OK** — graph validated, rev 1→2 | **Finally succeeds.** Correct payload, no reasoning. |

**Model reply:** "I have successfully added a new variable named **temp_var** with a value of **42**." — Accurate.

**Assessment:**
- **Tool selection:** query_knowledge is harmless but unnecessary; change_graph is correct.
- **Argument quality:** `add_blocks` payload is correct (block_id: "variable", params, instance_name).
- **Efficiency:** 1 call → 4 used. 300% overhead, entirely from `reasoning` + duplicate reasoning retry.
- **Surface compliance:** Violated (reasoning kwarg).
- **Note:** The model called change_graph twice with the same `reasoning`-laden args — it should have learned from the first `internal_error`.

#### Turn 2 — 27s, 3 tool calls

| # | Tool | Args | Result | Critique |
|---|------|------|--------|----------|
| 1 | change_graph | `{reasoning: "…", remove_blocks: [{instance_name: "temp_var"}]}` | **internal_error** — `reasoning` kwarg | **Surface violation.** Remove payload is correct. |
| 2 | change_graph | `{reasoning: "…", remove_blocks: [{instance_name: "temp_var"}], add_blocks: [], add_connections: [], remove_connections: [], update_params: [], update_states: [], force: false}` | **internal_error** — `reasoning` kwarg | **Inflated, still wrong.** Adds empty lists for every possible field, but the core problem is still `reasoning`. |
| 3 | change_graph | `{remove_blocks: [{instance_name: "temp_var"}]}` | **OK** — rev 2→3, validated | **Finally succeeds.** Minimal correct payload. |

**Model reply:** "The variable **temp_var** has been completely removed from the graph." — Accurate.

**Assessment:**
- **Multi-turn planning:** Not tested (single-step request).
- **Tool selection:** Correct.
- **Argument quality:** `remove_blocks` with `instance_name` is correct format.
- **Efficiency:** 1 call → 3 used. 200% overhead.
- **Surface compliance:** Violated.
- **Note:** Call #2 is notable — the model tried to "fix" the issue by explicitly zeroing every other field rather than removing the `reasoning` kwarg. This shows it didn't understand the error source despite seeing it fail identically on call #1.

---

## Cross-Cutting Patterns

### 1. Persistent `reasoning` Hallucination (100% of change_graph calls)

Every single `change_graph` invocation in this release (9 out of 9 total, across both scenarios) includes a `reasoning` keyword argument that the tool schema does not accept. The model never learns:
- After getting `internal_error: got an unexpected keyword argument 'reasoning'` once, it immediately retries with the same kwarg
- After getting the error twice in a row, it retries a *third* time without `reasoning` and succeeds
- Next turn, it does the exact same 3-step pattern again

This suggests the model has a strong prior that `reasoning` is a valid argument — likely from the tool's natural language description or similar tools in its training data — and surface-level error messages do not override this prior.

### 2. Redundant Inspection (Scenario 1 only)

The model calls `inspect_graph` twice before the first mutation:
- Call #1: full overview (already sufficient)
- Call #2: targeted params query (subset of call #1 output)
- Recovery inspect_graph after failure hits dedup cache (wasted)

The model could have parsed all needed information from call #1 alone.

### 3. Param Name Inference Failure

The variable block `samp_rate` has param `value` (not `sample_rate`). The model guesses `sample_rate` despite:
- The variable block's `name` being `samp_rate` (a strong signal for a rate variable)
- The block type being `variable` with only `{comment, value}` as available params in inspect_graph output

The model anthropomorphizes param names instead of reading the schema.

### 4. Failure to Learn Across Turns

In scenario 1 turn 1, the model learns that `value` is the right param (corrects from `sample_rate` to `value`). This knowledge persists to turn 2 — good. But the `reasoning` kwarg lesson does NOT persist across turns. Each turn starts fresh with the wrong prior.

### 5. Deduplication Cache Helps and Hinders

- **Helps:** In scenario 1 turn 1, the redundant recovery inspect_graph is blocked (saves a round trip)
- **Hinders:** The model doesn't know why it was blocked — it sees a failure but no useful data, which may lead to confusion or cascading retries

---

## Proposed Improvements

### High Priority (Blocking Real-World Use)

1. **Strip `reasoning` from change_graph schema or accept it silently**
   - If the model has a strong prior that mutation tools accept `reasoning`, either (a) add an optional `reasoning` field to the schema that is accepted but ignored/logged, or (b) improve the internal_error message to explicitly say "remove the 'reasoning' field" rather than "got an unexpected keyword argument 'reasoning'"

2. **Prompt engineering: Add anti-hallucination instruction**
   - Add to the system prompt: "The 'reasoning' parameter is NOT a valid argument for any tool. Do not include it."

3. **Improve error messages for hallucinated kwargs**
   - Instead of `GrcAgent._change_graph() got an unexpected keyword argument 'reasoning'`, use: `change_graph does not accept a 'reasoning' parameter. Remove it and retry.`

### Medium Priority (Efficiency)

4. **Teach the model to parse block params from inspect_graph output**
   - The model should not guess param names. If inspect_graph shows a variable block has `{comment, value}`, use those exact names.

5. **Reduce prologue inspect_graph calls**
   - For simple param changes, one `inspect_graph` with `params` targeting the specific block is sufficient. A full overview adds no useful signal.

6. **Implement recovery retry budget with backoff**
   - After 2 identical errors in a row, the recovery layer should inject guidance rather than letting the model retry with the same broken payload 3+ times.

### Low Priority (Nice-to-Have)

7. **Scenario 2 turn 1: skip query_knowledge for well-known block types**
   - "variable" is a fundamental block type. The model should not need a catalog lookup unless `block_id` is unknown.

8. **Inter-turn memory for surface compliance**
   - The model remembers param names across turns but not surface rules. Consider adding turn-level state about which kwarg patterns were rejected.

---

## Summary Table

| Metric | Scenario 1 | Scenario 2 | Combined |
|--------|-----------|-----------|----------|
| Expected calls | 2 | 2 | 4 |
| Actual calls | 9 | 7 | 16 |
| Overhead | +350% | +250% | +300% |
| Avg elapsed | 37.7s | 31.9s | 34.8s |
| Ideal elapsed (est.) | ~10s | ~10s | ~10s |
| `reasoning` violations | 4/9 | 5/7 | 9/16 |
| Unnecessary tool calls | 3 (inspect_graph×2 + dedup ×1) | 1 (query_knowledge) | 4 |
| Failed retro calls | 4 | 4 | 8 |
| Param name errors | 1 (sample_rate) | 0 | 1 |

**Verdict:** 100% pass rate masks severe inefficiency and a systematic surface compliance failure. The model can complete tasks but consumes 3–7× the minimum tool calls. The `reasoning` kwarg hallucination is the single largest drag — addressing it would eliminate ~50% of all failed tool calls and bring real call counts much closer to ideal.
