# GRC-Agent Product Backlog & Client Notes

This document tracks feature requests, feedback, and prospective capabilities
sourced from clients, users, and community interactions — **plus every agreed
future step from design reviews**, so nothing gets forgotten between sessions.

Status legend: 📥 Proposed / Future Work · ⏳ Approved, not started · 🔧 In progress · ✅ Done

---

## 📋 Backlog Items

### 1. Out-of-Tree (OOT) Module Development Support
* **Source/Client**: External client (DoD-affiliated fellowship, academic Ph.D. candidate)
* **Date Added**: July 17, 2026
* **Status**: 📥 Proposed / Future Work

#### Description
Users want the agent to handle the entire lifecycle of custom Out-of-Tree (OOT) modules rather than just integrating already-installed blocks. This includes:
1. **Scaffolding**: Automating directory setup and boilerplate generation using `gr-modtool`.
2. **Code Generation/Editing**: Implementing block processing logic in Python or C++.
3. **Descriptor Creation**: Automatically writing or updating the companion YAML configuration files (`.yml`) defining block signatures, parameters, and ports.
4. **Compilation & Installation**: Orchestrating building (`cmake`, `make`) and installation workflows so GRC can discover the new block.

#### Current Workaround / State
* **Integration is supported**: Any block that is already installed locally is successfully discovered via GNU Radio's block catalog and can be added/configured/wired using [`change_graph`](../src/grc_agent/agent.py#L557).
* **Reusable-block persistence is now supported, but this is not OOT**: [`save_block`](../src/grc_agent/adapter/block_library.py) exports a working Embedded Python Block into GNU Radio's own native hier-block library (`~/.grc_gnuradio`), so it becomes a reusable catalog block for future flowgraphs — closing a narrow slice of this gap (packaging already-working Python logic for reuse). It does not scaffold, generate, or compile a real OOT module; there is still no `gr-modtool` scaffolding, no C++ support, and no build/install orchestration.
* **Development is not supported**: GRC-Agent is currently confined to the flowgraph layout and configuration layer. It lacks tools to interact with codebases outside the flowgraph or run compiler/system commands.

---

### 2. FileSystem tools for `.grc`-folder workflows
* **Source**: Engineering review / product direction (user-driven sessions)
* **Date Added**: 2026-08 (post-harness-capability adoption)
* **Status**: ⏳ Approved in principle — design questions open, implementation NOT started

#### Description
Give the agent real filesystem access so it can work with a user's GNU Radio
project folder — not just the single open flowgraph. The harness ships
pydantic-ai-harness's FileSystem capability (`pydantic_ai_harness.filesystem`),
already reviewed (`/tmp/harness_docs/filesystem.md`); wiring it is the
envisioned implementation.

#### Open design questions (must be settled before implementation)
1. **`untitled.grc`**: GRC creates untitled graphs on startup that don't exist
   on disk. What does a `read_file("untitled.grc")` mean, and should the
   tools be gated on the graph being saved at least once?
2. **Dynamic `root_dir`**: the filesystem tools take a `root_dir` at build
   time. The app's working directory can change at runtime (GRC tracks the
   active flowgraph's directory). Decide: bind `root_dir` to the active
   graph's directory (re-built on tab switch / file-open) vs. a fixed root.
3. **No forced repo-folder development**: the app must not force a
   "repository layout" on users — the tools should work on any folder the
   user opens a `.grc` from, not a prescribed project structure.
4. **Safety envelope**: read-only vs read/write, which globs/paths are
   excluded (`.env`, `~/.config`, `.grc_gnuradio`), and whether writes go
   through the same atomic-save discipline as `adapter/graph.py`.

#### Notes
* Explicitly NOT the OOT toolchain (item 1) — filesystem tools are for
  user-side scripting/helper files, not module build orchestration.

---

### 3. Planner → Executor handoff via `SqlitePlanStore`
* **Status**: ⏳ Approved in concept (implementation was attempted then reverted — see note)
* **Date Added**: 2026-08

#### Description
Make the existing `Planning` capability (in-memory, fresh per run) durable and
cross-run: the model writes a plan in one run and a later run picks it up.
Harness provides `SqlitePlanStore(session=...)` for exactly this.

#### Decisions already agreed
- Use `SqlitePlanStore` co-located on the chat DB (like `SqliteStepStore`),
  keyed by chat session, so a plan survives the turn that created it.
- A sub-agent/planner mode was previously implemented (`test_planner_subagent.py`
  existed) and **reverted** — user asked for investigate-and-report only at
  that time, and the hand-rolled sub-agent layer was removed. Do NOT rebuild
  the sub-agent; the sanctioned path is the harness `Planning` capability +
  `SqlitePlanStore`.

---

### 4. SummarizingCompaction + ConversationSearch + unbounded snapshots
**Status**: ✅ Done — implemented and verified live on Ollama Cloud (2026-08-18). `ResilientSummarizingCompaction` (D2 app-side), `ConversationSearch(scope='conversation')`, `max_snapshots_per_run=None`, `compact_now` button — see AGENTS.md compaction bullet.
**Date Added**: 2026-08

#### The decision (D1/D2/D3)
Replace the current `TieredCompaction` hard-clamp behavior with
`SummarizingCompaction`:

- **D1**: summarizer model inherits the chat model (no separate model config).
- **D2**: summary failures are accepted and degrade gracefully (keep messages)
  — never a hard failure of the turn.
- **D3**: history is kept via `keep_user_messages=True`, `ConversationSearch`
  (over `SnapshotHistorySource(store)`) for mid-conversation retrieval, and
  `max_snapshots_per_run=None` (unbounded snapshots) so no turn is ever
  dropped from the recoverable history.

#### Reference
Harness API: `SummarizingCompaction(max_messages=1, keep_messages=20,
keep_user_messages=True)`, `ConversationSearch(SnapshotHistorySource(store))`.
Docs reviewed at `/tmp/harness_docs/compaction.md`.

---

### 5. `compact_now` button in the chat UI
**Status**: ✅ Done (implemented together with item 4 — the compact_now button drives the same `make_summarizing_strategy()` via the harness `compact_now()`)
**Date Added**: 2026-08

A toolbar/status affordance that manually triggers context compaction
mid-session ("context is getting full" indicator → click to compact), instead
of only automatic tiered compaction. Should integrate with the D3
`ConversationSearch` design so a manual compact never loses retrievable
history.

---

### 6. Model-load popup regression follow-up (DONE — keep on radar)
**Status**: ✅ Done (`0df48ec`)

The `probe_backend` hung-chat guard (one bounded HTTP call answering
reachability + model-membership from the same response) replaced the
`model_listed_warning`/`preflight_connection` split. The Save-path model
mismatch surfaces as a **status-bar warning**, never a popup. Startup warns
non-blockingly in the status bar. If a future UI iteration wants a *visible*
modal again, it must first fix known-issues #4 (modal `.run()` stalls the
event loop) — do not reintroduce `.run()` modals for diagnostics.

---

## 🛠️ How to Add to This Backlog
When a client or community member raises a query or requests a feature:
1. Document the request, the source, and the date.
2. Outline the gap between the current state and the proposed feature.
3. Reference relevant code segments (e.g. tools or adapters) that would be affected.
4. For engineering decisions, record the agreed decision verbatim (like items 3–4) — decisions get forgotten faster than requests.
