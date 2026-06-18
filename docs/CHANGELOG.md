# Changelog

All notable changes to GRC Agent are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once
1.0 ships.

## [Unreleased]

### Changed — CLI removed; GUI is the sole surface
- **The `grc-agent` console script is gone.** `src/grc_agent/cli.py`,
  `src/grc_agent/__main__.py`, and the `[project.scripts]` entry are deleted.
  Only `grc-agent-gui` (`grc_agent_gui.app:main`) remains. The GUI already
  had zero coupling to `grc_agent.cli` (verified by import audit).
- **Library logic preserved.** `doctor.run_doctor`, `sessions_store.*`,
  `history.GraphHistoryJournal`, `model_manager.*`, `config.collect_package_paths`,
  and the rest of `src/grc_agent/` are unchanged and available to the GUI.
- **Trapped CLI helpers deleted.** `_build_health_report`,
  `_build_release_manifest`, and `_render_init_template` had no library caller;
  they are removed with `cli.py`. `doctor.print_doctor_report` (CLI-only
  printer) is also removed; the structured `run_doctor` dict remains.
- **`dogfood.py` is dormant.** No production caller remains; the module is
  retained as a standalone library for eval/test pipelines.
- **Tests pruned.** CLI-only tests deleted; library-level tests rewritten to
  call the underlying modules directly (`collect_package_paths`,
  `GraphHistoryJournal.list_records/get_record/diff_records/restore_record`,
  etc.).

### Removed — dead GUI wizard/dialog code (~1200 LOC)
- `setup_panel.py` (ProviderPickerWidget / OllamaSetupWidget /
  OllamaStartHintWidget), `provider_picker_dialog.py`,
  `recent_sessions_dialog.py`, and the `ModelDialog` class were orphaned when
  the inline `ModelToolbar` replaced the setup wizard. Only
  `ModelDialogSelection` is kept (still imported by `main_window`).

### Fixed — GUI no longer `sys.exit`s on backend failure
- `grc_agent_gui/app.py` previously called `sys.exit(1)` when the Ollama
  probe failed. Per AGENTS.md "Non-blocking flow", the GUI now launches into
  degraded mode (`MainWindow.backend_reachable = False`, status-bar message)
  so the user can recover via the inline model toolbar.
- `sys.exit(2)` on graph-load failure replaced with in-window error surfacing.
- Dead `_register_server_cleanup` path removed (bootstrap never returns
  `launch_status='started'`). Unused `atexit`/`signal` imports cleaned up.

### Refactored — GUI block taxonomy, slash commands, error detection
- `inspector.py:198-216` block classification: 5-arm substring cascade
  replaced with dispatch table on native `BlockRole` StrEnum. Fabricated
  `filters` category removed.
- `main_window.py:732-744` slash-command routing: prefix ladder replaced
  with dispatch table. Legacy `\\`-prefixed alternates dropped.
- `main_window.py:851-856` `_result_is_error`: substring-scan of
  re-serialized JSON replaced with `json.loads` + structured field read.
- `main_window.py:673-674` MagicMock test leak removed (production was
  special-casing `type(model_alias).__name__ in ("MagicMock","Mock")`).
- Magic strings (OpenRouter URL, Ollama URL, default model) hoisted to
  `config.py` as `DEFAULT_OLLAMA_URL` / `DEFAULT_OPENROUTER_URL` /
  `default_openrouter_model()` — single source of truth for 12+ sites.

### Refactored — system prompt (declarative contract)
- `build_system_prompt` in `model_context.py:191` rewritten from imperative
  prose ("First, echo the user's request...", "you must batch...",
  "Use X only if...") into labeled "Routing contract" and "Structural
  contract" bullet lists. Every routing/structural fact preserved;
  no imperatives remain.

### Removed — in-band behavioral directives from model-visible strings
- All `toolagents_runtime.py` assistant/historical strings: `no_model_text`,
  `ceiling_text`, `dedup_result.message`, `_backend_unreachable_hint`
  stripped of "Please", "Reformulate", "Rephrase", "Ensure" commands.
- All `clarification.py` + `session.py` clarification `question` and
  `message` fields stripped of "Choose the one to remove",
  "Which one should be inserted", "Choose one candidate" directives.
  Now pure fact statements.

### Refactored — centralized runtime helpers
- New `runtime/text_utils.py` with `format_truncation_flag` (one uniform
  truncation sentinel), `tokenize_identifier` (canonical casefold+
  alphanumeric tokenizer replacing 6 near-duplicate normalizers), and
  `compact_whitespace` (one whitespace compactor).
- New `runtime/enums.py` with `BlockState`, `ValidationStatus`,
  `SearchDomain`, `ValidationErrorCode` StrEnums replacing
  hardcoded string tuples across change_graph/tool_schemas/inspect_graph.
- New `runtime/integrity.py` with unified `compact_file_integrity` (full
  SHA preserved). Both agent.py and change_graph.py had divergent
  copies — one clipped silently to 12 chars.
- High-impact silent truncations flagged: `transaction.py:207/210`
  (stderr/stdout), `search_blocks.py:334` (evidence),
  `toolagents_runtime.py:947` (native errors), `clarification.py:128`
  (params preview).
- `_tool_argument_candidates` now derived from `build_tool_schemas()`
  (was a 13-element hardcoded tuple that drifted — contained `scope`
  which no tool exposes).
- Three tokenization helpers + dead `_ALIAS_TOKEN_PATTERN` removed.

### Removed — regex/dtype heuristics, hardcoded allowlists
- `_first_dtype_mismatch` / `_is_port_occupancy_error` regex heuristics
  parsing GRC's free-text error strings removed per maintainer decision
  (model relies on structured error codes from `errors_payload`).
- `block:` prefix stripped from `stable_block_uid`; downstream consumer
  workarounds (`agent.py:453` strip loop, `change_graph.py:761`
  `startswith` check) deleted. `_normalize_change_graph_args` inlined.
- `_is_core_block` / `_is_hardware_or_external` substring allowlists
  replaced with exact path-component matching via named frozensets.
- Port error-code tuple replaced with `ValidationErrorCode` StrEnum.
- State decoding replaced with `BlockState` StrEnum.
- Validation status check replaced with `ValidationStatus` StrEnum.
- Domain check replaced with `SearchDomain` StrEnum.
- `validation_error_summary` status check uses `ValidationStatus`.
- `_NUMERIC_SHORTHAND_RE` dead regex removed.
- `_compact_block_summary` truncation flag corrected from
  "chat-history compactor" to "block-summary".

---

### Fixed (RAG audit remediation — findings S1–S10)
- **S1 — Vector DB no longer polluted by the test suite.** `GrcAgent.__init__`
  used to spawn an unconditional background ingestion thread that wrote to
  the real `.grc_agent/vectors/docs_v1.db`. When tests instantiated an agent
  with `get_embedding` patched, the patched (constant-valued) vectors were
  written into the production DB, permanently defeating retrieval. Ingestion
  is now an explicit `GrcAgent.warmup_vector_index()` call wired only at the
  CLI/GUI production entry points. A new `is_db_usable()` gate rejects any DB
  whose stored embeddings have zero variance (the exact pollution signature).
  `tests/conftest.py` redirects `GRC_AGENT_VECTORS_DIR` to a per-session tmp
  dir at module load and asserts the real production path is never touched.
- **S2 — `[llama].model` is now required.** A config file without a model
  silently degraded every LLM call (chat completion, RAG synthesis) to a
  backend 400. `config.py` now uses `_require_non_empty_string` for `model`
  and the repo `grc_agent.toml` carries `model = "gemma4:12b-it-qat"`. The
  GUI/CLI provider-picker can still override at runtime.
- **S4 — Removed `sanitize_text` denylist.** The hand-rolled regex list
  (`["ignore previous", "system prompt", …]`) violated the "no hand-picked
  heuristics" and "no silent transformation" rules. Source excerpts now
  roundtrip verbatim.
- **S5 — Removed in-band control flow.** The docs-synthesis system prompt no
  longer tells the model to "reply exactly with" a fixed phrase, and
  `ask_grc_docs` no longer sniffs that phrase from the answer. Confidence is
  now derived purely from the retrieval distance.
- **S6 — Calibrated distance thresholds.** `DISTANCE_THRESHOLD_HIGH=0.35`,
  `DISTANCE_THRESHOLD_MEDIUM=0.50`, `INSUFFICIENT_EVIDENCE_DISTANCE=0.65` —
  sourced from `tests/retrieval_eval/calibrate_thresholds.py` against the live
  wiki corpus (histogram in `docs/MODEL_CONTEXT_BIBLE.md`).
- **S7 — Embedding model is configurable.** `[llama].embedding_model` (default
  `embeddinggemma:latest`) threads through to `get_embedding`.
- **S8 — gemma-3 task prefixes applied uniformly.** Every query and every
  document chunk is prefixed (`task: search result | query: …` /
  `… | document: …`) per Google's gemma-3-embedding spec.
- **S9 — Resource safety and truthful flags.** `search()` and
  `ingest_if_needed()` use `try/finally`; the CWD-relative `vec1.so` fallback
  is gone (raises a clear error if not found); `output_truncated` is now
  derived from a `K=limit+1` probe rather than `len(sources) >= limit`.
- **S10 — Chunking overhaul.** Sections track the full heading hierarchy
  (each chunk's embed text carries `title > heading_path > excerpt`); a
  uniform markdown-noise stripper drops bare image/nav lines; chunks are
  256 words with 100-word overlap (was 400 words, no overlap).

### Added
- **RAG live-integration tests** (`tests/retrieval_eval/test_rag_integration.py`)
  gated behind `GRC_AGENT_LIVE_RAG=1`. Verify real ingestion, real vec1
  retrieval, and real LLM synthesis end-to-end. These would have caught S1
  and S2 before they shipped. The mock-based tests in
  `test_mvp_tool_profile.py` remain for fast unit coverage of wrapper logic.
- **`GrcAgent.warmup_vector_index()`** — explicit production hook for
  background vector-DB ingestion. Called from `cli.py` (main chat loop,
  single-tool path) and `grc_agent_gui/app.py`. Tests must not call it.
- **`is_db_usable(db_path)`** in `doc_answer.py` — uniform sanity gate that
  rejects empty DBs and DBs whose stored embeddings have zero variance.
- **`[llama].embedding_model`** config knob.

### Multi-backend LLM support (pre-existing)
- **Multi-backend LLM support.** `[llama].backend` selects the active backend
  at startup; `config.py` accepts only `ollama` (default) or `openrouter`.
  - **Ollama**: probed via `GET /v1/models` over the OpenAI-compatible path;
    model discovery via `/api/tags`; tool-calling template support is checked
    and the user is warned if the model's chat template lacks the `{{ .Tools }}`
    section. The application never spawns or stops `ollama serve`.
  - **OpenRouter**: `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` are read from
    the root `.env` and reached through the same OpenAI-compatible provider
    (no separate HTTP client).
  - `grc-agent model list [--backend {ollama,openrouter}]` lists models in the
    active backend; `grc-agent model swap [--backend ...] [--model ...]`
    switches backend/model and persists the choice.
- **GUI inline model toolbar** (`src/grc_agent_gui/model_toolbar.py`): a
  provider combo, model combo, status dot, and refresh button living
  permanently above the chat pane. Replaces the pre-launch setup wizard and
  the `Model > Select Model…` dialog; the live swap persists to both
  `preferences.json` and `config.toml`.
- **First-launch provider picker** (embedded, not a modal): Ollama (local) or
  OpenRouter (cloud); persisted to `~/.config/grc_agent/preferences.json`.
- **Degraded mode** when the backend probe fails at launch: the main window
  still opens (no crash, no forced exit); chat input and Validate are
  disabled; the model toolbar remains the recovery path. A cross-thread
  `backend_unreachable` Qt signal keeps GUI mutations on the main thread.
- **Backend-unreachable handling during turns**: `httpx.ConnectError`,
  `ConnectTimeout`, and `ReadTimeout` in the turn loop produce a typed
  `backend_unreachable` payload (`ErrorCode.BACKEND_UNREACHABLE`) with the
  server URL, surfaced as a chat hint.
- `UserPreferences.provider_chosen` field and `update_provider_chosen()`.
- **User preferences (System A)**: `~/.config/grc_agent/preferences.json`
  persists the last-loaded model, deliberately separate from the hand-edited
  `grc_agent.toml` so UI writes never clobber user config. Listed under
  `grc-agent paths`.
- **Local chat-session history (System B)**: SQLite + FTS5 store at
  `~/.grc_agent/sessions.db`. The async writer runs on a dedicated daemon
  thread with a 1000-message bounded queue, drop-oldest backpressure, and
  batched WAL commits, so the GUI main thread never blocks on SQLite I/O.
  New `grc-agent sessions {list,show,export,gc}` subcommand and a
  `File > Recent Sessions…` browser.
- **Session-history sidebar** (System B part 2): persistent left-side
  `SidebarWidget` (resizable, collapsible via `Ctrl+Shift+H`); double-clicking
  a session autoloads the associated `.grc` and resumes that session.

### Changed
- **Conversation model is now a typed `ChatHistory`** (`ToolAgents==0.3.0`).
  The ad-hoc `list[dict]` history is gone; `GrcAgent.history` becomes
  `GrcAgent.chat_history`; the runtime appends the typed `ChatMessage`
  returned by `chat_agent.step` directly. The "session" pseudo-role is gone;
  the graph snapshot is kept out-of-band on the agent. A reduced adapter and
  `_resolve_final_assistant_text` are retained for the JSON-only helper path.
- **Real-token streaming** via `ToolAgentsRunner.stream_turn`. The previous
  post-hoc QTimer throttle of finished output is replaced by a generator that
  yields `chunk` / `tool_start` / `tool_end` / `model_message` / `final`
  events. `AgentWorker.run_turn_streaming` consumes it and falls back to the
  bounded non-streaming `run_turn` when the provider cannot stream.
- **Retry-storm guard** in the tool-call loop: a repeated
  `(name, canonicalized-args)` call in one turn short-circuits to a
  `deduplicated: True` result that reuses the prior output. Prevents small
  local models from exhausting the history with identical calls.
  `tests/test_tool_call_dedup.py` covers a 5-call storm → 1 execution.
- **One-pass proportional compaction** in `compact_chat_history`. Truncated
  payloads end with `... [TRUNCATED by chat-history compactor: was N chars,
  kept M]` so the model can tell the JSON was cut off.
- **Reminder** is wrapped in `<runtime_directive>` tags and emitted as a
  `user`-role message, keeping the control plane's voice distinct from the
  human user's while avoiding non-standard wire roles.
- **Empty assistant bubble dropped** when a turn ends with only tool calls
  (`ChatWidget.drop_last_assistant()`); the persistence layer also skips the
  empty flat `assistant` display row.
- **GUI sidebar width halved** (18% → 9%).
- **Eval harness** (`tests/eval_chat/`): JSON-fixture scenarios driving the
  real `ToolAgentsRunner._run_turn_events` loop with a stubbed
  `chat_agent.step` (no real llama.cpp). `write_measured_behavior_block()`
  emits the baseline summary for this changelog.

### Removed
- `src/grc_agent/llama_launcher.py` and `src/grc_agent/llama_probe.py`
  (obsoleted by the ToolAgents-backed runtime). The app no longer manages a
  `llama-server`/daemon lifecycle.
- `llama_cpp` as a backend option (`[llama].backend` is now `ollama` or
  `openrouter` only), along with the `hf_repo` / `hf_model` / `model_path`
  config fields and the HF-cache GGUF discovery path that supported it.

### Fixed
- **`'LlamaConfig' object has no attribute 'llama'` on OpenRouter/Ollama
  swap**: `ModelSwapRunnable` was passing a bare `LlamaConfig` to
  `bootstrap_runtime`, which expects an `AppConfig`. Fixed by wrapping the
  mutated `LlamaConfig` in a fresh `AppConfig`.
- **GUI layout — sidebar over-wide / inspector collapsed on relaunch**:
  splitter state is now restored in `showEvent` (after window geometry is
  realized) instead of `__init__`, with `setStretchFactor` keeping the
  sidebar and inspector at fixed widths on resize.
- **Session resumption created a new session instead of continuing**:
  `_open_past_session` now preserves `active_session_id` instead of clearing
  it.
- **Resume dropped tool evidence**: the old path rebuilt the model history
  from only `user`/`assistant` rows, dropping every tool result and every
  `tool_calls`-bearing assistant row, so a resumed session had no
  `inspect_graph` / `search_blocks` evidence in context. The new path
  persists typed `ChatMessage` payloads in the `payload` column
  (`assistant_model` / `tool_model` roles) and replays them via
  `ChatMessage.from_dict` on resume. See
  `docs/superpowers/specs/2026-06-09-chat-history-refactor-design.md`.
- **Legacy resume fallback removed.** Per AGENTS.md (no backward-compat
  shims), `_open_past_session` refuses to resume a pre-typed-history session
  (no model rows → `active_session_id = None` with a status-bar hint to start
  a new chat). Enforced by `test_open_legacy_session_refuses_to_resume`.

### `inspect_graph` data-layer refactor
- **Uniform 5-field `_base_payload` shape** (`runtime/inspect_graph.py`):
  `ok`, `errors`, `unmatched_params`, `variable_references`,
  `param_keys_by_block`, `graph`. Same fields for every view, every
  call path, success and failure.
- **`variable_references` lifted from the `param_filter` gate.** The
  reverse map (variable → `{value, referenced_by}`) is now always present,
  so a model answering "what uses variable X" needs no second call. The
  prior behavior required `params=['X']` to surface the map.
- **`param_keys_by_block` filtered to GRC-evaluated `hide != 'all'`.**
  Uses the native `evaluated_param_hides` (lru_cached, calls GRC's
  `platform.make_flow_graph().new_block(...).rewrite()`). 87-key blocks
  collapse to 3 visible params; `-42%` model-visible payload for overview.
- **`role` added to details rows** (mirrors the overview row's `role`).
- **Renderer (`tool_context.py`)** promotes every `errors[i].message` to
  a structural `error: {code} — {message}` line (was: first only). Uses
  the same `f"{label}: {value}"` pattern as the existing `message`/`hint`
  promotion. Applies uniformly to all tools.
- **`ambiguous_target` error** now lists the matched candidate names
  (sorted, native from `_resolve_target(...).candidates`). No more second
  `inspect_graph(view='overview')` to recover.
- **`target_not_found` error** lists valid block names via the native
  `flowgraph.blocks` iterator, sorted, capped at 20 with `+N more`
  suffix — no magic number in the call site, no hand-tuned limit.
- **`is_variable_block` bridged to native GRC `Block.is_variable`** via
  the platform registry. Catches 6 variable types the string-prefix
  heuristic missed (`json_config`, `yaml_config`, `qtgui_dialgauge`,
  `qtgui_levelgauge`, `qtgui_msgdigitalnumbercontrol`, `uhd_rfnoc_graph`).
  String-prefix fallback retained for platform-unavailable paths.
- **Schema documentation**: `targets` description now lists `['all']` /
  `['*']` as the documented way to request overview. `query_knowledge`
  description is dual-purpose (catalog + docs).
- **System prompt**: catalog soft-escape-hatch clause removed (Variant A
  experiment verified safe via 3 R0 runs). Model now routes variable-
  reference questions to `inspect_graph` because the data layer makes the
  right tool obvious.
- **Dead code removed**: `_validation_status` (30 lines), `_params_filter`
  (20 lines), `block_semantics._compact_value` (one of 3 copies,
  zero callers), 3 dead `GuardrailsConfig` fields
  (`max_detail_params_default`, `max_connections_per_block`,
  `min_detail_params_before_truncation`).
- **`_connection_summaries` consolidated**: removed duplicate from
  `inspect_graph.py`, imported from `block_semantics.py`.
- **`output_truncated` telemetry bug fixed**: was reading
  `result.get("truncation", {})` (renamed to `omitted` in the refactor),
  always returning `False`. Now reads `omitted` correctly.
- **5 `GuardrailsConfig` fields wired into TOML loader**
  (`max_overview_connections`, `max_detail_params_all`,
  `max_detail_params_requested`, `max_inspect_targets`,
  `max_inspect_params`): previously defined with defaults but silently
  ignored by the TOML loader.
- **FTS5 wiki index**: in-memory SQLite FTS5 over the bundled
  `grc_agent/wiki_gnuradio_org` corpus. `tokenize='porter unicode61
  remove_diacritics 2'` for English-prose recall (verified 36× more
  results on "filtering" vs. plain unicode61). Section truncation
  removed (was 600-char cap dropping 49% of text; bm25 was distorted
  by length normalization). BM25F column weights `bm25(wiki_fts, 8.0,
  4.0, 1.0)` replace hand-tuned `title_hits * 2.0` /
  `heading_hits * 1.5` multipliers. Three accidental cargo-cult items
  removed (`PRAGMA temp_store`, `PRAGMA cache_size`, `prefix='2 3'`,
  `optimize`): 0 ms benefit on `:memory:` DBs, +2 MB index size, +70%
  build time.
- **Ranking constants cleaned**: 3 class-C hand-picked heuristics
  dropped from `rank_docs_candidates` (`off_topic_curated_penalty`
  per-doc `6.0` for "Variables in Flowgraphs", `preferred_source_bonus`
  per-query allowlist `3.5` + its `_preferred_docs_source_markers`
  function, `weak_absence_penalty` `< 0.74` threshold). The 6-way
  `source_pref` if/elif collapsed to a module-level
  `_SOURCE_PREF_WEIGHTS: dict[tuple[bool, bool, str], float]`.
- **Live R0 (`gemma4:e4b-it-qat`)**: 3 consecutive runs at 14/14
  after each fix batch. Unit tests: 404 passing, 0 fail.
- **Ground-truth set**: 97 wiki-title-based queries + 5 verified
  queries, used for I6 ranking experiments.

### Known surface items needing maintainer sign-off
- **`get_grc_context` is a 4th model-facing tool** with in-band control
  flow (`hint: "If the user also asked for a real change after
  inspecting, call \`apply_edit\` next."` at
  `runtime/inspect_graph.py:1051`). AGENTS.md §"Active MVP surface"
  states 3 tools; this is a 4th. Flag for maintainer decision (unify
  or accept).
- **`(semantic_score - 0.62) * 7.0`** in `rank_docs_candidates:1764` is
  the last remaining magic pair. It's calibrated to the
  `1/(1+rank)` semantic-score scale and cannot be changed without
  re-calibrating the whole composite. Flag for maintainer: replace
  with normalized bm25 magnitude (subagent's `M1 = -bm25 /
  (1+abs(bm25))`), which would also drop the `0.62`/`7.0` pair.

## [0.1.0] - 2026-06-05

First open-source release. Functional-complete internal tool released for
external use.

### Added
- **Startup cache cleanup hardening**: Added automatic reaping of orphan `llama-server` backend processes on startup, log file pruning/retention in the launcher log directory, and cleanup of stale GUI temp compile directories left behind by abnormal exits.
- **CLI** (`grc-agent`): REPL chat, one-shot `--message` runs, scripted
  `tool` invocation, vector index build/search/gc, history journal with
  list/show/diff/restore, dogfood intake, `doctor`/`health`/`debug-bundle`
  diagnostics, `release-manifest` evidence dumper, and a `fake` deterministic
  smoke harness.
- **GUI** (`grc-agent-gui`): PySide6 desktop sidekick with real-time streamed
  chat (Pygments-highlighted), live flowgraph inspector, two-phase
  `grcc`+run process manager, cancel/stop, and persistent window state.
- **MVP model-facing tool surface** (3 wrappers): `inspect_graph`,
  `query_knowledge` (dispatches to `search_blocks` / `ask_grc_docs` by
  intent), and `change_graph`. Larger internal surface (17 tools) is not
  model-facing.
- **Local retrieval** over the bundled GNU Radio manual/tutorial corpus
  (Qdrant + FastEmbed, `thenlper/gte-base`).
- **Shared startup bootstrap** for CLI and GUI; lazy llama.cpp auto-launch
  with PID-liveness checks.
- **Open-source hygiene**: MIT license, `uv` install path, CONTRIBUTING,
  CODE_OF_CONDUCT, SECURITY, customer docs consolidated into the README.

### Notes
- The bundled `docs/wiki_gnuradio_org/` corpus is shipped for retrieval;
  license and attribution are documented in the directory.
- `grc-agent init` writes a starter config to `~/.config/grc_agent/config.toml`.
- `grc-agent paths` lists every on-disk location the package uses.

## Deferred harder wins

The 0.1.0 polish pass shipped a curated batch of "easy wins." The items
below are deliberately deferred — each scoped to a single PR, with a
rough effort estimate for a contributor already familiar with the
codebase. See `README.md` for what's already implemented.

**Multi-provider LLM support** (~1 week). `ProviderConfig` base class in
`toolagents_runtime.py` with `LlamaCppProviderConfig`,
`OpenAIProviderConfig`, `AnthropicProviderConfig`, `OllamaProviderConfig`
subclasses. Move the OpenAI SDK call out of the runtime and into the
provider subclasses. Add `anthropic` and `ollama` as optional extras.
Select via a new `[llama].provider` key. Add provider-specific health
checks to `grc-agent doctor`.

**Settings / preferences dialog** (~3 days). GUI `QDialog` wrapping the
keys exposed by `default_app_config()`. Validate as the user types; on
Save, write through a thin wrapper around the loader. Re-entrant. Persist
window geometry via `QSettings`.

**First-run GUI onboarding wizard** (~1 week). `QWizard` with one page
per `grc-agent init` question, followed by a "Verify" page that runs
`grc-agent doctor`. Wire into the first launch of `grc-agent-gui`: if
`user_config_path()` is missing, open the wizard instead of the main
window. "Skip for now" acceptable.

**Conversation history sidebar (with persistence)** — *shipped in
[Unreleased] as System B Part 2*. The persistent `SidebarWidget`
(left-edge panel, collapsible, resizable to max 20% width, session
resumption with agent history restoration) replaces the modal
`File > Recent Sessions...` dialog. The SQLite/FTS5 store at
`~/.grc_agent/sessions.db` and the `grc-agent sessions` CLI
subcommand that shipped in v0.1.0 System B remain the data layer.
**Light / dark theme toggle (with system follow)** (~2-3 days). Move the
hardcoded Catppuccin stylesheet out of `app.py` into two external `.qss`
files. Add a `QSettings`-backed theme picker under **Help > Theme** (Dark
/ Light / Follow system). For "Follow system", listen to
`QStyleHints.colorScheme()`. Re-apply on the fly, no restart.

**System tray icon / minimize to tray** (~2-3 days). `QSystemTrayIcon`
with a context menu (Show, Stop, Quit). On `closeEvent`, hide the window
instead of closing it when a flowgraph is running. Wire the existing
two-phase termination into **Stop**.

**Multi-window / multi-session support** (~1 week). Detach `GrcAgent` and
`ChatWidget` into a per-session controller. The main window becomes a
workspace holding many `SessionController` instances, each with its own
tab. Cross-session features (shared history, parallel `grcc` jobs) are
out of scope.

**Test coverage tooling** (~half a day). Add `pytest-cov` to `[dev]`. Add
`[tool.coverage.run]` and `[tool.coverage.report]` blocks targeting
`grc_agent` and `grc_agent_gui`. Add a `coverage` job to CI and a badge to
the README. Floor: 70% line coverage, gated.

**PyPI publish workflow** (~half a day). Add a `release.yml` workflow
that triggers on a `v*.*.*` tag push. Use PyPI Trusted Publishing (OIDC,
no long-lived API token). Job runs `uv build`, uploads via
`pypa/gh-action-pypi-publish`, creates a GitHub Release with the wheel
and sdist attached.

**Multi-OS CI matrix** (~1 day). Add `os: [ubuntu-latest, macos-latest,
windows-latest]` to `.github/workflows/ci.yml`. On macOS, install GNU
Radio through MacPorts; on Windows, document that `grcc` is not
available and skip the GNU-Radio-dependent test step (but still run ruff,
pytest, and the no-`grcc` subset of `unittest`).

**GUI test runner unification** (~half a day). CI runs `unittest`; the
GUI tests under `tests/gui/` use `pytest-qt` style fixtures (`qtbot`) and
aren't picked up. Move GUI tests to a pytest-style discovery path that
CI invokes with `uv run pytest tests/gui/`.

**`grc-agent clean` / `grc-agent uninstall-purge`** (~half a day). Add
`grc-agent clean` (deletes user-owned state directories, **not** model
weights in `~/.cache/huggingface` — those are usually shared with other
tools) and `grc-agent uninstall-purge` (calls `uv tool uninstall
grc-agent` first, then `clean`). Both must require explicit `--yes` and
print a summary of what they would delete before doing it.
