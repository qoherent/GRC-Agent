# GRC-Agent Product Backlog

Active feature requests, architectural improvements, and planned capabilities. Completed milestones are documented in detail in [CHANGELOG.md](../CHANGELOG.md).

---

## 📋 Active Backlog & Proposed Capabilities

### 1. Autonomous Run / Debug / Screenshot Loop & Multimodal Context
* **Status**: 🔄 Partial — Text run loop shipped (`run_flowgraph(action='start'|'stop')` + `get_run_log`); vision/multimodal input and artifact capture remain proposed.
* **Shipped (Text Loop)**:
  - Agent controls GRC's native Execute/Stop via unified `run_flowgraph(action='start'|'stop')` (native `Actions.FLOW_GRAPH_EXEC/KILL`, output streaming to GRC console; `start` gated by `ApprovalRequired()`, `stop` ungated, optional `stop_after_seconds` bounded runs).
  - `exec_monitor` completion events (`wait_for_run_end`) and agent-initiated failure-notification suppression.
  - Autonomous probe-verification strategy (probe_rate → message_debug → run → read log).
* **Remaining Proposed Work**:
  - **Vision-model probe**: `resolve_model_vision(provider, model)` inspecting backend capabilities (`/api/show` for Ollama, `/v1/models` for OpenRouter) surfaced as a Settings badge.
  - **V1 data-plane capture**: probe/file-sink block → `numpy.fromfile` → PIL PNG → `ToolReturn(content=[BinaryContent(..., media_type='image/png')])` with numeric fallback for text-only models.
  - **Pictorial Approval Card Previews**: Offscreen Cairo surface rendering (`gui.FlowGraph.draw(cr)` → PNG) to display proposed visual diffs in `ApprovalCard`.
  - **File-RAG tool**: Bounded semantic query tool for large binary project documents (PDF datasheets, CSVs, RF captures) separate from literal line-by-line `read_file`.

---

### 2. Extended RCA Hardening & Verification
* **Status**: 🔄 Partial — Core diagnostics and typography shipped; error attribution and corpus extensions active.
* **Shipped Items**:
  - Port `vlen` visibility in `render_port` and catalog inspection when `vlen ≠ 1`.
  - Friendly retry-exhaustion continuation UX.
  - Native GTK text tags with inter-line spacing and baseline-aligned block tags.
  - Prompt invariants: failed-fix counter-strategy, external grounding nudges, and QT GUI FFT platform rules.
* **Remaining Proposed Work**:
  - **Validation-gate error attribution**: Snapshot pre-batch `iter_error_messages()` to distinguish pre-existing broken graph states from newly introduced errors.
  - **Corpus extension**: Expand local sqlite-vec / FTS5 wiki corpus beyond 94 files to include QT GUI sinks, SDR hardware recipes, and modulation design guides.

---

## 🔍 Known Upstream Limitations & Platform Diagnostics

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

## 📦 Shipped Milestones

| Capability | Shipped Version | Key Deliverables |
| :--- | :---: | :--- |
| **Out-of-Tree (OOT) Module Support** | `0.3.0` / `0.4.0` | `gr-modtool` scaffolding, CMake/C++/Python file editing, `.block.yml` authoring, and block discovery via `shell_tools` + `fs_tools` + `save_block`. |
| **Durable Planner → Executor Handoff** | `0.3.1` | Session-keyed `SqlitePlanStore` co-located with chat DB, cascade cleanups, and dynamic `SystemReminders` `<execution-plan>` handoff. |
| **Research / Deep Planner Mode** | `0.3.1` | Dedicated read-only Planner agent with web search/fetch, docs RAG, and an in-chat `Implement the Plan` handoff. |
| **Deterministic Sugiyama Layout** | `0.4.0` | `adapter/layout.py` full-canvas layout with per-component row bands and barycenter crossing minimization. |
| **Sandboxed Engineering Shell** | `0.4.0` | Project-dir rooted execution, denylist security, session-scoped prefix approval, background process management, and API key scrubbing. |
| **GTK3 Theming & AST Markdown** | `0.3.1` / `0.3.2` | 3-way theme switching (`system`/`dark`/`light`), Pygments syntax highlighting, direct AST walker (`markdown-it-py`), hanging indents, and copy buttons. |
| **Human-in-the-Loop Approvals** | `0.3.2` / `0.4.0` | Pydantic AI native `requires_approval=True` deferred tools, `ApprovalCard` diffs/command rendering, and composer `Mode` button (Manual / Auto / YOLO). |
| **Unified Flowgraph Execution** | `0.4.0` / `0.5.0` | Unified `run_flowgraph(action='start'\|'stop')` tool, bounded runtime `stop_after_seconds`, GRC console streaming, and `exec_monitor` log retention. |

---

## 🛠️ Contributing to the Backlog
When recording new requests or design decisions:
1. Document the requirement, user context, and target scope.
2. Outline specific architecture constraints, safety boundaries, and affected modules.
3. Keep entries concise, actionable, and grounded in the current codebase state.

