# Changelog

All notable changes to GRC Agent are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once
1.0 ships.

## [Unreleased]

### Added
- **Multi-backend LLM support** (`llama_cpp`, `ollama`, `openrouter`).
  - `[llama].backend` config field selects the active backend at startup.
  - **Ollama**: probed via `GET /v1/models` through the standard OpenAI-compat
    path; model discovery via `/api/tags`; on-demand GGUF registration uses
    `ollama create <name> -f <tmpModelfile>` so any cached HF model can be
    loaded without a manual pull.
  - **OpenRouter**: API key (`OPENROUTER_API_KEY`) and model name
    (`OPENROUTER_MODEL`) loaded exclusively from the root `.env`; no
    hardcoding in source. Uses the `requests`-based OpenRouter client.
  - New `grc-agent model list --backend {llama_cpp,ollama}` and
    `grc-agent model swap --backend {llama_cpp,ollama,openrouter}` CLI paths.
  - **GUI client/model switcher**: `ModelDialog` now lists all three backends
    in a "Client" combo. The `llama_cpp` and `ollama` model combos both show
    the full local HF cache (5 models) so users can select any cached GGUF
    regardless of backend. Pulled-only Ollama models appear beneath with an
    "(Ollama Pulled)" suffix; duplicates are suppressed by normalised name.
  - Chat input accepts `/client`, `\client`, `/model`, `\model` slash-commands
    to open the switcher dialog from the keyboard.
  - Status bar shows the active backend next to the loaded model name.

### Fixed
- **`'LlamaConfig' object has no attribute 'llama'` on OpenRouter/Ollama swap**:
  `ModelSwapRunnable` was passing a bare `LlamaConfig` to `bootstrap_runtime`,
  which expects an `AppConfig`. Fixed by wrapping the mutated `LlamaConfig`
  in a fresh `AppConfig` before calling `bootstrap_runtime`.
- **Ollama model dialog showed only already-pulled models**: The dialog now
  lists all locally cached HF GGUF files for the Ollama backend and registers
  them on demand, matching the `llama_cpp` model list.
- **Switch button not enabled when changing backends**: `_refresh_button_state`
  now enables the button whenever the selected backend differs from the active
  one, independent of the selected model.

  - New module `grc_agent.model_manager` with `discover_cached_models()`
    (scans `~/.cache/huggingface/hub/` and an optional
    `[llama].models_dir`; preserves original GGUF filenames from HF
    snapshot symlinks; skips in-progress downloads; filters
    `mmproj-*` projection files that the launcher would refuse to
    load) and `list_system_specs()` (VRAM/GPU from `nvidia-smi`,
    RAM from `/proc/meminfo`, CPU from `/proc/cpuinfo`; returns
    `None` per field on unsupported platforms).
  - New CLI subcommand `grc-agent model {list,specs,swap}`.
    `swap` is a stub that surfaces a clear "Phase 3 not yet wired"
    error so scripts can be authored against the final shape.
  - New optional config field `[llama].models_dir` for hand-placed
    `.gguf` directories that are not under the HF cache.

- **Model selector (Phase 2 of 3)**: GUI model-selector dialog and
  Model menu.
  - New `Model` menu in the menubar with `Select Model...` (Ctrl+M)
    and a disabled `Currently loaded: <name>` status entry that
    resolves through `cfg.model`, then `cfg.hf_model`'s filename,
    then `cfg.model_path` basename.
  - New non-modal `ModelDialog` (`src/grc_agent_gui/model_dialog.py`)
    with a dropdown listing every discovered `.gguf`, a confirm
    strip whose "Switch model" button is enabled only when a
    different model is picked, and a system-specs panel showing
    compact one-liner (full names in tooltip).
  - The `Select Model...` action is disabled while a chat turn is
    in flight, mirroring the existing mid-turn protection.
  - Confirming a selection in this phase appends a system-style
    "Phase 3 not yet wired" note to the chat; the live swap ships
    in Phase 3.

- **Model selector (Phase 3 of 3)**: live model swap wired into the
  GUI and the CLI.
  - New `LlamaServerLauncher.swap_model(new_hf_repo, new_filename,
    new_alias=None)` builds a fresh `LlamaConfig` (preserving
    server URL, device, GPU layers, context window, and timeout)
    and delegates to `ensure_server_ready` on a new launcher.
    Server URL and health-evidence contract are preserved across
    the swap.
  - `grc-agent model swap --hf-repo <repo> --filename <name>
    [--alias <name>]` now performs the live swap; on success it
    prints the new model alias, server URL, status, and
    health-evidence keys. `LlamaLauncherError` is surfaced as
    rc=1 with a human-readable message.
  - The GUI dispatches the swap through a `ModelSwapRunnable`
    (QRunnable) so the UI does not freeze during HF download
    (which can take minutes for an uncached model). The chat
    input and Validate button are locked for the duration; the
    "Currently loaded" menu and the model status label are
    refreshed on success; failures are surfaced in both the
    status bar and the chat panel.
  - The model dialog's "Currently loaded" preselect now matches
    `cfg.hf_repo:cfg.filename` (repo-aware) before falling back
    to filename-only or stem matching.
  - The chat history is preserved across a swap; the next model
    turn sees the same messages.

- **User preferences (System A)**: small JSON file at
  `~/.config/grc_agent/preferences.json` persists the model the
  user most recently loaded through the GUI or CLI. The file is
  deliberately separate from `grc_agent.toml` (which is
  hand-edited) so auto-written UI prefs do not clobber user
  config. After a successful `grc-agent model swap` (CLI) or a
  GUI `Switch model` confirmation, `last_model.{hf_repo,
  filename, alias, saved_at}` is written atomically. On next
  startup, preferences overlay `model` and `hf_model` onto the
  in-memory `LlamaConfig` and clear `model_path` to match the
  live-swap behavior. Failure to write is non-fatal (warning,
  swap itself is not rolled back). The file path is listed in
  `grc-agent paths` output as `preferences`. 17 unit tests.

- **Local chat-session history (System B)**: SQLite-backed
  store at `~/.grc_agent/sessions.db` (FTS5-indexed) holds
  the user's chat history. The async writer runs on a dedicated
  daemon thread with a 1000-message bounded queue,
  drop-oldest backpressure, and 64-message batched commits
  under WAL — the GUI's main thread is never blocked on
  SQLite I/O. New `grc-agent sessions {list, show, export,
  gc}` subcommand. New `File > Recent Sessions...` menu in
  the GUI opens a browser dialog with a markdown preview;
  double-click a row to clear the chat widget and replay the
  session's messages. Per the agreed design, opening a `.grc`
  always starts a fresh chat session; the user explicitly
  reopens a past session via the dialog. The on-disk graph
  file is unchanged by the new system. 26 unit tests + 7 GUI
  tests.

- **Session history sidebar (System B — Part 2)**: `SidebarWidget`
  replaces the modal `File > Recent Sessions...` dialog with a
  persistent left-side panel inside the main splitter.
  - Defaults to 18% width (max 20%); resizable and collapsable via
    the `◀` chevron button, `File > Session Sidebar` menu item, or
    `Ctrl+Shift+H`.
  - Double-clicking a session autoloads the associated `.grc` graph
    **and** resumes that session: `active_session_id` is preserved,
    `agent.history` is reconstructed from the DB `user`/`assistant`
    messages, and subsequent sends append to the same SQLite record.
  - `ChatWidget` event roles (`tool_started`, `mutation`, `error`,
    `tool_finished`) now write to `self._history` and render through
    `_render_chat()` — fixing display drift on turns that include
    tool use.
  - Splitter state restored in `showEvent` (after window geometry is
    fully realized) instead of `__init__`; `setStretchFactor(1, 1)`
    on the chat pane keeps the sidebar and inspector at fixed widths
    on window resize. 5 GUI tests (116 total, all green).

### Fixed
- **GUI layout — sidebar over-wide / inspector collapsed on
  relaunch**: `QSplitter.restoreState` was called in `__init__`
  before `window.show()`, so `self.width()` returned the
  pre-geometry value (~92 px). Size-correction arithmetic based on
  that value produced wrong proportions. Moved restoration to
  `showEvent` (fires once when the window is first mapped) where
  `self.width()` returns the true resolved width. Added
  `setStretchFactor` so only the chat pane grows on window resize.

- **Session resumption — new session created instead of continuing**:
  `_open_past_session` was unconditionally setting
  `active_session_id = None` after loading a past session, so the
  next `send_prompt` call created a fresh DB record instead of
  appending to the resumed one. Now `active_session_id` is set to
  the loaded session's ID. `agent.history` is rebuilt from the
  stored `user`/`assistant` messages so the model sees prior
  context on continuation turns.

- **Resume dropped tool evidence — model lost inspect/search context**:
  The previous resume path reconstructed `agent.history` from DB rows
  by filtering on `role in {"user", "assistant"}`. Every
  `tool`-role row and every `assistant` row that carried
  `tool_calls` was silently dropped, so a resumed session started
  with no `inspect_graph` / `search_blocks` evidence in the model's
  context. The first mutation after a resume would force a re-inspect.
  The new path persists typed `ChatMessage` payloads in the
  `payload` column (`assistant_model` / `tool_model` roles) and
  replays them via `ChatMessage.from_dict` on resume. See
  `docs/superpowers/specs/2026-06-09-chat-history-refactor-design.md`.

### Changed
- **Conversation model is now a typed `ChatHistory`** (ToolAgents
  `ToolAgents==0.3.0`). The previous ad-hoc `list[dict]` history is
  gone; the `ToolAgentsHistoryAdapter` collapses to a thin shim used
  only by the JSON-only helper path (`ToolAgentsJsonClient`).
  `GrcAgent.history` becomes `GrcAgent.chat_history`; the runtime
  appends the typed `ChatMessage` returned by `chat_agent.step`
  directly. The "session" pseudo-role is gone; the graph snapshot
  is kept out-of-band on the agent.

- **Real-token streaming via `ToolAgentsRunner.stream_turn`**. The
  GUI's previous post-hoc 16-char QTimer throttle of the *finished*
  model output is replaced by a `stream_turn` generator that yields
  `chunk` / `tool_start` / `tool_end` / `model_message` / `final`
  events. `AgentWorker.run_turn_streaming` consumes the iterator and
  falls back to the bounded non-streaming `run_turn` when streaming
  is not exposed by the provider.

- **Retry-storm guard** in the tool-call loop: when the model issues
  the same tool call (`(name, canonicalized-args)`) more than once
  in a turn and the first call returned successfully, the duplicate
  short-circuits to a `deduplicated: True` result that reuses the
  prior result. Prevents small local models that loop on themselves
  from exhausting the chat history with identical calls. `tests/test_tool_call_dedup.py::EndToEndRetryStormTests`
  covers a 5-call storm → 1 underlying execution.

- **One-pass proportional compaction**: `compact_chat_history` does
  a single pass over the candidates, computing each new payload size
  by proportional allocation against the remaining budget. No
  iterative shave-loop, no per-cycle length re-computation. Truncated
  payloads end with `... [TRUNCATED by chat-history compactor: was N
  chars, kept M]` so the model can tell the JSON was cut off.

- **Reminder is wrapped in `<runtime_directive>`** tags and emitted
  as a `user`-role message. The `user` role dodges both the
  "system mid-stream" template rejection and the "non-standard
  wire role" rejection that an earlier `Custom`-role tag design
  introduced. The XML wrapper keeps the control plane's voice
  distinct from the human user's.

- **Empty assistant bubble dropped** when a turn ends with the model
  producing only tool calls (no final text). The chat widget no
  longer shows an empty "Agent:" header; the typed `assistant_model`
  row already carries the message for resume. New
  `drop_last_assistant()` method on `ChatWidget`; the persistence
  layer also skips writing the empty flat `assistant` display row.

- **GUI sidebar width halved from 18% to 9%** (per UX feedback).
  `toggle_sidebar`'s reopen-size and the showEvent size-guard clamp
  both updated to match.

- **Eval harness (`tests/eval_chat/`)**: 10 JSON-fixture-driven
  scenarios driving the real `ToolAgentsRunner._run_turn_events`
  loop with a stubbed `chat_agent.step`. No real llama.cpp, runs
  in CI. Captures the runtime's contract for the retry-storm
  guard, surface-gate refusal, schema validation, safety ceiling,
  and edge-case JSON parsing. Baseline: 10/10 PASS. New
  capabilities must ship with fixtures for their failure modes —
  documented in BLUEPRINT's "Measured Behavior" section.

- **Legacy resume fallback removed.** AGENTS.md now explicitly
  forbids backward-compat shims, fallback syntheses, and dual-format
  persistence. `_open_past_session` refuses to resume a session
  written before the typed-history format: no model rows → no
  typed history → `active_session_id = None` and a status-bar
  message tells the user to start a new chat. The test
  `test_open_legacy_session_refuses_to_resume` enforces the
  refusal.

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
