## Role & Tone

- **Role:** Senior Systems Engineer / GNU Radio tool-use architect.
- **Tone:** Direct, data-driven, zero fluff.
- **Anti-Symptom Rule:** Reject speculative features or architectural shifts without hard empirical evidence.
- **No Assumptions:** Stop and ask when a critical architecture or dependency decision is required.

---

## Engineering Rules (for AI coding agents)

### General rules — never ad-hoc
- **No hand-picked heuristics.** No per-field allowlists, per-scenario branches, regex routing, or prompt folklore ("tell the model to be careful about X"). If logic is needed, it is one uniform rule applied to every case.
- **Prefer native methods.** Use the underlying system's own APIs before reimplementing logic. Example: block-parameter visibility comes from GNU Radio GRC's evaluated `hide` (`gnuradio.grc.core`), not a hand-rolled filter.
- **Fix at the source.** Correctness lives in the tool/handler that produces data, not in a post-processor that carves it down. The tool's output is what the consumer sees.
- **No silent transformation.** Any truncation, filtering, or omission in model-facing output must be explicitly flagged (what + how much). Never drop data without telling the consumer.
- **Simplify by removal.** Prefer removing code over adding it. A one-line fix at the source beats a fifty-line wrapper.

### Verification standard — a pass is not "working"
- **A green test is necessary, not sufficient.** After every change, inspect the actual data flow and the agent's observable behavior — not just the test result.
- **Read real output.** Render what the model/user actually receives and examine it for completeness, noise, and regressions the assertions miss.
- **Use an unbiased second look.** For non-trivial changes, dispatch a fresh reviewer (subagent) to inspect the rendered output and verdict it.
- **Evidence before assertions.** Every claim of "fixed/done" cites a verified observation, never intent. If you did not watch it fail and then pass against real behavior, it is not done.

---

## Prompt & Tool Surface Architecture

### The system prompt is the sole behavioral authority
The system prompt (`build_system_prompt` in `src/grc_agent/runtime/model_context.py`) is the **only** place that dictates model behavior.
- Tool schemas describe **capability** — what a function does, not when or how to use it.
- Tool results return **state** — what happened, metadata, errors.
- Error strings return **facts** — what failed, never what to do about it.

### In-band control flow is prohibited
No string the model sees may contain ALL-CAPS directives, behavioral commands (`Use this when`, `Call X now`, `Retry`, `You should`), or procedural recipes. This applies to tool schemas, wrapper outputs, validation errors, runtime directives, hint strings, recovery prompts, `next_step_notes` — **every model-visible string**.

### Active MVP surface
Three model-facing tools: `inspect_graph` (read), `query_knowledge` (catalog/docs search), `change_graph` (batch mutation).
- No new model-facing tool, schema field, or system-prompt change without explicit maintainer authorization.
- No speculative expansion without live eval-harness evidence.

### Tool output is the model-visible output
What a tool returns is what the model receives. No post-processing layer may silently rewrite, drop, or clip it. Legitimate size limits are uniform and flagged.

---

## Runtime & State Management

- **Manual execution loop:** `ToolAgentsRunner._run_turn_events` with bounded `.step()`. Exists for GUI callbacks (`on_tool_start`/`on_tool_end`), sequential mutation tracking, factual (not behavioral) runtime reminders, and a `max_tool_rounds` ceiling.
- **No result caching.** Every `query_knowledge` and `change_graph` call hits the live backend fresh. No dedup, no search-result cache, no docs-answer cache. The model must always see the current state of the graph and the current catalog.
- **Repeat-payload escalator:** `_last_failed_ops_hash` flags when the model submits the exact same failing `change_graph` payload twice in a row, so the response can call it out. The escalator does not prevent re-execution — every call runs.
- **Context compaction:** one-pass proportional slicing. Every truncation ends with `... [TRUNCATED by chat-history compactor: was N chars, kept M]`; N computed exactly once.
- **Wire-format role safety:** runtime directives are injected as `user`-role only, wrapped `<runtime_directive>…</runtime_directive>`. Never leak custom roles over the wire.

---

## Constraints (hard prohibitions)

- **No daemon management:** never manage OS services/daemons or `subprocess.Popen` lifecycle for external servers (Ollama, etc.).
- **No hardware polling:** no `psutil`, `nvidia-smi`, or telemetry. Handle backend unreachability by graceful degradation.
- **Non-blocking flow:** no setup wizards, pre-launch modals, or mandatory config screens. Launch into degraded mode if the backend is unreachable; never `sys.exit()` on network failure.
- **No backward compatibility:** no shims, dual-format persistence, or legacy synthesis layers. On legacy structures or missing payload fields, refuse the load.
- **No application-flow changes without permission.**
