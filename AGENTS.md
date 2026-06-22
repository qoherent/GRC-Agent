# AGENTS.md

Rules for AI coding agents working on this codebase. Direct, data-driven, zero fluff.

## Architecture at a glance

The bridge: `grc_native_adapter.py` is the **only** module that imports `gnuradio`. Everything else is pure Python over the Pydantic V2 surface it exposes.

| Module | Role |
|--------|------|
| `grc_native_adapter.py` | All GRC native API calls. Lazy singleton `get_platform()`. |
| `domain_models.py` | Pydantic V2 schemas. Outbound `extra="forbid"`, inbound `extra="ignore"`. |
| `flowgraph_session.py` | Owns path, integrity, atomic save, revision. `flowgraph` is a live `gnuradio.grc.core.FlowGraph`. |
| `runtime/param_filter.py` | **The Bible** — single source of truth for param visibility. |
| `runtime/inspect_graph.py` | Adapter-backed read tool. |
| `runtime/change_graph.py` | Flat batch mutation dispatch. Stale-revision gate, noop detection, autosave. |
| `agent.py` | Tool registry, dispatch, lifecycle. |
| `transaction.py` | Clone/commit via `export_data()`/`import_data()`. |

Data flow: `.grc file` → `grc_native_adapter.load_flow_graph()` → `FlowgraphSession.flowgraph` → `render_flow_graph()` → `GrcFlowgraph` Pydantic model → tool result.

## Engineering rules

- **No hand-picked heuristics.** No per-field allowlists, per-scenario branches, regex routing, or prompt folklore. If logic is needed, it is one uniform rule applied to every case. (Bible: `runtime/param_filter.py`.)
- **Prefer native methods.** Use GNU Radio GRC's Python API — `param.hide`, `param.category`, `Block.is_variable`, `flow_graph.is_valid()`, etc.
- **Fix at the source.** Correctness lives in the tool/handler that produces data, not in a post-processor.
- **No silent transformation.** Any truncation, filtering, or omission in model-facing output must be explicitly flagged (e.g. an `omitted`/`truncated` field on the payload).
- **Simplify by removal.** Prefer deleting code over adding it.
- **No backward compatibility.** No shims, dual-format persistence, or legacy synthesis layers. Delete old paths in the same commit that obsoletes them.
- **Evidence before assertions.** Every claim cites a verified observation, never intent. A green test is necessary, not sufficient — inspect actual data flow.

## Tool surface

Three model-facing tools, all backed by the adapter:

| Tool | Direction | Backed by |
|------|-----------|-----------|
| `inspect_graph` | read | `grc_native_adapter.render_flow_graph()` → `GrcFlowgraph` |
| `change_graph` | write | `grc_native_adapter.apply_mutation()` + `validate_and_finalize()` |
| `query_knowledge` | read | `search_blocks` (catalog) + `ask_grc_docs` (RAG) — no native API |

- No new model-facing tool, schema field, or system-prompt change without maintainer authorization.
- Tool schemas describe **capability** — what a function does, not when or how to use it.
- **No in-band control flow:** no ALL-CAPS directives, behavioral commands, or procedural recipes in model-visible strings. The system prompt is the only behavioral authority.

## Runtime & state

- **Manual execution loop:** `ToolAgentsRunner._run_turn_events` with bounded `.step()`.
- **No result caching.** Every call hits the live backend fresh.
- **Repeat-payload escalator:** `_last_failed_ops_hash` flags duplicate failing payloads.
- **Context compaction:** one-pass proportional slicing with truncation flags.
- **Wire-format role safety:** runtime directives injected as `user`-role only.

## Constraints (hard prohibitions)

- **No daemon management.** Never manage OS services/daemons.
- **No hardware polling.** No `psutil`, `nvidia-smi`, or telemetry.
- **Non-blocking flow.** Launch into degraded mode if backend unreachable; never `sys.exit()`.
- **No result caching outside the transaction history.**
- **No application-flow changes without permission.**
- **No `gnuradio` imports outside `grc_native_adapter.py`** and auxiliary files (doctor, dogfood, session catalog paths).

## Key conventions

- **Param visibility** (one rule, in `param_filter.py`): drop `hide == "all"`, `category ∈ {Advanced, Config}`, `dtype == "gui_hint"`. Keep: `enum` OR `value != default` OR `references_variable`.
- **State values:** `enabled`, `disabled`, `bypassed` (accept `bypass` as alias). Enforced at `_update_state_operation()` in `change_graph.py`.
- **Disconnect precision:** native `flow_graph.disconnect(src, dst)` removes ALL edges from source port. Adapter `disconnect()` finds the exact `Connection` object and drops from set.
- **Graph identity:** file-bytes SHA-256 (cross-session) + `state_revision` counter (in-session). No deep-JSON hashing.
- **Atomic save:** temp file → fsync → `os.replace()` → directory fsync. Lock via `fcntl.flock` on `.grc_agent/<name>.lock`. Backup saved before each save.

## Test gate

| Marker | Command |
|--------|---------|
| default | `pytest -m "not grc_native and not gui and not llama_eval"` (361 passed, 10 skipped) |
| `grc_native` | `pytest -m grc_native` (28 passed; requires GNU Radio) |
| `gui` | `xvfb-run pytest -m gui` (6 passed) |

**Total: 395 passing.** Default CI command: `pytest -m "not grc_native and not gui and not llama_eval"`.
