# DSP Gauntlet Critique — gemma4:e4b-it-qat

## Summary

- **Total scenarios:** 40
- **Passed:** 4 (10.0%)
- **Failed:** 36 (90.0%)
- **Mean tool calls per scenario:** 5.7 (median: 6, max: 8, min: 1)

| DSP Category | Scenarios | Passes | Pass Rate |
|---|---|---|---|
| Cascade (sample rate doubling) | 3 | 2 | 66% |
| MAC Sniffer (message debug PDU) | 8 | 1 | 12.5% |
| Notch Filter (band reject insertion) | 8 | 1 | 12.5% |
| Inline Block Swap (char→float replacement) | 9 | 0 | 0% |
| QAM Upgrade (QPSK→16/64/256-QAM) | 3 | 0 | 0% |
| Typo Correction (add AGC block) | 9 | 0 | 0% |

---

## Category Breakdown

### Notch Filters (8 scenarios, 1 pass)

The lone pass (`notch_sr2000000_cf20000_bw2000`) required 7 tool calls: inspect_graph → query_knowledge → 5× change_graph (4 failed), with the last succeeding by splicing `band_reject_filter` into the add_xx→qtgui_freq_sink path.

**Failures cluster into three patterns:**
1. **Early bailout** (3 scenarios): 2-4 calls only, never attempt a full splice. The model quit after `query_knowledge` or a single failed `change_graph` (reasoning keyword error).
2. **Schema-invalid arguments** (1 scenario): `tool_call_invalid` error — the model sent malformed port definitions (named ports instead of integer indices, or missing `force` flag).
3. **Block ID hallucination** (4 scenarios): Tried `block_id: "band_reject_filter"` which is correct, but the param names (`high_freq`/`low_freq` vs `high_cutoff_freq`/`low_cutoff_freq`) were wrong, or the model used `reasoning` keyword, exhausting budget before figuring out the right param keys.

The pass succeeded only when the model eventually omitted `reasoning` and used the correct param keys (`high_cutoff_freq`, `low_cutoff_freq`, `width`) — requiring 4 failed attempts to arrive at the right formulation.

### QAM Upgrades (3 scenarios, 0 passes)

All three fail with the same root causes:
- **Non-MVP tool use** (`qam_order256`): attempted `update_params` tool which is not in the model-facing surface, triggering `tool_not_allowed_for_surface`. This also caused `model_contract_pass: FAIL`.
- **Parameter name mismatch**: `digital_constellation_modulator_0.modulation="256-QAM"` was rejected — the actual param key is different (likely `constellation` or `modulation` doesn't accept string values).
- **`reasoning` keyword** on change_graph calls wasted budget.
- `qam_order16` only made 1 tool call (query_knowledge) then gave up without attempting any mutation.

### MAC Sniffer (8 scenarios, 1 pass)

The lone pass (`mac_sniffer_1159513892`) consumed all 8 tool rounds: 5× change_graph (4 with `reasoning` → err), query_knowledge, then 2× change_graph (1 with `reasoning` → err, 1 finally ok).

**Block ID confusion dominates failures:**
- The model cycles between `"message_debug"` and `"blocks_message_debug"` as block_id. Both are wrong or wrong in different contexts:
  - `message_debug` → `unknown_block_id` (preflight_rejected)
  - `blocks_message_debug` → `gnu_validation_failed` (unconnected ports or wrong port number)
- **Port index confusion**: The model connects to port `0` or `1` (stream ports) rather than the PDU message port. The prompt explicitly says "asynchronous message ports, not standard stream ports" but the model never adjusts from numeric port indices.
- 4/8 scenarios hit the 8-round limit and were forcibly terminated.

### Inline Block Swap (9 scenarios, 0 passes)

**100% failure rate — the worst-performing category.**

The model universally hallucinates `block_id: "blocks_float_to_float"` which does not exist in the GNU Radio block catalog. Every single scenario uses this same non-existent block ID.

After preflight rejects it with `unknown_block_id`, the model searches for "float to float", "type cast block", "type converter", "float passthrough", "char to float" — none of which return matching results for `blocks_float_to_float`. The model never finds the actual valid block_id and eventually exhausts budget or gives up.

The correct block for a float→float pass-through in GNU Radio is typically `blocks_copy` or a custom block — the model has no way to infer this from the prompt "Replace with a blocks_float_to_float block."

### Typo Correction — Add AGC (9 scenarios, 0 passes)

All fail with the identical pattern:
1. `query_knowledge("AGC")` — finds `analog_agc_xx` ✓
2. `change_graph(add_blocks=[analog_agc_xx])` — fails with `gnu_validation_failed: "Source - out(0): Port is not connected."`
3. Repeat step 2 three to five times (sometimes with `reasoning` keyword, sometimes not)
4. End — never successfully adds the block

The model never addresses the fundamental constraint: GNU Radio requires all source ports to have downstream connections. Adding an AGC block inline requires either:
- The `force=true` flag to bypass validation
- Simultaneously adding connection wiring
- The prompt explicitly says "Add an AGC block" without wiring context

The model fails to detect this pattern and apply `force=true`, instead repeating the same doomed operation.

### Cascade — Sample Rate Doubling (3 scenarios, 2 passes)

**Best-performing category but still fragile:**
- Both passes required multiple failed `change_graph` calls due to the `reasoning` keyword issue.
- The 2MHz fail (`cascade_sr2000000_x2`) made only 3 tool calls total: 2× inspect_graph + 1× change_graph (with `reasoning` → error) then gave up. The passing cases at 32kHz/96kHz had 4-5 changed_graph calls, persisting past the `reasoning` error.
- No attempt to update dependent block params besides `samp_rate.value` — if the graph requires cascade updates to other blocks (throttle, filter params), the model didn't do it.

---

## Per-Scenario Highlights

### PASS: `cascade_sr32000_x2` (profile: R1_SET_PARAM_ONLY)
- **5 tool calls** — but 4 of 5 failed with `internal_error` (reasoning keyword)
- **Final success** simply dropped the `reasoning` key. Same params otherwise.
- **No cascade to dependent blocks** — the prompt says "Update all dependent block parameters" but only `samp_rate.value=64000` was changed.
- **Verdict:** Brittle pass. Correct end state but >80% wasted tool calls.

### PASS: `notch_sr2000000_cf20000_bw2000` (profile: R3_REWIRE)
- **7 tool calls**, 5 of them `change_graph`, only the last succeeded
- **Key insight:** The model correctly identified the signal path (blocks_add_xx_0 → qtgui_freq_sink_x_0), disconnected it, inserted `band_reject_filter` with params `high_cutoff_freq=21000`, `low_cutoff_freq=19000`, `width=1`, and reconnected the chain.
- **But:** The first 4 change_graph calls all failed — 2x reasoning keyword, 1x preflight_rejected (wrong port indices), 1x gnu_validation_failed.
- **Verdict:** Passing only because the model got enough retries to stumble into correct arguments. 

### FAIL: `inline_swap_1540227808` (profile: R3_REWIRE)
- **8 tool calls** (hit budget cap)
- **Tool pattern:** inspect_graph → 3× change_graph (with reasoning, internal_error) → 4× query_knowledge (searching for "float to float", "type cast", "type converter", "float cast")
- **Root cause:** The model hallucinates `"blocks_float_to_float"` as a block_id. No GNU Radio block with that ID exists. After exhausting 4 query_knowledge searches (all returning no match or irrelevant results), the model hits the round limit and fails.
- **Verdict:** The scenario prompt itself is problematic — it asks to replace with a block whose canonical name the model can't know. The real GNU Radio block for float passthrough is `blocks_copy`, not `blocks_float_to_float`.

### FAIL: `typo_agc_379298624` (profile: R1_SET_PARAM_ONLY)
- **3 tool calls**: query_knowledge → 2× change_graph (add AGC, gnu_validation_failed)
- **Root cause:** Adding an unconnected block. The test harness validates the graph after each mutation and rejects blocks with floating ports. The model never learns to use `force=true` or add connections.
- The error message helpfully says "Source - out(0): Port is not connected." but the model repeats the exact same attempted operation.
- **Verdict:** The model cannot recover from validation errors that require a different strategy (use `force` or add connections).

### FAIL: `qam_order256` (profile: R1_SET_PARAM_ONLY)
- **8 tool calls** (hit budget cap)
- **Highlights:** Attempted non-MVP tool `update_params` (model_contract violation) → attempted `change_graph` with `reasoning` → eventually sent `update_params` inside `change_graph` with param key `modulation="256-QAM"` (wrong key, preflight_rejected) and `bits=8` for the random source (correct intent, wrong field).
- **Verdict:** Two compounding errors — non-MVP tool use + wrong parameter keys.

---

## Cross-Cutting Patterns

### 1. The `reasoning` keyword bug dominates failures (68 occurrences)

The `change_graph` tool does **not** accept a `reasoning` parameter, but the model persistently includes it — **75% of scenarios (30/40) make at least one call with `reasoning: "..."`**. Every such call returns `internal_error`, consuming ~68 tool call slots across the gauntlet.

This is a **tool schema misalignment**: the model was trained on tools that accept `reasoning` as a field (standard in function-calling LLM patterns), but this backend's `change_graph` strips it. The schema should either:
- Accept and silently discard `reasoning`, or
- Include it in the schema explicitly (even if unused server-side)

### 2. Validation error recovery is nonexistent

When `gnu_validation_failed` or `preflight_rejected` fires, the model almost never adjusts its strategy:
- Typo AGC: Repeats the identical add-block-without-connections call 2-5 times
- Inline swap: Repeats the identical block_id call after `unknown_block_id`
- Notch filter: Tries slight variations but never consults the error details to correct param names

### 3. Block ID hallucination

The model invents block IDs that don't exist:
- `"blocks_float_to_float"` (9/9 inline_swap scenarios)
- `"message_debug"` (wrong prefix, missing `blocks_`)
- Parameter keys like `"modulation"` for constellation modulator

The `query_knowledge` tool returns valid catalog entries, but the model ignores them in favor of hallucinated IDs from its training data.

### 4. No MVP surface violations (mostly)

Only 1 scenario (`qam_order256`) attempted a non-MVP tool (`update_params`). Zero attempts at `python`, `bash`, or other system tools. **Surface compliance is good** — the model stays within `inspect_graph`/`query_knowledge`/`change_graph`.

### 5. Edge scenarios fail faster

Scenarios that require ≤2 tool calls are exclusively failures (4 scenarios): the model bails out early after seeing `query_knowledge` results or an error. The pass scenarios all required 5-8 calls — persistence correlates with success.

### 6. Wire-rewiring (add/remove connections) is harder than param-only changes

Cascade (param-only, profile R1): 2/3 pass.  
Notch (rewire, profile R3): 1/8 pass.  
Inline swap (rewire, profile R3): 0/9 pass.

Multi-step graph surgery (disconnect → add block → configure → reconnect) dramatically increases failure rate. The model can handle simple param updates but cannot reliably orchestrate topological changes involving connection rewiring.

### 7. Dedup cache fires on repeated calls

Several scenarios (e.g., `notch_sr2000000_cf10000_bw5000`, `cascade_sr96000_x2`) hit `deduplicated: true` when repeating identical `inspect_graph` calls, wasting budget. The model repeats `inspect_graph({})` at least once per scenario, and in 2 cases the duplicate fires because the model didn't change its query.

---

## Proposed Improvements

### Critical (immediate impact)

1. **Accept `reasoning` in `change_graph` schema**
   - Current: The schema lacks `reasoning`, causing 68+ internal errors (50% of all failures).
   - Fix: Add `reasoning` as an optional string parameter to the change_graph tool schema. Even if the backend discards it, the model won't get the `internal_error` rejection, saving 1-2 tool calls per scenario that can go toward actual work.

2. **Expose `force` flag better mechanism or auto-force for orphan blocks**
   - The `gnu_validation_failed` on unconnected ports (29 occurrences) kills typo_agc and partial block additions. Either:
     a. Make the tool auto-apply `force=true` when the user explicitly asks to add a block without specifying connections, or
     b. Provide a clearer error path that suggests `force=true`, or
     c. Add a two-phase approach: allow adding blocks in a "draft" state before connecting them.

3. **Fix block_id hallucination for inline_swap**
   - The scenario prompt references `blocks_float_to_float` — this block doesn't exist in GNU Radio's catalog. Either:
     a. Change the test scenario to use a real block ID (e.g., `blocks_copy`), or
     b. Ensure `query_knowledge` maps "float to float" to a real block and communicates the correct ID.
   - As designed, this scenario is **unsolvable** by the model.

### High Priority

4. **Improve error feedback in validation failures**
   - When `preflight_rejected` fires with `unknown_block_id`, the model ignores the error and retries the same ID. Consider appending a strong signal (not an ALL-CAPS directive) that indicates the block_id was not found — perhaps the error message should include near-matches from the catalog.

5. **Reduce identical retry loops**
   - The model repeats identical failed calls 2-5 times in many scenarios. Consider:
     a. A stricter dedup that includes error state context, or
     b. A circuit-breaker after N identical failures for the same operation that forces a new strategy.

6. **Add port name resolution for message/PDU connections**
   - MAC sniffer scenarios fail because the model uses numeric port indices (0, 1) where named ports (`pdu_print`, `pdus`) are required. Either:
     a. Normalize numeric-to-named port mapping server-side, or
     b. Include port-name hints in the `inspect_graph` response for connection targets.

### Medium Priority

7. **Budget-aware early exit for unsolvable scenarios**
   - 3 notch scenarios and 1 QAM scenario terminated with ≤2 tool calls, giving up prematurely. The model should at minimum attempt mutation before declaring inability.
   
8. **Parameter key discovery loop**
   - The model guesses param keys (`modulation`, `high_freq`) that don't match schema. A `query_knowledge(domain="catalog", query="band_reject_filter params")` often returns the correct keys, but the model doesn't use them before mutating. Encourage a "look before you leap" pattern.

9. **Model contract hardening for tool surface boundary**
   - Only 1 scenario violated model contract (`update_params`), but that tool is listed in error messages (`allowed_tools: ["change_graph", "inspect_graph", "query_knowledge"]`). Remove all references to internal/non-MVP tools from model-visible strings.
