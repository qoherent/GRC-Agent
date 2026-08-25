# Audit C — New Tools (run_flowgraph/stop_flowgraph/shell): Correctness, Redundancy, Optimization

Investigation brief C. Scope: `agent.py` run/stop/get_run_log tools, `native_canvas.py` proxy, `exec_monitor.py`, `shell_tools.py`, `chat_sidebar._request_approvals`/ApprovalCard, `approval_card.py`, `agent_factory.py` wiring, and their tests. Checkout `f928197`; pydantic-ai **2.31.0**, pydantic-ai-harness **0.23.0** (`uv.lock`, verified via installed sources below).

**Command evidence baseline:**
```
$ timeout 300 uv run pytest tests/test_run_stop_tools.py tests/test_shell_toolset.py tests/test_exec_monitor.py -q   → 69 passed in 0.91s
$ timeout 600 xvfb-run -a uv run pytest tests/test_chat_sidebar.py -q -k "approval or card or shell"               → 4 passed, 82 deselected
$ timeout 900 xvfb-run -a uv run pytest tests/test_chat_sidebar.py tests/test_isolation.py tests/test_desktop_app.py tests/test_native_canvas.py -q → 164 passed
$ timeout 600 uv run pytest tests/test_agent_factory.py tests/test_fs_tools.py tests/test_prompt_injection.py tests/test_tool_output_limits.py -q → 70 passed
```
No file was modified; no live-LLM suite (`test_integration.py`, `test_button_integration.py`) was run. Harness sources quoted from `.venv/lib/python3.12/site-packages/pydantic_ai*/` (paths abbreviated `site-packages/…`).

---

## 1. Executive summary

1. **The run/stop/shell/monitor machinery is real, wired, and mostly matches AGENTS.md.** Pre-gates, native Execute/Stop paths, `agent_initiated` suppression, 512 KB eviction disclosure, dynamic cwd, env scrubbing at both spawn sites, denylist policy, session-scoped prefix-allow — all verified in code and by the passing hermetic suites (evidence §2).
2. **One genuine model-facing bug: the `run_command` schema tells the model the default timeout is 30s; the app's actual default is 600s.** The harness docstring line survives into the JSON schema (verified live: `"description": "Maximum seconds to wait (default: 30)."`, `toolsets/function.py` builds the description from the docstring at `_toolset.py:332`), while `GrcShell.default_timeout = 600.0` (`shell_tools.py:107–114`). The custom description replacement in `_apply_exec_approval` (`shell_tools.py:184–202`) never touches parameter descriptions.
3. **One real AGENTS.md drift:** "flipping the composer Mode toggle to Auto remains the only way to approve everything" is false — the change_graph/run_flowgraph card's **Always accept** (`_always_approve_all`, `chat_sidebar.py:3238–3250`) persists `GRC_AGENT_APPROVE_CHANGES=always`, which auto-approves **shell commands too** (`_request_approvals`, `chat_sidebar.py:3123–3125`). The shell-card prefix-allow deliberately avoids that gate; the non-shell cards silently bypass that design decision.
4. **Two state-machine ambiguities in exec_monitor** (both low-probability, both untested): (a) `wait_for_run_end`'s `"not_started"` is returned only when *no run has ever completed in the process lifetime* — after the first run, a silent no-op Execute reports `"completed"` with the *previous* run's `last_run_code` (docstring says the opposite, `exec_monitor.py:156–170`); (b) `_agent_initiated` has no consume-on-timeout: if the Execute action raises or silently no-ops after `mark_run_agent_initiated()` (`native_canvas.py:159–160`), the flag stays set until the *next terminal marker of any run* — which can be a later user-initiated run whose failure notification gets wrongly suppressed (`exec_monitor.py:246–261`).
5. **Test-coverage gaps:** `_request_approvals` itself has **zero hermetic tests** (no auto-approve path, no `GRC_AGENT_APPROVE_CHANGES=always` + shell-card test, no cancellation path); no test for the stale-`agent_initiated` interleave; no tool-level spawn-failure (completed + non-zero code) test; no test asserts `grc_tools()`'s `run_flowgraph` carries `requires_approval=True`; the shell test asserts the `Tool` field but not the resulting `ToolDefinition.kind` (the actual mechanism, `tools.py:506`). `test_button_integration.py::test_agent_modifies_graph_via_chat` builds an agent with approval-gated `change_graph` but `output_type=[GrcAgentResponse, str]` with no deferred resolution — broken by construction since commit `38707ce` (live suite, unrun; §6).
6. **Merging run/stop into one tool is not recommended** — evidence in §3.1: pydantic-ai approval is per-`ToolDefinition.kind`, and conditional per-call approval (run asks, stop doesn't) requires the `ApprovalRequiredToolset` wrapper or a `raise ApprovalRequired` in the tool body — *more* machinery than today's one-line `requires_approval=True` on the run tool, for a saving of 1 tool slot out of ~24. Two tools keep "stop is approval-free" true by construction.

---

## 2. VERIFIED FACTS

### (1) run_flowgraph wiring — VERIFIED

- Tool registration: `run_fg_tool = Tool(run_flowgraph_func, name="run_flowgraph", requires_approval=True, docstring_format="google", require_parameter_descriptions=True)` + `run_fg_tool.max_retries = 3` (`agent.py:864–871`); `stop_fg_tool` is **not** approval-gated (`agent.py:873–877`, docstring "Stopping is the safe direction, so this needs no approval" `agent.py:797–798`).
- Proxy pre-gates in order: monitor wired? (`native_canvas.py:114–121`), page exists (`:122–126`), `page.process is None` (`:127–132`), `page.file_path` set (`:133–138`), `fg.validate()` then `is_valid()` with error list (`:139–147`) — the validate-before-is_valid convention from AGENTS.md is honored.
- `set_enabled(True)` before both actions (`native_canvas.py:154, 222`) and `mark_run_agent_initiated()` **before** `actions.FLOW_GRAPH_EXEC()` (`native_canvas.py:159–160`) — the start-marker-fires-synchronously rationale is in the comment at `native_canvas.py:155–158` and the monitor docstring (`exec_monitor.py:68–74`).
- `wait=False` returns `{"status": "started", ...}`; `wait=True` maps monitor outcomes `completed`/`still_running`/`not_started` (`native_canvas.py:166–200`). `stop_flowgraph` awaits `wait_for_run_end(10.0)` and returns `stopped` vs `stop_requested` (`native_canvas.py:224–240`). No-op `not_running` when `page.process is None` (`:214–219`).
- `run_flowgraph_func`/`stop_flowgraph_func` wrap `ValueError` → `ModelRetry` and treat a missing `deps` method as a *do-not-retry* wiring fault (`agent.py:806–824, 833–840`).
- Tests assert behavior (not just absence of error): gate tests assert `exec_action.assert_not_called()`, `validate.assert_called_once()`, `set_enabled(True)` and action firing on happy paths (`tests/test_run_stop_tools.py:84–233`). 13/13 pass.

### (2) Shell toolset correctness — VERIFIED (mostly), with one bug

- **Dynamic `_cwd`**: `GrcShellToolset._cwd` is a property over `resolve_shell_cwd()` (`shell_tools.py:204–206` → `:119–133`: project dir → active `.grc` parent → `_UNSAVED_CWD`), with the swallow-the-setter pattern (`:207–209`). The harness parent `__init__` assigns `self._cwd = cwd.resolve()` (`site-packages/pydantic_ai_harness/shell/_toolset.py:110`) — the property setter discards it, so `_initial_cwd` reads the dynamic value. Verified: `fr._cwd == other` after provider switch, and `pwd` output follows the provider change between calls (`tests/test_shell_toolset.py:96–108, 151–162`).
- **Gate covers both exec tools**: harness `run_command` calls `self._check_command(command)` (`_toolset.py:337`) **and** `start_command` calls it too (`_toolset.py:415`) — the subclass gate (`shell_tools.py:227–238`, `PermissionError(_NO_ACTIVE_GRAPH_MSG)` when `_cwd == _UNSAVED_CWD`) thus covers both. Tests assert both paths raise `ModelRetry` (`test_shell_toolset.py:109–115`). `_recoverable` converts only `PermissionError` → `ModelRetry` (`_toolset.py:33–55`).
- **Env scrubbing at spawn for BOTH run and start**: `run_command` spawns with `env=self._resolve_env()` (`_toolset.py:369`) and `start_command` likewise (`_toolset.py:423–427`). `_resolve_env` strips `denied_env_patterns` from the inherited env (`_toolset.py:185–205`). Derived patterns = provider-catalog key vars + `OLLAMA_CLOUD_API_KEY` + `LLM_API_KEY_ENV_PATTERNS` (`shell_tools.py:78–93`); harness list verified at `site:shell/_capability.py:28–34` to be `ANTHROPIC_* GATEWAY_* GEMINI_* GOOGLE_* OPENAI_* OPENROUTER_* PYDANTIC_AI_GATEWAY_API_KEY` — indeed missing OLLAMA/GROQ/MISTRAL/COHERE/XAI (the "gap" test `test_harness_llm_pattern_gap_is_documented` asserts exactly that). Live `printenv` test proves scrubbing at spawn (`test_shell_toolset.py:117–126`).
- **`for_run` subclass preservation**: the harness implementation returns a plain `ShellToolset(cwd=self._initial_cwd, ...)` (`_toolset.py:143–163`); `GrcShellToolset.for_run` rebuilds the subclass (`shell_tools.py:211–221`) — verified live by the test asserting `isinstance(fr, GrcShellToolset)` (`test_shell_toolset.py:96–108`).
- **Approval flags**: `_apply_exec_approval` mutates `tool.requires_approval = True` for `run_command`/`start_command` post-registration (`shell_tools.py:184–202`). **Mechanism verified**: `Tool.tool_def` is a live property reading `self.requires_approval` (`site:tools.py:506` `kind='unapproved' if self.requires_approval else 'function'`), and `FunctionToolset.get_tools` calls `tool.prepare_tool_def(run_context)` per run (`site:toolsets/function.py:608–624`). Live check against the built toolset: `run_command` → `kind: unapproved`, `check_command`/`stop_command` → `kind: function`. So the mutation **is** effective; the shell_tools.py docstring's "registration-time resolution" phrasing is wrong about the mechanism (§3.1).
- **Denylist defaults + knobs**: `default_denied_commands()` returns the harness `_DEFAULT_DENIED_COMMANDS` (`rm rmdir mkfs dd format shutdown reboot halt poweroff init`, `_capability.py:11–26`) unless `GRC_SHELL_DENIED_COMMANDS` is set (comma-separated; empty disables) — verified by tests (`test_shell_toolset.py:176–194`). `default_timeout()` → `GRC_SHELL_TIMEOUT` else 600.0 (`shell_tools.py:107–114`), verified by test (`:196–202`).
- **Background lifecycle**: `start_command` → `check_command`/`stop_command`; `__aexit__` kills every leaked process group and cleans temp files (`_toolset.py:206–213`); tested (`test_shell_toolset.py:133–150`).

### (3) exec_monitor state machine — VERIFIED, with 3 edges

- **agent_initiated consume-once**: set by `mark_run_agent_initiated()` (`exec_monitor.py:138–148`), consumed at **both** terminal markers (Done: `:216`; Generate Error: `:228`), suppression in `_fail` (`:246–261`). Sequential interleave is tested: agent-run failure suppressed, subsequent user-run failure notified (`test_exec_monitor.py:348–363`); success run consumes the flag (`:365–375`).
- **wait_for_run_end outcomes**: `completed` (tracking + event set, or not tracking + `_last_run_log is not None`), `still_running` (timeout), `not_started` (only when `_last_run_log is None`) (`exec_monitor.py:155–178`). The synchronous-done (spawn-failure) case is tested (`test_exec_monitor.py:394–405`) and the Generate-Error path sets the event + code 1 (`exec_monitor.py:224–232`; test `:407–415`).
- **512 KB cap**: whole-chunk eviction from the front (`exec_monitor.py:38, 231–242`), eviction disclosed as `log_truncated` + `truncation_note`, frozen at Done before `_reset` (`:112–128, 208–211`); both disclosure tests pass (`test_exec_monitor.py:262–286`).
- **`run_in_progress`** always present (`:116–123`); `in_progress_note` while tracking; test `:377–388`.
- **graph_modified note**: `notify_graph_modified()` (`:88–90`) called only from `NativeFlowgraphProxy.notify_edit` (`native_canvas.py:249–250`) — i.e. only after `change_graph`. Reset at each run start (`:183`). Tested (`test_exec_monitor.py:240–260`).

### (4) Approval/sidebar integration — VERIFIED

- `_request_approvals` (auto mode) returns `{c.tool_call_id: ToolApproved()}` for **all** pending approvals — including run_flowgraph and shell — when `get_approval_mode() != "ask"` (`chat_sidebar.py:3123–3125`). Ask mode: one `ApprovalCard` per call, awaiting futures; shell calls with a session-allowed prefix bypass cards via `auto` (`:3129–3156`); cancellation destroys cards and re-raises (`:3170–3177`).
- Prefix-allow is session-scoped: `_shell_allowed_session != _active_session_id → False` (`:3186–3190`); grant writes both the token set and the session id (`:3207–3208`); clear/delete/new all null `_active_session_id` (`:1465–1466, 1703, 1726`), which makes the set inert. Tested (`test_chat_sidebar.py:3194–3242`).
- `_always_allow_command` approves matching pending futures by token and destroys exactly those cards via `GLib.idle_add` (`:3198–3229`); `_always_approve_all` persists `set_approval_mode("always")` + approves all pending (`:3238–3250`). Both tested (`test_chat_sidebar.py:3244–3280`).
- Mode toggle ↔ gate correctness: `_on_approval_toggled` → `set_approval_mode("ask"|"always")` (`chat_sidebar.py:1042–1045`); `get_approval_mode` validates against `.env` (`settings.py:296–304`); label/tooltip sync `_update_approval_toggle` (`:1047–1055`). Tested (`test_chat_sidebar.py:40`).
- ApprovalCard per-tool titles + summaries: `_TOOL_CARD_TITLES` (`approval_card.py:140–148`), `format_tool_summary` with change_graph/simple/run/shell branches (`:108–139`); the shell branch renders the literal fenced command and a background suffix for start_command (`:119–125`). Tested (`test_chat_sidebar.py:3145–3193`).
- Deferred resume: the turn loop persists history on the approval pause and resumes the SAME run via `agent.iter(deferred_tool_results=...)` (`chat_sidebar.py:3026–3042`); executor `output_type` includes `DeferredToolRequests` (`agent_factory.py:815` — required: a deferred run raises without it, per commit 38707ce message).

### 5. Integration surfaces — VERIFIED

- **Injection classification of shell results**: `PromptInjectionDefender` (capability) hooks `after_tool_execute` for every tool result (`site:.../prompt_injection_defender/_capability.py:192–228`); bare-str results (the shell tools return `str`) are classified (`values=[result]`); ordering is `'innermost'` — classified closest to tool execution, before ToolOutputLimits reshapes (`:183–186`). So a 40 KB build log is classified (≈20 ms at the measured 0.5 ms/KB) and then spilled if >20 k.
- **ToolOutputLimits on run_command**: toolset-internal `truncate_tail` at `max_output_chars=50_000` (`shell_toolset.py:265–276`) runs first; the >20k spill (`Band(over=20_000, action=Spill(then=Truncate()))`, `agent_factory.py:641–651`) then replaces the string with a handle — two-stage reduction, lossless via `read_tool_result`.
- **Compaction vs big shell logs**: `ClearToolResults(keep_pairs=3, min_clear_tokens=2000)` (`agent_factory.py:696–707`) can blank old tool pairs; the placeholder tells the model to re-call the tool. Spilled payloads are exempt (they're in the store, not history).
- **Planner visibility**: `get_run_log` is in `_PLANNER_FUNCTION_TOOLS` (`agent_factory.py:96–105`) — the read-only planner can read run logs including `run_in_progress`; the planner gets **no** `GrcShell` capability and its whitelist excludes every shell tool name — fail-closed (verified in the two capability lists at `agent_factory.py:830–852` vs `:870–884`).

---

## 3. REFUTED / DRIFTED CLAIMS

1. **"Approval is a registration-time resolution in pydantic-ai"** (`shell_tools.py:126–132` comment; AGENTS.md repeats the phrasing). In pydantic-ai 2.31 the approval classification is computed **per run** — `Tool.tool_def` reads `self.requires_approval` live (`site:.../tools.py:506–507`) and `FunctionToolset.get_tools` builds the `ToolDefinition` per run (`site:.../toolsets/function.py:608–624`). The post-registration mutation happens to work, but the stated mechanism is wrong; it would also work only because it runs in `__init__`, before any run. (Claim severity: comment-only, no behavior change.)
2. **"Flipping the composer Mode toggle to Auto remains the only way to approve everything"** (AGENTS.md). Refuted: `_always_approve_all` on **any** non-shell card (change_graph, run_flowgraph) persists the global gate-off (`chat_sidebar.py:3238–3250`) which auto-approves **shell commands** on every later turn (`:3123–3125`). The shell cards deliberately avoid exactly this (session-scoped prefix-allow instead, `:3141–3148`); the general cards quietly achieve it.
3. **`wait_for_run_end` docstring: "not_started when the Execute action was a silent no-op"** (`exec_monitor.py:156`). Refuted: `not_started` is returned only when `_last_run_log is None` — i.e. **no run has ever completed** in the process. After the first completed run, a no-op Execute (or a stale-proxy call) returns `"completed"` with the previous run's `last_run_code` (`exec_monitor.py:161–170`). The `run_flowgraph` result note "GRC did not start an execution" (`native_canvas.py:196–199`) is therefore unreachable in steady state.
4. **AGENTS.md: "`ShellToolset.for_run` returns a plain `ShellToolset` and would silently drop both"** — verified **true** in the harness (`_toolset.py:143–163`) and correctly mitigated (`shell_tools.py:211–221`). No drift.
5. **AGENTS.md: "`get_run_log` always carries `run_in_progress`"** — verified (`exec_monitor.py:116`, `res["run_in_progress"]` unconditionally set). No drift.

---

## 4. REDUNDANCY & LEAN AUDIT

### 4.1 run_flowgraph + stop_flowgraph → one tool? **Keep them separate.** Evidence:

- **Approval is per-`ToolDefinition.kind`, not per-call** (`tools.py:506`; `ToolDefinition` carries a single `kind`). A merged `flowgraph_control(wait=True, timeout=60, action="stop")` cannot keep `requires_approval=True` — the run would end deferred before the stop branch ever executes. Making only "run" ask requires the sanctioned `ApprovalRequiredToolset` wrapper with a per-call `approval_required_func(ctx, tool_def, args)` (`site:.../toolsets/approval_required.py:24–37`, doc: "raises `ApprovalRequired` for calls where the function returns True") or a `raise ApprovalRequired` inside the tool body (the tool_manager converts raised deferrals identically, `tool_manager.py:1101–1108`). That is **more** machinery than today's one-line `requires_approval=True` on the run tool + no flag on stop.
- **Schema cost is negligible**: the executor's model-visible surface is ~24 tools (8 domain + capability web tools + 8 fs + 4 shell + read_tool_result + search_conversation_history). Merging saves one slot.
- **Prompt/doc fallout is real and multi-site**: `prompts.py:60–65` and `:79–83` name both tools by name; `run_flowgraph`'s result notes and docstrings reference `stop_flowgraph` (`agent.py:769–776`, `native_canvas.py:169–199`); `_TOOL_CARD_TITLES` and `format_tool_summary` key on `"run_flowgraph"` (`approval_card.py:114–118, 144–148`). A merged tool breaks the "stop needs no approval" invariant unless the conditional-approval machinery is added and the card titles/summaries gain a new branch.
- **The current split already expresses the risk model correctly**: run = physical-world side effect → gated; stop = remedy → ungated. There is no duplicated engine between the two proxy methods worth merging (they share only trivial `cm`/`page` resolution).

### 4.2 `_apply_exec_approval` vs pydantic-ai's own sanctioned extension points

- pydantic-ai 2.31 ships exactly this use-case: `FunctionToolset(..., requires_approval=True)` per-toolset (toolsets/function.py:125–128) and `ApprovalRequiredToolset` (`toolsets/approval_required.py`). The harness `ShellToolset` does not use them, and the app mutates `Tool.requires_approval` post-registration. **Works (verified kind='unapproved' live)** but is a hand-rolled route; a cleaner minimal change would be `GrcShellToolset` overriding `add_function`… the app-side change would be: `super().__init__(...)` then `self.tools[name].requires_approval = True` — same thing. The sanctioned path with identical semantics is `self.tools[name].requires_approval = True` (mutation) vs a wrapper — mutation is the least-churn route; flag for awareness, not for change.
- **Truly dead code inherited from the previous audit still present**: `ui/approval_card.py:243–244 get_tool_call_id` (zero callers in `src/` and `tests/`; only a pyc byte-match — confirmed with `rg "get_tool_call_id" src tests`). Safe to delete.

### 4.3 Small duplicates worth noting (no action)

- The run note strings "Read the full console output with the get_run_log tool…" appear twice (`native_canvas.py:176–180` and `:183–190` in two branches) — trivially shareable, not worth the churn.
- `persist_cwd` plumbing: the subclass forwards `persist_cwd` (`shell_tools.py:220`) but the property setter swallows `_apply_captured_cwd`'s assignment (`_toolset.py:265–275`) — the captured cwd is silently discarded while the wrapping (`pwd > tmpfile; exit $__harness_ec`) still mutates the executed command (`_toolset.py:248–263`). Latent footgun only (the app never sets `persist_cwd=True`; `shell_tools.py:162` default False).

---

## 5. SMALL LOST DETAILS

1. **`run_command`'s model-visible default timeout is a lie (the one real bug).** The harness docstring says `timeout_seconds: Maximum seconds to wait (default: 30).` (`_toolset.py:332`); that text is the parameter description the model sees (verified live: the built schema carries `"description": "Maximum seconds to wait (default: 30)."`, `default: null`), while the actual default is `GRC_SHELL_TIMEOUT` → 600 s (`shell_tools.py:107–114`). A model reasoning about the 30 s budget is reasoning about a default that doesn't exist.
2. **`_always_approve_all` is a global gate-off that also approves shell commands** (§3.2) — the shell-scoped design is bypassed by clicking "Always accept" on a change_graph or run_flowgraph card, and the tooltip ("stop asking for approval", `approval_card.py:128–131`) never mentions shell. If "the Mode toggle remains the only way to approve everything" is the intent, this is a hole; if not, the tooltip and AGENTS.md need updating.
3. **`wait_for_run_end` "not_started"/"completed" ambiguity** (§3.3) — plus a consequence in `run_flowgraph`: `return_code` is read after `"completed"` (`native_canvas.py:173–175`), so a stale completion returns the *previous* run's code. Mitigated by the pre-gates (`set_enabled(True)` + mirroring GRC's own conditions), but the state machine has no way to tell "just ended" from "never started".
4. **Stale `agent_initiated` on a marker-less Execute.** If `FLOW_GRAPH_EXEC()` raises (unhandled — no try/except at `native_canvas.py:154–160`) or silently no-ops, the flag stays `True` until the next terminal marker — which can be a user's later failed run; `_fail` suppresses its notification (`exec_monitor.py:248–261`). No test covers this interleave.
5. **graph_modified note is change_graph-only**: manual canvas edits (the exact case the safety-net poll exists for) never call `notify_graph_modified` (`native_canvas.py:249–250` is the only caller), so a run log read after a manual edit carries no staleness note even though the same reasoning applies.
6. **Mode-toggle chrome says "flowgraph" only**: accessible name "Flowgraph change gate" (`chat_sidebar.py:1032`) and tooltips "ask before the agent changes the flowgraph" (`:1056–1061`) while the gate also covers run_flowgraph and shell commands. UI-text drift.
7. **`GRC_SHELL_TIMEOUT` typo resilience**: `default_timeout` swallows `ValueError` → 600 (`shell.py:110–113`) — fine; but the knob is only read at *agent build time* (`GrcShell` dataclass `default_factory`), so editing `.env` mid-session requires a Settings Save (rebuild). Consistent with other settings; note only.

---

## 6. UNVERIFIED

- **`test_button_integration.py::test_agent_modifies_graph_via_chat` (and the change_graph chat path in general)** — live-LLM suite (key-gated), not runnable here. Static analysis: `_build_cloud_agent` (`test_button_integration.py:47–65`) uses `grc_tools()` (approval-gated `change_graph`) with `output_type=[GrcAgentResponse, str]` — no `DeferredToolRequests` — and never resolves deferred calls. The approval gate shipped in `38707ce` (commit message: "Executor output_type extended with DeferredToolRequests (required: a deferred run raises without it)"); the test file's last touch was `cffcffe` (v0.3.1, before the gate). By construction, when the model calls `change_graph` the run ends with an unresolved `DeferredToolRequests` and the graph is never mutated — `assert "center_freq" in names` can only fail. **How to confirm**: run `GRC_TEST_BACKEND=ollama_cloud uv run pytest tests/test_button_integration.py::test_agent_modifies_graph_via_chat` with a key.
- **Behavior of a deferred run whose `output_type` excludes `DeferredToolRequests`** — whether pydantic-ai raises, retries, or returns the deferred output unvalidated: only partially traced (the graph has special-casing around `_collect_deferred_calls` at `_tool_execution.py`; exact outcome depends on the `_validate_result` path). The sidebar agent itself always includes the type, so production is unaffected; only the integration harness is exposed.
- **`_run_end` event timeout interplay when a user starts a new run while `stop_flowgraph`'s 10s wait is parked** — the new run's start marker clears the event, and the stop caller's `wait_for_run_end` then returns `completed` only when the *new* run ends. Unverified; extremely unlikely interleave (needs a user Execute inside the 10s window), no test.
- **Whether `actions.FLOW_GRAPH_EXEC()` can raise in real GRC** (e.g. handler exception) after the proxy's `set_enabled(True)` — impossible to verify headless; if it can, see finding 5.4.

---

## 7. RECOMMENDATIONS (ordered by impact)

1. **Fix the run_command timeout schema lie** — `src/grc_agent/shell_tools.py:184–202` (in `_apply_exec_approval`). Minimal diff:
   ```python
   tool = self.tools.get(name)
   if tool is not None:
       tool.requires_approval = True
       if name == "run_command":
           tool.description = (...)
           # The harness docstring hardcodes "(default: 30)"; the app's real
           # default is GRC_SHELL_TIMEOUT/600 (shell_tools.default_timeout).
           schema = tool.function_schema.json_schema  # same dict every read (verified)
           schema["properties"]["timeout_seconds"]["description"] = (
               "Maximum seconds to wait (default: GRC_SHELL_TIMEOUT, 600s)."
           )
   ```
   (Verified the schema dict is stable across reads, so the mutation persists.) Add a test asserting the schema description text.

2. **Add `_request_approvals` hermetic tests** (`tests/test_chat_sidebar.py`): (a) `GRC_AGENT_APPROVE_CHANGES=always` returns `ToolApproved` for every approval including `run_command`/`start_command`; (b) ask mode + prefix-allowed shell call auto-approves without a card; (c) deny path resolves `ToolDenied`; (d) CancelledError destroys cards. The method is import-safe (`chat_sidebar.py:3107`) and `DeferredToolRequests` can be constructed with `ToolCallPart`s.

3. **Consume `agent_initiated` on a no-start or exception** (`exec_monitor.py:138–150` + `native_canvas.py:159–160`). Minimal diff sketch:
   ```python
   # native_canvas.py run_flowgraph:
   monitor.mark_run_agent_initiated()
   try:
       actions.FLOW_GRAPH_EXEC()
   except Exception:
       monitor.mark_run_agent_initiated_cancelled()  # clears the flag
       raise
   ```
   with a new `mark_run_agent_initiated_cancelled()` that resets `self._agent_initiated = False` (and a test: mark → cancel → user failure → callback fires).

4. **Remove the not_started/completed ambiguity** (`exec_monitor.py:155–178`) with a run-epoch:
   ```python
   # in ExecutionErrorMonitor.__init__: self._run_epoch = 0
   # start marker: self._run_epoch += 1
   # wait_for_run_end: return "not_started" when the caller's epoch wasn't
   # seen (add param `epoch: int | None = None`; the proxy passes its own
   # epoch captured pre-action) — or simpler: the proxy records
   # monitor._run_epoch after the action and passes it to wait_for_run_end.
   ```
   Update the docstrings (`exec_monitor.py:155–158`, `native_canvas.py:196–199`) to match reality even if the epoch work is deferred.

5. **Resolve the global-gate leak**: either (a) restrict `_always_approve_all`'s persistence to non-shell tools with a tooltip that names run_flowgraph + shell ("approves future runs and shell commands too"), or (b) keep the current behavior and correct AGENTS.md §"Shell policy" claim. Minimal: change the button tooltip text in `approval_card.py:70–74` and the Mode-toggle tooltip (`chat_sidebar.py:1056–1061`) to say "changes, runs, and commands".

6. **Fix `test_button_integration.py::test_agent_modifies_graph_via_chat` for the approval gate**: `_build_cloud_agent` should either (a) include `DeferredToolRequests` in `output_type` and resolve approvals via `DeferredToolResults(approvals=...)` on the resumed `iter` (mirroring the sidebar loop), or (b) build a separate tool list with `requires_approval=False` change_graph for the harness. Note (b) changes what the test measures; (a) keeps the gate active.

7. **Add the missing monitor tests** (`tests/test_exec_monitor.py`): stale-`_agent_initiated` interleave (mark → no marker → user failure still notifies); proxy-level spawn-failure shape (`outcome="completed"` with `code=1` → `ran_successfully is False`); `run_flowgraph` tool returning the previous run's code on a silent no-op (documented as known-limitation or fixed by #4).

8. **Shell tests, one stronger assertion**: `test_exec_tools_carry_requires_approval_on_init_and_for_run` (`test_shell_toolset.py:87–95`) asserts the field; also assert `ts.tools["run_command"].tool_def.kind == "unapproved"` — the actual model-visible contract, and the thing a harness upgrade could silently break.

9. **`sync_manual_edit` staleness note** (small): call `monitor.notify_graph_modified()` from `sync_manual_edit`'s save path (`native_canvas.py:416–451`) so the `get_run_log` staleness note also covers manual edits. This is the last gap between the note's docstring ("modified in memory") and its triggers.

10. **Delete `get_tool_call_id`** (`ui/approval_card.py:243–244`, zero callers — carried over from audit A; still true).
