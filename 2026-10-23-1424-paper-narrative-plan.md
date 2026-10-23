# Paper Narrative Build Plan
Created: 2026-10-23

## Goal
Build the non-scientific GRC-Agent narrative for the paper: idea, features, system design with mermaid diagrams and flows, plus a concise pivot history. Saved under `docs/paper-narrative/` as split sources plus one assembled file for the science agent. No refs/citations here — the science agent owns those.

## Decisions (settled in Q&A)
- Output shape: folder of sections + one assembled comprehensive file.
- Location: `docs/paper-narrative/` (new folder; does not exist yet).
- History depth: concise pivots grounded in full 593-commit analysis (mix of concise + detailed).

## Research completed (evidence, not memory)
- Docs sweep: `README.md`, `AGENTS.md`, `CHANGELOG.md`, `docs/backlog.md`, `docs/blog.md`, `docs/technical_overview.md`, `docs/grc-interaction.md`, `handoff.md`. Key invariants: one GTK3 process / one event loop, agent + canvas share same live `FlowGraph`, no file round-trip; PydanticAI owns loop/dispatch/history; GRC APIs own parsing/validation; project-sandboxed fs/shell with approvals.
- Git history: 593 commits `2026-04-07` → `2026-10-23`, 5 phases identified with representative hashes (see `04-evolution-pivots.md` spec below).
- System design mapping: `pyproject.toml` scripts → `grc_agent.desktop_app:main`; `event_loop.install()` (`gi.events` primary, `gbulb` fallback); `NativeFlowgraphProxy` → `window.current_page.flow_graph`; 7-phase `change_graph`; `grc_interaction` start/stop/log/measure/plot/canvas; native `Actions.FLOW_GRAPH_EXEC/KILL`.
- Site brief: `https://gnuradioagent.com/` captured as compact context. Stale flags: site says `gbulb` throughout (repo: `gi.events` primary) and documents `run_flowgraph`/`get_run_log` as primary tools (repo now: consolidated `grc_interaction`). Do not copy stale claims into narrative.

## Deliverables — `docs/paper-narrative/` (6 files)

### 1. `00-overview.md`
- 1-page entry for the science agent: what this folder is / isn't, ingest order, file map.
- Explicit boundary: this folder owns idea/features/design/flow/pivots; science agent owns refs, related work, evaluation framing, academic prose.
- Stale-claim warnings (event-loop naming, old tool names, provider-count, `save_block` destination).

### 2. `01-idea-features.md`
- Idea in ~3 sentences: AI agent for GNU Radio Companion built into the native desktop app; works on the live `.grc` structurally; validated edits, docs-grounded answers, self-run debugging in a chat sidebar.
- Audience: RF/SDR engineers, testbed teams, educators, offline/air-gapped shops.
- Feature groups:
  - Inspect/edit structurally; Manual/Auto/YOLO approvals with reason + structured summary; Sugiyama layered relayout; native undo preserved.
  - Native Execute/Stop with bounded `stop_after_seconds`; failure listener with return-code + full log loop.
  - `generate_python` read-only preview; `save_graph` (Ctrl+S, untitled → project dir); `save_block` → `~/.grc_gnuradio`.
  - Sandboxed fs/shell (project-rooted, denylist, key scrubbing; `.grc` only via graph tools).
  - Knowledge: lexical FTS5/BM25 default zero-download; optional local vector (EmbeddingGemma) with RRF k=60 over C++ SWIG docstrings + wiki.
  - Hot-swappable providers (Ollama/LAN/Cloud, OpenRouter, OpenAI-compatible, Anthropic, Gemini, Groq, Mistral, Cohere, xAI); session persistence; cost/context readout; injection detect-and-log.

### 3. `02-system-design.md`
- Components: entry + unified loop; native GRC shell + sidebar; shared in-memory `FlowGraph`; PydanticAI agent layer (executor + read-only planner); tool surface table; native-GRC-use rule (never regex raw `.grc` XML; C++ VOLK catalog default, `epy_block` + vectorized NumPy only for custom logic); execution→evidence chain (child process, `exec_monitor`, File Sink prepare/seal/read, `measure`/`plot`).
- Invariants: GUI-only `desktop_app:main`, no CLI; single process/thread, no `GLib.idle_add`; `ModelRetry` (retryable) vs `ToolFailed` (terminal, 3-strikes `StopGracefully`); atomic writes + `flock`.
- Diagram A (`flowchart LR`): User ↔ Chat sidebar ↔ PydanticAI agent; agent → Proxy → Shared FlowGraph (canvas draws same object); branches to RAG catalog/wiki, project fs, web; tool/web outputs → injection defense → agent; run fail → return code + log → agent.
- Diagram B (`flowchart TD`): Prompt → Executor → Inspect (`inspect_graph`/`query_knowledge`/`generate_python`) → ApprovalCard → `change_graph` P1–P7 (remove_connections → remove_blocks → add_blocks → update_params → auto_resolve_types → update_states → add_connections) → native `validate()` → Save → Start (fresh-path File Sink, no append) → Native child → Log/Seal → Measure/Plot → Answer; fail edges to Rollback → retry.

### 4. `03-flows.md`
- Flow 1 — edit loop: inspect → propose → approve → transactional change → relayout → redraw → validate.
- Flow 2 — run-measure-verify: fresh sink → `start(wait, stop_after)` → `stop` → `measure(run_id, range, rate)` → numbers + plot; identity/hash/provenance checks; stopped = partial; canvas image = topology only.
- Flow 3 — planner → executor handoff: read-only planner writes durable plan; executor receives it via system reminder, no auto-replan.
- Prerequisites: native runner must publish `page.process`; approval required for `change_graph`/`run_command`/`start_command`/`start`.

### 5. `04-evolution-pivots.md`
- 5-phase arc with date ranges + hashes:
  - P1 `2026-04-07`–`05-22` local prototype + retrieval scaffolding (`0a5f2d0`, `b5e490a`, `ed70928`, `2678469`).
  - P2 `05-24`–`07-14` prompt-folklore + out-of-process GUI detour (`03094ea`, `f3f493e`, `8ef0d04`, `6c11b69` Broadway/native embedding).
  - P3 `06-18`–`06-24` native pivot: delete `cli.py` 2400 lines (`2bb0a6c`), kill allowlists/regex (`9d6ec88`), `grc_native_adapter.py` (`e9591e7`), −4200 lines dead code (`c455acc`).
  - P4 `07-15`–`08-18` single-process convergence: native GTK3 rewrite (`5038dfc` deletes `web.py` + `canvas_app`), `event_loop.py` (`4417211`), adopt `pydantic-ai-harness` (`6c345eb`).
  - P5 `08-19`–`10-23` product hardening: sandboxed fs (`189cf1e`), shell + approvals (`a6379e2`), hybrid RRF (`e437987`), `save_graph` (`7b05389`), `ToolFailed` types (`5d564a8`), verified `grc_interaction` (`9b2ddf8`).
- Pivot table `Tried → Failed (evidence) → Replaced by → Why it won`:
  - Hand-rolled session/payload/YAML-crawl → divergence + 1000-line wrappers → native `adapter/` load/mutate/validate/identity.
  - Hint/prompt folklore (interceptors, escalators, templates) → model-specific brittleness → declarative contracts + uniform rules + native booleans + schema bounds.
  - Retrieval churn (FTS5 → pure-vector → lexical fallback → hybrid RRF) → false refusals/chrome dilution → RRF k=60 + SWIG docstrings + corpus scrub.
  - Multi-process UI (PySide6 workers, Broadway/web dashboard) → GC chasm/loop starvation/desync → single-process GTK + shared `FlowGraph` + `gi.events` loop.
  - Bespoke agent loop (`trace.py`, shims) → silent history mutation → PydanticAI + harness + `ModelRetry`/`ToolFailed`.
- Convergence statement: stable by `2026-08-18`; since then additive only.
- Verify each hash with `git show -s --format=%s <hash>` before writing.

### 6. `PAPER-NARRATIVE.md`
- Assembled single file from 00–04, deduped, comprehensive yet concise (~150–200 lines + 2 mermaids). Header states: no refs/citations; science-agent handoff.

## Build order
1. `01-idea-features.md` → `02-system-design.md` (mermaids) → `03-flows.md` → `04-evolution-pivots.md` → `00-overview.md` → assemble `PAPER-NARRATIVE.md`.
2. Per-file verify: paths exist; tool names current; `gi.events` primary; no exact provider count; `save_block` → `~/.grc_gnuradio`; mermaid fences render; history hashes resolve.
3. Keep each split file scannable (bullets/tables); prose only for rationale connecting dots.

## Resources
- Sources: repo files listed above + site brief + `git log` (no network except site already fetched).
- Out of scope: academic writing, refs/citations, related-work comparison, evaluation design — science agent's job.

## Verification checklist
- [ ] All 6 files exist under `docs/paper-narrative/`.
- [ ] `PAPER-NARRATIVE.md` reads standalone; split files have no contradictions.
- [ ] Both mermaids render (LR startup/shared-object, TD edit→run→verify).
- [ ] No stale claims (`run_flowgraph` primary, `gbulb`-only, invented provider count).
- [ ] Every pivot row cites resolvable hashes + rationale.
- [ ] Headers mark science-agent boundary (no refs expected from this folder).
