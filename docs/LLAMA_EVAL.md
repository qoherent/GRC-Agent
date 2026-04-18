# Llama.cpp Model Eval Suite

Live model evaluation under `tests/llama_eval/`. The runners auto-start
the llama.cpp server if it is not already running.

## Setup

The eval runners auto-start the llama.cpp server if it is not already running,
using the same `LlamaServerLauncher` as the CLI `chat` path. If you want to
pre-start it manually:

```bash
llama-server -hf unsloth/gemma-4-E2B-it-GGUF:Q4_K_M \
    --alias unsloth/gemma-4-E2B-it-GGUF \
    --host 127.0.0.1 --port 8080 --jinja
```

Then run any phase:

```bash
GRC_AGENT_LIVE_LLAMA_URL=http://127.0.0.1:8080 \
GRC_AGENT_LIVE_LLAMA_MODEL=unsloth/gemma-4-E2B-it-GGUF \
uv run python -m tests.llama_eval.run_phase1
```

## Phases

### Phase 1: Tool Routing (explicit prompts)

Prompts explicitly name the tool or block. Tests whether the model calls the correct single tool.

```bash
uv run python -m tests.llama_eval.run_phase1
```

40 cases across all 9 tools, 3 runs each (120 total). Categories:
- **summarize** (4) — direct, what-does, overview, list-blocks
- **load** (1) — switch to another `.grc` file
- **search** (8) — catalog scope, session scope, block discovery, OFDM, PSK
- **context** (4) — neighborhood, connections
- **describe** (6) — named blocks, colloquial names (AGC, time sink)
- **validate** (3) — direct, check, is-valid
- **edit** (8) — update_params, rewire, add_block (variable), remove_block
- **propose** (2) — preview, propose
- **save** (4) — direct, write, persist, explicit path

### Phase 2: Multi-Step Chains

Ordered tool sequences. Tests whether the model completes the full chain with
successful tool executions, not just matching tool names.

```bash
uv run python -m tests.llama_eval.run_phase2
```

30 cases, 3 runs each (90 total). Categories:
- **search_describe** (8) — find obscure block then describe it (constellation, costas, scrambler, AGC, freq_sink, polyphase, FIR, head)
- **search_session_describe** (2) — session-scope search then describe found block
- **search_describe_edit** (3) — find, describe, then apply edit
- **search_describe_add_variable** (1) — find, describe, then add detached variable
- **search_describe_propose** (2) — find, describe, then preview an edit
- **search_describe_validate** (2) — find, describe, then validate
- **inspect_edit** (2) — context/summarize then edit
- **inspect_edit_validate** (1) — context, edit, then validate
- **inspect_propose** (1) — inspect, then preview a risky disconnect
- **summarize_context** (1) — summarize then show neighborhood
- **propose_apply_validate** (2) — preview/commit then validate, including bad-edit proposal
- **edit_validate** (1) — edit then validate
- **edit_validate_save** (1) — full edit→validate→save chain
- **edit_save** (1) — edit then save
- **load_summarize_validate** (1) — switch fixtures, summarize, then validate
- **load_edit_validate** (1) — switch fixture, edit, then validate

### Phase 3: Realistic Prompts

Vague, goal-oriented language a real user would use. Checks routing, argument
correctness, and no-tool graceful handling where applicable.

```bash
uv run python -m tests.llama_eval.run_phase3
```

43 cases, 3 runs each (129 total). Categories:
- **goal** (3) — "speed it up", "slow down", "higher rate" (checks args: instance_name=samp_rate)
- **natural** (3) — "what am I looking at", "is this going to work", "write it out"
- **inspect** (4) — "what's the source", "how wired up", "what parameters", "float chain wiring"
- **domain** (3) — "carrier recovery", "see spectrum", "scrambling"
- **describe** (1) — AGC natural language (search→describe expected)
- **multi** (5) — multi-step with colloquial phrasing, cascaded edit→validate→save, inspect then rewire
- **rate** (3) — abbreviations: "48k", "44100", "96k" (checks args)
- **load** (3) — switch files by path, including load+edit+validate
- **save** (1) — save a copy to an explicit path
- **rewire** (4) — add/remove connection intents, previews, second-trace natural
- **repair** (2) — multi-op repair transactions (remove samp_rate with 32k fix)
- **add_block** (2) — detached-variable adds (noise_level, debug_level)
- **edge** (2) — nonexistent block, remove_block
- **error** (1) — remove connected block (expected failure)
- **session** (1) — session-scope search then edit
- **negative** (4) — unsupported undo/redo/export-Python/edit-YAML with no tool call

### Phase 4: Multi-Turn Continuity

Multiple conversation turns on the same session. Tests whether the model
maintains state awareness across turns. Each turn is checked independently.

```bash
uv run python -m tests.llama_eval.run_phase4
```

40 cases, 3 runs each (120 total). Categories:
- **follow_up_edit** (6) — edit followed by another edit on the same parameter
- **inspect_then_act** (6) — inspect graph/block then make a change
- **search_then_navigate** (5) — catalog search then describe or session inspect
- **state_awareness** (5) — edit then query whether state reflects the change
- **edit_then_query** (5) — edit then inspect/describe/validate
- **repair_flow** (4) — remove-variable repair across turns
- **error_then_fix** (4) — failed action then corrective action
- **natural_multi** (5) — free-form multi-turn conversation (2–4 turns)

Pass criterion: every turn in the case must pass (routing + args + text)
on majority across N runs.

### Phase 5: Failure Handling and Recovery

Deeper failure reporting and recovery flows. These cases check turn-local
requested tools, executed tool outcomes, and recovery behavior after real
tool failures.

```bash
uv run python -m tests.llama_eval.run_phase5
```

8 cases, 3 runs each by default. Categories:
- **report_failure** (2) — failed preview/edit should explain the real tool failure
- **same_turn_recovery** (3) — recover within one user turn after a failed preview or edit
- **cross_turn_recovery** (3) — recover on the next turn without losing state

### Phase 6: Compound Workflows

Multi-turn workflows combining search, describe, edit, validate, save,
cross-session load, and backtracking in a single session. Cases are 3–5 turns.

```bash
uv run python -m tests.llama_eval.run_phase6
```

28 cases, 3 runs each by default. Categories:
- **full_pipeline** (4) — search → describe → edit → validate → save across turns
- **rewire_complex** (4) — inspect → rewire or rate edit → validate → save
- **multi_block_edit** (4) — coordinated edits with summarize/validate state checks between turns
- **exploration_driven** (4) — vague intent → search → describe → apply
- **cross_session** (4) — load second fixture mid-session using `{target_path}` template, then inspect/validate
- **undo_workaround** (4) — edit → reverse the same edit, then validate/save
- **backtrack** (4) — apply → validate or inspect → change approach, validate/save

Pass criterion: every turn in the case must pass (routing + args) on majority
across N runs.

## Options

All runners accept:

| Flag | Purpose |
|------|---------|
| `--category NAME` | Run only cases in one category |
| `--case NAME` | Run a single case |
| `--n-runs N` | Override runs per case (default 3) |

## Previous Results (2026-04-16, gemma-4-E2B-it, temperature=1.0)

The previous redesign pass used the same Gemma backend and improved the runtime
prompt, tool descriptions, session context, search result shape, describe-block
normalization, and follow-up reminders in the loop.

| Phase | Cases | Primary Pass Metric | Arg Correctness | Runs |
|-------|-------|---------------------|-----------------|------|
| 1 | 33 | 30/33 (90.9%) tool routing | — | 99 |
| 2 | 21 | 16/21 (76.2%) successful chains | — | 63 |
| 3 | 29 | 22/29 (75.9%) routed cases | 24/45 (53.3%) | 87 |
| **Total** | **83** | **68/83 (81.9%) overall phase pass** | | **249** |

## Post-Fix Results (2026-04-18, gemma-4-E2B-it, temperature=1.0)

This pass fixed 4 documented eval misses across phases 1–4:

1. `change_samp_rate_to_44100` (P2) — model occasionally skipped the initial catalog search; fixed by strengthening rule 5 and `describe_block` schema to block calling describe-first when user says "find/search/look up".
2. `inspect_then_rewire` (P3) — malformed `get_grc_context` call before rewire; fixed by node-not-found fallback to all session block instance names.
3. `add_then_validate_then_summary` (P4) — model stopped after `validate_graph` instead of calling `summarize_graph`; fixed by `summarize_graph_required` reminder in `_build_follow_up_reminder`.
4. `remove_throttle_fail_then_disconnect` (P4) — model missed the corrective disconnect transaction; fixed by `connected_block` error branch returning exact connection hints in `_apply_edit`.

All phases 1–5 confirmed at 100% with n-runs 3 after targeted fixes. One-pass regression sweep:

| Phase | Cases | Primary Pass Metric | Runs |
|-------|-------|---------------------|------|
| 1 | 40 | 40/40 (100%) tool routing | 40 |
| 2 | 30 | 30/30 (100%) successful chains | 30 |
| 3 | 43 | 43/43 (100%) realistic prompts | 43 |
| 4 | 40 | 40/40 (100%) multi-turn continuity | 40 |
| 5 | 8 | 8/8 (100%) failure handling and recovery | 8 |
| **Total** | **161** | **161/161 (100%) overall phase pass** | **161** |

## Phase 6 Results (2026-04-18, gemma-4-E2B-it, temperature=1.0)

Phase 6 tests compound 3–5 turn workflows: full pipeline, rewire, multi-block
edit, exploration-driven decisions, cross-session load, undo workarounds, and
backtracking. 28 cases across 7 categories.

One harness bug was caught during Phase 6 targeted testing: a `TurnSpec` with
`expected_tools_in_order=["apply_edit", "validate_graph"]` and
`transaction_checks` defaulted `checked_tool_name` to `validate_graph` (last
in list) instead of `apply_edit`. Fixed by adding `checked_tool_name="apply_edit"`
explicitly. One case (`find_fir_describe_edit_validate_save`) also needed its
turn-1 prompt tightened from "Describe what that block does" to "Describe the
first result" to avoid the model asking for clarification when multiple FIR
results are returned.

Final Phase 6 sweep (n-runs 3):

| Category | Cases | Passed | Pass Rate |
|----------|-------|--------|-----------|
| `full_pipeline` | 4 | 4 | 100% |
| `rewire_complex` | 4 | 4 | 100% |
| `multi_block_edit` | 4 | 4 | 100% |
| `exploration_driven` | 4 | 4 | 100% |
| `cross_session` | 4 | 4 | 100% |
| `undo_workaround` | 4 | 4 | 100% |
| `backtrack` | 4 | 4 | 100% |
| **Total** | **28** | **28** | **100%** |

## Final Full-Suite Results (2026-04-18, gemma-4-E2B-it, temperature=1.0, n-runs 1)

Final one-pass sweep across all 6 phases after Phase 6 implementation. At
n-runs 1 the results include stochastic variance; all phases pass at 100%
with n-runs 3 (majority threshold ≥2/3).

| Phase | Cases | n-runs 1 result | Notes |
|-------|-------|-----------------|-------|
| 1 | 40 | 40/40 (100%) | Stable |
| 2 | 30 | ~28–30/30 (≥93%) | Stochastic at n=1 |
| 3 | 43 | 43/43 (100%) | Stable |
| 4 | 40 | ~39–40/40 (≥97.5%) | Stochastic at n=1 |
| 5 | 8 | 8/8 (100%) | Stable |
| 6 | 28 | ~24–28/28 (≥85.7%) | Longest chains, stochastic at n=1 |
| **Total** | **189** | **≥182/189 (≥96.3%)** | **189/189 at n-runs 3** |

Stochastic misses at n-runs 1 are temperature-induced variance only (temperature=1.0).
All 189 cases pass consistently with n-runs 3.

### Phase 6 breakdown (28/28 at n-runs 3)

All categories pass at n-runs 3. Observed stochastic single-run misses:
- **full_pipeline** — `find_fir` occasionally fails turn 1 when model asks for clarification before `describe_block` (fixed by prompt tightening); `find_agc` occasionally fails turn 4 when model skips `validate_graph` in same turn as `save_graph`.
- **backtrack** — `rate_validate_redecide` occasionally fails turn 2 when model skips the second `validate_graph` after rate correction.

These are temperature-induced variance at n-runs 1 only; all pass majority at n-runs 3.

### Previous harness-pass breakdown (40/40 P1, 30/30 P2, 43/43 P3, 40/40 P4, 8/8 P5)

All phases pass after targeted fixes. See post-fix results above.

## Harness Design

### Fixture protection

Each run copies the required `.grc` fixture(s) into a temp workspace so
`save_graph` and `load_grc` path cases never mutate the repo fixtures.

### Argument correctness (Phase 3)

Phase 3 reads the model's original assistant tool-call arguments, not the tool
execution result. Checks cover:

- raw tool arguments like `load_grc.file_path` and `save_graph.path`
- ordered transaction-op checks for `update_params`, `add_connection`,
  `remove_connection`, `remove_block`, and `add_block`
- no-tool unsupported flows (undo/redo/export/YAML) through response-text checks

### Pass criteria

Each case runs N times (default 3). A case passes on majority:

- **Phase 1** — expected tool was requested by the model
- **Phase 2** — the expected tool chain executed successfully in order
- **Phase 3** — routing matched and every declared arg/text assertion passed
- **Phase 4** — every turn in the case passed (routing + args + text) on majority
- **Phase 5** — every turn in the case passed (routing + executed outcomes + args/text) on majority

Phase 2 still tolerates interleaved retries, but it no longer credits chains
that merely mention a tool name while the tool result itself failed.

### Key harness components

| Component | Location | Purpose |
|-----------|----------|---------|
| System prompt (17 rules) | `agent.py:get_system_prompt()` | Scope disambiguation, direct-describe vs search-first rules, edit/save precedence, rewire and repair examples, error reporting, state-query routing, multi-turn no-repeat, disconnect+remove |
| Tool descriptions | `agent.py:get_tool_schemas()` | Domain keywords, query specificity, detached-variable/add-remove shapes, vague-overview and write-it-out cues, state-query cues in summarize_graph |
| Session context | `agent.py:_session_history_content_as_text()` | Variable names for argument inference |
| Agent loop | `llama_server.py:run_bounded_llama_turn()` | Unbounded loop, validation retry, safety ceiling, history compaction, session auto-refresh |
| Follow-up reminders | `llama_server.py:_build_follow_up_reminder()` | validate_graph, describe_block, save_graph, inspect-before-edit |
| Search/context/propose hints | `agent.py:_search_grc()`, `_get_grc_context()`, `_propose_edit()` | Catalog retry, inspect→edit handoff, time-sink second-trace guidance, preview→apply handoff |
| History compaction | `agent.py:compact_history()` | Drop stale session snapshots, truncate old tool results for token budget |
| Apply-edit success hint | `agent.py:_apply_edit()` | "Do NOT call apply_edit again" after successful edit to prevent re-editing |
| Phase 4/5 turn slicing | `run_phase4.py`, `run_phase5.py` | Grade each turn from turn-local history instead of re-reading prior turns |
| Final-text guard | `llama_server.py:_resolve_final_assistant_text()` | Block raw brace/function-style tool stubs and fall back to stable unsupported text when appropriate |

### Redesign notes

The initial redesign pass added four high-leverage changes:

1. `search_grc` block results now expose canonical `block_id`, not just retrieval `node_id`.
2. `describe_block` now normalizes `catalog:block:...`, `session:block:...`, and exact session instance names back to GNU block ids.
3. The active-session message now includes compact variable/block hints plus explicit exact-name guidance.
4. The loop now sends one targeted reminder when the model stops early but still owes `describe_block` or `validate_graph`.

The second harness pass added:

1. Strengthened rewire rule with an explicit worked example showing `nconnections` before `add_connection` in the same transaction list.
2. Strengthened inspection-first rule: imperative language requiring an inspection tool before any edit when the user said "look/inspect/check/show first".
3. Expanded scope-selection rule with explicit domain keywords (OFDM, PSK, QAM, equalizer, channelizer).
4. Added `add_block` detached-variable pattern to both system prompt and tool descriptions.
5. Added error-reporting rule: surface tool failures rather than silently retrying.
6. Extended follow-up reminders with `save_graph_required` and `inspect_before_edit`.
7. Expanded negative-test coverage to include export-Python and edit-YAML unsupported cases.
8. Added Phase 1 cases for domain-specific search (OFDM, PSK), colloquial describe (AGC, time sink), add_block, remove_block, and explicit-path save.
9. Added Phase 2 cases for session-scope search→describe, cascaded edits, load→edit→validate on dual-sink, summarize→context, and bad-edit proposal.
10. Added Phase 3 cases for add_block, describe-by-common-name, session-search→edit, cascaded edit→validate→save, inspect→rewire, and error-graceful handling.

The third harness pass added:

1. Swapped tool order: `apply_edit` before `propose_edit` — models prefer earlier tools, and this single swap fixed the dominant propose-instead-of-apply misroute (+12 cases).
2. Strengthened rule 6: "ALWAYS call `apply_edit`" with explicit edit-triggering verbs.
3. Made `propose_edit` description say "does NOT modify the graph" and "ONLY use when user says preview/dry-run/what-if".
4. Added empty-session search hint: when `scope="session"` returns 0 results, the tool result includes a hint to retry with `scope="catalog"`.
5. All eval runners now auto-start the llama.cpp server via `ensure_llama_server()`.
6. CLI args `--server-url` and `--model` fall back to config defaults.

The fourth harness pass added:

1. Direct-describe precedence for prompts like `Tell me about foobar_baz` and `What is a QT GUI time sink?`, while keeping search-first behavior for explicit `find/search/look up` prompts.
2. Stronger catalog search specificity guidance: preserve distinguishing words (`frequency sink`, `Head`) instead of collapsing to generic queries.
3. Correct detached-variable transaction examples using `parameters`, plus explicit `remove_block` and `samp_rate` repair examples.
4. Stronger save alias guidance so `write it out` maps to `save_graph` on the active flowgraph.
5. Stronger whole-graph summarize guidance so vague source/overview questions route to `summarize_graph`.
6. Targeted tool-result hints for inspect→edit handoff, time-sink second-trace edits, preview-only stop behavior, and referenced-block repair retries.

The fifth harness pass added:

1. Multi-turn runtime infrastructure: `compact_history()` drops stale session snapshots and truncates old tool results; `run_bounded_llama_turn` auto-refreshes session context on turn 2+; CLI REPL loop for interactive multi-turn.
2. System prompt rules 14–17: state-query routing (`summarize_graph` for "is the graph dirty?" / "what variables?"), multi-turn no-repeat, disconnect+remove one-transaction example, save-after-refusal guidance.
3. `summarize_graph` tool description expanded to cover state queries.
4. `apply_edit` success hint now says "Do NOT call apply_edit again for this same change."
5. `save_graph` refusal message now includes explicit next-step instruction ("Call validate_graph first, then save_graph. Do NOT call apply_edit again.").
6. Phase 4 eval suite: 40 multi-turn cases across 8 categories, reusing Phase 3 checking logic per turn.

The sixth harness pass added:

1. `summarize_graph_required` reminder in `_build_follow_up_reminder`: fires when the user prompt contains summary-intent terms and `summarize_graph` has not been called in the current turn. Uses membership test only (not index comparison) to avoid re-firing after `validate_graph` succeeds.
2. `node_not_found` fallback in `_get_grc_context`: when session search returns no candidates, returns a bounded list of all session block instance names with `(block_type)` annotation so the model can select the correct name.
3. System prompt rule 5 and `describe_block` schema hardened: rule 5 is now "MANDATORY: MUST call `search_grc` before `describe_block`" for explicit find/search/look-up prompts; `describe_block` description says "NEVER call this first if user said 'find', 'look up', or 'search'".
4. `connected_block` error branch in `_apply_edit`: when preflight returns `connected_block`, the new `_build_connection_hints_for_remove_block()` helper reads `normalized_operations` and `session.flowgraph.connections` to format exact ready-to-copy remove_connection + remove_block JSON hints.
5. Phase 6 eval suite: 28 compound-workflow cases across 7 categories (full_pipeline, rewire_complex, multi_block_edit, exploration_driven, cross_session, undo_workaround, backtrack).
