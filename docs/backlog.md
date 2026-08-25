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
* **Current State**: Confined to flowgraph layout/configuration and native hier-block library export via [`save_block`](../src/grc_agent/adapter/block_library.py). With the shell capability shipped (backlog item 6), the build half is now agent-reachable: `gr_modtool` scaffolding, `cmake`/`make`/`ctest` builds, and installation all run as approved shell commands inside the project folder, and `save_block`'s catalog-refresh machinery (`get_platform().build_library()` + block-tree repopulate) is the ready-made discovery hook after an install. Remaining gap: no structured orchestration of the OOT *lifecycle* (modtool newblock → edit → build → reload catalog in one taught flow) — currently left to the model composing shell + fs tools.

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
* **Status**: 🔄 Partial — the TEXT half of the loop is shipped (`run_flowgraph`/`stop_flowgraph` + `get_run_log`, 2026-08-26); vision/multimodal input and artifact capture remain proposed.
* **Scope**: Let the agent run flowgraphs itself, debug outputs, and take screenshots — then feed screenshots + run logs back into the model as multimodal input for richer context (pydantic-ai [`core-concepts/input`](https://pydantic.dev/docs/ai/core-concepts/input/)).
* **Shipped (text loop)**: the agent triggers GRC's native Execute/Stop via approval-gated `run_flowgraph`/`stop_flowgraph` (native `Actions.FLOW_GRAPH_EXEC/KILL`, output streams to GRC's console for the user; `exec_monitor` gained a completion event + agent-initiated failure-notification suppression; `get_run_log` gained `run_in_progress`). The probe-verification strategy (probe_rate → message_debug → run → read log) is now fully in-turn.
* **Remaining (future)**:
  - **Vision-model probe** — `resolve_model_vision(provider, model)` in the shape of `resolve_model_context_length` (Ollama `/api/show` → `capabilities.vision`; OpenRouter `/v1/models` → `architecture.input_modalities`; cached, negative-TTL), surfaced as a Settings badge. Prerequisite for any image input: none of the default models are vision-capable.
  - **V1 data-plane capture** (research-grounded): probe/file-sink block → `numpy.fromfile` → PIL PNG → `ToolReturn(content=[BinaryContent(..., media_type='image/png')])` — verified in installed pydantic-ai 2.31.0 that OpenAIChatModel (our Ollama `/v1` path) maps this to a base64 `image_url` user part, Codex/Anthropic map it natively, and session DB + `SqliteStepStore`'s 64 KiB media externalization round-trip it. Works headless and for text-only models (same data → numeric summary fallback when the model has no vision).
  - **V2 literal window screenshots**: X11-only, cross-process window capture, Wayland-uncertain — a separate spike.
  - **File-RAG tool** for large/binary project artifacts (PDF, Excel, big CSV/JSON): a SEPARATE bounded query tool in the `query_knowledge` shape (relevant extracts + citations over the fs-sandbox root), NOT a patch inside `read_file` — `read_file`'s contract is verbatim lines + hash; a RAG layer's contract is relevant extracts; merging them muddies both and couples injection-defender classification to two output shapes. Run-tool outputs need no changes: sink files land in the project dir and are discoverable from `inspect_graph` params, so consumption routes by file type (text → read_file; raw IQ/numeric → shell + numpy compute-over-data; documents → the future file-RAG tool).

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
* **Status**: ✅ Shipped (2026-08-26) — full-shell sweet spot, NOT a build-tool allowlist
* **Scope**: Give the executor agent general shell execution (builds, SDR CLIs, standalone scripts, data wrangling) with risk managed by CONSENT GRANULARITY instead of hand-picked command lists.
* **Key Design Decisions** (grounded by two deepseek subagent rounds against harness 0.23.0 + GRC sources):
  - **Denylist mode, never allowlist**: the harness's ten destructive defaults (`rm`, `mkfs`, `dd`, ...) stay denied; every engineering command (cmake/make/python3/uhd_*/SoapySDRUtil/rtl_*/project scripts/pipes) is available. Rationale: the GR engineer's command surface is not enumerable (12+ SDR vendor CLI families alone); an allowlist is a forecast of user tasks whose failure mode is a crippled agent, and AGENTS.md forbids hand-picked heuristics. `GRC_SHELL_DENIED_COMMANDS` in `.env` lets a user tighten or loosen.
  - **Approval is the boundary**: `run_command`/`start_command` are re-registered with `requires_approval=True` → the same native `DeferredToolRequests` + `ApprovalCard` flow as `change_graph`, one uniform Manual/Auto gate (`GRC_AGENT_APPROVE_CHANGES`). Manual mode shows the FULL LITERAL COMMAND on the card. Shell cards get a session-scoped "Always allow `<first-token>`" (prefix-allow) instead of the persisted global gate-off — approve `cmake` once, the rest of the build flows. Auto mode (explicit user choice) approves everything, Claude-Code-style. `check_command`/`stop_command` need no approval (observation + cleanup of the agent's own background processes).
  - **Blast-radius reducers that restrict nothing**: cwd resolves dynamically to the configured project dir per spawn (same providers as `fs_tools`; unset root gates with the same ModelRetry), env scrubbing DERIVED from the provider catalog (`PROVIDER_API_KEY` values + `OLLAMA_CLOUD_API_KEY` + harness `LLM_API_KEY_ENV_PATTERNS` — the harness list alone misses both Ollama keys and groq/mistral/cohere/xai), `allow_interactive=False` (sudo/ssh/vi are non-TTY-broken anyway), `default_timeout=600`, `persist_cwd=False`.
  - **Flowgraphs stay on `change_graph`**: the structured tool is strictly better than `sed` on XML (transactional, rollback, relayout, structured approval diff); shell-side `.grc` edits are already handled as external-editor edits by the sync machinery (detected, not clobbered). Steered by prompt, never by regex-over-command gating (trivially bypassable, heuristic-pattern-forbidden).
  - **Known upstream limitations (documented in known-issues.md)**: no_gui graphs may run in an external terminal on GNOME (empty console log; `>>> Done (0)` while the graph still runs); ExecFlowGraphThread spawn failures are success-shaped; the first-token denylist is accident-grade, not a security boundary — the human reading the literal command is.
* **Hardware & User Permission Investigation** (original item, now agent-assisted): with shell access the agent can itself run `uhd_find_devices`/`SoapySDRUtil --probe`/`lsusb` to distinguish "no device" from "permission denied" before advising the documented no-sudo `usermod -aG plugdev,dialout,usrp` + `udevadm` fix — instead of guessing from a run log. Host device permissions are NOT sandboxed away: commands run as the user, so SDR USB access works exactly as it does for GRC itself.

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
