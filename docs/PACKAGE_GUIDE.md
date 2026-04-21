# Python Package Guide

Fast map of `src/grc_agent/` for someone trying to understand the harness
without reading the whole codebase.

## Mental model

- `FlowgraphSession` owns the live `.grc` graph
- `agent.py` exposes the narrow model-facing tool contract
- `llama_server.py` runs the chat/tool loop
- `llama_launcher.py` owns local llama.cpp cold-start/reuse
- package helpers (`catalog`, `retrieval`, `session`, `validation`, `transaction`)
  stay reusable outside the chat runtime

## End-to-end flow

```text
CLI chat
  -> load .grc into FlowgraphSession
  -> cheap retrieval readiness check
  -> create GrcAgent
  -> ensure llama.cpp server is ready
  -> run bounded tool loop
  -> execute routed tools against the session
  -> validate with grcc when required
```

## File map

| File | Why you open it |
|---|---|
| `config.py` | Runtime defaults and config resolution |
| `cli.py` | CLI entrypoints and startup order |
| `doctor.py` | Environment/readiness checks |
| `flowgraph_session.py` | Load, inspect, mutate, validate, save |
| `agent.py` | System prompt, tool schemas, runtime tool execution, session history |
| `llama_server.py` | Chat-completions loop, tool-order guards, follow-up reminders |
| `llama_launcher.py` | Local backend startup, readiness polling, reuse |
| `runtime_tool_validation.py` | Schema validation for model-returned tool calls |
| `catalog/describe.py` | `describe_block(...)` |
| `retrieval/search.py` | `search_grc(...)`, retrieval context, ranking |
| `session/` | Thin read-only wrappers over `FlowgraphSession` |
| `validation/preflight.py` | Pure staged validation |
| `transaction/apply.py` | Atomic apply-on-copy then commit |

## Public package surface

Import from `grc_agent` when possible:

- `GrcAgent`
- `FlowgraphSession`
- `initialize_retrieval`
- `describe_block`
- `search_grc`
- `load_grc`
- `summarize_graph`
- `get_grc_context`
- `preflight_transaction`
- `propose_edit`
- `apply_edit`

## Runtime tools

| Tool | Owner | Notes |
|---|---|---|
| `load_grc` | `agent.py` + `session/load.py` | Switches the active session |
| `summarize_graph` | `agent.py` + `session/summary.py` | Whole-graph, bounded summary |
| `search_grc` | `agent.py` + `retrieval/search.py` | Catalog or active-session search |
| `get_grc_context` | `agent.py` + `session/context.py` | Exact instance-name neighborhood |
| `describe_block` | `agent.py` + `catalog/describe.py` | GNU block truth |
| `apply_edit` | `agent.py` + `transaction/apply.py` | Default mutation path |
| `propose_edit` | `agent.py` + `transaction/planner.py` | Preview-only path |
| `validate_graph` | `agent.py` + `FlowgraphSession.validate()` | Runs `grcc` |
| `save_graph` | `agent.py` + `FlowgraphSession.save()` | Blocked until current dirty state is validated |

## Nuance that matters

### Retrieval

- Model-facing search passes explicit session/catalog context on every call.
- Direct package calls may use `bind_retrieval_context(...)`; that binding is
  **context-local**, not process-global.
- Session-scope retrieval reuses its index until `FlowgraphSession.state_revision`
  changes.

### Session state

- The active-session snapshot is explicit in tool results and model-visible history.
- Session-history rendering uses the recorded snapshot, not the current live session.
- History compaction keeps the latest session snapshot and trims older bulky tool payloads.

### Validation and save gating

- `preflight_transaction(...)` is in-memory only.
- `apply_edit(...)` mutates only after candidate validation succeeds.
- `save_graph(...)` refuses dirty, unvalidated state.

### Prompt / loop behavior

- Tool order matters: `apply_edit` must stay before `propose_edit`.
- `llama_server.py` owns follow-up reminders and out-of-order tool-call rejections.
- If a user asks for a repair/removal flow, the loop can require the missing edit
  before allowing `validate_graph` or `save_graph`.

## Where to look for tests

| Area | Tests |
|---|---|
| Runtime tool contract | `tests/test_agent.py`, `tests/test_runtime_tool_validation.py` |
| llama loop / reminders | `tests/test_llama_server.py` |
| launcher | `tests/test_llama_launcher.py`, `tests/test_llama_server_live.py` |
| retrieval | `tests/retrieval/` |
| session helpers | `tests/session/`, `tests/test_flowgraph_session.py` |
| transaction + validation | `tests/transaction/`, `tests/validation/` |
| live eval harness | `tests/llama_eval/` |

## Canonical fixture

Use `tests/data/random_bit_generator.grc` for examples, CLI smoke checks, and
live evals. It is the canonical example fixture in this repo.
