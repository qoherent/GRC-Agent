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
| 3 | 43 | Natural prompts + arg checks | Routing plus declared arg/text checks |
| 4 | 40 | Multi-turn continuity | Every turn passes on majority |
| 5 | 8 | Failure handling + recovery | Every turn passes on majority |
| 6 | 28 | Compound workflows | Every turn passes on majority |

### Coverage shape

- **Phase 1**: one-turn routing across all 9 tools
- **Phase 2**: search/describe/edit/validate/save chains
- **Phase 3**: vague user language, arg correctness, unsupported requests
- **Phase 4**: same-session follow-ups, state awareness, repair flows
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

`run_all.py` ensures the server is up once before the sweep starts; each phase
still performs its normal readiness check and takes the fast reuse path when the
server is already healthy.

## Current verified results

Gemma backend: `unsloth/gemma-4-E2B-it-GGUF`, temperature `1.0`.

| Phase | Cases | Result |
|---|---:|---|
| 1 | 40 | 40/40 |
| 2 | 30 | 30/30 |
| 3 | 43 | 43/43 |
| 4 | 40 | 40/40 |
| 5 | 8 | 8/8 |
| 6 | 28 | 28/28 |
| **Total** | **189** | **189/189** |

### Evidence basis

- Phase 1 and Phase 2 were confirmed in a real `run_all` sweep.
- Phases 3, 4, 5, and 6 were then rerun at full strength on the audited build
  after the last harness fixes.
- The final evidence is therefore **full-strength phase coverage on the current
  build**, but not a fresh uninterrupted post-fix `run_all` sweep.

That is strong regression evidence for the supported harness contract. It is not
a blanket production proof.

## Harness components that matter

| Component | Location | Why it matters |
|---|---|---|
| System prompt | `src/grc_agent/agent.py` | Routing rules, edit precedence, repair guidance |
| Tool schemas | `src/grc_agent/agent.py` | Tool order and arg contract |
| Session context | `src/grc_agent/agent.py` | Model-visible active-session snapshot |
| Loop / reminders | `src/grc_agent/llama_server.py` | Retry behavior, order guards, follow-up enforcement |
| Launcher | `src/grc_agent/llama_launcher.py` | Cold start + reuse path |
| Eval harness | `tests/llama_eval/harness.py` | Live server setup, fixture isolation, grading helpers |

## Recommended usage

1. Reproduce with the smallest relevant phase or case.
2. Fix the real issue.
3. Rerun the affected phase(s) at full strength.
4. Finish with `uv run python -m tests.llama_eval.run_all`.
