# Audit A — Lost Details, Drift & Lean

Investigation brief A. Scope: all of `src/grc_agent/` (incl. `ui/`, `adapter/`, `providers/`) against AGENTS.md, plus pydantic-ai 2.31.0 / pydantic-ai-harness 0.23.0 (`uv.lock`, verified via `importlib.metadata`). Environment: PyGObject 3.48.2 → the `gbulb` event-loop path is the one live here (`event_loop.py` fallback), `gi.events` path unexercised.

**Command evidence baseline:** `uv run pytest tests/ --ignore=tests/test_integration.py --ignore=tests/test_button_integration.py -x -q` → **445 passed** (24.34s). `uv run ruff check --select F401,F841,F811 src/` → **All checks passed!**. No source file was modified; no live-LLM suite was run.

---

## 1. Executive summary

- **All 8 Part-1 feature groups exist, are wired, and match AGENTS.md.** `resolve_auto` + Phase-5 `add_blocks`-only rewrite + `connection_silently_dropped` (pre-Phase-3 snapshot, object-identity set compare) — `adapter/graph.py:357, 989, 1193, 1274–1298`; two-stage `keep_param` — `graph.py:509–546`; snapshot pushes from both mutation paths, no UI undo buttons; 1.5 s safety-net poll with state-cache gate + `_POLL_FULL_CHECK_EVERY=10` backstop; compaction `keep_pairs=3`/`min_clear_tokens=2_000`/0.85 fraction/pre-compaction archive; `Band(over=20_000)` spill + 64 KiB media; exec_monitor `run_in_progress`/`agent_initiated`/-15 carve-out/512 KB deque; fs sandbox suffix allowlist + `.grc` name rule + `**/` nested denies. Regression suites for each exist in `tests/` and all pass.
2. **One real AGENTS.md drift:** "`_compute_ranks` is still used to feed `compute_full_layout`" is false — `_compute_ranks` (`adapter/layout.py:193`) is a test-only thin wrapper; `compute_full_layout` uses `_compute_layout_model`/passed-in model (`layout.py:280–303`). Only tests call it (`test_layout.py:36, 605, 648`).
3. **Dead code (5 items):** `ui/approval_card.py:243 get_tool_call_id` (zero callers anywhere), `settings.get_project_dir`/`set_project_dir` (`settings.py:324, 333`; production-dead — the sidebar re-implements the same `GRC_PROJECT_DIR` upsert inline), `fs_tools.set_project_dir_provider` (`fs_tools.py:96`; test-only), `NativeFlowgraphProxy.get_state_lock` (`native_canvas.py:89`; zero callers), `adapter/layout.py:_compute_ranks` (test-only).
4. **Duplication the framework already covers:** none of the flagged items is safely removable without a behavior decision, but three genuine table-duplications exist inside the app: provider→endpoint URL tables are defined twice (`_PREFLIGHT_ENDPOINTS` vs `_OPENAI_SHAPED_PROVIDERS` + the Google URL in `_google_context_length`), and base-URL `/v1`-suffix logic is triplicated. `settings.get_project_dir`/`set_project_dir` duplicate the sidebar's inline env-key logic.
5. **No hand-rolled retry/loop/context machinery found** — retries go through `AsyncTenacityTransport` + `retries={"tools": 3, "output": 3}`; compaction/trimming through harness capabilities; the only manual history-trimming (`_clean_message_history_for_new_turn`, `_without_truncated_thinking_tail`) handles pydantic-ai states the framework deliberately rejects (verified: no sanctioned utility exists in 2.31.0).
6. All 14 AGENTS.md claims spot-checked in Part 4 → 13 CLAIM-OK, 1 DRIFTED (the `_compute_ranks` one, severity: cosmetic).

---

## 2. VERIFIED FACTS (Part 1)

### (1) Auto type resolution + epy_block silent-drop check — VERIFIED

- `resolve_auto(flow_graph, block_name, param_key, add_connections, new_block_names, is_add_phase, add_blocks, update_params)` at `adapter/graph.py:357`. Refuses to resolve from another `"auto"` neighbor (`graph.py:418–438`, "Deliberately not treated as a candidate"), raises `ValueError` when no explicit value exists anywhere (`graph.py:455–461`). `_canonical_dtype` is built from GNU Radio's own `Constants.ALIASES_OF` (`graph.py:329–345`).
- Phase-5 rewrite is **gated on `add_blocks` only**: `graph.py:1193–1194` (`if add_blocks: flow_graph.rewrite()`) — matches the doc's epy_block consequence claim (only a same-call `add_blocks` unlocks the early port regeneration). Phase 7 `add_connections` follows at `graph.py:1197–1248`; final unconditional rewrite at `graph.py:1250`.
- Silent-drop check: snapshot `connections_before_rewrites = set(flow_graph.connections)` at `graph.py:989` — taken after deliberate Phase-1/2 removals, before Phase 3 (`add_blocks` at 992) — exactly the "pre-phase-3" scope the doc claims. Compare at `graph.py:1274–1298`: `expected = connections_before_rewrites | {c for _, c in made_connections}`; set-difference on `Connection` objects (identity-based; docstring at 1260–1273 explains `Port.rewrite()` rekeys in place). Error code `connection_silently_dropped` at `graph.py:1288`, checked **unconditionally, not just under `force`** (`graph.py:1254–1259` comment).
- Tests assert all of it and pass: `test_change_graph_same_call_port_dtype_change_and_connect_rolls_back` (`tests/test_adapter_graph.py:449–489`), two-call workaround (`:491–545`), `test_change_graph_update_params_only_batch_catches_dropped_preexisting_connection` (`:546+`), `auto_resolve_failed` (`:337, :357`).
- `change_graph_func`'s `ModelRetry` only suggests `force=True` for `error_type == "validation_failed"` — `agent.py:644–652`.

### (2) keep_param two-stage filtering — VERIFIED

- `keep_param` at `adapter/graph.py:509`. Stage A (`graph.py:519–524`) drops exactly: `dtype == "id"`, `param_key == "showports"`, `param_key.startswith("bus_structure_")`, `hide == "all"`, `dtype == "gui_hint"` — matches AGENTS.md verbatim.
- Stage B (`graph.py:526–546`): `hide == "none"` keep; type-controlling or `generate_options` count as structural (`graph.py:530–532`); `hide == "part"` non-structural enums dropped unless custom or variable-referencing (`:534–540`); `dtype == "enum"` kept iff non-default or structural (`:542`); default-valued params kept only when referencing a variable (`:544–546`). Doc's "value != default OR references a variable OR type-controlling OR generate_options" holds.
- Invoked per-param with `mode=selected_view` at `graph.py:657`. Stage-B rule is overview-only (`graph.py:525`: `if mode != "overview": return True`).

### (3) Undo/redo — VERIFIED

- GRC's native StateCache owns user undo/redo; `adapter/snapshots.py` module docstring (`snapshots.py:24–34`) states it is "kept only for the snapshot-push side effect". `push_undo_snapshot` (`snapshots.py:70`) is called from **both** claimed paths: `graph.py:1396` (change_graph save path, with `initial_data` baseline seeding) and `native_canvas.py:448` (`sync_manual_edit`).
- Agent edits push into GRC's native state cache: `after_agent_edit` → `page.state_cache.save_new_state(fg.export_data())` (`native_canvas.py:356–358`).
- No UI buttons: grep for undo/redo in `chat_sidebar.py`, `ui/*.py`, `desktop_app.py` finds only unrelated strings ("This cannot be undone.", delete-conversation confirm). No `NativeCanvasManager.undo()/redo()` methods exist.
- Test coverage: `tests/test_native_canvas.py` exercises the push path.

### (4) 1.5 s safety-net poll — VERIFIED

- `GLib.timeout_add(1500, self._check_for_unsynced_edit)` (`native_canvas.py:650`); `_POLL_FULL_CHECK_EVERY = 10` (~15 s backstop) at `native_canvas.py:39` with the class-level rationale comment (`:25–39`).
- Cheap gate: `_state_cache_version(page)` tuple `(current_state_index, num_prev_states, num_next_states)` (`native_canvas.py:509–517`); poll skips the full export-hash compare when unchanged and not a backstop tick (`:646–654`).
- Baselines: `_sync_page_baselines()` sets `last_synced_export_hash` (in-memory export hash), `last_disk_hash` (`_sha256_file` of the file), `_baseline_path` (`native_canvas.py:524–536`). Path-change re-baseline in the poll: `page.file_path != self._baseline_path → _sync_page_baselines()` (`:639–643`) — covers untitled-saved-in-place/Save-As (the doc's "baselines follow the page's path").
- `sync_manual_edit` lock protocol (`native_canvas.py:416–451`): `fcntl.flock(fd, LOCK_EX | LOCK_NB)` on `.grc_agent/<name>.lock`; `BlockingIOError` → log + defer to next tick (never blocks the loop); disk-hash changed → refuse with user-visible status via `on_sync_failed` (`:431–442`); atomic write via `_atomic_write_text` then re-derive both hashes then `push_undo_snapshot` (`:444–448`).

### (5) Compaction — VERIFIED

- Tiers in `_build_compaction_capability`: `ClampOversizedMessages` → `ClearToolResults(max_tokens=1, keep_pairs=3, min_clear_tokens=2_000, placeholder=...)` → `ResilientSummarizingCompaction` → `SlidingWindowCompaction(max_tokens=1, keep_messages=20, preserve_first_user_message=True)` (`agent_factory.py:688–714`). Harness confirms `keep_pairs=3` is the library default and `min_clear_tokens` is a total-reclaim gate (`pydantic_ai_harness/compaction/_clear_tool_results.py:86, 98, 45`).
- 0.85 fraction with probed window at `agent_factory.py:744, 752, 761, 766`; `GRC_COMPACTION_TARGET_TOKENS` escape hatch at `:715–725`; `_MODEL_WINDOW_OVERRIDES` at `:32–41`; probes: Ollama `/api/show → model_info.context_length` (`:407–438`), OpenAI-shaped `/v1/models → context_length` (`:442–458`), Codex `context_window` (`:460–465`), negative-cache TTL 60 s (`:376, 395–405`).
- `TranscriptPreservingTieredCompaction` (`agent_factory.py:592–618`) archives `pre_compaction_transcript` via `archive_transcript` (`db.py:211–234`, deliberately re-raising — "store failure fails the turn" claim verified in docstring `db.py:213–215`). Manual `compact_now` archives `manual_compaction_transcript` first (`chat_sidebar.py:1369–1377`).
- `ConversationSearch(SnapshotHistorySource(get_step_store()), scope="conversation")` on both roles (`agent_factory.py:829, 861`); `max_snapshots_per_run=None` (`db.py:173–175`).
- Session-14 tuning comment (keep_pairs=2 blanking) preserved verbatim at `agent_factory.py:692–698`.

### (6) Tool output limits + media externalization — VERIFIED

- `ToolOutputLimits(bands=[Band(over=20_000, action=Spill(then=Truncate()))], store=LocalFileStore(base_dir=get_db_path().parent / "tool_overflow"))` (`agent_factory.py:641–651`). `cleanup_after` deliberately unset (`:649–651`).
- `read_tool_result` exemption is harness-native: `READ_TOOL_NAME = 'read_tool_result'` with "Its own returns are exempt from reduction" and the exemption check `if call.tool_name == READ_TOOL_NAME: return` (`pydantic_ai_harness/tool_output_limits/_capability.py:37, 194–205`).
- 64 KiB media externalization: harness default `_DEFAULT_MEDIA_THRESHOLD_BYTES = 64 * 1024` (`pydantic_ai_harness/step_persistence/_store.py:60`); the app relies on the default (no `media_threshold` kwarg anywhere in `src/`), doc'd at `db.py:168–170`.

### (7) exec_monitor — VERIFIED

- `run_in_progress: self._tracking` always present in `get_last_run_log` (`exec_monitor.py:113–126`), plus `in_progress_note` while live.
- `mark_run_agent_initiated()` (`exec_monitor.py:141–150`) sets a flag consumed at **both** terminal markers (`exec_monitor.py:212, 231` → `self._agent_initiated = False`); `_fail` suppresses the callback while set (`:264–271`). Pre-action call verified at `native_canvas.py:170–175` (comment: start marker fires synchronously inside the action).
- `wait_for_run_end` returns `completed / still_running / not_started` (`exec_monitor.py:156–170`); `last_run_code` property (`:173–175`).
- SIGTERM carve-out: `_SIGTERM_RETURN_CODE = -15` (`:33`), gate `if code != _SIGTERM_RETURN_CODE and (code != 0 or self._has_runtime_error): self._fail(code) else: self._reset()` (`:242–245`).
- 512 KB cap: `_MAX_LOG_BYTES = 512 * 1024` (`:38`), byte-counted, whole-chunk pop from the oldest end (`_append`, `:253–261`); `_last_run_evicted` frozen at Done time (`:221`); `log_truncated`/`truncation_note` surfaced (`:124–128`).

### (7) fs_tools sandbox — VERIFIED

- Write suffix allowlist `WRITE_SUFFIXES` (`fs_tools.py:151–166`) exactly as documented (`.py .cmake .txt .md .m .json .yml .yaml .c .cc .cpp .cxx .h .hh .hpp .xml .conf .rst .i`; no `.grc`).
- `.grc` name rule: `_is_grc_name` — any name containing `.grc` case-insensitively (`fs_tools.py:186–196`). Read routing in `read_file` (`:239–247` → `_inspect_grc_file` with active-file→live-object preference at `:278–296`); write gate `_assert_writable_suffix` applied to **both** the requested name and the symlink-resolved target (`:213–230`, called at `:299–300` and `:341–342`).
- `_DENIED_PATTERNS` incl. `**/` nested forms: `.env`, `.env.*`, `.grc_agent/*`, `.git/*`, `.envrc`, each in bare and `**/`-prefixed form (`fs_tools.py:134–149`). Harness `_matches` strips the leading `**/` for the root case — verified in harness source (`pydantic_ai_harness/filesystem/_toolset.py:140–151`).
- Caps: `max_read_lines=1000`, `max_list_results=200` (`fs_tools.py:211–212, 436–437`). `_safe_resolve` gating chokepoint for the unset-root `ModelRetry` (`:202–209`); fixed-text parse-failure `ModelRetry` with details logged (`:297–305`).
- Tests pass incl. nested-deny and symlink-bypass regressions (`test_fs_tools.py:205–234`).

### (8) Additional Part-4 CLAIM-OK verifications (evidence)

| # | Claim | Verdict | Evidence |
|---|-------|---------|----------|
| 1 | change_graph does not report `canvas_synced` | CLAIM-OK | `agent.py:659–666` (comment) + `notify_edit` unconditional `return {"ok": True}` `native_canvas.py:221–228`; `after_agent_edit` logs GTK failures (`:340–343`) |
| 2 | `get_run_log` unwired raises explicit do-not-retry | CLAIM-OK | `agent.py:684–695`; same for run/stop wiring-faults `:774–806`; "no run yet" stays a normal result `:696–699` |
| 3 | Planner fail-closed allowlist contents | CLAIM-OK | `_PLANNER_FUNCTION_TOOLS` `agent_factory.py:106–119`; harness `READ_ONLY_TOOL_NAMES` = {read_file, list_directory, search_files, find_files, file_info} (`pydantic_ai_harness/filesystem/_toolset.py:20–24`); `PrepareTools(_prepare_planner_tools)` wired `agent_factory.py:899` |
| 4 | PromptInjectionDefender scope limits | CLAIM-OK | `prompt_injection_cap = PromptInjectionDefender(block_high_risk=True, on_detection=...)` `agent.py:516–519`; `semantic_detection` defaults False; harness docstring: pattern tier inspects "known risky fields, bare string results, and ToolReturn content" (`_capability.py:95–103`); stackone-defender **0.7.4** installed (matches doc's tested version) |
| 5 | model_catalog single parser | CLAIM-OK | `_list_http_models` is the only HTTP parser (`model_catalog.py:51–72`); `probe_backend` = parser + membership check (`agent_factory.py:1007–1054`, reuses it at `:1030`); Settings Load uses `list_models` (`ui/settings_dialog.py:390`) |
| 6 | Codex `?client_version=` catalog filter | CLAIM-OK | `CLIENT_VERSION = "9999.0.0"` with the gpt-5.5/gpt-5.6 hiding story (`providers/openai_codex/model.py:34–44`); server array order kept — no sort in `list_models` (`model.py:159–167`) |
| 7 | Codex `stream: true` + reasoning summary | CLAIM-OK | `CODEX_MODEL_SETTINGS` (`model.py:57–64`); non-streaming `request()` raises explicitly (`model.py:135–140`) |
| 8 | Codex auth never touches `.env`; 0600/0700 | CLAIM-OK | `credentials.py:72` (`~/.config/grc_agent/openai-codex-auth.json`), `:136–151` (0700 dir, `O_NOFOLLOW`, 0600 temp+rename); `PROVIDER_API_KEY["openai_codex"]` is None (`ui/providers.py:65–77`) |
| 9 | Codex refresh double-checked under asyncio.Lock | CLAIM-OK | `_lock = asyncio.Lock()` (`credentials.py:35`), double-check pattern `:186–204` |
| 10 | `_with_state_lock` gone | CLAIM-OK | no occurrence in `src/` (grep); `get_state_lock()` returns None unconditionally but now has **zero callers** (see §4) |
| 11 | `get_run_log` always carries `run_in_progress` | CLAIM-OK | `exec_monitor.py:113–126` |
| 12 | Planner gets `Planning(tools=['write_plan','read_plan'], guidance=...)` explicit | CLAIM-OK | `agent_factory.py:885–899`; the `get_instructions` gate rationale preserved in the comment `:893–896` |
| 13 | `retries={"tools":3,"output":3}` + AsyncTenacityTransport | CLAIM-OK | `agent_factory.py:856–857`; transport `:166–178` (AsyncTenacityTransport w/ retry_if_exception_type((TransportError, HTTPStatusError)), 3 attempts, reraise) |
| 14 | GRC 600×400 min-size workaround | CLAIM-OK | `sw.set_size_request(1, 1)` in `_setup_drawing_area` (`native_canvas.py:431–432`) |

---

## 3. REFUTED / DRIFTED CLAIMS

**D1 — "`_compute_ranks` is still used to feed `compute_full_layout`" (AGENTS.md Key Conventions, layout paragraph): DRIFTED.**
`compute_full_layout` (`adapter/layout.py:280`) only consumes the passed-in `model` or builds it via `_compute_layout_model` (`layout.py:285–287, 147–192`). `_compute_ranks` (`layout.py:193–202`) is now a thin wrapper whose own docstring says "for callers that only need the column assignment (tests)". Grep: production callers = none; only `tests/test_layout.py:36, 605, 648` call it. The claim was true for an earlier architecture; today `_compute_ranks` is test-only dead code in `src/` (see §4/§5).

**D2 — AGENTS.md "No Browse button" phrasing**: the sidebar **does** have a "Browse" button — but for the *project directory* (`chat_sidebar.py:821`, `_on_browse_clicked`), while *active-graph* selection is auto-detected from the notebook. The doc's "Auto-detects the active graph from GRC's notebook — no Browse button" refers to graph selection only. Borderline phrasing, not a functional drift.

No other tested claim failed. Everything else in the 14-row table above is CLAIM-OK, including subtle ones: `_fit_to_view` `FIT_PAD=1.1` and clamp to GRC's 0.1–5.0 (`native_canvas.py:29–38, 493–535`), "expand every tool result classified incl. `read_file`/`web_fetch`" (`agent.py:513–519`), the `PromptInjectionDefender` measured-cost comment (`agent.py:519–523`), and the poll's "undo-then-different-edit" tuple-collision rationale (`native_canvas.py:25–38`).

---

## 4. REDUNDANCY & LEAN AUDIT (Part 2 + Part 3)

### 4.1 Custom logic duplicating pydantic-ai / harness 0.23 (Part 2 sweep)

**None of the "hand-rolled core" patterns exists** — verified by grep across all of `src/`: no manual retry loops (`while True`+sleep), no manual subprocess streaming (harness `ShellToolset` owns it; the agent runs via GRC's native Execute), no custom message-persistence serializer (`db.py` uses `ModelMessagesTypeAdapter.dump_json/validate_json`), no hand-written agent loop (uses `agent.iter()` + capabilities), no reimplemented ddgs wrapper (`adapter/search.py` deleted — absent from `src/grc_agent/adapter/`). The `AsyncTenacityTransport` + `retries=` and `StopGracefully` capability (`agent.py:406–444`) are the sanctioned patterns.

Remaining duplication is *internal to the app*, not vs. the framework:

| # | Location | What duplicates | Framework-native replacement | Severity |
|---|----------|-----------------|------------------------------|----------|
| R1 | `agent_factory.py:443–451` (`_OPENAI_SHAPED_PROVIDERS`) vs `:906–933` (`_PREFLIGHT_ENDPOINTS`) vs `:384–405` (`_google_context_length` URL built by hand) | Same provider→endpoint+key table for groq/mistral/cohere/xai/google written **three times**; the context probes could consume `_PREFLIGHT_ENDPOINTS` | none (probes are app-specific, model_catalog only lists ids) — but the table should be the one `_PREFLIGHT_ENDPOINTS` | safe refactor |
| R2 | `agent_factory.py:293–297` (`_build_model` openai_compatible) vs `:975–987` (`_preflight_target`) vs `:449–453` (`_openai_shaped_context_length`) | The "strip `/`, append `/v1` (or `/models`)" base-URL normalization triplicated | none | safe refactor |
| R3 | `settings.get_project_dir`/`set_project_dir` (`settings.py:324–339`) vs `ChatSidebar._build_project_bar` inline `get_env_value("GRC_PROJECT_DIR")` (`chat_sidebar.py:824`) and `set_project_directory` inline `upsert_env_key("GRC_PROJECT_DIR", …)` (`chat_sidebar.py:860–876`) | The project-dir env persistence logic exists both as library functions and as inline copies; the inline copies win (the library functions have no src callers) | none | safe to remove one side (recommend deleting the settings functions; they are exercised only by `test_chat_sidebar.py:2741–2752`) |
| R4 | `agent_factory.py:252, 258, 270, 719` and `chat_sidebar.py:2931` | `get_env_value(X) or os.environ.get(X)` repeated 5× | `settings` could offer one `resolve_key(name)` | cosmetic |
| R5 | `chat_sidebar.py:378–398` (`_clean_message_history_for_new_turn`) + `:410–430` (`_without_truncated_thinking_tail`) | Manual history trimming | pydantic-ai **raises** `UserError("Cannot provide a new user prompt when the message history contains unprocessed tool calls.")` and `UnexpectedModelBehavior` for length-only-reasoning tails — verified no trimming utility exists in `pydantic_ai.messages` 2.31.0. **Keep**; this is the sanctioned-exception territory, and both paths archive before deleting | keep (justified) |
| R6 | `chat_sidebar.py:567–615` (`_collect_token_usage` + overrides) | Extracts usage from message history because pydantic-ai exposes no per-turn aggregate accessor | none in 2.31.0 (`run.usage` is the only aggregate; the label needs per-turn breakdown for history) | keep |
| R7 | `adapter/snapshots.py` push-cursor discipline (`_read_undo_cursor`/`_write_undo_cursor`/`_prune_undo_stack`) | — | GRC's native StateCache owns interactive undo; this stack is intentionally append-only for durability (module docstring `snapshots.py:24–34`) | keep (documented deliberate) |

### 4.2 Dead code (Part 3)

- **`ui/approval_card.py:243–244` `get_tool_call_id`** — zero callers in `src/` and `tests/` (only a `.pyc` byte match). Safe to delete.
- **`settings.py:324–339` — `get_project_dir`/`set_project_dir`** — no callers in `src/` (grep). Test-only (`test_chat_sidebar.py:2741–2752`). Safe to delete after re-pointing the test, or keep as the single implementation and make the sidebar use it (R3).
- **`fs_tools.py:96–98` — `set_project_dir_provider`** — no production callers; only `tests/test_shell_toolset.py:78, 84`. Dead in the shipped app.
- **`native_canvas.py:89–90` — `NativeFlowgraphProxy.get_state_lock`** — zero callers anywhere (AGENTS.md's own "`_with_state_lock` is gone" note; the no-op method outlived its caller). Safe to delete per "simplify by removal".
- **`adapter/layout.py:193–202` — `_compute_ranks`** — test-only (D1). If kept as a test helper it should move to `tests/` or be deleted with its 3 test call sites.
- **Not dead (verified):** `fs_tools.write_file/edit_file/get_toolset`, `shell_tools.get_toolset` — `get_toolset` is invoked by the harness capability machinery (`capability_creation/_validate.py:108`, toolset assembly); the `@_recoverable`-wrapped tools are registered by `FunctionToolset`; `StopGracefully.for_run/wrap_node_run` and `ModelRequestLogger.before_model_request` are capability lifecycle hooks; `exec_monitor.handle_message` is wired via `register_execution_messenger` (`desktop_app.py:251`).
- **Legacy-name sweep:** no `OLLAMA_THINKING_ENABLED`, no `canvas_synced` field, no `_with_state_lock`, no CLI/`__main__` entry (agent.py's final comment confirms the deleted pydantic-graph runner), no `to_jsonable_python` remnants (only docstring history).
- `ruff check --select F401,F841,F811` → clean.
- 445/445 hermetic tests pass — no test-only-imported symbols remain in `src/` beyond the four listed above.

---

## 5. SMALL LOST DETAILS

- **`_compute_ranks` doc drift (D1)** — the AGENTS.md text will mislead a future editor into thinking layout depends on it. One-line doc fix or deletion.
- **`settings.get_project_dir`/`set_project_dir` shadow the sidebar's behavior**: the sidebar's `_build_project_bar` falls back to `Path.cwd()` when `GRC_PROJECT_DIR` is unset (`chat_sidebar.py:826–828`), while `settings.get_project_dir` returns `None` — two different "unset" semantics for the same setting, only one of which is live. Not a bug (fs_tools resolves the fallback itself), but the drift window is real.
- **`get_state_lock` on the proxy still exists despite "no-op wrappers are gone"** — it is exactly the no-op wrapper the doc claims was deleted; it just happens to live on the proxy instead of a module. No caller, no harm, but it is the kind of leftover the engineering rules ask to remove.
- **Poll's `_last_state_cache_version` is never re-armed on manual sync** — after `sync_manual_edit` writes, `_check_for_unsynced_edit` updates `_last_state_cache_version = version` on the next full tick (`native_canvas.py:671–672`); harmless, but a full hash run happens once per manual edit. Not a behavior gap (backstop covers it).
- **AGENTS.md "12 concrete providers remain available"** — `_VALID_PROVIDERS` indeed holds 12 (`settings.py:11–24`) — CLAIM-OK.

---

## 6. UNVERIFIED

- **Live GNU Radio behaviors** (epy_block port-replacement, `Port.rewrite()` rekeying, GRC console markers, native Execute/KILL enablement) — the hermetic tests assert these against `gnuradio.grc.core` objects, and all pass, but I did not run a live GUI session or the integration suites (explicitly out of scope; `tests/test_integration.py` / `test_button_integration.py` untouched).
- **The `gi.events` (PyGObject ≥ 3.50) loop path** — this machine runs PyGObject 3.48.2 so only the `gbulb` path executes here; the `gi.events` branch (`event_loop.py:46–52`) is code-read but not exercised.
- **4 Hz expanded-thinking visual cadence + auto-scroll stickiness** under xvfb — code-verified (`chat_sidebar.py:2161–2198`: `interval = 0.25` for thinking-only, collapsed-thinking skips GTK layout), not visually confirmed.
- **Codex end-to-end (auth/refresh/catalog)** — `CLIENT_VERSION=9999.0.0` behavior and `openai_reasoning_summary` claim rely on the docstring's live findings; no Codex account was available to re-prove.
- **stackone-defender tier-1 nested-key miss** ("strings nested under other keys, e.g. `inspect_graph` JSON `params.value`, are not classified") — the harness docstring confirms the tier-1 scan surface, but I did not re-run the live detection probe.
- **Sidebar copy-action and block-badge hover behavior** — covered by test_chat_sidebar (passed), not visually confirmed.

---

## 7. RECOMMENDATIONS (ordered by impact)

1. **Fix the `_compute_ranks` doc drift — low effort, removes a documented lie.**
   `AGENTS.md` (Key Conventions, layout paragraph) + `adapter/layout.py:193–202` docstring.
   ```text
   - AGENTS.md: replace "_compute_ranks is still used to feed compute_full_layout"
     with "_compute_ranks is a test-only thin wrapper over _compute_layout_model".
   - layout.py:193: delete _compute_ranks and re-point tests/test_layout.py:36,605,648
     at _compute_layout_model(...).ranks (or keep if the tests read better with it —
     then the doc fix alone suffices).
   ```

2. **Delete `NativeFlowProxy.get_state_lock` (`native_canvas.py:89–90`)** and drop the "get_state_lock() returns None" clause from AGENTS.md. Zero callers; it is the very no-op-wrapper pattern AGENTS.md says was removed.

3. **Delete `ui/approval_card.py:243–244 get_tool_call_id`** (zero callers). Minimal diff: remove the two lines.

4. **Resolve the `settings.get_project_dir`/`set_project_dir` dead-API (R3).** Either:
   - **(a) Delete** `settings.py:324–339` and re-point `test_chat_sidebar.py:2741–2752` to the sidebar's own read (`get_env_value("GRC_PROJECT_DIR")`), or
   - (b) make `chat_sidebar.py:824, 868, 875` call `settings.get_project_dir`/`set_project_dir` — but note (a) is smaller and matches "simplify by removal".

5. **Delete or fold `fs_tools.set_project_dir_provider` (`fs_tools.py:96–98`)** — test-only; either move to `tests/conftest.py` or delete with the two `test_shell_toolset.py` call sites switching to `set_active_graph_providers`.

6. **Unify the provider→endpoint table (R1/R2)** in `agent_factory.py`: `_google_context_length` (`:384–405`) should use `_PREFLIGHT_ENDPOINTS["google"](key)` (already returns exactly `(url, headers)`), and `_openai_shaped_context_length` should iterate `_PREFLIGHT_ENDPOINTS` instead of its own `_OPENAI_SHAPED_PROVIDERS` dict — the URLs at `:447–450` are byte-identical to `:920–932`. Diff sketch:
   ```python
   # _openai_shaped_context_length
   for provider, mk in _PREFLIGHT_ENDPOINTS.items():
       url, headers = mk(get_env_value(_PREFLIGHT_KEY[provider]) or "")
       ...
   ```
   Net removal: one duplicated table (12 lines) + one hand-written URL (`:394`).

7. **Optional: collapse the `get_env_value(X) or os.environ.get(X)` idiom** into one helper in `settings.py` (`resolve_key(name, default=None)`) and update the 5 call sites. Pure dedup, zero behavior change; low priority.

Severity key: items 1–5 are **safe to remove** (dead/vestigial); 6–7 are **safe refactors** (no behavior change). Nothing found in Part 2 rose to "risky to remove" — no live duplication of harness functionality was found, which is the strong side of this codebase.
