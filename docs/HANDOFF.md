# Handoff: Agentic Reliability Hardening + Complex Graph Tests

Date: 2026-05-28

## Current State

GRC Agent is running locally with llama.cpp through ToolAgents and the four
model-facing wrappers: `inspect_graph`, `search_blocks`, `ask_grc_docs`, and
`change_graph`.

On this machine, model-backed chat is configured for native NVIDIA CUDA:

- `llama-server` resolves to the CUDA-capable binary under
  `/home/mahmoud/.unsloth/llama.cpp/build/bin/llama-server`
- `llama-server --list-devices` must show `CUDA0`
- `[llama].device = "CUDA0"`
- `[llama].gpu_layers = 999`
- `[llama].model_path` points at the local Gemma 4 E4B text GGUF
- launcher uses `-m <model_path>` when configured, avoiding Gemma 4 `mmproj`
  downloads from Hugging Face GGUF repos

Health/doctor proved llama reachability, model alias match, actual context from
`/props`, CUDA device use, and built-in tool suppression.

## Implemented Behavior

### Native Validation Refusal

Plain invalid edits now stop with deterministic refusal text. Example:

```text
Disable the QT GUI time sink and validate the graph.
```

On `tests/data/random_bit_generator.grc`, disabling the only sink creates a
native GRC validation error. The final behavior is:

- `change_graph` attempts the literal candidate
- native GRC validation rejects it
- no mutation is committed
- rollback is complete
- copied graph remains byte-identical
- assistant says no commit, graph unchanged, and quotes:
  `Source - out(0): Port is not connected.`

### Explicit Invalid Intermediate

If the user intent allows an invalid intermediate graph, the model may choose
`force=true`.

Example:

```text
Disable the QT GUI time sink. It is okay if this leaves the graph invalid as an
intermediate working state; commit it and warn me.
```

Expected and observed behavior:

- `change_graph` uses `update_states` plus `force=true`
- schema, graph references, catalog facts, preflight, candidate apply, and
  autosave must still pass
- final native/`grcc` validation failure is committed as an invalid intermediate
- result says committed but invalid
- result includes exact effect and validation warning

This is not a general bypass. `force=true` must not bypass unknown blocks,
unknown params, bad ports, stale files, preflight failures, ambiguity, or save
failures.

### Disable vs Remove

The tool schema now nudges the model away from interpreting "disable" as
`remove_blocks`:

- `update_states` is the state edit bucket for enable/disable
- `remove_blocks` says remove/delete only; for disable/turn off use
  `update_states`

Live E4B proof:

- "Disable the second QT GUI time sink..." on
  `random_bit_generator_dual_sink.grc` committed
  `qtgui_time_sink_x_1.state=disabled` and validated.
- "Remove the second QT GUI time sink..." removed `qtgui_time_sink_x_1`,
  detached only its incident edge, and validated.

### Clarification Must Not Be Forced Into Mutation

Runtime reminders were repaired so they do not override a valid clarification.

Previously observed failure:

- model inspected an ambiguous target and asked which sink to edit
- runtime reminder forced a `change_graph`
- model guessed `qtgui_time_sink_x_0`
- graph mutated despite ambiguity

Current behavior:

- if graph/tool evidence shows ambiguity and the assistant asks clarification,
  the turn may end with no mutation
- if the assistant asks a graph-evidence-backed clarification after inspection,
  the runtime does not force `change_graph`
- if the assistant asks for details before any tool call on a graph-local edit,
  the runtime nudges inspection/search rather than forcing placeholder
  `change_graph` arguments

Live E4B proof:

- Dual-sink ambiguous disable asks which sink and leaves the graph unchanged.
- Ambiguous stream rewire asks which chain/input/connection and leaves all six
  connections unchanged.
- Ambiguous message rewire asks which strobe, connection, and debug block, and
  leaves the original message connections unchanged.

### Variable Removal Repair

`remove_variables` is a list of variable instance-name strings. E4B initially
tried:

```json
{"remove_variables": [{"instance_name": "unused_var"}]}
```

The schema repair hint now says:

```text
remove_variables=['unused_var']
```

Live proof:

- "Remove the unused variable and validate the graph." on
  `random_bit_generator_with_unused_var.grc` now removes only `unused_var`,
  keeps `samp_rate`, keeps the three original connections, and validates.

### Earlier Null-Sink Dtype Repair Feedback

The older standalone `CHANGE_GRAPH_REPAIR_FEEDBACK.md` note was folded into
this handoff and removed. Its durable lesson:

- an E4B null-sink add/connect attempt failed safely because the new
  `blocks_null_sink` defaulted to `complex` while the connected source output
  was `float`;
- rollback/no-commit worked and the saved graph was unchanged;
- the useful fix was generic repair feedback, not a block-specific macro;
- compact `search_blocks` context should preserve candidate params, enum
  options, and port dtype facts;
- compact `change_graph` context should include native GNU validation errors
  and flat-schema hints such as `retry with add_blocks[].params.type="float"`
  when catalog metadata supports that repair.

## Live Test Matrix Already Run

All live tests were run against copied files under `/tmp`.

| Fixture | Prompt | Observed result | File/graph verification |
| --- | --- | --- | --- |
| `random_bit_generator.grc` | Change sample rate to 48000 | committed valid update | `samp_rate=48000`, graph valid |
| `random_bit_generator.grc` | Disable QT GUI time sink and validate | no commit | file byte-identical, state enabled, native error quoted |
| `random_bit_generator.grc` | Disable sink as invalid intermediate | committed invalid with warning | state disabled, autosaved invalid copy |
| `random_bit_generator_dual_sink.grc` | Disable second QT GUI time sink | committed valid state edit | `_1.state=disabled`, block and edge remain |
| `random_bit_generator_dual_sink.grc` | Disable QT GUI time sink | clarification | file byte-identical, both sinks enabled |
| `random_bit_generator_dual_sink.grc` | Remove second QT GUI time sink | committed valid remove | `_1` absent, only its edge detached |
| `random_bit_generator_dual_sink_sink1_disabled.grc` | Disable `_1` | safe no-op | file byte-identical, but assistant still says committed |
| `random_bit_generator_dual_sink_sink1_disabled.grc` | Enable `_1` | committed valid state edit | `_1.state=enabled`, graph valid |
| `random_bit_generator_with_unused_var.grc` | Remove unused variable | committed valid remove | `unused_var` absent, `samp_rate` remains |
| `rewire_stream_ambiguous.grc` | Reconnect time sink to other random source | clarification | file byte-identical, six connections unchanged |
| `rewire_message_ambiguous.grc` | Reconnect message strobe to another debug block | clarification | file byte-identical, two message connections unchanged |

## Deterministic Tests Added Or Updated

Focused tests now cover:

- native validation refusal result shape and compact rendering
- terminal failure text with no-commit/unchanged/native-error facts
- forced invalid commits not being treated as terminal failures
- forced invalid commit assistant wording
- ambiguity clarification not being forced into mutation
- evidence-backed clarification not being forced into mutation
- pre-evidence clarification receiving an inspect/search reminder, not forced
  `change_graph`
- `remove_variables` schema repair hint
- text-only local Gemma 4 `model_path` launcher path
- CUDA launcher device/gpu-layer configuration

Recent verification commands:

```bash
uv run python -m unittest \
  tests.test_toolagents_runtime.ToolAgentsRepairClassificationTests \
  tests.test_runtime_tool_validation.RuntimeToolValidationTests.test_change_graph_remove_variable_shape_hint_is_specific \
  tests.test_change_graph_flat_batch.ChangeGraphFlatBatchTests.test_remove_connected_block_auto_detaches_and_force_saves_invalid_working_copy \
  tests.test_change_graph_flat_batch.ChangeGraphFlatBatchTests.test_update_states_accepts_disabled_boolean_alias \
  tests.test_change_graph_flat_batch.ChangeGraphFlatBatchTests.test_native_validation_failure_reports_unchanged_graph_facts \
  tests.test_mvp_tool_profile.MvpToolProfileTests.test_prompt_and_schemas_stay_compact_and_wrapper_only \
  tests.test_model_context_bible

uv run ruff check \
  src/grc_agent/toolagents_runtime.py \
  src/grc_agent/runtime_tool_validation.py \
  tests/test_toolagents_runtime.py \
  tests/test_runtime_tool_validation.py

git diff --check
```

The latest focused run passed `16 tests`.

## Known Soft Issues

- Some clarification responses are too verbose and may include hypothetical
  "If you mean X..." wording. Behavior is safe, but answer style can be tighter.
- The unused-variable repair now succeeds, but E4B may still spend invalid tool
  retries before using the corrected `remove_variables=['unused_var']` shape.
- Live E4B behavior is promising but not production evidence. Keep treating
  live runs as routing/behavior evidence separate from deterministic safety
  tests.

## Recommended Next Steps

1. Run the remaining live matrix on E4B:
   - duplicate instance-name fixture should fail safely/no mutation
   - exact valid rewire with explicit old connection and new endpoint
   - exact message rewire to `debug_1` after user names it
   - invalid `force=true` attempts with unknown params/ports must still refuse
2. Tighten clarification answer style without adding fixture-specific prompts.
   Prefer compact options and exact graph candidates.
3. Consider a lightweight live-eval scenario file for the proven prompts above
   so future model/backend swaps can replay them.

---

## Agentic Reliability Hardening (2026-05-28 Session)

### Root Cause Analysis

Three confirmed agent failure modes were identified during complex multi-step
graph mutation sessions (Scenarios 1–11):

1. **Tool Confusion**: The model used `inspect_graph` to search for catalog
   block types instead of `search_blocks`, receiving `target_not_found` errors
   and looping unproductively.
2. **State Blindness**: In multi-turn sessions the model assumed graph state
   from conversation history and issued duplicate `add_block` calls for blocks
   already committed in previous turns.
3. **Brittle Rewiring**: Rewire operations required exact `connection_id`
   strings that the model had to construct from memory, leading to
   format/port-index guessing errors.

### Fixes Applied

All four fixes are deterministic — no prompting hacks, no heuristics.

#### Fix 1: Tool Schema Disambiguation

**File**: `src/grc_agent/runtime/tool_schemas.py`

- **`inspect_graph`** description changed from the terse
  `"Read graph. No args=overview..."` to an explicit positive+negative boundary:
  *"Inspect the live, currently active graph... Do NOT use this to discover
  new block types or parameter names."*
- **`search_blocks`** description now reads:
  *"Search the installed GNU Radio catalog... Do NOT use this to check what is
  in the current graph."*
- **`change_graph`** description now opens with:
  *"Always call inspect_graph before change_graph to verify current instance
  names and connections. Never assume graph state from history..."*

#### Fix 2: `inspect_graph` Guided Error Messages

**File**: `src/grc_agent/runtime/wrappers/inspect_graph.py`

When a `target_not_found` error fires, the error message now includes an
explicit recovery hint:

```text
Target 'analog_agc_cc' did not match any active block instance or connection
in the graph. If you want to search the catalog of available GNU Radio block
types (e.g. to discover parameter IDs or block names like 'analog_agc_cc'),
you MUST use the 'search_blocks' tool instead.
```

#### Fix 3: Duplicate Block Guided Error Messages

**Files**: `src/grc_agent/validation/checks.py`, `src/grc_agent/flowgraph_session.py`

`duplicate_block_name` error messages now include recovery guidance:

```text
Block already exists: lo_source. If the block was already added in a previous
turn, do not add it again. Call inspect_graph to verify the current state.
```

This fires at the preflight validation layer (before any candidate is applied)
so it is always seen before any mutation is attempted.

#### Fix 4: `change_graph` Hint Injection

The existing `_repair_hint_for_validation_failure` in `dispatcher.py` already
handles dtype mismatches and invalid parameter expressions with actionable
hints. The schema-level description additions extend this to the
pre-call reasoning phase so the model front-loads correct tool selection.

### Test Verification

- `MODEL_CONTEXT_BIBLE.md` regenerated to match updated schemas.
- Full test suite: **235 tests, 0 failures** (`uv run python -m unittest discover tests`).
- No existing safety contracts weakened; all changes are additive error enrichment.

### Complex Scenario Test Matrix (Scenarios 1–11)

All 11 complex wireless-engineering graph mutation scenarios were run and
succeeded. Scenarios covered:

| # | Task | Key Challenge |
|---|------|---------------|
| 1 | LPF insertion on float stream | dtype mismatch recovery |
| 2 | Volume variable + multiply const | multi-step batch, parameter linking |
| 3 | Swap signal source connections | port-occupancy, atomic rewire ordering |
| 4 | Rational Resampler + sink rate update | concurrent block + param mutations |
| 5 | Non-specific HPF insertion | vague prompt → exact param mapping |
| 6 | GUI Range control + LPF linkage | variable-linked parameter expressions |
| 7 | Heterodyne mixer / downconverter | multi-block add + complex rewiring |
| 8 | Bandpass filter + spectrum visualizer | dual-output routing, Qt GUI block |
| 9 | AM SC transmitter | parallel signal branches |
| 10 | AM coherent demodulator/receiver | shared carrier oscillator multi-fan-out |
| 11 | AWGN channel + AGC normalization | 5-block pipeline insert, disconnect + rewire |

### Known Soft Issues (Updated)

- Model may still over-rely on cached reasoning for connection IDs across
  very long sessions. The new guided error messages shorten recovery loops
  but do not eliminate them in worst-case contexts.
- Rewiring involving more than 2 connection swaps in one batch may still
  fail if the model constructs connection IDs without a prior `inspect_graph`.
  The `change_graph` description now makes this mandatory, but cannot enforce
  it programmatically at the schema layer.
- Validation hint injection covers dtype mismatches and param errors. Semantic
  intent errors (wrong block choice, wrong signal path) remain the model's
  responsibility.

