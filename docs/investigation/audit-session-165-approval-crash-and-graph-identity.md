# Audit — Session 165: the approval-card crash and the two "untitled" graphs

Behavioral post-mortem of one deliberate stress test. **The goal was never to make the requested flowgraph work** — it was to audit the agent's decisions and split model faults from harness faults.

**Session under audit:** `sessions` row 165, `2026-09-08 16:43:41 → 16:49:29 UTC`, project `playground/experiment_read_files/newtest`, OpenRouter `inclusionai/ling-3.0-flash-fin:free` (`runs.metadata`). Prompt: *"make me a qpsk modulator and show me e2e msgs getting modulated, coded, sent and decoded and compared with the original msg with theoritical BER curve and what not"*. Outcome: `Agent Error: 'str' object has no attribute 'get'`, zero graph mutations.

**Evidence base.** `.grc_agent/chat_sessions.db` (47.8 MB, WAL empty): 3 `runs` (`grc_planner-dd49e26f`, `grc_planner-handoff-926d19a1`, `grc_executor-62bd02a5`), 82 `events` (seq 1625–1706), 18 `snapshots` (seq 429–446, all `state='complete'`), 22 `tool_effects`, 3 `plan_items`, and the 35-message `sessions.messages` blob (229,317 chars). Library claims verified against installed sources — pydantic-ai 2.37.0, pydantic-ai-harness 0.28.0 — and against current pydantic-ai docs via Context7, never from memory. The F1 crash was **reproduced** against the real module. Every claim below cites a `file:line` or a table and seq; pre-fix line numbers are marked where the fix has since moved them.

---

## 1. Executive summary

| # | Fault | Owner | Severity |
|---|---|---|---|
| F1 | The deferred-approval card renders un-repaired model args: crashes on three arg families, garbles three others | Harness | Blocker |
| F2 | No graph identity anywhere in the model-facing surface, and the one instruction that would have supplied it is suppressed exactly when the graph is unsaved | Harness | High |
| F3 | `read_file` on a `.grc` path accepts `offset`/`limit`, discards them silently, and returns a byte-identical payload | Harness | Medium |
| F4 | Nothing records a client-side turn failure, and the failure-recovery path destroys the only copy of the response that caused it | Harness | Medium |
| F5 | Every composite argument sent as a JSON string | Model | High (trigger) |
| F6 | Reached for a forbidden `.grc` write path twice, against an explicit instruction | Model | High |
| F7 | The hand-authored `.grc` was GNU Radio 3.7-era XML with `^` used as exponentiation | Model | High |
| F8 | Replaced its own 8-step plan with 3 steps, dropping 5 | Model (+ harness disclosure) | Medium |
| F9 | Confused a block's `block_id` with its `instance_name` | Model | Low |
| F10 | 12 knowledge lookups and 4 reads before a single mutation attempt; re-grounded across the handoff | Model | Low |

**Headline.** The harness did not cause the agent's bad decisions — every model fault hit a guard, and every guard held. But the harness caused the crash, and it withheld the one fact the agent most needed: that the live graph and `newtest/untitled.grc` are different graphs. The run died on the one path with no guard at all.

---

## 2. Harness faults

### F1 — The approval card reads the model's raw args, and `change_graph` is the only tool that can kill it

Reproduced verbatim against the real module (headless, real widget constructor):

```
File "src/grc_agent/ui/approval_card.py", line 214, in __init__
    summary_text = format_tool_summary(call.tool_name, args)
File "src/grc_agent/ui/approval_card.py", line 135, in format_tool_summary
    return format_change_summary(args)
File "src/grc_agent/ui/approval_card.py", line 87, in format_change_summary
    _add_group(groups, "**Add blocks:**", _add_blocks_lines(args.get("add_blocks")))
File "src/grc_agent/ui/approval_card.py", line 38, in _add_blocks_lines
    name = b.get("instance_name") or "?"
AttributeError: 'str' object has no attribute 'get'
```

| args the model sends | pre-fix result |
|---|---|
| `{"add_blocks": "[{…}]"}` | raises at `approval_card.py:38` |
| `{"update_params": "[{…}]"}` | raises at `:64` |
| `{"update_states": "[{…}]"}` | raises at `:106` |
| `{"add_connections": "[\"a:0->b:0\"]"}` | no raise — **one bullet per character** |
| `{"remove_connections": "[…]"}` | same per-character garble |
| `{"remove_blocks": "[\"src0\"]"}` | same per-character garble |

The last three are worse than the crash: the user is asked to approve a change they cannot read — the exact failure the card's own comment claimed to prevent.

**Why this is a harness fault.** The project already built the right uniform mechanism. `JsonRepairCapability.before_tool_validate` schema-drives a repair of every JSON-stringified composite argument, and each `change_graph` list parameter additionally carries `JsonCoercedSequence`. Both are correct. But pydantic-ai keeps the repair in a local — `raw_args = await cap.before_tool_validate(...)` at `pydantic_ai/tool_manager.py:378-380` — and builds the approval request from the **original** `ToolCallPart` (`pydantic_ai/_tool_execution.py:942, 953, 977`; the alternate path at `pydantic_ai/result.py:1069-1083`). `call.args_as_dict()` (`approval_card.py:190`) parses the top-level JSON string successfully while every per-field stringification survives. `change_graph` (`agent.py:725`) is the only `requires_approval=True` tool with nested list-of-dict args — `run_flowgraph`'s are scalars, the shell tools' are a string and a float — so it is the only tool on this path.

`args_validator=` cannot help: `tool_manager.py:329-340` validates and coerces *first*, then calls the validator with the already-coerced dict and no reference to the part. Context7-confirmed against current docs, which also confirm `before_tool_validate` is contractually return-value based.

**Surfacing.** `ApprovalCard` is constructed at `chat/approvals.py:135` inside `_request_approvals`, awaited at `chat/turn_driver.py:299` — outside the `async with self._agent.iter(...)` block but inside `_run_agent_turn`'s own `try`. The `AttributeError` is caught by the catch-all at `turn_driver.py:332` (pre-fix) and formatted by `chat/errors.py:106` as `f"Agent Error: {e}"`.

**A latent second defect found while fixing this.** `tool_manager.py:378` passes `call.args` itself, so the pre-fix in-place repair loop rewrote the recorded `ToolCallPart` whenever a provider delivers arguments as a dict — silently editing the history of what the model emitted. OpenRouter delivered session 165's arguments as JSON strings, which is the only reason this session's evidence survived at all.

**Honest limit on the trigger.** The `change_graph` call that killed the turn **is not in the database**. `events` seq 1704/1705 record `model_request_started`/`model_request_completed` for step 6, but that response appears in no snapshot and not in `sessions.messages` (see F4 for why). So F1's trigger is established by reproduction plus elimination — the shape reproduces exactly, `change_graph` is the only tool that can produce it, and this model stringified *every* composite argument it sent all session (F5) — not by a recovered artifact.

### F2 — Two "untitled" graphs, and the one line that would have disambiguated them is suppressed

The largest cause of the agent's confusion, and it is entirely ours.

| | live graph (`inspect_graph`) | on-disk file (`read_file`) |
|---|---|---|
| identity | `graph_name: "default"`, options instance `default`, title *Not titled yet* | `graph_name: "untitled"`, title *5G NR Spectrum & Spectrogram Analyzer* |
| content | 2 blocks, 0 connections | 10 blocks, 6 connections, 14,759 bytes |
| location | unsaved buffer, **no path in the payload** | `playground/experiment_read_files/newtest/untitled.grc` |

Three compounding gaps:

1. **`inspect_graph` emitted no provenance.** Its payload keys were exactly `graph_name`, `blocks`, `connections`, `validation` — no path, no saved flag, no source marker (`adapter/graph.py:802-812`, pre-fix). `graph_name` is only the options block's `id` param, which is `"default"` for a fresh page — straight out of GRC's own `core/default_flow_graph.grc:8` — and coincides with a filename merely because saving renames the id to the sanitized file stem (`native_canvas.py:526-530`). Meanwhile the `.grc` read path *did* label its source (`fs_tools.py:317-325`): `source: file on disk`. So one channel was labelled and the other was not.
2. **The instruction that would have said it went silent exactly when needed.** `agent_factory.py:1007-1012` (pre-fix) returned `f"Active flowgraph file path: {cm.path}"` only `if cm and getattr(cm, "path", None)`, and returned `None` otherwise. `cm.path` is `page.file_path or None` (`native_canvas.py:748-753`), and GRC's `new_page` sets `page.file_path = ''` for a fresh tab (`gnuradio/grc/gui/MainWindow.py:259, 264`). Silence reads as *no information*, never as *evidence of unsavedness*. It also reached its dependency by probing a private `_canvas_manager` attribute — the pattern `deps.py:11-20` exists to end.
3. **Neither prompt mentioned the distinction.** `prompts.py:60` carried the `.grc`-write ban and nothing about live-versus-disk; the planner prompt (`prompts.py:68-83`) carried neither.

Result, in the model's own words — `sessions.messages` msg 19.0 (planner) and msg 31.0 (executor):

> *"There's a skeleton flowgraph (`untitled.grc`) already with 5G NR content, but I'll create a fresh dedicated QPSK E2E flowgraph."*
>
> *"Good — I've confirmed the live graph has the old 5G NR flowgraph in `untitled.grc`. I'll now replace it with the QPSK E2E flowgraph."*

Both conflate the two. `inspect_graph` had just returned a 2-block `default` graph and `read_file` a 10-block `untitled` 5G NR graph, and the model called the latter "the live graph". It never once mentions the live graph being empty, unsaved, or distinct from the file — the harness supplied every reason to believe they were the same.

**Aggravating detail, worse than it first looked.** `sessions.grc_file_path` for session 165 is `/…/GRC_Agent/untitled:untitled.grc`: `chat_sidebar.py:697-698` builds the sentinel `f"untitled:{page_title}"`, and `db.py:496` (pre-fix) `Path(...).resolve()`d it — resolving a non-path against the process CWD and persisting a fabricated absolute path. The consequence is not a mislabeled row: `get_recent_sessions` filters on `path_obj.exists() and path_obj.is_file()` (`db.py:327, 331`) and session loading rejects a missing file (`chat/session.py:275-277`), so **every chat held on an unsaved tab is permanently unreachable** — sessions 162, 163, 164 and 165 all carry the same fabricated form.

### F3 — `.grc` reads accepted `offset`/`limit` and dropped them without a word

Pre-fix, `read_file` returned before the paging arguments were ever read (`fs_tools.py:298-308`), and `_inspect_grc_file` could not have accepted them anyway — the inspection engine has no line concept. Both parameters were nonetheless advertised unconditionally in the docstring (`fs_tools.py:290-293`) and in the generated JSON schema the model reads.

The cost is visible in the transcript: three reads of the same file at `limit` `"50"` → `"10"` → `"5"` (msgs 15.0, 25.2, 29.0) returned three payloads of 8,800 chars with identical sha256 `1cd5eb53…`. The model was trying to shrink the view, had no way to learn that was impossible, and spent two steps on it. Passing `"50"` as a string was harmless — pydantic's lax mode coerced it to `50`, which was then discarded.

This is AGENTS.md §3 ("No Silent Transformations or Hidden Truncation") applied to an *input* argument. The `.grc` branch also never reaches `_format_lines`, the only thing that emits the harness's `"… (N more lines. Use offset=N to continue reading.)"` notice.

**The reroute itself was never silent** — the header names the engine and the source (`fs_tools.py:322-325`), one of `live in-memory flowgraph (includes unsaved canvas edits)` / `active file on disk` / `file on disk`. Only the dropped parameters were.

### F4 — Nothing recorded the turn failure, and the recovery path destroyed the evidence

`run_completed` (events seq 1706) is **honest**: `DeferredToolRequests` is a declared final output (`agent_factory.py:945`), and the harness emits `run_completed` on the result path (`pydantic_ai_harness/step_persistence/_capability.py:298`, reached from `pydantic_ai/agent/__init__.py:286`), not from a `finally`. The run really did complete; the *turn* died afterwards in `_request_approvals`.

The gap is that nothing recorded that. Searched and confirmed empty for session 165: zero events with `kind LIKE '%failed%'`, `error` empty on all 82 rows, and no occurrence of `Agent Error`, `'str' object`, `AttributeError` or `Traceback` in `sessions.messages`, in any of the 18 snapshots, or in `events.metadata` / `events.error` / `tool_effects.effect_summary`. The database said the run completed; the user saw `Agent Error`. Nothing reconciled the two.

Worse, the evidence was actively destroyed, by three mechanisms in sequence:

1. `turn_driver.py:297-298` persisted the run's messages — including the response carrying the unapproved call — *before* `_request_approvals`.
2. The crash then hit the catch-all, which called `_recover_history_after_failure` → `_clean_message_history_for_new_turn` (`chat/history.py:31-44`). That correctly pops a trailing `ModelResponse` with unprocessed tool calls (pydantic-ai rejects a new prompt on such a history), and re-saved — overwriting the 36-message copy with 35 messages ending at the retry prompt. `sessions.updated_at` = `16:49:29` matches `run_completed` to the second.
3. That pop's own comment claimed the calls "stay recoverable in the step-store snapshots". For this failure shape they are not: `StepPersistence` gates **every** snapshot on `is_provider_valid` (`_capability.py:288, 544`), and a history ending in an unresolved tool call is not provider-valid — hence 18 snapshots ending at step 5, and none for step 6.

And the traceback that would have named the crashing tool went to the terminal only: `src/grc_agent/` configures no `logging.basicConfig` and no `FileHandler` anywhere.

**Secondary observability notes, with correct ownership:**

- `tool_effects` leaves the two `ModelRetry`-terminated calls pinned at `status='started'` with `ended_at=NULL` (`inspect_graph` 16:48:41 / events seq 1690, `write_file` 16:49:25 / seq 1703 — the 22-vs-20 gap against `tool_call_completed`). That table is written only by the harness (`_capability.py:420-427, 449, 483`), and the root cause is upstream in pydantic-ai: `tool_manager.py:469-470` re-raises `ModelRetry` **without** calling `on_tool_execute_error`, so no terminal effect is ever recorded. Not ours to fix; worth reporting.
- **Test isolation gap, found while verifying the `run_tools` drop and not caused by it.** Most suites redirect `GRC_AGENT_ENV` to a tmp dir (`tests/test_session_persistence.py:20-25` and friends), but some unit tests construct a `ChatSidebar` without doing so (e.g. `tests/test_chat_sidebar_golden.py:379`), and the sidebar's own start-up initializes the store — so those tests run `init_db` (sweeps included) against the developer's live `.grc_agent/chat_sessions.db`. Evidence: that DB holds 125 `runs`, 250 `events` and 160 `snapshots` against only 5 `sessions`, and `run_tools` disappeared from it during this session's test runs — dropped by the new statement, executed through one of those unredirected tests. Not fixed here; worth a follow-up, since the fast gate is otherwise hermetic.
- `run_tools` was orphaned schema: 140 rows, all created between `2026-08-22 14:27:34` and `17:33:41`, 20 per executor run across seven runs. No writer in `src/`, nothing in `git log -S"run_tools"`, and harness 0.28.0 does not create it (a fresh `SqliteStepStore` makes only `runs`, `events`, `snapshots`, `tool_effects`). No sweep reached it either (`db.py:247-262` covers `snapshots`, `events`, `tool_effects`, `runs`; `plan_items` separately; `media` deliberately).

---

## 3. Model faults

### F5 — Stringified every structured argument (the trigger, high)

`inspect_graph {"targets": "[\"samp_rate\"]"}` (msg 17.0) and `{"targets": "[\"options\"]"}` (msg 27.0); `read_file {"limit": "50"}` (msg 15.0), `"10"` (25.2), `"5"` (29.0); `write_plan {"items": "[…]"}` with `items` a 4,070 / 3,643 / 1,292-char **string** in all three calls; and, by elimination, `change_graph`. Genuinely scalar arguments passed through fine — all 12 `query_knowledge` calls carried real scalars. Every coercion shim absorbed this except the one path that had none (F1). A free-tier small-model weakness, not a harness defect — but it is the trigger, so the harness now applies one rule uniformly.

### F6 — Reached for a forbidden `.grc` write path twice (high)

- Planner, msg 21.0: `write_file` on `untitled.grc` with 21,886 chars of content → `Unknown tool name: 'write_file'. Available tools: 'duckduckgo_search', 'file_info', 'find_files', 'generate_python', 'get_run_log', 'inspect_graph', 'list_directory', 'query_knowledge', 'read_file', 'search_files', 'web_fetch', 'write_plan'`. The planner's fail-closed allowlist (`agent_factory.py:99-111`, enforced by `PrepareTools` at `:1000`) never exposed it — nor `save_graph`, nor `change_graph`, so the planner structurally could not have written or saved anything.
- Executor, msg 33.0: `write_file` on `untitled.grc` with 21,332 chars → *"Writing .grc files is not allowed — flowgraphs are edited through the change_graph tool…"* (`fs_tools.py:262-263`, reached from `:363-365`; `ValueError` → `ModelRetry` via the `@_recoverable` decorator).

Both guards worked. The instruction existed verbatim — `prompts.py:60`: *"Flowgraph structure is edited ONLY through change_graph — never by writing or scripting .grc files."* — and was ignored. Model fault.

### F7 — The hand-authored flowgraph was wrong anyway (high)

Both payloads were GNU Radio 3.7-era XML (`<flowgraph>`, `<block><key>`, `<param><key>/<value>`, `<connection><source_block_id>`), while current GRC `.grc` files are YAML — the very format the file it wanted to overwrite is written in, and which it had read three times. The second attempt fixed a mismatched `<type>…</id>` closing tag and swapped invented `channels_channel_model` parameters for real ones, so the model was iterating on a format that could never have loaded. It also invented a second `options` block alongside the file's own, and used `10^(-esn0_db/10)` — in Python `^` is XOR, not exponentiation. Had `write_file` been permitted, this would have destroyed a real 14,759-byte flowgraph. The `.grc` write ban earned its keep.

### F8 — Destroyed 5 of its own 8 plan steps (medium)

`write_plan` #1 (msg 7.1) was rejected for malformed JSON — the model appended `, "warnings": []}` after the closing bracket, and `coerce_plan_items` (`agent_factory.py:163`, wired as a `BeforeValidator` at `:207`) returned *"Invalid JSON for plan items… Error: Extra data: line 1 column 4054"*. #2 (msg 9.0) recorded 8 steps. #3 (msg 19.1) replaced them with 3, dropping the theoretical-BER, visualization-sink and message-display steps; `plan_items` holds exactly those 3, statuses `in_progress`/`pending`/`pending`, never advanced. The semantics were disclosed — the tool docstring says *"Create or replace the whole plan"* and the `Planning` guidance says *"Pass the full plan every time you call `write_plan`"* (`agent_factory.py:992`) — so this is a model fault. Minor harness contribution: the return string `"Plan updated: 3 step(s)."` (`agent_factory.py:221`) does not disclose that 5 recorded steps were dropped, and the superseded 8-step plan stays in the shared history as an authoritative-looking tool return, so the executor inherited two contradictory plans.

### F9 — `block_id` versus `instance_name` (low)

At msg 27.0 it called `inspect_graph(targets=["options"])` one message after a payload that showed `instance_name: "default", block_id: "options"`. The `ModelRetry` listed every valid block (`[{instance_name: default, block_id: options}, {instance_name: samp_rate, block_id: variable}]`) and the model recovered. Model fault, exemplary harness behavior.

### F10 — Research-to-action ratio (low)

12 `query_knowledge` calls, one `list_directory` and three `.grc` reads before a single mutation attempt; the executor re-ran two catalog lookups the planner had already made inside the same shared history. Not a defect, but wasteful across the handoff.

---

## 4. Corrections to the initial reading of this session

1. **`save_graph` handled the name collision correctly, and was never called.** `resolve_save_target` (`adapter/graph.py:891-930`) walks `while candidate.exists()` to the smallest free `untitled(n).grc`, and the options id is renamed before serialization (`native_canvas.py:526-530`); regression-tested at `tests/test_save_graph_tools.py:328-355`, which proves the pre-existing file byte-identical afterwards. The 14,759-byte file was never at risk from the save tool. The string `save_graph` does not appear anywhere in session 165, and it is absent from the planner's allowlist. What went unplanned was the *reasoning* (F2), not the write.
2. **The `.grc` reroute is disclosed**, engine and source both (F3). Only the dropped `offset`/`limit` were silent.
3. **`run_completed` is honest** (F4). The missing record is of the *turn* failure that followed it.
4. **It was `write_file` both times, not `edit_file`.** `edit_file` is denied identically and just as specifically (`fs_tools.py:262-263` via `:407-409`).
5. **Nothing in the prompts warns about `^` versus `**`.** The closest guidance is that math functions need the `math.` namespace (`prompts.py:36-37`). F7's exponentiation bug was unguarded, not warned-against.
6. **The crash-triggering arguments are unrecoverable from this session.** F1's trigger rests on reproduction plus elimination, not on a recovered artifact.

---

## 5. What changed

Landed per [`docs/plans/2026-09-08-1340-fix-session-165-harness-grounding-plan.md`](../plans/2026-09-08-1340-fix-session-165-harness-grounding-plan.md). All four harness faults; none of the model faults were chased with prompt folklore (AGENTS.md §1 — F6's instruction already exists, and a stronger model obeys it).

| Fault | Fix | Evidence |
|---|---|---|
| F1 | The repair rule moved to one shared home, `src/grc_agent/json_args.py`; the capability delegates to it and so does the approval card, which now also renders a non-mapping element literally instead of raising. The rule returns a new mapping, so the recorded history keeps what the model actually emitted. The validation contract is unchanged: only the schema-free renderer decodes stringified list *elements*, since loosening the schema-driven path would make arguments the contract currently rejects start validating — strictness there is a recorded decision (`docs/backlog.md`, "Scenario suite default model"). | `tests/test_json_args.py`, `tests/test_chat_sidebar.py::test_change_summary_formatter_repairs_json_stringified_args`, `::test_approval_card_renders_json_stringified_change_graph_args` |
| F2 | `inspect_graph` payloads carry `file_path` (null = never written to disk), sourced from GRC's own `flow_graph.grc_file_path`; the instruction hook always returns a line and reads the path through the declared deps surface; both prompts carry the live-versus-disk contract; the session row stops fabricating a path. | `tests/test_adapter_graph.py::test_inspect_graph_reports_file_identity`, `tests/test_fs_tools.py::test_grc_read_carries_the_file_identity_of_what_it_read`, `tests/test_agent_factory.py::test_active_flowgraph_context_*`, `tests/test_isolation.py::test_system_prompt_keeps_unobservable_contracts`, `tests/test_db_sessions.py::test_unsaved_tab_sentinel_is_stored_verbatim` |
| F3 | A `.grc` read given `offset` or `limit` discloses it in the header that already names the engine and source; the schema stops advertising them unconditionally. | `tests/test_fs_tools.py::test_grc_read_discloses_ignored_paging_arguments`, `::test_grc_read_discloses_paging_for_the_live_graph_too` |
| F4 | Every client-side turn failure archives its at-failure history — captured *before* the salvage pops the trailing call — as a `turn_failure` run, with one `run_failed` event carrying the exact text the user saw. `run_tools` is dropped. The false snapshot-recoverability comment is corrected. | `tests/test_session_persistence_advanced.py::test_turn_failure_after_approval_request_is_recorded`, `tests/test_db_sessions.py::test_record_turn_failure_archives_history_and_the_user_facing_error`, `tests/test_session_persistence.py::test_init_db_drops_the_orphaned_run_tools_table` |

**Deliberately not done.**

- Chats held on unsaved tabs remain unreachable from Recent. The fabrication is fixed; reopenability needs a nullable-path schema migration and listing/loading work, and was scoped out.
- No file logging. The DB now records the user-facing error; the traceback stays terminal-only.
- No prompt emphasis for F5–F10, and no change to the `.grc` write ban or the planner allowlist — both did their job.
- Two upstream items, reported not patched: pydantic-ai's deferred-approval path hands out un-repaired args (`tool_manager.py:378-380` versus `_tool_execution.py:953`), and `ModelRetry` leaves `tool_effects` rows unterminated (`tool_manager.py:469-470`).

**Gates.** `uv run pytest tests/ --ignore=tests/test_integration.py --ignore=tests/test_button_integration.py` → 664 passed; `uv run ruff check` → clean; the GTK suites under `xvfb-run` → 180 passed, golden transcript unchanged.
