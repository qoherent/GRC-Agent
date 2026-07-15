# AGENTS.md

Rules for AI coding agents working on this codebase. Direct, data-driven, zero fluff.

## Architectural Vision & Core Rules

- **Simplify First**: Lean towards simplifying, not complicating. If a feature or approach is ad-hoc, hardcoded, or not essential, **remove it**.
- **No Brittle Reinventions**: Reject complex manual implementations or from-scratch logic when reliable, standard libraries can replace them. PydanticAI owns the agentic loop, tool dispatch, and message history; gbulb owns the asyncio+GTK event loop unification. Do not reimplement either.
- **No Backward Compatibility**: Delete dead code completely. Do not write shims, dual-format persistence layers, or legacy bridges. Keep changes clean and direct.
- **No Assumed Reasoning Failures**: Do not assume task failures are solely due to LLM reasoning. Audit the execution harness for context flooding, poor prompt construction, hidden ad-hoc logic, or silent error message clipping. Correctness lives at the source.
- **Maximizing Context & No String-Based Clipping**: Do not enforce arbitrary context limits beyond what the backend actually supports. Never clip inputs or outputs using raw character slicing which breaks structured context.
- **Be Bold, Objective, and Grounded**: Base every decision on grounded, verified observations, never on assumptions. Ask for clarification when requirements are ambiguous or when a major decision (e.g. library selection, backend config) needs to be made.

---

## Architecture at a glance

Single-process, single-thread native GTK3 desktop app. `gbulb.install(gtk=True)`
unifies asyncio and the GLib main loop — the agent, canvas, and all tool calls
run on one thread with zero cross-thread marshaling. GRC's own `MainWindow` is
extended in-place with a `ChatSidebar` widget (packed into the MainWindow's
existing `Gtk.HPaned` structure — no reparenting of the `DrawingArea`).

Everything lives in the installable `grc_agent` package, under `src/grc_agent/`.
It must never import from anywhere outside the package — the one accepted
exception is `ingest.py` reading the `docs/wiki_gnuradio_org/` corpus, which is
shipped package data via `pyproject.toml`'s `force-include`.

| File | Role |
|--------|------|
| `desktop_app.py` | Entrypoint. `gbulb.install(gtk=True)`, builds GRC's `Application`/`MainWindow`, wraps `window.main` in an outer `Gtk.HPaned` with `ChatSidebar` as `pack2`. Wires toolbar signals (New, Blocks toggle), Ctrl+/- zoom, SIGTERM shutdown. Auto-detects the active graph from GRC's notebook — no Browse button. |
| `chat_sidebar.py` | Native GTK chat UI. `Gtk.ListBox` message list with streaming via `agent.iter()` + `run.next(node)` (fires capability hooks). Pango markup for basic markdown (applied at part-close, not per-token). Settings dialog backed by `settings.py`. Slim side toggle for GRC block library. Send button doubles as Stop/abort while busy. Graph badge shows `<name> active`. |
| `native_canvas.py` | GRC `MainWindow` canvas signal-wiring. `NativeCanvasManager` resolves `drawing_area`/`path`/`current_flow_graph` dynamically from `window.current_page` — follows tab switches automatically. Connects to notebook `switch-page`/`page-added`/`page-removed` signals. `NativeFlowgraphProxy` is the agent's `deps` — forwards attribute access to the current page's live `FlowGraph`. 1.5s safety-net poll for unsynced edits. Middle-click pan. GRC's native undo/redo is left enabled. |
| `agent_factory.py` | `build_interactive_agent()` — constructs the provider-specific model from `settings.py` (Ollama/OllamaCloud/OpenRouter) and the PydanticAI `Agent` with tools, capabilities, output validator. `extra_body={"think": True}` only for Ollama providers. Retries on `HTTPStatusError` for cloud providers. |
| `runner.py` | `FlowgraphRunner` — async subprocess lifecycle for Run/Stop. `asyncio.create_subprocess_exec` with `start_new_session=True`. Stop: `SIGTERM` process group → 2s wait → `SIGKILL`. Single-flight gate. |
| `adapter/` | Sole `gnuradio` importer. `graph.py` (flowgraph load/save, `change_graph` 7-phase mutation engine with single `rewrite()` after `resolve_auto`, `keep_param` filtering, `generate_flowgraph_py` codegen), `rag.py` (catalog/docs vector RAG with cached embed client), `snapshots.py` (undo/redo disk stack), `layout.py` (grandalf block placement), `search.py` (async DuckDuckGo fallback). |
| `agent.py` | PydanticAI `Tool`s via `grc_tools()` (`inspect_graph`, `query_knowledge`, `change_graph`), `web_search_cap`/`web_fetch_cap` capabilities, system prompt, scenario harness for integration tests. `MODEL`/`OLLAMA_V1` constants are fixed for reproducible benchmarking. |
| `settings.py` | Persisted preferences (provider, models, API keys) in `.env` via `dotenv.set_key`/`get_key`. `env_path()`: `GRC_AGENT_ENV` override → `.env` walking up from CWD → `~/.config/grc_agent/.env`. |
| `ingest.py` | Builds catalog/docs sqlite-vec databases on first use (`adapter._ensure_db_built`). |
| `_paths.py` | Package-relative runtime-data directory resolution (`vectors_dir()`, `docs_dir()`). |

Data flow: `.grc` file → GRC's `MainWindow` → `window.current_page.flow_graph` (shared between canvas and agent tools on the same thread) → `inspect_graph()` → JSON tool result.

---

## Engineering Rules

- **No hand-picked heuristics.** No per-field allowlists, per-scenario branches, regex routing, or prompt folklore. If logic is needed, it is one uniform rule applied to every case (see `keep_param` in `adapter/graph.py`).
- **Prefer native methods.** Use GNU Radio GRC's Python API — `param.hide`, `param.category`, `Block.is_variable`, `flow_graph.is_valid()`, etc.
- **Fix at the source.** Correctness lives in the tool/handler that produces data, not in a post-processor.
- **No silent transformation.** Any truncation, filtering, or omission in model-facing output must be explicit.
- **Simplify by removal.** Prefer deleting code over adding it.
- **Evidence before assertions.** Every claim cites a verified observation, never intent. A green test is necessary, not sufficient — inspect actual data flow.
- **Prefer pydantic_ai's own sanctioned extension points** (capabilities, `ModelRetry`, `AsyncTenacityTransport`, etc.) over hand-rolled retry/loop/context logic.
- **gbulb owns the event loop.** `gbulb.install(gtk=True)` must be called after `gi.require_version("Gtk", "3.0")` but before any asyncio/GTK usage. All async work (agent streaming, runner subprocess I/O) runs on this unified loop — no `GLib.idle_add` marshaling for widget updates from async code.
- **The agent and canvas share the same `FlowGraph` object.** In the single-process app, `NativeFlowgraphProxy` forwards to `window.current_page.flow_graph`. Agent edits modify it in-place — `after_agent_edit()` just calls `queue_draw()`, no reload-from-disk needed. Manual edits need `write_flow_graph_atomic()` + `push_undo_snapshot()`.

---

## Tool Surface

Five model-facing tools. Three are wired in `agent.py`'s `grc_tools()`, registered
as plain `Tool(fn, name=..., description=...)` relying on pydantic-ai's own
signature/type-hint introspection. The other two are provider-adaptive
`WebSearch`/`WebFetch` capabilities.

| Tool | Direction | Engine |
|------|-----------|--------|
| `inspect_graph` | read | `adapter.inspect_graph()` (Stage A + B filtered) |
| `query_knowledge` | read | `adapter.query_catalog()` (catalog domain) **or** `query_docs()` (docs domain, vector RAG) — builds its `.db` on first use via `ingest.py` if missing |
| `web_search` | read | pydantic-ai `WebSearch` capability — native on OpenRouter, `adapter.lite_web_search` (async lite.duckduckgo.com scrape) local fallback on Ollama. |
| `web_fetch` | read | pydantic-ai `WebFetch` capability — native where supported, bundled markdownify fallback otherwise. |
| `change_graph` | write | `adapter.change_graph()` — 7-phase transactional batch mutation with rollback; a failure raises `ModelRetry` instead of returning `ok=false` |

---

## Key Conventions

- **Param filtering** (one rule, in `adapter/graph.py`'s `keep_param`): Stage A (every mode) drops `dtype == "id"`, `showports`, `bus_structure_*`, `hide == "all"`, `dtype == "gui_hint"`. Stage B (overview mode only) keeps `hide == "none"` OR `dtype == "enum"` with a non-default value or structural status OR `value != default` OR references a variable OR the param is type-controlling OR `generate_options`.
- **State values:** `enabled`, `disabled`, `bypass` (accept `bypassed` as alias). Validated against the block's own `STATE_LABELS`.
- **Block lookup:** use native `flow_graph.get_block(name)`, not a manual scan.
- **Atomic save:** temp file → fsync → `os.replace()` → directory fsync. Lock via `fcntl.flock` on `.grc_agent/<name>.lock`.
- **`change_graph` output:** returns `{"ok": true}` on success; `{"ok": false, "error_type": "...", "errors": [...]}` on failure. The tool wraps this: on `ok=false` it raises `ModelRetry` with errors folded into the message. `force=True` bypasses native-validation failures only.
- **Graph-validity gate:** `agent.output_validator(validate_flowgraph_state)` runs on every turn's final output. If any `change_graph` tool call appears in that turn's message history, it checks the live flow graph's native `is_valid()`/`iter_error_messages()` and raises `ModelRetry` if invalid.
- **`flow_graph.validate()` must be called before trusting `is_valid()`/`iter_error_messages()`.** GNU Radio's `Element._error_messages` list is populated only by an explicit `.validate()` call and is cleared — without being refilled — by `.rewrite()`. Since every mutation path calls `rewrite()`, skipping the `validate()` call right before checking `is_valid()` makes the check vacuously pass.
- **Ports** (`inspect_graph`'s block `inputs`/`outputs`): Stage A drops hidden ports. Stage B additionally drops a port only if it is both `optional` and unconnected.
- **`type`/dtype auto-resolution:** `change_graph` accepts the literal string `"auto"` on a type-controlling param, resolved from a connected neighbor's dtype via `resolve_auto`. Known gap: resolution for a brand-new block whose only connection is in the same batch's `add_connections` list can silently fall through to GNU Radio's own default.
- **`set_param`'s "unknown param" error lists the block's actual valid param keys** — so the model doesn't waste a `query_knowledge` round-trip guessing.
- **`resolve_auto` only ever resolves from an explicit, non-`"auto"` value** — it never guesses from an equally-unresolved neighbor, and it fails loudly instead of silently defaulting.
- **`change_graph`'s tool description is derived from its own docstring** (`docstring_format="google", require_parameter_descriptions=True`).
- **`change_graph` calls `flow_graph.rewrite()` once** — after `resolve_auto` (Phase 5) and before `add_connections` (Phase 7), so new blocks' ports are initialized. The old per-block `rewrite()` in the add_blocks loop was removed (O(N) rewrites → 1).
- **Undo/redo uses GRC's native StateCache.** Our custom disk-based undo/redo stack (`adapter/snapshots.py`) is kept for snapshot pushes during `sync_manual_edit` and `change_graph`, but the UI buttons and `NativeCanvasManager.undo()`/`redo()` methods are removed. GRC's built-in Ctrl+Z/Y works natively.
- **New blocks are positioned by a headless, collision-avoiding placement.** `change_graph`'s `add_blocks` phase uses `grandalf`'s Sugiyama-style rank assignment + a spiral grid search for the non-overlapping coordinate. This must stay fully automatic since `inspect_graph` filters coordinates out of context.
- **Vector DBs are built, not shipped.** `adapter._ensure_db_built` lazily calls `ingest.py` to build the catalog/docs DB on first `query_catalog`/`query_docs` call. Each backend gets its own `.db` file. `_db_meta` table stores `embedding_model` and `corpus_version` (checked on every query, auto-rebuild on mismatch). The OpenAI embed client is cached at module level for connection reuse.
- **Chat model/provider is user-configurable.** `agent_factory.build_interactive_agent()` reads `settings.py`'s `load_settings()` to build the model. Three providers: `ollama` (local), `ollama_cloud` (cloud), `openrouter` (cloud). A saved change requires an app restart. `extra_body={"think": True}` is only sent to Ollama-based providers.
- **`generate_flowgraph_py` (Run/Stop codegen) overrides `run_options` to `'run'` — MUST call `.rewrite()` after `.set_value()`.** `FlowGraph.get_option(key)` returns `params[key].get_evaluated()`, which reads a *cached* `Param._evaluated` — and `.set_value()` only sets `self.value`, it never touches that cache. Only `.rewrite()` resets and recomputes `_evaluated`. Without the `.rewrite()` call, the generated `no_gui` script silently still contains `input('Press Enter to quit: ')`.
- **GRC's Notebook/ScrolledWindow hardcodes a 600×400 minimum size** (`Notebook.py:123-124`). `NativeCanvasManager.setup_signal_handlers()` calls `set_size_request(1, 1)` on the `ScrolledWindow` to let the canvas shrink to whatever the paned divider allows.
- **Middle-click (button 2) canvas panning** binds on the `DrawingArea` and returns `True` to consume. GRC's own `DrawingArea.py` only handles button 1 (select/drag) and button 3 (context menu).
- **A 1.5s safety-net poll catches edits that don't fire a trackable GTK signal** (properties dialog OK/Apply/Cancel, context-menu actions). `native_canvas.py`'s `_check_for_unsynced_edit` compares `flow_graph_content_hash` against `last_synced_export_hash` and triggers `sync_manual_edit()` on mismatch.
- **The active flowgraph is resolved dynamically** (`NativeFlowgraphProxy` in `native_canvas.py`). `drawing_area`, `path`, and `current_flow_graph` are properties that resolve from `window.current_page` on every access — tab switches are followed automatically. `get_state_lock()` returns `None` (single-thread, no races). `notify_edit()` calls `canvas_manager.after_agent_edit()` directly.
- **`lite_web_search` is async** — uses `httpx.AsyncClient` to avoid blocking the gbulb event loop.
- **Settings use `dotenv.set_key`/`get_key`** — the old manual regex-based `.env` upsert was replaced with `python-dotenv`'s built-in API.
- **`_sha256_file` uses `hashlib.file_digest`** — streams the file instead of loading it entirely into memory.
- **`_scroll_to_new_blocks` multiplies coordinates by `zoom_factor`** — scroll targets are in zoomed space.

---

## Test Gate

```bash
uv run pytest tests/test_unit.py              # fast, no LLM, no display
uv run pytest tests/test_isolation.py         # settings/model isolation, no LLM
uv run pytest tests/test_button_integration.py # tool/button integration, Ollama Cloud
uv run pytest tests/test_integration.py       # live model scenarios, ~15-20 min
uv run ruff check
```

`test_unit.py`/`test_isolation.py` touch live network (lite.duckduckgo.com search,
Ollama embeddings/chat for RAG) — they are not fully hermetic, but need no
GUI/display server. `test_integration.py` runs the full scenario suite against
a live local model.

Tests that need a real GTK widget tree require `xvfb-run` (e.g. `xvfb-run -a uv run pytest`).
