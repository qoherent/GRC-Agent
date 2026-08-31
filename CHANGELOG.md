# Changelog

All notable changes to this project are documented in this file.

The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning starts fresh at `0.1.0` for the current native GTK3 architecture —
earlier `v1.0.0`/`v2.0.0` tags belonged to an unrelated, since-rewritten
web-dashboard codebase and are not part of this history.

## [Unreleased]

### Added
- **Chat image input (multimodal user prompts)**: The composer gained a paperclip attach button opening a multi-select image chooser (png/jpeg/gif/webp — pydantic-ai's `ImageMediaType` set, derived via `get_args`), with removable thumbnail chips; image-only sends are allowed. Dispatch builds the user prompt as pydantic-ai's native `Sequence[UserContent]` (`[text, BinaryContent...]`) straight into `agent.iter()`, so images reach vision-capable models through the standard multimodal contract — no provider-specific code. Session persistence needed no schema change: the existing `ModelMessagesTypeAdapter` store round-trips `BinaryContent` bytes as base64, verified by a new regression test; the new canonical `db.user_request()` builder replaces both `ModelRequest.user_text_prompt` sites (new-session insert and failed-turn remember) and `db.prompt_images()` extracts image parts. User bubbles and reloaded history render thumbnails decoded at target scale via `PixbufLoader`'s `size-prepared` hint (a 24MP photo never allocates a full-resolution RGBA buffer for a 128px thumb). Screenshot tooling and vision-capability probing remain out of scope (backlog #1).
- **Offline knowledge corpus expansion & cleanup (`docs/wiki_gnuradio_org/`)**: 18 new high-signal pages crawled from official wiki snapshots (Wayback Machine — the live wiki's Cloudflare challenge blocks automated fetches): UHD USRP Source/Sink, PlutoSDR Source/Sink, Costas Loop, Symbol Sync (successor to the deprecated Polyphase Clock Sync), Correlation Estimator, MPSK SNR Estimator, a full PSK Demodulation guided tutorial, PDU Split, Tags To PDU, Rational Resampler, Frequency Xlating FIR, QT GUI Range/Time/Frequency/Waterfall/Time-Raster sinks, and full replacements for Message Passing (257→3,187 words) and Tagged Stream Blocks (257→1,978 words). All 93 pre-existing files scrubbed of MediaWiki debris (`From GNU Radio`, jump-nav links, `## Contents` TOCs, `Retrieved from` footers, edit/image junk); 136 duplicate H1 headings demoted to H2 across 18 files so level-1/2 chunking splits on natural section boundaries; 12 zero-signal meta pages removed (Chat, Wiki_account, UsingVSCode, AcademicPapers, Tutorials index, DevelopersCalls, Hack Fests, Octave, CB, Eclipse). Docs vector DB rebuilt through the standard `_ensure_db_built` → `ingest_docs` path (670 chunks, FTS5 + sqlite-vec); 17-query retrieval battery returned hybrid RRF mode with new documents at rank ≤ 3 on every topic, `tests/test_adapter_rag.py` 18 passed, full unit suite 495 passed, ruff clean.
- **Option-based RAG installation instructions in `README.md`**: Added explicit pre-launch installation commands and guidance for Option 1 (Lexical Search) vs Option 2 (Local Vector Search Hybrid RAG with lightweight `llama.cpp` and `EmbeddingGemma`), detailing retrieval benefits and system requirements.
- **Structured product backlog tracks**: Restructured `docs/backlog.md` into 5 discrete capability tracks: Multimodal GRC Visual Inspection, Data-Plane File & Stream Visualization, Project File-RAG, Knowledge Corpus Expansion, and Platform Hardening.
- **Dynamic thinking expander streaming & auto-collapse**: When the model streams reasoning (`ThinkingPart`/`ThinkingPartDelta`), the thinking container in `ChatSidebar` now expands automatically (`expanded=True`) and auto-scrolls newly streamed tokens to the latest lines (via `scroll_to_mark`). When reasoning finishes (or transitions to text/tools), the container collapses automatically (`expanded=False`) and transitions its label from `"Thinking..."` to `"Thought"` (or `"Thought summary (Codex)"`). Completed/reloaded thoughts in message history remain collapsed by default.
- **Taller reasoning viewport**: Increased `ChatSidebar` thinking container height (`min_content_height: 200px`, `max_content_height: 750px`), allowing substantial vertical reading space for complex reasoning traces before internal scrollbars engage.
- **Integration test isolation in pytest**: Configured `addopts = "-ra -m 'not integration'"` and registered the `integration` marker in `pyproject.toml`, isolating live-LLM integration test suites (`test_integration.py` and `test_button_integration.py`) from default test runs while keeping them runnable on-demand via `pytest -m integration`.
- **Native Wayland startup advisory**: Detects native Wayland sessions (`XDG_SESSION_TYPE=wayland` or `WAYLAND_DISPLAY` without `GDK_BACKEND=x11`) during startup preflight and surfaces an actionable non-blocking advisory in the status bar (`GDK_BACKEND=x11 uv run grc-agent`) to prevent dropped GTK3 nested menu grabs.
- **ChatGPT (Codex) reasoning summary expander label**: When the active provider is `openai_codex`, the thinking widget in `ChatSidebar` displays `Thinking (summary)...` while streaming and `Thought summary (Codex)` when completed or reloaded from session history, accurately indicating OpenAI's summary reasoning API design.
- **`no_gui` flowgraph external terminal logging annotation**: `NativeFlowgraphProxy.get_run_log()` and `run_flowgraph()` now inspect GNU Radio's native `flow_graph.get_option("generate_options")` and annotate the tool result payload with `generate_options='no_gui'` and an explanatory `external_terminal_note` when console output is directed to an external terminal wrapper.
- **Validation-gate error attribution**: `change_graph` now snapshots pre-existing validation errors prior to Phase 1 mutations and isolates them from the Phase 7 validation gate and the turn-end `validate_flowgraph_state` output validator. The agent is only penalized via `ModelRetry` for errors newly introduced by its own mutations, allowing valid edits to succeed on flowgraphs that were already broken by pre-existing user state.
- **Catalog implementation docstrings in `query_knowledge`**: catalog payloads now embed each block's implementation-class docstring — resolved through the block's own code templates (imports exec'd exactly as GRC's generator runs them; `templates.make`'s target resolved to the installed SWIG class), so parameter units and semantics ("All settings max_freq and min_freq are in terms of radians per sample, NOT HERTZ") are retrievable offline without web fetches. Templated `*_x` blocks honestly carry no doc. The catalog corpus fingerprint gained a composition marker (`catalog-docstrings-v2`) so cached DBs rebuild exactly once. Validated against a corpus-derived ground-truth stress run: 7/7 units/semantics queries returned the correct block at rank ≤ 2 with the docstring attached, on both embedding backends.
- **Hybrid retrieval (Reciprocal Rank Fusion)**: when a backend DB contains both a vec0 index and an FTS5 index (llamacpp DBs always do) and the query embeds successfully, `_query_index` runs both rankings and fuses them via RRF (`_RRF_K = 60`, the Cormack/Clarke/Bütcher SIGIR 2009 literature constant — never tuned locally), tagging `search_mode: "hybrid"` and attaching a truthful fused `score` to catalog results. The complementary failure modes measured by the ground-truth stress run (lexical owns verbatim phrases, vector owns paraphrases; union hit@5 0.97 vs 0.87 best single) are now captured by the default engine: docs hit@5 0.87 → 0.95, MRR 0.72 → 0.79, exact-tier hit@5 1.00, with all 11 vector-only misses rescued and latency unchanged (~33 ms; the lexical leg costs ~1 ms on the already-open connection). All lexical-only paths (native lexical backend, embed-call failure, outage-built lexical-only DBs) are structurally excluded by the both-indexes guard and behave exactly as before, still tagged `search_mode: "lexical"`.

### Changed
- **Scientific rules & commandments rewrite of `AGENTS.md`**: Restructured `AGENTS.md` into a zero-fluff engineering guide centered on an empirical verification persona (evidence before assertions, mandatory `context7` MCP/skills lookups), core engineering practices (simplify first, no brittle reinventions, zero ad-hoc heuristics, no backwards-compatibility shims), and native GRC invariants.
- **C++ catalog block priority & EPB NumPy slice vectorization**: Mandated vectorized NumPy/SciPy slice operations in `work()` when custom logic requires an `epy_block`, while prioritizing standard GNU Radio C++ catalog blocks with VOLK SIMD vectorization.
- **Streamlined system prompts and tool docstrings**: Reduced static prompt and tool docstring surface by ~42%, eliminating tool parameter duplication for `run_flowgraph`, removing hardcoded command folklore, aligning filesystem sandbox descriptions with the project directory, and grounding block selection to prioritize standard GNU Radio C++ catalog blocks over Embedded Python Blocks.
- **SDR hardware permissions streamlined**: Updated prerequisite instructions and system prompt diagnostics to guide users toward driver udev rules installation (`uhd-host`, `rtl-sdr`, `hackrf`) and reloading (`sudo udevadm control --reload-rules`) rather than recommending overly broad group additions (`usermod -aG plugdev,dialout,usrp`).
- **Silenced embedding ingestion context truncation warnings**: Lowered `_cap_words` and `fit_to_context` log levels from `WARNING` to `DEBUG` in `adapter/rag.py` and `embed_runtime.py`, eliminating console log noise during initial vector database ingestion while retaining complete untruncated document bodies in the database payloads.
- **Indirect prompt-injection defense is detect-and-log, never withhold** (`block_high_risk=False`): withholding high-risk tool results false-positived deterministically on official GNU Radio doxygen pages — their own jQuery boilerplate (`$(document)` ×2) trips the tier-1 `shell_command` regex `\$\([^)]+\)` escalated to high by the 2-matches+entropy rule — blinding the agent mid-build (sessions 150/151 forensic). Every detection is still classified and logged via `_log_injection_detection`; the tool result passes through unchanged.
- **Shell timeout: tighter default, truthful schema**: `GRC_SHELL_TIMEOUT` is set to 120 s (from 600) in `.env` — a hung command is killed visibly and recoverably instead of stalling the turn for 10 minutes — and `run_command`'s model-facing `timeout_seconds` description now states the resolved value (`Maximum seconds to wait (default: GRC_SHELL_TIMEOUT, 120s)`) instead of a hardcoded "600s", so the schema can no longer lie about the user-tunable knob. Long jobs keep the documented escapes: an explicit `timeout_seconds` per call, or `start_command` for unbounded background work. (The true root cause of the session-150 hang — the harness spawning commands with an open, never-written stdin pipe — is an upstream issue: anyio's unset `stdin` default is `PIPE`.)
- **Cancelled-run salvage visibility**: `_clean_message_history_for_new_turn` now logs a warning whenever it drops a response carrying unprocessed tool calls, ending the silent divergence between the canonical history blob (popped) and the step-store snapshots (which retain the calls) that made session-forensics tool-call counts undercount.

### Fixed
- **Catalog distance semantics & distance honesty**: Omitted the `distance` key on non-vector lexical rows (`distance is None`) in `render_catalog_block` and `ingest.py`, eliminating misleading fabricated `0.0` values while retaining true cosine distances on vector-evaluated candidates and fused scores in hybrid mode.
- **Flowgraph validation retry error message**: Removed misleading `(or set force=True if they are unresolvable)` suggestion from `validate_flowgraph_state` retry prompt, ensuring the model resolves genuine graph errors rather than getting trapped in retry loops.
- **`output_truncated` now exists — and can fire — on the docs domain**: `query_docs` previously omitted the key entirely and, passing no over-fetch, could structurally never report truncation; the model had no "more results exist" signal on docs. Both `query_knowledge` domains now pass `extra_limit=1`, surface at most `limit` entries (docs slices its joined answer — the spare rowid is a truncation probe, never an extra chunk), and copy the engine-computed boolean. Truncation stays exact under hybrid fusion (per-index over-fetch, flag computed from the full fused pool).
- **`run_command`'s schema no longer hardcodes the timeout default**: the model-facing `timeout_seconds` description derives from the resolved `GRC_SHELL_TIMEOUT` value, so changing the knob in `.env` keeps the schema truthful.
- **Catalog rendering no longer floods the terminal with GRC's expected variable-eval noise**: rendering any of the 7 stock variables (`json_config`, `yaml_config`, `variable_file_filter_taps`, `variable_ldpc_encoder_def`, `variable_adaptive_algorithm`, `variable_modulate_vector`, `variable_struct`) inside the one-block dummy flowgraph makes `FlowGraph._reload_variables` fail by design ("tolerant of evaluation failures") and upstream `log.exception` flooded stderr via `logging.lastResort` on every rebuild and every query rendering one into top-k. The gnuradio.grc logger level is now raised for the render only and restored after — real GRC diagnostics during actual flowgraph load/run are unaffected. Also fixed: an unknown block id now returns `None` from `render_catalog_block` (the `except KeyError` was dead — `FlowGraph.new_block` never raises).

## [0.5.0] - 2026-08-26

### Added
- **Unified flowgraph execution tool**: Consolidated `run_flowgraph` and `stop_flowgraph` into a single domain tool [`run_flowgraph(action='start'|'stop', wait=True, timeout_seconds=60.0)`](src/grc_agent/agent.py). Dynamic conditional approval via Pydantic AI's native `ApprovalRequired()` gates `action='start'` before RF/hardware execution, while `action='stop'` executes ungated immediately.
- **Flowgraph execution boundary & shell tool grounding**: Added explicit system prompt invariants and Pydantic AI tool descriptions in `run_flowgraph_func` and `GrcShellToolset` (`run_command`/`start_command`) clarifying that flowgraphs must be executed via `run_flowgraph` (which compiles and generates the latest Python code from the in-memory graph and streams to GRC console) rather than via shell tools (which execute stale/un-compiled scripts on disk and bypass console logging).
- **`run_flowgraph` bounded-run auto-stop (`stop_after_seconds`)**: an optional runtime budget for `action='start'` + `wait=True` — the flowgraph is stopped automatically once it has run that many seconds without finishing on its own, one call instead of the start-then-stop pair (the old pattern forced two calls: `start` → `still_running` → `stop`, or a `wait=False` start the model had to remember to stop — a leaked process, possibly transmitting RF). The kill reuses the exact `stop_flowgraph()` native Stop path the toolbar button takes (SIGTERM) and reports `status='stopped_after_timeout'` with the return code; the timer runs on the existing unified loop via `exec_monitor.wait_for_run_end` — no background-task machinery, and the start marker fires synchronously inside Execute so the budget is measured from the run's own start. Deliberately opt-in (`default: null`): an enforced default would silently kill long GUI captures unless the model remembered to override it — a silent dead run is worse than the visible leaked one it prevents. Rejected up front (`ModelRetry`) when combined with `wait=False` (the call returns before anything could enforce the deadline) or a non-positive value; a run that finishes on its own right at the deadline reports the real `completed` outcome instead of a stop that never happened. `timeout_seconds` is ignored while the budget is set. Schema, system-prompt guidance, and AGENTS.md updated; 5 new hermetic tests.

### Removed
- **Removed `search_conversation_history` & `ConversationSearch`**: Deleted the capability and tool from both executor and planner, eliminating snapshot search overhead and keeping message histories direct and lossless.
- **Removed `read_tool_result` & `ToolOutputLimits`**: Deleted output spill handles and slice-reading indirection.
- **Removed redundant live-cloud compaction test**: Deleted slow 140s cloud test in favor of the existing 100% hermetic compaction test suite.

### Changed
- **Focused System Prompts on 7 Domain Tools**: Refined `prompts.py` to instruct exclusively on our 7 custom domain tools (`inspect_graph`, `query_knowledge`, `generate_python`, `change_graph`, `run_flowgraph`, `get_run_log`, `save_block`), removing hand-written tool lists in favor of Pydantic AI's automatic JSON schemas.
- **Single Provider-Adaptive Web Search**: Verified unified `WebSearch` capability resolving cleanly per backend (native `web_search` or fallback `duckduckgo_search`) with zero tool shadowing.

### Fixed
- **Block-name badges no longer render as superscript in chat prose.** GTK3
  child-anchor widgets are top-aligned and stretch to the full line box, with
  the label text centered inside that stretched box — so the pill text rode
  ~4px above the surrounding sentence baseline (measured: label center 11.5px
  vs text center ~15.2px). The proposed `rise`-tag fix does nothing (measured
  0px movement), and CSS padding on the `EventBox` is ignored by GtkBin. The
  anchored badge now wraps its label in a `Gtk.Box` with `padding-top: 4px`
  (GtkBox respects CSS padding), landing the label center within 0.2px of the
  text baseline; table-cell badges keep the plain centered look. Regression
  test measures the alignment numerically against the TextView's own font
  baseline.
- **`validate_flowgraph_state` no longer blames the agent for denied or rolled-back edits.** The turn-end validity gate previously armed on ANY `change_graph` ToolCallPart — including approval-card denials (the tool body never ran) and failed/rolled-back calls — so an invalid graph the USER created before the turn would `ModelRetry` the agent for it. It now arms only on calls that actually executed (`ToolReturnPart.outcome == 'success'`) plus the `rollback_failed` double-fault marker (the one failure path that can leave a mutated graph). Gated on pydantic-ai 2.31.0 semantics (denial normalizes to `outcome='denied'`; tool retries leave a no-outcome `RetryPromptPart`), grounded via context7 + pydantic.dev docs.
- **`run_command`'s model-visible schema no longer lies about the timeout default.** The harness docstring hardcodes `"(default: 30)"`; the app's real default is `GRC_SHELL_TIMEOUT` → 600s. `_apply_exec_approval` now corrects the `timeout_seconds` parameter description in-place (`Tool.description` and `function_schema` are separate fields — verified the mutation reaches every `tool_def` read).
- **exec_monitor: silent no-ops and stale suppression flags.** `wait_for_run_end` gains an `epoch` parameter — the run tool captures the run counter before triggering Execute, so a silent no-op (disabled Gio action) reports `not_started` instead of the previous run's stale `completed`. The `agent_initiated` suppression flag is now dropped when no start marker fires (`mark_run_agent_initiated_cancelled` + an `is_tracking` check in the proxy) instead of lingering to wrongly suppress a later user-run failure notification. Race-free: the start marker fires synchronously inside the Execute action.
- **Chat auto-scroll intent is tracked on the vadjustment, not wheel events.** The old `scroll-event` handler only saw wheel events — GTK3 emits none for scrollbar thumb dragging or keyboard scrolling (keybindings write the adjustment directly), so a user who scrolled up by dragging the bar or pressing PageUp kept `_auto_scroll = True` and the next streaming flush yanked them back to the bottom. `_on_scroll_value_changed` is now connected to the vadjustment's `value-changed` and recomputes stickiness from the scroll position on every change — one uniform rule for every scroll source (wheel, drag, keyboard, touch/kinetic). This is safe against streaming: `value-changed` fires only for the `value` property, while content growth only fires `changed`, so appends can never corrupt the intent flag (verified against the GTK3 docs' signal semantics). The 50-line `_on_user_scroll` wheel-direction/SMOOTH special-casing was deleted.
- **Expanding a thinking/tool container no longer scrolls the chat elsewhere.** GTK3 anchors the viewport to the adjustment *value*, so a row growing above the fold pushes every visible row down — clicking an expander in an older message visibly jumped the conversation. The shared `_on_expander_toggled` handler now compensates: after a synchronous re-layout it shifts the value by the toggled row's bottom-edge delta when the row ends at/above the viewport top, so the visible content stays anchored (the same compensation Polari applies to prepended log entries — verified via its commit history); at/below the fold no compensation is needed, and users pinned to the bottom still get the expansion revealed. The old "`_auto_scroll = False` on expand" hack — which permanently killed follow until a manual bottom-scroll — is gone; the two byte-identical expander callbacks are unified.
- **`_listbox.check_resize()` never re-laid-out rows (fixed at all 6 call sites).** Verified empirically: `Gtk.ListBox.check_resize()` compares its requisition against its viewport-fixed allocation and only *queues* a resize, so rows never re-allocate through that path — the original code's "force immediate re-measure" was a no-op. `self._scrolled.check_resize()` re-allocates the scrollable child synchronously, which is what makes the post-toggle allocation read in the expander compensation valid.
- **Appending rows no longer force-scrolls the view.** `_add_message_row` and `_replace_streaming_turn` dropped `force=True` — previously every tool expander/error label added mid-turn yanked a user reading earlier content to the bottom. New rows now follow the same stickiness gate as streaming; sending a message still re-engages follow explicitly in `send_message()`, and full rebuilds (`_render_history`) still force. Regression coverage: the old `test_tool_expander_disables_auto_scroll` (which encoded the removed hack) is replaced by three new tests — expand-toggles-keep-intent, anchor compensation with real widget allocations under xvfb, and intent tracking on direct adjustment changes.

### Changed
- **Dead code removed** (verification round A, all confirmed zero-callers): `settings.get_project_dir`/`set_project_dir`, `NativeFlowgraphProxy.get_state_lock`, `fs_tools.set_project_dir_provider`, `adapter/layout._compute_ranks` (incl. its `adapter/__init__` re-export), and `ApprovalCard.get_tool_call_id`. Tests re-pointed (`test_layout` → `_compute_layout_model(...).ranks`; project-dir assertion → `get_env_value("GRC_PROJECT_DIR")`; shell cwd test → monkeypatch).
- **One endpoint table instead of three** (`agent_factory.py`): `_PREFLIGHT_ENDPOINTS` now carries every fixed-endpoint provider (openrouter/openai added) as (URL template, key var, header builder); the duplicated `_OPENAI_SHAPED_PROVIDERS` dict is deleted and `_OPENAI_SHAPED_PROVIDER_IDS` is derived from the single table; the hand-built Google URL and the two 3-way `/models`-suffix expressions are replaced by the table + one `_models_url()` helper. Behavior spot-checks pass.
- **Flowgraph-execution boundary deduplicated** (×5 → 3): the invariant "run flowgraphs only via `run_flowgraph`, never shell scripts" now lives in the system prompt's Execution & Diagnostics clause plus the two shell tool descriptions; the duplicate prompt clause and the duplicated docstring paragraph were removed.
- **Planner allowlist += `web_search`** (defensive; native tools bypass `PrepareTools` today). **Mode toggle tooltip/accessible name** now state that Auto covers flowgraph changes, runs, and shell commands.
- **AGENTS.md drift corrected** (4 claims): `_compute_ranks` deleted → production uses `_compute_layout_model`/`LayoutModel`; the validity-gate description now says conversation-wide (run ids change per deferred-approval resume) and outcome-gated; `get_state_lock` mention removed; the Mode-toggle "only way to approve everything" claim corrected — any non-shell card's 'Always accept' also persists the global gate (backlog item 6's documented design).
- **Agent-facing schemas compressed** (verification round B, R3–R5): the model-visible tool descriptions shrank 3,907 → 3,347 chars. `change_graph` 788 → 514 (phase-order enumeration moved into a code comment — backend detail the model never needs; kept atomic-batch, approval, `auto`-resolution, and `force` semantics), `generate_python` 593 → 480 (failure modes condensed), the `k` argument descriptions in `query_knowledge`/`generate_python` shortened inline (a shared constant is not expressible in docstring-derived descriptions), and `run_flowgraph`'s probe-before-run paragraph reduced to a pointer at the system prompt (the strategy lives there once).
- **`settings.resolve_key`** — one uniform "where do secrets come from" rule replacing the repeated `get_env_value(X) or os.environ.get(X)` idiom at 5 call sites (provider key reads, compaction override, sidebar probe).


## [0.4.0] - 2026-08-26

### Added
- **Flowgraph execution tools** (`run_flowgraph`/`stop_flowgraph`): the agent triggers GRC's native Execute/Stop — the exact toolbar path — with output streaming to the GRC console where the user watches it live, and reads results back via `get_run_log`. Running is approval-gated (`requires_approval=True`, same native deferred-tool mechanism as `change_graph`) because it may transmit RF on connected hardware; stopping is the safe direction and ungated. `exec_monitor` gained a run-completion event (`wait_for_run_end`) and an agent-initiated flag that suppresses the redundant follow-up failure-notification turn when the tool result already reported the failure; `get_run_log` now always reports `run_in_progress` (with a note while a run is live, since the retained log then belongs to the previous run). Pre-gates replicate GRC's own handler conditions (unsaved page, already-running, invalid graph) because a disabled `Gio` action is a silent no-op and the unsaved path would open a modal Save-As that blocks the unified loop. GUI flowgraphs run until stopped: the tool returns `started` with `wait=False` and the prompt teaches the run/poll/stop pattern, making the probe-verification strategy (probe blocks -> run -> read log) fully autonomous in one turn.
- **Shell execution capability** (`shell_tools.py`, executor-only): the harness `Shell` toolset narrowed to this app — commands run in the configured project directory (dynamic per spawn, same providers as the file sandbox; unset root gates with the same actionable error), `run_command`/`start_command` require user approval showing the full literal command, and long jobs get background start/check/stop tools with automatic process-group cleanup at run end. Policy is a DENYLIST, not an allowlist: the harness's destructive defaults stay denied (user-tunable via `GRC_SHELL_DENIED_COMMANDS`/`GRC_SHELL_TIMEOUT`) while every engineering command (cmake/make/python3/gr_modtool, uhd_*/SoapySDRUtil/rtl_* SDR CLIs, project scripts, pipes) stays available — risk is managed by consent granularity, not by forecasting command names. Environment scrubbing is derived from the app's provider catalog (covers both Ollama keys and groq/mistral/cohere/xai, which the harness's `LLM_API_KEY_ENV_PATTERNS` misses). The planner stays structurally read-only (fail-closed allowlist).
- **Session-scoped shell prefix-allow**: shell approval cards offer "Always allow `<command>`" for the first token (approve `cmake` once, the rest of the build flows without nagging) — scoped to the current chat session only, never persisted, leaving the global Manual/Auto gate untouched. Non-shell cards keep the persisted "Always accept".
- **Generic approval cards**: `ApprovalCard` now renders per-tool titles and summaries (`format_tool_summary`) — the literal fenced command for shell tools, intent lines for `run_flowgraph`, the existing structured diff for `change_graph`, and one uniform per-argument bullet list for anything else.
- `adapter.gui_actions()` accessor: imports GRC's gui `Actions` namespace via the canonical Platform-first order — importing `Actions` directly into a fresh interpreter hits an upstream circular import (Actions -> Dialogs/Utils -> Bars -> partially-initialized Actions).
- Grounding notes for the autonomous-loop future (backlog item 4): vision-model probe design, V1 data-plane screenshot capture, and the file-RAG tool boundary (a separate bounded query tool, never a patch inside `read_file`).
- **Untitled graph save defaults to the project directory**: `Ctrl+S` on a new (untitled) flowgraph now opens GRC's own Save-As dialog pre-pointed at the sidebar's configured project directory (`GRC_PROJECT_DIR`), instead of GRC's arbitrary default folder. Implemented by seeding GRC's native `SaveFlowGraph` dialog class for the untitled case only — the entire native save flow (dialog, id rename, recent-files, `page.saved`/`grc_file_path`) still runs unmodified, with no duplicated handler logic and no new modal `.run()` in our code. Uniformly covers `Ctrl+S`, `Ctrl+Shift+S`, and File → Save As; already-named graphs keep GRC's own "start in the file's folder" behavior.
- **Untitled Save-As starts in the project directory**: GRC's own Save-As dialog is seeded with the sidebar's configured work directory for new, never-saved graphs (Ctrl+S proposes the project folder instead of GRC's arbitrary default) — one uniform rule, enforced by swapping `FileDialogs.SaveFlowGraph` for a thin subclass that only seeds the default folder; the native save flow, id rename, recent-files bookkeeping, and `page.file_path`/`page.saved` handling all stay GRC's own.
- **Layout: per-component row bands + crossing minimization** (`adapter/layout.py`): the flow band now gives each weakly-connected component its own row band (independent chains no longer interleave in shared columns with wires threading through each other's blocks), same-rank blocks are ordered by a bounded barycenter crossing-minimizer (8 sweeps), and the rank/order model is computed once per batch (`LayoutModel`) and shared between the add-blocks ordering and the full relayout — no second grandalf pass.

### Changed
- **Full-canvas auto-arrange is now a proper Sugiyama layout with per-component row bands** (`adapter/layout.py`): one grandalf pass (`_compute_layout_model`) ranks every block AND runs grandalf's own `Layer.order` crossing-minimizing barycenter sweeps per weakly-connected component, returning a `LayoutModel` (ranks + per-component ordered layers) that is computed once per `change_graph` batch and reused for both `add_blocks_sorted` and `compute_full_layout(model=...)` — never a second grandalf pass. `_place_flow_components` then gives each connected component its own row band starting again at the left margin (column = rank × `GRID_W`, rows in the crossing-minimized order), replacing the old single shared vertical stack where two independent chains interleaved in the same columns and their wires threaded through each other's blocks. Determinism is explicit, not incidental: grandalf's discovery/initial layer order walks Python sets (identity-hash order, varies between processes), so components are sorted by first member and every layer is alphabetically sorted before the sweeps — two independent runs produce byte-identical coordinates. A component grandalf's `init_all` refuses contributes no ranks and lands in a deterministic alphabetical fallback band instead of silently missing a coordinate. `ROW_GAP = GRID_H` between bands keeps one collision assumption for the whole canvas. Invocation is unchanged: still event-driven from inside `change_graph` after every topology-changing batch — never a tool or manual action. The old single-shared-stack `_order_flow_band` was deleted; `_compute_ranks` remains as a thin wrapper for tests.

### Fixed
- **Layout crash on skip-layer connections (`'DummyVertex' object has no attribute 'data'`)**: Grandalf's Sugiyama layout splits multi-rank edges ($\Delta\text{rank} > 1$) by inserting intermediate `DummyVertex` routing objects that lack a `.data` attribute. `_rank_and_order_component` (`adapter/layout.py`) previously sorted layers by `v.data` directly, throwing an unhandled `AttributeError` on any branching topology that skipped a layer. Layer sorting now accesses vertex data defensively (`getattr(v, "data", "")`), barycenter sweeps are guarded against unexpected ordering failures, and `change_graph` (`adapter/graph.py`) isolates cosmetic layout computations so that any unexpected layout exception falls back to default grid coordinates instead of rolling back the flowgraph mutation.
- **Manual edits after saving an untitled graph were never auto-synced**: saving an untitled graph in place (or Save-As to a new path) changes `page.file_path` without firing `switch-page`, so `last_disk_hash` stayed `None` and the 1.5s safety-net poll's `sync_manual_edit` early-return silently dropped every later manual edit for that tab. The poll now tracks the path it baselined and re-baselines on any path change — one uniform rule ("baselines follow the page's path").

## [0.3.2] - 2026-08-25

### Added
- **Direct AST Markdown Renderer (`markdown-it-py` `SyntaxTreeNode`)**: Replaced the intermediate HTML generation and BeautifulSoup (`bs4`) DOM parser with a direct recursive AST walker into `Gtk.TextBuffer` tags and native GTK widgets. Removed `beautifulsoup4` dependency.
- **Native GTK3 List Typography & Hanging Indents**: Replaced manual whitespace prefixes with centralized `Gtk.TextTag` indentation (`left_margin=24`, `indent=-16`), ensuring wrapped list continuation lines align with the bullet text across arbitrarily nested depths and ordered steps (`1.`, `9.`, `10.`, `100.`). Contiguous tag coverage spans all embedded `BlockBadge` child anchor characters (`U+FFFC`).
- **Normalized Vertical Rhythm & Loose Lists**: Consolidated structural block boundary newlines so list items and paragraphs emit single structural newlines with GTK text tag spacing (`pixels_above_lines`/`pixels_below_lines`), eliminating redundant blank lines and oversized gaps at list-to-block transitions while supporting multi-paragraph loose lists.
- **Search Mode Tool Indicator**: Settled `query_knowledge` tool expander titles dynamically display the active search mode (`⚙ query_knowledge (vector) ✓` or `⚙ query_knowledge (lexical) ✓`).
- **Stream Pacing & Timing Instrumentation**: Added monotonic timing fields (`queue_wait_ms`, `flush_duration_ms`, `pending_chunks`, `pending_chars`) in `_flush_streaming()` to diagnose streaming pacing without artificial debouncing or timers.
- **Human-in-the-loop flowgraph-change approval** via pydantic-ai's native `requires_approval=True` deferred-tool mechanism: `change_graph` calls never execute before the user approves. Each proposed edit shows an in-chat `ApprovalCard` with the model's required one-line `reason`, a uniform structured summary of the change (rendered as Markdown bullets — no raw JSON), and Approve / Deny / Always-accept actions. The gate persists in `.env` (`GRC_AGENT_APPROVE_CHANGES`, default `ask`) and is re-enabled any time via the new `Mode` toggle under the composer (Manual = ask, Auto = apply without asking). Denial feeds back to the model natively (`ToolDenied`); the same turn resumes automatically after the decision.
- `change_graph` now requires a `reason: str` argument (one-sentence intent) shown to the user in the approval card and echoed into the success payload, so the persisted transcript carries the edit's intent next to its outcome.
- The layout gate became one uniform rule: any `change_graph` batch that changes topology (`add_blocks`/`remove_blocks`/`add_connections`/`remove_connections`) re-ranks and relayouts the whole flowgraph — a later wire-only call now heals the stale alphabetical stack that add-then-wire editing previously froze.
- RCA hardening (backlog item 9): ports now expose `vlen` when ≠ 1 in both `inspect_graph` and catalog results (turns the opaque "8 vs 8192" item-size puzzle into vlen 1024 vs vlen 1); retry-budget turn deaths render a friendly continuation message instead of pydantic-ai's developer-aimed "Consider raising the max retry limit" text; chat CodeBlock + prose TextViews gained GTK3-native 3px inter-line spacing (with the code-block height pin updated to include per-line spacing); the prompt gained a formatting rule (lists as Markdown lists, fences for code only).
- System prompt: failed-fix counter-strategy (never repeat a failed fix; re-inspect and reconsider topology), external-grounding nudge for concepts local knowledge can't cover, the QT GUI freq-sink-owns-its-FFT quirk, and the approval/reason contract.

### Changed
- **Icon-based copy buttons** on code blocks and chat messages (compact, tooltip feedback instead of text swap), and the chat column chrome constant reduced 140 → 36 px — bubbles are wider with more reading width, empirically verified hbar-free at 320–1000 px window widths.
- Standardized footer typography and font sizes (`0.92em`) across toggle buttons and status labels.

### Fixed
- Fixed PyGObject segfaults caused by background `GLib.timeout_add` timers on destroyed copy buttons in `CodeBlock` and message rows.

## [0.3.1] - 2026-08-24

### Added
- **Native GTK3 System & Dark/Light Theming**: Full 3-way theme switching (`System Default`, `Dark (Black)`, and `Light`) with 1-click header toolbar toggle and Settings Dialog dropdown; dynamically pairs installed system dark themes (`Yaru-dark`, `Adwaita-dark`) with symbolic `@theme_*` palette variables and automatic Pygments syntax highlighting (`monokai` on dark, `friendly` on light) derived from background relative luminance.
- Planning state is now durable per saved chat session through the harness `SqlitePlanStore`, co-located with `chat_sessions.db`; plans survive turns, restarts, and agent/provider live-swaps, while ungrouped runs remain in memory and session delete/clear/prune operations cascade plan rows.
- Automatic and manual compaction now preserve the complete pre-compaction transcript—including emitted `ThinkingPart` reasoning—in unbounded StepPersistence snapshots inside the same user-exported database, so compacted session history remains usable without sacrificing fine-tuning data.
- A separate, manually selected Planner agent now shares the current chat history and durable plan store with the GRC executor. Its model-visible surface is structurally read-only (`PrepareTools` plus only `write_plan`/`read_plan`), its reasoning/tool activity persists under the `grc_planner` identity, and a compact `Plan` toggle supports both empty-session planning and explicit mid-session plan revision.
- Successful planner turns that call `write_plan` now produce an in-chat `Implement the Plan` action after the UI verifies the session's `SqlitePlanStore` is non-empty. Clicking it automatically flips to GRC-Agent and sends a visible implementation request; merely finishing a response or reading a plan does not trigger the action, and execution never starts without the click.

### Changed
- Color-coded `Agent` (Blue `#3584e4`) / `Planner` (Orange `#e66100`) toggle button with clear mode indicators replacing the old switch.
- Streamlined project selector ("Browse" button with downward menu expansion) and moved "Delete all sessions" action inside recent sessions view.
- The GTK chat welcome screen is denser and sidebar-safe: smaller one-row quick prompts, two-line ellipsized recent-session rows, long-name width bounds, and clearer composer/message boundaries reduce the measured minimum width from 562 px to 472 px while preserving full details in tooltips.
- Session durability tests are named for the active StepPersistence architecture (`test_session_persistence*.py`), and the known-issues document now contains only unresolved defects instead of retaining fixed provider and indexing history.
- The main GRC executor no longer has any planning capability or planning tools. Approved plans are injected read-only from `SqlitePlanStore` through a cache-safe ephemeral `SystemReminders` handoff; switching back from Planner mode never auto-executes.
- Planner mode and manual compaction now live beside the Context readout under the composer instead of in the top toolbar. A native switch is paired with an explicit `Planner active`/`GRC-Agent Active` label; the old refresh-icon compaction affordance is replaced by a `Compact` text button with a default-No confirmation explaining summarization and retained transcript snapshots.
- The composer is taller (64px minimum, growing to 160px), and the header now presents active graph plus provider/model in flexible bordered badges with middle ellipsis and full tooltips. The Context row also displays Pydantic AI's native persisted USD cost for the latest turn (aggregating all model requests around tool calls) when every response is priceable, or `Cost: NA` when the framework returns `None`; no local price table or partial estimate is used.

### Fixed
- Code blocks collapsed to a 46px porthole — height-pinned scroll area fixed with clean vertical expansion and Pygments dynamic token styling.
- Provider failures now surface the real cause: turn errors extract the provider's JSON error message from the httpx response/body chain (e.g. "Invalid API key provided" instead of a bare status line), and a missing API key for the configured cloud provider is caught before the turn with a clear "Open Preferences (Ctrl+,) to configure" message instead of a confusing model error. The model-build error from startup/live-swap is carried into the sidebar and shown when a turn is attempted.
- Near-100% CPU during long reasoning streams: every `ThinkingPartDelta` tried to close a nonexistent text part, which force-flushed and laid out the entire growing thought on every delta (with the reciprocal defect on text deltas). Stream accumulation is now chunked and append-only, nonexistent parts are true no-ops, collapsed thinking does no hidden GTK layout work until close, and expanded thinking refreshes at 4 Hz. A deterministic 196,608-character reproduction dropped from 20.7 CPU-seconds to 0.057 seconds while preserving every character.
- Removed the misleading tok/s display. Pydantic AI 2.31 exposes native token counts and cost but no generation-duration/throughput metric; streamed `ModelResponse.timestamp` records first-chunk time for the OpenAI-compatible path, not stream completion. The previous calculation divided reasoning-inclusive Ollama output by roughly TTFT, producing impossible values such as 3,381 tok/s.
- Thinking-only provider-limit failures no longer poison active history with unusable repetition. A structurally identified `finish_reason='length'` + thinking-only response is archived losslessly in a session-scoped StepPersistence snapshot, removed from active context, and replaced with an actionable error. Executor reminders now reinforce exact tool-grounded schema lookup instead of schema recall from memory.
- Transcript copying is reliable after turn completion: only the temporary streaming row is rebuilt, older selections and transcript focus survive, explicit `Copy` text buttons copy complete messages, table cells are selectable, and dragging across a markdown link no longer opens it. Cross-widget selection remains a GTK limitation, so the per-message Copy action is the complete-message path.

## [0.3.0] - 2026-08-19

### Added
- **Filesystem tools** (`fs_tools.py`, a harness `FileSystemToolset` subclass — pydantic-ai-harness 0.23): eight sandboxed tools (`read_file`, `write_file`, `edit_file`, `list_directory`, `search_files`, `find_files`, `create_directory`, `file_info`) scoped to the active flowgraph's project folder, re-resolved per tool call (tab switches and saves are followed; unsaved tabs gate with a clear "save first" error). `.grc` files never reach the model as raw XML — `read_file` routes them through the structural `inspect_graph` engine (active file from the live in-memory graph, others headlessly) — and can never be written: `change_graph` owns all flowgraph edits, and one uniform name rule (case-insensitive `.grc`, covering `.GRC` and `.grc~` backups) drives both read routing and the write gate, which is re-checked against the symlink-resolved target. `write_file`/`edit_file` accept source/config formats only (`.py .cmake .txt .md .m .json .yml/.yaml C/C++ .xml .conf .rst .i` — OOT-module-ready), write atomically (temp → fsync → rename) with `expected_hash` conflict detection, and require an existing parent directory. Secret/config paths denied outright at root and nested (`.env`/`.env.*`/`.envrc`/`.grc_agent/`/`.git/`), on top of the harness-protected defaults. Reads capped at 1000 lines, listings at 200 entries.
- **Indirect prompt-injection defense** (`PromptInjectionDefender`, `stackone-defender` tier-1 pattern detection over every client-executed tool result): the agent ingests untrusted text (project files, web content) and can write files, so a high/critical-risk result is withheld and replaced with a short notice; flags are logged. Live-verified end-to-end: an injection payload planted in a project file — via `read_file` or surfaced by a `search_files` grep — never reaches the model's message history.
- **Oversized tool outputs spill losslessly** (`ToolOutputLimits`, default `Spill(then=Truncate())` band at 10k chars): the full payload is persisted under `.grc_agent/tool_overflow` (0700, alongside the chat DB) and replaced with a handle + bounded head/tail preview; a new `read_tool_result` tool reads slices back on demand (`offset`/`limit`/`from_end`/`pattern`). A 20k-char tool return no longer re-floods context on every later request, and nothing is silently dropped.
- **Twelve-provider support, live-swappable**: Ollama (local) and Ollama Cloud, OpenRouter, OpenAI, any OpenAI-compatible endpoint, plus pydantic-ai's dedicated native classes for Anthropic (Claude), Google (Gemini), Groq, Mistral, Cohere, and xAI (Grok) — and ChatGPT Plus/Pro (Codex) via OAuth. Legacy `.env` provider values are normalized on load; each provider has its own model env var and API-key var, fixed-endpoint providers show their URL read-only, and the Settings dialog's model Load button lists what the backend actually serves for every provider. Changes apply immediately on Save (agent rebuilt in place, chat history preserved), with the active provider shown live in a toolbar badge; a model the backend doesn't list (or an unreachable backend) is a non-blocking status-bar warning backed by one bounded HTTP probe — never a modal popup.
- **Adopted pydantic-ai-harness capabilities in place of hand-rolled layers**: `StepPersistence` on a `SqliteStepStore` co-located with the chat DB (per-run events and full-history snapshots at every settled tool boundary — crash-mid-turn resume points, session-scoped cascade cleanup) and the `Planning` capability (`write_plan`/`read_plan`/task tools with a cache-safe reminder tail). The old `trace.py`/`turn_traces` layer is gone.
- **Compaction targets the model's REAL context window**: the trigger threshold is 85% of the window probed from the backend itself (Ollama `/api/show` `num_ctx`, OpenRouter/OpenAI `/v1/models`, Codex `context_window`) instead of hardcoded 24k/96k guesses; the pricing registry remains only a fallback. `GRC_COMPACTION_TARGET_TOKENS` is the absolute override.
- **Consolidated search architecture (RAG packaging)**: knowledge-base search is two first-class options — default **Lexical Search** (instant SQLite FTS5/BM25 keyword search, zero external dependencies or background processes) and **Local Vector Search** (in-tree `llama.cpp` + `EmbeddingGemma-300M-QAT` over a private UNIX socket, one-click install from Settings, nothing system-wide). Each backend gets its own database file (never one model's vectors in another's index), vector ingest is all-or-nothing (a failed embed never leaves a silently partial index), and an unreachable embeddings backend falls back to lexical search with a clear status message.
- `ResilientSummarizingCompaction` and a manual `Compact` action: summarizes aging turns when approaching context limits while preserving all user prompts (`keep_user_messages=True`) and degrading gracefully (history unchanged) if summarization fails — verified against the harness source, a summary failure escalates to the zero-LLM sliding-window tier instead of failing the turn.
- `ConversationSearch` over `SnapshotHistorySource(store)`: the agent can search prior turn snapshots even after context compaction.
- Continuous prose grouping in `MarkdownView`: contiguous markdown paragraphs, headings, and lists stream into a single `Gtk.TextBuffer` for unified selection and natural paragraph spacing.

### Changed
- Role-appropriate planning, filesystem, and web tools surface through harness capabilities — the app now rides `pydantic-ai-harness` 0.23 (upgraded from 0.21 for the `list_directory` result cap and symlink-hardened walker authorization).
- Unbounded snapshot retention (`max_snapshots_per_run=None`) across all settled tool boundaries so `ConversationSearch` always has a pre-compaction snapshot.
- Settings dialog simplified to the two search backends with inline status and one-click installation triggers.
- Fast test suite reorganized from a 5056-line `test_unit.py` god file into a clustered minimal suite (adapter graph/layout/RAG, sidebar, canvas, factory, sessions, fs tools, injection defense).

### Fixed
- `ClearToolResults` evicted small mid-turn answers (a ~100-token catalog answer was blanked within a tool call or two, making the model re-ask the same question up to 18 times): now keeps the last 3 tool-call/return pairs and only clears when the clearable set reclaims at least 2000 tokens.
- `Ctrl+,` crashed on every keypress (`Gdk.KEY_Comma` does not exist in GTK3).
- Ghost vertical gaps below chat markdown paragraphs in GTK3: unallocated initial listbox widths (`allocated_width <= 1`) forced a 160px wrap calculation and excessive height allocations.
- Widget fragmentation in markdown rendering: specialized containers (`CodeBlock`, `TableBlock`) stay distinct while contiguous plain prose consolidates.
- Filesystem sandbox gaps found by two adversarial audits and re-verified by a third independent auditor (all closed with live repros, no regressions): nested secret files readable, `.grc` write bypass via in-root symlink, case-variant `.GRC`/`.grc~` raw-source leaks, ModelRetry text interpolating arbitrary file content, and missing-`.grc` reads reporting a parse error instead of File-not-found.

## [0.2.0] - 2026-08-17

### Added
- `save_block` tool: exports an existing Embedded Python Block (`epy_block`) instance into GNU Radio's native hier-block library (`~/.grc_gnuradio`) as a standalone, reusable catalog block — available to `change_graph` in this flowgraph or any other. Not an out-of-tree (OOT) module; the current flowgraph's own `epy_block` instance is left untouched.
- Tiered context compaction via `pydantic-ai-harness`'s `TieredCompaction` capability: when a conversation approaches the context budget, bulky older tool-return contents (e.g. `inspect_graph` JSON payloads, `generate_python` previews) are cleared to a short placeholder first — keeping the last 2 tool-call/return pairs intact — and only if that isn't enough does a sliding window trim the oldest dialogue (preserving the first user message). Target threshold defaults to 24k tokens for local Ollama models (~75% of a 32k window) and 96k for cloud providers, overridable via `GRC_COMPACTION_TARGET_TOKENS`. Per-turn reasoning traces in `turn_traces` are unaffected — they record the full uncompacted events in memory.
- System prompt now teaches a native runtime-verification strategy: wire a diagnostic block (`blocks_probe_rate` → `blocks_message_debug`, or a signal-magnitude probe) into a flowgraph before asking the user to run it, then read `get_run_log` — since a zero exit code doesn't guarantee correct output.
- System prompt now nudges the agent to consult `query_knowledge` (catalog domain) for casual/symptom-described requests (e.g. "remove the gaps", "make it smoother") before concluding no fix exists.
- ChatGPT Plus/Pro (Codex) as a third provider: OAuth sign-in with PKCE against `auth.openai.com` (loopback callback on port 1455 with a manual-paste fallback), tokens stored 0600 under `~/.config/grc_agent/openai-codex-auth.json` (never in `.env`), a thin `OpenAIResponsesModel` subclass that only adds the OAuth bearer, the three Codex headers, and a subscription-limit error taxonomy, and reasoning summaries requested so thinking parts populate the trace.
- Optional local llama.cpp + EmbeddingGemma embedding runtime (`embed_runtime.py` + Settings dialog provisioning): downloads a pinned llama.cpp build and the EmbeddingGemma GGUF into `~/.local/share/grc-agent` (hash-verified, path-traversal-safe extraction, glibc/musl platform gate before download), serves `/v1/embeddings` on a UNIX socket in a 0700 directory, and is stopped on app exit. Nothing is installed system-wide; an existing `llama-server` on `PATH` is reused.
- Model picker in Settings: an editable dropdown with a Load button that lists the models the configured backend actually serves (Ollama `/api/tags`, OpenAI `/v1/models`, ChatGPT `/codex/models`) — no more guessing model ids by hand.
- Context label now shows `tokens / context-window` (resolved per provider) plus the last turn's generation rate in tok/s, taken from the persisted trace row so the displayed and stored numbers cannot disagree.
- Ubuntu 26.04 / Python 3.14 support: `event_loop.py` picks PyGObject's in-tree `gi.events` (>= 3.50) and falls back to `gbulb` (Ubuntu 24.04), with CI running a 24.04 + 26.04 matrix.
- `docs/known-issues.md`: defects found in review, each with a verified observation and a proposed fix.

### Changed
- New dependency: `pydantic-ai-harness>=0.21.0` (tiered context compaction capability).
- The embeddings backend is now chosen independently of the chat provider (`GRC_EMBED_BACKEND`: `auto` | `ollama` | `llamacpp` | `openai_compatible`) — a chat endpoint that speaks the OpenAI API need not serve `/v1/embeddings`, and coupling the two silently degraded `query_knowledge` to lexical search. Vector-DB filenames are keyed on the backend so switching never queries one model's index with another model's vectors.
- `change_graph`'s `add_blocks` phase now relays out the *entire* flowgraph from scratch on every batch that adds a block, not just the new block: variables/options/imports/snippets pack into an alphabetically-sorted header band (options pinned first), everything else flows below via rank-ordered placement. Fixes new variables landing mid-signal-path (they never had wire-connection neighbors to anchor from). Only ever triggered by `change_graph`; manual canvas edits are untouched.
- Integration scenario harness (`agent.py`'s `check_expect`) gained a mode-agnostic `tools_called` expectation field, generalized from the previous hardcoded read-tool-name check, so any scenario can assert a specific tool was actually invoked by the model.

### Fixed
- The status line's tok/s rate was computed from the LAST model response's output tokens over the WHOLE turn's wall time — undercounting multi-request turns (each response's output was dropped) and tool-heavy turns (tool-call latency inflated the denominator). It now uses the run's own aggregated output tokens (pydantic-ai's `run.usage`, which sums every request in the turn) minus hidden reasoning tokens, over the time the model was actually generating — computed **natively** from pydantic-ai's own `ModelRequest`/`ModelResponse` high-precision timestamps (the delta per request/response pair; tool execution happens between pairs and is excluded by construction; `result.new_messages()` scopes it to this run only, so prior turns can't leak in). The trace row's `output_tokens`/`reasoning_tokens`/`total_tokens` also come from `run.usage`, and `generation_ms` is a new `turn_traces` column (schema v2→v3 migration, existing rows default to 0) so the displayed rate and the persisted row can never disagree.
- `save_block_to_library` no longer accepts a `gui_platform` parameter that rebuilt the GNU Radio block registry a second, redundant time on every successful live save — `NativeFlowgraphProxy.save_block()` already calls `NativeCanvasManager.reload_block_library()` afterward, which rebuilds it (and refreshes the visible block panel) on its own.
- Silently partial vector indexes: a single mid-build embed failure used to leave a `vec0` table over a fraction of the corpus that still reported `search_mode: "vector"`. The index is now all-or-nothing; any failure discards the partial embeddings and builds lexical-only.
- The EmbeddingGemma task prefix was applied to every backend (it keyed on `provider != "openrouter"`, a string `load_settings()` can no longer return). It now keys on the resolved embedding model, applied identically at ingest and query.
- Crash on content-free `ThinkingPartDelta`s (Codex emits them around its reasoning summaries): `content_delta` is Optional and appending `None` killed the turn.
- `change_graph`'s rollback could itself raise on GNU Radio 3.10.12 (whose `import_data` calls `validate()` itself), replacing the structured error with a traceback; reverts now go through `_revert_flow_graph`, which reports a `rollback_failed` error instead of raising.
- Two ChatGPT sign-in races: the callback server now binds both loopback families (a dual-stack `localhost` resolves to `::1` first), and `_cancel_task` never cancels the task it runs in (which killed its own token exchange mid-flight).
- Settings dialog `show_all()` no longer re-shows widgets the per-provider sync had hidden (the Ollama Cloud checkbox leaked into the OpenAI-compatible view).
- `ensure_server()` is serialized: concurrent cold-start embedding fans-out used to race several llama-servers onto one socket and the survivor answered every request "unauthorized".

### Removed
- `_find_block_placement`, the old per-new-block spiral-search placement function, deleted outright now that `compute_full_layout` replaced its only caller.

## [0.1.5] - 2026-08-15

### Added
- Universal OpenAI-compatible backend support (`OpenAIChatModel` + `OpenAIProvider`) covering OpenRouter, llama.cpp / llama-server, vLLM, LM Studio, OpenAI, Groq, and custom endpoints.
- Settings dialog options for Ollama Cloud toggle (automatic `https://ollama.com/v1` endpoint handling and key management) and default local Ollama URL (`http://localhost:11434`) with optional customization.

### Changed
- Consolidated provider configuration into two unified native categories: `ollama` (local / cloud) and `openai_compatible` (OpenRouter, llama.cpp, vLLM, etc.).
- Upgraded dependencies to latest releases, including `pydantic-ai` 2.31.0 and `openai` 3.1.0.
- Streamlined RAG embedding client and SQLite FTS5 lexical fallback logic to seamlessly handle the consolidated endpoints.

## [0.1.4] - 2026-08-14

### Fixed
- Fixed an `UnboundLocalError` on `dst_port` in `change_graph` (Phase 7) when looking up non-existent destination ports or blocks, which previously caused the mutation to fail with an internal Python error instead of returning clear, actionable port connection diagnostic messages.
- Fixed session lockout caused by saving unfulfilled `ToolCallPart` instances into `_message_history` when turns failed or were cancelled mid-stream. Added `_clean_message_history_for_new_turn()` to strip trailing unprocessed tool calls before new agent runs, auto-repairing existing corrupted sessions and preventing PydanticAI `UserError: Cannot provide a new user prompt when the message history contains unprocessed tool calls.`.

## [0.1.3] - 2026-08-14

### Added
- Per-turn reasoning traces: every agent turn (success, abort, or error) is now
  recorded as a row in a new `turn_traces` SQLite table — run/conversation ids,
  provider/model/base_url snapshot at turn start, system-prompt hash, user
  prompt, origin flowgraph, timings, the ordered event stream (part starts,
  tool calls with args, tool results, errors), final output, and per-turn token
  usage (input/output/reasoning/total). Traces cascade-delete with their
  session and can never resurrect after a Clear History.
- WAL journal mode, `busy_timeout`, and `foreign_keys` pragmas on every
  chat-DB connection — session/trace writes (worker threads) can no longer
  hit "database is locked" against main-loop reads.
- Versioned DB schema: a `_meta(schema_version)` table with ordered, idempotent
  migrations (v1 adds the `first_message` column, v2 adds `turn_traces`);
  existing databases migrate in place on first launch.
- Two new hermetic test suites: `tests/test_session_traces.py` (23 tests) and
  `tests/test_session_traces_advanced.py` (16 tests — real pydantic-ai
  `TestModel` agent runs, multi-thread concurrency stress, Unicode/large-blob
  integrity, v1→v2 migration, and end-to-end ChatSidebar → trace-row coverage).
- CI now installs xvfb and runs the session/trace suites alongside the unit
  tests.

### Changed
- Message-history serialization now uses pydantic-ai's builtin
  `ModelMessagesTypeAdapter.dump_json`/`validate_json` (single step, ~9%
  smaller output) instead of the redundant `json.dumps(to_jsonable_python(...))`
  double conversion; `ThinkingPart` reasoning, tool parts, usage, and
  run/conversation ids all round-trip exactly.
- The recent-sessions list reads a `first_message` column populated at save
  time instead of re-deserializing each row's full messages blob.
- Removed the one-time legacy `chat_sessions.db` path-migration code
  (the relocation ran for all existing installs long ago).

### Fixed
- Hardened `init_db()` against concurrent first-run initialization with a
  threading lock, and the v0→v1 migration now survives a crash between the
  `ALTER TABLE` (auto-committed) and the `first_message` backfill
  (the backfill is idempotent and re-runs on the next launch).
- Recorder/trace wiring no longer feeds the trace recorder before the GTK
  handlers, and a cancellation during the final trace save can no longer skip
  the chat UI's busy-state cleanup.

## [0.1.2] - 2026-08-01

### Added
- Chat-to-canvas block highlighting: when an agent message mentions a flowgraph
  block by name, it now renders as a rounded pill badge in the chat. Hovering
  a badge outlines the corresponding block on the GRC canvas with a blue
  border overlay (drawn via a second `draw` handler on GRC's own
  `DrawingArea`, independent of GRC's native selection highlighting, which
  gets reset on every canvas action); clicking a badge scrolls the canvas to
  center that block. Prose markdown (paragraphs, lists, headings) now renders into a
  `Gtk.TextView` with `GtkTextChildAnchor`-embedded badge widgets, replacing the
  previous Pango-markup label path; code blocks and tables are unaffected.
- OpenAI-compatible local server provider support (e.g., llama.cpp / vLLM) in
  `settings.py`, `agent_factory.py`, and Preferences UI with custom base URL and
  reasoning toggle configuration.
- SQLite chat session persistence fallback for unsaved flowgraph tabs (`untitled:<page_title>`).
- Persisting complete intermediate tool call history and error traces in `chat_sessions.db` on failed or aborted turns.
- Real-time active context token usage label updates during streaming turns, featuring output and reasoning token breakdown tooltips.
- System prompt connection validation guidance for GNU Radio stream fan-in (strictly 1 source per sink port) and vector itemsize matching.
- Keyboard numeric keypad `Ctrl+0` (`Gdk.KEY_KP_0`) zoom reset support.

### Fixed
- Fixed tool expander jump-scrolling by connecting a `notify::expanded` listener that pauses `_auto_scroll` upon click.
- Fixed thinking box readability by increasing max content height (from 250px to 500px), enlarging typography (1.0em), and expanding padding.
- Enhanced `_format_turn_error()` to unpack structured JSON/dict error bodies returned by API providers (such as OpenRouter HTTP 403 quota limits) and expose underlying exception causes.
- Fixed HTTP transport retry configuration in `_retrying_http_client()` by catching all `httpx.TransportError` subtypes.
- Fixed agent message bubbles collapsing to a one-word-per-line column.
  `Gtk.TextView` (unlike the `Gtk.Label` it replaced for prose rendering)
  doesn't self-report a usable natural width for word-wrapped content, which
  broke the message bubble's "hug the content" sizing. Bubbles now measure
  their actual text via Pango and clamp to the available column width, and
  re-clamp automatically on the sidebar's next layout pass.

## [0.1.1] - 2026-07-22

### Added
- Native agent context usage indicator under text input box displaying exact input context tokens and dynamic provider-reported maximum model context limits.
- Dynamic API model context resolution (`resolve_model_context_length`) querying `/api/show` (Ollama / Ollama Cloud) and `/api/v1/models` (OpenRouter) with zero hardcoded lookup tables.

### Fixed
- Fixed block layout wire criss-crossing and backward loops by enforcing topological rank sorting on `add_blocks` and `min_allowed_x` downstream placement boundaries in `layout.py`.
- Fixed thinking expander sizing to expand 100% width and label transition ("Thinking..." -> "Thinked").
- Fixed quick prompt chip handler and recent sessions list rendering in Welcome Screen.
- Attached `_graph_modified_since_last_run` warning to `get_run_log` when called post-edit before a fresh run.

## [0.1.0] - 2026-07-18

### Added
- SQLite FTS5/BM25 lexical fallback for `query_knowledge` (catalog and docs
  domains): when the embedding backend is unreachable, results now come from
  a local keyword search instead of a hard failure — including on a cold
  cache where embeddings were never reachable at all. Every result is
  tagged `search_mode: "vector" | "lexical"`, never silent.
- Real, non-mocked Ollama Cloud integration tests covering the lexical
  fallback end-to-end, plus a new scenario (`23_lexical_conjugate_insert`)
  exercising the full agent loop under a genuine embedding-backend outage.
- A `on_sync_failed` callback surfacing previously log-only manual-edit
  auto-save failures through the sidebar's status bar.
- `query_knowledge` now takes a model-controlled `k` parameter (how many
  results to return; default 5, clamped 1-20) instead of a fixed count, so
  the agent can widen or narrow recall per query.
- `CHANGELOG.md` (this file).

### Fixed
- The 1.5s canvas safety-net poll no longer re-serializes the entire
  flowgraph on every tick — gated behind a cheap check of GRC's own
  undo/redo `state_cache`, with a periodic backstop covering the two edit
  paths that bypass it (found via adversarial testing: an undo-then-edit
  tuple collision, and block-library drag-and-drop/Variable Editor
  add-remove).
- `ollama_cloud` with no API key configured used to silently proceed with a
  placeholder credential and only fail on the first real chat call;
  `agent_factory.py` now raises explicitly, degrading the same way
  `openrouter` already did.
- An unreadable `.env` (e.g. permission error) is now caught by the same
  fallback path as a bad model config, instead of crashing at startup.
- GNU Radio failing to load in `build_app()` (e.g. not installed, or a venv
  created without `--system-site-packages`) now shows a native GTK error
  dialog with a specific remediation hint, instead of a raw traceback.
- Several narrow, previously-silent failure paths in `chat_sidebar.py` (a
  Settings-dialog save failure, corrupted/locked session-DB reads on
  tab-switch, an unrecoverable stuck "sending fix" UI state) now surface
  through the existing status-bar/logging mechanisms instead of failing
  invisibly.
- The RAG embedding client had no request timeout (SDK default allowed up
  to ~30 minutes worst-case); now bounded to the same order of magnitude as
  the chat-model client.
- An adversarially long/repetitive `query_knowledge` query could stall the
  lexical fallback for tens of seconds; the FTS5 match expression is now
  deduplicated and capped.
- CI's test step referenced `tests/test_web_app.py`, deleted since the
  native-GTK3 rewrite — corrected to the actual current test suite.
- Fixed a native-method inconsistency in `change_graph`'s duplicate-name
  check (manual scan → `flow_graph.get_block()`, matching every other
  lookup in the file).
- `AGENTS.md` updated in several places where documentation had drifted
  from actual behavior (the RAG lexical fallback, the poll's state-cache
  gate, `after_agent_edit()`'s scope, the exact `dotenv` API used).

### Changed
- `pydantic-ai`, `pydantic-graph` (fast-moving, used directly and deeply)
  and `sqlite-vec` (still pre-1.0) now have upper version bounds.
- CI and the documented local dev setup both use `uv sync --locked`
  (stricter than the previous `--frozen` — also catches a `uv.lock` that's
  drifted out of sync with `pyproject.toml`).
- README's GNU Radio version claim tightened to reflect what's actually
  tested (3.10.x via CI) rather than an unverified "3.10+".

### Removed
- `docs/codebase_audit_report.md` — a point-in-time code-quality audit.
  Every finding in it was individually re-verified against the current tree
  (18 of 19 fixed and confirmed via passing regression tests; the one
  remaining item was already documented, accepted debt) before removal;
  the full re-verification is recorded in `docs/efficiency_audit.md`.

### Architecture
- This is a GUI-only application by explicit design going forward — no CLI
  surface (no subcommands, no `--check`/`--doctor`, no `argparse`). Startup
  diagnostics are handled inside the GUI itself. Documented as a permanent
  rule in `AGENTS.md`.
