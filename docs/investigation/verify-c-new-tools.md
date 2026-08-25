# Verification Round V-C — audit-c-new-tools.md

Scope: independent re-verification of the five headline claims in
`docs/investigation/audit-c-new-tools.md` against the repo at **commit 85f938d**
(`git rev-parse HEAD` = `85f938daf204809ba6edde0ae7a69ba1b8c3d`; `git show --stat`
confirms 85f938d is docs-only — the audit reports themselves — so the code
audit-c examined at f928197 is byte-identical to what was verified here).
Versions: pydantic-ai **2.31.0**, pydantic-ai-harness **0.23.0** (installed
sources at `.venv/lib/python3.12/site-packages/`, abbreviated `site/…` below).

**Verification environment** (all read-only; no files modified; live-LLM suites
not run):
```
$ git rev-parse HEAD                      → 85f938daf204809ba6edde0ae7a69ba1b8c3d
$ timeout 300 uv run pytest tests/test_run_stop_tools.py tests/test_shell_toolset.py tests/test_exec_monitor.py -q
                                          → 69 passed in 0.89s        (audit baseline: 69 passed)
$ timeout 600 xvfb-run -a uv run pytest tests/test_chat_sidebar.py -k 'approval or always or prefix' -v
                                          → 4 passed, 82 deselected in 0.78s
$ .venv/bin/python /tmp/dump_schema.py    → live schema dump (C1), quoted below
```

## 1. Executive summary

| # | Claim | Verdict |
|---|-------|---------|
| C1 | `run_command` model-visible timeout default says 30s; app default is 600s | **CONFIRMED** (live schema dump). Fix is safe: `tool.description` override does **not** clobber parameter descriptions in pydantic-ai 2.31 (verified live + source), and the schema dict is a stable plain dict across reads. |
| C2 | `_always_approve_all` (persisted gate) auto-approves shell commands; AGENTS.md "only way to approve everything" is false | **CONFIRMED (behavior)**; **AMENDED (intent framing)** — backlog item 6 explicitly documents "Auto mode … approves everything", so the semantics are the *documented design*; what drifted is AGENTS.md's "only way" phrasing and the tooltips. Recommendation: option (b) — fix docs/UI text, keep global semantics. |
| C3 | exec_monitor: `not_started` unreachable after run 1; stale-`_agent_initiated` suppression hazard | **CONFIRMED** (both edges; source-traced + call-site traced). Fixes validated for feasibility (epoch counter + flag cancellation). |
| C4 | ApprovalCard 'Always allow \<tok\>' label change broke nothing | **CONFIRMED** — the 4 always/approval/prefix tests pass under xvfb; no test asserts the always-button tooltip text anywhere. |
| C5 | Upstream shell semantics: background cleanup, timeout, denylist, env scrubbing | **CONFIRMED** (context7 `/pydantic/pydantic-ai-harness` + `https://pydantic.dev/docs/ai/harness/shell/` + installed source). `GRC_SHELL_*` knobs have **no upstream conflict** — the harness has no env-var interface for these knobs. |

Net: audit-c is accurate on every headline. The two real fixes remain (C1
schema text; C2 doc drift), plus two monitor state-machine fixes (C3) whose
triggers are low-probability but whose failure mode (a user-initiated failure
notification suppressed, or a stale return code reported as fresh) is
user-visible.

## 2. C1 — run_command timeout-schema drift: CONFIRMED

### 2.1 Live evidence (fresh dump, not from the audit)

Script built `GrcShellToolset(cwd=Path("/tmp"))` and dumped every tool's
`tool_def` (the model-visible `ToolDefinition`):

```
== default_timeout() == 600.0
== run_command: kind=unapproved
   description: Execute a shell command in the project directory (e.g. build toolchains, ...
   params: ['command', 'timeout_seconds']
   timeout_seconds desc: Maximum seconds to wait (default: 30).
   timeout_seconds default: None
   schema dict identity stable across reads: True
== start_command: kind=unapproved
   params: ['command']                       # <- no timeout param at all
== check_command: kind=function
== stop_command: kind=function
```

- The model-visible `timeout_seconds` description is **"Maximum seconds to wait
  (default: 30)."** while `default_timeout()` returns **600.0**.
- The app default comes from `default_timeout()` (`src/grc_agent/shell_tools.py:107-117`,
  reads `GRC_SHELL_TIMEOUT`, else 600.0) via the dataclass field
  `GrcShell.default_timeout: float = field(default_factory=default_timeout)`
  (`shell_tools.py:239-241`) and the constructor default
  `GrcShellToolset(..., default_timeout: float = 600.0, ...)` (`shell_tools.py:163`).
- The "default: 30" text is the **harness docstring** at
  `site-packages/pydantic_ai_harness/shell/_toolset.py:332`
  (`timeout_seconds: Maximum seconds to wait (default: 30).`), copied into the
  JSON schema at Tool construction by the docstring parser
  (`site-packages/pydantic_ai/_function_schema.py:153-204`, field descriptions
  applied at `:203-204`).
- Note the drift is **per `run_command` only**: `start_command` takes no
  `timeout_seconds` at all (`_toolset.py:403`; live schema `params: ['command']`),
  so `GRC_SHELL_TIMEOUT` cannot be "wrong" there — the model simply has no
  timeout knob for background commands (upstream design: background processes are
  bounded by run-end cleanup, not per-call timeouts).

### 2.2 Fix feasibility — overriding `tool.description` does NOT clobber parameter descriptions

Verified live and in source on pydantic-ai 2.31.0:

```
== after tool.description override alone ==
   param desc still: Maximum seconds to wait (default: 30).
   tool desc now: Brand new tool description
```

Mechanics: `Tool` is a dataclass holding `description` (`site-packages/pydantic_ai/tools.py:299`)
and `function_schema` (`:311`) as **independent fields**; `Tool.__init__` sets
`self.description = description or self.function_schema.description` (`:424`);
`tool_def` assembles `ToolDefinition(description=self.description, parameters_json_schema=self.function_schema.json_schema, ...)` (`:499-507`).
Parameter descriptions live in `function_schema.json_schema['properties'][…]`,
generated once at construction and never re-derived: `prepare_tool_def` returns
`self.tool_def` unchanged when no `prepare` hook is set (`tools.py:513-525`), and
`FunctionToolset.get_tools` calls `prepare_tool_def` per run without re-wrapping
the schema (`site-packages/pydantic_ai/toolsets/function.py:608-624`).

The audit's proposed in-place schema mutation is also sound: `json_schema` is a
plain `dict` stored on `FunctionSchema` (`_function_schema.py:285-288`), and the
live check `schema dict identity stable across reads: True` confirms the same
object is re-read on every `tool_def` access — a mutation persists. Also
confirmed live: the audit's exact fix text works:

```python
schema = t.function_schema.json_schema
schema["properties"]["timeout_seconds"]["description"] = "Maximum seconds to wait (default: GRC_SHELL_TIMEOUT, 600s)."
# re-read → new desc, tool description untouched
```

### 2.3 Upstream grounding

- Installed harness README: `.venv/.../pydantic_ai_harness/shell/README.md`
  documents `default_timeout=30.0  # seconds, per run_command`.
- context7 `/pydantic/pydantic-ai-harness` (shell docs, fetched 2026-08-25):
  same `default_timeout=30.0`; `run_command` "Honors a per-call or default timeout";
  `denied_commands` "Defaults to blocking destructive commands … pass an empty list
  to disable".
- `https://pydantic.dev/docs/ai/harness/shell/` (fetched): API reference
  `default_timeout — Default: 30.0`; "`run_command` accepts an optional
  `timeout_seconds` argument that overrides `default_timeout` for a single call."
- Conclusion: 30s is the **upstream** default; 600s is the app's deliberate
  reconfiguration. The stale "(default: 30)" in the model-visible schema is an
  app-side lie (the model budgets 30s for a command that will actually run up to
  600s), and the minimal fix is app-side schema text — exactly as audit-c
  recommends.

## 3. C2 — `_always_approve_all` vs session prefix-allow: CONFIRMED behavior, AMENDED intent

### 3.1 Behavior confirmed (all re-traced)

1. Auto-approve branch: `_request_approvals` returns
   `DeferredToolResults(approvals={c.tool_call_id: ToolApproved() for c in approvals})`
   for **all** pending approvals — including `run_command`/`start_command` —
   whenever `get_approval_mode() != "ask"` (`src/grc_agent/chat_sidebar.py:3123-3125`).
   No per-tool_name filtering exists.
2. `_always_approve_all` (non-shell card "Always accept", wired at
   `chat_sidebar.py:3146`) calls `set_approval_mode("always")` → persists
   `GRC_AGENT_APPROVE_CHANGES=always` in `.env` (`settings.py:306-310`), approves
   all pending, destroys cards (`chat_sidebar.py:3238-3250`).
3. Shell cards instead route to `_always_allow_command` (`chat_sidebar.py:3141-3148`),
   which grants a **session-scoped prefix-allow**: `_shell_allowed_prefixes` +
   `_shell_allowed_session`, checked on every consult (`:3186-3190`), the persisted
   gate deliberately untouched — a test asserts `get_approval_mode()` is unchanged
   by the shell click (`tests/test_chat_sidebar.py:3275-3277`).
4. Therefore clicking **"Always accept"** on a change_graph/run_flowgraph card
   (tooltip: "Apply this change and stop asking for approval — re-enable Manual
   mode with the 'Mode' toggle", `ui/approval_card.py:231-233`) flips the persisted
   gate, and **every later turn's shell commands auto-approve with no card and no
   prefix-allow**. The audit's finding is exactly right.

### 3.2 Design intent — the audit's "design contradiction" framing is AMENDED

- AGENTS.md:174 (the claim audit-c refutes): "Shell cards get a session-scoped
  'Always allow `<first-token>`' (prefix-allow …) **instead of the persisted
  global gate-off**; flipping the composer Mode toggle to Auto **remains the only
  way to approve everything**."
- `docs/backlog.md` item 6, line 78 (the design record): "Shell cards get a
  session-scoped 'Always allow `<first-token>`' (prefix-allow) instead of the
  persisted global gate-off — approve `cmake` once, the rest of the build flows.
  **Auto mode (explicit user choice) approves everything, Claude-Code-style.**"
- So the *semantics* — the persisted gate covers shell too — are the **documented
  intent** ("Auto mode approves everything"), not an accident. What is false is
  the AGENTS.md sentence "the Mode toggle remains the **only** way": the
  change_graph card's "Always accept" reaches the same persisted state, and both
  are explicit user choices. The drift is documentation + UI text, not behavior:
  (i) AGENTS.md:174's "only way" wording; (ii) the non-shell always-button tooltip
  never mentions run/command coverage; (iii) the Mode-toggle tooltip says "ask
  before the agent changes the flowgraph" (`chat_sidebar.py:1056-1061`) though the
  gate also covers runs and commands; (iv) the toggle's accessible name "Flowgraph
  change gate" (`:1032`).

### 3.3 Proposal (a) impact — traced

`get_approval_mode` call sites in the app (grep across `src/`, excluding pyc):
- `chat_sidebar.py:1049` (`_update_approval_toggle`) — display-only label sync;
- `chat_sidebar.py:3123` (`_request_approvals`) — the only **behavioral** consumer.
No hits in `ui/settings_dialog.py`, `desktop_app.py`, or `agent_factory.py`.

So proposal (a) (gate the auto-approve branch per tool_name) would not break any
other consumer mechanically. But it **contradicts the documented design**
(backlog item 6 "Auto mode approves everything"), and it converts a one-uniform-rule
gate into per-tool conditional logic (the exact hand-picked-heuristics pattern
AGENTS.md forbids), and it would make Mode=Auto show shell cards — a UX regression
vs the shipped design. Recommendation: **(b)** — keep the global semantics, fix
AGENTS.md:174 and the tooltips (exact text below in §7).

## 4. C3 — exec_monitor state machine: CONFIRMED (both edges)

### 4.1 (a) `not_started` unreachability after run 1 — CONFIRMED

`wait_for_run_end` (`src/grc_agent/exec_monitor.py:150-170`):

```python
if self._tracking:                      # :152
    await asyncio.wait_for(self._run_end.wait(), timeout) → "completed"/"still_running"
if self._last_run_log is not None:      # :165
    return "completed"                   # :169
return "not_started"                     # :170
```

- `not_started` is returned only when `_last_run_log is None` — i.e. **no run
  has ever completed** in the process lifetime. The docstring's parenthetical
  ("not_started when … the Execute action was a silent no-op", `:156-158`) is
  only half true: a no-op after the first completed run falls into the
  `_last_run_log is not None` branch and returns `"completed"`.
- Caller consequence: `run_flowgraph`'s `"completed"` branch reads
  `monitor.last_run_code` and reports `ran_successfully: code == 0`
  (`src/grc_agent/native_canvas.py:173-185`) — a silent no-op after run 1 returns
  **the previous run's return code as if fresh**. The `"not_started"` result note
  (`native_canvas.py:196-199`) is unreachable in steady state. The `wait=False`
  branch is blind in the same way: it returns `"started"` unconditionally
  (`:166-171`).
- Mitigation in place: the proxy's pre-gates mirror GRC's EXEC enabled-condition
  (`native_canvas.py:122-153`) so a no-op is near-impossible — but the state
  machine has no way to *tell* "just ended" from "never started", exactly as audit
  found.

### 4.2 (b) stale `_agent_initiated` — CONFIRMED, traced end to end

- Set: `mark_run_agent_initiated()` (`exec_monitor.py:138-148`) called at
  `native_canvas.py:159`, **immediately before** `actions.FLOW_GRAPH_EXEC()`
  (`:160`). No try/except wraps the action.
- Consumed only at the **two terminal markers**: the Done branch
  (`exec_monitor.py:216`) and the Generate-Error branch (`:228`), both
  `self._agent_initiated = False`.
- `_reset()` (`exec_monitor.py:232-238`) clears chunks/bytes/evicted/runtime-error
  only — **not** `_agent_initiated`; the start marker's `_reset()` (`:183`)
  therefore cannot clear it either.
- Suppression site: `_fail` (`exec_monitor.py:246-261`) skips the
  `notify_run_failure` callback while `_agent_initiated` is True.
- Stale path: if `FLOW_GRAPH_EXEC()` raises (exception propagates into
  `run_flowgraph_func`'s wrapper → `ModelRetry`, `agent.py:806-824`, but nothing
  clears the flag) or silently no-ops (no marker, no flag consumption), the flag
  stays True until the **next terminal marker of any run** — a later user-initiated
  failed run then has its failure notification wrongly suppressed
  (`_fail` returns early, `exec_monitor.py:248-255`).
- Also confirmed: `wait=False` returns before any marker with the flag still set
  (correct in the normal case — the in-flight run's marker will consume it), and
  no existing test covers the mark→no-marker→user-failure interleave.

### 4.3 (c) Minimal fixes — feasibility validated, diffs in §7.3

1. Fix the misleading docstrings/notes even if the epoch work is deferred
   (`exec_monitor.py:155-158`, `native_canvas.py:196-199`).
2. Run-epoch counter so `wait_for_run_end` can distinguish "this call's run never
   started" from "a run completed synchronously":
   - `self._run_epoch = 0` in `__init__`; `self._run_epoch += 1` at the start
     marker (`exec_monitor.py:181-187`);
   - `wait_for_run_end(timeout, *, epoch=None)`: `if epoch is not None and epoch != self._run_epoch: return "not_started"` before the tracking checks;
   - the proxy captures `epoch = monitor.run_epoch` **before** the action and
     passes it (`native_canvas.py:158-173`).
   This preserves the synchronous-done path (start marker fired inside the action
   → epoch incremented → "completed" with the current run's code) and makes
   `not_started` truthful again. Single-threaded loop means no read/write race.
3. Consume `_agent_initiated` when no start was observed: after the action
   returns, `if not monitor.is_tracking(): monitor.mark_run_agent_initiated_cancelled()`.
   Safe because the start marker fires synchronously inside the action (per
   `native_canvas.py:157-159` comment + `exec_monitor.py:66-74` docstring), so a
   post-action check cannot race the normal path; the synchronous-done case has
   already consumed the flag at its marker (clearing again is idempotent).
   Wrap the action in try/except clearing the flag on raise.

## 5. C4 — ApprovalCard shell-label change: no test breakage (CONFIRMED)

- Ran `timeout 600 xvfb-run -a uv run pytest tests/test_chat_sidebar.py -k 'approval or always or prefix' -v` → **4 passed, 82 deselected in 0.78s**:
  - `test_approval_mode_settings_helpers` (gate persistence)
  - `test_approval_card_titles_and_summary_per_tool` — constructs a `run_command`
    ApprovalCard, asserts "Proposed command" title + literal command rendered,
    clicks **all three** buttons including the always button (label
    `Always allow \`cmake\``, `ui/approval_card.py:224`)
  - `test_shell_prefix_allow_is_session_scoped`
  - `test_always_allow_command_resolves_matching_pending_futures`
- Grep of `tests/` shows **no test asserts the always-button tooltip or label
  text** — only copy-button/provider/context-label tooltips are asserted
  (`test_chat_sidebar.py:72,75,236-239,1862,2245,2254`). The label change is
  therefore unobservable by the existing suite except through the click-wiring
  tests above, all green.
- The always-button text/tooltip logic itself (`approval_card.py:220-237`): shell
  cards → `Always allow \`<token>\`` + "The global Manual/Auto gate is untouched";
  non-shell → `Always accept` + "stop asking for approval" tooltip. The tooltip
  wording discrepancy from §3.2 stands (no test covers it).

## 6. C5 — Upstream shell semantics: CONFIRMED, no knob conflicts

Grounding (all fetched 2026-08-25):

| Claim | Local source | Upstream |
|---|---|---|
| `default_timeout=30.0` upstream, per `run_command` only | `_toolset.py:102,117,338` (`timeout = timeout_seconds if … else self._default_timeout`) | context7 `/pydantic/pydantic-ai-harness` (README + `docs/shell.md`): "`default_timeout=30.0, # seconds, per run_command`"; pydantic.dev/docs/ai/harness/shell/: API reference `default_timeout Default: 30.0` |
| Timeout enforcement: `anyio.fail_after` + process-group kill (SIGTERM→SIGKILL after grace) | `_toolset.py:366-377`, `:277-293` | pydantic.dev harness shell page: "SIGTERM, escalating to SIGKILL after a grace period" |
| Background cleanup at run end via `AsyncExitStack` | `_toolset.py:206-213` (`__aexit__` kills + deletes temp files) | pydantic.dev page: "the agent runtime enters toolsets via an `AsyncExitStack` … runs whether the run succeeds or raises"; pydantic-ai source `site/pydantic_ai/agent/__init__.py:1791` `async with AsyncExitStack() as stack` |
| Denylist defaults `rm rmdir mkfs dd format shutdown reboot halt poweroff init`; empty list disables | `_capability.py:11-26`; `_toolset.py:235-240` | README + docs: same tuple; "pass an empty list to disable" — matches `GRC_SHELL_DENIED_COMMANDS=""` (empty disables) exactly |
| `_check_command` is first-token/shlex, best-effort, not a security boundary | `_toolset.py:232-241` | docs: "Best-effort, not a security boundary" |
| `LLM_API_KEY_ENV_PATTERNS` = `ANTHROPIC_* GATEWAY_* GEMINI_* GOOGLE_* OPENAI_* OPENROUTER_* PYDANTIC_AI_GATEWAY_API_KEY` — missing OLLAMA/GROQ/MISTRAL/COHERE/XAI | `_capability.py:28-34` | pydantic.dev page lists identical prefixes; "It is not the default… opt-in" — the app's derived patterns (`shell_tools.py:78-93`) close the gap |
| `for_run` returns a plain `ShellToolset` (would drop subclass customization) | `_toolset.py:143-163` | README: "Each run gets a fresh toolset instance" — the app's `for_run` override (`shell_tools.py:211-221`) is required, confirmed by `test_shell_toolset.py:96-108` (`isinstance(fr, GrcShellToolset)`) |

**No upstream conflicts for `GRC_SHELL_TIMEOUT` / `GRC_SHELL_DENIED_COMMANDS`:**
the harness exposes these knobs only as constructor arguments (`Shell(...)` /
`ShellToolset(...)`); there is no harness-side environment-variable interface, so
the app's `.env` names cannot collide with anything upstream. The only
cross-layer artifact is the one C1 documents: the harness docstring's hardcoded
"(default: 30)" becomes the model-visible text even when the app passes 600.

## 7. VERIFIED FACTS (numbered, fresh evidence)

1. `run_command`'s model-visible `timeout_seconds` description is exactly
   `"Maximum seconds to wait (default: 30)."` while `default_timeout()` returns
   600.0 and `GrcShell.default_timeout` defaults through the same function
   (`shell_tools.py:107-117, 239-241`; harness docstring `_toolset.py:332`; live
   dump above). CONFIRMED.
2. `tool.description` override does not clobber parameter descriptions in
   pydantic-ai 2.31.0: `Tool.description` and `Tool.function_schema` are separate
   dataclass fields (`tools.py:299,311`), `tool_def` assembles both independently
   (`tools.py:499-507`), and parameter descriptions are baked into
   `function_schema.json_schema` once at construction (`_function_schema.py:153-204`),
   with `prepare_tool_def` returning the same object per run (`tools.py:513-525`,
   `toolsets/function.py:608-624`). Live: after setting `tool.description`, the
   param description is unchanged. CONFIRMED.
3. `function_schema.json_schema` is a plain dict with stable identity across
   `tool_def` reads — an in-place mutation persists (live check). CONFIRMED.
4. `_request_approvals` auto-approves **every** pending approval, including
   `run_command`/`start_command`, when `get_approval_mode() != "ask"`
   (`chat_sidebar.py:3123-3125`); `_always_approve_all` persists the gate via
   `set_approval_mode("always")` (`chat_sidebar.py:3238-3250`, `settings.py:306-310`);
   shell cards route to the session-scoped `_always_allow_command`
   (`chat_sidebar.py:3141-3148, 3198-3229`); the persisted gate is untouched by
   the shell path (test asserts it, `test_chat_sidebar.py:3275-3277`). CONFIRMED.
5. `get_approval_mode` has exactly two call sites in `src/`: the display-only
   toggle sync (`chat_sidebar.py:1049`) and the behavioral auto-approve branch
   (`:3123`). No consumer in `ui/settings_dialog.py`, `desktop_app.py`, or
   `agent_factory.py`. CONFIRMED.
6. `wait_for_run_end` returns `"completed"` for not-tracking + `_last_run_log is
   not None` (`exec_monitor.py:165-169`), so after the first completed run a
   silent-no-op Execute reports `"completed"` with the previous run's
   `last_run_code`; `"not_started"` is reachable only when `_last_run_log is None`
   (`:170`). The caller's `"not_started"` result (`native_canvas.py:196-199`) is
   unreachable in steady state. CONFIRMED.
7. `_agent_initiated` is set at `native_canvas.py:159`, consumed only at the two
   terminal markers (`exec_monitor.py:216, 228`), not cleared by `_reset()`
   (`:232-238`), and suppresses `_fail`'s callback while set (`:246-261`); the
   Execute action at `native_canvas.py:160` is unguarded by try/except. CONFIRMED.
8. The four `-k 'approval or always or prefix'` sidebar tests pass under xvfb
   (4 passed, 82 deselected); the shell-card label change is exercised only via
   button-click wiring, and no test asserts the always-button label/tooltip.
   CONFIRMED.
9. Upstream (context7 + pydantic.dev harness-shell page + installed README)
   documents `default_timeout=30.0`, per-run `run_command`, `AsyncExitStack`
   cleanup, empty-list-disables denylist, best-effort `_check_command`,
   `LLM_API_KEY_ENV_PATTERNS` prefix list exactly as the app's docstrings claim.
   No upstream env-var knobs exist → no `GRC_SHELL_*` conflicts. CONFIRMED.
10. Audit's other evidence spot-checks re-verified: run/stop tools registered at
    `agent.py:864-877` (`requires_approval=True` only on `run_flowgraph`);
    proxy pre-gates `native_canvas.py:114-160`; `set_enabled(True)` at `:154, 222`;
    `get_tool_call_id` (`ui/approval_card.py:243-244`) has zero callers outside
    its own file (`rg` across `src/` + `tests/`); baseline suites re-run green
    (69 passed). CONFIRMED.

## 8. REFUTED / DRIFTED CLAIMS

1. **Audit §3.2 "the general cards quietly achieve [the gate-off]" framing** —
   the *behavior* is confirmed, but calling the persisted-gate coverage of shell
   a "design contradiction" overstates it: `docs/backlog.md:78` documents "Auto
   mode (explicit user choice) approves everything, Claude-Code-style", so the
   persisted gate covering shell is the design intent. The actual drift is
   **AGENTS.md:174's "only way" wording** and the tooltips/accessible-name
   (`chat_sidebar.py:1032, 1056-1061`; `approval_card.py:231-233`). AMENDED.
2. **Audit §3.1** — "approval is a registration-time resolution in pydantic-ai"
   (the app comment) is refuted by `tools.py:506` (`kind='unapproved' if
   self.requires_approval else 'function'` read live per `tool_def` access) and
   `toolsets/function.py:608-624` (per-run `get_tools`). Re-confirmed; also note
   the live dump's `kind` values (run/start = `unapproved`, check/stop =
   `function`) prove the post-registration mutation is effective.
3. Audit's §3.4-3.5 ("no drift" for `for_run` subclass preservation and
   `run_in_progress` always present) — re-verified true (`_toolset.py:143-163` vs
   `shell_tools.py:211-221`; `exec_monitor.py:116`).
4. No other audit claim was refuted: all §2 facts spot-checked above held.

## 9. REDUNDANCY & LEAN AUDIT (re-check)

- `get_tool_call_id` (`ui/approval_card.py:243-244`): zero callers, safe to
  delete — re-confirmed with `rg "get_tool_call_id" src tests` (only the
  definition and pyc byte-matches). Still true at 85f938d.
- Keep run/stop as two tools: per-tool approval is per-`ToolDefinition.kind`
  (`tools.py:506`) and a merged tool would need conditional-approval machinery
  (wrapper or `raise ApprovalRequired`) — the audit's evidence holds and the
  argument is sound (one-line `requires_approval=True` today vs wrapper machinery).
- `_apply_exec_approval`'s mutation is the least-churn sanctioned-compatible
  route; the harness exposes `FunctionToolset(requires_approval=...)`
  (`toolsets/function.py:125-128`) but `ShellToolset` doesn't use it — noting for
  awareness only, as the audit says.
- persist_cwd footgun re-confirmed in source: `_build_cwd_capture` wraps the
  command when `persist_cwd=True` (`_toolset.py:248-263`), `_apply_captured_cwd`
  writes `self._cwd` (`:265-275`) — which the subclass property setter swallows
  (`shell_tools.py:204-209`). Latent only (the app never sets `persist_cwd=True`).
- Run-note duplication in `native_canvas.py:176-185` (two "Read the full console
  output…" branches) — trivial, not worth churn; re-confirmed present.

## 10. SMALL LOST DETAILS (re-checked against audit)

1. C1 schema lie: **confirmed live** — the only genuine model-facing bug.
2. `_always_approve_all` global reach: **confirmed**; recommend docs+tooltip fix
   (§7.2), not a code-behavior change.
3. `not_started`/`completed` ambiguity + stale `return_code`: **confirmed**.
4. Stale `_agent_initiated`: **confirmed**; no test covers it.
5. `graph_modified` note is change_graph-only (`exec_monitor.py:88-90` called
   only from `native_canvas.py:249-250`): **confirmed** — manual canvas edits
   (the safety-net poll case) never set it.
6. Mode-toggle chrome says "flowgraph" only (accessible name + tooltips): 
   **confirmed**.
7. `GRC_SHELL_TIMEOUT` read once at agent-build time (dataclass `default_factory`),
   mid-session edits need Settings Save: **confirmed** (`shell_tools.py:239-241`).

## 5. UNVERIFIED

- `test_button_integration.py::test_agent_modifies_graph_via_chat` — live-LLM
  suite (key-gated), not runnable. Static analysis stands (test builds
  `grc_tools()` with `output_type=[GrcAgentResponse, str]` — no
  `DeferredToolRequests` — so a `change_graph` call would end the run deferred;
  the file's last code touch predates the approval gate). Confirm with
  `GRC_TEST_BACKEND=ollama_cloud uv run pytest tests/test_button_integration.py::test_agent_modifies_graph_via_chat`.
- Whether `Actions.FLOW_GRAPH_EXEC()` can raise in real GRC — not verifiable
  headless; the try/except fix (§7.3) is cheap insurance regardless.
- The stop-vs-new-run event interleave (user Execute inside `stop_flowgraph`'s
  10s window) — unchanged, still untested.

## 6. RECOMMENDATIONS (ordered by impact; text-only diffs)

### 1. Fix the `run_command` timeout-schema lie — `src/grc_agent/shell_tools.py:184-202`

```python
def _apply_exec_approval(self) -> None:
    for name in _EXEC_TOOL_NAMES:
        tool = self.tools.get(name)
        if tool is not None:
            tool.requires_approval = True
            if name == "run_command":
                tool.description = (
                    "Execute a shell command in the project directory (e.g. build toolchains, "
                    "SDR utilities, standalone scripts, data analysis). Do not use this to run the "
                    "active flowgraph — use run_flowgraph so GRC generates the latest code."
                )
                # The harness docstring hardcodes "(default: 30)"; the app's real
                # default is GRC_SHELL_TIMEOUT → 600 (shell_tools.default_timeout).
                # Parameter descriptions live in function_schema.json_schema, which
                # is the same dict on every tool_def read — safe to mutate in place.
                props = tool.function_schema.json_schema.setdefault("properties", {})
                td = props.get("timeout_seconds")
                if isinstance(td, dict) and "description" in td:
                    td["description"] = "Maximum seconds to wait (default: GRC_SHELL_TIMEOUT, 600s)."
            elif name == "start_command":
                tool.description = ( ... unchanged ... )
```
Add to `tests/test_shell_toolset.py`:
```python
def test_run_command_schema_timeout_matches_app_default():
    from pathlib import Path
    from grc_agent.shell_tools import GrcShellToolset
    ts = GrcShellToolset(cwd=Path("/tmp"))
    p = ts.tools["run_command"].tool_def.parameters_json_schema["properties"]["timeout_seconds"]
    assert "default: 30" not in p["description"]
    assert "600" in p["description"]
```

### 2. Fix AGENTS.md:174 wording + tooltips (option (b); keep global semantics)

AGENTS.md:174 — replace:
"…instead of the persisted global gate-off; flipping the composer Mode toggle to
Auto remains the only way to approve everything."
with:
"…instead of the persisted global gate-off. The persisted gate itself —
`GRC_AGENT_APPROVE_CHANGES=always`, set by the composer Mode toggle **or** by the
'Always accept' button on any non-shell card — approves every later approval
including shell commands; only the shell cards' 'Always allow <first-token>'
remains session-scoped."

Tooltips (`src/grc_agent/chat_sidebar.py:1056-1061` and accessible name at
`:1032`): mention runs + commands, e.g. "Mode: Auto — flowgraph changes, runs,
and shell commands apply without asking".

### 3. exec_monitor — epoch + flag cancellation (minimal)

`src/grc_agent/exec_monitor.py`:
```python
# in __init__:  self._run_epoch = 0

def mark_run_agent_initiated_cancelled(self) -> None:
    """No start marker will ever follow — drop the suppression flag."""
    self._agent_initiated = False

@property
def is_tracking(self) -> bool:
    return self._tracking

# start marker (handle_message, after the _tracking guard):
    self._run_epoch += 1

async def wait_for_run_end(self, timeout: float, *, epoch: int | None = None) -> str:
    """... "not_started" when `epoch` is given and no start marker was seen
    for it (the Execute was a silent no-op) ..."""
    if epoch is not None and epoch != self._run_epoch:
        return "not_started"
    if self._tracking:
        ...
```

`src/grc_agent/native_canvas.py` (run_flowgraph, around `:154-174`):
```python
        actions.FLOW_GRAPH_EXEC.set_enabled(True)
        epoch = monitor.run_epoch
        monitor.mark_run_agent_initiated()
        try:
            actions.FLOW_GRAPH_EXEC()
        except Exception:
            monitor.mark_run_agent_initiated_cancelled()
            raise
        # Start marker fires synchronously inside the action: not tracking now
        # means no run started, so no terminal marker will consume the flag.
        if not monitor.is_tracking:
            monitor.mark_run_agent_initiated_cancelled()
        ...
        outcome = await monitor.wait_for_run_end(timeout_seconds, epoch=epoch)
```
(Add `run_epoch` as a read-only property.) Update the docstrings at
`exec_monitor.py:155-158` and `native_canvas.py:196-199` even if the epoch work
is deferred — current text over-promises `not_started`.

### 4. Add the missing hermetic tests (per audit, all still missing)

- `_request_approvals`: (a) gate=always → every approval incl. shell gets
  `ToolApproved`; (b) ask + prefix-allowed shell call → auto without card;
  (c) deny resolves `ToolDenied`; (d) CancelledError destroys cards.
- exec_monitor: mark → no marker → cancel → later user failure still notifies;
  proxy-level spawn-failure shape.
- shell: assert `ts.tools["run_command"].tool_def.kind == "unapproved"` in
  `test_exec_tools_carry_requires_approval_on_init_and_for_run` (the
  model-visible contract, and the thing a harness bump could silently break).

### 5. Small

- Fix `test_button_integration.py::test_agent_modifies_graph_via_chat` for the
  approval gate (add `DeferredToolRequests` to `output_type` and resolve via
  `DeferredToolResults(approvals=...)`, mirroring the sidebar loop) — audit's
  option (a), which keeps the gate active.
- `notify_graph_modified()` from `sync_manual_edit`'s save path
  (`native_canvas.py:416-451`) so the staleness note also covers manual edits.
- Delete `get_tool_call_id` (`ui/approval_card.py:243-244`).

## 7. Method / how to re-run

```
git rev-parse HEAD                                        # 85f938d
timeout 300 uv run pytest tests/test_run_stop_tools.py tests/test_shell_toolset.py tests/test_exec_monitor.py -q
timeout 600 xvfb-run -a uv run pytest tests/test_chat_sidebar.py -k 'approval or always or prefix' -v
.venv/bin/python /tmp/dump_schema.py                     # the schema dump quoted above
```

Upstream: context7 library `/pydantic/pydantic-ai-harness`
(`docs/shell.md`, `pydantic_ai_harness/shell/README.md`, `_toolset.py` sources)
and `https://pydantic.dev/docs/ai/harness/shell/` (fetched 2026-08-25).
