# Phase 13 Production Readiness: Multi-Turn Composition Failure Attribution

## 1. Artifact Attribution

Analysis of the failed `dial_tone_workflow` runs from Phase 12 dogfood (`/tmp/grc_agent_phase12_dogfood/dial_tone_workflow_run_0*.json`) reveals:
- **Failure Point:** Turn 3 (0-indexed Turn 3, technically the 4th user turn).
- **Exact User Turn:** "Use this exact selected option: insert with operation_kind insert_block, dry_run false, connection_id analog_sig_source_x_0:0->blocks_add_xx:0, block_id blocks_throttle2, instance_name blocks_throttle2_phase12, type=float, samples_per_second=48000."
- **GRC Agent Response:** No text content output. The agent planner explicitly classified the intent as `uncertain_mutation` with `requires_clarification: True`, avoiding any tool calls.
- **Tool Calls Before Failure:** `inspect_graph`, `change_graph` (set `samp_rate`), `change_graph` (add `phase12_gain`).
- **Graph State/Context:** The graph state was fully populated with blocks and connections (9 blocks, 4 connections), and the variables `samp_rate` and `phase12_gain` were successfully modified in prior turns.
- **Judge Failure Reason:** `dummy_user_underspecified`, with `final_state_pass: false` and `graph_delta_pass: false` (since the insert tool was never called).

## 2. Comparison of Insert Success

Comparing the failing prompt with passing insert prompts from other phases:

| Scenario / Phase | Prompt Wording | Context / Visibility | Result |
| :--- | :--- | :--- | :--- |
| **Phase 12 dial_tone** (Failed) | *"Use this exact selected option: insert with..."* | Multi-turn prior edits; no prior `get_insert_options` call in history. | Fail |
| **Phase 10 Exact** | *"Call change_graph now with operation_kind insert_block... and insert_params {{...}}."* | Isolated exact parameters with explicit schema structure. | Pass |
| **Phase 11 Guided** | *"Use this exact selected option: insert with..."* | Follows a previous turn where `get_insert_options` output the exact options text. | Pass |

**Key Finding:** The phrase *"Use this exact selected option"* only works if the agent just presented a list of insert options. In `dial_tone_workflow`, the scenario author skipped the first half of the guided flow, leaving the prompt severely underspecified and structurally confusing to the model without prior context.

## 3. Controlled Variant Results

We executed 4 controlled variants (`n=3` each) using `gameplay_runner.py` to isolate the root cause. 

| Variant | Conditions | Pass Rate (n=3) |
| :--- | :--- | :--- |
| **A. `dial_tone_insert_exact_after_prior_edits`** | Prior edits + explicit exact prompt (`Call change_graph now with...`) | 3/3 (100%) |
| **B. `dial_tone_insert_guided_after_prior_edits`** | Prior edits + 2-turn guided insert flow | 3/3 (100%) |
| **C. `dial_tone_insert_isolated_same_prompt`** | Isolated + failed prompt (*"Use this exact selected option..."*) | 0/3 (0%) |
| **D. `dial_tone_insert_exact_isolated`** | Isolated + explicit exact prompt | 3/3 (100%) |

## 4. Root Cause Verdict

**Primary:** `prompt_under_specified`

**Conclusion:** Variant A and D both pass, meaning the runtime, schema, and prior multi-turn context compaction are perfectly healthy. Variant C fails just like the original, confirming the issue is purely the prompt wording. Guided Variant B passes, confirming the proper multi-turn interaction model. 

The original script authored for `dial_tone_workflow` made a scenario composition error by supplying the second half of a guided interaction without initiating the first half. The model correctly flagged this out-of-context command as `uncertain_mutation`.

## 5. Recommended Fix

Since Variant A (exact prompt) and Variant B (guided workflow) both succeed, we must fix the scenario definitions. 

As per the guidelines, **production workflows should use guided insert** (Variant B) rather than one-shot insert, as it reflects realistic user interaction patterns. The `dial_tone_workflow` scenario should be updated to use the full two-turn guided selection process, or the harness tests should adopt `dial_tone_insert_guided_after_prior_edits` as the new baseline.

*Disclaimer: No production-ready claims are made.*
