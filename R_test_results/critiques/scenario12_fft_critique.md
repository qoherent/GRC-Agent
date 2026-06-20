# Critique: Scenario 12 — FFT Pipeline (fft_pipeline_n1024)

**Status:** FAIL  
**Model:** gemma4:e4b-it-qat  
**Profile:** R4C_ADD_VARIABLE  
**Test date:** 2026-06-15T16:38:40

---

## Summary

The model **failed catastrophically**: 0 mutations committed across 6 `change_graph` attempts. The graph was completely unchanged after 85 seconds of turns. Root cause is a **backend code bug** — the `change_graph` tool schema declares `reasoning` as a required parameter, but the `_change_graph()` implementation doesn't accept it. Every single mutation call died with `TypeError: GrcAgent._change_graph() got an unexpected keyword argument 'reasoning'`.

---

## Root Cause Analysis

### PRIMARY: Schema–Implementation Mismatch

`src/grc_agent/runtime/tool_schemas.py:386-388` defines `reasoning` as a required parameter. At line 507, `required=["reasoning"]`.

But `src/grc_agent/agent.py:1598-1619` — the `_change_graph()` method — does **not** accept a `reasoning` parameter. Its signature is:
```python
def _change_graph(self, add_blocks=None, remove_blocks=None, update_params=None,
                  update_states=None, add_connections=None, remove_connections=None,
                  force=False, debug=False)
```

Dispatch at `agent.py:462` is `func(**kwargs)` — all validated kwargs are splatted into the handler. Since `reasoning` passes schema validation (it's declared valid), it reaches `_change_graph()` and Python raises `TypeError`. **The model correctly followed the schema; the backend code contradicts it.**

The validation layer at `agent.py:726-729` already strips `view` before validation but has no analogous `reasoning` strip. The `_normalize_change_graph_args` at line 513 doesn't strip it either.

**Fix:** Either (a) add `reasoning` as a no-op parameter to `_change_graph()`, (b) pop it from kwargs at `execute_tool` line 462, or (c) strip it from validation_kwargs at line 726 (though it would still reach the handler).

### SECONDARY: Catastrophic Error Loop

The model made **6 identical errors** in a row (calls 3, 4, 6, 8, 9 — all with `reasoning`). It never adapted despite receiving the clear error message `got an unexpected keyword argument 'reasoning'` each time. The model appears unable to drop a schema-required parameter even when the runtime rejects it, suggesting it prioritizes schema compliance over runtime error recovery.

### TERTIARY: Poor query_knowledge Retrieval

| # | Query | Result Quality |
|---|-------|---------------|
| 1 | "Stream to Vector block id" | **Irrelevant** — returned Complex To IChar, IChar To Complex, etc. Lexical search failed. |
| 2 | "stream to vector block id" | **Same irrelevant results** — case change didn't help. |
| 3 | "Stream to Vector FFT blocks" | **Relevant** — returned FFT, Stream to Vector, Vector to Stream. |

The model wasted 3 calls and ~1.2s on retrieval before getting usable block IDs. The lexical search mode doesn't handle trailing noise like "block id" well — it matches tokens literally.

### QUATERNARY: Wrong FFT Block

When the model finally found blocks, it selected `uhd_fpga_fft` — an FPGA hardware FFT for USRP RFNoC — instead of the standard GNU Radio software FFT (`fft_fft_vxx`). A dial-tone audio pipeline has no FPGA.

### QUINARY: No Connections, No Params, No Wiring

The most complete call (#9) added blocks `fft_len`, `stov`, `forward_fft`, `null_sink` but:
- No `add_connections` — blocks were added but entirely disconnected
- Stream to Vector had no `vlen` param set to `fft_len`
- FFT had no `fft_size` param set to `fft_len`
- `uhd_fpga_fft` port names differ from `fft_fft_vxx` anyway

---

## Per-Turn Analysis

### Turn 1 (single turn, 10 tool calls, 85.2s)

**Call 1 — `query_knowledge(domain="catalog", query="Stream to Vector block id")`**  
- **Tool:** Correct (query_knowledge)  
- **Domain:** Correct (catalog)  
- **Query:** Poor — "block id" suffix caused lexical search to return irrelevant results (Complex To IChar, etc.)  
- **Result:** 5 blocks returned, none useful  

**Call 2 — `inspect_graph({})`**  
- **Tool:** Correct (inspect_graph)  
- **Args:** Empty — correct for full overview  
- **Result:** Success, returned complete graph topology (8 blocks, 4 connections)  
- **Verdict:** Correct  

**Call 3 — `change_graph(add_blocks=[{block_id:"variable", instance_name:"fft_len"}], reasoning=...)`**  
- **Tool:** Correct (change_graph)  
- **Args:** Includes `reasoning` — follows schema but kills the call  
- **Params:** Missing `value: 1024` on fft_len  
- **Result:** `TypeError: unexpected keyword argument 'reasoning'`  
- **Verdict:** Model follows buggy schema; code bug prevents execution  

**Call 4 — `change_graph(remove_blocks=[{instance_name:"audio_sink"}], reasoning=...)`**  
- **Tool:** Correct  
- **Args:** Again includes `reasoning`  
- **Result:** Same TypeError  
- **Verdict:** Model repeats same structural error  

**Call 5 — `query_knowledge(domain="catalog", query="stream to vector block id")`**  
- **Tool:** Correct  
- **Args:** Lowercase query — same bad retrieval  
- **Result:** Same irrelevant blocks  
- **Verdict:** Wasted call; model didn't learn from call 1  

**Call 6 — `change_graph(add_blocks=[{block_id:"variable",...}], remove_blocks=[{audio_sink}], reasoning=...)`**  
- **Tool:** Correct  
- **Args:** Combines add+remove, but still has `reasoning`  
- **Params:** Still missing `value: 1024` on fft_len  
- **Result:** Same TypeError  
- **Verdict:** Mutated but not fixed  

**Call 7 — `query_knowledge(domain="catalog", query="Stream to Vector FFT blocks")`**  
- **Tool:** Correct  
- **Query:** Better — removed "block id" suffix  
- **Result:** Success — returned Stream to Vector (`blocks_stream_to_vector`), FFT (`uhd_fpga_fft`), Vector to Stream  
- **Verdict:** Correct retrieval at last, but `uhd_fpga_fft` is wrong block  

**Call 8 — `change_graph(add_blocks=[{block_id:"variable",...}], remove_blocks=[{audio_sink}], reasoning=...)`**  
- **Tool:** Correct  
- **Args:** Same structure as call 6, still has `reasoning`  
- **Result:** Same TypeError  
- **Verdict:** Model still not adapting after 3 identical errors  

**Call 9 — `change_graph(add_blocks=[{block_id:"variable",..., params:{value:1024}}, {block_id:"blocks_stream_to_vector", instance_name:"stov"}, {block_id:"uhd_fpga_fft", instance_name:"forward_fft"}, {block_id:"null_sink", instance_name:"null_sink"}], remove_blocks=[{audio_sink}], reasoning=...)`**  
- **Tool:** Correct  
- **Args:** Best structural attempt — all 4 blocks, fft_len has value:1024, Stream to Vector has correct block_id  
- **Problems:**
  1. **Still has `reasoning`** — same TypeError, so nothing commits
  2. **Wrong FFT block** — `uhd_fpga_fft` is FPGA-only; needs `fft_fft_vxx`
  3. **No `add_connections`** — blocks are orphaned
  4. **No `vlen` on Stream to Vector** — should be `{"vlen": "fft_len"}`
  5. **No `fft_size` on FFT** — should be `{"fft_size": "fft_len"}`
- **Result:** Same TypeError  
- **Verdict:** Best semantic payload, dead on arrival due to `reasoning` param  

**Call 10 — `inspect_graph({})`**  
- **Tool:** Correct  
- **Args:** Empty (same as call 2)  
- **Result:** **DUPLICATE DEDUP** — rejected because args identical to call 2 and state_revision never changed  
- **Verdict:** Correct dedup behavior, but the model was trying to verify changes that never happened  

---

## Overall Assessment

| Dimension | Result | Assessment |
|-----------|--------|------------|
| routing_pass | PASS | Only used MVP tools (query_knowledge, inspect_graph, change_graph) |
| argument_pass | PASS | Arguments structurally valid per schema; `reasoning` is a schema-declared field |
| tool_success_pass | PASS | No tool caused an uncaught crash; errors were clean TypeErrors |
| semantic_pass | FAIL | Model failed to understand tool surface; repeated same error 5x; wrong FFT block |
| safety_pass | PASS | No dangerous operations attempted |
| end_state_pass | FAIL | Graph completely unchanged (0 blocks, 0 connections modified) |
| recovery_pass | PASS | System returned clean error messages — but model never recovered |
| model_contract_pass | PASS | Model produced tool calls, not non-compliant output |
| runtime_safety_pass | PASS | No crashes, no data corruption |
| budget_pass | PASS | Within budget |
| lint_pass | PASS | Validation passes on unchanged graph |

**True severity:** The `semantic_pass` should arguably be even stronger FAIL given 5 consecutive identical errors. `tool_success_pass` being PASS is generous when 6/10 calls failed, though the rubric likely checks for system crashes.

---

## Proposed Improvements

### Critical (Blocking)

1. **Fix `_change_graph()` to accept `reasoning`** (`agent.py:1598-1619`)  
   Add `reasoning: str | None = None` to the method signature, or strip it from kwargs at the dispatch point (`agent.py:462`). The schema declares it required; the implementation must tolerate it.

2. **Fix `execute_tool` dispatch to filter unknown kwargs** (`agent.py:462`)  
   Either inspect the handler's signature and pass only accepted params, or whitelist known params for each tool. This prevents any future schema–handler mismatches.

### High

3. **Improve search retrieval for "Stream to Vector"**  
   The lexical search failed to match the canonical `blocks_stream_to_vector` for "Stream to Vector block id". The trailing "block id" tokens crowded out the meaningful terms. Either strip common noise tokens or boost semantic retrieval for catalog queries.

4. **Add error pattern recognition for catastrophic loops**  
   If the same tool with the same error type fires ≥3 consecutive times, inject a runtime reminder or force-terminate the loop. The model should not be allowed to burn 5 mutation attempts on the same schema error.

### Medium

5. **Validate block selection for context**  
   `uhd_fpga_fft` is inappropriate for an audio flowgraph. The catalog results didn't include `fft_fft_vxx` (the standard FFT) in the top 5. Consider domain-tagged searching or filtering out FPGA-only blocks when the graph has no UHD sources.

6. **Test should verify `add_connections` in expected chain**  
   The current prompt tells the model to connect the blocks, but no test assertion validates that connections were made. Add connection assertions to catch wiring omissions.

### Low

7. **Dedup cache should not block read-only calls forever**  
   After 5 failed mutations, the dedup cache made `inspect_graph({})` a permanent no-op because state_revision never changed. Consider a backoff or timestamp-based dedup for read-only calls.

---

## Root Cause Verdict

**The test failure is 100% caused by a backend code bug** (`agent.py:462` splats `reasoning` into a function that doesn't accept it), **compounded by** the model's failure to adapt after 5 identical errors. The model correctly followed the schema; the implementation contradicted it. Fix the schema–handler mismatch first, then address the catastrophic error loop and retrieval quality.
