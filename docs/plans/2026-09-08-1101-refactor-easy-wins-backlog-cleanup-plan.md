---
title: Easy-Wins Backlog Cleanup Pass - Plan
type: refactor
date: 2026-09-08
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
execution: code
---

# Easy-Wins Backlog Cleanup Pass - Plan

## Goal Capsule

- **Objective:** The codebase's cleanup backlog reflects verified reality, and its reliable easy-win items are done: the chat sidebar renders tool results from structured fields and shared provider tables instead of string spellings, the offline knowledge corpus carries only real page content, flowgraph runs that die at process spawn are reported as failures, and the scenario suite's default model matches the chosen free-tier default.
- **Means:** Four independently landable clusters — sidebar deterministic-source cleanup (KTD1–KTD3), wiki-corpus hygiene (KTD4), exec-monitor spawn-failure detection (KTD5), and the scenario default alignment (Product Decision D2).
- **Authority:** `AGENTS.md` invariants govern conflicts — zero ad-hoc heuristics, fix at the source, native GRC APIs only, no version bump, single-branch `main`. R-IDs own product behavior; KTD-IDs own mechanism. Repo test and ruff gates arbitrate completion.
- **Stop conditions:** All units land with the fast gate, linter, and GTK gate green; the corpus contains zero MediaWiki chrome markers; the backlog doc matches verified state. Stop and re-scope if the frame-bounded flush regresses streaming under the golden or xvfb smoke, or if the spawn-failure identity rule cannot distinguish real runs without message-content parsing.
- **Execution profile:** Seven small units, four independent clusters; `docs/backlog.md` truth-up lands last. No sequencing risk beyond U7 depending on all others.
- **Tail ownership:** `ce-work` or the user. Single-branch `main`, conventionally scoped commit messages per `AGENTS.md`.

---

## Product Contract

### Summary

Execute the backlog's easy-win cleanup clusters: replace the sidebar's nine-literal `search_mode` substring matching with a structured-field read, move codex provider magic strings into the shared provider catalog, collapse the four-interval stream flush into one frame-bounded cadence, strip MediaWiki chrome from 71 of 101 corpus files and consolidate the one verified pointer stub into its real content, distinguish spawn-crashed flowgraph runs from legitimate zero-exit runs, and set the scenario-suite default back to `dots-studio/dots-3-note-preview:free`.

### Problem Frame

The 2026-09-02 harness lean-out and 2026-09-03 sidebar decomposition landed, but their origin plans deliberately fenced out follow-up work now recorded as backlog items 8, 9, and parts of 5/6. The backlog also drifted from the codebase: research for this plan verified that two of item 8's bullets (never-removed timers, competing font scalers) were already fixed during the decomposition, that item 5.2's named duplicate pair (`Binary_Files_for_DSP`) no longer exists, and that its `Provenance:`/`Aliases:` stub pages were never present in the current corpus — while surfacing a larger, unrecorded hygiene debt: 71 of 101 wiki files still carry full MediaWiki navigation chrome that dilutes lexical and vector ranking mass. Item 6.1's spawn-crash confusion is real and verified against GRC's own `Executor.py`: a failed `subprocess.Popen` emits `send_verbose_exec(str(e))` followed by `send_end_exec()` with the default code 0, which the monitor currently reports as a clean success. The user directed an easy-wins-first pass: cluster what can be researched and implemented most reliably, hold out risky or expensive work, and route online research through the hermes subagent.

### Requirements

**Sidebar deterministic sources**

- R1. The `query_knowledge` tool-label suffix is derived from the result payload's structured `search_mode` field, not from substring matching of nine literal spellings.
- R2. Provider-specific render and preflight behavior in the chat sidebar is read from the shared provider catalog in `src/grc_agent/ui/providers.py`, not from scattered `provider == "openai_codex"` string comparisons in `chat/` modules.
- R3. Visible-text streaming flushes run on one frame-bounded cadence; the four adaptive intervals and their character-count thresholds are deleted.

**Corpus hygiene**

- R4. No file under `docs/wiki_gnuradio_org/` contains MediaWiki navigation chrome (Navigation menu, Personal tools, Namespaces, Views blocks).
- R5. The corpus's one verified upstream pointer stub (`Coding_guide_impl.md`, whose entire live content is "This page has been replaced by GREP1") is consolidated into the real guidelines content it points to.
- R6. The knowledge index is rebuilt after the content changes, and a sample retrieval still returns relevant chunks from a cleaned page.

**Run monitoring**

- R7. A flowgraph run whose subprocess failed to spawn is reported to the agent and the failure callback as a failed run, distinguishable from a legitimate zero-exit run, without parsing message prose.

**Defaults and documentation**

- R8. The integration scenario suite's OpenRouter default model is `dots-studio/dots-3-note-preview:free`, overridable per-run by `GRC_OPENROUTER_MODEL`.
- R9. `docs/backlog.md` and `CHANGELOG.md` reflect the verified state of every item this plan touched, including the bullets found already complete.

### Key Decisions

- D1. **Easy-wins-first scope** — this pass executes only the reliable, bounded clusters and defers the risky and expensive backlog work (session-settled: user-directed — chosen over executing the full backlog: focus on what can be researched and implemented easily and reliably). Governs R1–R9 selection and Scope Boundaries.
- D2. **Scenario suite stays free-tier OpenRouter at `dots-studio/dots-3-note-preview:free`** — no paid model, no ollama-cloud switch (session-settled: user-directed — chosen over a stronger paid model and the passing ollama_cloud baseline: no real-token burn unless needed). Accepted consequence, recorded in `docs/backlog.md`: this model double-encodes arrays and fails the `01_add_throttle` scenario per the 2026-09-03 finding; the suite is integration-marked and opt-in, so no gate burns tokens. Governs R8.

### Scope Boundaries

**Deferred to Follow-Up Work**

- Off-loop async work (synchronous SQLite reads and the blocking HTTP context probe off the unified loop) — the one piece of backlog item 8 with real regression surface on the loop; deferred to a dedicated pass.
- Test-tree split and private-surface rewrite (backlog item 9): migrating `tests/test_chat_sidebar.py` (4,931 lines, ~99 ad-hoc constructions) onto the conftest fixture, plus resolving the remaining `# noqa: C901` suppressions.
- Upstream engagement (backlog 6.2–6.5): filing issues/PRs on `pydantic-ai-harness` (shell stdin DEVNULL), `stackone-defender` (regex false positives), and ConversationSearch interrupted-run recovery — external dependencies with uncontrollable timelines.

**Outside this pass**

- Feature tracks: canvas screenshots (backlog 1), data-plane visualization (2), SigMF catalog (3), project File-RAG (4), wiki corpus expansion (5.1).
- Any scenario-model upgrade work beyond the default flip (D2 closes backlog item 10 as decided).

---

## Planning Contract

### Key Technical Decisions

- KTD1. **Structured `search_mode` read via one JSON-parse rule.** `_tool_label` in `src/grc_agent/chat/format.py` attempts one `json.loads` on the result string and reads the `search_mode` key; any non-JSON or missing-field result yields no suffix. Rejected alternative: keeping quote-variant spellings (violates zero-ad-hoc-heuristics; the nine literals exist only because the payload was stringified). If implementation finds a caller passing Python-repr strings, fix that caller at the source rather than widening the parse rule. Advances R1.
- KTD2. **Provider behaviors live in the shared catalog.** `src/grc_agent/ui/providers.py` gains a per-provider behavior entry (thinking-summary label, sign-in requirement); `chat/settings_controller.py`, `chat/stream_view.py`, and `chat/turn_driver.py` read the table instead of comparing provider strings. Rejected alternative: a chat-local constants module (duplicates the single-source-of-truth catalog the settings dialog and toolbar badge already share). `agent_factory.py`'s construction dispatch stays out of scope — it is already table-driven module dispatch, not render branching. Advances R2.
- KTD3. **One frame-bounded flush cadence.** While a stream node is active, visible-text flushes coalesce onto a single GLib frame-cadence source; the forced final flush before node handoff is preserved. The four intervals (0.25/0.066/0.050/0.033 s) and the 2,000/5,000-char thresholds are deleted. Rejected alternative: keeping adaptive intervals (performance tuning expressed as magic thresholds — exactly the ad-hoc branching `AGENTS.md` forbids; the `_ChunkAccumulator` already batches deltas, so frame-cadence draining is bounded work per flush). Advances R3.
- KTD4. **Corpus cleaning is a one-shot scripted transform, not runtime code.** Chrome stripping and the GREP1 consolidation run once via a small committed maintenance script under `playground/`; nothing ships in `src/`. Rejected alternative: runtime ingest-time cleaning (adds permanent complexity to parse what a one-time crawl artifact made; the originals stay recoverable in git history). Advances R4, R5.
- KTD5. **Spawn-failure detection by process-identity snapshot, not prose parsing.** `ExecutionErrorMonitor` accepts an injected process-identity provider (wired in `src/grc_agent/desktop_app.py` from the canvas manager's current page, whose `.process` GRC's own `Executor` assigns on successful spawn). The monitor snapshots the identity at the start marker; a Done marker carrying no return-code text with an unchanged identity records a spawn failure, surfacing the retained verbose exception. Rejected alternative: pattern-matching the verbose exception text between markers (message-content heuristics; a clean silent flowgraph and a spawn crash differ only in process identity). Advances R7.
- KTD6. **`get_run_log` payload gains an additive `spawn_failed` field** rather than fabricating a non-zero return code, so the model sees the truthful distinction the monitor actually observed. Advances R7.

### Sequencing

U1, U2, U3, U4, U5, U6 are mutually independent; each lands as its own commit. U7 (docs truth-up) runs last so it records the landed state of all others.

---

## Implementation Units

### U1. Structured `search_mode` in the tool label

- **Goal:** The `query_knowledge` expander label derives its `(lexical|hybrid|vector)` suffix from the parsed result payload.
- **Requirements:** R1
- **Dependencies:** none
- **Files:**
  - `src/grc_agent/chat/format.py`
  - `tests/test_chat_format.py`
  - `src/grc_agent/chat/stream_view.py` (conditional — only if the retry caller hands over a Python-repr string)
  - `src/grc_agent/chat/transcript_view.py` (conditional — same check on the settled-result path)
- **Approach:**
  1. In `_tool_label`, replace the nine substring checks with one `json.loads` attempt on the result string, reading `search_mode` when the parse yields a mapping.
  2. Non-JSON results and payloads without the field render the plain label.
  3. Check the two callers (`chat/stream_view.py` retry path, `chat/transcript_view.py` settled-result path): if either hands over a Python-repr string rather than the serialized JSON the tool boundary produces, fix that caller to pass the payload it actually holds (fix at the source).
- **Patterns to follow:** the unified `query_knowledge` return shape (adapter's `search_mode` key); `AGENTS.md` §1 zero-ad-hoc-heuristics.
- **Test scenarios:**
  - Existing tests `test_query_knowledge_label_shows_search_mode` (three modes) pass unchanged.
  - A prose (non-JSON) result string renders the plain label with no suffix.
  - A JSON payload without `search_mode` renders the plain label.
  - Retry-label combination (`retry=True`) still prefixes the warning marker.
- **Verification:** `uv run pytest tests/test_chat_format.py tests/test_chat_sidebar_golden.py` passes; golden transcript bytes unchanged.

### U2. Provider behavior table

- **Goal:** Codex-specific render and preflight branches read from the shared provider catalog.
- **Requirements:** R2
- **Dependencies:** none
- **Files:**
  - `src/grc_agent/ui/providers.py`
  - `src/grc_agent/chat/settings_controller.py`
  - `src/grc_agent/chat/stream_view.py`
  - `src/grc_agent/chat/turn_driver.py`
  - `tests/test_ui_providers.py` (new)
- **Approach:**
  1. Add a per-provider behavior mapping to the catalog module (fields: thinking-summary label, sign-in requirement), with a default entry for providers without quirks.
  2. Replace the `provider == "openai_codex"` comparisons at the three `chat/` sites with catalog lookups.
  3. Leave `agent_factory.py` construction dispatch untouched.
- **Patterns to follow:** the existing `PROVIDER_LABELS` / `PROVIDER_MODEL_KEY` table shapes in the same module.
- **Test scenarios:**
  - Codex thinking stream renders the table's summary label; a default provider renders the default label.
  - The turn-driver sign-in gate branches on the table's sign-in flag for codex and skips it for a default provider.
  - Settings preflight uses the table entry; unknown providers fall back to the default behavior.
- **Verification:** new tests plus `uv run pytest tests/test_chat_sidebar.py tests/test_agent_factory.py` pass; no `openai_codex` literal remains in `src/grc_agent/chat/`.

### U3. Frame-bounded stream flush

- **Goal:** One flush cadence replaces the four-interval adaptive throttle.
- **Requirements:** R3
- **Dependencies:** none
- **Files:**
  - `src/grc_agent/chat/stream_view.py`
  - `tests/test_chat_sidebar.py`
- **Approach:**
  1. While a stream context is active, arm one GLib frame-cadence repeating source that drains dirty accumulators; disarm on stream end.
  2. Keep the forced final flush before node handoff and the shutdown-skip guard (`shutting_down`).
  3. Delete `_STREAM_FLUSH_INTERVAL`, the per-length intervals, the 2,000/5,000-char branches, and the `last_flush` bookkeeping the rule owned.
- **Execution note:** The golden pins copy text, not cadence — after the fast gate, run one manual long-stream smoke under `xvfb-run` to confirm no visual stall or missed final paint.
- **Patterns to follow:** the existing bounded-timer lifecycle discipline (`_remove_timers` disarms what `__init__` arms).
- **Test scenarios:**
  - A streamed turn's transcript copy text is byte-identical to the pre-change golden.
  - The final chunk of a stream is painted without a further event (forced flush at node handoff).
  - Timer sources armed for a stream are removed when the stream ends or the sidebar shuts down.
- **Verification:** fast gate and GTK gate green; golden unchanged.

### U4. Corpus chrome strip and pointer-stub consolidation

- **Goal:** Corpus files contain only page content; the pointer stub carries its real target's content.
- **Requirements:** R4, R5, R6
- **Dependencies:** none
- **Files:**
  - `playground/corpus_clean.py` (new, one-shot maintenance script)
  - `docs/wiki_gnuradio_org/*.md` (71 chrome-carrying files; `Coding_guide_impl.md` replaced)
- **Approach:**
  1. Script a structure-based chrome remover (Navigation menu, Personal tools, Namespaces, Views, search/footer blocks) applied uniformly to every matching file; verify zero residual markers afterward.
  2. Replace `Coding_guide_impl.md` with the GREP1 coding-guidelines markdown fetched once from `gnuradio/greps` during this maintenance step; keep the page title heading.
  3. Leave the verified-distinct page pairs untouched (UHD sink/source, CRC append/check, Band-pass Filter Taps vs Band Pass Filter, the short block pages) — no merges.
  4. Trigger the app's knowledge-index rebuild and run one sample retrieval against a cleaned page.
- **Patterns to follow:** the 0.6.0-era MediaWiki artifact cleanup conventions noted in `CHANGELOG.md`.
- **Test scenarios:**
  - Test expectation: none — content-only transform with no runtime code path; correctness is the marker grep and the retrieval smoke below.
- **Verification:** `grep -rlE "Navigation menu|Personal tools|Namespaces|Views|Search" docs/wiki_gnuradio_org/` returns nothing; rebuilt index serves a relevant chunk for a query whose answer lives in a cleaned page; fast gate green (`tests/test_adapter_rag.py`).

### U5. Spawn-failure detection in the run monitor

- **Goal:** Runs that die at subprocess spawn are reported as failures.
- **Requirements:** R7
- **Dependencies:** none
- **Files:**
  - `src/grc_agent/exec_monitor.py`
  - `src/grc_agent/desktop_app.py`
  - `tests/test_exec_monitor.py`
- **Approach:**
  1. Constructor gains an optional process-identity provider; `desktop_app` wires it to the canvas manager's current page process.
  2. Snapshot the identity at the start marker.
  3. On a Done marker with no return-code text: unchanged or absent identity records a spawn failure — retain the verbose exception in the log, set the additive `spawn_failed` flag on the retained run record, and route through the existing failure path.
  4. Done markers carrying a return code, and identity changes, behave exactly as today.
- **Patterns to follow:** the epoch-snapshot pattern `wait_for_run_end` already uses for silent no-op detection; the monitor's marker-driven state machine.
- **Test scenarios:**
  - Start marker → verbose text → code-less Done with unchanged identity: failure callback fires, `get_run_log` carries `spawn_failed` and the exception text.
  - Same message sequence with a changed identity: success, no callback, no flag.
  - A code-carrying Done (non-zero) with unchanged identity still fails via the existing return-code path.
  - `wait_for_run_end` resolves `completed` for the spawn-failure sequence (the terminal marker fired).
  - Monitor constructed without a provider (existing tests) keeps current behavior for code-less Done markers.
- **Verification:** `uv run pytest tests/test_exec_monitor.py tests/test_run_stop_tools.py` passes; GTK gate green.

### U6. Scenario default model flip

- **Goal:** The integration suite's default matches the chosen free model.
- **Requirements:** R8
- **Dependencies:** none
- **Files:**
  - `tests/test_integration.py`
- **Approach:**
  1. Change the `_OPENROUTER_DEFAULT_MODEL` fallback from `inclusionai/ling-3.0-flash-fin:free` to `dots-studio/dots-3-note-preview:free`, keeping the `GRC_OPENROUTER_MODEL` override.
  2. Record the accepted `01_add_throttle` consequence beside the constant in one comment line, citing the backlog finding.
- **Patterns to follow:** D2; the existing env-override pattern on that constant.
- **Test scenarios:**
  - Test expectation: none — the constant is integration-marked and outside every gate; no live run is part of this unit.
- **Verification:** fast gate green; grep confirms the new default and the override remain.

### U7. Backlog and changelog truth-up

- **Goal:** The backlog records verified reality for every touched item.
- **Requirements:** R9
- **Dependencies:** U1, U2, U3, U4, U5, U6
- **Files:**
  - `docs/backlog.md`
  - `CHANGELOG.md`
- **Approach:**
  1. Item 8: mark the timer-removal and font-scaler bullets verified complete (with the landed-fix references), the heuristic replacements done via U1–U3, and the off-loop work explicitly deferred.
  2. Item 5.2: replace the stale duplicate-pair and stub-page claims with the verified state — chrome stripped, GREP1 consolidation, no merge pairs among the audited candidates.
  3. Item 6.1: completed via U5; keep 6.2–6.5 as deferred upstream asks.
  4. Item 10: closed as decided per D2, including the accepted scenario failure.
  5. Append the pass's entries to the `CHANGELOG.md` Unreleased section. Preserve the file's existing uncommitted user edits — append, never rewrite them.
- **Test scenarios:**
  - Test expectation: none — documentation only.
- **Verification:** every claim in the touched backlog items matches the landed code and this plan's verification results.

---

## Verification Contract

- **Fast gate:** `uv run pytest tests/ --ignore=tests/test_integration.py --ignore=tests/test_button_integration.py`
- **Linter:** `uv run ruff check`
- **GTK gate (under xvfb if headless):** `xvfb-run -a uv run pytest tests/test_chat_sidebar.py tests/test_chat_sidebar_golden.py tests/test_native_canvas.py tests/test_desktop_app.py tests/test_session_persistence_advanced.py tests/test_context_compaction.py`
- **Corpus:** zero chrome markers by grep; rebuilt index serves a relevant chunk from a cleaned page.
- **No live-LLM runs:** nothing in this plan requires an integration-marked test or real tokens.

## Definition of Done

- All gates above pass with zero errors.
- No `search_mode` substring spellings remain in `src/grc_agent/chat/format.py`; no `openai_codex` literals remain in `src/grc_agent/chat/`.
- One flush cadence drives streaming; the four intervals and char thresholds are gone.
- Corpus carries zero MediaWiki chrome; the pointer stub is consolidated; the index is rebuilt and retrieval-verified.
- Spawn-crashed runs fail visibly with `spawn_failed` truthfully reported.
- Scenario default is `dots-studio/dots-3-note-preview:free` with the override intact.
- `docs/backlog.md` and `CHANGELOG.md` match the landed state; dead or stale backlog claims are deleted, not commented out.
- No abandoned experimental code or dead branches from this pass remain in the diff.

## Appendix: Sources & Research

- **Verified code sites:** `src/grc_agent/chat/format.py` (`_tool_label` nine literals), `src/grc_agent/chat/stream_view.py` (four-interval throttle), `src/grc_agent/chat/settings_controller.py`, `src/grc_agent/chat/turn_driver.py` (codex comparisons), `src/grc_agent/ui/providers.py` (catalog), `src/grc_agent/exec_monitor.py` (marker state machine), `src/grc_agent/desktop_app.py` (monitor wiring), `tests/test_integration.py` (model default).
- **Verified already-complete (backlog drift):** timer disarm in `chat_sidebar.py` (`_remove_timers`/`destroy`/`shutting_down`); single font scaler in `chat/zoom_projection.py`; `Binary_Files_for_DSP.md` absent; zero `Provenance:`/`Aliases:` stub files.
- **GRC ground truth:** `/usr/lib/python3/dist-packages/gnuradio/grc/gui/Executor.py` (constructor try/except: `send_verbose_exec(str(e))` + `send_end_exec()` on spawn failure; `page.process` assigned only on success) and `gnuradio/grc/core/Messages.py` (`send_end_exec(code=0)` renders no return-code text for code 0 — real clean exits and spawn crashes are byte-identical in the console stream).
- **Origin plans:** `docs/plans/2026-09-02-0830-refactor-harness-lean-and-tool-contracts-plan.md` (U16 fenced-out sidebar work), `docs/plans/2026-09-03-0829-refactor-sidebar-decomposition-review-plan.md`.
- **Hermes research session (per user directive, research-only subagent):** live-verified `Coding_guide_impl` as a 116-byte protected pointer stub whose content is "This page has been replaced by GREP1" → `https://github.com/gnuradio/greps/blob/master/grep-0001-coding-guidelines.md`; corroborated by the wiki's ShortPages listing. Near-duplicate pairs (UHD sink/source, CRC append/check, Band-pass Filter Taps vs Band Pass Filter, Binary Slicer / Constellation Decoder / Adaptive Algorithm / Add) reported live-separate from search evidence, fetches throttled — local content inspection independently confirms distinct subjects, so no merge depends on the unverified-today labels.
- **Local corpus audit:** line-set similarity scan over 101 files; 71 files carry MediaWiki chrome markers.
