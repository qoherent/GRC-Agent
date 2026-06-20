# R0 Release Critique — gemma4:e4b-it-qat

## Summary

- **Total scenarios**: 14
- **Passed**: 13
- **Failed**: 1
- **Pass rate**: 92.9%
- **Breakdown**: `inspect`=6, `search`=2, `docs`=2, `external`=4

**Bottom line**: The headline pass rate is misleadingly high. Three of the 13 "passes" contain serious semantic errors or tool-selection failures that should have been caught. The single formal FAIL (`search/search_time_sink`) is a query-quality problem, not a tool-routing problem. The real semantic pass rate is closer to 71% (10/14).

---

## Per-Scenario Analysis

### 1. [PASS] inspect/summarize_what_does
- **Tools used**: `inspect_graph({})`
- **Expected**: `inspect_graph`
- **Elapsed**: 74.76s
- **Critique**: Correct tool selection. Empty arguments are acceptable for a blanket "what does this do" — the overview view gives the model everything it needs. Reply is accurate and well-structured, correctly traces the signal chain from Random Source → Throttle → Char To Float → QT GUI Time Sink. No hallucination.
- **Efficiency**: Single call, correct. 74.76s is very slow — likely backend inference latency (model generates after tool return), not a model behavior issue.
- **Suspicion**: None.
- **Improvement**: None needed.

### 2. [PASS] inspect/summarize_blocks
- **Tools used**: `inspect_graph({})`
- **Expected**: `inspect_graph`
- **Elapsed**: 9.54s
- **Critique**: Correct tool, correct no-args call. Reply lists all 5 blocks with types and instance names. Accurate and complete.
- **Efficiency**: Single call, optimal.
- **Suspicion**: None.
- **Improvement**: None needed.

### 3. [PASS] inspect/context_throttle
- **Tools used**: `inspect_graph({targets: ["blocks_throttle2_0"]})`
- **Expected**: `inspect_graph`
- **Elapsed**: 7.36s
- **Critique**: Excellent. The model used a **targeted** query with the specific block name, which returns only the relevant block's connections/params rather than the full overview. This is the optimal pattern — minimal data, faster response. Reply correctly shows input from `analog_random_source_x_0:0` and output to `blocks_char_to_float_0:0`.
- **Efficiency**: Optimal — no wasted fields returned.
- **Suspicion**: None.
- **Improvement**: This is the gold standard for block-specific queries. Document this pattern.

### 4. [PASS] inspect/context_samp_rate
- **Tools used**: `inspect_graph({params: ["samp_rate"]})`
- **Expected**: `inspect_graph`
- **Elapsed**: 12.86s
- **Critique**: **Correct tool, semantically wrong reply.** The model says: *"the `samp_rate` block ... does not appear to be connected to or used by any other blocks in the active signal flow."* This is **factually incorrect**. The inspect_graph response shows:
  - `blocks_throttle2_0` has `samples_per_second: "samp_rate"`
  - `qtgui_time_sink_x_0` has `srate: "samp_rate"`
  
  The model confused "stream connections" (the `connections` array) with "parameter references" (variable bindings in block params). The tool's `connections` field only lists stream edges, not variable references. The model should have cross-referenced block params to find `samp_rate` usage. This is a genuine semantic error that the test PASS criteria missed.
- **Efficiency**: Using `params` argument was correct intent, but the tool returned the full overview (same as empty args), not a param-cross-reference view.
- **Suspicion**: This reveals a tool schema gap: there's no way to query "what blocks reference variable X?" The `params` field in inspect_graph appears to return the same overview regardless.
- **Improvement**: Two options: (a) add a `variable_references` field to inspect_graph output that lists which blocks reference each variable, (b) train the model to scan `summary.blocks[].params` for string values matching the variable name. Option (a) is more reliable.

### 5. [PASS] inspect/status_check
- **Tools used**: `inspect_graph({})`
- **Expected**: `inspect_graph`
- **Elapsed**: 10.12s
- **Critique**: Correct. Reply nicely formats name, revision, validation, blocks table, and connections. No errors.
- **Efficiency**: Single call, fine.
- **Suspicion**: None.
- **Improvement**: Could optionally use `{targets: ["all"]}` to be slightly more explicit, but no-args works.

### 6. [PASS] inspect/compile_status
- **Tools used**: `inspect_graph({targets: ["all"]})`
- **Expected**: `inspect_graph`
- **Elapsed**: 11.23s
- **Critique**: Correct tool. Reply correctly says "valid". However, the model over-asked: the validation_status is available in the overview (no-args call) as `validation_status.status`. Using `{targets: ["all"]}` returns the full block topology which is unnecessary for this question. Minor waste.
- **Efficiency**: Slightly suboptimal — `{targets: ["all"]}` is equivalent to `{}` but semantically odd (there's no block named "all" in the graph).
- **Suspicion**: Using `targets: ["all"]` suggests the model may have guessed at an argument format rather than knowing the schema. The tool accepts target block names, so "all" is not a valid target name. This happened to work because the backend appears to treat it as a no-op / overview request.
- **Improvement**: Clarify in the schema that `targets` accepts block instance names, not meta-keywords.

### 7. [FAIL] search/search_time_sink
- **Tools used**: `query_knowledge({domain: "catalog", query: "time sink block"})`
- **Expected**: `query_knowledge`
- **Elapsed**: 11.78s
- **Failure reason**: `argument_pass` = FAIL
- **Critique**: **Correct tool, wrong query.** The model correctly routed to `query_knowledge` with `domain="catalog"`, which is exactly right. But the query `"time sink block"` was too generic. The catalog returned `audio_sink`, `uhd_rfnoc_null_src_sink`, and `uhd_msg_push_button` — none of which are "time sink" blocks. The model's reply was also bad: it presented these irrelevant results and even said `uhd_rfnoc_null_src_sink` "is often the most direct match." The actual answer should be `qtgui_time_sink_x` (QT GUI Time Sink).
- **Root cause**: Query quality. `"qtgui time sink"` or `"time sink"` would likely have returned the correct block. The model didn't use GNU Radio-specific terminology.
- **Avoidable**: Yes. Better query construction: `"qtgui time sink"` or just `"time sink"` as a tighter phrase.
- **Suspicion**: This was the only FAIL and it tests the most nuanced skill (catalog search with specific block names). The model's tendency to use generic NL queries instead of known block IDs is a pattern.
- **Improvement**: (a) Train model to prefer block-specific terminology in catalog queries. (b) Consider whether the catalog search should auto-suggest known block type strings. (c) Add a fallback: if first query returns irrelevant results, try again with a more specific query.

### 8. [PASS] search/search_session_source
- **Tools used**: `inspect_graph({})`
- **Expected**: `inspect_graph`
- **Elapsed**: 7.71s
- **Critique**: Correct. Model correctly inspected the graph and identified `analog_random_source_x` as the only source block (by its `role: "source"`). Reply is accurate.
- **Efficiency**: Fine.
- **Suspicion**: None.
- **Improvement**: None.

### 9. [PASS] docs/pmt_dict_immutability
- **Tools used**: **_(none)_**
- **Expected**: `query_knowledge`
- **Elapsed**: 15.88s
- **Critique**: **This should be a FAIL.** The user asked about **PMT** (Polymorphic Types) dictionaries — a GNU Radio-specific concept. The model did not use `query_knowledge` at all. Instead, it answered about standard Python dictionaries (`dict` unpacking, merge operators), and only in the very last paragraph added a caveat: *"If `PMT` refers to a specific class or library object within the GNU Radio environment..."*. The model guessed wrong — it assumed PMT was a typo for "Python dict" or an unknown custom class.
- **Root cause**: The model failed to recognize "PMT" as a GNU Radio term. It treated the question as a general Python question. It should have called `query_knowledge(domain="docs", query="PMT dictionary immutability")` or similar.
- **Avoidable**: Yes. The model ignored its documented tool for GNU Radio documentation queries.
- **Suspicion**: Why does this test say PASS? `routing_pass: PASS` when no tool was called and `query_knowledge` was expected is very concerning. It suggests the routing check is too lenient — perhaps it checks "did you call any allowed tool" rather than "did you call the expected tool." This is a **test harness bug**.
- **Improvement**: (a) The test harness must enforce that `expected_tools` includes at least one tool that was actually called. (b) The model needs to be much more sensitive to GNU Radio-specific terminology (PMT, grc, flowgraph, etc.). (c) Consider inserting a system prompt reminder that any GNU Radio terminology question should first try `query_knowledge`.

### 10. [PASS] docs/binary_short_scaling
- **Tools used**: **_(none)_**
- **Expected**: `query_knowledge`
- **Elapsed**: 17.24s
- **Critique**: **Same problem as scenario 9.** The user asked about "scale factor between floats and 16-bit shorts" — this is clearly a GNU Radio/conversion-block question. The model answered with generic DSP theory (quantization, Q format, linear scaling) without consulting GNU Radio docs. It never checked whether GNU Radio has a `blocks_short_to_float` or `blocks_float_to_short` block with a specific `scale` parameter.
- **Root cause**: Same as above — the model didn't recognize this as a query_knowledge-worthy question.
- **Avoidable**: Yes.
- **Suspicion**: Same test harness issue. `routing_pass: PASS` when expected tools include `query_knowledge` and no tool was called.
- **Improvement**: (a) Same test harness fix. (b) Train model that questions about data type conversions, scales, and block parameters should trigger `query_knowledge`.

### 11. [PASS] external/dial_tone_summary
- **Tools used**: `inspect_graph({})`
- **Expected**: `inspect_graph`
- **Elapsed**: 19.93s
- **Critique**: Correct. Model inspects the dial_tone graph and produces an accurate summary of signal sources, noise, adder, and audio sink. Reply correctly identifies all 3 signal sources (2 analog + 1 noise), the `blocks_add_xx` mixer, and the `audio_sink`. Good.
- **Efficiency**: Single call, correct.
- **Suspicion**: None.
- **Improvement**: None needed.

### 12. [PASS] external/resampler_status
- **Tools used**: `inspect_graph({})`
- **Expected**: `inspect_graph`
- **Elapsed**: 24.52s
- **Critique**: Correct. Reply accurately describes the resampler_demo graph: signal source → adder → throttle → FM modulator → PFB Arb Resampler → freq sink. Mentions control variables (`frac_bw`, `new_rate`, etc.). Good detail.
- **Efficiency**: Single call, fine.
- **Suspicion**: None.
- **Improvement**: None.

### 13. [PASS] external/stream_mux_status
- **Tools used**: `inspect_graph({})`
- **Expected**: `inspect_graph`
- **Elapsed**: 21.28s
- **Critique**: Correct. Reply accurately describes the Stream Mux example: two vector sources, stream mux, throttle, three time sinks. Correctly traces the data flow.
- **Efficiency**: Single call, fine.
- **Suspicion**: None.
- **Improvement**: None.

### 14. [PASS] external/sig_source_msg_ports_context
- **Tools used**: `inspect_graph({targets: ["analog_sig_source_x_0"]})`
- **Expected**: `inspect_graph`
- **Elapsed**: 11.32s
- **Critique**: Excellent. Model used a **targeted** query with the specific block name, like scenario 3. Reply correctly shows message inputs (`cmd` from `blocks_message_strobe_0` and `blocks_message_strobe_random_0`) and stream output (port 0 → `blocks_throttle_0`). The model correctly distinguished between message ports and stream ports.
- **Efficiency**: Optimal — targeted query.
- **Suspicion**: None.
- **Improvement**: This is pattern-matching excellence. Document this approach.

---

## Cross-Cutting Patterns

### Pattern 1: Tool-routing failures on domain-knowledge questions (Scenarios 9, 10)
The model failed to call `query_knowledge` for two questions that clearly reference GNU Radio concepts (PMT dictionaries, float↔short scaling). In both cases it answered from its general training knowledge — plausible-sounding answers that are technically correct in a vacuum but wrong in the GNU Radio context. The test harness marked these as PASS despite the model not using the expected tool.

**Severity**: HIGH. This undermines the entire value proposition of the tool-use architecture. If the model bypasses its knowledge tools on domain questions, users will get generic/incorrect answers.

**Root cause**: Two factors:
1. The model doesn't reliably recognize GNU Radio specific terminology (PMT, scale factors for type conversion blocks) as triggers for tool use.
2. The test harness allows `routing_pass: PASS` even when `expected_tools` includes `query_knowledge` and no tool was called. This is a test validation gap.

### Pattern 2: Variable-reference blindness (Scenario 4)
The model correctly inspects a variable but fails to trace which blocks reference it via parameter bindings. There's a gap between "stream connections" (shown in `connections[]`) and "parameter references" (embedded in block params). The tool output shows the params, but the model didn't synthesize them.

**Severity**: MEDIUM. For a flowgraph assistant, understanding variable dependencies is essential. If a user asks "what uses `samp_rate`?", the answer should list `blocks_throttle2_0` and `qtgui_time_sink_x_0`.

**Root cause**: The `inspect_graph` output separates connections (stream edges) from block params. Variable references live in params but are not cross-indexed. The model needs to scan every block's param dict for string values matching the variable name — a non-trivial synthesis step.

### Pattern 3: Over-fetching with no-args calls (Scenarios 1, 2, 5, 8, 11, 12, 13)
7 of 14 scenarios used `inspect_graph({})` — the full overview. This is fine for simple graphs (5-10 blocks) but won't scale to larger graphs. The model rarely used the `targets` argument even when asking about a specific block.

**Severity**: LOW for R0 (small graphs), HIGH for future releases (large graphs will produce enormous tool results).

**Root cause**: No-args is the path of least resistance. The model isn't rewarded for being efficient.

### Pattern 4: Generic catalog queries (Scenario 7)
The model's `query_knowledge` queries are overly generic. `"time sink block"` returned irrelevant blocks. More specific queries (`"qtgui time sink"`, `"time sink"`) would have returned correct results.

**Severity**: MEDIUM. Poor query quality leads to wrong answers even when the right tool is used.

### Pattern 5: No surface violations
Zero attempts to call `change_graph` or any tool outside the R0 surface. Safety compliance is perfect.

**Severity**: N/A (positive).

### Pattern 6: Latency variation
Tool-only turns (7–25s) are reasonable. The outlier is scenario 1 (74.76s), which includes model generation after tool return. The `docs` scenarios (9, 10: 15–17s) are slow because the model generates long generic answers without calling any tool.

---

## Proposed Improvements

### Test Harness Fixes (HIGH priority)

1. **Enforce tool routing strictly**: `routing_pass` must check that at least one tool from `expected_tools` was actually called. Currently it passes when no tool was called. This is why Scenarios 9 and 10 show `routing_pass: PASS` despite `expected_tools: ["query_knowledge"]` and no tool called.

2. **Add semantic assertions for `docs` category**: For documentation questions, the model's answer should reference concrete GNU Radio APIs, constants, or block names. If the answer is purely generic (no GNU Radio-specific content), flag it as a semantic failure regardless of tool routing.

### Prompt Fixes (HIGH priority)

3. **Strengthen `query_knowledge` triggering**: Add explicit instruction that ANY question containing "PMT", "scale factor", or referencing GNU Radio data types should first call `query_knowledge`. Consider a blocklist of terms that must trigger a knowledge query.

4. **Add variable-reference training**: Train the model (via prompt examples) that "what uses variable X?" requires scanning all block params for string references, not just checking the `connections` array. Example:
   ```
   User: "Show me what uses the samp_rate block."
   Model action: For each block in the graph, check if any param value equals "samp_rate".
   ```

### Tool Schema Fixes (MEDIUM priority)

5. **Add `variable_references` to `inspect_graph` output**: A new field mapping variable names to the blocks that reference them:
   ```json
   "variable_references": {
     "samp_rate": [
       {"block": "blocks_throttle2_0", "param": "samples_per_second"},
       {"block": "qtgui_time_sink_x_0", "param": "srate"}
     ]
   }
   ```
   This eliminates the synthesis burden on the model.

6. **Add `suggest_queries` to `query_knowledge`**: When a catalog query returns few or irrelevant results, auto-suggest alternative query terms. Or implement a fallback: if the first query returns < N relevant results, try a more specific query automatically.

### Test Suite Expansion (LOW priority)

7. **Add efficiency assertions**: Flag scenarios where the model uses `inspect_graph({})` when a targeted query would suffice. Start with a warning, eventually enforce.

8. **Add "anti-generic-answer" check for `docs` domain**: Any `docs`-category scenario where the answer lacks GNU Radio-specific identifiers (block names, API functions, constants) should get a semantic penalty.
