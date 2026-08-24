# GRC-Agent Product Backlog

Active feature requests, architectural improvements, and planned capabilities. Completed milestones are documented in [CHANGELOG.md](../CHANGELOG.md).

---

## 📋 Active Backlog

### 1. Out-of-Tree (OOT) Module Development Support
* **Status**: 📥 Proposed / Future Work
* **Scope**: Full lifecycle management of custom GNU Radio OOT modules:
  1. **Scaffolding**: Automating directory setup and boilerplate generation using `gr-modtool`.
  2. **Implementation**: Writing and editing block processing logic in Python or C++.
  3. **Block Descriptors**: Creating and updating companion block YAML configuration files (`.yml`).
  4. **Build & Install**: Orchestrating `cmake`, `make`, and installation workflows so GRC discovers the new block.
* **Current State**: Confined to flowgraph layout/configuration and native hier-block library export via [`save_block`](../src/grc_agent/adapter/block_library.py). Direct compiler, shell, and `gr-modtool` orchestration is not yet supported — but the filesystem tools ([`fs_tools.py`](../src/grc_agent/fs_tools.py)) already let the agent scaffold and edit OOT source/config files (`.py`/`.yml`/CMake/C++ all pass the write-suffix allowlist) inside the flowgraph's folder; building/installing remains the user's job.
* **Research note — shell access**: the harness ships a `Shell` capability/toolset, but it is general command access and conflicts with this app's no-shell/GUI-only security posture; if adopted it must be a narrowly-scoped build capability (`cmake`/`make` allowlist inside the project folder), not the general toolset. The existing `save_block` catalog-refresh machinery (`get_platform().build_library()` + block-tree repopulate) is the ready-made discovery hook once builds exist.

---

### 2. Durable Planner → Executor Handoff (`SqlitePlanStore`)
* **Status**: ✅ Implemented — durable per-session plans, lifecycle cleanup, and lossless pre-compaction transcript retention
* **Scope**: Make the harness `Planning` capability durable across user turns within the same chat session.
* **Key Design Decisions**:
  - Replace `Planning()` with `Planning(store_resolver=…)` keyed on `ctx.conversation_id` (verified present on `RunContext` in installed pydantic-ai 2.31.0) → `SqlitePlanStore(get_db_path(), session='session-{id}')`; `InMemoryPlanStore()` for ungrouped runs. Zero new deps.
  - Co-location on the chat DB is safe: WAL persists at file level; the plan store uses per-op short-lived connections under a lock; `delete_session`/prune need `plan_items` cascade twins of the existing step-row SQL.
  - Planner and executor deliberately share the same `session-{id}` key. The UI permits only one run at a time, the planner has only atomic whole-plan `write_plan`, and the executor cannot write plans, so the store's documented concurrent granular-update race is unreachable.
  - **Handoff mechanism**: the planner atomically replaces the durable store; a `SystemReminders` dynamic reminder renders that store into an ephemeral `<execution-plan>` for the executor on every request. The executor has no `Planning` capability or planning tools.

---

### 3. Research/Planning Front-End Agent ("Deep Planner")
* **Status**: ✅ Implemented — manual read-only Planner mode with durable handoff
* **Scope**: A dedicated planning agent that runs *before* execution: researches online (web search + fetch), reads PDFs and long-form documentation, and hands the GRC-Agent a proper, grounded plan instead of it planning inside the chat turn.
* **Key Design Decisions**:
  - Use Pydantic AI's **programmatic agent hand-off**: the user explicitly enters Planner mode and the app runs a separate `Agent`. After a successful durable plan write, the user explicitly authorizes execution through the in-chat `Implement the Plan` action, which switches to GRC Agent and dispatches the visible handoff turn. No automatic delegation tool, graph workflow, or Deep Agents layer.
  - The durable `SqlitePlanStore` is the plan handoff; ordinary Pydantic AI `message_history` is shared between the agents so planner output, reasoning, and read-tool activity stream through the same chat UI and persist transparently.
  - The planner is structurally read-only via the built-in `PrepareTools` capability over an explicit allowlist: planning (`write_plan`/`read_plan`), graph/file inspection, knowledge lookup, web search/fetch, and run-log reads. `change_graph`, `save_block`, and every filesystem mutation tool are absent—not merely prohibited by prompt text.
  - Reuse the existing provider/model construction for v1, but instantiate a distinct named agent (`grc_planner`) with dedicated planning instructions and StepPersistence identity. A separate stronger model remains a later settings decision, not a prerequisite.
  - Multimodal input (pydantic-ai `core-concepts/input`) may be used by the planner for plots/screenshots when researching — including `DocumentInput` for PDFs if the provider supports it (verify per-provider in the vision spike of item 4).
  - **Grounding discipline stays the same**: the planner is subject to the same docs-first rules as the executor — plans cite their sources so the executor can re-verify cheaply (the `query_knowledge` corpus + `web_search` are shared).
  - The Context row's native planner switch is disabled during a run and paired with a dynamic `Planner active`/`GRC-Agent Active` label. On an empty chat it waits for the user's goal; mid-session it sends a visible “create or revise” turn through the planner. After a successful `write_plan`, the UI verifies the durable store and adds an in-chat `Implement the Plan` button; clicking it flips to the executor and dispatches a visible implementation turn automatically. Loading/clearing/switching sessions resets safely to executor mode.
  - Planner and executor turns append to the same canonical `ModelMessage` history and `session-{id}` conversation. `ThinkingPart`s persist in the session blob and pre-compaction StepPersistence snapshots; run rows distinguish `grc_planner` from `grc_executor` for dataset reconstruction.
  - Live-verified on Ollama Cloud `deepseek-v4-flash:0731`: planner called `inspect_graph` + `write_plan`, produced a visible two-step plan, executor received it without planning calls, and the fixture remained byte-identical.

---

### 4. Autonomous Run / Debug / Screenshot Loop (multimodal context)
* **Status**: 💡 Agreed direction
* **Scope**: Let the agent run flowgraphs itself, debug outputs, and take screenshots — then feed screenshots + run logs back into the model as multimodal input for richer context (pydantic-ai [`core-concepts/input`](https://pydantic.dev/docs/ai/core-concepts/input/)).
* **Key Design Decisions**:
  - Extends the existing `get_run_log`/`exec_monitor` machinery (return-code notification, full-log tool) rather than replacing it.
  - Screenshots of QT GUI sinks (spectrograms, scopes, constellation plots) captured headlessly/offscreen; images returned as tool results via `BinaryContent` so the model *sees* the plot, not a text description of it.
  - **V1 data-plane capture** (research-grounded): probe/file-sink block → `numpy.fromfile` → PIL PNG → `ToolReturn(content=[BinaryContent(..., media_type='image/png')])` — verified in installed pydantic-ai 2.31.0 that OpenAIChatModel (our Ollama `/v1` path) maps this to a base64 `image_url` user part, Codex/Anthropic map it natively, and session DB + `SqliteStepStore`'s 64 KiB media externalization round-trip it. V2 (literal X11 QT-window screenshots) is a separate spike with Wayland/PID uncertainties.
  - **Prerequisite**: a live vision-model spike (all current default models are text-only; no uniform vision-capability probe exists) — pick/configure a vision-capable model first.
  - **Companion**: adopt the harness `tool_output_limits` capability (`Spill`/`Truncate` with `read_tool_result` read-back) so large tool outputs — and any captured artifacts — park lossless handles out of context instead of flooding it (production-time complement to the compaction stack).
  - **✅ Shipped**: wired in `agent_factory.py` — `Band(over=20_000)` spills oversized tool returns to `.grc_agent/tool_overflow` with `read_tool_result` read-back (see AGENTS.md Tool Surface).
  - **Research note — large files**: the data-analyst pattern's answer to big files is *engine-side analysis*, not bigger context: park the object out-of-context and compute over it (DuckDB-style). For us that maps to a bounded, read-only `query_file` tool over big project `.csv/.json` (SQL SELECT gate, no write statements) rather than raising the 1000-line read cap — MAYBE, pending a real use case.

---

### 5. Deterministic Block Placement (algorithm-based layout)
* **Status**: ✅ Shipped — `adapter/layout.py` `compute_full_layout()` (header band + grandalf Sugiyama-style flow band, full-canvas relayout from **any topology-changing `change_graph` batch**: `add_blocks`/`remove_blocks`/`add_connections`/`remove_connections` — one uniform rule added 2026-08-24, so a later wire-only call re-ranks blocks that were added unwired instead of freezing the stale alphabetical stack; the old `add_blocks`-only gate is gone). The sketch below is kept for the record; the implementation is authoritative (see AGENTS.md's layout conventions).
* **Scope**: Fix the "blocks thrown at random places" problem. When `change_graph` changes topology (add/remove blocks or connections), the whole workspace must be rearranged deterministically — and **positions must stay algorithm-based, never LLM-based**: block coordinates are deliberately filtered out of the model's context (they would flood it and confuse it), so the model can never be asked to choose positions.
* **User requirements (paraphrased)**:
  - **Variables/parameters first**: all variable and parameter blocks are listed horizontally along the top of the workspace — the universal GNU Radio convention. Order doesn't matter, but alphabetical is preferred.
  - **Full rearrangement on add**: whenever a new component is added, the agent arranges the *entire* workspace, not just the new block — moving existing components is cheap and expected.
* **Proposed algorithm (rough sketch, to be refined)**:
  - Split the workspace into a grid of defined cells (e.g. 150×150 px).
  - Row 1: all variables/parameters (alphabetical).
  - Remaining rows: place components column-by-column following connection topology — e.g. blocks with no inputs go in the first column (stacked vertically), their consumers in the next column, and so on (a Sugiyama-style rank assignment; the existing `adapter/layout.py` grandalf machinery already does rank assignment and can be extended rather than replaced).
* **Constraints**: must stay fully automatic (no model input), deterministic, and must not fight manual user drags (the current rule: relayout runs only from `change_graph`, never on manual edits — keep that).

---

### 6. Shell Tool Access & Sandboxed Execution
* **Status**: 📥 Proposed / Research
* **Scope**: Provide the agent with safe, structured shell execution capabilities to run build commands (e.g., `cmake`, `make`, `gr-modtool`), run standalone scripts, and inspect system environments.
* **References**:
  - [PydanticAI Harness Shell](https://pydantic.dev/docs/ai/harness/shell/)
  - [PydanticAI Harness Modal Sandbox](https://pydantic.dev/docs/ai/harness/modal-sandbox/)
* **Key Considerations & Investigation Points**:
  - **Hardware & User Permission Investigation**: Investigate how shell execution interacts with host device permissions (e.g., SDR USB access, missing `udev` rules, `plugdev`/`usrp` group membership) to automatically detect or prevent permission errors without requiring the user to run full GUI IDEs or workflows with `sudo`.
  - **Security & Sandboxing Boundaries**: Evaluate local directory-sandboxed allowlists vs. isolated execution (e.g. Modal sandboxes or local containers), balancing safety against the need to access host GNU Radio C++ bindings and physically connected SDR hardware.

---

### 7. Native GTK3 Theming & Optional Canvas Dark Palette
* **Status**: ✅ Shipped (UI & Sidebar Theming); 📥 Proposed (Optional Cairo Canvas Dark Mode)
* **Scope**:
  - **✅ Shipped in 0.3.1**: Pure GTK3 symbolic theming (`@theme_bg_color`, `@theme_fg_color`, `@theme_selected_bg_color`, and `alpha(@theme_fg_color, ...)`), 3-way theme switching (`system`, `dark`, `light`) paired with native installed desktop themes (`Yaru-dark`, `Adwaita-dark`), and dynamic Pygments syntax highlighting (`monokai` vs `friendly`) based on background relative luminance.
  - **📥 Future / Optional**: Dynamic in-memory patch for GNU Radio's Cairo canvas palette (`gnuradio.grc.gui.canvas.colors`) to optionally support dark schematic canvas backgrounds while preserving port data-type color legibility.

---

### 8. Flowgraph Change Approval (human-in-the-loop gate)
* **Status**: ✅ Implemented — pydantic-ai native `requires_approval=True` + `DeferredToolRequests`/`ToolApproved`/`ToolDenied`; `ApprovalCard` in-chat UI (reason + structured summary + Approve/Deny/Always-accept); persisted `GRC_AGENT_APPROVE_CHANGES` gate with composer `Mode` toggle (Manual = ask / Auto = apply without asking).
* **Origin**: Intern feedback — "a change approval button before it rewires and edits the whole flowgraph … a pictorial representation of the graph with the recommended changes, as well as a description of what will be changed and why. This prevents me from having to load my back up files every time the agent makes a change I didn't ask for."
* **Key Design Decisions**:
  - Use pydantic-ai's **native deferred-tool approval** (the same mechanism the harness's `exa` capability uses): the tool body never executes before consent — no hand-rolled gate, no interception layer, and the 1.5s safety-net poll has no unapproved mutation to auto-write by construction.
  - `change_graph` gains a required `reason: str` (one-sentence intent) shown with the change and echoed into the tool result for transcript self-description.
  - The gate is static per tool (never dynamic per-turn logic) and persists in `.env` (`GRC_AGENT_APPROVE_CHANGES` = `ask` default | `always`); the composer's `Mode` toggle (Manual/Auto) re-enables it anytime.
  - Approval pauses the SAME turn (run ends with `DeferredToolRequests`, resume with `deferred_tool_results=`); denial feeds back to the model via `ToolDenied`, and the system prompt forbids re-submitting a denied edit.
  - **Pictorial preview (PNG of the proposed graph)**: not yet shipped — the offscreen render recipe is verified (gui FlowGraph `draw(cr)` → pycairo `ImageSurface`; `element.highlighted` marks proposed changes) and is a natural follow-up on the card's summary text.

---

### 9. RCA-Derived Hardening (evidence-verified)
* **Status**: 🔄 Partial — items 1, 2, 5, 6 implemented 2026-08-24 (✅ below); items 3, 4 remain proposed. Root causes verified by two adversarial subagent rounds against the intern feedback (reports `/tmp/grc_rca_*.md`, `/tmp/grc_verify_*.md`).
* **Items**:
  1. **✅ `vlen` visibility on ports** — `render_port`/`render_catalog_block` omit `port.vlen` (native GRC attribute, `port.py:23-24`), so an `fft_vxx` (vlen 1024) vs scalar-sink mismatch reads as an opaque "8 vs 8192" byte puzzle; emit `vlen` when ≠ 1. Fix-at-source, one uniform rule; kills the need for any item-size error rewriting. (2026-08-24: `render_port` and `_catalog_port_info` emit `vlen` when ≠ 1, live + catalog; both tested.)
  2. **✅ Retry-exhaustion UX** — 4 consecutive `change_graph` failures end the turn with a raw `UnexpectedModelBehavior` bubble ("crashed with an error message" in user terms). Surface it as a bounded continuation message ("out of fix attempts this turn — graph unchanged; send Continue"). (2026-08-24: `_friendly_exhaustion_message` in `chat_sidebar.py` renders the continuation text for both tool-retry and output-validation exhaustion; tested.)
  3. **Validation-gate error attribution** — GRC's `iter_error_messages()` natively yields `(element, message)`; snapshot the pre-batch error-element set and report pre-existing vs newly-introduced errors separately, so a fix batch isn't blamed for unrelated pre-existing graph errors (verified: even a trivially valid add is rejected on a broken graph, citing only the pre-existing error).
  4. **Corpus extension** — the shipped wiki corpus (94 files) has no QT GUI sink pages, no FM-stereo/pilot-tone recipes, and lexical queries return false positives (OFDM "pilot symbols" for "pilot tone"). Add wiki pages as data (never code heuristics).
  5. **✅ Code-block readability (spacing part)** — GTK3-native `pixels-above/below-lines` spacing on chat TextViews (verified capability; GTK3 CSS has no `line-height`), a prompt rule that list-like content belongs in Markdown lists, not fences, and a soft-wrap toggle for code fences (the last remains proposed). (2026-08-24: 3px spacing on CodeBlock + prose TextViews with the height pin updated to include per-line spacing; prompt formatting rule added; tested.)
  6. **✅ Prompt/grounding fixes (RCA-derived)** — the system prompt now carries: the failed-fix counter-strategy (never repeat a failed fix — re-inspect, re-ground, reconsider topology), the external-grounding nudge for concepts local knowledge can't cover (tool-agnostic, respecting the no-tool-enumeration rule), the QT GUI freq-sink-owns-its-FFT platform quirk (the semantic fact behind the intern's FFT/buffer-size loop, otherwise present in no yml, catalog payload, or corpus page), the approval/reason contract, and the formatting rule (lists as Markdown lists, fences for code only). All are prompt-level data — no per-scenario code heuristics.

---

## 🛠️ Contributing to the Backlog
When recording new requests or design decisions:
1. Document the requirement, user context, and target scope.
2. Outline specific architecture constraints, safety boundaries, and affected modules.
3. Keep entries concise, actionable, and grounded in the current codebase state.
