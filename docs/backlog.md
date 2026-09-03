# GRC-Agent Product Backlog

Active feature requests, architectural improvements, and planned capabilities. Completed milestones are documented in detail in [CHANGELOG.md](../CHANGELOG.md).

---

## 📋 Active Backlog & Proposed Capabilities

### 1. Multimodal GRC Visual Inspection & Canvas Screenshots
* **Status**: 🔄 Proposed
* **Objective**: Provide a model-facing visual tool (`screenshot_graph` / `capture_canvas`) that captures a high-resolution rendering of the live `.grc` flowgraph canvas and returns it as an image directly into the agent's multimodal context upon invocation.
* **Architecture & Mechanics**:
  - **On-Demand Visual Tool**: An explicit tool called by the agent when it requires visual, spatial, or layout verification beyond semantic JSON inspection.
  - **Offscreen Cairo Rendering**: Directly hooks GRC's native Cairo drawing engine (`gui.FlowGraph.draw(cr)` rendered onto an offscreen `cairo.ImageSurface` → PNG byte stream) or canvas drawing area capture, ensuring clean renders free from window occlusion or OS desktop artifacts.
  - **Multimodal Payload**: Attaches the captured image as `BinaryContent(data=png_bytes, media_type='image/png')` via Pydantic AI to vision-capable models (e.g. Gemini 2.5/3.7, Claude 3.5/3.7 Sonnet, GPT-4o).
  - **Vision Model Probing**: Inspects backend provider capabilities (`/api/show` for Ollama, `/v1/models` for OpenRouter/OpenAI) to detect vision support dynamically, surfacing an active Vision badge in Settings.
* **Target Use Cases**:
  - Validating spatial visual arrangement, bus wiring clarity, and custom block layout aesthetics.
  - Inspecting graphical state indicators (bypassed blocks, disabled subtrees, error highlights) directly on the graphical canvas.

---

### 2. Data-Plane Capture & Multimodal RF / File Visualization
* **Status**: 🔄 Proposed
* **Objective**: Enable the agent to directly observe, inspect, and visually diagnose live flowgraph streaming data, numerical plot telemetry, and multimodal project files without flooding its context window with raw numbers or large text dumps.
* **Supported Formats & Modality Handling**:
  1. **Binary RF Stream Data & Decimated Plot Telemetry (`.bin`, `.dat`, `.raw`, `.iq`, probe streams)**:
     - *Source*: Output files from `blocks_file_sink` or probe taps.
     - *Processing*: Sample parsing via `numpy.fromfile` (complex64, float32, int16). Never dumps raw binary samples into LLM context.
     - *Bounded Numerical Telemetry (Tier 1)*: Returns lightweight, strictly decimated statistics (peak $X/Y$ frequency, estimated SNR, RMS power, min/max/mean, DC offset, 16–32 decimated points) consuming < 100 tokens.
     - *Multimodal Visualization (Tier 2)*: Generates spectral power density (FFT), I/Q constellation diagrams, eye diagrams, or time-domain waterfall plots via matplotlib/PIL and returns `BinaryContent(data=png_bytes, media_type='image/png')` to vision LLMs when requested or when numerical stats indicate ambiguity.
  2. **Direct Image Files (`.png`, `.jpg`, `.jpeg`, `.svg`)**:
     - *Status*: ✅ Partially shipped (`Unreleased`) — `read_file` passes project-dir `png/jpeg/gif/webp` through as `BinaryContent` into multimodal context (see Shipped Milestones). Remaining: `.svg` (not in pydantic-ai's `ImageMediaType`; needs rasterization or text fallback).
     - *Handling*: Direct binary reading and multimodal passthrough for flowgraph plot captures, UI diagrams, and schematic images in the project directory.
  3. **Tabular & Register Spreadsheets (`.xlsx`, `.csv`, `.tsv`)**:
     - *Handling*: Specialized structural parser that extracts sheet metadata, column schemas, and targeted row slices, or renders summary heatmap plots rather than dumping massive raw CSV text into prompt context.
  4. **Text-Only Fallback Pipeline**:
     - Automatically falls back to high-density statistical characterization (quantiles, FFT peak tables, symbol error rate estimates) when operating with text-only LLMs.

---

### 3. Curated SigMF Test Dataset Catalog & Virtual RF Assets
* **Status**: 🔄 Proposed
* **Objective**: Provide a built-in curated catalog of standard, lightweight SigMF (Signal Metadata Format) over-the-air RF recordings (e.g. broadcast FM, ADS-B, LoRa, FSK, ISM remotes) enabling the agent to test real-world demodulator and decoder flowgraphs offline without physical SDR hardware.
* **Architecture & Mechanics**:
  - **Catalog Index**: Lightweight JSON index of curated SigMF captures with standard RF metadata (`core:sample_rate`, `core:frequency`, `core:datatype`, `core:description`, capture byte size).
  - **Metadata-Driven Auto-Configuration**: Automatically configures the flowgraph's `samp_rate` and center frequency variables to match the selected SigMF file's metadata upon insertion, preventing sample-rate mismatch bugs.
  - **On-Demand Downloader**: Downloads small, bounded capture files (e.g. 2–10 MB) into the local project directory on-demand.

---

### 4. Project Document File-RAG (Large Document Retrieval)
* **Status**: 🔄 Proposed
* **Objective**: Provide a dedicated semantic retrieval tool (`query_project_documents` / `file_rag`) enabling the agent to search through very large project assets (datasheets, hardware manuals, RF register maps, RFC specifications, IEEE standards, PDFs, and large text/code archives) without flooding or exhausting its limited context window.
* **Key Distinctions**:
  - Unlike `fs_tools.read_file` (which reads literal raw text lines and hits context limits on large files), `file_rag` chunks, embeds, and indexes project documents into a local project-level vector store.
  - The model queries technical questions (e.g. "What are the register addresses for PLL divider N on chip X?", "What is the packet header sync word format?") and receives only the top-k relevant semantic chunks.
* **Architecture & Privacy**:
  - Uses the existing local, zero-dependency embedding runtime (`EmbeddingGemma` via `llama.cpp` over UNIX socket).
  - Storage: Project-scoped SQLite vector + FTS5 database (`.grc_agent/project_docs.db`), keeping user project data 100% local.

---

### 5. Knowledge Base & Corpus Expansion
* **Status**: 🔄 Partial (Hybrid RRF and SWIG docstrings shipped; expansion and cleanup active)
* **5.1 Wiki Corpus Expansion**:
  - Expand local knowledge corpus beyond the initial 94 wiki pages to close domain gaps identified in forensic audits:
    - *QT GUI Sinks*: Detailed parameter guides, internal FFT behaviors, trigger modes, and vector lengths for `qtgui_sink_x`, `qtgui_freq_sink_x`, `qtgui_time_sink_x`, `qtgui_waterfall_sink_x`.
    - *SDR Hardware Recipes*: Hardware setup guides, antenna selections, gain staging, and buffer tuning for UHD/USRP, RTL-SDR, HackRF, bladeRF, and ADALM-PLUTO.
    - *Digital Synchronization & Modulation*: Practical recipes for Costas loops, Polyphase Clock Sync, Mueller & Müller clock recovery, symbol timing, and framing/deframing.
* **5.2 Corpus De-duplication & Hygiene**:
  - De-duplicate overlapping wiki articles (e.g. `Binary_Files_for_DSP.md` duplicates 43 of 63 long lines in `Reading_and_Writing_Binary_Files.md`).
  - Eliminate synthetic stub pages containing `Provenance:` and `Aliases:` meta-text that dilute ranking mass in lexical and vector indexes.
* **4.3 Truncation Density Monitoring**:
  - Monitor `output_truncated` flag behavior across both `catalog` and `docs` domains to evaluate result density and k-parameter tuning.

---

### 6. Platform Hardening & Upstream Fixes
* **Status**: 🔄 Active & Planned
* **5.1 ExecFlowGraphThread Early Spawn Crash Tracking**:
  - *Observation*: If spawning the run subprocess raises inside GRC (`gnuradio/grc/gui/Executor.py:44`), GRC's exception handler emits `send_end_exec()` with a default return code of 0.
  - *Planned Resolution*: Track process PID allocation directly in `exec_monitor` to distinguish immediate subprocess spawn failures from legitimate zero-exit runs.
* **5.2 Upstream Pydantic AI Harness `Shell` Stdin Hang**:
  - *Observation*: `anyio.open_process` without an explicit `stdin=` defaults to `PIPE`, leaving an unwritten, unclosed stdin pipe that causes commands reading stdin (e.g. `grep` on empty file lists) to hang until timeout (up to 120s/600s).
  - *Planned Resolution*: Submit upstream issue and pull request to `pydantic-ai-harness` proposing `stdin=subprocess.DEVNULL`.
* **5.3 Upstream StackOne Defender Regex Refinement**:
  - *Observation*: Regex `\$\([^)]+\)` escalates on benign jQuery boilerplate (`$(document)` ×2) in official GNU Radio Doxygen web pages, triggering false-positive injection withholding.
  - *Planned Resolution*: Submit upstream issue to `stackone-defender` proposing exclusions for benign JS identifiers or configurable sensitivity thresholds.
* **5.4 Catalog Distance Semantics**:
  - *Status*: ✅ Completed. The `distance` field is omitted on non-vector lexical rows (`distance is None`) rather than emitting a fabricated `0.0`, eliminating confusion with perfect vector matches.
* **5.5 ConversationSearch Snapshot Recovery for Interrupted Runs**:
  - Engage with upstream harness to allow recovery of user-interrupted tool calls (`state=interrupted`) during session history analysis.

### 7. Finish the chat-sidebar decomposition (≤1,000-line bar)
* **Status**: ✅ Completed (2026-09-03). U5 landed `chat/turn_driver.py` (TurnDriverMixin, 381 lines) and U6 landed `chat/session.py` (SessionMixin, 452) and `chat/status_view.py` (StatusContextMixin, 294) — move-only, golden byte-identical at every step, all gates green forward and `--reverse`. `chat_sidebar.py`: 1,925 → 943 lines (composition root); largest `chat/` module: stream_view at 574. Unblocked item 8.
* **Objective**: Extract the three remaining clusters from `chat_sidebar.py` (1,925 lines) so no module under `src/grc_agent/chat/` — nor the root file itself — exceeds U15's 1,000-line verification bar.
* **Mechanics** (measured line budgets from the review plan): a turn-driver mixin (~275 lines: `_run_agent_turn`, `_send_fix_when_free`, `notify_run_failure`, `_on_chat_task_done`, `_recover_history_after_failure`); a session-lifecycle mixin (~360: recent-session open/save/clear/delete and the implement-plan handoff); a status/context mixin (~270: context label, status/model-wait, indexing poll); the scroll cluster (~112) as the named buffer if the bar is still short. `chat_sidebar.py` remains the composition root (`__init__` + the `_build_*` widget-tree methods). Extractions follow the established mixin conventions and must be move-only.
* **Verification**: the committed behavioral golden (`tests/test_chat_sidebar_golden.py`) byte-identical after each extraction step; fast gate, lint, and the GTK gate green forward and `--reverse`; line counts re-measured per step.
* **Reference**: `docs/plans/2026-09-03-0829-refactor-sidebar-decomposition-review-plan.md` (units U5/U6, KTD3 line budgets); origin plan U15.

### 8. Sidebar heuristics replacement & blocking work off the loop (origin plan U16)
* **Status**: 🔄 Planned — the origin plan's next sidebar unit, deliberately fenced out of the decomposition review.
* **Mechanics**: the `search_mode` suffix decided by substring-matching nine literal spellings in the tool-label helper → read the adapter's structured field; codex provider magic strings in render/preflight branches → the provider tables; the four-interval flush throttle → one GLib frame-bounded timer; the two competing font scalers → one (startup scale vs reset mismatch included); synchronous SQLite reads and the blocking HTTP probe off the unified loop; the two never-removed `__init__` timers removed on destroy.
* **Reference**: `docs/plans/2026-09-02-0830-refactor-harness-lean-and-tool-contracts-plan.md` (U16).

### 9. Test-tree split & private-surface rewrite (origin plan U18)
* **Status**: 🔄 Proposed
* **Mechanics**: `tests/test_chat_sidebar.py` (4,600+ lines) constructs `ChatSidebar` ad hoc ~99 times and reaches ~470 private attributes; migrate onto the conftest `sidebar` fixture and the public/widget surface — the golden and the copy-coverage matrix are already first consumers of the fixture pattern. Four `# noqa: C901` complexity suppressions remain on sidebar render/turn code; refactor or justify each. Every test file under the 1,500-line cap.
* **Reference**: `docs/plans/2026-09-02-0830-refactor-harness-lean-and-tool-contracts-plan.md` (U18).

### 10. Scenario suite needs a stronger default model than the free tiers
* **Status**: 🧪 Open finding (recorded 2026-09-03)
* **Evidence**: all three free OpenRouter models tried this session fail the schema-strict `01_add_throttle` scenario — `dots-studio/dots-3-note-preview:free` double-encoded even the flattest array (`inspect_graph`'s `targets` arrived as the string `"[\"all\"]"`) and exhausted retries at the scenario's first tool call; `poolside/laguna-s-2.1:free` saturated upstream (HTTP 429); `inclusionai/ling-3.0-flash-fin:free` (the current default, matching `.env`) double-encoded the heavier nested `add_blocks` array — the only validation error in the call, repeated across all 3 retries. Minimal-probe verification shows BOTH models conform on the identical shapes in small contexts, so it is encoding reliability under the full tool surface, not missing capability (dots' ceiling is lower: it drops the trivial flat case where ling drops only the nested one). Decision (2026-09-03): the tool contract stays strict — no tolerated coercion of string-encoded arrays (user-directed).
* **Options**: default scenarios to a stronger OpenRouter model (per-run `GRC_OPENROUTER_MODEL` override until chosen), or run scenarios on `GRC_TEST_BACKEND=ollama_cloud` (the origin plan's baseline shows the selected scenarios passing there).

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
| **Hybrid RAG & Catalog Docstring Enrichment** | `0.5.0` / `Unreleased` | Reciprocal Rank Fusion (RRF $k=60$) vector + FTS5 search, embedded C++ SWIG docstrings for parameter units, silenced ingestion log noise. |
| **Prompt Streamlining & C++ Catalog Default** | `Unreleased` | ~42% prompt token reduction, C++ catalog default prioritization, NumPy/SciPy vectorization rules for EPBs, and safe udev permissions guidance. |
| **Chat Image Input (Multimodal Prompts)** | `Unreleased` | Composer attach button + drag-and-drop with thumbnail chips, pydantic-ai `Sequence[UserContent]`/`BinaryContent` user prompts into `agent.iter()`, `read_file` image passthrough from the project dir into multimodal context, base64 session round-trip via the existing TypeAdapter store, and bubble/history thumbnails decoded at target scale. |

---

## 🛠️ Contributing to the Backlog
When recording new requests or design decisions:
1. Document the requirement, user context, and target scope.
2. Outline specific architecture constraints, safety boundaries, and affected modules.
3. Keep entries concise, actionable, and grounded in the current codebase state.

