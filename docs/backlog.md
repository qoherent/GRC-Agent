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
* **Status**: 🔄 Partial — Core diagnostics and typography shipped; validation-gate attribution and docstring enrichment shipped; wiki-corpus expansion active.
* **Shipped Items**:
  - Port `vlen` visibility in `render_port` and catalog inspection when `vlen ≠ 1`.
  - Friendly retry-exhaustion continuation UX.
  - Native GTK text tags with inter-line spacing and baseline-aligned block tags.
  - Prompt invariants: failed-fix counter-strategy, external grounding nudges, and QT GUI FFT platform rules.
  - **Validation-gate error attribution**: pre-batch `iter_error_messages()` snapshot distinguishes pre-existing broken graph state from the agent's own mutations; only new errors raise `ModelRetry`.
  - **Catalog docstring enrichment**: implementation-class docstrings (parameter units/semantics) embedded into catalog payloads offline; validated 7/7 units/semantics queries at rank ≤ 2.
* **Remaining Proposed Work**:
  - **Wiki-corpus expansion**: the corpus still lacks dedicated pages for some topics (the 2026-08-28 ground-truth stress run left 4 queries missing in *both* engines — a corpus-coverage ceiling, not ranking); expansion beyond 94 files to QT GUI sinks, SDR hardware recipes, and modulation design guides remains.
  - **Corpus de-duplication**: `Binary_Files_for_DSP.md` duplicates 43/63 long lines of `Reading_and_Writing_Binary_Files.md`; synthetic stub pages (`Provenance:`/`Aliases:` meta-text) split ranking mass in both engines. De-duplicate/stub-out before re-running the ground-truth stress matrix.
  - **`output_truncated` density watch**: on the 568-block catalog the flag fired 7/7 in the stress run (honest, near-always true). If flag-fatigue is ever measurable, revisit the rejected count-field variant (`more_available: N`) — recorded in `docs/investigation/hybrid-fusion-and-docs-truncation-design.md` R3.

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

## 🔧 Upstream Issues to File (diagnosed in the 2026-08-28 sessions-150/151 forensic + grounding reports)

1. **pydantic-ai-harness `Shell` spawn stdin** (`pydantic_ai_harness/shell/_toolset.py:342-348`, `:422-428` in 0.23.0; still absent on main post-0.27.0):
   - *Observation*: `anyio.open_process(..., stdout=PIPE, stderr=PIPE)` with no `stdin=` — anyio's unset default (`-1` == `PIPE`) hands the child a fresh pipe nobody writes or closes until the post-timeout `aclose()`. A command that ends up reading stdin (`grep -A6 "x" $(find /nonexistent …)` — empty file operand) blocks until the full default timeout (observed: 600 s of a real session).
   - *Planned Resolution*: upstream issue proposing `stdin=subprocess.DEVNULL` at both spawn sites. Reproduced: blocks at any time cap, exits 0.00 s with DEVNULL. Harness #623 (0.26.0) covers spawn *failures*, not this hang.
2. **stackone-defender tier-1 `shell_command` pattern refinement** (`stackone_defender/classifiers/patterns.py:124` in 0.7.4 — mitigated locally by detect-and-log):
   - *Observation*: regex `\$\([^)]+\)` (medium) escalates to high on 2 matches + an entropy flag; official doxygen pages' own jQuery boilerplate (`$(document)` ×2) deterministically trip it → whole fetch withheld with `block_high_risk=True`. No pattern-level config, domain exemption, or tier-1 threshold knob exists up through 0.8.2.
   - *Planned Resolution*: upstream issue proposing exclusion of benign `$(identifier)` JS patterns from command-substitution matching, or a withhold/severity knob on the harness defender.
3. **ConversationSearch misses interrupted captures**:
   - *Observation*: harness docs: `SnapshotHistorySource` "recovers original messages by unioning snapshots … while excluding interrupted captures" — tool calls cancelled with a Stop press (e.g. s150's two `run_command`s, retained only in `state=interrupted` snapshots) are invisible to in-app conversation search; only direct DB reads reach them.
   - *Planned Resolution*: raise with upstream; sanctioned in-session recovery already exists (`continue_run(include_interrupted=True)`).
4. **Catalog `distance` semantics for non-vector rows**:
   - *Observation*: lexical-sourced catalog rows render `distance: 0.0` (pre-existing convention, `rag.py` render loop), which reads as a perfect vector match; under hybrid, the truthful fused `score` field carries the ranking signal instead.
   - *Planned Resolution*: make `distance` nullable/omitted for non-vector rows in a lean pass (rejected-for-now in the design report R7 — touches every catalog-shape assertion; bundle with the next render-surface change).

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

