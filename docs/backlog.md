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
  - Known caveat: documented last-write-wins race on `update_item` if a planner and executor share one session key concurrently — planner and executor must use distinct session keys (e.g. `session-{id}-plan`).
  - Follows standard harness `Planning` capabilities without introducing custom sub-agent layers.
  - **Handoff mechanism (research-verified)**: the store IS the whole protocol — `set_items` is an atomic full-plan write; the executor's `<plan-reminder>` is rebuilt from the store on every request, so a plan written by a separate planner agent run is visible to the executor with zero custom plumbing and no planner tokens burned at execution time.

---

### 3. Research/Planning Front-End Agent ("Deep Planner")
* **Status**: 📐 Design grounded in Pydantic AI built-ins — not implemented
* **Scope**: A dedicated planning agent that runs *before* execution: researches online (web search + fetch), reads PDFs and long-form documentation, and hands the GRC-Agent a proper, grounded plan instead of it planning inside the chat turn.
* **Key Design Decisions**:
  - Use Pydantic AI's **programmatic agent hand-off**: the user explicitly enters Planner mode, the app runs a separate `Agent`, and the user explicitly switches back to GRC Agent mode when satisfied. No automatic delegation tool, graph workflow, or Deep Agents layer.
  - The durable `SqlitePlanStore` is the plan handoff; ordinary Pydantic AI `message_history` is shared between the agents so planner output, reasoning, and read-tool activity stream through the same chat UI and persist transparently.
  - The planner is structurally read-only via the built-in `PrepareTools` capability over an explicit allowlist: planning (`write_plan`/`read_plan`), graph/file inspection, knowledge lookup, web search/fetch, and run-log reads. `change_graph`, `save_block`, and every filesystem mutation tool are absent—not merely prohibited by prompt text.
  - Reuse the existing provider/model construction for v1, but instantiate a distinct named agent (`grc_planner`) with dedicated planning instructions and StepPersistence identity. A separate stronger model remains a later settings decision, not a prerequisite.
  - Multimodal input (pydantic-ai `core-concepts/input`) may be used by the planner for plots/screenshots when researching — including `DocumentInput` for PDFs if the provider supports it (verify per-provider in the vision spike of item 4).
  - **Grounding discipline stays the same**: the planner is subject to the same docs-first rules as the executor — plans cite their sources so the executor can re-verify cheaply (the `query_knowledge` corpus + `web_search` are shared).

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
  - **Research note — large files**: the data-analyst pattern's answer to big files is *engine-side analysis*, not bigger context: park the object out-of-context and compute over it (DuckDB-style). For us that maps to a bounded, read-only `query_file` tool over big project `.csv/.json` (SQL SELECT gate, no write statements) rather than raising the 1000-line read cap — MAYBE, pending a real use case.

---

### 5. Deterministic Block Placement (algorithm-based layout)
* **Status**: 💡 Agreed direction — design open
* **Scope**: Fix the "blocks thrown at random places" problem. When `change_graph` adds blocks (or any new component appears), the whole workspace must be rearranged deterministically — and **positions must stay algorithm-based, never LLM-based**: block coordinates are deliberately filtered out of the model's context (they would flood it and confuse it), so the model can never be asked to choose positions.
* **User requirements (paraphrased)**:
  - **Variables/parameters first**: all variable and parameter blocks are listed horizontally along the top of the workspace — the universal GNU Radio convention. Order doesn't matter, but alphabetical is preferred.
  - **Full rearrangement on add**: whenever a new component is added, the agent arranges the *entire* workspace, not just the new block — moving existing components is cheap and expected.
* **Proposed algorithm (rough sketch, to be refined)**:
  - Split the workspace into a grid of defined cells (e.g. 150×150 px).
  - Row 1: all variables/parameters (alphabetical).
  - Remaining rows: place components column-by-column following connection topology — e.g. blocks with no inputs go in the first column (stacked vertically), their consumers in the next column, and so on (a Sugiyama-style rank assignment; the existing `adapter/layout.py` grandalf machinery already does rank assignment and can be extended rather than replaced).
* **Constraints**: must stay fully automatic (no model input), deterministic, and must not fight manual user drags (the current rule: relayout runs only from `change_graph`'s `add_blocks` path, never on manual edits — keep that).

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

## 🛠️ Contributing to the Backlog
When recording new requests or design decisions:
1. Document the requirement, user context, and target scope.
2. Outline specific architecture constraints, safety boundaries, and affected modules.
3. Keep entries concise, actionable, and grounded in the current codebase state.
