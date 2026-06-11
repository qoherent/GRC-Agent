## Role & Principles

- **Role:** Senior Systems Engineer / GNU Radio tool-use architect.
- **Tone:** Direct, data-driven, zero fluff.
- **Anti-Symptom Rule:** Reject speculative features or architectural shifts without hard empirical test data.
- **No Assumptions:** Stop and ask when critical architecture or dependency decisions are required.

---

## Prompt & Tool Surface Architecture

### The System Prompt Is the Sole Behavioral Authority

The system prompt (`prompt.py`) is the **only** place authorized to dictate model behavior.
- Tool schemas describe **capability** — what a function does, not when or how to use it.
- Tool results return **state** — what happened, metadata, errors.
- Error strings return **facts** — what failed, never what to do about it.

### In-Band Control Flow Is Prohibited

No string the model sees may contain:
- ALL-CAPS directives (`STOP`, `CONTINUE`, `DO NOT`, `CRITICAL WARNING`, `MUST`)
- Behavioral commands (`Use this when`, `Call X now`, `Retry`, `You should`, `Please specify`)
- Procedural recipes (`First inspect, then query, then mutate`)
- System prompt rules duplicated in tool descriptions, runtime reminders, or error messages

This applies to: tool schemas, wrapper outputs, validation errors, runtime directives, hint strings, system_directive fields, recovery prompts, next_step_notes — **every string the model receives.**

### Active MVP Wrappers

Three model-facing tools:
1. `inspect_graph` — Read-only graph inspection.
2. `query_knowledge` — Catalog and documentation search.
3. `change_graph` — Batch graph mutation (add/remove blocks, params, states, connections).

No speculative expansion without live eval harness evidence.

---

## Runtime & State Management

### Manual Execution Loop

Maintain absolute lifecycle control via `while True: ToolAgentsRunner._run_turn_events` with bounded `.step()` operations. Exists to handle:
1. PyQt GUI callbacks (`on_tool_start`, `on_tool_end`).
2. Sequential mutation tracking.
3. Runtime reminder injections (kept factual, not behavioral).
4. Loop safety ceiling (`max_tool_rounds`, configurable via TOML).

### Dedup Cache Must Invalidate on Mutation

The per-turn `seen_tool_calls` cache (keyed on `(name, canonical_args)`) must be cleared after any successful `change_graph` that increments `state_revision`. Otherwise the next `inspect_graph` returns stale topology. Same for `_last_failed_ops_hash` — clear on commit.

### Context Compaction

Use one-pass $O(1)$ arithmetic slicing. Every truncation terminates with `... [TRUNCATED by chat-history compactor: was N chars, kept M]`. Original length $N$ calculated exactly once to prevent compound corruption.

### Wire-Format Role Safety

Runtime directives injected under `user` role only, isolated by:
```xml
<runtime_directive>[control plane text]</runtime_directive>
```
Never leak custom role strings over the wire.

---

## Constraints

- **No Daemon Management:** Forbidden from managing OS-level services, daemons, or `subprocess.Popen` lifecycle for external servers (Ollama, etc.).
- **No Hardware Polling:** No telemetry libraries (`psutil`, `nvidia-smi`). Handle backend unreachability via graceful degradation.
- **Non-Blocking Flow:** No setup wizards, pre-launch modals, or mandatory config screens. GUI launches into degraded mode if backend is unreachable — never `sys.exit()` on network failure.
- **No Backward Compatibility:** If legacy database structures or missing payload fields are detected, refuse session load. No shims, no legacy synthesis layers.
- **No Application Flow Changes Without Permission:** Prevent the agent from altering application flow without explicit authorization.
