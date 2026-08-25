# Verification Round V-A — audit-a-lost-details-lean.md

Verifier: independent re-check of `docs/investigation/audit-a-lost-details-lean.md` (checkout `85f938d`). Every claim below was re-derived with my own commands; the audit's citations were re-read at the cited lines. No file modified (report excepted); no live-LLM suite run (`test_integration.py`, `test_button_integration.py` untouched).

**Command evidence baseline:**
- `uv run pytest tests/test_adapter_graph.py -q` → **33 passed** in 1.87s
- `uv run pytest tests/test_exec_monitor.py -q` → **34 passed** in 0.69s
- `xvfb-run -a uv run pytest tests/test_native_canvas.py -q` → **12 passed** in 0.11s (display required)
- `uv run ruff check --select F401,F841,F811,F823 src/ tests/` → **All checks passed!** (superset of the audit's `F401,F841,F811 src/` run: adds F823 and the tests/ tree)
- Greps below exclude `.pyc` matches (stale bytecode from deleted `test_unit.py` and prior refactors; not source call sites).

---

## 1. Executive summary

- **All four Part-1 spot-check groups are CONFIRMED** with the audit's substance intact; a handful of audit line citations are a few lines off (noted per item: `_state_cache_version` at native_canvas.py:677 not 509; Stage A at 521–528 not 519–524; resolve_auto's "deliberately not a candidate" comment at graph.py:474 not 418–438). All three hermetic clusters pass (79 tests).
- **All five dead-code items are CONFIRMED-DEAD**, with one refinement: `settings.set_project_dir` has **zero callers anywhere, including tests** (the audit's "exercised only by test_chat_sidebar.py:2741–2752" is true only for `get_project_dir`); `fs_tools.set_project_dir_provider`, `_compute_ranks`, and `get_state_lock` remain test-only/zero-caller as claimed. `ruff` with the broader `F823` + `tests/` scope is still clean; no TODO/FIXME markers; no legacy names (`OLLAMA_THINKING_ENABLED`, `_with_state_lock`, `_find_block_placement`, UI undo/redo methods all absent — only a docstring/history mention of `to_jsonable_python` at db.py:357 and a comment mention of `canvas_synced` at agent.py:661).
- **Endpoint-table triplication CONFIRMED** with one amendment: the URL data is duplicated in exactly 3 places (`_PREFLIGHT_ENDPOINTS`, `_OPENAI_SHAPED_PROVIDERS`, hand-built Google URL in `_google_context_length`), but the `/v1`-suffix normalization appears at **4** code sites (agent_factory.py:288, 308, 475, 963–967), not 3; `_CTX_PROBES` (:501–507) is a probe-*function* table and duplicates no URL data. The settings.py project-dir dead pair is CONFIRMED — the sidebar never calls `settings.get_project_dir`/`set_project_dir`; it re-implements the `GRC_PROJECT_DIR` read (chat_sidebar.py:824–828) and upsert (868, 875) inline.
- **Both AGENTS.md drift claims are CONFIRMED-DRIFTED**: (a) `_compute_ranks` is test-only (AGENTS.md:131 claim false; layout.py:193–202 docstring literally says "for callers that only need the column assignment (tests)", `compute_full_layout` builds via `_compute_layout_model` at layout.py:300); (b) the Mode-toggle "only way to approve everything" claim (AGENTS.md:174) is false — `_always_approve_all` persists `GRC_AGENT_APPROVE_CHANGES=always` and `_request_approvals` then auto-approves **all** approvals including shell `run_command`/`start_command` (chat_sidebar.py:3123–3125).

---

## 2. VERIFIED FACTS (Part 1 spot-checks)

### (1) resolve_auto + Phase-5 rewrite gate + connection_silently_dropped — CONFIRMED

| Audit claim | My evidence |
|---|---|
| `resolve_auto` at graph.py:357 | `def resolve_auto(` at `src/grc_agent/adapter/graph.py:357` ✓ |
| `_canonical_dtype` from `Constants.ALIASES_OF` (graph.py:329–345) | `_DTYPE_CANON_CACHE` built from `Constants.ALIASES_OF` at graph.py:329–345; docstring "not a hand-maintained alias table" ✓ |
| Refuses to resolve from another `"auto"` neighbor | `elif not (new_block_names and other in new_block_names):` at graph.py:464; comment at 474 "Deliberately not treated as a candidate at all in that case"; audit's citation 418–438 is the connection-scan region — the refusal comment itself is at 474 (minor line drift) |
| Raises `ValueError` when nothing explicit exists | graph.py:488–492: `raise ValueError(f"Cannot auto-resolve param {param_key!r} ...")` ✓ |
| Pre-Phase-3 snapshot at graph.py:989 | `connections_before_rewrites = set(flow_graph.connections)` at **graph.py:989**, immediately followed by `# Phase 3: add_blocks` at 991 — i.e., after the caller's deliberate Phase-1/2 removals, before any rewrite. Comment at 984–988 states exactly the audit's "pre-phase-3" scope ✓ |
| Phase-5 rewrite gated on `add_blocks` only | **graph.py:1193–1194** `if add_blocks: flow_graph.rewrite()`; Phase 6 at 1196, Phase 7 at 1211, unconditional final rewrite at **graph.py:1250** ✓ |
| Object-identity set compare at 1274–1298 | `expected_connections = connections_before_rewrites \| {c for _, c in made_connections}` at **graph.py:1274**; `dropped = expected - actual` at 1276; error `"connection_silently_dropped"` at **graph.py:1288**; the "checked unconditionally, not just under force" comment at 1254–1273 ✓ |
| `ModelRetry` suggests `force=True` only for `validation_failed` | `agent.py:644–652` — verified (force suggestion gated on `error_type == "validation_failed"`) ✓ |
| Tests | `test_change_graph_same_call_port_dtype_change_and_connect_rolls_back` at test_adapter_graph.py:447 (audit said 449 — def line 447); `test_change_graph_update_params_only_batch_catches_dropped_preexisting_connection` at :537 (audit said 546); `auto_resolve_failed` asserts at :337 and :357. Cluster: **33 passed** ✓ |

### (2) keep_param Stage A/B — CONFIRMED

- `def keep_param(` at **graph.py:509**. Stage A at **graph.py:521–525** (audit said 519–524; the prelude `hide = getattr(...)` sits at 518–520): `dtype == "id" or param_key == "showports" or param_key.startswith("bus_structure_")` (:521), `hide == "all"` (:523), `dtype == "gui_hint"` (:525) — exactly AGENTS.md's five drops.
- Overview-only gate at :528 (`if mode != "overview": return True`).
- Stage B: `hide == "none"` keep (:532); type-controlling / `generate_options` structural (:535–539); `hide == "part"` non-structural enums dropped unless custom or variable-referencing (:541–547); `dtype == "enum"` kept iff non-default or structural (:549–550); default-valued kept only on variable reference (:552–555). Matches the audit's transcription verbatim.

### (3) 1.5s safety-net poll + state-cache gate + backstop — CONFIRMED (citation amended)

- `_POLL_FULL_CHECK_EVERY = 10  # ~15s at the 1.5s poll interval` at **native_canvas.py:39**; rationale comment block :25–39 (incl. the undo-then-different-edit tuple-collision case).
- `GLib.timeout_add(1500, self._check_for_unsynced_edit)` at **native_canvas.py:650**.
- `_check_for_unsynced_edit` at **native_canvas.py:773**; path-change re-baseline at :784–785 (`page.file_path != self._baseline_path → _sync_page_baselines()`); version gate at :786–790 (`state_cache_unchanged` and not `due_for_backstop` → skip full hash); backstop at :790; full-hash fallthrough and `self._last_state_cache_version = version` re-arm at :793–806.
- `_state_cache_version` def at **native_canvas.py:677–685** returning `(sc.current_state_index, sc.num_prev_states, sc.num_next_states)` — the audit cited it at 509–517, which is actually inside `_fit_to_view`'s scroll-adjustment block; **citation drift, substance correct**.
- Baselines: `_sync_page_baselines` at :688–700 sets `last_synced_export_hash`, `last_disk_hash` (`_sha256_file`), `_baseline_path`, `_last_state_cache_version` ✓.
- Lock protocol: `sync_manual_edit` at :397, `fcntl.flock(..., LOCK_EX | LOCK_NB)` at :419, `BlockingIOError` defer at :420, atomic write + `push_undo_snapshot(fg, Path(self.path))` at :448 ✓.

### (4) exec_monitor run_in_progress / agent_initiated — CONFIRMED

- `get_last_run_log` at exec_monitor.py:92; `"run_in_progress": self._tracking` at **:116**, `in_progress_note` at :119, `log_truncated`/`truncation_note` at :124–128 (audit's :113–126 ≈ correct).
- `mark_run_agent_initiated` at :138, `self._agent_initiated = True` at :148; consumed at both terminal markers (:216 and :228 set it back to False); `_fail` suppresses the callback while set (:246–248, `if self._agent_initiated: ... return`).
- `wait_for_run_end` returns `"completed"` (:162, :169), `"still_running"` (:164), `"not_started"` (:170) ✓.
- `_SIGTERM_RETURN_CODE = -15` at :33; the carve-out gate at :212 (`if code != _SIGTERM_RETURN_CODE and (code != 0 or self._has_runtime_error): self._fail(code) else: self._reset()`); `_MAX_LOG_BYTES = 512 * 1024` at :38; byte-counted oldest-chunk eviction in `_append` at :231–237; `_last_run_evicted` frozen at Done at :205/:226 ✓.

### Test-cluster pass counts

| Cluster | Result |
|---|---|
| tests/test_adapter_graph.py | **33 passed** (2.03s) |
| tests/test_exec_monitor.py | **34 passed** (0.69s) |
| tests/test_native_canvas.py (xvfb-run) | **12 passed** (0.11s) |
| ruff F401,F841,F811,F823 src/ tests/ | clean |

---

## 3. REFUTED / DRIFTED CLAIMS

**D1 — AGENTS.md:131 "`_compute_ranks` is still used to feed `compute_full_layout`": CONFIRMED-DRIFTED.**
`_compute_ranks` (adapter/layout.py:193–202) is a thin wrapper whose own docstring says "thin wrapper over `_compute_layout_model` for callers that only need the column assignment (**tests**)". `compute_full_layout` (layout.py:280) never calls it: when `model is None` it builds via `_compute_layout_model` at layout.py:300; otherwise it reuses the passed-in model (docstring at 286–290: "`model`, if provided, is reused as-is…"). Production callers of `_compute_ranks`: none (grep: only the def, the re-export at adapter/__init__.py:29/57, and tests/test_layout.py:36, 605, 648).

**D2 — AGENTS.md:174 "flipping the composer Mode toggle to Auto remains the only way to approve everything": CONFIRMED-DRIFTED** (audit-c's claim, re-traced end-to-end):
1. `_request_approvals` (chat_sidebar.py:3107) collects **all** approvals — `approvals = [c for c in output.approvals]` (:3109), which per its own docstring includes "change_graph, run_flowgraph, the shell exec tools".
2. :3123–3125: `if get_approval_mode() != "ask": return DeferredToolResults(approvals={c.tool_call_id: ToolApproved() for c in approvals})` — in `always` mode every pending approval, **including `run_command`/`start_command`**, is auto-approved with no UI.
3. `_always_approve_all` (:3238–3250) — the "Always accept" handler on **non-shell cards** (shell cards get `_always_allow_command` instead, :3139–3148) — calls `set_approval_mode("always")` (:3241) → settings.py:306–310 `upsert_env_key("GRC_AGENT_APPROVE_CHANGES", mode)`.
4. `get_approval_mode()` (settings.py:296–304) reads that persisted value.
Conclusion: one click on a change_graph/run_flowgraph card's "Always accept" flips the global gate, and every later turn auto-approves shell commands — exactly what the shell prefix-allow design (session-scoped, deliberately avoiding the persisted gate-off) was meant to prevent. The audit's drift finding stands.

No other Part-1/Part-4 claim was refuted; all 4 spot-checked groups and the audit's remaining table rows (canvas_synced absence, unwired-run do-not-retry, planner fail-closed allowlist, PromptInjectionDefender limits, model_catalog single parser, Codex client_version/stream/auth/refresh, `_with_state_lock` gone, `get_run_log` run_in_progress, `Planning` guidance, `retries=`/AsyncTenacityTransport, GRC 600×400 workaround) match the code I re-read.

---

## 4. REDUNDANCY & LEAN AUDIT

### 4.1 Dead-code inventory — all 5 items, all call sites grepped (src/ AND tests/)

| Item | Def | Call sites | Verdict |
|---|---|---|---|
| `settings.get_project_dir` | settings.py:324–330 | tests/test_chat_sidebar.py:2741 (import), :2752 (assert) only | **CONFIRMED-DEAD** (production); test-only |
| `settings.set_project_dir` | settings.py:333–339 | **none anywhere** (grep src/ + tests/) | **CONFIRMED-DEAD** — even the audit's "test-only" claim overstates it: the test calls `sidebar.set_project_directory`, never `settings.set_project_dir` |
| `native_canvas.NativeFlowgraphProxy.get_state_lock` | native_canvas.py:89–90 | none anywhere (only a stale `.pyc`) | **CONFIRMED-DEAD** (zero callers) |
| `fs_tools.set_project_dir_provider` | fs_tools.py:96–98 | tests/test_shell_toolset.py:78, 84 only | **CONFIRMED-DEAD** (production); test-only |
| `adapter.layout._compute_ranks` | layout.py:193–202 | adapter/__init__.py:29, 57 (public re-export) + tests/test_layout.py:36, 605, 648 only | **CONFIRMED-DEAD** (production); test-only (see D1) |
| `approval_card.get_tool_call_id` | ui/approval_card.py:243–244 | none anywhere (src/ and tests/) | **CONFIRMED-DEAD** (zero callers) — the sidebar's approval resolution uses `call.tool_call_id` attributes and `_resolve_approval`, never this accessor |

Additional checks the brief asked for:
- `ruff check --select F401,F841,F811,F823 src/ tests/` → clean (audit ran only `F401,F841,F811 src/`; the wider scope adds nothing).
- TODO/FIXME/XXX/HACK in src/: **none**.
- Legacy-name sweep: `OLLAMA_THINKING_ENABLED` absent; `canvas_synced` only as a comment (agent.py:661); `_with_state_lock` absent; `to_jsonable_python` only in a docstring (db.py:357); `_find_block_placement` absent; no `def undo`/`def redo` anywhere in src/.

### 4.2 Endpoint-table triplication (brief item 3) — CONFIRMED, one amendment

Read all four structures in `agent_factory.py`:

- **`_PREFLIGHT_ENDPOINTS`** (:910–933): provider → `lambda k: (url, headers)` for anthropic/google/groq/mistral/cohere/xai. Google row at :917–918: `f"https://generativelanguage.googleapis.com/v1beta/models?key={k}"`.
- **`_OPENAI_SHAPED_PROVIDERS`** (:444–451): provider → `(url, key_var)` for openrouter/openai/groq/mistral/cohere/xai. The groq/mistral/cohere/xai URLs are **byte-identical** to `_PREFLIGHT_ENDPOINTS` (`https://api.groq.com/openai/v1/models`, `https://api.mistral.ai/v1/models`, `https://api.cohere.com/v1/models`, `https://api.x.ai/v1/models`). The key-var data (`GROQ_API_KEY` etc.) is not in `_PREFLIGHT_ENDPOINTS` (which takes the key as an argument instead) — so one consolidated table must keep both the key-var name and the URL.
- **`_google_context_length`** (:384–405): hand-builds the same Google URL at :394 (`url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"`) — byte-identical to `_PREFLIGHT_ENDPOINTS["google"]`'s output.
- **`_CTX_PROBES`** (:501–507): maps providers to probe *callables* — **no URL duplication** (the URLs live inside the probe functions). It is the dispatch table, not a data duplicate. The brief's premise that `_CTX_PROBES` participates in the triplication is not supported: it duplicates nothing.

**Base-URL `/v1`-suffix normalization is written at FOUR sites** (audit said three, citing :293–297/:975–987/:449–453 — actual lines):
1. `_build_model` openai_compatible branch — agent_factory.py:288 (`base_url = raw_url if raw_url.endswith("/v1") else f"{raw_url}/v1"`, raw_url at 283–287)
2. `_build_model` ollama_local branch — agent_factory.py:308 (same idiom, raw_url at 303–307)
3. `_openai_shaped_context_length` openai_compatible branch — agent_factory.py:475 (`url = base if base.endswith("/models") else f"{base}/models"`, base at 473–474)
4. `_preflight_target` openrouter/openai branch — agent_factory.py:963–967 (the 3-way `models_url` construction)

### 4.3 settings.py project-dir dead pair — CONFIRMED (sidebar persistence path traced)

- Sidebar reads: `_build_project_bar` at chat_sidebar.py:824–829 — `saved_dir = get_env_value("GRC_PROJECT_DIR")`, fallback `Path.cwd().resolve()` at :828. This is the *live* read; `settings.get_project_dir` (which returns `None` on unset, no cwd fallback) is never consulted.
- Sidebar writes: `set_project_directory` at chat_sidebar.py:860–876 — `upsert_env_key("GRC_PROJECT_DIR", str(p))` at :868 / `upsert_env_key("GRC_PROJECT_DIR", "")` at :875. Same upsert as `settings.set_project_dir` (:336/:339) — the two implementations are byte-equivalent in effect; only the sidebar's are reached.
- `desktop_app.py:268, 275` use `sidebar.get_project_directory()` (the sidebar method, chat_sidebar.py:856–858) for fs_tools providers and the save-folder provider — never `settings.get_project_dir`.
- The test `test_chat_sidebar.py:2741–2752` (`test_project_directory_selector`) exercises the sidebar path and asserts `get_project_dir() == proj_dir` merely as an equivalence check.

So the audit's R3 is exactly right: the settings pair is production-dead duplication of the sidebar's inline logic; the sidebar's own fallback semantics (`Path.cwd()`) differ from `settings.get_project_dir`'s (`None`), confirming the §5 "two different unset semantics" note.

---

## 5. SMALL LOST DETAILS

- **Citation drift in the audit itself (no functional impact):** `_state_cache_version` is at native_canvas.py:677–685, not 509–517 (that range is `_fit_to_view` scroll code); keep_param Stage A is 521–525, not 519–524; the "Deliberately not treated as a candidate" comment is graph.py:474, not418–438; `/v1`-suffix normalization is 4 sites, not 3 (agent_factory.py:288, 308, 475, 963–967).
- `settings.set_project_dir` is even deader than the audit reported: zero callers in tests too — the removal diff should delete it outright without test churn, while `get_project_dir`'s removal needs the one-line test re-point at test_chat_sidebar.py:2752.
- `adapter/__init__.py:29, 57` re-exports `_compute_ranks` as public API — any removal must also drop the `__init__` import and `__all__` entry (the audit's removal sketch missed this).
- AGENTS.md:131's `_compute_ranks` sentence and AGENTS.md:174's "Mode toggle… only way" sentence are both actively misleading today (D1/D2).

---

## 6. UNVERIFIED

- Live GNU Radio/GUI behaviors (epy port replacement, GRC markers, native Execute enablement) — same scope exclusion as the audit; the hermetic suites assert against `gnuradio.grc.core` objects and pass.
- `gi.events` event-loop path (this machine is PyGObject 3.48.2 → gbulb path only).
- Codex end-to-end (auth/refresh/catalog); no Codex account available.
- stackone-defender tier-1 nested-key behavior — harness docstring read only, not re-probed.
- The audit's full 445-test pass was not re-run (only the three clusters covering the spot-checked features, plus ruff) — cluster results supersede for the scope of this round.

---

## 7. RECOMMENDATIONS (ordered by impact, grounded in the verified removals)

1. **Fix the two AGENTS.md lies (D1/D2)** — both are documented claims that a future editor will trust. `AGENTS.md:131` and `AGENTS.md:174`.
   ```text
   AGENTS.md:131: replace "_compute_ranks is still used to feed compute_full_layout"
   with "_compute_ranks is a test-only thin wrapper over _compute_layout_model
   (production layout uses _compute_layout_model / the passed-in model)".
   AGENTS.md:174: replace "flipping the composer Mode toggle to Auto remains the only
   way to approve everything" with "the global gate can also be flipped by any
   non-shell ApprovalCard's 'Always accept' (persists GRC_AGENT_APPROVE_CHANGES=always,
   which auto-approves shell approvals on later turns); only the session-scoped
   shell prefix-allow avoids the persisted gate."
   ```

2. **Delete `get_state_lock`** (`native_canvas.py:89–90`) and the `get_state_lock()` clause in AGENTS.md's "The active flowgraph is resolved dynamically" paragraph. Zero callers.
   ```text
   native_canvas.py:89-90: remove
       def get_state_lock(self) -> None:
           return None
   ```

3. **Delete `approval_card.get_tool_call_id`** (`ui/approval_card.py:243–244`). Zero callers in src/ and tests/ (verified §4.1). Keep `_call` private.
   ```text
   ui/approval_card.py:243-244: remove the two-line method.
   ```

4. **Delete `settings.get_project_dir`/`set_project_dir`** (`settings.py:324–339`); re-point the single test assertion.
   ```text
   settings.py:324-339: delete both functions.
   tests/test_chat_sidebar.py:2752: change `assert get_project_dir() == proj_dir` to
   `assert get_env_value("GRC_PROJECT_DIR") == str(proj_dir)` (and drop the
   import at :2741). Note: set_project_dir has no test caller, so only the
   read side needs re-pointing.
   ```

5. **Delete or fold `fs_tools.set_project_dir_provider`** (`fs_tools.py:96–98`) — test-only; the tests can call `fs_tools.set_active_graph_providers` with only the project-dir argument (its docstring at :85–90 shows the project_dir_fn parameter is already optional there), or move the helper to tests/conftest.py.
   ```text
   tests/test_shell_toolset.py:78, 84: replace
     fs_tools.set_project_dir_provider(lambda: other)
   with
     fs_tools.set_active_graph_providers(None, None, lambda: other)
   (verify the None-safe signature first) — or delete the helper entirely.
   ```

6. **Delete or re-home `_compute_ranks`** (`adapter/layout.py:193–202` + `adapter/__init__.py:29, 57`) — test-only; re-point tests to `_compute_layout_model(...).ranks`.
   ```text
   layout.py:193-202: delete; adapter/__init__.py: remove the import at :29 and
   the __all__ entry at :57.
   tests/test_layout.py:36, 605, 648: `_compute_ranks(fg, set(), [])` →
   `_compute_layout_model(fg, set(), []).ranks` (import at :12 updated).
   ```

6. **Consolidate the endpoint tables (R1/R2)** — one table wins: extend `_PREFLIGHT_ENDPOINTS` to carry the key-var name, and delete `_OPENAI_SHAPED_PROVIDERS` + the hand-built Google URL. All call sites verified: `_preflight_target` (:978–981), `resolve_model_context_length` (:529), `_openai_shaped_context_length` (:467–468).
   ```python
   # agent_factory.py: replace _PREFLIGHT_ENDPOINTS (and _PREFLIGHT_LABELS) with
   _PREFLIGHT_ENDPOINTS = {
       # (provider) -> (url, headers, key_var) — callable form kept for the
       # key->headers needs; google's query-string URL moves here once.
       "google": (  "https://generativelanguage.googleapis.com/v1beta/models?key={key}",
           "GOOGLE_API_KEY"),
       ...
   }
   # _openai_shaped_context_length: iterate _PREFLIGHT_ENDPOINTS (drop the
   # _OPENAI_SHAPED_PROVIDERS dict entirely at :444–451), and:
   # _google_context_length: drop its hand-built `url = f"...?key={api_key}"` line
   # (:394) in favor of the shared builder.
   # The four /v1-suffix sites (:288, :308, :475, :963–967) can then share one
   # helper, e.g.:
   def _models_url(base: str) -> str:
       """append the OpenAI-shaped /models path once."""
       b = base.rstrip("/")
       if b.endswith("/models"):
           return b
       return f"{b}/models" if b.endswith("/v1") else f"{b}/v1/models"
   ```
   Net: one duplicated table (8 rows) + one hand-written URL removed; 4 suffix sites → 1 helper. Behavior-neutral.

7. **Optional (cosmetic):** collapse the `get_env_value(X) or os.environ.get(X)` idiom (agent_factory.py:252, 258, 270, 719; chat_sidebar.py:2931) into `settings.resolve_key(name)` — 5 call sites, zero behavior change.
