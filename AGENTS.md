# AGENTS.md

Rules for AI coding agents working on this codebase. Direct, data-driven, zero fluff.

## Architectural Vision & Core Rules

- **Simplify First**: Lean towards simplifying, not complicating. If a feature or approach is ad-hoc, hardcoded, or not essential, **remove it**.
- **No Brittle Reinventions**: Reject complex manual implementations or from-scratch logic when reliable, standard libraries can replace them. Always opt for robust libraries to avoid reinventing the wheel.
- **No Backward Compatibility**: Delete dead code completely. Do not write shims, dual-format persistence layers, or legacy bridges. Keep changes clean and direct.
- **No Assumed Reasoning Failures**: Do not assume task failures are solely due to LLM reasoning. Audit the execution harness for context flooding, poor prompt construction, hidden ad-hoc logic, or silent error message clipping. Correctness lives at the source.
- **Maximizing Context & No String-Based Clipping**: Do not enforce arbitrary 4k context limits. Always use the maximum context window the backend (Ollama/OpenRouter) supports. Never clip inputs or outputs (like error payloads or long tool results) using raw character slicing which breaks structured context.
- **Be Bold, Objective, and Grounded**: Base every decision on grounded, verified observations, never on assumptions. Ask for clarification when requirements are ambiguous or when a major decision (e.g. library selection, backend config) needs to be made.

---

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
| `runtime/catalog_vector.py` | sqlite-vec vector index for the GNU Radio catalog (per-backend embedder). |
| `runtime/doc_answer.py` | sqlite-vec RAG for GNU Radio docs wiki; owns the shared `get_embedding` (OpenAI-compat `/v1/embeddings`, both backends). |
| `runtime/search_blocks.py` | Vector search over the catalog (`BlockDescription` payload, Stage A filtered). |
| `runtime/model_context.py` | `render_model_messages` + MVP `ToolSurface` (5-tool profile). |
| `runtime/tool_schemas.py` | MVP tool JSON schemas (5 tools). |
| `runtime/connection_ids.py` | `connection_id` (build) + `parse_connection_id` (parse). |
| `agent.py` | MVP `GrcAgent`: tool registry, dispatch, lifecycle, history journal. |
| `transaction.py` | `capture_session_state` / `restore_session_state` for `change_graph` rollback. |

Data flow: `.grc file` → `grc_native_adapter.load_flow_graph()` → `FlowgraphSession.flowgraph` → `render_flow_graph()` → `GrcFlowgraph` Pydantic model → tool result.

---

## Engineering Rules

- **No hand-picked heuristics.** No per-field allowlists, per-scenario branches, regex routing, or prompt folklore. If logic is needed, it is one uniform rule applied to every case. (Bible: `runtime/param_filter.py`.)
- **Prefer native methods.** Use GNU Radio GRC's Python API — `param.hide`, `param.category`, `Block.is_variable`, `flow_graph.is_valid()`, etc.
- **Fix at the source.** Correctness lives in the tool/handler that produces data, not in a post-processor.
- **No silent transformation.** Any truncation, filtering, or omission in model-facing output must be explicitly flagged (e.g. an `omitted`/`truncated` field on the payload).
- **Simplify by removal.** Prefer deleting code over adding it.
- **Evidence before assertions.** Every claim cites a verified observation, never intent. A green test is necessary, not sufficient — inspect actual data flow.

---

## Tool Surface

Five model-facing wrapper tools (the entire MVP model surface):

| Tool | Direction | Engine |
|------|-----------|--------|
| `inspect_graph` | read | `grc_native_adapter.render_flow_graph()` → `GrcFlowgraph` (Stage A + B filtered) |
| `query_knowledge` | read | `runtime/search_blocks.search_blocks()` (catalog) **or** `runtime/doc_answer.ask_grc_docs()` (docs RAG) |
| `web_search` | read | `runtime/web_search.web_search()` — Ollama hosted web search (**Ollama backend only**) |
| `web_fetch` | read | `runtime/web_search.web_fetch()` — Ollama hosted web fetch (**Ollama backend only**) |
| `change_graph` | write | `runtime/change_graph.dispatch_flat_change_graph_batch()` + `grc_native_adapter.apply_mutation()` |

`search_blocks` and `ask_grc_docs` are internal engines under `query_knowledge`, not separately surfaced to the model. Both use the shared `get_embedding` (OpenAI-compat `/v1/embeddings` via the `openai` SDK — one code path for Ollama and OpenRouter) + sqlite-vec. Each backend owns its own per-backend vector DB pair (`catalog_<backend>.db` / `docs_<backend>.db`), sized by a probed dimension and rebuilt automatically when the stamped embedding model changes. Embedding model names live in `.env` (`OLLAMA_EMBEDDING_MODEL`, `OPENROUTER_EMBEDDING_MODEL`); embedding follows the chat backend.

**Web search is backend-split (no mixing):**
- **Ollama:** `web_search`/`web_fetch` are model-facing tools hitting Ollama's hosted REST API (`OLLAMA_API_KEY`-gated).
- **OpenRouter:** no standalone search REST API exists. Web grounding is a request-side plugin — `ToolAgentsLlamaProviderConfig.create_settings()` injects `extra_body["plugins"]=[{"id":"web",...}]` (on by default, env-controlled: `OPENROUTER_WEB_SEARCH`, `OPENROUTER_WEB_SEARCH_MAX_RESULTS`, `OPENROUTER_WEB_SEARCH_INCLUDE_DOMAINS`/`_EXCLUDE_DOMAINS`), and `GrcResponseConverter` surfaces the returned `url_citation` annotations as a `Sources:` footnote (both stream + non-stream). The Ollama web tools are dropped from the surfaced tool set on OpenRouter via `_OLLAMA_ONLY_TOOLS`.

- Tool schema and system-prompt tuning are permitted for general fixes and clarifying system boundary constraints. Do not implement ad-hoc or hardcoded prompt/schema rules targeting specific test scenarios or individual block instances.
- No new model-facing tool or schema field changes without maintainer authorization.
- Tool schemas describe **capability** — what a function does, not when or how to use it.
- **No in-band control flow:** no ALL-CAPS directives, behavioral commands, or procedural recipes in model-visible strings. The system prompt is the only behavioral authority.

---

## Runtime & State

- **Manual execution loop:** `ToolAgentsRunner._run_turn_events` with bounded `.step()`.
- **No result caching.** Every call hits the live backend fresh.
- **Context window:** there is no per-request `num_ctx` — Ollama's `/v1` endpoint ignores it, so `ToolAgentsLlamaProviderConfig` deliberately sends none (regression-guarded by `tests/test_toolagents_runtime.py::test_ollama_provider_settings_has_no_per_request_num_ctx`). A large context window is baked into the Ollama Modelfile (e.g. `gemma4:e4b-it-qat-120k`).
- **No generation-length cap either.** `create_settings()` sends no `max_tokens` — a prior hardcoded cap was confirmed by direct replay to truncate a model mid-reasoning before it could emit a tool call. The backend's own maximum output capacity is used instead (AGENTS.md "Maximizing Context & No String-Based Clipping").
- **Context compaction:** one-pass proportional slicing with truncation flags.
- **Wire-format role safety:** runtime directives injected as `user`-role only.
- **`change_graph` output is minimal.** Success: `{"ok": true}`. Failure: `{"ok": false, "error_type": "...", "errors": [{"code": "...", "message": "..."}]}`. Validation errors surface as `errors[].code == "gnu_validation"`. The `force=True` flag bypasses validation but the batch is still applied; the model must read `ok` to know whether edits applied. A batch with every operation array empty/absent is rejected (`error_type: invalid_request`) rather than trivially returning `ok=true` with nothing applied.
- **Stuck-loop detection, two layers:** a tight detector on byte-identical repeated failing arguments (same tool + same args, native to `_call_signature`), and a looser detector on repeated failures of the same category (same tool + same `error_type`/error code, higher threshold) that catches a model varying its arguments each time while repeating the same underlying mistake. Either tripping stops the turn with `error_type: safety_ceiling_reached` instead of burning the turn budget on a run that will never converge — there is no round-count ceiling (`max_tool_rounds` was removed as dead config once loop detection replaced it; the loop is bounded only by loop/stuck detection).
- **Degenerate-response retries are visible, not just logged.** `_fetch_model_response` yields a `degenerate_retry` event (attempt number, `max_attempts`, `finish_reason`) whenever the model returns no content and no tool calls and the runner retries — previously this only produced a `logger.warning` with no trace in `agent.chat_history` or any yielded event, so a saved transcript couldn't show it happened. Not model-facing (dev/debug observability only, same category as the `on_tool_rejected` hook); retry behavior itself is unchanged.

---

## Constraints (hard prohibitions)

- **No daemon management.** Never manage OS services/daemons.
- **No hardware polling.** No `psutil`, `nvidia-smi`, or telemetry.
- **Non-blocking flow.** Launch into degraded mode if backend unreachable; never `sys.exit()`.
- **No result caching outside the transaction history.**
- **No application-flow changes without permission.**
- **No `gnuradio` imports outside `grc_native_adapter.py`** and auxiliary files (doctor, session catalog paths).

---

## Key Conventions

- **Param filtering** (one rule, in `param_filter.py`): Stage A (every mode) drops `hide == "all"`, `category ∈ {Advanced, Config}`, `dtype == "gui_hint"`. Stage B (overview mode only) keeps `hide == "none"` OR `dtype == "enum"` OR `value != default` OR `references_variable` OR the param is type-controlling (native-derived via `type_controlling_params`, from each port's raw dtype template — not a hardcoded name) OR `generate_options`. Details mode = Stage A only; overview mode = Stage A + Stage B. Do not reimplement filtering inline. Four narrow, documented structural exceptions precede/extend the two stages (`dtype == "id"`, `showports`, `bus_structure_*`, `generate_options`) — see the module docstring in `param_filter.py` for why each one can't be derived from a uniform hide/category/dtype rule.
- **State values:** `enabled`, `disabled`, `bypass` (accept `bypassed` as alias). Use `Block.STATE_LABELS` for validation, not a hardcoded set.
- **Block lookup:** use native `flow_graph.get_block(name)`, not a manual scan.
- **Graph identity:** file-bytes SHA-256 (cross-session) + `state_revision` counter (in-session). No deep-JSON hashing.
- **Atomic save:** temp file → fsync → `os.replace()` → directory fsync. Lock via `fcntl.flock` on `.grc_agent/<name>.lock`. Backup saved before each save.
- **Tool surface:** `agent.py` only registers the 5 MVP tools. No internal tools, no legacy tool registry.
- **`change_graph` output:** `{"ok": true}` on success; `{"ok": false, "error_type": "...", "errors": [...]}` on failure. No `committed`, `ops_applied`, `state_revision`, `validation`, `hint`, `rejected_phase`, `graph_unchanged`, `native_validation_errors`, or `rollback` fields.
- **Ports** (`inspect_graph`'s `GrcBlock.inputs`/`.outputs`): one uniform rule, same Stage A/B shape as params, no separate mode. Stage A drops hidden ports (native `Block.active_sinks`/`active_sources`, already filtered). Stage B additionally drops a port only if it is both `optional` and unconnected (native `Port.connections(enabled=True)`); required or connected ports always show. Catalog/`query_knowledge` ports get Stage A only (no live connections to check pre-instantiation).
- **`type`/dtype auto-resolution:** `change_graph` accepts the literal string `"auto"` on any type-controlling param (native-derived per-block via `type_controlling_params`, e.g. `type`, or `itype`/`otype` for multi-type blocks) in both `add_blocks` and `update_params` — resolved from a connected neighbor's dtype. `add_blocks` falls back to GRC's own default silently if unresolvable (mirrors omitting the key); `update_params` returns an explicit `type_auto_unresolvable` error instead of guessing, since the model asked directly for an existing block. Manual override with a real value always still works.
- **Port-count-controlling params:** `port_count_controlling_params(block_type)` in `param_filter.py` mirrors `type_controlling_params` exactly, but derives which param controls a block's *port count* (e.g. `num_inputs` for `blocks_add_xx`, `num_streams` for `pad_source` — no single conventional name) from each port's raw `_multiplicity` template, not a hardcoded name. `grc_native_adapter.py`'s `_find_port` uses it to name the controlling param and its current value when a connection targets a port index that doesn't exist yet, instead of leaving the model to guess that a param needs to change at all.

---

## Test Gate

| Marker | Command |
|--------|---------|
| default | `pytest -m "not grc_native and not gui and not llama_eval"` (387 passed, 6 skipped) |
| `grc_native` | `pytest -m grc_native` (87 passed, 1 skipped; requires GNU Radio) |
| `gui` | `xvfb-run pytest -m gui` (9 passed) |

Default CI command: `pytest -m "not grc_native and not gui and not llama_eval"`. The `docs/MODEL_CONTEXT_BIBLE.md` staleness guard (`tests/test_model_context_bible.py`) runs in this default gate.
