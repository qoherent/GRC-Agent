# 04 — Evolution and Pivots
Science-agent input. No refs or citations here by design. All hashes verified with `git show -s` on 2026-10-23. Total history: 593 commits, `2026-04-07` → `2026-10-23`.

## Phase arc (short mention per phase — paper-ready)
- **P1 — Local prototype + retrieval scaffolding (`2026-04-07`–`05-22`).** Initial commit `0a5f2d0`; runtime wrapper `b5e490a`; structured retrieval + provenance; catalog phase work; Release-1 `2678469` "Production-grade local DSP agent architecture". Custom `ToolAgents` runtime, YAML/grcc/dict-crawl session handling, local llama.cpp harness.
- **P2 — Prompt folklore + out-of-process GUI detour (`05-24`–`07-14`).** Runtime-around-ToolAgents `03094ea`; bypass-hint interceptor matrix `f3f493e`; PySide6 audit/hardening `8ef0d04`; Broadway iframe → native MainWindow embedding experiments `6c11b69`.
- **P3 — Native pivot (`06-18`–`06-24`).** Delete `cli.py` (~2400 lines), GUI-only `2bb0a6c`; eliminate allowlists/regex heuristics `9d6ec88`; complete native GRC adapter `e9591e7` (464 lines); consolidate to native APIs, delete ~4200 lines dead code `c455acc`; inspect 1052→195 lines, change 1277→215 lines.
- **P4 — Single-process convergence (`07-15`–`08-18`).** Native GTK3 rewrite `5038dfc` (deletes `web.py` + `canvas_app`); unified loop work `4417211`; adopt `pydantic-ai-harness`, drop hand-rolled layers `6c345eb`.
- **P5 — Product hardening (`08-19`–`10-23`).** Sandboxed fs tools `189cf1e`; shell + approval gate `a6379e2`; hybrid RRF + injection defense `e437987`; `save_graph` work `7b05389`; `ToolFailed` typed errors `5d564a8`; verified `grc_interaction` + docs consolidation `9b2ddf8`.

## What was tried, why it failed, what replaced it

| Tried (early/bad approach) | Why it failed | Replaced by (why it won) |
|---|---|---|
| Hand-rolled session/payload stack, YAML/grcc/dict-crawl graph handling | Divergence risk between shadow copies and live graph; ~1000-line wrappers; maintenance load | Native `adapter/` using GRC's own load/mutate/validate/identity; zero-divergence experiment + large line-count win (`e9591e7`, `c455acc`) |
| Prompt folklore: topology interceptors (`f3f493e`), repeat-payload escalators, usage templates, in-band control text | Model-specific brittleness; hint blindness across providers | Declarative contracts, one uniform rule applied identically (e.g. `keep_param` in `adapter/graph.py`), native booleans, actionable errors, schema bounds (`9d6ec88`) |
| Retrieval churn: FTS5 → pure-vector → lexical fallback → hybrid | False refusals, chrome dilution, time-sink misranking | Hybrid Reciprocal Rank Fusion (k=60) over FTS5 + vector with C++ SWIG docstrings + corpus scrub (`e437987`); lexical default keeps zero-download installs working |
| Multi-process UI: PySide6 workers/process manager, Broadway + web dashboard | GC/lifecycle chasm, event-loop starvation, subprocess escape, canvas/agent desync | Single-process GTK3 app + shared `FlowGraph` + `gi.events` unified loop (`5038dfc`, `4417211`); one thread, no marshaling |
| Bespoke agent loop, schema shims, hand-rolled trace | Silent history mutation, mock anti-patterns, почте debugging cost | PydanticAI + harness capabilities own loop/dispatch/history (`6c345eb`); `ModelRetry` vs `ToolFailed` + `StopGracefully` bound retries (`5d564a8`) |
| CLI surface (`cli.py`) alongside GUI | Two entry points, diagnostic flags outside the app, support burden | GUI-only `desktop_app:main`; all diagnostics in the GUI (`2bb0a6c`) |

## Convergence point
Stable by `2026-08-18`: single-process GTK (`5038dfc`) + native GRC APIs (`e9591e7`, `c455acc`) + unified loop (`4417211`) + PydanticAI harness (`6c345eb`). Since then additive only: sandboxed fs/shell, approval-gated mutations and execution, `save_graph`/`save_block`, RRF retrieval, consolidated `grc_interaction` (start/stop/log/measure/plot/canvas).

## Rationale to carry into the paper (one paragraph)
Early work optimized the wrong layer — prompt tricks and shadow graph copies — while the real failure modes were architectural: desynchronized graph copies, competing event loops, and model-specific heuristics. Each pivot deleted code and moved authority to the owner: GRC owns graph truth and execution, the GLib/asyncio bridge owns the loop, PydanticAI owns the agent loop, standard libraries own retrieval fusion. The result is smaller, harder to break, and provider-agnostic.
