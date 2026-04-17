# Project Blueprint

## Purpose

GRC Agent is a local-first assistant for GNU Radio Companion `.grc` flowgraphs.
It inspects, explains, modifies, validates, and drives safe graph changes
without letting the model edit raw YAML directly.

## Architecture

```
.grc YAML  →  FlowgraphSession  →  GrcAgent (tool registry)  →  llama.cpp adapter
                    ↑                        ↑
            models.py (IR)          catalog / retrieval / validation / transaction
```

### Layers

| Layer | Owner | Purpose |
|-------|-------|---------|
| Raw `.grc` | YAML on disk | Persistence format; never edited by model |
| Session | `flowgraph_session.py`, `models.py` | Load, summarize, save, validate, mutate |
| Catalog | `catalog/` | Block descriptions from installed GNU metadata |
| Retrieval | `retrieval/` | Search over GNU catalog and active session |
| Validation | `validation/` | Preflight staged checks (pure in-memory) |
| Transaction | `transaction/` | Propose/apply with copy-validate-commit |
| Runtime | `agent.py` | Tool registry, history, system prompt |
| CLI | `cli.py` | `fake`, `chat`, `tool` entry points |
| Adapter | `llama_server.py`, `llama_launcher.py` | HTTP to llama.cpp, auto-start, readiness |
| Config | `config.py`, `grc_agent.toml` | Defaults + optional overrides |

### Model-facing tool contract

Nine tools, in schema order:

1. `load_grc(file_path)` — switch session
2. `summarize_graph()` — bounded graph overview
3. `search_grc(query, scope, k)` — catalog or session search
4. `get_grc_context(node_id, hops, max_nodes)` — block neighborhood
5. `describe_block(block_id)` — full block truth from catalog
6. `apply_edit(transaction)` — **default edit tool** for all changes
7. `propose_edit(transaction)` — preview only, does NOT modify graph
8. `validate_graph()` — compile-check with `grcc`
9. `save_graph(path)` — write to disk (gated by validation)

Tool order matters: `apply_edit` must appear before `propose_edit` because
models prefer earlier tools. The session layer is broader than the runtime
contract deliberately.

## Canonical Working Rules

- Raw `.grc` YAML is never edited by the model directly.
- All mutations go through `FlowgraphSession`.
- `save_graph` is rejected until the latest dirty state passes validation.
- `propose_edit` does not modify the graph; `apply_edit` does.
- Structural APIs widen only when experiments justify change.

## Session Semantics

- `save()` writes in-memory raw YAML to disk.
- `validate()` writes to a temp `.grc` and runs `grcc`.
- `set_param`, `connect`, `disconnect`, `remove_block`, `add_block` update
  both parsed model and raw YAML.
- Structural adds use copy-validate-commit: failed validation leaves live
  session unchanged.

## Retrieval Semantics

- `search_grc(query, scope="catalog|session", k=5)` supports two scopes.
- Catalog indexes system GNU metadata only. Session indexes the active graph.
- Session-scope results include canonical `block_id` for `describe_block`.
- Empty session results include a hint to retry with catalog scope.
- Index is reused until the session revision changes.

## Validation Semantics

- `preflight_transaction(session, operations)` — pure in-memory staged check.
- Supports: `update_params`, `add_connection`, `remove_connection`,
  `remove_block`, detached-`variable` `add_block`.
- Ordered transactions can repair later preconditions on the staged snapshot.
- `grcc` remains the final semantic authority.

## Transaction Semantics

- `apply_edit(session, transaction)` — copy, apply, validate with `grcc`,
  swap only on success. Rollback is snapshot-based.
- `propose_edit(session, transaction)` — wraps preflight, returns
  `commit_eligible=False`. Does not touch the live session.
- Net-zero transactions may still advance revision.

## Structural-Edit Boundary

### Settled rules

- `remove_block(...)` — detached, unreferenced blocks only.
- `connect/disconnect` — permissive staged edits; callers validate.
- `add_block(...)` — detached `variable` blocks only.
- Three bespoke add-and-connect helpers exist for sink, transform, and source workflows.
- Broader fresh-sink and throttle-inclusive source workflows stay unsupported.

### Evidence tables

These `grcc` probe tables anchor the current contract. Each row is a real
validation result on this machine.

**Early structural constraints**

| ID | Mutation | `grcc` | Derived rule |
|----|----------|--------|-------------|
| A | Remove throttle + leave connections | Fail | Dangling connections invalid |
| C | Remove `samp_rate` | Fail | Variable references still break graph |
| D | Add detached `blocks_throttle2_1` | Fail | Detached stream blocks invalid |
| H | Add connection from `missing_block` | Fail* | `grcc` exit 0 but error output present |
| K | Add detached `variable` | Pass | Zero-port variables valid unattached |

**Variable add probes**

| ID | Mutation | `grcc` | Derived rule |
|----|----------|--------|-------------|
| AB1 | Add detached `variable` with full payload | Pass | Safe first target |
| AB2 | Add detached `blocks_char_to_float` | Fail | Stream transforms invalid detached |
| AB7 | Add `variable` with undefined expression | Fail | Expressions must be semantically valid |
| AB8 | Add `variable` with minimal `states` | Pass | Minimal `states` sufficient |
| AB9 | Add second detached `variable` | Pass | Contract is repeatable |

**Stream add-plus-connect probes**

| ID | Mutation | `grcc` | Derived rule |
|----|----------|--------|-------------|
| SW1 | Add copied sink + wire required input | Pass | Atomic add-plus-connect safe |
| SW5 | Add copied transform + expand sink `nconnections` | Pass | Coordinated tap needs simultaneous mutation |
| BX5 | Transform into expanded sink port 1 | Pass | Narrow coordinated tap valid |
| BX11 | Source + transform into expanded sink port 1 | Pass | Smallest source workflow |
| SX2 | Smallest source workflow confirmed | Pass | `analog_random_source_x -> blocks_char_to_float -> qtgui_time_sink_x(port 1)` |

**Removal policy probes**

| ID | Mutation | `grcc` | Derived rule |
|----|----------|--------|-------------|
| RM1 | Remove throttle + leave connections | Fail | Cannot leave dangling connections |
| RM10 | Remove `samp_rate` + patch all references | Pass | Variable removal needs case-specific repair |
| RM14 | Remove sink + add fresh sink + rewire | Pass | Replacement requires adding new block |

**Connection policy probes**

| ID | Mutation | `grcc` | Derived rule |
|----|----------|--------|-------------|
| CP3 | Add second connection to occupied port | Fail | Stream inputs cannot have multiple upstream |
| CP9 | Disconnect then reconnect same edge | Pass | Valid final state reachable through invalid intermediate |
| CP10 | Add type-mismatched connection | Fail | Type mismatches caught by validation |

## Current Verified State

### Environment

- `uv run ruff check` — lint gate
- `uv run python -m unittest` — regression gate (279 tests)
- `uv run grc-agent fake tests/data/random_bit_generator.grc` — runtime smoke test
- GNU Radio 3.10.9.2 on this machine, catalog at `/usr/share/gnuradio/grc/blocks` (564 block files)
- `grcc` is the validation authority

### Key verified behaviors

- `summarize_graph` returns bounded summary with block_count, connection_count, variable_count
- `get_grc_context("blocks_throttle2_0")` returns 3-node/2-edge neighborhood
- `describe_block("blocks_throttle2")` returns full block truth from real GNU metadata
- `search_grc("throttle", scope="catalog")` returns block-centric results with `block_id`
- `search_grc("equalizer", scope="session")` returns 0 results with catalog-retry hint
- `apply_edit(update_params, samp_rate, 48000)` → `ok=True`, `validation.status='valid'`
- `apply_edit(add_connection with nconnections expansion)` → second-trace rewire passes
- `apply_edit(staged repair: patch throttle+sink then remove samp_rate)` → passes
- `apply_edit(add_block, variable, debug_flag, 0)` → detached variable add passes
- Session-scope search index reuse: second call ~60x faster than first
- Launcher auto-start/reuse: cold start and warm reuse both verified through CLI `chat`

### Live model eval

The eval suite (`tests/llama_eval/`) covers 113 cases across three phases.
Runners auto-start the llama.cpp server. See [docs/LLAMA_EVAL.md](LLAMA_EVAL.md).

Latest results (2026-04-17, gemma-4-E2B-it, temperature=1.0):

| Phase | Cases | Pass |
|-------|-------|------|
| 1 — Tool routing | 40 | 38/40 (95.0%) |
| 2 — Multi-step chains | 30 | 26/30 (86.7%) |
| 3 — Realistic prompts | 43 | 36/43 (83.7%) |
| **Total** | **113** | **100/113 (88.5%)** |

Remaining weaknesses: multi-op repair transactions (0/2), cascaded chains (2/5),
second-trace rewire arg construction (2/5), add_block transaction shape (0/1).

### Harness design

| Component | Location | Purpose |
|-----------|----------|---------|
| System prompt (13 rules) | `agent.py:get_system_prompt()` | Scope selection, edit precedence, rewire examples, abbreviation expansion |
| Tool schemas | `agent.py:get_tool_schemas()` | `apply_edit` before `propose_edit`; domain keywords; add_block examples |
| Session context | `agent.py:_session_history_content_as_text()` | Variable names for argument inference |
| Agent loop | `llama_server.py:run_bounded_llama_turn()` | Unbounded loop, validation retry, safety ceiling (50 rounds) |
| Follow-up reminders | `llama_server.py:_build_follow_up_reminder()` | validate_graph, describe_block, save_graph, inspect-before-edit |
| Search hints | `agent.py:_search_grc()` | Catalog-retry hint on empty session results |
| Propose hint | `agent.py:_propose_edit()` | Guides model toward `apply_edit` |

### Key harness decisions

1. **Tool order matters**: `apply_edit` before `propose_edit` in the schema list.
   Models prefer earlier tools. This single swap fixed 12 failing cases.
2. **Session search fallback**: When session returns 0 results, the hint suggests
   catalog scope. The model self-corrects on the next tool call.
3. **Follow-up reminders**: One targeted reminder per unfinished requirement.
   Avoids nagging; each reminder fires at most once per turn.
4. **Arg correctness is strict**: Phase 3 checks raw model arguments, not tool
   results. Transaction ops must match in order.

## Milestone History

| Phase | Summary |
|-------|---------|
| 0–3 | Package layout, `FlowgraphSession`, fixture, load/summarize/save/validate |
| 4–6 | `set_param`, `connect`, `disconnect`, persistence through mutate→save→reload |
| 7–12 | `remove_block`, `add_block` (variable), three bespoke stream workflows, copy-validate-commit |
| 13–14 | Structural boundary stabilization, removal/connection policy settled |
| 15–16 | `GrcAgent` thin runtime, `--fake` path, blueprint consolidation |
| 17–18 | Retrieval index (`search_grc`), catalog (`describe_block`), GNU metadata integration |
| 19–20 | Preflight validation (`preflight_transaction`), transaction editing (`propose_edit`, `apply_edit`) |
| 21 | Agent routing to session/retrieval/catalog/transaction, CLI `fake`/`chat`/`tool` modes |
| 22 | CLI-owned llama.cpp startup (`LlamaServerLauncher`), alias verification, state reuse |
| 23 | Runtime guardrails: schema validation, tool-call rejection, session-context messages |
| 24 | Unbounded agent loop, eval suite (3 phases, 83 cases), validation-error retry |
| 25 | Harness tuning: tool order swap, scope hints, expanded suite (113 cases), 88.5% overall pass |

## Backlog

1. Improve multi-op repair transaction correctness (currently 0/2).
2. Improve cascaded chain arg correctness (multi 2/5, rewire 2/5).
3. Add a one-session interactive CLI conversation loop over the current runtime.
4. Decide multi-turn session persistence (one-command vs. persistent session).
5. Revisit structural API growth only if new use cases justify it.

## Intentionally Deferred

- Direct raw YAML edits by the model
- Automatic connected-block removal
- Immediate validation on every `connect()`/`disconnect()`
- Generic multi-block structural builders
- Broader fresh-sink / throttle-inclusive source workflows
- Heavy orchestration frameworks
- Multi-flowgraph session management

## Related Files

- [README.md](../README.md)
- [PACKAGE_GUIDE.md](PACKAGE_GUIDE.md)
- [LLAMA_EVAL.md](LLAMA_EVAL.md)
