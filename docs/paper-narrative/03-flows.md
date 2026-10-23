# 03 — Flows
Science-agent input. No refs or citations here by design.

## Flow 1 — Inspect → propose → approve → edit
1. User asks in the sidebar (e.g. "add a low-pass after the source").
2. Executor calls `inspect_graph` (and `query_knowledge` for block schema, `generate_python` if a preview helps).
3. Executor proposes one `change_graph` with a one-line reason; `ApprovalCard` shows the structured diff.
4. On approve: 7 phases run (remove_connections → remove_blocks → add_blocks → update_params → auto_resolve_types → update_states → add_connections); any failure rolls back via `export_data()`.
5. Sugiyama relayout (`src/grc_agent/adapter/layout.py`), canvas `update()` + redraw, native `validate()` gate. Deny returns to chat with no mutation.

## Flow 2 — Run → observe → measure
1. Prerequisite: native runner has published the child process to `page.process` (some distro GRC 3.10 builds omit it — packaging fix, not runner logic).
2. `grc_interaction(start)` with a fresh File Sink path (append off), optional `stop_after_seconds` bound; approval required.
3. GRC `Executor` spawns the generated flowgraph as a child; `exec_monitor` binds `run_epoch` → `run_id`, buffers ≤512KB, watches `>>>Done(return code N)`.
4. `stop` sends `FLOW_GRAPH_KILL` (SIGTERM → grace → SIGKILL); ungated.
5. `log` reads the retained dict (`run_id`, `return_code`, `process_exited`, truncation flags).
6. `measure` / `plot`: checks graph/run/file identity, alignment, TOCTOU, nonfinite guards; rectangular-FFT bin estimate (DC included, not SNR); `measure` returns numbers + user-visible plot, `plot` additionally sends the image to a vision-capable model. Stopped runs are explicitly partial. Canvas captures are topology-only.
7. Mismatch (wrong amplitude, peak in wrong bin) loops back to Flow 1 with the numbers attached.

## Flow 3 — Planner → executor handoff
1. Planner (read-only tool subset + `write_plan`) produces a durable plan; stored via `SqlitePlanStore`.
2. Executor receives the saved plan through a system reminder; it does not run an automatic planning stage.
3. User-visible "Implement the Plan" step applies the plan through Flows 1–2 with the same approvals.

## Approval and safety summary
| Action | Gate |
|---|---|
| `change_graph` | Always approval-gated |
| `grc_interaction(start)` | Approval-gated |
| `run_command` / `start_command` | Approval-gated |
| `stop`, `log`, `measure`, `plot`, `canvas`, `save_graph` | Ungated |
| Filesystem/shell outside project dir, `.env`/`.git`/`.grc_agent`, raw `.grc` writes | Denied |
