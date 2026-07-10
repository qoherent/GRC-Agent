# AGENTS.md

Rules for AI coding agents working on this codebase. Direct, data-driven, zero fluff.

## Architectural Vision & Core Rules

- **Simplify First**: Lean towards simplifying, not complicating. If a feature or approach is ad-hoc, hardcoded, or not essential, **remove it**.
- **No Brittle Reinventions**: Reject complex manual implementations or from-scratch logic when reliable, standard libraries can replace them. Always opt for robust libraries to avoid reinventing the wheel — PydanticAI owns the agentic loop, tool dispatch, and message history; do not reimplement any of that here.
- **No Backward Compatibility**: Delete dead code completely. Do not write shims, dual-format persistence layers, or legacy bridges. Keep changes clean and direct.
- **No Assumed Reasoning Failures**: Do not assume task failures are solely due to LLM reasoning. Audit the execution harness for context flooding, poor prompt construction, hidden ad-hoc logic, or silent error message clipping. Correctness lives at the source.
- **Maximizing Context & No String-Based Clipping**: Do not enforce arbitrary context limits beyond what the backend actually supports. Never clip inputs or outputs (like error payloads or long tool results) using raw character slicing which breaks structured context.
- **Be Bold, Objective, and Grounded**: Base every decision on grounded, verified observations, never on assumptions. Ask for clarification when requirements are ambiguous or when a major decision (e.g. library selection, backend config) needs to be made.

---

## Architecture at a glance

Everything lives in the installable `grc_agent` package, under `src/grc_agent/`. It must never import from anywhere outside the package — the one accepted exception is `ingest.py` reading the `docs/wiki_gnuradio_org/` corpus from the repo, since that corpus is source data for the docs vector DB, not shipped package data.

| File | Role |
|--------|------|
| `adapter.py` | The **only** module that imports `gnuradio`. Lazy singleton `get_platform()`. All GRC-facing logic: flow-graph load/save, parameter/port visibility filtering, block role classification, the `change_graph` mutation engine, catalog/docs vector RAG, `lite_web_search` (a lite.duckduckgo.com scrape used as the local fallback of pydantic-ai's `WebSearch` capability on providers without native search). |
| `agent.py` | Wires `adapter`'s functions into PydanticAI `Tool`s via `grc_tools()` (`inspect_graph`, `query_knowledge`, `change_graph`) plus provider-adaptive `WebSearch`/`WebFetch` capabilities (defined once here as `web_search_cap`/`web_fetch_cap`, imported by `web.py` and the tests), defines the system prompt, hosts the scenario harness used by integration tests. Its `MODEL`/`OLLAMA_V1` constants are deliberately fixed (not wired to `settings.py`) so the scenario harness stays reproducible run-over-run. |
| `web.py` | Starlette app: proxies/rebrands `agent.to_web()`'s chat widget, serves the GNU Radio dashboard (`panel.html`) and its `/grc/*` JSON API, builds the interactive chat `Agent`'s model from `settings.py`'s saved provider/model preference. |
| `panel.html` | The dashboard page — plain HTML/CSS/vanilla JS, no build step. |
| `ingest.py` | Builds the catalog/docs sqlite-vec databases from scratch on first use (`adapter._ensure_db_built`) — no separate CLI or warmup step. |
| `settings.py` | Persisted provider/model preference (`settings.json`, gitignored). |
| `_paths.py` | Package-relative runtime-data directory resolution (`vectors_dir()`, `docs_dir()`), each overridable via env var. |

Data flow: `.grc` file → `adapter.load_flow_graph()` → live `gnuradio.grc.core.FlowGraph` → `inspect_graph()` → JSON tool result.

---

## Engineering Rules

- **No hand-picked heuristics.** No per-field allowlists, per-scenario branches, regex routing, or prompt folklore. If logic is needed, it is one uniform rule applied to every case (see `keep_param` in `adapter.py`).
- **Prefer native methods.** Use GNU Radio GRC's Python API — `param.hide`, `param.category`, `Block.is_variable`, `flow_graph.is_valid()`, etc.
- **Fix at the source.** Correctness lives in the tool/handler that produces data, not in a post-processor.
- **No silent transformation.** Any truncation, filtering, or omission in model-facing output must be explicit.
- **Simplify by removal.** Prefer deleting code over adding it.
- **Evidence before assertions.** Every claim cites a verified observation, never intent. A green test is necessary, not sufficient — inspect actual data flow. When verifying UI behavior, drive it with a real (even headless) browser — curl/API-level checks can miss pure client-side rendering bugs.
- **Prefer pydantic_ai's own sanctioned extension points** (capabilities, `ModelRetry`, `AsyncTenacityTransport`, etc.) over hand-rolled retry/loop/context logic. Check what the framework already provides (e.g. via its current docs) before building something custom.

---

## Tool Surface

Five model-facing tools. Three are wired in `agent.py`'s `grc_tools()`, registered as plain
`Tool(fn, name=..., description=...)` relying on pydantic_ai's own signature/type-hint
introspection (Pydantic models `BlockAdd`/`ParamUpdate`/`StateUpdate` for `change_graph`'s
structured args) — there are no hand-written JSON schema constants. The other two are
provider-adaptive `WebSearch`/`WebFetch` capabilities (`web_search_cap`/`web_fetch_cap` in
`agent.py`), added to `capabilities=[...]` alongside `ProcessHistory`/`StopGracefully` rather
than being part of `grc_tools()`. Both are eager (`defer_loading=False`), so they are always
callable — no `load_capability` round-trip. On providers with native support (OpenRouter via
its plugins) search/fetch run server-side; on providers without it (Ollama has none) they
fall back to `local` — `adapter.lite_web_search` (a lite.duckduckgo.com scrape) for search,
and the bundled markdownify tool (`WebFetch(local=True)`) for fetch.

| Tool | Direction | Engine |
|------|-----------|--------|
| `inspect_graph` | read | `adapter.inspect_graph()` (Stage A + B filtered) |
| `query_knowledge` | read | `adapter.query_catalog()` (catalog domain) **or** `query_docs()` (docs domain, vector RAG) — builds its `.db` on first use via `ingest.py` if missing |
| `web_search` | read | pydantic-ai `WebSearch` capability — native on OpenRouter, `adapter.lite_web_search` (lite.duckduckgo.com scrape) local fallback on Ollama. Returns raw snippets for in-context grounding (no separate LLM synthesis call). |
| `web_fetch` | read | pydantic-ai `WebFetch` capability — native where supported, bundled markdownify fallback (`WebFetch(local=True)`) otherwise. |
| `change_graph` | write | `adapter.change_graph()` — 7-phase transactional batch mutation with rollback; a failure raises `ModelRetry` (see Key Conventions) instead of just returning `ok=false` for the model to notice on its own |

No internal tools, no legacy tool registry. Tool schema and system-prompt tuning are permitted for general fixes; no ad-hoc or hardcoded prompt/schema rules targeting specific test scenarios or block instances.

---

## Key Conventions

- **Param filtering** (one rule, in `adapter.py`'s `keep_param`): Stage A (every mode) drops `dtype == "id"`, `showports`, `bus_structure_*`, `hide == "all"`, `category ∈ {Advanced, Config}`, `dtype == "gui_hint"`. Stage B (overview mode only) keeps `hide == "none"` OR `dtype == "enum"` with a non-default value or structural status OR `value != default` OR references a variable OR the param is type-controlling (native-derived via `type_controlling_params`, never a hardcoded name) OR `generate_options`. Do not reimplement filtering inline.
- **State values:** `enabled`, `disabled`, `bypass` (accept `bypassed` as alias). Validated against the block's own `STATE_LABELS`, not a hardcoded set.
- **Block lookup:** use native `flow_graph.get_block(name)`, not a manual scan.
- **Atomic save:** temp file → fsync → `os.replace()` → directory fsync. Lock via `fcntl.flock` on `.grc_agent/<name>.lock` next to the target file. Backup saved before each save.
- **`change_graph` output:** `adapter.change_graph()` returns `{"ok": true}` on success; `{"ok": false, "error_type": "...", "errors": [...]}` on failure. `force=True` bypasses native-validation failures only — adapter errors (unknown param, missing block) cannot be bypassed. A batch with every operation array empty/absent is rejected rather than trivially returning `ok=true` with nothing applied. The `change_graph` **tool** wraps this: on `ok=false` it raises `ModelRetry` (with the errors folded into the message) instead of returning the payload as an inert tool result, so the model gets an automatic corrective turn — `change_tool.max_retries = 3`, pydantic_ai's own sanctioned retry budget. A separate `args_validator` (`validate_change_graph_args`) pre-checks that referenced block names actually exist before the call ever reaches `adapter.change_graph()`, raising `ModelRetry` early and cheaply for that specific failure mode.
- **Graph-validity gate:** `agent.output_validator(validate_flowgraph_state)` runs on every turn's final output. If any `change_graph` tool call appears in that turn's message history, it checks the live flow graph's native `is_valid()`/`iter_error_messages()` and raises `ModelRetry` with the real GNU-Radio-native error text if invalid — the model cannot end a turn having left the graph broken without either fixing it or explicitly using `force=True` earlier in the same turn.
- **Ports** (`inspect_graph`'s block `inputs`/`outputs`): Stage A drops hidden ports (native `active_sinks`/`active_sources`, already filtered). Stage B additionally drops a port only if it is both `optional` and unconnected; required or connected ports always show.
- **`type`/dtype auto-resolution:** `change_graph` accepts the literal string `"auto"` on a type-controlling param, resolved from a connected neighbor's dtype via `resolve_auto`. Known gap: resolution for a brand-new block whose only connection is in the same batch's `add_connections` list can silently fall through to GNU Radio's own default instead of resolving correctly — verify with a live-connected-block test case before trusting this path for new blocks.
- **The active flowgraph is a swappable proxy** (`FlowgraphProxy` in `web.py`), not fixed at startup — every tool already does plain attribute access on `ctx.deps`, so this needed no changes to `agent.py`'s tool code to support loading a different file mid-session. It starts empty (`None`) — a tool call before a file is loaded raises a clear `RuntimeError`, not a crash.
- **Vector DBs are built, not shipped.** `src/grc_agent/vectors/*.db` is gitignored; `adapter._ensure_db_built` lazily calls `ingest.py` to build it on first `query_catalog`/`query_docs` call. Each backend (Ollama/OpenRouter) gets its own `.db` file, namespaced by backend only — changing an embedding model for a backend that already has a cached `.db` requires manually deleting that file to force a rebuild (no staleness auto-detection, by design — flagged, not silently handled).
- **Chat model/provider is user-configurable, not hardcoded.** `web.py`'s interactive agent builds its model from `settings.py`'s `load_settings()` (provider + model name, persisted to a gitignored `settings.json`, editable via the GUI's settings panel or `/grc/settings`). A saved change requires an app restart — no live in-process model swap (`Agent.override()` is a bounded-scope test tool, not a persistence mechanism). `agent.py`'s scenario-harness `MODEL`/`OLLAMA_V1` constants are intentionally separate and fixed, for reproducible benchmarking.

---

## Test Gate

```bash
uv run pytest tests/test_unit.py        # fast, no LLM
uv run pytest tests/test_web_app.py     # web endpoints, no LLM
uv run pytest tests/test_integration.py # live model, ~15-20 min
uv run ruff check
```

`test_unit.py`/`test_web_app.py` still touch live network (lite.duckduckgo.com search, page fetches, Ollama embeddings/chat for RAG) — they are not fully hermetic, but need no GUI/display server. `test_integration.py` runs the full scenario suite against a live local model; expect occasional flakiness on weaker/quantized models (loop/retry limits, tool-call formatting) unrelated to code correctness — retry the specific failing scenario before treating it as a regression.
