# Changelog

All notable changes to this project are documented in this file.

The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning starts fresh at `0.1.0` for the current native GTK3 architecture —
earlier `v1.0.0`/`v2.0.0` tags belonged to an unrelated, since-rewritten
web-dashboard codebase and are not part of this history.

## [Unreleased]

Harness lean-out and model-facing tool contract rework, planned in [`docs/plans/2026-09-02-0830-refactor-harness-lean-and-tool-contracts-plan.md`](docs/plans/2026-09-02-0830-refactor-harness-lean-and-tool-contracts-plan.md) and [`docs/plans/2026-09-03-0829-refactor-sidebar-decomposition-review-plan.md`](docs/plans/2026-09-03-0829-refactor-sidebar-decomposition-review-plan.md). Easy-wins backlog cleanup pass, planned in [`docs/plans/2026-09-08-1101-refactor-easy-wins-backlog-cleanup-plan.md`](docs/plans/2026-09-08-1101-refactor-easy-wins-backlog-cleanup-plan.md).

**Measured effect on model-facing surface**: Tool descriptions reduced from 4,058 to 2,179 characters, schemas from 5,932 to 5,743, and system prompt from 5,012 to 4,056 characters. Static per-request context floor dropped from **15,002 to 11,978 characters**. `inspect_graph` payloads are 18–20% smaller and catalog results 20% smaller. Verified end-to-end against all bounded Ollama Cloud benchmark scenarios.

- **Committed behavioral golden for chat sidebar**: Added `tests/test_chat_sidebar_golden.py` to render a fixed Pydantic AI session (text, thinking, tool calls, failures, results) through the real widget tree, pinning transcript structure, tool status markers, and clipboard copy text against committed literals. Verified byte-identical against the pre-split codebase (`1fb1d19`).

### Fixed

- **Spawn-crashed flowgraph runs misreported as clean successes**: GRC's `Executor` emits `send_verbose_exec` + `send_end_exec()` (default code 0) when the run subprocess fails to spawn — byte-identical in the console stream to a legitimate clean exit. `exec_monitor` now snapshots the page-process identity at the start marker; a code-less Done marker with absent-or-unchanged identity (wired provider only) records a spawn failure with an additive `spawn_failed` flag, and run results plus `get_run_log` report `ran_successfully: False` with a spawn note pointing at the retained exception. The return code stays truthful; no-provider, code-carrying, and mid-run tab-switch paths keep legacy behavior.
- **Spawn-crash failure prompt names the spawn**: user-initiated runs that die at process spawn now notify the agent with spawn guidance ("failed to spawn — nothing was executed, read get_run_log") instead of a misleading "return code 0" — the spawn verdict threads through the monitor's failure callback without fabricating a code.
- **Leaked GLib timers in test suite**: Added `destroy()` to `ChatSidebar` to remove armed 60s and 500ms repeating timer sources, and replaced 15 unbounded `Gtk.main_iteration()` loops with bounded helper drains to prevent order-dependent test hangs.
- **Screen CSS pollution in desktop tests**: Stubbed `_apply_global_css` in `test_desktop_app.py` fatal-error tests to prevent process-wide `Gtk.CssProvider` leaks from overriding widget-scoped zoom styling in subsequent tests.
- **Attach dialog test lookup collision**: Changed file chooser detection from `isinstance(w, Gtk.FileChooserDialog)` to `type(w) is Gtk.FileChooserDialog` to avoid matching GRC's `SaveFlowGraph` subclass; added `pytest-reverse` to dev dependencies.
- **Streaming tool success indicator**: Corrected `_set_tool_result` to thread `ok=(outcome != "failed")` from streaming events in `_stream_tools` and `_on_part_start`, rather than unconditionally displaying success checkmarks.
- **Transcript tool call ordering**: Added `replace_chunk` to `_ChunkAccumulator` to patch streaming tool calls and their results into unified entries in place, preventing transcript copy ordering bugs.
- **Consent approval card rendering**: Updated `format_change_summary` to read `instance_name`/`params` from `change_graph` models instead of obsolete `name`/`param`/`value` keys, rendering accurate block names, parameter diffs, and initial states.
- **Hermetic test gate and clipboard race**: Marked live API tests in `tests/test_isolation.py` with `@pytest.mark.integration`; added explicit skip for clipboard negotiation races in `test_ctrl_v_with_clipboard_image_attaches_png`.
- **Verification of `change_graph` transactions**: Checked `import_data` return values during rollback, removed unsafe disk re-parse fallbacks, latched commits, and truthfully reported `persisted`, `still_invalid`, and `relayout` statuses.
- **Local model context window probing**: Dynamically re-probes and caches backend context limits on the first request rather than permanently defaulting to a conservative 27,200 tokens when the server is cold.
- **Off-loop context probe**: Cached synchronous context-length lookups off the main loop to prevent 3-second UI freezes during sidebar label refreshes.
- **Safe docstring imports**: Replaced `exec()` in `query_knowledge` block docstring resolution with `ast` parsing, allowing only genuine import statements and rejecting unsafe code.
- **Deterministic graph inspection**: Emitted connections in `inspect_graph` in deterministic sorted order instead of arbitrary set order, improving diffing and prompt caching.
- **Non-breaking space normalization**: Extended NBSP cleanup across all argument families, including connection DSL strings.
- **Approval helper re-entrance**: Prevented `_always_approve_all` from running its body twice and double-destroying approval cards.
- **Dual transformation disclosures**: Fixed `if/elif` branch in disclosure reporting so queries that are both token-capped and fall back to lexical search report both notices.
- **Retained copy button on rich re-render**: Prevented `_render_last_message_rich` from wiping the action row containing the message copy button.
- **Consistent reasoning copy text**: Unified mid-stream and post-render thinking block formatting using canonical `<Thinking>` tags via `chat/format.py`.
- **Tracked history saving on cancellation**: Wrapped `CancelledError` history saves with `_track_background_task` to prevent race conditions during chat clearing.
- **Transcript mouse selection**: Restricted `text/uri-list` drag-and-drop target to the composer input area, allowing normal mouse text selection inside the transcript message list; set `.chat-copy-btn` to full opacity.
- **Resilient `write_plan` input coercion**: Added Pydantic `BeforeValidator` to `write_plan` to decode stringified JSON lists, coerce numeric IDs to strings, normalize `name` to `content`, and emit actionable schema `ModelRetry` feedback.
- **Flexible `change_graph` argument ingestion**: Decoded stringified JSON arguments, aliased `block_id` to `id` for block adds/removals, coerced 2-element tuples and dictionaries into connection strings, and wrapped single-item inputs into sequences.
- **Responsive HTTP transport timeouts**: Lowered default HTTP read timeout from 1800s to 120s across providers to fail fast with actionable errors on dropped or overloaded connections.

### Changed

- **Structured `search_mode` tool labels**: Replaced nine literal substring spellings in the `query_knowledge` tool-label helper with one `json.loads` rule reading the parsed payload's `search_mode` field; callers pass the raw result payload (fix at the source), and non-JSON or field-less results render the plain label.
- **Shared provider behavior table**: Added `PROVIDER_BEHAVIORS` to the provider catalog (`ui/providers.py`); the codex thinking-summary label, sign-in gate, and settings preflight read the table instead of scattered `provider == "openai_codex"` comparisons — zero provider string literals remain under `chat/`.
- **Frame-bounded stream flush**: Replaced the four-interval adaptive throttle (0.25/0.066/0.050/0.033 s plus 2,000/5,000-char branches) with one per-turn `add_tick_callback` frame-cadence source that disarms on stream end and sidebar destroy; golden transcript copy text stays byte-identical.
- **Wiki corpus chrome strip and GREP1 consolidation**: 70 of 101 crawl files carried trailing MediaWiki navigation chrome diluting lexical/vector ranking mass; one structure-based rule (cut at the first `## Navigation menu` heading) cleans them. `Coding_guide_impl.md` — live-verified upstream as a 116-byte "replaced by GREP1" pointer stub — now carries the real GREP-0001 coding guidelines. Verified-distinct near-duplicate page pairs untouched; the RAG index auto-rebuilds on corpus fingerprint change.
- **Scenario suite default model**: Integration scenarios default OpenRouter to `dots-studio/dots-3-note-preview:free` (user-directed, free tier accepted knowingly — the recorded `01_add_throttle` double-encoding failure stands; no real-token burn unless needed); `GRC_OPENROUTER_MODEL` override intact.
- **JSON schema bounds and formats**: Moved tool parameter constraints into Pydantic schema annotations (`Field(ge=1, le=20)`, connection string regex `pattern`, and plain lists instead of nullable `anyOf` unions).
- **Standardized tool error reporting**: Replaced `ModelRetry` prose workarounds with `ToolFailed` for unfixable environment faults; added `StopGracefully.max_repeated_failures` (3 strikes); typed dependencies against a GTK-free Protocol.
- **Unified `query_knowledge` return shape**: Standardized catalog and docs search payloads into a consistent structure and filtered GRC Advanced parameters via `param.category`.
- **Offloaded troubleshooting recipes**: Moved SDR udev and TUN/TAP `CAP_NET_ADMIN` setup guides from system prompt into knowledge corpus markdown files (`Flowgraph_Runtime_Permissions.md`, `Verifying_Flowgraph_Behaviour_With_Probes.md`).
- **Streamlined `Planning` capability**: Registered `write_plan` only, removing redundant `read_plan` and manual prompt tool enumerations.
- **Dependency upgrades**: Upgraded `pydantic-ai` to 2.37 and `pydantic-ai-harness` to 0.28 (bounded `<0.29`); assigned provider-specific HTTP clients (`httpx2` default, `httpx` for Groq).
- **Minimal `adapter` public exports**: Reduced `adapter/__init__.py` exports to 22 consumed symbols and migrated build status polling to `rag.build_status()`.
- **Standardized provider preflight names**: Harmonized provider labels (`"Anthropic (Claude)"`, `"xAI (Grok)"`, `"OpenAI API"`) against a single shared table.
- **Decoupled headless chat logic**: Extracted GTK-free formatting, errors, history, and usage accounting into the `chat/` package (`format.py`, `errors.py`, `history.py`, `usage.py`).
- **Consolidated `ChatSidebar` state accessors**: Unified canvas manager access via `_get_cm()`, message queries via `_current_messages()`, and task cancellation via `_track_background_task`.
- **Shifted message history sanitation**: Moved unfulfilled tool call cleanup from turn start to the session load boundary (`_on_recent_session_clicked`).
- **Modularized `ChatSidebar` widget hierarchy**: Split monolithic sidebar into 9 specialized mixins (`StreamViewMixin`, `TranscriptViewMixin`, `ComposerMixin`, `ApprovalsMixin`, `ZoomProjectionMixin`, `SettingsControllerMixin`, `TurnDriverMixin`, `SessionMixin`, `StatusContextMixin`), reducing `chat_sidebar.py` to 943 lines and keeping all modules under 1,000 lines.
- **Unified copy feedback and scroll detection**: Consolidated copy button clipboard handling and tooltip timeouts (1500ms) in `ui/copy_confirm.py`, and unified bottom-scroll checks into a shared helper.
- **OpenRouter integration test defaults**: Defaulted integration scenarios to OpenRouter using `inclusionai/ling-3.0-flash-fin:free` with optional Ollama Cloud selection.
- **Architectural alignment for planning context**: Replaced ad-hoc regex scrapers (`extract_plan_from_text`) and silent history mutations (`_sanitize_history_for_executor`) with source validation and native `TieredCompaction`.

### Removed

- **Scenario benchmark harness from package**: Relocated 547 lines of test scenarios from `src/grc_agent/agent.py` to `tests/scenarios/harness.py`.
- **Write-only undo snapshot stack**: Removed unused `push_undo_snapshot` and `cursor.json` writes while preserving `.grc_agent/backups/` directory.
- **Outdated model context overrides**: Removed manual `_MODEL_WINDOW_OVERRIDES` following upstream fixes in `genai-prices` 0.1.6.
- **Dead code across 8 modules**: Deleted unused view/mode parameters, obsolete widget helpers, duplicate CSS themes, and test-only branches.
- **Write-only active-graph state**: Removed unused `_active_graph_name`, `_active_graph_path`, and `set_active_graph`.

### Notes

- Verified audit non-issues: Retained `get_theme_mode`/`set_theme_mode`, documented `ingest`<->`rag` circular import, and kept `gbulb` for Python 3.12/PyGObject 3.48 support.
- Updated `AGENTS.md`: Documented `ModelRetry` vs `ToolFailed` contracts, tool schema invariants, and headless xvfb test gates.
- Sidebar decomposition verified: All extractions audited with zero semantic deviation, validated against pre-split tree at `1fb1d19` via byte-identical golden tests.

## [0.6.0] - 2026-09-01

### Added

- **Agent-side flowgraph save (`save_graph`)**: Enabled the agent to save active flowgraphs directly (untitled graphs auto-named in project directory with collision handling and options-id sync; titled graphs saved in place). Implemented atomic writes via temp file, fsync, and flock. Added pre-flight validation (project dir, open tabs, read-only paths) and updated unsaved run prompts to self-serve via `save_graph`.
- **Canvas-to-chat zoom sync**: Unified zoom tracking through GRC's `_set_zoom_factor`, scaling sidebar typography proportionally via `sqrt(zoom)` clamped to 0.7–1.8× in scoped CSS. Supports tab switches, preserves scroll anchoring, and allows Ctrl+scroll over chat to zoom canvas without feedback loops.
- **Reliable composer image input**: Replaced portal-dependent `FileChooserNative` with in-app `Gtk.FileChooserDialog` supporting multi-select and image filters. Added Ctrl+V clipboard image paste with pending attachment preview.
- **TUN/TAP privilege-failure guidance**: Added system prompt guidance and README documentation attributing `tun_alloc`/`TUNSETIFF` EPERM to missing `CAP_NET_ADMIN`, directing users to safe one-time interface pre-creation (`sudo ip tuntap add dev tap0 mode tap user $USER`).
- **Chat image input (multimodal user prompts)**: Added paperclip image chooser (png/jpeg/gif/webp), drag-and-drop support, thumbnail chips, and native `BinaryContent` dispatch for vision models. Updated `read_file` to return images as `BinaryContent`, verified base64 SQLite persistence, and optimized decoding via `PixbufLoader` target scaling.
- **Offline knowledge corpus expansion & cleanup (`docs/wiki_gnuradio_org/`)**: Crawled 18 new wiki pages (UHD USRP, PlutoSDR, Costas Loop, Symbol Sync, Correlation Estimator, QT GUI sinks, etc.) and replaced Message Passing / Tagged Stream guides. Cleaned MediaWiki artifacts, demoted duplicate H1 headings, and rebuilt hybrid vector/FTS5 database across 670 chunks.
- **Option-based RAG installation instructions in `README.md`**: Documented setup for Option 1 (Lexical FTS5) vs Option 2 (Local Vector Search Hybrid RAG via bundled llama.cpp and EmbeddingGemma).
- **`hermes-subagent` project skill**: Added in-repo skill definition for online research persona in `.agents/skills/hermes-subagent/`.
- **Structured product backlog tracks**: Reorganized `docs/backlog.md` into 5 capability tracks (Visual Inspection, Stream Visualization, File-RAG, Corpus Expansion, Platform Hardening).
- **Dynamic thinking expander streaming & auto-collapse**: Auto-expands and scrolls `ThinkingPartDelta` reasoning streams; auto-collapses on completion and labels as "Thought" or "Thought summary (Codex)".
- **Taller reasoning viewport**: Expanded thinking widget height bounds in `ChatSidebar` (min 200px, max 750px) to prevent early scrollbars.
- **Integration test isolation in pytest**: Configured `addopts = "-ra -m 'not integration'"` and registered `integration` marker in `pyproject.toml` to separate live-LLM suites from default test runs.
- **Native Wayland startup advisory**: Added preflight detection for native Wayland sessions, showing status-bar recommendations (`GDK_BACKEND=x11`) to avoid dropped GTK3 menu grabs.
- **ChatGPT (Codex) reasoning summary expander label**: Tailored thinking labels for `openai_codex` provider (`Thinking (summary)...` -> `Thought summary (Codex)`).
- **`no_gui` flowgraph external terminal logging annotation**: Annotated `run_flowgraph` and `get_run_log` results with `generate_options='no_gui'` and terminal notice when logs route to an external wrapper.
- **Validation-gate error attribution**: Isolated pre-existing flowgraph errors prior to mutations so `change_graph` and `validate_flowgraph_state` only raise `ModelRetry` for errors introduced by the agent's own edits.
- **Catalog implementation docstrings in `query_knowledge`**: Extracted SWIG/C++ implementation class docstrings via block code templates into catalog payloads to expose units and parameter semantics offline (`catalog-docstrings-v2`).
- **Hybrid retrieval (Reciprocal Rank Fusion)**: Fused vector (vec0) and lexical (FTS5) rankings using RRF ($k=60$) when both indexes exist, tagging `search_mode: "hybrid"` with combined scoring.

### Changed

- **Default Ollama Cloud model**: Updated default to `deepseek-v4-flash:0731` across settings, fallbacks, and integration tests.
- **Scientific rules & commandments rewrite of `AGENTS.md`**: Structured `AGENTS.md` around empirical verification, standard library usage, single-branch git workflows, and native GRC invariants.
- **C++ catalog block priority & EPB NumPy slice vectorization**: Mandated standard C++ VOLK-vectorized catalog blocks by default and required NumPy slice vectorization in Embedded Python Blocks (`epy_block`).
- **Streamlined system prompts and tool docstrings**: Reduced prompt and tool docstring footprints by ~42%, eliminating parameter duplication, hardcoded command lists, and aligning sandbox descriptions.
- **SDR hardware permissions streamlined**: Recommended package udev rule reloads (`uhd-host`, `rtl-sdr`, `hackrf`) over broad group additions.
- **Silenced embedding ingestion context truncation warnings**: Reduced `_cap_words` and `fit_to_context` logs from WARNING to DEBUG during vector database ingestion.
- **Indirect prompt-injection defense switched to detect-and-log**: Configured `block_high_risk=False` to log detections without withholding results, preventing false positives on documentation JavaScript boilerplate.
- **Shell timeout: tighter default, truthful schema**: Set default `GRC_SHELL_TIMEOUT` to 120s and dynamically reflected the configured timeout in `run_command`'s schema description.
- **Cancelled-run salvage visibility**: Added warning logs in `_clean_message_history_for_new_turn` when unfulfilled tool calls are discarded.

### Fixed

- **Stale zoom projection after tab switch**: Re-projected foregrounded page zoom upon switching tabs.
- **Composer chooser on Wayland**: Replaced native portal chooser with in-app dialog to prevent silent failures on Wayland.
- **Dedicated Ollama Cloud model slot**: Separated `ollama_cloud_model` setting from local `ollama_model` so provider switches retain distinct model selections.
- **Catalog distance semantics & distance honesty**: Omitted `distance` key on non-vector lexical results instead of emitting misleading `0.0` values.
- **Flowgraph validation retry error message**: Removed misleading `force=True` suggestion from retry messages to prevent unresolvable loops.
- **`output_truncated` probe on docs domain**: Added `extra_limit=1` probe to `query_docs` to accurately report truncation flags under lexical and hybrid search.
- **Truthful `run_command` schema timeout default**: Derived `timeout_seconds` schema description directly from resolved `GRC_SHELL_TIMEOUT`.
- **Variable evaluation noise in catalog rendering**: Temporarily suppressed `gnuradio.grc` logger during catalog rendering to eliminate stderr noise from expected variable evaluation failures; returned `None` for missing block IDs.

## [0.5.0] - 2026-08-26

### Added

- **Unified flowgraph execution tool**: Consolidated start and stop actions into single domain tool [`run_flowgraph(action='start'|'stop', wait=True, timeout_seconds=60.0, stop_after_seconds=...)`](src/grc_agent/agent.py) with conditional approval gating for `start`.
- **Flowgraph execution boundary & shell tool grounding**: Instructed agent to execute flowgraphs exclusively via `run_flowgraph` to ensure live in-memory compilation and console streaming, discouraging stale script execution via shell tools.
- **Bounded-run auto-stop (`stop_after_seconds`)**: Added optional runtime deadline to `run_flowgraph(action='start', wait=True)` that cleanly terminates the flowgraph via SIGTERM after the specified duration, returning `status='stopped_after_timeout'`.

### Removed

- **Removed `search_conversation_history` & `ConversationSearch`**: Eliminated snapshot search indirection in favor of direct message history.
- **Removed `read_tool_result` & `ToolOutputLimits`**: Deleted output spill handles and slice-reading tools.
- **Removed redundant live-cloud compaction test**: Replaced slow cloud test with hermetic test suite.

### Changed

- **Focused System Prompts on 7 Domain Tools**: Centered `prompts.py` on the 7 custom domain tools, relying on automatic JSON schemas instead of manual tool lists.
- **Single Provider-Adaptive Web Search**: Unified `WebSearch` capability across providers with automatic fallback to DuckDuckGo.
- **Dead code removal**: Deleted unused `get_project_dir`/`set_project_dir`, `get_state_lock`, `_compute_ranks`, and `ApprovalCard.get_tool_call_id`.
- **Consolidated preflight endpoint table**: Unified fixed-endpoint provider configurations into `_PREFLIGHT_ENDPOINTS` in `agent_factory.py`.
- **Flowgraph execution boundary deduplicated**: Consolidated flowgraph execution rules across prompt clauses and shell tool descriptions.
- **Planner allowlist and Mode toggle updates**: Added `web_search` to planner tools and clarified Auto mode coverage across flowgraph edits, runs, and shell commands.
- **Documentation drift corrected in `AGENTS.md`**: Updated layout, validity gate, and approval mode documentation to match implementation.
- **Agent-facing schemas compressed**: Reduced tool description footprints from 3,907 to 3,347 characters across `change_graph`, `generate_python`, `query_knowledge`, and `run_flowgraph`.
- **Unified secret resolution (`settings.resolve_key`)**: Centralized environment variable and settings secret lookup logic into a single helper.

### Fixed

- **Block-name badges baseline alignment**: Wrapped anchored `BlockBadge` labels in `Gtk.Box` with 4px top padding, aligning pill text with surrounding TextView prose baselines.
- **Validation gate attribution**: Configured `validate_flowgraph_state` to trigger only on successful tool executions (`outcome == 'success'`) or double-fault rollbacks, preventing retries for pre-existing graph errors or denied actions.
- **Truthful shell timeout schema**: Corrected `run_command` schema to describe real `GRC_SHELL_TIMEOUT` (600s default) rather than harness default 30s.
- **`exec_monitor` state tracking**: Added run `epoch` tracking to `wait_for_run_end` to detect no-op runs and reliably cleared agent-initiated suppression flags.
- **Unified scroll intent tracking**: Bound auto-scroll tracking directly to vadjustment `value-changed` signals, supporting wheel, scrollbar drag, and keyboard navigation while deleting 50 lines of event-specific heuristics.
- **Viewport anchoring on expander toggle**: Compensated vadjustment shifts when expanding thinking or tool containers above the fold, keeping visible content stable.
- **Synchronous scroll child allocation**: Replaced ineffective `_listbox.check_resize()` calls with `self._scrolled.check_resize()` across 6 call sites for reliable allocation measurements.
- **Preserved scroll position on message append**: Dropped forced bottom-scrolling on `_add_message_row` and `_replace_streaming_turn`, adhering to active user scroll position.

## [0.4.0] - 2026-08-26

### Added

- **Flowgraph execution tools (`run_flowgraph`/`stop_flowgraph`)**: Enabled the agent to trigger GRC's native Execute/Stop actions with console output streaming, approval gating on start, and post-run log retrieval via `get_run_log`.
- **Sandboxed shell execution capability (`shell_tools.py`)**: Added project-directory-rooted shell tools (`run_command`, `start_command`, `check_command`, `stop_command`) with denylist security, full-command approval cards, background job management, and API key environment scrubbing.
- **Session-scoped shell prefix-allow**: Allowed users to "Always allow `<command>`" by prefix token for the current chat session without altering persistent global approval settings.
- **Generic approval cards**: Rendered clear approval summaries in `ApprovalCard` (fenced shell commands, flowgraph run intent, structured graph diffs).
- **`adapter.gui_actions()` accessor**: Centralized GRC `Actions` namespace imports in platform-first order to avoid upstream circular dependencies.
- **Untitled graph save defaults to project directory**: Configured Ctrl+S and Save-As on untitled flowgraphs to default to the configured `GRC_PROJECT_DIR`.
- **Per-component Sugiyama layout (`adapter/layout.py`)**: Arranged connected flowgraph components into independent row bands with 8-sweep barycenter crossing minimization and shared `LayoutModel` caching.

### Changed

- **Sugiyama full-canvas auto-arrange**: Replaced shared vertical stack with per-component horizontal bands, ensuring deterministic alphabetical sorting and untangled wiring.

### Fixed

- **Layout crash on skip-layer connections**: Handled grandalf's intermediate `DummyVertex` routing objects safely by using `getattr(v, "data", "")` during layer sorting.
- **Manual edit synchronization after saving untitled graphs**: Re-baselined manual edit tracking on `page.file_path` changes, preventing dropped canvas change detection after initial saves.

## [0.3.2] - 2026-08-25

### Added

- **Direct AST Markdown Renderer**: Replaced intermediate HTML and BeautifulSoup parsing with a direct `markdown-it-py` `SyntaxTreeNode` recursive AST walker into `Gtk.TextBuffer` tags and widgets.
- **Native GTK3 List Typography & Hanging Indents**: Replaced manual whitespace prefixes with `Gtk.TextTag` margin indentation (`left_margin=24`, `indent=-16`) for consistent multi-line list alignment across nesting depths.
- **Normalized Vertical Rhythm & Loose Lists**: Consolidated structural block boundary newlines using GTK tag line spacing (`pixels_above_lines`/`pixels_below_lines`).
- **Search Mode Tool Indicator**: Displayed active search mode in `query_knowledge` tool headers (`vector` vs `lexical`).
- **Stream Pacing & Timing Instrumentation**: Added monotonic timing fields (`queue_wait_ms`, `flush_duration_ms`, etc.) to diagnose streaming performance.
- **Human-in-the-loop flowgraph-change approval**: Integrated Pydantic AI deferred-tool approvals for `change_graph`, presenting in-chat `ApprovalCard` widgets with diffs, reason display, and Manual/Auto mode toggling.
- **Explicit edit reason requirement**: Required a `reason: str` argument on `change_graph` calls to capture edit intent in the transcript.
- **Topology-driven full relayout**: Automatically re-ranked and relaid out the entire flowgraph whenever a mutation altered graph topology.
- **Context-aware port vlen inspection**: Exposed `vlen` on ports when not equal to 1 in `inspect_graph` and catalog results to clarify vector sizing.
- **System prompt guidance improvements**: Added guidance on recovering from failed fixes, consulting external documentation, and QT GUI frequency sink FFT properties.

### Changed

- **Icon-based copy buttons & reduced chrome**: Replaced text swap copy buttons with compact icon buttons on code blocks and messages, narrowing sidebar horizontal chrome from 140px to 36px.
- **Standardized footer typography**: Unified button and label font sizing to `0.92em` across toggles.

### Fixed

- **PyGObject timer segfaults**: Cleaned up active `GLib.timeout_add` timers when copy buttons in `CodeBlock` and message rows were destroyed.

## [0.3.1] - 2026-08-24

### Added

- **Native GTK3 System & Dark/Light Theming**: Added 3-way theme switching (`System Default`, `Dark (Black)`, `Light`) with dynamic Pygments syntax highlighting matching theme luminance.
- **Durable SQLite plan storage (`SqlitePlanStore`)**: Co-located persistent planning state in `chat_sessions.db` to survive turns, restarts, and provider swaps.
- **Transcript preservation during compaction**: Preserved full pre-compaction history including `ThinkingPart` reasoning in `StepPersistence` SQLite snapshots.
- **Dedicated Planner agent mode**: Introduced read-only Planner agent (`write_plan`/`read_plan`) with durable plan creation and distinct session identity.
- **"Implement the Plan" action handoff**: Added in-chat action button on completed plans to transition execution to GRC-Agent.

### Changed

- **Color-coded agent mode indicators**: Added blue (`Agent`) and orange (`Planner`) mode toggle buttons.
- **Streamlined Welcome Screen & project selector**: Reduced sidebar minimum width from 562px to 472px with compact prompt chips and two-line ellipsized session rows.
- **Executor planning separation**: Removed planning capabilities from the executor agent, injecting approved plans via read-only system reminders.
- **Relocated planner and compaction controls**: Moved Planner toggle and `Compact` button beneath the composer with explicit status labels.
- **Dynamic composer height and turn cost display**: Increased composer height range (64–160px) and added per-turn USD cost display from native framework metrics.

### Fixed

- **Code block scroll clipping**: Enforced vertical scroll expansion and fixed code block height clamping.
- **Clear provider API error extraction**: Extracted structured JSON error messages from HTTP error responses (e.g. invalid API keys or quota limits).
- **CPU optimization during reasoning streaming**: Batched `ThinkingPartDelta` streaming appends, reducing long-thought CPU overhead from 20.7s to 0.057s.
- **Removed misleading generation rate display**: Removed inaccurate tok/s calculation derived from first-chunk timestamps.
- **Length-limit truncation recovery**: Safely archived `finish_reason='length'` reasoning responses to prevent poisoned repetition loops.
- **Reliable message transcript copying**: Ensured full-message copy actions cleanly capture markdown and code blocks without widget selection conflicts.

## [0.3.0] - 2026-08-19

### Added

- **Sandboxed filesystem tools (`fs_tools.py`)**: Added 8 project-directory sandboxed tools (`read_file`, `write_file`, `edit_file`, `list_directory`, `search_files`, `find_files`, `create_directory`, `file_info`). Routed `.grc` file reads through `inspect_graph`, blocked raw `.grc` file writes, enforced atomic writes with hash checks, and denied access to secret paths (`.env`, `.git`).
- **Indirect prompt-injection defense (`PromptInjectionDefender`)**: Integrated pattern scanning over tool results to detect and log high-risk content.
- **Lossless oversized tool output spilling (`ToolOutputLimits`)**: Spilled tool outputs exceeding 10k characters to `.grc_agent/tool_overflow` with retrieval handles via `read_tool_result`.
- **Twelve live-swappable LLM providers**: Added support for Ollama (local/cloud), OpenRouter, OpenAI, OpenAI-compatible backends, Anthropic, Gemini, Groq, Mistral, Cohere, Grok, and ChatGPT Codex OAuth.
- **`pydantic-ai-harness` capabilities adoption**: Integrated `StepPersistence` on SQLite and `Planning` capabilities.
- **Model-calibrated context compaction**: Calibrated compaction triggers to 85% of dynamically probed backend context limits.
- **Consolidated dual-backend search architecture**: Provided default Lexical Search (SQLite FTS5/BM25) and optional Local Vector Search (bundled llama.cpp + EmbeddingGemma over UNIX socket) with automatic fallback.
- **Resilient summarizing compaction**: Summarized aging conversation turns while preserving all user messages.
- **Historical conversation search**: Added `ConversationSearch` over pre-compaction session snapshots.
- **Continuous prose markdown grouping**: Grouped contiguous markdown elements into single `Gtk.TextBuffer` instances for natural selection and spacing.

### Changed

- **Upgraded to `pydantic-ai-harness` 0.23**: Adopted upstream improvements for path traversal defenses and directory listing caps.
- **Unbounded snapshot retention**: Enabled unbounded snapshot persistence across tool boundaries for full historical searchability.
- **Simplified search settings**: Streamlined Settings dialog around the two search backends with one-click installer triggers.
- **Decomposed unit test suite**: Split monolithic 5,056-line `test_unit.py` into modular, domain-focused test suites.

### Fixed

- **Preserved recent tool outputs in `ClearToolResults`**: Retained last 3 tool-call/return pairs and required a minimum 2,000 token reclaim threshold before clearing.
- **GTK3 Preferences shortcut crash**: Resolved `Gdk.KEY_Comma` crash on `Ctrl+,`.
- **Unallocated markdown widget gaps**: Fixed excessive vertical spacing caused by unallocated initial widget widths.
- **Widget fragmentation in markdown**: Consolidated contiguous prose into single text buffers while isolating specialized code and table containers.
- **Filesystem sandbox hardening**: Resolved security vulnerabilities regarding nested secrets, symlink traversal, case-insensitive `.grc` leaks, and missing-file errors.

## [0.2.0] - 2026-08-17

### Added

- **Hier-block library export (`save_block`)**: Enabled exporting Embedded Python Blocks (`epy_block`) to GNU Radio's reusable block library (`~/.grc_gnuradio`).
- **Tiered context compaction (`TieredCompaction`)**: Integrated harness compaction to prune bulky older tool results before trimming dialogue turns.
- **Runtime verification prompting**: Added system prompt guidance for inserting diagnostic probe blocks and inspecting execution logs.
- **Catalog query nudges**: Encouraged consulting catalog knowledge for symptom-based troubleshooting before concluding no fix exists.
- **ChatGPT Plus/Pro (Codex) provider**: Added OAuth PKCE sign-in for ChatGPT accounts with secure token storage and reasoning summary extraction.
- **Bundled local embedding runtime**: Added downloadable llama.cpp + EmbeddingGemma runtime served over local UNIX socket.
- **Settings model catalog loader**: Added dynamic model listing from provider endpoints directly into the Settings dropdown.
- **Real-time context and throughput readout**: Displayed live token usage against model context limits along with generation speed in tok/s.
- **Ubuntu 26.04 & Python 3.14 support**: Supported PyGObject in-tree `gi.events` with fallback to `gbulb` on older platforms.
- **Added `docs/known-issues.md`**: Documented verified codebase issues and planned remediations.

### Changed

- **Added `pydantic-ai-harness>=0.21.0` dependency**: Adopted harness tiered context compaction.
- **Independent embeddings backend configuration**: Decoupled `GRC_EMBED_BACKEND` from the active chat provider.
- **Full canvas relayout on block addition**: Re-arranged all flowgraph blocks into sorted header bands and rank-ordered flow bands upon adding blocks.
- **Integration scenario tool assertions**: Added generalized `tools_called` checks to scenario test expectations.

### Fixed

- **Accurate generation throughput calculation**: Calculated generation tok/s from aggregated output tokens over active model execution time; added `generation_ms` database column.
- **Redundant block registry rebuilds**: Prevented duplicate block library indexing during `save_block_to_library`.
- **Atomic vector index building**: Discarded partial vector tables on embedding failure, falling back completely to lexical indexing.
- **Embedding task prefix scoping**: Scoped EmbeddingGemma task prefixes strictly to the resolved model.
- **Empty reasoning delta crash**: Handled empty `ThinkingPartDelta` objects from Codex safely.
- **Revert error handling on GNU Radio 3.10.12**: Caught exceptions during `import_data` rollback and reported structured `rollback_failed` errors.
- **ChatGPT OAuth callback server races**: Bound callback server to both IPv4 and IPv6 loopback addresses and prevented task self-cancellation.
- **Hidden widget leakage in Settings**: Prevented `show_all()` from exposing provider-specific widgets across different provider views.
- **Serialized embedding server startup**: Serialized `ensure_server()` to prevent socket collisions during concurrent embedding initialization.

### Removed

- **Deleted `_find_block_placement`**: Removed obsolete spiral-search placement helper in favor of `compute_full_layout`.

## [0.1.5] - 2026-08-15

### Added

- **Universal OpenAI-compatible backend support**: Added support for OpenRouter, llama.cpp, vLLM, LM Studio, OpenAI, Groq, and custom endpoints via `OpenAIChatModel` and `OpenAIProvider`.
- **Settings dialog Ollama Cloud controls**: Added Ollama Cloud endpoint toggle and configurable local Ollama URL.

### Changed

- **Consolidated provider taxonomy**: Grouped providers into unified `ollama` and `openai_compatible` native categories.
- **Dependency upgrades**: Upgraded `pydantic-ai` to 2.31.0 and `openai` to 3.1.0.
- **Streamlined RAG embedding and lexical search**: Unified embedding client and FTS5 fallback across all configured endpoints.

## [0.1.4] - 2026-08-14

### Fixed

- **UnboundLocalError on missing port lookup**: Resolved `UnboundLocalError` on `dst_port` in `change_graph` when destination blocks or ports are missing, returning actionable diagnostics.
- **Unprocessed tool call session lockouts**: Stripped trailing unfulfilled `ToolCallPart` instances from message history on failed or aborted turns via `_clean_message_history_for_new_turn`.

## [0.1.3] - 2026-08-14

### Added

- **Per-turn reasoning traces (`turn_traces`)**: Recorded detailed per-turn event streams, prompts, system prompt hashes, timings, and token metrics into SQLite.
- **Hardened SQLite concurrency settings**: Configured WAL mode, `busy_timeout`, and foreign keys across chat database connections.
- **Versioned database schema migrations**: Added `_meta(schema_version)` table with automated, idempotent migrations.
- **Hermetic session and trace test suites**: Added `test_session_traces.py` and `test_session_traces_advanced.py`.
- **CI xvfb test execution**: Configured xvfb in CI to run GTK session and trace suites headlessly.

### Changed

- **Pydantic AI message serialization**: Switched history serialization to native `ModelMessagesTypeAdapter.dump_json`/`validate_json`.
- **Indexed recent session lookups**: Populated `first_message` column at save time for fast recent-session listing.
- **Removed legacy DB path migration**: Deleted obsolete `chat_sessions.db` path relocation logic.

### Fixed

- **Concurrent DB initialization**: Protected `init_db()` with a threading lock and made v0→v1 schema migrations crash-resilient.
- **Trace recorder timing and cancellation**: Ensured UI event handlers execute before trace recording and preserved UI cleanup on cancellation.

## [0.1.2] - 2026-08-01

### Added

- **Chat-to-canvas block highlighting**: Highlighted mentioned blocks with interactive pill badges that outline blocks on the GRC canvas and scroll into view on click.
- **OpenAI-compatible local server provider**: Added settings and Preferences UI for local servers (e.g. llama.cpp, vLLM) with configurable base URLs and reasoning toggles.
- **Session persistence for untitled tabs**: Supported SQLite session persistence for unsaved flowgraph tabs (`untitled:<page_title>`).
- **Persisted intermediate tool calls on failure**: Saved complete intermediate tool calls and error traces on failed or cancelled turns.
- **Real-time token usage indicator**: Added live active context token usage label with detailed tooltip breakdowns.
- **Connection validation guidance in system prompt**: Added guidance for single-source sink ports and vector itemsizes.
- **Numeric keypad zoom reset**: Supported `Ctrl+0` (`Gdk.KEY_KP_0`) for resetting canvas zoom.

### Fixed

- **Paused auto-scroll on tool expander interaction**: Stopped chat auto-scrolling when users expand tool containers.
- **Thinking box dimensions and typography**: Increased maximum content height to 500px, font size to 1.0em, and expanded padding.
- **Detailed provider error formatting**: Unpacked structured JSON error bodies from HTTP responses in `_format_turn_error`.
- **Comprehensive HTTP transport error handling**: Caught all `httpx.TransportError` subtypes in HTTP retry client.
- **Gtk.TextView message bubble width collapse**: Measured Pango layout widths to ensure text bubbles hug content properly without horizontal collapse.

## [0.1.1] - 2026-07-22

### Added

- **Context token usage indicator**: Displayed active input context tokens and dynamic provider-reported limits beneath the input box.
- **Dynamic model context length resolution**: Queried `/api/show` (Ollama) and `/api/v1/models` (OpenRouter) dynamically to resolve model context windows.

### Fixed

- **Topological block layout ordering**: Enforced topological rank sorting and downstream placement boundaries to eliminate wire criss-crossing.
- **Full-width thinking container**: Sized thinking container to 100% width and corrected state transition labels.
- **Welcome screen rendering**: Fixed quick prompt chips and recent sessions rendering on the Welcome Screen.
- **Modified graph warning on log inspection**: Added warning to `get_run_log` when inspecting logs of flowgraphs edited after the last run.

## [0.1.0] - 2026-07-18

### Added

- **SQLite FTS5/BM25 lexical fallback for `query_knowledge`**: Automatically fell back to local keyword search when embeddings are unavailable, tagging results with `search_mode`.
- **Ollama Cloud integration tests**: Added non-mocked integration tests and scenario (`23_lexical_conjugate_insert`) verifying the agent under embedding outages.
- **Status bar sync failure notifications**: Surfaced manual-edit auto-save failures via `on_sync_failed` callback in the status bar.
- **Configurable `k` parameter for `query_knowledge`**: Supported model-controlled result count `k` (1–20, default 5).
- **Initial `CHANGELOG.md`**: Created project changelog following Keep a Changelog conventions.

### Fixed

- **State-cached canvas safety-net poll**: Optimized 1.5s poll by checking GRC's `state_cache` before re-serializing the flowgraph.
- **Explicit Ollama Cloud missing-key handling**: Raised explicit configuration errors instead of silently using placeholder keys.
- **Resilient `.env` loading**: Handled unreadable `.env` files gracefully during startup.
- **Native GTK error dialog on GNU Radio load failure**: Displayed actionable GTK error dialogs when GNU Radio fails to import.
- **Surfaced silent sidebar failures**: Reported settings save errors, locked database reads, and stuck fix states through status bar and logs.
- **Bounded RAG embedding client timeouts**: Added request timeouts to embedding client requests.
- **Sanitized FTS5 search queries**: Deduplicated and capped FTS5 query terms to prevent search stalls on repetitive queries.
- **Updated CI test suite path**: Updated CI configuration to reference the native GTK3 test suite.
- **Consistent block lookup in `change_graph`**: Standardized duplicate block name checks to use `flow_graph.get_block()`.
- **Aligned documentation in `AGENTS.md`**: Corrected documentation drift regarding RAG fallbacks, auto-save polling, and dotenv handling.

### Changed

- **Upper version bounds on core dependencies**: Added upper version constraints for `pydantic-ai`, `pydantic-graph`, and `sqlite-vec`.
- **Locked dependency synchronization**: Switched local and CI installation commands to `uv sync --locked`.
- **Tested GNU Radio version specification**: Clarified supported GNU Radio version in README as 3.10.x.

### Removed

- **Removed point-in-time audit report**: Deleted `docs/codebase_audit_report.md` after verifying fixes.

### Architecture

- **GUI-only native desktop design**: Established single-entry-point native GTK3 desktop architecture without CLI subcommands, enforced in `AGENTS.md`.
