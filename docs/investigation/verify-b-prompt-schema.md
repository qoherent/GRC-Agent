# Verification Round V-B — audit-b-prompt-schema.md (commit 85f938d)

Scope: independent verification of the four headline claims of `docs/investigation/audit-b-prompt-schema.md`
(output validator false-positive, run/stop merge question, tool-schema measurements, prompt/enumeration/boundary claims),
with upstream grounding in pydantic-ai **2.31.0** (`importlib.metadata`), pydantic-ai-harness **0.23.0**, the context7
`/pydantic/pydantic-ai` docs corpus, pydantic.dev docs (via web search), and the `building-pydantic-ai-agents` skill
(`/home/mahmoud/.pi/agent/skills/building-pydantic-ai-agents/`). Working tree at commit `85f938d`; no file modified
(other than this report); no live-LLM suite run. Fast hermetic checks run: `test_prompts_do_not_enumerate_tools`
(1 passed), `test_separate_planner.py` + `test_isolation.py` (64 passed, xvfb-run).

---

## 1. Executive summary

| # | Audit claim | Verdict | Notes |
|---|-------------|---------|-------|
| 1 | `validate_flowgraph_state` fires on any `change_graph` ToolCallPart; denied/rolled-back calls trip the gate; fix = gate on `outcome == 'success'` | **CONFIRMED** (fix sound in 2.31 semantics) | Two amendments: (a) the scan is conversation-wide, not turn-wide (AGENTS.md drift), so the cross-turn false-positive survives the fix; (b) the `rollback_failed` double-fault path ("flowgraph may be left mutated") is skipped by the fix — optional hardening documented. |
| 2 | Approval is per-`ToolDefinition.kind`; a merged run/stop tool "cannot ask per-call"; merging would break the sidebar loop | **AMENDED** | The kind claim is true (`tools.py:506`); the "cannot ask per-call" premise is **refuted** in 2.31.0: `ApprovalRequiredToolset` exists and raising `ApprovalRequired` from the tool body is the documented sanctioned per-call gate. "Break the sidebar loop" is overstated (it degrades card titles / gates stop). No-merge conclusion **stands**, on lean-surface grounds. |
| 3 | 6/8 domain tools >300-char descriptions; total 3,907; `change_graph` leaks phase order; `run_flowgraph` duplicates probe paragraph | **CONFIRMED — exact numbers reproduced** | All 12 measured values match to the character. |
| 4 | Guard test passes; planner surface verified; execution boundary quadrupled | **CONFIRMED** | Guard passes (1 passed); boundary is **5 statements in 4 locations** (audit under-counted the prompt's second clause in its headline count, but stated it correctly in §4.1). |

Top new finding beyond the audit: **AGENTS.md's "that turn's message history" wording is drifted** — `RunContext.messages` is
"messages exchanged in the conversation so far" (pydantic-ai `_run_context.py:74`), and the GRC sidebar passes the full
canonical history to `agent.iter()` (chat_sidebar.py:2988–2996), so the validator scans **every** turn. A successful
`change_graph` from turn 1 still fires the gate at the end of turn 5 — blaming the agent for user GUI edits made since.
The audit's fix does not address this; see R1 amendment.

---

## 2. VERIFIED FACTS

### 2.1 Claim 1 — validator false positive (CONFIRMED)

1. **The trigger is any `change_graph` ToolCallPart.** `validate_flowgraph_state` (src/grc_agent/agent.py:900) scans `ctx.messages` and sets `has_mutated` for any `ToolCallPart` whose `tool_name == "change_graph"` — no check of whether the call executed, was denied, or rolled back (agent.py:902–909). On `has_mutated` it calls `fg.validate()` + `is_valid()` and raises `ModelRetry("The flowgraph has validation errors after mutation: ...")` (agent.py:915–928). Registered on the executor only (agent_factory.py:899).
2. **The discriminator exists.** `BaseToolReturnPart.outcome: Literal['success', 'failed', 'denied', 'interrupted'] = 'success'` (.venv/pydantic_ai/messages.py:1328). Denial normalization: `_tool_execution.build_tool_return_part` maps `ToolDenied` → `ToolReturnPart(..., outcome='denied')` (.venv/pydantic_ai/_tool_execution.py:76–86, exact line 82 `outcome='denied',`).
3. **Rolled-back calls produce no success part.** A `ModelRetry` from `change_graph_func` (agent.py:651–653) is a tool retry: per upstream retries doc, "A retried tool call has no `ToolReturnPart` — the `RetryPromptPart` takes its place" (pydantic.dev/docs/ai/core-concepts/retries/). `RetryPromptPart` has no `outcome` attribute, so the audit's `getattr(part, "outcome", None) == "success"` is safe against both the denied part and the retry part. `ToolFailed` would yield `outcome='failed'` — also not a mutation.
4. **Denied calls cannot mutate.** Approval-gated tools never execute before approval: the run ends with `DeferredToolRequests` (final_result set at pydantic_ai/_tool_execution.py:1044–1050); the tool body runs only on resume with `ToolApproved` (sidebar loop chat_sidebar.py:2979–3024, `_request_approvals` at 3123–3177).
5. **`change_graph` rollback is unconditional.** `adapter/graph.py` reverts via `_revert_flow_graph(flow_graph, initial_data)` on mutation failure, batch errors, validation failure, and gate exceptions (graph.py:1303–1309, 1319–1323, 1337–1341, 1356–1360). The `force=True` hint is gated on `error_type == "validation_failed"` (agent.py:648–652). So `outcome != 'success'` ⇔ no mutation in all ordinary paths.
6. **The one real exception is `rollback_failed`**: `_revert_flow_graph` returns `"rollback failed, flowgraph may be left mutated: {exc}"` (graph.py:862–878) and that code is appended to the errors list. In that double-fault, the graph CAN be mutated while the part is `failed`/RetryPromptPart — the audit's gate would skip validation. Rare (requires GNU Radio ≥3.10.12's own `import_data` → `validate()` to re-raise during revert), and surfaced in the retry text, but not silent; see R8 for optional hardening.
7. **"Could gating on success miss a real mutation?" — No, in this app.** The only synthetic success-part path is `_TOOL_SKIPPED_FINAL_ALREADY_PROCESSED` under `end_strategy='early'`; the executor uses the default `'graceful'` and `response_output` is only computed `if ctx.deps.end_strategy == 'early'` (pydantic_ai/_agent_graph.py:2026), so text can never preempt a co-emitted `change_graph` call here. A successful `change_graph` (outcome `success`) still triggers validation — the force=True mid-edit intent is preserved.
8. **Cross-turn scope is real.** `RunContext.messages` is documented as "Messages exchanged in the conversation so far" (pydantic_ai/_run_context.py:74), seeded from `message_history` (agent/__init__.py:1424, 3207); chat_sidebar passes the full canonical history every `iter()` (chat_sidebar.py:2790–2797). A `change_graph` from any earlier turn keeps the gate armed. AGENTS.md says "in that turn's message history" — drift confirmed (§3).
9. **A run_id-scoped fix is not viable.** `resolve_run_id`: "run_id is never inherited from message_history. Each agent run — including a deferred-tool resume — gets its own id" (pydantic_ai/_agent_graph.py:264–276). The sidebar's approval resume is a fresh `iter()` per segment, so the ToolCallPart (segment 1) and the final text output (segment 3) carry different run_ids; filtering on `ctx.run_id` would miss the approved mutation. The audit's history-wide scan is the right shape.

### 2.2 Claim 2 — approval granularity & merge question (AMENDED)

10. **Per-tool kind confirmed:** `Tool.__init__` sets `kind='unapproved' if self.requires_approval else 'function'` (pydantic_ai/tools.py:506); `ToolDefinition.kind: ToolKind = Literal['function','output','external','unapproved']` (tools.py:539); the execution pipeline routes unapproved calls to `deferred_calls['unapproved']` (pydantic_ai/_tool_execution.py:947–955). One `Tool` = one kind = all-or-nothing approval under the plain `Tool` API. GRC's wiring matches (agent.py:852–872).
11. **But per-call approval IS possible in 2.31.0, two sanctioned ways:** (a) `ApprovalRequiredToolset` — exists in the venv (pydantic_ai/toolsets/approval_required.py, exported at pydantic_ai/__init__.py:164) and in the docs ("Requiring Tool Approval with ApprovalRequiredToolset", docs/toolsets.md, context7 `/pydantic/pydantic-ai`): `toolset.approval_required(lambda ctx, tool_def, tool_args: ...)` raises `ApprovalRequired` at call time; (b) raising `ApprovalRequired` from the tool function or its `args_validator` — documented in pydantic.dev/docs/ai/tools-toolsets/deferred-tools/ ("If whether a tool function requires approval depends on the tool call arguments … you can raise the `ApprovalRequired` exception from the tool function") and in the building-pydantic-ai-agents skill (`references/TOOLS-ADVANCED.md:40`: "for conditional approval, raise `ApprovalRequired(...)` instead of marking the whole tool `requires_approval=True`"). `ApprovalRequired` is caught as control flow at `_tool_execution.py:1168` and deferred as 'unapproved'.
12. **The audit's "would break the sidebar deferred loop" is overstated.** `_request_approvals` (chat_sidebar.py:3123–3177) iterates `output.approvals` generically and keys decisions on `tool_call_id`; a merged tool would flow through it unchanged. What degrades: (a) card titles are per-tool-name (`ui/approval_card.py:137–142` `_TOOL_CARD_TITLES` keyed "run_flowgraph"/"change_graph"/"run_command"/"start_command" — a merged tool falls to the generic "Proposed action — requires approval"); (b) `stop_flowgraph` is today deliberately un-gated (agent.py:867) — merging forces stop through the gate unless the new `ApprovalRequired` conditional path is added; (c) the model surface gains an `action`-style arg + an ~1100-char description for zero token saving (791 + 328 today). So the audit's **conclusion** (current two-tool surface is the lean optimum; don't merge) is correct and remains AGENTS.md-aligned ("Simplify by removal" cuts the other way here: two small single-purpose tools beat one wide conditional tool), but its premise "cannot ask per-call" is refuted in 2.31.0. If merging were ever reconsidered, `ApprovalRequired` raised from the tool body (only for the run action) is the sanctioned per-call gate — no hand-rolled logic.

### 2.3 Claim 3 — schema measurements (CONFIRMED, reproduced exactly)

13. Re-measured by constructing `grc_tools()` (grc_agent/agent.py:814–901) and dumping `name`, `description`, `function_schema.json_schema` (`.venv/bin/python`):

| Tool | desc chars (measured) | audit value | args (required) | approval | max_retries |
|------|------:|------:|------|------|------|
| `inspect_graph` | 130 | 130 | 1 (0) | no | default |
| `query_knowledge` | 122 | 122 | 3 (2) | no | default |
| `generate_python` | 593 | 593 | 1 (0) | no | default |
| `change_graph` | 788 | 788 | 8 (1) | yes | 3 |
| `get_run_log` | 554 | 554 | 0 | no | default |
| `run_flowgraph` | 791 | 791 | 2 (0) | yes | 3 |
| `stop_flowgraph` | 328 | 328 | 0 | no | default |
| `save_block` | 601 | 601 | 5 (1) | no | 3 |

Total: **3,907 chars** (measured 3907); **6 of 8** > 300. Arg-description lengths also match: `query_knowledge.k`=270, `generate_python.k`=248, `run_flowgraph.wait`=231, `change_graph.force`=173.
14. **Phase-order leak confirmed** in the model-visible description: "Runs in a fixed phase order regardless of argument order: remove_connections, remove_blocks, add_blocks, update_params, resolve 'auto' types, update_states, add_connections" (agent.py:607–613, from the google-format docstring at 601–620). The `probe-before-run` paragraph is confirmed in `run_flowgraph`'s description ("The probe-before-run strategy applies: wire native diagnostic blocks (e.g. blocks_probe_rate -> blocks_message_debug) BEFORE running…", agent.py:767–771). The execution-boundary sentence is also in the description ("Always use this tool to execute flowgraphs — do not execute flowgraph Python scripts in the shell, which would run stale code and bypass GRC console logging.", agent.py:758–762).

### 2.4 Claim 4 — prompt guard, planner surface, quadrupled boundary (CONFIRMED)

15. `test_prompts_do_not_enumerate_tools` **passes** (ran: 1 passed, 0.70s; tests/isolation.py:825–850). The prompt names no provider-dependent capability tool; "web search" appears only as prose (prompts.py:49–50).
16. Planner surface: `_PLANNER_FUNCTION_TOOLS` (agent_factory.py:93–106) = 10 explicit names ∪ `READ_ONLY_TOOL_NAMES`; `_prepare_planner_tools` filters function tools only; native `web_search` bypasses the filter (verified live in audit; re-verified by running `test_separate_planner.py` + `test_isolation.py` → 64 passed). Harness `Planning.get_instructions` gates the granular sentence on `registered & {'read_plan', 'add_task', 'update_task_status', 'update_task_statuses'}` — confirmed at pydantic_ai_harness/planning/_capability.py:144–163; the explicit `guidance=` in agent_factory.py:863–874 is used verbatim (required).
17. **Execution-boundary count: 5 statements, 4 launch sites** (one more than the audit headline's "four places", which itself acknowledged the prompt had two):
   - prompts.py:60–64 (Execution & Diagnostics: "Never execute flowgraph Python scripts directly via shell tools (which runs stale code and bypasses the run monitor)")
   - prompts.py:80 (Environment Boundaries: "shell tools are not for executing the active flowgraph")
   - agent.py:758–762 (run_flowgraph docstring)
   - shell_tools.py:191–194 (run_command description, set in `_apply_exec_approval`)
   - shell_tools.py:196–199 (start_command description)
   The audit's line refs for the shell side (shell_tools.py:169–183) point at the constructor; the descriptions actually live at 191–199. Cosmetic drift only; the substance (quadruplication, commit f928197 lineage) is confirmed.

### 2.5 Other audit claims re-verified

19. pydantic-ai **2.31.0**, harness **0.23.0** (importlib.metadata) — matches the audit's dependencies baseline.
20. `retries={"tools":3,"output":3}` on both agents (agent_factory.py:886, 841 region — planner at 886, executor at 841); `max_retries=3` on change_graph/run_flowgraph/save_block (agent.py:852, 871, 886); `output_validator` executor-only (agent_factory.py:899). Confirmed.
21. `OllamaModel.profile['supported_native_tools']` is `frozenset()` while `OllamaModel.supported_native_tools()` returns `frozenset({WebSearchTool})` (inherited via `OllamaModel → OpenAIChatModel`; verified by instantiation with `OllamaProvider`). The audit's claim 6 (latent drift) is confirmed — and the comment at agent.py:465–468 ("Ollama has none") plus test_isolation.py:833–834 both rest on the profile lookup.
22. The three "do not retry" wiring-fault texts are present exactly as audited (agent.py:689–692 get_run_log, 782–788 run_flowgraph, 806–812 stop_flowgraph); `get_run_log`'s empty-log carve-out returns a normal result (agent.py:698–704).
23. `retries.md` upstream: output-validator `ModelRetry` consumes the output budget and is the sanctioned mechanism ("Output retries are triggered by … output functions or validators raising ModelRetry") — pydantic.dev/docs/ai/core-concepts/retries/; docs/output.md shows `@agent.output_validator` raising `ModelRetry` as the canonical pattern. The audit's R1 does not change that mechanism — it only narrows when the validator raises. The building-pydantic-ai-agents skill has no dedicated output-validator section beyond TOOLS-ADVANCED.md:94 (output validators use `ModelRetry`; `ToolFailed` is an ordinary exception there) and :46 (ModelRetry → model should try again, consumes budget) — nothing contradicts the fix.

---

## 3. REFUTED / DRIFTED CLAIMS

1. **Audit §1.2: "a merged tool cannot ask per-call (unless `ApprovalRequiredToolset` exists)"** — REFUTED in 2.31.0. `ApprovalRequiredToolset` DOES exist (pydantic_ai/toolsets/approval_required.py; `pydantic_ai/__init__.py:164`; docs/toolsets.md), and the docs additionally sanction raising `ApprovalRequired` from the tool function/args_validator (docs/deferred-tools.md; skill TOOLS-ADVANCED.md:40). The audit's own hedge was the escape hatch; the hatch is open. The conclusion (don't merge) survives on other grounds (§2.2.12).
2. **Audit §1.2 claim (implicit) that the merge "would break the sidebar deferred loop"** — OVERSTATED. `_request_approvals` is tool-name-agnostic (iterates `output.approvals`, keys on `tool_call_id`). It degrades card titles and the stop path, it does not break. Amended.
3. **AGENTS.md: "If any change_graph tool call appears in that turn's message history"** — DRIFTED: the scan is over `ctx.messages` = the whole conversation (see 2.1.8). The audit repeated AGENTS.md's framing without flagging the cross-turn behavior; the fix R1 leaves it in place.
4. **Audit shell line refs (shell_tools.py:169–183)** — drift: descriptions are at 191–199; cosmetic.
5. **AGENTS.md's guard-test history sentence** ("the guard test … hardcoded the same wrong name and off-thread via anyio.to_thread") — still describes the OLD test; the current synchronous guard (tests/test_isolation.py:825–850) is correct. Audit §3.2 confirmed, re-verified by reading the test.

Nothing else in the audit's scope was refuted: all measurements, retry texts, `force=True` gating, `keep_pairs`/`min_clear_tokens` claims, and the ApprovalCard flow descriptions matched live code.

---

## 4. REDUNDANCY & LEAN AUDIT

1. **Boundary ×5 (see 2.4.17)** — the prompt's two clauses (prompts.py:60–62, 80) plus docstring (agent.py:758–762) plus two shell descriptions (shell_tools.py:191–194, 196–199). The audit's R2 (keep one near-tool copy each + one prompt clause) remains the right shape; my count is 5 statements rather than 4 locations×1.
2. **Probe-before-run ×2** (prompts.py:71–73 vs agent.py:767–771) — confirmed; both were added for different audiences (strategy vs per-tool), but the docstring copy is 200 chars of per-request text.
3. **`wait` semantics ×2** (prompts.py:63–64 vs `run_flowgraph.wait` arg schema 231 chars, agent.py:770–776) — confirmed.
4. **Empty-log external-terminal note ×2** (prompts.py:65–67 vs the tool-result note native_canvas.py:186–192) — confirmed.
5. **`k` guidance essays** — `query_knowledge.k` 270 chars, `generate_python.k` 248 chars — confirmed, both restate the same "default 5, clamped 1–20" convention.
6. **`change_graph` phase-order enumeration** (agent.py:607–613) — confirmed model-visible; backend detail, AGENTS.md's own `change_graph` convention documents the phase order in adapter/graph.py comments, so the description is not the single source.
7. **`save_block` leaks `~/.grc_gnuradio`** — confirmed (agent.py:716–717), and the AGENTS.md section on block_library confirms the path is `Config.hier_block_lib_dir` — implementation detail.
8. Mixed shell description formats (plain text for run/start_command, harness XML `<summary>` for check/stop) — confirmed live via the audit's capture; re-verified the plain-text overrides at shell_tools.py:191–199.
9. NOT redundant: the `reason` rule in prompt (prompts.py:38–41) vs schema (agent.py:616–617) — schema says what, prompt adds the denial-recovery rule; keep both. Confirmed.

---

## 5. SMALL LOST DETAILS

1. **Cross-turn false-positive remains after the audit's R1** (see 2.1.8): a successful `change_graph` from an earlier turn still gates the current turn's output. The audit's fix narrows the trigger to executed mutations but keeps the conversation-wide window. Document the trade-off; AGENTS.md's "that turn" wording should be corrected (see R7).
2. **`rollback_failed` leaves the gate skip** under R1: the one case where `outcome != 'success'` can coincide with a mutated graph. Present in the retry text ("rollback failed, flowgraph may be left mutated"), so not silent; R8 offers a targeted check.
3. **Deferred-resume run_id isolation** — each approval segment is a fresh run (`resolve_run_id`), which makes run-scoped filtering impossible (2.1.9). Anyone tempted to "scope the validator to this turn by run_id" must not.
4. **Audit §5.2 approval-model imprecision** ("Each command requires the user's approval", prompts.py:81, vs session-scoped prefix-allow) — confirmed; chat_sidebar.py:3131–3136 (auto-approve before card creation) and 3138–3143 (session prefix-allow).
5. **Planner allowlist comment** (agent_factory.py:89–92) relies on the native-tools bypass; `"web_search"` is absent from `_PLANNER_FUNCTION_TOOLS` — harmless today (native tools never flow through `PrepareTools`), latent drift. Audit's R6 stands.
6. **`GrcAgentResponse` structured output** (agent.py:387, executor `output_type=[GrcAgentResponse, str, DeferredToolRequests]` at agent_factory.py:815) — the validator's `output: str` annotation is notional; validators run for structured outputs too (run via `run_output_with_hooks`). Not a defect; a comment could state it.

---

## 6. UNVERIFIED

1. **The end-to-end denied-call→validator interaction** — confirmed by source-reading both sides (denial normalization + validator trigger) and the deferred docs' own message-history example showing `ToolReturnPart(..., outcome='denied')`; not by an executing test against the real sidebar loop. Confirmation: a TestModel test that denies a `change_graph` while the live graph is invalid and asserts the turn completes without `ModelRetry` (the audit's §6.2, still open).
2. **Whether any real backend ever emits text+change_graph in one response under 'graceful'** — irrelevant to the gate (tools always run under graceful), so no correctness risk; not tested live.
3. **Token cost of the redundancy** — structural only (3, 907 chars of descriptions + 6,134-char prompt); marginal per-request tokens measurable only with a live model.
4. **A future pydantic-ai version resolving native-tool support via the classmethod** would flip Ollama to server-side `web_search` (unclassified by the injection defender) — not observable today (2.31.0 confirmed profile-based).

---

## 7. RECOMMENDATIONS (ordered by impact; minimal diff sketches, text only)

**R1 (audit R1, CONFIRMED + hardened). Gate the validator on executed mutations.**
File: `src/grc_agent/agent.py:902–910`.
```python
    # A change_graph call only mutates when it EXECUTED successfully: denied
    # calls (approval card) and failed/rolled-back calls never mutate the
    # graph, and validating the live graph against them blames the agent for
    # pre-existing user state. outcome is set on ToolReturnPart
    # ('success'|'failed'|'denied'|'interrupted'); RetryPromptPart has none.
    has_mutated = any(
        getattr(part, "tool_name", None) == "change_graph"
        and getattr(part, "outcome", None) == "success"
        for msg in ctx.messages
        if hasattr(msg, "parts")
        for part in msg.parts
    )
```
Soundness in 2.31.0 (verified): the only 'success'-without-execution path (early-strategy skip parts) is unreachable under the executor's default `'graceful'`; denied → `'denied'`; tool-retry → `RetryPromptPart` (no outcome); `ToolFailed` → `'failed'`. Keep the local `import ToolCallPart` only where still used (it is, at agent.py:940 in `_any_tool_called`).

**R7 (NEW, beyond audit). Correct the AGENTS.md drift and document the cross-turn window.**
File: `AGENTS.md` ("Graph-validity gate" section). Replace "in that turn's message history" with "in the conversation's message history (`ctx.messages` is the whole conversation — a successful `change_graph` from any earlier turn still arms the gate)". If per-turn semantics are desired instead, note that run_id cannot delimit a sidebar turn (every approval segment is a fresh run), so any such change needs a user-prompt-boundary heuristic — currently not worth it; documenting is.

**R8 (NEW, optional hardening). Cover the `rollback_failed` double-fault under the new gate.**
File: `src/grc_agent/agent.py:902–909` — extend the condition:
```python
    has_mutated = any(
        getattr(part, "tool_name", None) == "change_graph"
        and (
            getattr(part, "outcome", None) == "success"
            or "rollback failed, flowgraph may be left mutated" in str(getattr(part, "content", ""))
        )
        for msg in ctx.messages
        if hasattr(msg, "parts")
        for part in msg.parts
    )
```
(retry text carries the exact `_revert_flow_graph` message, graph.py:177.) Optional because the double-fault is already surfaced in the retry text; without it, a mutated graph after a failed revert would not be re-checked at turn end.

**R3–R5 (CONFIRMED) — schema compression.** Keep the audit's diffs for `change_graph` (agent.py:601–620, drop the phase enumeration into a code comment, keep auto-resolution + approval + force semantics, 788→~420), `generate_python` (agent.py:559–571, ~593→~340), the shared `_K_GUIDANCE` constant (agent.py:544–551, 575–583 → one string, saves ~250 chars/request), and the run_flowgraph probe paragraph (agent.py:767–771 → one-line pointer). All measured values re-verified; no change to their shape.

**R2 — boundary ×5→×2.** Keep the shell descriptions (shell_tools.py:191–199 — nearest to the violation) and ONE prompt clause (prompts.py:60–62), delete the duplicate prompt clause (prompts.py:80 tail: "shell tools are not for executing the active flowgraph") and either the run_flowgraph docstring paragraph or the prompt paragraph (keep exactly one full copy, agent.py:758–762 vs prompts.py:60–62).

**R6 — planner allowlist.** Add `"web_search"` to `_PLANNER_FUNCTION_TOOLS` (agent_factory.py:93–106) with the bypass comment (native tools don't flow through `PrepareTools` today; defensive). Confirmed harmless and read-only.

**R7 — save_block path.** agent.py:716–717: replace the literal `~/.grc_gnuradio` with "GNU Radio's lighter hier-block library mechanism (not an OOT module)".

**R8 — mixed description styles on check_command/stop_command.** Leave harness XML (they are harness-owned) or overwrite in the same `_apply_exec_approval` block for consistency — cosmetic.

**Not changing:** retries (`{"tools":3,"output":3}` + per-tool 3), the retry texts, the do-not-retry carve-outs, the approval loop (chat_sidebar.py:2979–3177), the two-tool run/stop surface, the executor/planner split, `Planning(guidance=...)`.

---

*Baseline: fast hermetic checks re-run — `test_prompts_do_not_enumerate_tools` 1 passed; `test_separate_planner.py` + `test_isolation.py` 64 passed (xvfb-run). No live-LLM suites run. No source file modified (report only). Upstream: context7 `/pydantic/pydantic-ai` (docs/toolsets.md, docs/deferred-tools.md, docs/output.md, docs/retries.md), pydantic.dev API pages (ToolReturnPart.outcome, ToolDefinition.kind, DeferredToolRequests), pydantic-ai 2.31.0 venv sources, building-pydantic-ai-agents skill (references/TOOLS-ADVANCED.md:40,46,94).*
