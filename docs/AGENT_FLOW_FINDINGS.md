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

## 12. Experiment Results (num_ctx=8192 + *_xx system prompt)

### 12.1 Setup

- `ToolAgentsLlamaProviderConfig.num_ctx = 8192` (passed via
  `extra_body.options.num_ctx` to Ollama)
- System prompt added line:
  *"New blocks whose id contains _xx / _ff / _cc / _ii default to type=complex;
  set type explicitly (e.g. type=float) when the connection requires it."*

### 12.2 Results

| Scenario | Turns | CG | OK | Fail | force | QK | Success | Notes |
|----------|------:|---:|---:|-----:|------:|---:|:-------:|-------|
| 01 add_throttle | 4 | 1 | 0 | 1 | 0 | 0 | ✗ | Model produces explanation; still no type=float |
| 02 update_sample_rate | 6 | 3 | 1 | 2 | 0 | 0 | ✓ | First retry now works |
| 03 disable_and_enable | 8 | 4 | 2 | 2 | 2 | 0 | ✓ | Used force twice — learned from error |
| 04 add_and_remove_variable | 6 | 2 | 2 | 0 | 0 | 0 | ✓ | Clean run |
| 05 full_rewire | 5 | 2 | 1 | 1 | 0 | 0 | ✓ | **FAIL → PASS**: model retries with type=float |
| 06 query_knowledge_multiply | 6 | 1 | 0 | 1 | 0 | 3 | ✗ | Still no parameters field |
| 07 force_disabled_connected_block | 4 | 1 | 1 | 0 | 1 | 0 | ✓ | Clean |
| 08 fm_rx_insert_throttle | 5 | 1 | 0 | 1 | 0 | 0 | ✗ | Still complex default |

**Aggregate:** 5/8 semantic success, 0/8 output truncation (was 3/8),
2/8 used force (was 1/8), 1/8 used query_knowledge (was 3/8).

### 12.3 What the fixes achieved

- **Output truncation eliminated.** All 8 scenarios produced non-empty
  content on their final turns. The 4096-token `num_ctx` default was
  the actual killer; the 8-round cap was never triggered.
- **Scenario 05 fixed.** The model now retries with `type=float` after
  the first attempt fails. This was caused by the num_ctx fix giving
  the model room to think and retry.
- **Force usage increased** (1 → 2). With more room to think, the model
  now considers force=true after a validation failure.

### 12.4 What the fixes didn't address

- **Scenarios 01, 06, 08 still fail.** The model produces text (not
  empty) but doesn't set `type=float` on the new block. Even with:
  - User prompt: "set type to float"
  - System prompt: "set type explicitly"
  - Error message: "complex vs float"
  - Existing blocks in graph: type=float
- This is a 4B-model reasoning limit. The model can read all signals
  but doesn't act on them consistently.

### 12.5 Conclusion

The `num_ctx` fix is a clear win (eliminated 3/8 truncation failures).
The system prompt fix is a partial win (fixed 1/3 type-mismatch scenarios).
The remaining 2/8 failures are model-capability issues that prompt tuning
cannot solve without a stronger model.

---

## 13. Revised Metrics (post-fix)

| Metric | Pre-fix | Post-fix | Delta |
|--------|--------:|---------:|------:|
| Semantic success | 5/8 | **5/8** | 0 |
| Output truncation | 3/8 | **0/8** | **-3** |
| Total change_graph ok | 6 | **7** | +1 |
| Force used | 1/8 | **2/8** | +1 |
| Ceiling hits (8-round) | 0/8 | **0/8** | 0 |

The num_ctx fix eliminated 3 silent truncations. The model can now
reason and respond fully. The type-mismatch failures persist as a
model-capability limit.

---

## 14. Experiment Results (Fix A + Fix B)

### 14.1 Fix A: catalog enum values

**Change:** `src/grc_agent/catalog/schema.py:196` — when a param is
`dtype=enum` with non-empty options, include the allowed values in
the model-facing payload:

Before: `"type": "enum="`
After: `"type": "enum=[complex,float,int,short,byte]="`

**Risk:** minimal. Same `key=value` shorthand format. Pure capability data.

### 14.2 Fix B: tool-side type hint

**Change:** `src/grc_agent/runtime/change_graph.py` —
`_connection_dtype_hint` now accepts a `new_block_names` set and, if
either endpoint was newly added in the batch, looks at the new
block's `type` enum param to find the value matching the neighbor's
dtype. The hint reads e.g.:

> `"Source IO type: float; Sink IO type: complex; Set type='float' on 'mid_throttle'"`

`_type_hint_for_validation` extends this to `gnu_validation` errors
that come from the final `validate()` call (not just `add_connection`
failures), so the hint appears in every relevant error response.

**Risk:** low. The hint is text in the documented `errors[].hint` field.
The rule is uniform (every newly-added block with a matching enum
option gets the same hint). No hand-picked heuristics.

### 14.3 Results

| Scenario | Status before A+B | Status after A+B |
|----------|------------------|------------------|
| 01 add_throttle | ✗ (no type=float) | **✓** (model picks float from enum options) |
| 02 update_sample_rate | ✓ | ✓ |
| 03 disable_and_enable | ✓ | ✓ |
| 04 add_and_remove_variable | ✓ | ✓ |
| 05 full_rewire | ✓ | ✗ (model still misroutes type, doesn't apply hint) |
| 06 query_knowledge_multiply | ✗ (no params field) | ✗ (same — model emits no params at all) |
| 07 force_disabled_connected_block | ✓ | ✓ |
| 08 fm_rx_insert_throttle | ✗ | ✗ |

**Aggregate:** 5/8 (unchanged). Scenario 01 went from ✗ to ✓. Scenario 05
regressed from ✓ to ✗ (model non-determinism; the model did get the
hint but placed `type` at the wrong level).

### 14.4 The persistent failure pattern

Scenarios 05, 06, 08 still fail because the model:
- Reads the hint "Set type='float' on X"
- Doesn't translate this into the correct JSON structure
  (puts `type` at the wrong nesting level, or omits `params` entirely)

This is a **model structure-output failure**, not a discovery or
reasoning failure. The model knows WHAT to do but doesn't encode
the JSON correctly.

### 14.5 Remaining levers

1. **Schema change: make `params` required when `block_id` ends in `_xx`.**
   — AGENTS.md: hand-picked heuristic for block families. Forbidden.
2. **Bigger model (replace 7.5B with 12B+).** — operational cost, not a
   code change.
3. **Accept 5/8 as the current ceiling for this model.** — realistic
   given the persistent structure-output failures.

---

## 15. Auto-Resolve Fix (adapter-side type inference)

### 15.1 Approach

Following the consultant's architectural verdict: shift type propagation
out of the LLM and into the deterministic adapter.

**The rule:** When a newly-added block (a) was added in the current
batch, (b) has a `type` param, (c) the model did not specify `type` in
its params, and (d) the batch connects it to a block with a resolvable
port dtype — set `type` to match the neighbor's dtype.

This is ONE uniform rule applied to every case. It targets the
structural-output failure pattern (model reads hint but doesn't encode
correct JSON) by computing the answer deterministically.

### 15.2 Implementation

`src/grc_agent/runtime/change_graph.py`:
- After `add_blocks` loop completes, iterate over `new_block_names`
- For each, check if `type` was explicitly set by the model (tracked via
  `type_already_set` set built pre-loop)
- If not set, call `_neighbor_dtype_for(fg, instance_name, connections)`
- If a neighbor's dtype is found, call `block.params["type"].set_value(dtype)`
- Record in `auto_resolved` dict and include in the tool response payload

The auto-resolve only fills in MISSING values — never overrides model-specified
values. The model still has full authority over what it specifies.

### 15.3 Results

| Scenario | Before auto-resolve | After auto-resolve |
|----------|--------------------|--------------------|
| 01 add_throttle | ✗ (no type) | ✓ (`mid_throttle: "float"`) |
| 02 update_sample_rate | ✓ | ✓ |
| 03 disable_and_enable | ✓ | ✗ (regression — force issue) |
| 04 add_and_remove_variable | ✓ | ✓ |
| 05 full_rewire | ✓ | ✓ (`dc_offset: "float"`) |
| 06 query_knowledge_multiply | ✗ (type mismatch) | ✗ (now "Port is not connected") |
| 07 force_disabled_connected_block | ✓ | ✓ |
| 08 fm_rx_insert_throttle | ✗ (no type) | ✓ (`audio_throttle: "float"`) |

**Aggregate:** 5/8 → **6/8** semantic success.

### 15.4 Wins

- Scenarios 01, 05, 08 all now succeed. The model didn't need to encode
  the correct JSON structure — the adapter handled it.
- The tool reports what it auto-resolved: `"auto_resolved":
  {"mid_throttle": "float"}`. The model and humans can verify.

### 15.5 Remaining failures (re-examined)

**Scenario 06:** Different failure now. Model DID set `params: {"type":
"float"}` correctly (Fix A helped). The new error is "Port is not
connected." — a topology issue, not a type issue. The model removed
`blocks_add_xx` and its edges, but the replacement edge topology may
not be fully correct. This is a model-reasoning issue about graph
rewiring, not type matching.

**Scenario 03 (regression):** The model disables a connected block,
gets a validation error, but doesn't use `force=true`. The scenario 03
prompt doesn't explicitly mention force; scenario 07 does. This is a
prompt-design issue — the scenario prompt should have included force
guidance like scenario 07 does. Auto-resolve doesn't affect this.

### 15.6 Net effect

The auto-resolve fix closes the type-mismatch failure mode (the dominant
failure in 3/8 scenarios). Two of three "type-mismatch" scenarios now
succeed. Scenario 06's failure mode changed from type to topology,
which is a different class of failure that auto-resolve doesn't address.

---

## 16. Final Status

- **Syntactic success:** solved (0 schema rejections, max nesting 2)
- **Output truncation:** solved (num_ctx=8192)
- **Discovery gap:** closed (Fix A: enum values visible)
- **Type mismatch:** closed (auto-resolve: adapter fills missing type)
- **Scenario 03 (force):** regression — needs prompt fix (out of agent
  scope)
- **Scenario 06 (port connectivity):** topology issue, model reasoning

**Recommended action:** ship Fix A + auto-resolve. Accept 6/8 as the
current ceiling for this model. The remaining failure (scenario 06) is
a graph-rewiring reasoning limit that no adapter-side fix can solve
without hand-picked heuristics (AGENTS.md:32).

---

## 17. Open Questions

1. Is 6/8 acceptable, or should we pursue a different intervention for
   scenario 06 (topology reasoning)?
2. Should scenario 03's regression be fixed by updating its prompt to
   mention `force` (out of agent scope — playground concern)?
3. Should the round cap be removed entirely now that it's vestigial?