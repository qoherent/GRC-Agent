# Changelog

All notable changes to GRC Agent are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once
1.0 ships.

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

**Conversation history sidebar (with persistence)** (~3-4 days). Add a
left-hand `QListView` to the main window, one row per saved session
(loaded `.grc` path, chat messages, timestamp). Save through a small
SQLite (or JSONL) layer under `~/.grc_agent/sessions/`. Double-click to
reload.

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
