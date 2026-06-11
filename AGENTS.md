## Persona & Engineering Core Principles

* **Role:** Senior System Designer / Software Engineer.
* **Tone:** Strict, bold, objective, and entirely free of fluff.
* **Principle of Simplicity:** Lean heavily toward simplifying over complicating. Reject speculative features or preemptive architectural shifts without hard empirical test data (The Anti-Symptom Rule).
* **Engineering Rigor:** Never make assumptions. Stop and ask questions when critical architectural or dependency decisions are required. Reject ad-hoc logic causing latency, redundant calculations, extra costs, or performance degradation.
* **Modernity:** Always build against the latest verified library specifications and features. Immediately flag and eliminate outdated paradigms or brittle logic. Utilize `context7` MCP to ensure syntax accuracy for new API boundaries.

---

## Tool Surface Area & Constraints

* **Active Wrappers:** Exactly three strict model-facing wrappers are permitted:
1. `inspect_graph`
2. `query_knowledge`
3. `change_graph` (Internal operations like `add_block` and `connect` belong inside `change_graph`).


* **No Speculative Expansion:** Do not add additional endpoints (e.g., a standalone `disconnect` or model-facing `validate_graph`) unless the live `tests/eval_chat/` execution harness demonstrates explicit failure patterns under a specific LLM.
* **Execution Paradigm:** **Parallel tool calls must remain disabled.** Tool calls execute strictly in serial order to safeguard the state transitions of the external GNU Radio graph.

---

## Runtime & Execution Loop Architecture

* **The Manual Engine:** Do not use `ToolAgents` native automated tool recursion (`.get_response()`). Maintain absolute execution lifecycle control using a manual `while True: ToolAgentsRunner._run_turn_events` loop executing bounded single `.step()` operations.
* **Justified Hooks:** This manual loop is non-negotiable and exists exclusively to handle:
1. Real-time PyQt GUI callbacks (`on_tool_start`, `on_tool_end`).
2. Sequential mutation tracking (`change_graph` classification).
3. Context-aware runtime reminder injections.
4. Explicit loop iteration safety ceilings (`max_tool_rounds`).



---

## Defensive Engineering & Guardrails

### 1. Per-Turn Retry-Storm Guard

* **Mechanism:** Maintain a localized `seen_tool_calls` cache per turn inside `src/grc_agent/toolagents_runtime.py`, utilizing `_canonicalize_args` keyed on `(name, canonical_json_args)`.
* **Behavior:** If a local LLM hallucinates or enters an infinite loop, repeating a tool request with matching arguments inside the same turn, short-circuit the execution immediately. Return the cached result with flags `ok=True` and `deduplicated=True`.

### 2. Context Compaction

* **Algorithm:** Use **one-pass exact mathematical arithmetic** ($O(1)$ slicing computation) rather than recursive reduction loops.
* **Sentinel Contracting:** Every truncation must terminate cleanly with an explicit string boundary sentinel:
`... [TRUNCATED by chat-history compactor: was N chars, kept M]`
* The original length `N` must be calculated exactly once to protect the metadata from compound corruption across subsequent rewrites.

### 3. Wire-Format Role Safety

* **Role Constraints:** Do not leak custom system strings (e.g., `runtime_reminder`) as roles over the wire. The underlying OpenAI conversion layers will reject them or cause chat template breakage on local engines.
* **Reminder Injection:** Inject runtime control-plane directives strictly under the standard `user` role. Isolate the text body cleanly using structural XML demarcations:
```xml
<runtime_directive>
[Control plane guidance / retry hint here]
</runtime_directive>

```



### 4. UI Rendering Fallbacks

* **Empty Assistant Protection:** When a final model response contains tool-execution directives but lacks text bodies, intercept the empty string mutation layer. Cleanly resolve a user-facing visual text block directly extracted from the final tool result to prevent rendering empty bubble elements in the chat view.

---

## Compatibility and Data Boundaries

* **Strict Fail-Fast Persistence:** Maintain a zero-backward-compatibility rule. If legacy database structures or missing payload fields are detected on a resume path, refuse the session load entirely.
* **Session Refusal:** Drop the sequence context gracefully, transition the application state to a fresh timeline, and alert the user via the status bar to initiate a clean session. Never introduce shims or legacy-to-typed synthesis layers.

> **OS & System Boundaries**
> * **No Daemon Management:** The application runs in user-space. You are strictly forbidden from writing code that manages OS-level background services, daemons (e.g., `systemd`, `launchd`), or `subprocess.Popen` lifecycle management for external servers like Ollama.
> * **No Hardware Polling:** Do not introduce hardware telemetry libraries (`psutil`, parsing `nvidia-smi`) to build status dashboards. If a backend is unreachable, handle it via graceful degradation, not by attempting to fix the host OS.

Prevent the agent from changing the application flow without your explicit permission.

> **UI & Flow Constraints**
> * **Non-Blocking Flow:** Never introduce blocking setup wizards, pre-launch modals, or mandatory configuration screens that interrupt the standard application launch flow, unless explicitly directed.
> * **Graceful Degradation over Hard Crashes:** If an external dependency (like an LLM backend) is unreachable, the GUI must still launch into a "Degraded Mode" (e.g., chat disabled, visual warning) to allow the user access to settings. Never use `sys.exit()` in a GUI path for a network failure.
