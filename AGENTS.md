# AGENTS.md

Rules for AI coding agents working on this codebase. Direct, data-driven, zero fluff.

## Architecture at a glance

The bridge: `grc_native_adapter.py` is the **only** module that imports `gnuradio`. Everything else is pure Python over the Pydantic V2 surface it exposes.

| Module | Role |
|--------|------|
| `grc_native_adapter.py` | All GRC native API calls. Lazy singleton `get_platform()`. |
| `domain_models.py` | Pydantic V2 schemas (`BlockRole` is a `StrEnum`). Outbound `extra="forbid"`, inbound `extra="ignore"`. |
| `flowgraph_session.py` | Owns path, integrity, atomic save, revision. `flowgraph` is a live `gnuradio.grc.core.FlowGraph`. |
| `session.py` | `load_grc` (file → session) + `summarize_graph` (session → dict). |
| `runtime/param_filter.py` | **The Bible** — single source of truth for parameter filtering (Stage A + Stage B). |
| `runtime/inspect_graph.py` | MVP `inspect_graph` + `query_knowledge` wrapper (routes to catalog/docs). |
| `runtime/change_graph.py` | MVP `change_graph` engine — flat batch mutations via the native GRC adapter. |
| `runtime/catalog_vector.py` | sqlite-vec + embeddinggemma index for the GNU Radio catalog. |
| `runtime/doc_answer.py` | sqlite-vec + embeddinggemma RAG for GNU Radio docs wiki. |
| `runtime/search_blocks.py` | Vector search over the catalog (`BlockDescription` payload, Stage A filtered). |
| `runtime/model_context.py` | `render_model_messages` + MVP `ToolSurface` (5-tool profile). |
| `runtime/tool_schemas.py` | MVP tool JSON schemas (5 tools). |
| `runtime/clarification.py` | `normalize_pending_clarification` + `resolve_pending_clarification_state`. |
| `runtime/connection_ids.py` | `connection_id` (build) + `parse_connection_id` (parse). |
| `agent.py` | MVP `GrcAgent`: tool registry, dispatch, lifecycle, history journal. |
| `transaction.py` | `capture_session_state` / `restore_session_state` for `change_graph` rollback. |

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

Five model-facing wrapper tools (the entire MVP model surface):

| Tool | Direction | Engine |
|------|-----------|--------|
| `inspect_graph` | read | `grc_native_adapter.render_flow_graph()` → `GrcFlowgraph` (Stage A + B filtered) |
| `query_knowledge` | read | `runtime/search_blocks.search_blocks()` (catalog) **or** `runtime/doc_answer.ask_grc_docs()` (docs RAG) |
| `web_search` | read | `runtime/web_search.web_search()` — Ollama web search API |
| `web_fetch` | read | `runtime/web_search.web_fetch()` — Ollama web fetch API |
| `change_graph` | write | `runtime/change_graph.dispatch_flat_change_graph_batch()` + `grc_native_adapter.apply_mutation()` |

`search_blocks` and `ask_grc_docs` are internal engines under `query_knowledge`, not separately surfaced to the model. Both go through the same `embeddinggemma:latest` + sqlite-vec pipeline.

- Tool schema and system-prompt tuning are permitted for general fixes and clarifying system boundary constraints. Do not implement ad-hoc or hardcoded prompt/schema rules targeting specific test scenarios or individual block instances.
- No new model-facing tool or schema field changes without maintainer authorization.
- Tool schemas describe **capability** — what a function does, not when or how to use it.
- **No in-band control flow:** no ALL-CAPS directives, behavioral commands, or procedural recipes in model-visible strings. The system prompt is the only behavioral authority.

## Runtime & state

- **Manual execution loop:** `ToolAgentsRunner._run_turn_events` with bounded `.step()`.
- **No result caching.** Every call hits the live backend fresh.
- **Context window:** `num_ctx=120000` (model native 131072). Configured in `ToolAgentsLlamaProviderConfig`.
- **Context compaction:** one-pass proportional slicing with truncation flags.
- **Wire-format role safety:** runtime directives injected as `user`-role only.
- **`change_graph` output is minimal.** Success: `{"ok": true}`. Failure: `{"ok": false, "error_type": "...", "errors": [{"code": "...", "message": "..."}]}`. Validation errors surface as `errors[].code == "gnu_validation"`. The `force=True` flag bypasses validation but the batch is still applied; the model must read `ok` to know whether edits applied.

## Constraints (hard prohibitions)

- **No daemon management.** Never manage OS services/daemons.
- **No hardware polling.** No `psutil`, `nvidia-smi`, or telemetry.
- **Non-blocking flow.** Launch into degraded mode if backend unreachable; never `sys.exit()`.
- **No result caching outside the transaction history.**
- **No application-flow changes without permission.**
- **No `gnuradio` imports outside `grc_native_adapter.py`** and auxiliary files (doctor, dogfood, session catalog paths).

## Key conventions

- **Param filtering** (one rule, in `param_filter.py`): Stage A (every mode) drops `hide == "all"`, `category ∈ {Advanced, Config}`, `dtype == "gui_hint"`. Stage B (overview mode only) keeps `hide == "none"` OR `dtype == "enum"` OR `value != default` OR `references_variable`. Details mode = Stage A only; overview mode = Stage A + Stage B. Do not reimplement filtering inline.
- **State values:** `enabled`, `disabled`, `bypass` (accept `bypassed` as alias). Use `Block.STATE_LABELS` for validation, not a hardcoded set.
- **Block lookup:** use native `flow_graph.get_block(name)`, not a manual scan.
- **Graph identity:** file-bytes SHA-256 (cross-session) + `state_revision` counter (in-session). No deep-JSON hashing.
- **Atomic save:** temp file → fsync → `os.replace()` → directory fsync. Lock via `fcntl.flock` on `.grc_agent/<name>.lock`. Backup saved before each save.
- **Tool surface:** `agent.py` only registers the 5 MVP tools. No internal tools, no legacy tool registry.
- **`change_graph` output:** `{"ok": true}` on success; `{"ok": false, "error_type": "...", "errors": [...]}` on failure. No `committed`, `ops_applied`, `state_revision`, `validation`, `hint`, `rejected_phase`, `graph_unchanged`, `native_validation_errors`, or `rollback` fields.

## Test gate

| Marker | Command |
|--------|---------|
| default | `pytest -m "not grc_native and not gui and not llama_eval"` (328 passed, 6 skipped) |
| `grc_native` | `pytest -m grc_native` (30 passed, 1 skipped; requires GNU Radio) |
| `gui` | `xvfb-run pytest -m gui` (6 passed) |

Default CI command: `pytest -m "not grc_native and not gui and not llama_eval"`. The `docs/MODEL_CONTEXT_BIBLE.md` staleness guard (`tests/test_model_context_bible.py`) runs in this default gate.
