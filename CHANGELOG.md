# Changelog

All notable changes to this project are documented in this file.

The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning starts fresh at `0.1.0` for the current native GTK3 architecture —
earlier `v1.0.0`/`v2.0.0` tags belonged to an unrelated, since-rewritten
web-dashboard codebase and are not part of this history.

## [Unreleased]

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
