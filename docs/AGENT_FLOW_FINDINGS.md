# Agent Flow Findings Blueprint

> **Purpose:** Living document tracking root-cause analysis of agent-flow
> test failures, proposed fixes, implementation status, and experimental
> evidence. Updated each time we discover something new or implement a fix.

---

## 0. Setup

- **Model:** `gemma4:e4b-it-qat` (actual size: 7.5B params, Q4_0 quant; native
  context length: 131,072 tokens)
- **Provider:** Ollama OpenAI-compatible endpoint (`/v1/chat/completions`)
- **Test surface:** 8 autonomous scenarios in
  `playground/agent_flow_experiment/run_agent_flow.py`
- **Live test gate:** `tests/test_agent_flow_live.py` (gated on
  `GRC_AGENT_LIVE_MODEL=1`)
- **MD transcripts:** `playground/agent_flow_experiment/results/*.md` contain
  full evidence: system prompt, user prompt, .grc before/after, every
  tool call with args and results

---

## 1. Round-1 Audit (pre-cleanup)

| Metric | Value |
|--------|-------|
| Hard schema rejections (4B model × 3 tools) | **0/30** |
| `inspect_graph` correct calls | 13/13 |
| `query_knowledge` correct calls | 6/6 |
| `change_graph` syntactic success | 11/11 |
| Tasks with semantic success | 5/8 |
| `query_knowledge` never called for discovery | 5/8 missed opportunities |

**Verdict:** Syntactic layer solved (0 rejections). Semantic layer: 5/8.
The schema was too deep (depth-3 nesting in `add_connections`) for a 4B
model to reverse-engineer correctly.

---

## 2. Schema Flattening (consultant-approved)

5 changes implemented:

| # | Change | Impact |
|---|--------|--------|
| 1 | `add_connections` items: depth-3 nested objects → flat `"src:port->dst:port"` strings | Eliminates depth-3; read-write symmetric |
| 2 | `remove_blocks` items: `{instance_name: "..."}` objects → bare `"block_name"` strings | Consistent with `remove_connections` |
| 3 | `force` description: jargon → plain language with error example | Model can discover escape hatch |
| 4 | `block_id`/`instance_name` descriptions: added concrete examples | Grounds field meanings |
| 5 | Root description: "At least one array must be provided" | Prevents empty-call waste |

**Result:** Live test 8/8 schema-clean. Max nesting depth: 3 → 2.

---

## 3. Payload Simplification (hide internal mechanics)

Agent shouldn't see plumbing concepts. Removed from `change_graph` payload:

| Removed | Reason |
|---------|--------|
| `committed` | Internal state machine — agent doesn't need it |
| `ops_applied` | Internal counter |
| `rollback_failed` | Internal rollback state |

Agent now sees: `{"ok": true}` on success, or `{"ok": false, "error_type": "...",
"errors": [...]}` on failure. `force` stays in the schema (input) but its
semantics aren't exposed in the result.

**Result:** All tests pass with `ok`/`error_type`/`errors` only.

---

## 4. MD Transcript Enhancement

MD files at `playground/agent_flow_experiment/results/*.md` now include:

1. System prompt (injected every turn)
2. User prompt
3. **Flowgraph state BEFORE scenario** (full .grc YAML)
4. Every model turn with full assistant response
5. Every tool call with arguments and the tool result the agent saw
6. **Flowgraph state AFTER scenario** (full .grc YAML)

This makes MDs self-contained for root-cause investigation.

---

## 5. Turn Limit Investigation

### 5.1 The two ceilings

There are TWO different ceilings in the system:

1. **Round cap** (`max_tool_rounds=8` in `config.py:74` /
   `MVP_TOOL_SURFACE.default_max_tool_rounds`): counts MODEL TURNS where
   the model issues tool calls.
2. **Context window** (Ollama `num_ctx`, default ~4096 for the
   OpenAI-compatible endpoint): limits total prompt + completion tokens.

### 5.2 Which ceiling killed the failing scenarios?

| Scenario | Turn count | Final turn total_tokens | Hit |
|----------|-----------|------------------------|-----|
| 01 add_throttle | 5 | 4096 | Context window |
| 05 full_rewire | 6 | 4096 | Context window |
| 06 query_knowledge_multiply | 5 | 4096 | Context window |

**The round cap (8) was NEVER triggered in any scenario.** Max turns used
was 6 (scenario 05). The actual killer was the 4096-token context window
from Ollama's default `num_ctx`.

### 5.3 Why is num_ctx limited?

Our `doc_answer.py` explicitly sets `num_ctx: 32768` for the docs RAG
endpoint (`src/grc_agent/runtime/doc_answer.py:283`). But for the regular
chat completion path (`toolagents_runtime.py`), `num_ctx` is NOT set.
Ollama falls back to its default for the OpenAI-compatible endpoint,
which appears to be ~4096 for this model.

The model's native context length is 131,072 tokens (per Ollama
`/api/show`). We're using 4096 — a 32× waste of the model's capability.

**Fix:** Pass `options.num_ctx` to Ollama in the chat completion settings.

### 5.4 Question: should we have a round cap at all?

If the model makes different decisions each round, a round cap penalizes
thinking. The better safety net: cap CONSECUTIVE IDENTICAL CALLS (same
tool, same args, N times in a row). This catches looping without
handicapping exploration.

**Status:** Open question. Once context window is fixed, round cap becomes
a non-issue for these scenarios.

---

## 6. Root Root Cause: Scenario 01 (add_throttle)

### 6.1 What the model saw

User prompt (verbatim):
> "Inspect the current flowgraph, then add a `blocks_throttle` block
> between `analog_sig_source_x_0` and `blocks_add_xx`. Name the new
> block `mid_throttle`, set `type` to `float`, and use `samp_rate` for
> `samples_per_second`. Re-wire the connections so the throttle sits
> inline. After the changes, inspect the result to confirm."

### 6.2 Where the confusion came from

Turn 2 generated `params: {"samp_rate": "samp_rate"}` — the model
parsed "use `samp_rate` for `samples_per_second`" as a literal copy:
key=`samp_rate`, value=`samp_rate`. The user meant
`params: {"samples_per_second": "samp_rate"}`.

After the error, Turn 3 called `query_knowledge` which returned:
```
"samples_per_second": "real=samp_rate",
"type": "enum="
```
Turn 4 fixed the field name (`samples_per_second: "samp_rate"` ✓) but
STILL omitted `type`. The catalog returned `"type": "enum="` with
**empty allowed values** — the model literally cannot discover that
`"float"` is a valid enum option.

### 6.3 Where the run died

Turn 5: model produced `completion_tokens: 1159` with empty `content`.
`total_tokens: 4096` exactly. The model was attempting a retry but
got output-truncated when prompt + output hit the 4096-token default
context window.

### 6.4 Root root cause

Two compounding factors:
1. **User prompt ambiguity:** "use samp_rate for samples_per_second"
   can be parsed as `{key: samp_rate, value: samp_rate}` or
   `{key: samples_per_second, value: samp_rate}`. The model picked
   the wrong reading.
2. **Catalog gap:** `"type": "enum="` returns empty allowed values,
   so even after Turn 3 the model has no way to discover `"float"`
   is valid.
3. **Output truncation:** When the model finally figured things out,
   its recovery attempt was cut off at 4096 tokens.

### 6.5 Fixes

- **User prompt:** rewrite to avoid "use X for Y" ambiguity. Use explicit
  parameter listings.
- **Catalog:** extract enum allowed values from block YAML.
- **Context window:** set `num_ctx: 8192` so the model can output recovery
  calls.
- **System prompt:** one line: "When adding a block whose id contains
  `_xx` / `_ff` / `_cc` / `_ii`, pass the `type` parameter explicitly or
  it will default to `complex`."

---

## 7. Root Root Cause: Scenario 05 (full_rewire)

### 7.1 What the model saw

User prompt:
> "Remove the `analog_noise_source_x_0` block. Then add a new
> `analog_const_source_x` block named `dc_offset` with `const` set to
> `0.0`. Connect `dc_offset` port 0 to `blocks_add_xx` port 2
> (replacing the noise path). Inspect the final result to confirm
> the changes."

### 7.2 Where the confusion came from

Turn 2 generated:
```json
{
  "add_blocks": [{
    "block_id": "analog_const_source_x",
    "instance_name": "dc_offset",
    "params": {"const": "0.0"}
  }],
  "add_connections": ["dc_offset:0->blocks_add_xx:2"],
  "remove_blocks": ["analog_noise_source_x_0"]
}
```

No `type` parameter. `analog_const_source_x` defaults to `type=complex`,
but `blocks_add_xx` is `type=float`. GRC rejects: "complex vs float".

**Turn 5: byte-identical payload to Turn 2.** Same error. The model
never modified the params despite seeing the error.

Turn 6: `completion_tokens: 1092` with empty content. `total_tokens:
4096`. Output truncated — model was trying to retry but hit the wall.

### 7.3 Root root cause

Same pattern as 01:
1. No `type` parameter set on the new block.
2. Catalog `"type": "enum="` returns empty values — model can't
   discover `"float"` is valid.
3. Output truncation at 4096 tokens killed the recovery attempt.

### 7.4 Fixes

Same as 01: user prompt clarity, catalog enum values, num_ctx,
system prompt direction.

---

## 8. Root Root Cause: Scenario 06 (query_knowledge_multiply)

### 8.1 What the model saw

User prompt:
> "Inspect the current flowgraph. I want to multiply the two sinusoid
> sources together instead of adding them. The exact GNU Radio
> block_id for a signal multiplier is not something to guess: use
> query_knowledge (domain catalog) to look it up first, then add
> the block named `multiplier` with `type` set to `float`. Connect
> the two existing `analog_sig_source_x` outputs into the multiplier,
> remove the old `blocks_add_xx`, and inspect the result to confirm."

### 8.2 Where the confusion came from

The model DID follow the `query_knowledge`-first instruction. Turn 3
found `blocks_multiply_xx` with:
```
"type": "enum=",
"vlen": "int=1",
"num_inputs": "int=2"
```

Turn 4 generated:
```json
{
  "add_blocks": [{
    "block_id": "blocks_multiply_xx",
    "instance_name": "multiplier"
  }],
  "add_connections": [...],
  "remove_blocks": ["blocks_add_xx"]
}
```

**NO parameters field at all.** Three independent signals said to set
`type=float`:
- User prompt: "with `type` set to `float`"
- Catalog: `type` is an enum parameter
- Existing `blocks_add_xx` in graph: has `type: float`

The model produced `add_blocks` with NO params, defaulted to
`type=complex`, failed IO validation. Also missing the replacement
edge `multiplier:0->audio_sink:0` after removing `blocks_add_xx`.

Turn 5: `completion_tokens: 1208` with empty content. `total_tokens:
4096`. Same truncation.

### 8.3 Root root cause

Same pattern + one extra: the model emitted `add_blocks` with NO
parameters field at all. The schema permits this (params is optional).
Combined with empty enum values and output truncation, the run died.

### 8.4 Fixes

Same as 01/05. Additionally, could consider making `params` required
when adding `*_xx` blocks (but that's per-scenario heuristic — avoid).

---

## 9. Common Pattern Across All 3 Failures

| Aspect | 01 | 05 | 06 |
|--------|----|----|----|
| Missing `type` on new block | ✓ | ✓ | ✓ |
| Catalog `type: enum=` with empty values | ✓ | ✓ | ✓ |
| Error message names the dimension | ✓ | ✓ | ✓ |
| Model acted on the error | ✗ | ✗ | ✗ |
| Final turn output truncated at 4096 | ✓ | ✓ | ✓ |
| Empty `content` on final turn | ✓ | ✓ | ✓ |

**The single common defect is: the model doesn't set `type: "float"` on
new `*_xx` blocks despite multiple independent signals.**

**The single environmental defect is: Ollama's default `num_ctx=4096`
truncates the model's recovery attempts.**

---

## 10. Proposed Fixes (priority order)

| Fix | Effort | Impact | Status |
|-----|--------|--------|--------|
| F1: Set `num_ctx: 8192` in chat completion | Low | High | **TODO** |
| F2: Add `*_xx` system prompt direction | Low | Medium | **TODO** |
| F3: Catalog enum allowed values | Medium | High | Backlog |
| F4: User prompt clarity | Low | Medium | Open (prompt authors) |
| F5: Replace round cap with repeat-call cap | Low | Low | Open (debatable) |

---

## 11. Metrics

### 11.1 Latest run (with payload simplification + 8-round cap)

| Scenario | Turns | CG calls | OK | Fail | QK | Inline | Success |
|----------|------:|---------:|---:|-----:|---:|-------:|:-------:|
| 01 add_throttle | 5 | 2 | 0 | 2 | 1 | 2 | ✗ |
| 02 update_sample_rate | 4 | 1 | 1 | 0 | 0 | 0 | ✓ |
| 03 disable_and_enable | 5 | 2 | 1 | 1 | 0 | 0 | ✓ |
| 04 add_and_remove_variable | 6 | 2 | 2 | 0 | 0 | 0 | ✓ |
| 05 full_rewire | 6 | 2 | 0 | 2 | 2 | 0 | ✗ |
| 06 query_knowledge_multiply | 5 | 1 | 0 | 1 | 2 | 0 | ✗ |
| 07 force_disabled_connected_block | 4 | 1 | 1 | 0 | 0 | 0 | ✓ |
| 08 fm_rx_insert_throttle | 4 | 1 | 1 | 0 | 0 | 1 | ✓ |

**Aggregate:** 5/8 semantic success, 0/8 ceiling hits (was 2/8 with 5-round cap),
6 total OK, 3/8 used `query_knowledge`, 1/8 used `force`.

### 11.2 Projected after fixes

| Fix | Projected success |
|-----|-------------------|
| F1 (num_ctx 8192) | 5/8 → 6/8 or 7/8 (recovery attempts no longer truncated) |
| F2 (system prompt) | marginal improvement on scenarios 01/05/06 |
| F3 (catalog enum) | could push 6/8 → 7/8 or 8/8 |

---

## 12. Open Questions

1. Should the round cap be replaced with a repeat-call cap?
2. Should the system prompt add the `*_xx` direction, or should it go in
   user prompts?
3. Are there other hidden ceilings besides `num_ctx` and the round cap?
4. Does the 4B model (gemma4:e4b-it-qat, 7.5B actual params) have
   reasoning limits that no amount of context/prompt fixing can solve?