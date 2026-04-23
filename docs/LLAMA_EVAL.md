# Llama.cpp Eval Suite

Live regression coverage for the **documented single-session local CLI path**.
The runners use the real routed runtime, not mocked tool execution.

## What this suite is for

- Catch tool-routing regressions
- Catch argument/transaction-shape regressions
- Catch multi-turn continuity and recovery misses
- Prove the supported llama.cpp path against real `.grc` fixtures

## What this suite does **not** prove

- concurrent-session isolation under load
- large-graph scaling
- behavior across other model/backend combinations
- broad “production ready for everything” claims

## Setup

The runners auto-start the local llama.cpp server through
`LlamaServerLauncher`. No manual server start is required for normal use.

`run_all.py` now force-restarts the backend before each phase so the full sweep
does not depend on a single long-lived llama.cpp process. Phase 1 also retries a
run once after a direct llama connection timeout by restarting the backend and
replaying the same case.

Run a single phase:

```bash
uv run python -m tests.llama_eval.run_phase1
```

Run the full sweep:

```bash
uv run python -m tests.llama_eval.run_all
```

While iterating on a fix, prefer a targeted phase or case first. Run the full
`run_all` sweep only after the focused checks are green.

## Phase summary

| Phase | Cases | Focus | Pass rule |
|---|---:|---|---|
| 1 | 40 | Single-tool routing | Expected tool was requested |
| 2 | 30 | Ordered multi-step chains | Expected chain executed successfully |
| 3 | 51 | Natural prompts + arg checks | Routing plus declared arg/text checks |
| 4 | 41 | Multi-turn continuity | Every turn passes on majority |
| 5 | 8 | Failure handling + recovery | Every turn passes on majority |
| 6 | 28 | Compound workflows | Every turn passes on majority |

### Coverage shape

- **Phase 1**: one-turn routing across all 9 tools
- **Phase 2**: search/describe/edit/validate/save chains
- **Phase 3**: vague user language, arg correctness, unsupported requests, symbolic-expression preservation
- **Phase 4**: same-session follow-ups, state awareness, repair flows, detached-variable state toggles
- **Phase 5**: failed previews/edits, same-turn recovery, cross-turn recovery
- **Phase 6**: full pipelines, backtracking, cross-session loads, undo workarounds

Each run copies the required fixture(s) into a temp workspace so `save_graph`
and `load_grc` cases never mutate repo fixtures.

## Common commands

| Command | Purpose |
|---|---|
| `uv run python -m tests.llama_eval.run_phaseN` | Run one full phase |
| `uv run python -m tests.llama_eval.run_phaseN --case NAME` | Run one named case |
| `uv run python -m tests.llama_eval.run_phaseN --category NAME` | Run one category |
| `uv run python -m tests.llama_eval.run_phaseN --quick` | Fast iteration (`n_runs=1`) |
| `uv run python -m tests.llama_eval.run_all` | Run all 6 phases |
| `uv run python -m tests.llama_eval.run_all --phases 2,4` | Run selected phases |

`run_all.py` now restarts the backend before every phase, and each phase still
does its normal readiness path inside the phase runner.

## Current verified results

Gemma backend: `unsloth/gemma-4-E2B-it-GGUF`, temperature `1.0`.
Request timeout: `120.0` seconds.

| Phase | Cases | Result |
|---|---:|---|
| 1 | 40 | 39/40 on the last uninterrupted full sweep; `save_direct` re-passed 3/3 after the final timeout-retry harness fix |
| 2 | 30 | 30/30 |
| 3 | 51 | 51/51 |
| 4 | 41 | 41/41 |
| 5 | 8 | 8/8 |
| 6 | 28 | 28/28 |
| **Total** | **198** | **197/198 on the last uninterrupted full sweep** |

### Evidence basis

- Latest successful code gates in this session:
	- `uv run ruff check`
	- `uv run python -m unittest`
- Last uninterrupted `uv run python -m tests.llama_eval.run_all` result in this session: **197/198**.
- The only miss in that sweep was late Phase 1 `save_direct`, caused by a direct llama connection timeout rather than a wrong tool route.
- After the final harness fix, the formerly failing targeted case reran cleanly: `uv run python -m tests.llama_eval.run_phase1 --case save_direct` -> **3/3**.
- Additional late-session targeted confirmations after runtime/harness fixes:
	- `remove_samp_rate_keep_valid` -> **3/3**
	- `preview_connected_block_reports_error` -> **3/3**
	- `edit_summarize_trace_validate` -> **3/3**

That is strong regression evidence for the supported harness contract. It is not a blanket production proof, and this document does not claim a post-fix uninterrupted 198/198 sweep because that was not completed before wrap-up.

## Harness components that matter

| Component | Location | Why it matters |
|---|---|---|
| System prompt | `src/grc_agent/agent.py` | Routing rules, edit precedence, repair guidance |
| Tool schemas | `src/grc_agent/agent.py` | Tool order and arg contract |
| Session context | `src/grc_agent/agent.py` | Model-visible active-session snapshot |
| Loop / reminders | `src/grc_agent/llama_server.py` | Retry behavior, order guards, follow-up enforcement, preview-only failure finalization, malformed transaction canonicalization |
| Launcher | `src/grc_agent/llama_launcher.py` | Cold start + reuse path |
| Eval harness | `tests/llama_eval/harness.py` | Live server setup, fixture isolation, grading helpers, forced restart helpers |

## Recommended usage

1. Reproduce with the smallest relevant phase or case.
2. Fix the real issue.
3. Rerun the affected phase(s) at full strength.
4. Finish with `uv run python -m tests.llama_eval.run_all`.
