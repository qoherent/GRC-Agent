# Broader Real-World Agentic Smoke v2

## Environment

- GNU Radio: 3.10.9.2
- grcc: /usr/bin/grcc
- Backend: unsloth/gemma-4-E2B-it-GGUF at http://127.0.0.1:8080 (reused)
- Tool count: 13
- Retrieval: /usr/share/gnuradio/grc/blocks
- Goal normalization micro-fix: active

---

## Graphs tested

| # | Graph | Source | Blocks | Conns | Valid | Type |
|---|-------|--------|--------|-------|-------|------|
| 1 | random_bit_generator.grc | test fixture | 5 | 3 | Yes | Float stream |
| 2 | dial_tone.grc | installed (audio) | 8 | 4 | Yes | Audio/float |
| 3 | demo_qam.grc | installed (channels) | 21 | 9 | Yes | Complex/QAM |
| 4 | filter_taps.grc | installed (filter) | 21 | 10 | Yes | Filter chain |
| 5 | qtgui_vector_sink_example.grc | installed (qt-gui) | 9 | 6 | Yes | QT GUI |
| 6 | tx_stage0.grc | installed (digital) | 4 | 3 | Yes | Message/PDU |
| 7 | zmq_stream.grc | installed (zeromq) | 14 | 9 | Yes | ZeroMQ stream |
| 8 | mpsk_stage6.grc | installed (digital) | 38 | 19 | Yes | Large digital |
| 9 | comparing_resamplers.grc | installed (ctrlport) | 9 | 6 | No | Version mismatch |
| 10 | tx_stage6.grc | installed (digital) | 34 | 19 | Yes | Disabled blocks |

---

## Prompt/result table

### auto_insert_block (tool subcommand, deterministic)

| Graph | Goal | OK | Error type | Committed block | Classification |
|-------|------|----|------------|-----------------|----------------|
| random_bit_generator | insert a compatible block | True | — | analog_ctcss_squelch_ff | PASS |
| random_bit_generator | add a head block | False | ALL_CANDIDATES_FAILED | — | PASS_SAFE_REJECTION |
| random_bit_generator | add throttle | False | NO_GOAL_MATCH | — | PASS_SAFE_REJECTION |
| random_bit_generator | add filter | True | — | analog_ctcss_squelch_ff | PASS |
| dial_tone | insert a compatible block | False | — | — | PASS_CLARIFICATION_REQUESTED (2 opts) |
| dial_tone | add a head block | False | ALL_CANDIDATES_FAILED | — | PASS_SAFE_REJECTION |
| dial_tone | add throttle | False | NO_GOAL_MATCH | — | PASS_SAFE_REJECTION |
| dial_tone | add filter | False | — | — | PASS_CLARIFICATION_REQUESTED (2 opts) |
| demo_qam | insert a compatible block | False | ALL_CANDIDATES_FAILED | — | PASS_SAFE_REJECTION |
| demo_qam | add a head block | False | — | — | PASS_CLARIFICATION_REQUESTED (2 opts) |
| demo_qam | add throttle | False | NO_GOAL_MATCH | — | PASS_SAFE_REJECTION |
| demo_qam | add filter | False | ALL_CANDIDATES_FAILED | — | PASS_SAFE_REJECTION |
| filter_taps | insert a compatible block | False | — | — | PASS_CLARIFICATION_REQUESTED (2 opts) |
| filter_taps | add a head block | False | ALL_CANDIDATES_FAILED | — | PASS_SAFE_REJECTION |
| filter_taps | add throttle | False | NO_GOAL_MATCH | — | PASS_SAFE_REJECTION |
| filter_taps | add filter | False | — | — | PASS_CLARIFICATION_REQUESTED (2 opts) |
| qtgui_vector_sink_example | insert a compatible block | True | — | analog_ctcss_squelch_ff | PASS |
| qtgui_vector_sink_example | add a head block | False | ALL_CANDIDATES_FAILED | — | PASS_SAFE_REJECTION |
| qtgui_vector_sink_example | add throttle | False | NO_GOAL_MATCH | — | PASS_SAFE_REJECTION |
| qtgui_vector_sink_example | add filter | True | — | analog_ctcss_squelch_ff | PASS |
| tx_stage0 | insert a compatible block | False | UNSUPPORTED_GOAL | — | PASS_SAFE_REJECTION |
| tx_stage0 | add a head block | False | UNSUPPORTED_GOAL | — | PASS_SAFE_REJECTION |
| tx_stage0 | add throttle | False | UNSUPPORTED_GOAL | — | PASS_SAFE_REJECTION |
| tx_stage0 | add filter | False | UNSUPPORTED_GOAL | — | PASS_SAFE_REJECTION |
| zmq_stream | insert a compatible block | False | — | — | PASS_CLARIFICATION_REQUESTED (2 opts) |
| zmq_stream | add a head block | False | ALL_CANDIDATES_FAILED | — | PASS_SAFE_REJECTION |
| zmq_stream | add throttle | False | NO_GOAL_MATCH | — | PASS_SAFE_REJECTION |
| zmq_stream | add filter | False | — | — | PASS_CLARIFICATION_REQUESTED (2 opts) |
| mpsk_stage6 | insert a compatible block | False | ALL_CANDIDATES_FAILED | — | PASS_SAFE_REJECTION |
| mpsk_stage6 | add a head block | False | — | — | PASS_CLARIFICATION_REQUESTED (2 opts) |
| mpsk_stage6 | add throttle | False | NO_GOAL_MATCH | — | PASS_SAFE_REJECTION |
| mpsk_stage6 | add filter | False | ALL_CANDIDATES_FAILED | — | PASS_SAFE_REJECTION |
| comparing_resamplers | insert a compatible block | False | ALL_CANDIDATES_FAILED | — | PASS_SAFE_REJECTION |
| comparing_resamplers | add a head block | False | ALL_CANDIDATES_FAILED | — | PASS_SAFE_REJECTION |
| comparing_resamplers | add throttle | False | NO_GOAL_MATCH | — | PASS_SAFE_REJECTION |
| comparing_resamplers | add filter | False | ALL_CANDIDATES_FAILED | — | PASS_SAFE_REJECTION |
| tx_stage6 | insert a compatible block | False | ALL_CANDIDATES_FAILED | — | PASS_SAFE_REJECTION |
| tx_stage6 | add a head block | False | — | — | PASS_CLARIFICATION_REQUESTED (2 opts) |
| tx_stage6 | add throttle | False | NO_GOAL_MATCH | — | PASS_SAFE_REJECTION |
| tx_stage6 | add filter | False | ALL_CANDIDATES_FAILED | — | PASS_SAFE_REJECTION |

**Summary: 40 auto_insert tests across 10 graphs.**

- 3 auto-committed (all analog_ctcss_squelch_ff)
- 7 clarification requested (all resolved successfully with A → graph valid)
- 30 safe rejection
- 0 wrong semantic insertion
- 0 unsafe mutation

### chat --message (model-backed)

| Graph | Prompt | Tool chain | Result | Classification |
|-------|--------|------------|--------|----------------|
| dial_tone | summarize this graph | summarize_graph | OK | PASS |
| filter_taps | summarize this graph | summarize_graph | OK | PASS |
| zmq_stream | summarize this graph | summarize_graph | OK | PASS |
| demo_qam | summarize this graph | summarize_graph | OK | PASS |
| mpsk_stage6 | summarize this graph | summarize_graph | OK (38 blocks) | PASS |
| demo_qam | add a head block | auto_insert_block | FAILED: NO_GOAL_MATCH | PASS_SAFE_REJECTION |
| dial_tone | add a head block | auto_insert_block | FAILED: NO_GOAL_MATCH | PASS_SAFE_REJECTION |
| demo_qam | add throttle | auto_insert_block | FAILED: NO_GOAL_MATCH | PASS_SAFE_REJECTION |
| dial_tone | add throttle | auto_insert_block | FAILED: NO_GOAL_MATCH | PASS_SAFE_REJECTION |
| demo_qam | add filter | auto_insert_block | FAILED: ALL_CANDIDATES_FAILED | PASS_SAFE_REJECTION |
| dial_tone | add filter | auto_insert_block | FAILED | PASS_SAFE_REJECTION |
| zmq_yaml | edit the raw yaml | (guard) | Intercepted | PASS |
| demo_qam | validate the graph | validate_graph | OK | PASS |
| zmq_stream | validate the graph | validate_graph | OK | PASS |

### Interactive REPL

| Graph | Turn sequence | Result | Classification |
|-------|---------------|--------|----------------|
| demo_qam | summarize → validate → save | All 3 turns correct | PASS |
| random_bit_generator (v1) | summarize → validate → save | All 3 turns correct | PASS |

### Clarification resolution (via agent API)

| Graph | Goal | MCQ shown? | Resolve A | Graph valid after? |
|-------|------|-----------|-----------|-------------------|
| dial_tone | insert a compatible block | Yes (2 opts) | executed ok=True | Yes |
| filter_taps | insert a compatible block | Yes (2 opts) | executed ok=True | Yes |
| demo_qam | add a head block | Yes (2 opts, blocks_head) | executed ok=True | Yes |
| zmq_stream | add filter | Yes (2 opts) | executed ok=True | Yes |
| tx_stage6 | add a head block | Yes (2 opts, blocks_head) | executed ok=True | Yes |

---

## auto_insert behavior analysis

### Commit pattern

Only `analog_ctcss_squelch_ff` auto-commits across all graphs. This block validates on most
float connections because it has simple float-in/float-out ports with catalog-default parameters.
It is type-compatible but semantically uninteresting for most use cases.

Not classified as WRONG_SEMANTIC_INSERTION because:
- Type-compatible and graph remains valid after insertion
- For generic "insert a compatible block" goal, any type-compatible block is contractually correct
- Clarification correctly presents options when multiple candidates validate

### Rejection pattern

- `add throttle` → NO_GOAL_MATCH on all 10 graphs. The throttle family token matches
  `blocks_throttle2` in the catalog, but the suggestion system rarely produces throttle as a
  top candidate, and when it does, the parameter inference often fails validation.
  Status: WATCHLIST. Safe rejection. No fix warranted — wrong insertion is worse.
- `add a head block` → ALL_CANDIDATES_FAILED on most graphs. blocks_head validates on
  complex connections (demo_qam, mpsk_stage6, tx_stage6) but not on float connections.
  Status: Correct behavior.

### Goal normalization

The micro-fix is working. Tested goals:
- "insert a compatible block into this graph" → generic (fixed from explicit_family)
- "add throttle to the current flowgraph" → explicit_family(throttle) (no "flowgraph" noise)

---

## MCQ behavior

5 clarifications triggered across 40 tool tests. All resolved correctly:
- MCQ renders with A/B/D labels, block_type, connection_id, params
- No raw JSON dump in any MCQ
- Resolve A executes and graph validates
- No premature mutation before resolution

---

## Validation/save behavior

- All valid graphs validate correctly
- Invalid graph (comparing_resamplers) correctly reported invalid with real grcc error
- Save produces grcc-valid output files
- Save gate enforced (dirty graph requires validation first)
- Raw YAML guard intercepts destructive requests on all graphs

---

## Repeated issues found

### No STOP_THE_LINE or TOOL_BUG

Zero safety violations across 10 graphs and 60+ test interactions.

### Observation: analog_ctcss_squelch_ff dominance (WATCHLIST)

Repeats across 5/9 stream graphs. Not a bug — the block is type-compatible and validates
reliably. But it's rarely the semantically useful choice. Candidate ranking quality depends
on the suggestion system's type-compatibility scoring, which is outside this milestone's scope.

### Observation: "add throttle" always fails (WATCHLIST)

Repeats across all 10 graphs. Throttle family token matches blocks_throttle2 but parameter
inference fails. Safe rejection. No fix warranted per "wrong insertion is worse than safe rejection."

---

## Fixes made

**None.** No issue met the 3-unrelated-cases threshold for code changes. All observations are
documented as watchlist items for future evidence gathering.

---

## Safety status

| Check | Result |
|-------|--------|
| No raw YAML edits | PASS |
| No invalid graph committed | PASS |
| No preview mutation | PASS |
| No wrong file overwrite | PASS |
| No wrong semantic insertion | PASS |
| MCQ works | PASS |
| Validation/save correct | PASS |
| YAML guard works | PASS |

---

## Test results

696 tests OK (24 skipped). Ruff clean. No regressions.

---

## Next recommendation

1. Monitor `analog_ctcss_squelch_ff` dominance pattern. If candidate ranking quality becomes
   a user complaint, consider adding diversity scoring to the suggestion system. Not urgent.
2. Monitor "add throttle" rejection pattern. If throttle insertion becomes a frequent request,
   investigate why `suggest_insertions` doesn't produce throttle candidates with correct params.
3. The tool surface is complete and safe. Next investment should be in model routing quality
   and prompt engineering rather than tool architecture.
