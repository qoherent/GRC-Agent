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

## Latest Results (2026-04-17, gemma-4-E2B-it, temperature=1.0)

The second harness pass expanded the suite from 83 to 113 cases and tuned the
system prompt, tool schemas, and follow-up reminders. All three phases now
auto-start the llama.cpp server.

| Phase | Cases | Primary Pass Metric | Arg Correctness | Runs |
|-------|-------|---------------------|-----------------|------|
| 1 | 40 | 38/40 (95.0%) tool routing | — | 120 |
| 2 | 30 | 26/30 (86.7%) successful chains | — | 90 |
| 3 | 43 | 36/43 (83.7%) routed cases | 44/75 (58.7%) | 129 |
| **Total** | **113** | **100/113 (88.5%) overall phase pass** | | **339** |

### Phase 1 breakdown (38/40)

All categories pass except:
- **context** (3/4) — `context_samp_rate` misroutes to search_grc instead of get_grc_context
- **edit** (7/8) — `edit_remove_block_explicit` returns no tools (model treats block removal as unsupported)

### Phase 2 breakdown (26/30)

Strong categories: search_describe (8/8), search_session_describe (2/2), inspect_edit (2/2),
summarize_context (1/1), inspect_edit_validate (1/1), load_edit_validate (1/1), edit_save (1/1),
load_summarize_validate (1/1).

Weak categories:
- **search_describe_edit** (1/3) — head_block and 44100 chains fail (head misroutes, 44100 skips describe)
- **search_describe_add_variable** (0/1) — add_block transaction shape is still weak
- **propose_apply_validate** (0/2) — bad_removal calls validate_graph after propose_edit (unexpected extra step)

### Phase 3 breakdown (36/43)

Strong: add_block (2/2), describe (1/1), domain (3/3), inspect (4/4), load (3/3),
natural (3/3), negative (4/4), save (1/1), session (1/1), rate (3/3), goal (3/3), edge (2/2), error (1/1).

Weak:
- **repair** (0/2) — multi-op repair transactions remain the weakest class
- **multi** (2/5) — cascaded chains still lose steps or get wrong args
- **rewire** (2/5) — second-trace arg construction is uneven

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

Phase 2 still tolerates interleaved retries, but it no longer credits chains
that merely mention a tool name while the tool result itself failed.

### Key harness components

| Component | Location | Purpose |
|-----------|----------|---------|
| System prompt | `agent.py:get_system_prompt()` | Scope disambiguation, tool rules, rewire examples, abbreviation expansion, add_block pattern, error reporting |
| Tool descriptions | `agent.py:get_tool_schemas()` | Domain keywords, colloquial coverage, add_block examples, explicit path guidance |
| Session context | `agent.py:_session_history_content_as_text()` | Variable names for argument inference |
| Agent loop | `llama_server.py:run_bounded_llama_turn()` | Unbounded loop, validation retry, safety ceiling |
| Follow-up reminders | `llama_server.py:_build_follow_up_reminder()` | validate_graph, describe_block, save_graph, inspect-before-edit |
| Propose hint | `agent.py:_propose_edit()` | Result hint guides model to apply_edit |

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
