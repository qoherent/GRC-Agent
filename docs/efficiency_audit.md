# GRC Agent Efficiency Audit

**Performance/efficiency-focused audit.** A separate, earlier code-quality
audit (`docs/codebase_audit_report.md`, a snapshot from before commits
`417d214`/`f838ba2`/`f839a45`) has been removed: every finding in it was
re-verified against the current tree and is either fixed (with a passing
regression test) or was already documented, accepted debt independent of
that report (see the status table below for the full re-verification). This
document is scoped narrowly to *cost*: what runs, how often, and on which
thread — read-only investigation plus the highest-confidence fixes, applied
live.

---

## Fixed

### 1. The 1.5s canvas safety-net poll did a full flowgraph re-serialization on every tick, forever

**The single biggest always-on cost in the app.** `native_canvas.py`'s
`setup_signal_handlers` arms `GLib.timeout_add(1500, self._check_for_unsynced_edit)`
for the entire app lifetime, on the single gbulb-unified UI thread. Every
tick, unconditionally, it called `flow_graph_content_hash()` →
`_serialize_flow_graph()` (`adapter/graph.py`) — a full structural
`flow_graph.export_data()` walk, a full YAML dump, and a SHA-256 hash of the
*entire* live flowgraph — regardless of whether anything had changed.

Investigated whether GNU Radio Companion's own installed package already
exposes a cheap "did anything change" signal, to avoid reinventing
dirty-tracking:

- `page.saved` (GRC's own dirty flag, `gnuradio/grc/gui/Notebook.py`) — not
  usable here. This app never calls GRC's native `Actions.FLOW_GRAPH_SAVE`,
  so `page.saved` is monotonic in this app's usage (goes `False` on first
  edit, never resets), and it has three other live native consumers
  (title-bar asterisk, close-prompt, generate/exec auto-save gate) that
  writing to it would risk perturbing.
- `page.state_cache` (GRC's own undo/redo ring buffer,
  `gnuradio/grc/gui/StateCache.py`) — **usable**. Its three plain int
  attributes (`current_state_index`, `num_prev_states`, `num_next_states`)
  are updated by `Application._handle_action()` on every interactive edit
  path that doesn't already fire a trackable GTK signal on the canvas
  (properties-dialog OK/Apply, paste, align, rotate, delete, create,
  bussify, variable-editor edits, undo/redo) — confirmed by reading every
  relevant branch in GRC's `Application.py`. Purely read-only from this
  app's side, and a necessary condition for the flowgraph content to have
  changed via any currently-known GRC GUI edit path.

**Fix**: gate the expensive full-hash computation behind a cheap comparison
of `(current_state_index, num_prev_states, num_next_states)` first
(`native_canvas.py`: `NativeCanvasManager._state_cache_version`,
`_sync_page_baselines`, `_check_for_unsynced_edit`). When the tuple matches
the last-seen value, the tick returns immediately — no export, no YAML, no
hash.

**Measured win** (real `.grc` fixtures, real GNU Radio platform, 200
iterations per fixture, `time.perf_counter()`; full pipeline via
`flow_graph_content_hash()` vs. the cheap tuple read+compare):

| Fixture (blocks) | full hash (mean) | cheap check (mean) | speedup |
|---|---|---|---|
| empty (2) | 931 µs | 0.12 µs | ~8,000x |
| dial_tone (15) | 7,035 µs | 0.08 µs | ~87,000x |
| demo_qam (25) | 11,237 µs | 0.08 µs | ~146,000x |

The full-hash cost scales with flowgraph size (sub-millisecond to
double-digit milliseconds); the cheap check stays flat regardless of size.
Previously this ran, unconditionally, every 1.5 seconds for the app's entire
runtime.

**Adversarial verification found the cheap gate alone was not sound, and a
second fix was required.** A dedicated verification pass (see "Adversarial
findings" below) proved `state_cache`'s tuple is a *necessary but not
sufficient* signal: it can return to the exact value it had before an
ordinary "undo, then make a different edit" sequence, and three GRC actions
(block-library drag-and-drop add, double-click add, Variable Editor
add/remove) mutate the flowgraph without moving `state_cache` at all. Either
gap would have made the safety net miss a real edit indefinitely, not just
for one tick — a correctness regression, not merely a missed optimization.

**Final fix**: in addition to the cheap tuple gate, force the full check
unconditionally every `_POLL_FULL_CHECK_EVERY` ticks (10 ticks ≈ 15s)
regardless of what the tuple says (`_poll_tick_count`,
`_check_for_unsynced_edit`). This bounds both gaps' worst-case staleness to
~15 seconds instead of "forever, until an unrelated `state_cache` movement
happens to catch it" — while still skipping the expensive path on 9 out of
every 10 ticks, so the bulk of the measured win above is retained.

Covered by `test_check_for_unsynced_edit_skips_hash_when_state_cache_unchanged`
and `test_check_for_unsynced_edit_periodic_backstop_catches_undo_then_edit_collision`
(`tests/test_unit.py`).

### Adversarial findings (from a dedicated scientific verification pass)

A follow-up round of hypothesis-driven testing (real timing measurements,
exhaustive grep of every GRC GUI mutation path, direct `StateCache`
simulation) was run specifically to try to break the fix above before it
shipped. Two real risks were found and are both addressed by the periodic
backstop described above:

- **Undo-then-edit collision (high severity, trivially reproducible)**:
  algebraically, from any post-save state `(idx, prev, 0)`, undo produces
  `(idx-1, prev-1, 1)`, and any subsequent new edit produces `(idx, prev, 0)`
  again — identical to the pre-undo tuple, regardless of ring-buffer size or
  saturation. Verified directly against the installed `StateCache` class.
  This is an ordinary "Ctrl+Z, then edit something else" workflow, not an
  edge case.
- **`state_cache`-bypassing mutation paths (moderate severity)**: block
  drag-and-drop from the Block Library (`gnuradio/grc/gui/DrawingArea.py`,
  `_handle_drag_data_received`) and double-click-add /
  Variable-Editor-add-remove (`gnuradio/grc/gui/MainWindow.py`,
  `_add_block_to_current_flow_graph` / `_remove_block_from_current_flow_graph`)
  mutate the flowgraph directly, without going through
  `Application._handle_action()` and therefore without touching
  `state_cache` at all.
- A third theoretical risk — the 42-slot ring buffer (`STATE_CACHE_SIZE`)
  wrapping exactly back to a previously-seen tuple — was confirmed possible
  in principle but requires ~42 discrete interactive edits landing within a
  single 1.5s poll interval, which isn't realistically reachable by a human;
  negligible in practice (and also bounded by the same backstop regardless).

### 2. `query_catalog`/`query_docs` did one SQL round-trip per result (N+1)

Both functions ran a single vector/lexical similarity query, then looped
`SELECT ... WHERE rowid = ?` once per hit to fetch the chunk text. `N` is
bounded by `limit` (default 5), so the real-world impact was small, but it's
a free fix while these functions were already being touched for the lexical
fallback. Batched into a single `SELECT ... WHERE rowid IN (...)` per query,
re-ordered to match the original ranking (`adapter/rag.py`).

### 3. `_ensure_db_built` re-verified DB freshness on every single query, even on a fully warm cache

Every call to `query_catalog`/`query_docs` re-opened a connection and
re-ran 2–3 metadata queries (`sqlite_master` lookups, `_db_meta` reads) to
confirm the cached DB was still fresh — useful the first time, pure
overhead on every call after that within the same process. Added a
per-process `_FRESHNESS_CACHE` keyed by `(domain, db_path, model)`
(`adapter/rag.py`) that short-circuits `_build_db` entirely once a DB has
been verified fresh, invalidated implicitly whenever an actual rebuild
happens. No behavior change — same freshness guarantee, just not
re-verified redundantly within a process's lifetime.

### 4. (Side effect of the lexical-fallback feature) No more re-attempting a dead embedding backend on every query

Not a pre-existing bug, but worth calling out: naively "fall back to lexical
search when embedding fails" could have reintroduced a cost problem if a
lexical-only DB (built while the embedding backend was down) kept
re-triggering a full re-embed attempt on every subsequent query. `_build_db`
now treats "no vector index, but corpus unchanged" as a valid steady state,
not staleness — only a genuine corpus change gives embedding a fresh chance.
Covered by `test_lexical_only_db_does_not_rehammer_embedding_backend`
(`tests/test_isolation.py`).

### 5. Unbounded query length could stall the lexical fallback for tens of seconds

Found by adversarial fuzzing (see "Independent adversarial verification"
below): `_fts_query_string()` tokenized the raw query with no dedup and no
cap before building an `OR`-joined FTS5 `MATCH` expression. A ~100k-character
input produced an ~18,000-term expression whose evaluation cost scaled with
the *expression's own size*, not the corpus — measured at 8–46 seconds for a
single query, synchronously blocking the calling thread (`query_catalog`/
`query_docs` run via `asyncio.to_thread`, so this doesn't freeze the GTK
main thread, but it does hang that agent turn for the user). Vector search
was unaffected — only the lexical-fallback path.

Fixed by deduplicating (case-insensitive, order-preserving) and capping at
`_FTS_MAX_TOKENS = 32` tokens (`adapter/rag.py`) before building the MATCH
expression. Realistic natural-language queries are far under this cap; only
pathological input is clipped. Covered by
`test_fts_query_string_dedupes_and_caps_tokens` (`tests/test_unit.py`).

---

## Independent adversarial verification

Beyond the fixes above, three independent, hypothesis-driven verification
passes were run against the finished changes — one per area (RAG lexical
fallback, canvas-poll fix, full-suite regression/concurrency/fuzzing) — each
required to state a prediction, run a real experiment, and report
CONFIRMED/REFUTED/bug-found with evidence rather than inference.

- **RAG lexical fallback** (`ingest.py`/`rag.py`): 7 hypotheses tested —
  FTS5 query-string injection/edge-case safety, N+1 batching order
  preservation, partial-embedding-failure behavior, corpus-version-triggered
  re-embed, docs heading-only matches, migration-path rebuild-loop safety,
  empty-result handling. **All 7 confirmed correct, no bugs found** (the
  unbounded-query-length issue below was found by the separate full-suite
  fuzzing pass, not this one, since this pass used realistic-length queries).
- **Canvas-poll fix** (`native_canvas.py`): real timing measurements
  (931µs–11.2ms per full hash vs. ~0.08µs for the cheap check, scaling with
  flowgraph size) plus an exhaustive check of every GRC GUI mutation path.
  Found the undo-then-edit collision and the `state_cache`-bypassing
  mutation paths described under "Adversarial findings" above — both fixed
  via the periodic backstop.
- **Full-suite regression/concurrency/fuzzing**: repeated full-suite runs
  (93 tests, stable across 3 runs, no order-dependence), a 12-thread
  concurrent-build stress test (exactly-once builds, no deadlocks, correct
  cross-domain concurrency), and adversarial fuzzing of the public
  `query_catalog`/`query_docs` API (empty/null-byte/SQL-injection/100k-char/
  unicode inputs, fd-leak check over 50 repeated calls — zero leaks, zero
  crashes) — this pass is what surfaced the unbounded-query-length bug
  (fixed above) and two new mypy type errors in `ingest.py`'s
  `cur.lastrowid` (`int | None`) assignments, fixed with an explicit
  `assert cur.lastrowid is not None` (guaranteed true after a successful
  `INSERT`, so this documents the invariant rather than changing behavior).

---

## Investigated, deliberately left unchanged

### `chat_sidebar.py` — two remaining synchronous SQLite calls on the gbulb UI thread

Both were flagged as candidates for `asyncio.to_thread` offload (matching
`_save_history`'s already-correct pattern at `chat_sidebar.py`). Neither was
changed, for the following reasons:

- **`send_message`'s `save_session(None, path, ...)` call** (creates a new
  session row on a chat's first message). `test_send_message_guards_and_creates_session`
  (`tests/test_unit.py`) locks in a synchronous contract: `self._active_session_id`
  must be set immediately after `send_message()` returns, before the agent
  turn is even awaited — this is what lets the UI tag the tab
  (`page._grc_agent_session_id`) right away. Moving the call into
  `_run_agent_turn` (already async) would make session-id assignment
  observably asynchronous and break that tested contract for every caller
  of `send_message`, not just the one this audit is focused on. The actual
  cost is a single small `INSERT`, once per new chat session — not a hot
  path — so the risk of destabilizing a deliberately-tested behavior
  outweighs the gain. Left as-is.
- **`_render_welcome_screen`'s `get_recent_sessions()` call.** The sessions
  table is capped at 200 rows (`db.py`, already fixed from the prior
  code-quality audit's `DB-3`/`UI-4` findings), and `_render_history` — its
  only caller — has many synchronous call sites that expect the listbox
  populated immediately on return. Async-ifying this one read would need a
  render-skeleton-then-populate restructuring throughout `_render_history`'s
  callers for a read that's already small and bounded. Not worth the
  diff/risk for the expected gain. Left as-is.

---

## Status of the (now-removed) `docs/codebase_audit_report.md` findings

That audit predated commits `417d214`, `f838ba2`, and `f839a45`. Every
finding in it was re-verified directly against the current tree — not
assumed from commit messages — either by running its named regression test
(where one exists) or by reading the relevant code path. All 19 findings are
now either fixed (confirmed) or were already documented, accepted debt at
the time the report was written (unchanged since). The report itself has
been removed as fully superseded; this table is the durable record.

| Finding | Status |
|---|---|
| `UI-1` (error path wipes the user's message) | **Fixed** — confirmed via `test_run_agent_turn_error_preserves_user_message` (passing). |
| `UI-2` ("Clear History" doesn't delete) | **Fixed** — confirmed via `test_clear_history_deletes_active_session_real_db` (passing). |
| `UI-3` (a file's own session is unreachable) | **Fixed** — confirmed via `test_sync_to_file_restores_session_for_path` (passing). |
| `UI-4` / `DB-3` (unbounded `get_recent_sessions`, no eviction) | **Fixed** — `db.py` has a `LIMIT` and a 200-row prune (`_MAX_SESSIONS`, `_prune_in`). |
| `CANVAS-1` (blocking `flock`, no timeout, on the UI thread) | **Fixed** — `native_canvas.py` uses `LOCK_EX \| LOCK_NB`, defers to the next poll tick on contention instead of blocking. |
| `CANVAS-2` (dead `FlowgraphRunner`, double-spawn race) | **Resolved** — `runner.py` no longer exists in the tree. |
| `CANVAS-3` (silent poll-exception swallow) | **Fixed** — `_check_for_unsynced_edit`'s except branch logs via `_log.warning`, not a bare `pass`. |
| `CANVAS-4` (tab-switch baseline sync has no exception guard) | **Fixed** — `_sync_page_baselines` is wrapped in try/except, logs on failure. |
| `ADPT-1` (native undo/redo never actually disabled) | **Fixed** — confirmed via `test_disable_native_undo_redo_removed` (passing); resolved by deleting the disk-based undo/redo split entirely rather than wiring up the dead disable function — native `state_cache` is now the sole undo/redo path, consistent with the poll-efficiency work above. |
| `ADPT-2` (rollback doesn't cover the validation gate) | **Fixed** — confirmed via `test_change_graph_validation_gate_exception_rolls_back` (passing). |
| `ADPT-3` ("auto" dtype silently resets for standalone new blocks) | **Fixed** — confirmed via `test_change_graph_auto_standalone_new_block_fails_loudly` (passing). |
| `ADPT-4` (hand-rolled `dtype_map` reinvents `Constants.ALIASES_OF`, incorrectly) | **Fixed** — confirmed via `test_canonical_dtype_uses_native_aliases` (passing); now sourced from GNU Radio's own alias table. |
| `ADPT-5` (`prune_history` enforces an arbitrary context-limit) | **Fixed** — confirmed via `test_prune_history_removed` (passing); the fixed cutoff was removed rather than tied to a backend context window. |
| `ADPT-6` (`keep_param`'s 3 hardcoded param-key literals) | **Still present — unchanged, accepted debt.** Verified directly in `adapter/graph.py` (`"showports"`, `bus_structure_*`, `"generate_options"`). This was already documented as intentional, acknowledged debt in `AGENTS.md` itself (`AGENTS.md`'s param-filtering rule names these same three exceptions) at the time the original report was written, and remains so — not a regression, not silently drifted. |
| `ADPT-7` (unlocked concurrent RAG DB builds) | **Fixed** — `_build_lock_for`/`_BUILD_LOCKS` (per-domain `threading.Lock`) guard `_ensure_db_built`; also stress-tested with real concurrent threads this session (see "Independent adversarial verification" above). |
| `ADPT-8` (`search.py` can't distinguish selector drift from genuine no-results) | **Fixed** — confirmed via `test_lite_web_search_logs_selector_drift` (passing). |
| `ADPT-9` (backup snapshot taken outside the save lock) | **Fixed** — verified directly in `adapter/graph.py`: the backup copy now happens inside the lock (comment explicitly notes "Backup is taken INSIDE the lock so it snapshots exactly the on-disk state about to be overwritten"). |
| `ADPT-10` (dead redundant `_error_messages = []` line) | **Fixed** — verified directly; the line no longer exists in `adapter/graph.py`. |
| `ADPT-11` (system prompt describes an already-fixed gap, omits the real one) | **Fixed** — verified directly; `prompts.py` now accurately describes the current "auto" failure-mode behavior (fails loudly, per `ADPT-3`'s fix) rather than a stale scenario. |
| Dead `NativeCanvasManager`/`Proxy` methods (`reload_from_disk`, `get_drawing_area`, `graph_count`, `validate`, `swap`, `get_version`, `is_loaded`) | **Fixed** — verified directly; zero matches for any of these method names in `native_canvas.py`. |
| Duplicate markdown→Pango converters | **Fixed** — verified directly; only one `_node_to_pango` implementation remains, used consistently by `_render_markdown_to_box`. |
| Untracked `chat_sessions.db`, no `.gitignore` entry | **Fixed** — `chat_sessions.db` is now in `.gitignore`. |
| Stale `recent_sessions.json` `.gitignore` entry | **Fixed** — no longer present in `.gitignore`. |

No contradictions found between the two documents as of this writing.
