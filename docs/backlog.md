# GRC-Agent Product Backlog

Active feature requests, architectural improvements, and planned capabilities. Completed milestones are documented in [CHANGELOG.md](../CHANGELOG.md).

---

## 📋 Active Backlog

### 1. Out-of-Tree (OOT) Module Development Support
* **Status**: ✅ Shipped & Enabled (via `shell_tools` + `fs_tools` + `save_block` composition)
* **Scope**: Full lifecycle management of custom GNU Radio OOT modules:
  1. **Scaffolding**: Automating directory setup and boilerplate generation using `gr-modtool` via approved shell execution.
  2. **Implementation**: Writing and editing block processing logic in Python or C++ via sandboxed filesystem tools (`write_file`/`edit_file`).
  3. **Block Descriptors**: Authoring companion block YAML configuration files (`.yml`).
  4. **Build & Install**: Running `cmake`, `make`, `ctest`, and installation workflows inside the project directory.
  5. **Discovery**: Live block catalog reloading through `save_block`'s discovery hooks (`get_platform().build_library()` + block-tree repopulate).

---

### 2. Durable Planner → Executor Handoff (`SqlitePlanStore`)
* **Status**: ✅ Implemented — durable per-session plans, lifecycle cleanup, and lossless pre-compaction transcript retention
* **Scope**: Make the harness `Planning` capability durable across user turns within the same chat session.
* **Key Design Decisions**:
  - Replace `Planning()` with `Planning(store_resolver=…)` keyed on `ctx.conversation_id` → `SqlitePlanStore(get_db_path(), session='session-{id}')`; `InMemoryPlanStore()` for ungrouped runs. Zero new dependencies.
  - Co-location on the chat DB is safe: WAL persists at file level; the plan store uses per-op short-lived connections under a lock; `delete_session`/prune cascade `plan_items` rows.
  - Planner and executor deliberately share the same `session-{id}` key. The UI permits only one run at a time, the planner has atomic `write_plan`, and the executor cannot write plans.
  - **Handoff mechanism**: the planner atomically replaces the durable store; dynamic `SystemReminders` render that store into an ephemeral `<execution-plan>` for the executor on every request.

---

### 3. Research/Planning Front-End Agent ("Deep Planner")
* **Status**: ✅ Implemented — manual read-only Planner mode with durable handoff
* **Scope**: A dedicated planning agent that runs *before* execution: researches online (web search + fetch), reads documentation, and hands the GRC-Agent a grounded plan.
* **Key Design Decisions**:
  - Programmatic agent hand-off: the user explicitly enters Planner mode. After a successful durable plan write, the in-chat `Implement the Plan` action switches to GRC-Agent and dispatches the implementation turn.
  - The planner is structurally read-only via `PrepareTools` over an explicit allowlist: planning (`write_plan`/`read_plan`), graph/file inspection, knowledge lookup, web search/fetch, and run-log reads. Mutation tools (`change_graph`, `save_block`, filesystem writes, shell commands) are absent.
  - Shared canonical `ModelMessage` history and `session-{id}` conversation. `ThinkingPart`s persist in the session blob and StepPersistence snapshots; run rows distinguish `grc_planner` from `grc_executor`.

---

### 4. Autonomous Run / Debug / Screenshot Loop & Multimodal Context
* **Status**: 🔄 Partial — TEXT run loop shipped (`run_flowgraph(action='start'|'stop')` + `get_run_log`); vision/multimodal input and artifact capture remain proposed.
* **Shipped (Text Loop)**:
  - Agent controls GRC's native Execute/Stop via unified `run_flowgraph(action='start'|'stop')` (native `Actions.FLOW_GRAPH_EXEC/KILL`, output streaming to GRC console; `start` gated by `ApprovalRequired()`, `stop` ungated).
  - `exec_monitor` completion events (`wait_for_run_end`) and agent-initiated failure-notification suppression.
  - Autonomous probe-verification strategy (probe_rate → message_debug → run → read log).
* **Remaining (Future Work)**:
  - **Vision-model probe**: `resolve_model_vision(provider, model)` inspecting backend capabilities (`/api/show` for Ollama, `/v1/models` for OpenRouter) surfaced as a Settings badge.
  - **V1 data-plane capture**: probe/file-sink block → `numpy.fromfile` → PIL PNG → `ToolReturn(content=[BinaryContent(..., media_type='image/png')])` with numeric fallback for text-only models.
  - **Pictorial Approval Card Previews**: Offscreen Cairo surface rendering (`gui.FlowGraph.draw(cr)` → PNG) to display proposed visual diffs in `ApprovalCard`.
  - **File-RAG tool**: Bounded semantic query tool for large binary project documents (PDF datasheets, CSVs, RF captures) separate from literal line-by-line `read_file`.

---

### 5. Deterministic Block Placement (algorithm-based layout)
* **Status**: ✅ Shipped — `adapter/layout.py` Sugiyama-style full-canvas layout with per-component row bands.
* **Scope**: Automatic deterministic layout computed whenever `change_graph` alters topology (`add_blocks`/`remove_blocks`/`add_connections`/`remove_connections`).
* **Design**: Variables and parameters placed in horizontal header row; connected components placed in independent row bands with barycenter crossing-minimization sweeps. Block coordinates are strictly algorithm-computed and filtered out of model context.

---

### 6. Shell Tool Access & Sandboxed Execution
* **Status**: ✅ Shipped — Sandboxed engineering shell with consent granularity.
* **Scope**: Full engineering execution (builds, SDR vendor CLIs, standalone scripts, data analysis) rooted in the configured project directory.
* **Design**:
  - **Denylist policy**: Harness destructive commands denied; all engineering tools available.
  - **Approval boundary**: `run_command`/`start_command` require user approval showing literal commands; session-scoped prefix-allow ("Always allow `<command>`") reduces friction.
  - **Process management**: Background jobs tracked with timeout enforcement and process-group cleanup. Environment scrubbing derived from provider catalog.

---

### 7. Native GTK3 Theming & Canvas Palette
* **Status**: ✅ Shipped in 0.3.1 (UI & Markdown Theming); 📥 Proposed (Optional Cairo Canvas Dark Mode)
* **Scope**:
  - **✅ Shipped**: GTK3 symbolic theming (`@theme_bg_color`, `@theme_fg_color`, `@theme_selected_bg_color`), 3-way theme switching (`system`, `dark`, `light`), and dynamic Pygments syntax highlighting (`monokai` vs `friendly`).
  - **📥 Future / Optional**: Dynamic in-memory patch for GNU Radio's Cairo canvas palette (`gnuradio.grc.gui.canvas.colors`) to support dark schematic backgrounds while preserving port data-type color legibility.

---

### 8. Flowgraph Change Approval (human-in-the-loop gate)
* **Status**: ✅ Implemented — pydantic-ai native `requires_approval=True` + `DeferredToolRequests`/`ToolApproved`/`ToolDenied`.
* **Scope**: `ApprovalCard` in-chat UI (reason + structured summary + Approve/Deny/Always-accept) and persisted `GRC_AGENT_APPROVE_CHANGES` gate with composer `Mode` toggle (Manual = ask / Auto = apply without asking).

---

### 9. RCA-Derived Hardening & Verification
* **Status**: 🔄 Partial — Items 1, 2, 5, 6 shipped; Items 3, 4 active.
* **Items**:
  1. **✅ `vlen` visibility on ports**: `render_port` and catalog inspection emit `vlen` when ≠ 1.
  2. **✅ Retry-exhaustion UX**: Friendly continuation message rendered when retry budget is exhausted.
  3. **📥 Validation-gate error attribution**: Snapshot pre-batch `iter_error_messages()` to distinguish pre-existing broken graph states from newly introduced errors.
  4. **📥 Corpus extension**: Expand local sqlite-vec / FTS5 wiki corpus beyond 94 files to include QT GUI sinks, SDR hardware recipes, and modulation design guides.
  5. **✅ Code-block and prose typography**: Native GTK text tags with inter-line spacing and baseline-aligned block tags.
  6. **✅ Prompt/grounding invariants**: Failed-fix counter-strategy, external grounding nudges, and QT GUI FFT platform rules.

---

## 🔍 Known Limitations & Upstream Diagnostics to Address

1. **GTK3 Nested Submenus (File → New) on Wayland Compositors**:
   - *Observation*: On native Wayland sessions under certain compositors (e.g. Mutter / GNOME on Ubuntu 26.04), nested GTK3 menu popups fail to map due to unhandled XDG grab events.
   - *Planned Resolution*: Detect native Wayland display sessions at startup and display a subtle status-bar note advising `GDK_BACKEND=x11 uv run grc-agent`.

2. **`no_gui` Flowgraph External Terminal Logging**:
   - *Observation*: For `generate_options: no_gui` flowgraphs, upstream GRC wraps execution in `x-terminal-emulator`. The subprocess pipe captures the terminal wrapper stdout rather than the flowgraph output.
   - *Planned Resolution*: Detect terminal wrapper execution in `exec_monitor` and annotate `get_run_log` with an external-terminal note.

3. **ExecFlowGraphThread Early Spawn Failures**:
   - *Observation*: If spawning the run subprocess raises inside GRC (`gnuradio/grc/gui/Executor.py:44`), GRC's exception handler emits `send_end_exec()` with a default return code of 0.
   - *Planned Resolution*: Track process PID allocation directly to distinguish immediate spawn crashes from successful zero-exit runs.

4. **ChatGPT (Codex) Reasoning Summary Annotations**:
   - *Observation*: OpenAI does not expose raw reasoning tokens for GPT-5.x, returning concise high-level summaries by design.
   - *Planned Resolution*: Annotate the thinking expander with a summary indicator when using the Codex provider so brevity is not mistaken for a rendering bug.

---

## 🛠️ Contributing to the Backlog
When recording new requests or design decisions:
1. Document the requirement, user context, and target scope.
2. Outline specific architecture constraints, safety boundaries, and affected modules.
3. Keep entries concise, actionable, and grounded in the current codebase state.
