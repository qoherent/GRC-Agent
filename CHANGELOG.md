# Changelog

All notable changes to this project are documented in this file.

The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning starts fresh at `0.1.0` for the current native GTK3 architecture —
earlier `v1.0.0`/`v2.0.0` tags belonged to an unrelated, since-rewritten
web-dashboard codebase and are not part of this history.

## [Unreleased]

### Added
- Planning state is now durable per saved chat session through the harness `SqlitePlanStore`, co-located with `chat_sessions.db`; plans survive turns, restarts, and agent/provider live-swaps, while ungrouped runs remain in memory and session delete/clear/prune operations cascade plan rows.
- Automatic and manual compaction now preserve the complete pre-compaction transcript—including emitted `ThinkingPart` reasoning—in unbounded StepPersistence snapshots inside the same user-exported database, so compacted session history remains usable without sacrificing fine-tuning data.
- A separate, manually selected Planner agent now shares the current chat history and durable plan store with the GRC executor. Its model-visible surface is structurally read-only (`PrepareTools` plus only `write_plan`/`read_plan`), its reasoning/tool activity persists under the `grc_planner` identity, and a compact `Plan` toggle supports both empty-session planning and explicit mid-session plan revision.
- Successful planner turns that call `write_plan` now produce an in-chat `Implement the Plan` action after the UI verifies the session's `SqlitePlanStore` is non-empty. Clicking it automatically flips to GRC-Agent and sends a visible implementation request; merely finishing a response or reading a plan does not trigger the action, and execution never starts without the click.

### Changed
- The GTK chat welcome screen is denser and sidebar-safe: smaller one-row quick prompts, two-line ellipsized recent-session rows, long-name width bounds, and clearer composer/message boundaries reduce the measured minimum width from 562 px to 472 px while preserving full details in tooltips.
- The main GRC executor no longer has any planning capability or planning tools. Approved plans are injected read-only from `SqlitePlanStore` through a cache-safe ephemeral `SystemReminders` handoff; switching back from Planner mode never auto-executes.
- Planner mode and manual compaction now live beside the Context readout under the composer instead of in the top toolbar. A native switch is paired with an explicit `Planner active`/`GRC-Agent Active` label; the old refresh-icon compaction affordance is replaced by a `Compact` text button with a default-No confirmation explaining summarization and retained transcript snapshots.
- The composer is taller (64px minimum, growing to 160px), and the header now presents active graph plus provider/model in flexible bordered badges with middle ellipsis and full tooltips. The Context row also displays Pydantic AI's native persisted USD cost for the latest turn (aggregating all model requests around tool calls) when every response is priceable, or `Cost: unavailable` when the framework returns `None`; no local price table or partial estimate is used.

### Fixed
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
