# Scenario 11 Critique: NBFM Pivot — FAIL

**Model:** gemma4:e4b-it-qat | **Profile:** R4B_REMOVE_BLOCK | **Status:** FAIL (semantic)

---

## Summary

The model attempted a single-turn execution of the NBFM pivot but failed completely. The `change_graph` mutation was rejected because it included an invalid `reasoning` parameter not in the tool schema. Even if that parsing error were fixed, the mutation was semantically wrong (wrong port topology, no removal of Audio Sink, no param setting, no ZMQ PUB Sink). The end-state graph is **identical** to the initial graph — zero mutations applied.

---

## Per-Turn Analysis

### Turn 0 (only turn) — 54.5s | PASS routing, PASS argument, FAIL semantic, FAIL end_state

#### Tool 1: `inspect_graph` — Correct
- **Args:** `{}` (no targets/params specified — returns full overview)
- **Verdict:** Right tool, right first step. The model correctly inspected the graph to understand the existing topology: 8 blocks (3 sources, 1 Add, 1 Audio Sink, 3 vars), 4 connections.
- **Observations:** The returned snapshot confirmed `blocks_add_xx:0->audio_sink:0` as the output path to remove.

#### Tool 2: `query_knowledge(domain="catalog", query="nbfm transmit audio rate quadrature rate")` — Correct
- **Verdict:** Right tool, appropriate query. Found `analog_nbfm_tx` (NBFM Transmit) as the top result alongside `analog_nbfm_rx`, `analog_wfm_tx`, `analog_wfm_rcv`.
- **Issue:** The results only returned block IDs and names (truncated output). The model correctly identified `analog_nbfm_tx` as the target.

#### Tool 3: `query_knowledge(domain="catalog", query="analog_nbfm_tx audio rate quadrature rate param id")` — Correct
- **Verdict:** Right tool, good follow-up query. This returned full metadata for `analog_nbfm_tx`:
  - **Inputs:** 1 stream (float)
  - **Outputs:** 1 stream (complex)
  - **Params:** `audio_rate` (int), `quad_rate` (int), `tau`, `max_dev`, `fh`
- **Critical info acquired:** The block has exactly **1 input port** (port 0, float) and **1 output port** (port 0, complex). The param IDs are `audio_rate` and `quad_rate`.

#### Tool 4: `change_graph` — FAIL (root cause)
- **Args:**
  ```json
  {
    "add_blocks": [{"block_id": "analog_nbfm_tx", "instance_name": "nbfm_tx"}],
    "add_connections": [
      {"dst": {"block": "nbfm_tx", "port": 0}, "src": {"block": "blocks_add_xx", "port": 0}},
      {"dst": {"block": "nbfm_tx", "port": 1}, "src": {"block": "blocks_add_xx", "port": 1}}
    ],
    "reasoning": "The user wants to convert..."
  }
  ```
- **Failure 1 (fatal):** `reasoning` is not a valid parameter of `change_graph`. The backend raised `GrcAgent._change_graph() got an unexpected keyword argument 'reasoning'`. Entire mutation rejected.
- **Failure 2 (wire semantics):** `analog_nbfm_tx` has 1 input port (port 0), not 2. Port 1 doesn't exist. Also, `blocks_add_xx` (type=float, num_inputs=3) has only **1 output port** (port 0 — the scalar sum). Port 1 doesn't exist on the source side either.
- **Failure 3 (missing operations):**
  - `remove_blocks` for audio_sink — not included
  - `update_params` for `audio_rate` / `quad_rate` — not included
  - `add_blocks` for `zeromq_pub_sink` — not included
  - Connection from `nbfm_tx:0` → `zeromq_pub_sink:0` — not included
  - `remove_connections` for `blocks_add_xx:0->audio_sink:0` — not included
- **What the model *should* have done in one mutation batch:**
  1. `remove_blocks: ["audio_sink"]`
  2. `remove_connections: [{src:{block:"blocks_add_xx",port:0}, dst:{block:"audio_sink",port:0}}]`
  3. `add_blocks: [{block_id:"analog_nbfm_tx", instance_name:"nbfm_tx"}, {block_id:"zeromq_pub_sink", instance_name:"zmq_pub_sink"}]`
  4. `update_params: [{block:"nbfm_tx", param:"audio_rate", value:"samp_rate"}, {block:"nbfm_tx", param:"quad_rate", value:"samp_rate*8"}, {block:"zmq_pub_sink", param:"address", value:"tcp://10.0.0.1:7777"}]`
  5. `add_connections: [{src:{block:"blocks_add_xx",port:0}, dst:{block:"nbfm_tx",port:0}}, {src:{block:"nbfm_tx",port:0}, dst:{block:"zmq_pub_sink",port:0}}]`

#### Tool 5: `query_knowledge(domain="catalog", query="zmq pub sink address")` — Correct (but too late)
- **Verdict:** Found `zeromq_pub_sink` (ZMQ PUB Sink) as block ID. Correct lookup.
- **Issue:** This lookup happened *after* the failed `change_graph`. The model never issued a second `change_graph` to apply any of this information. The turn ended with no state change.

#### Model Reply
- `"Block candidates returned."` — This is the message echoed from the last query_knowledge result. It is not a useful summary for the user. No explanation of what was built or what failed.

---

## Overall Assessment

| Criterion | Grade | Notes |
|---|---|---|
| Tool routing | PASS | Only MVP tools used (`inspect_graph`, `query_knowledge`, `change_graph`) |
| Inspection first | PASS | `inspect_graph` was first call |
| Block discovery | PASS | Correct block `analog_nbfm_tx` found, params identified |
| Change_graph schema | FAIL | Included `reasoning` — not in tool schema, caused hard error |
| Block ID correctness | PASS | `analog_nbfm_tx` is correct |
| Wire routing | FAIL | `nbfm_tx` port 1 doesn't exist; `blocks_add_xx` port 1 doesn't exist |
| Audio Sink removal | FAIL | Never attempted |
| Param setting | FAIL | `audio_rate` / `quad_rate` never set via `update_params` |
| ZMQ PUB Sink | FAIL | Looked up but never added or wired |
| Model reply quality | FAIL | `"Block candidates returned."` — no value to user |
| Surface compliance | PASS | No non-MVP tools used (no accidental tool calls) |

---

## Root Cause Analysis

**Primary failure:** The `change_graph` call included a `reasoning` field that is not part of the tool schema. The backend rejected the entire mutation with `internal_error`. The model never retried.

**Contributing factor 1:** The model packed all mutation logic into a single `change_graph` call but got the wiring topology wrong — it assumed `blocks_add_xx` has 2 output ports (it has 1) and `nbfm_tx` has 2 input ports (it has 1). This indicates the model did not properly parse the `inspect_graph` results (the Add block has `num_inputs: 3` — 3 input ports, 1 output port) nor the catalog lookup (1 input, 1 output for `analog_nbfm_tx`).

**Contributing factor 2:** After the `change_graph` error, the model issued a `query_knowledge` for ZMQ PUB Sink but never followed up with a second `change_graph`. The turn ended with the graph in its original state.

**Contributing factor 3:** No retry/recovery was attempted. The test infrastructure classified this as "no_recovery_needed" because the error was an internal schema error rather than a tool failure, but the model had budget to continue and didn't.

---

## Proposed Improvements

1. **Schema validation on the model side:** The `reasoning` hallucination suggests the model's system prompt or tool description for `change_graph` did not sufficiently constrain the allowed parameters. The tool schema should be surfaced more explicitly, or the backend should strip unknown kwargs instead of hard-failing.

2. **Port topology comprehension:** The model failed to translate `num_inputs: 3` (on `blocks_add_xx`) and `inputs: [stream(float)]` (on `analog_nbfm_tx`) into correct port counts. The `inspect_graph` results or the catalog response should make port cardinality more salient — e.g., explicit `input_port_count` / `output_port_count` fields.

3. **Single-turn completeness:** For R4B_REMOVE_BLOCK profile, the model needs to handle all operations (remove, add, set params, rewire) in one or two `change_graph` calls. The observed attempt was missing 4 out of 5 required operations. Consider adding a pre-mutation planning step or checklist prompt.

4. **Error recovery:** After `change_graph` returns `ok: false`, the model should interpret the error, strip invalid args, and retry. The recovery subsystem should trigger on any failed mutation with budget remaining.

5. **Model reply quality:** "Block candidates returned" is a raw tool result echo. The model should produce a coherent summary of actions taken/not taken. This could be enforced via the model contract evaluation dimension.
