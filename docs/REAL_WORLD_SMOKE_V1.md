# Real-World Manual Smoke Pass v1

## Environment

- GNU Radio: 3.10.9.2
- grcc: /usr/bin/grcc
- Backend: unsloth/gemma-4-E2B-it-GGUF at http://127.0.0.1:8080 (reused)
- Tool count: 13
- Retrieval: /usr/share/gnuradio/grc/blocks

## Graphs tested

1. `random_bit_generator.grc` — custom test fixture, float stream, 5 blocks, 3 connections
2. `comparing_resamplers.grc` — installed GNU Radio example, float stream, 9 blocks, 6 connections
3. `tx_stage0.grc` — installed GNU Radio example, message/PDU, 4 blocks, message connections only

All graphs loaded from temp copies under `/tmp/grc_agent_smoke/`.

---

## Prompt/result table

### Graph 1: random_bit_generator.grc

| Prompt | Mode | Tool chain | Result | Classification |
|--------|------|------------|--------|----------------|
| summarize this graph | chat --message | summarize_graph | Correct summary of 5 blocks, 3 connections, 1 variable | PASS |
| insert a compatible block | chat --message | (none — model asked for clarification) | Model asked for more detail instead of calling auto_insert_block | MODEL_ROUTING |
| Use the auto_insert_block tool to insert a compatible block | chat --message | auto_insert_block | FAILED: no candidates matched goal with "into this graph" appended by model | MODEL_ROUTING |
| insert a compatible block | tool subcommand | auto_insert_block | Committed analog_ctcss_squelch_ff into char_to_float->time_sink | PASS |
| validate the graph | chat --message | validate_graph | Graph valid | PASS |
| save it to /tmp/.../random_bit_generator_out.grc | chat --message | save_graph | Saved, grcc-valid output file | PASS |
| directly edit the yaml... | chat --message | (guard) | YAML guard triggered, no YAML exposed | PASS |
| Multi-turn REPL: summarize → validate → save | chat (interactive) | summarize_graph → validate_graph → save_graph | All 3 turns correct, history accumulates, save produces valid file | PASS |
| apply_edit samp_rate=48000 | tool subcommand | apply_edit | ok=True, valid, affected=['samp_rate'] | PASS |

### Graph 2: comparing_resamplers.grc

| Prompt | Mode | Tool chain | Result | Classification |
|--------|------|------------|--------|----------------|
| summarize this graph | chat --message | summarize_graph | Correct summary of 9 blocks, 6 connections, 2 variables | PASS |
| insert a compatible block (tool subcommand) | tool subcommand | auto_insert_block | All 10 candidates failed validation — safe rejection | PASS_SAFE_REJECTION |
| validate the graph | chat --message | validate_graph | Graph invalid: gr.DISPTIME attribute error (real version mismatch) | PASS |
| save it to /tmp/.../resamplers_out.grc | chat --message | save_graph | Saved (graph was not dirty, no validation gate triggered) | PASS |

### Graph 3: tx_stage0.grc (message/PDU)

| Prompt | Mode | Tool chain | Result | Classification |
|--------|------|------------|--------|----------------|
| summarize this graph | chat --message | summarize_graph | Correct summary of 4 message/PDU blocks | PASS |
| insert a compatible block (tool subcommand) | tool subcommand | auto_insert_block | Safe rejection: no stream connections | PASS_SAFE_REJECTION |
| validate the graph | chat --message | validate_graph | Graph valid | PASS |

---

## MCQ behavior

No natural clarification was triggered during smoke testing. On this GNU Radio installation:

- `random_bit_generator.grc`: only 1 candidate validates → auto-commits
- `comparing_resamplers.grc`: 0 candidates validate → safe rejection
- `tx_stage0.grc`: no stream connections → safe rejection
- Float graph (src→throttle→sink): 0 candidates validate → safe rejection

MCQ is confirmed working via deterministic seeded integration tests
(`tests/integration/test_cli_clarification_repl.py`, 5 tests OK).

## Validation/save behavior

- validate_graph correctly reports valid/invalid with real grcc output
- save_graph correctly persists to specified path
- Save gate: refuses to save dirty graph without prior validation (tested in deterministic suite)
- Saved files pass grcc verification
- Raw YAML guard triggers correctly on destructive requests

## Fixes made

None. No STOP_THE_LINE, TOOL_BUG, or UX_CONFUSING issues found.

One MODEL_ROUTING observation documented below (no code fix — model behavior).

## MODEL_ROUTING observations

1. **Goal text inflation**: When the user says "insert a compatible block", the model
   (Gemma 4 E2B) sometimes rephrases the goal as "insert a compatible block into this graph".
   The word "graph" has catalog token frequency 1, so the goal classifier treats it as an
   explicit family token. No block matches "graph" in its block_type, causing
   AUTO_INSERT_NO_GOAL_MATCH.

   Workaround: user can say "insert a compatible block" without additional context, or
   the model can be instructed not to append "into this graph".

   Status: MODEL_ROUTING. No tool or architecture fix needed — the tool is correct.
   A future prompt/system improvement could reduce this if evidence accumulates.

2. **Model not calling auto_insert_block**: With the prompt "insert a compatible block"
   alone, the model asked for clarification instead of calling the tool. With the more
   explicit "Use the auto_insert_block tool to insert a compatible block", it called the
   tool but inflated the goal text.

   Status: MODEL_ROUTING. The tool is available and correctly defined in schemas.
   Model routing quality depends on model capability and prompt.

## Safety status

- No raw YAML edits exposed
- No invalid graph committed
- No preview mutation
- No wrong file overwrite
- MCQ works (via seeded tests)
- Validation/save behavior correct
- Raw YAML guard intercepts destructive requests

## Full suite result

692 tests OK (24 skipped). Ruff clean.

## Next recommendation

- Monitor MODEL_ROUTING patterns across more model backends and prompts
- If goal text inflation repeats with other models, consider adding "graph" to the
  stop word list in `_classify_goal` (one-line change, evidence-supported)
- No architecture or tool changes needed at this time
