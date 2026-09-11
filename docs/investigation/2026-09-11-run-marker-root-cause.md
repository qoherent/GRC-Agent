# Run-marker root cause: which mechanism stops GNU Radio's Done marker after stop

Date: 2026-09-11. Launcher: `playground/repro_done_marker.py` (kept,
`--mode plain|unified`). No LLM spend. Real DB byte-identical before/after
(`5738f1482503de02ffb155337d41d883719dc3581430ae742f68391596d33c17`).

## Verdict summary

| # | Candidate mechanism | Verdict |
|---|---------------------|---------|
| M1 | Child-side `tb.wait()` hang on SIGTERM | **NEGATIVE (qualified)** — all 10 children (incl. both SIGTERM-ignoring wedge runs) exited `poll=0` promptly after SIGTERM; thread EOF observed every cycle. Below the detection threshold for these runs, not disproof. |
| M2a | Idle-callback starvation, context-mismatch (default GLib context never pumped) | **REPRODUCED** — 5/5 plain-loop runs lost the Done marker permanently (0 sightings, 0 `send_end_exec` emissions) despite child exit + thread EOF; GLib starver 0 ticks vs asyncio ticker 48 ticks. |
| M2b | Idle-callback starvation, priority preemption (HIGH source starves idle `done()`) | **NEGATIVE (qualified)** — on the unified loop a HIGH-priority always-ready source (6M ticks / 2.4 s) delayed `done()` not at all beyond ~0.1 s; the stop still completed in 0.51 s. At most a transient delay, never the wedge. |
| M3 | Child-watcher reap race | **NEGATIVE (qualified)** — zero watcher/reap errors across 10 runs; `poll()`/`returncode` always consistent; Executor uses thread+`poll()`, no asyncio transport is involved. |
| — | Harness teardown tripwire fired? | **NO** — 0/10 checks (5 plain + 5 unified). |
| — | Harness kill-ladder disposition | **KEPT as defense-in-depth** (rationale below). |

The escalation synthesis in the app is the designed bounded fallback (KTD3):
in every plain-loop cycle it retained the log with disclosure, cleared
tracking, and cleared `page.process` — 5/5 healed, next cycles clean.

## How it was reproduced

Real app under `xvfb-run -a` via the project's real `build_app()`, qt_gui
graph `null_source → throttle → file_sink` (built with `change_graph` on a
`tests/data/empty.grc` copy, saved in a temp project dir), GRC_AGENT_ENV
redirected to temp (real DB untouched). Per cycle the launcher logs child
PID + `poll()`/`returncode`, `ExecFlowGraphThread` run-enter/exit and
`done()` dispatch, a `Messages`-level Done tap, `_tracking`/`_run_epoch`
snapshots, `synthesize_terminal_state` invocations + outcomes, and the
harness tripwire condition — five cycles per mode:

1. bounded run (`stop_after_seconds=4`, `wait=True`),
2. unbounded start (`wait=False`) + sleep 3 s + explicit stop,
3. unbounded start/stop with the starvation probe armed across the stop,
4. forced hang: SIGTERM-ignoring `epy_block` added (the dry-run F7 shape:
   `signal.signal(signal.SIGTERM, signal.SIG_IGN)`), bounded 4 s run,
5. heal: wedge removed, clean start/stop.

`--mode plain` drives with `asyncio.run` (mirrors the dry-run F7 harness
loop). `--mode unified` drives with production
`event_loop.main_event_loop()` on GLib's default context (control).

Re-run:

```bash
xvfb-run -a uv run python playground/repro_done_marker.py --log /tmp/repro_done_marker.log [--mode plain|unified]
```

Temporary DEBUG-gated (`GRC_REPRO_DEBUG`) instrumentation in
`src/grc_agent/native_canvas.py` (`_escalate_stop`, `stop_flowgraph`) and
`src/grc_agent/exec_monitor.py` (Done branch, `synthesize_terminal_state`)
was used during the runs and **removed afterwards** — `grep -rn REPRO-INST
src/ --include='*.py'` returns 0 lines and `git diff --name-only` is empty.

## Installed-source chain (what has to happen for Done to arrive)

- `ExecFlowGraphThread.run` reads child stdout to EOF, closes the pipe,
  spins on `poll()`, then `GLib.idle_add(self.done)` (`Executor.py:106-123`).
- `done()` sends `Messages.send_end_exec(returncode)` and clears
  `page.process` (`Executor.py:125-127`); `send_end_exec` emits
  `"\n>>> Done"` (`Messages.py:111-113`), which the monitor matches
  (`exec_monitor.py: _EXEC_DONE_MARKER`).
- Toolbar Stop is SIGTERM-only: `page.process.terminate()`
  (`Application.py:780-786`); the app's `stop_flowgraph` waits 10 s, then
  `_escalate_stop` waits a grace window, SIGKILLs direct PIDs, waits a
  settle window, then `synthesize_terminal_state(signal=SIGKILL)`
  (`native_canvas.py:465`, `:539`; `exec_monitor.py:411`).
- gbulb's loop owns a **private** `GLib.MainContext()` + `MainLoop` on it
  (`gbulb/glib_events.py:259,817`); plain `GLib.idle_add`/`timeout_add`
  attach to the **default** context. Only the default-loop path uses
  `main_context_default()` (`:1023-1025`), and `new_event_loop()` returns it
  only on the main thread before any loop exists (`:1004-1015`). An
  `asyncio.run` loop created **before** `event_loop.install()` is a plain
  `_UnixSelectorEventLoop` that never iterates any GLib context.

## M1 — child-side hang: negative (qualified)

Every cycle in both modes ended with the worker thread observing EOF and a
reaped child *before* any SIGKILL:

```text
[plain c1] THREAD ExecFlowGraphThread.run exit (EOF observed, entering reap poll): pid=93608 poll=0 returncode=0
[plain c2] THREAD ExecFlowGraphThread.run exit (EOF observed, entering reap poll): pid=93783 poll=0 returncode=0
[plain wedge] THREAD ExecFlowGraphThread.run exit (EOF observed, entering reap poll): pid=94107 poll=0 returncode=0
[unified c2] THREAD ExecFlowGraphThread.run exit (EOF observed, entering reap poll): pid=94744 poll=0 returncode=0
[unified wedge] THREAD ExecFlowGraphThread.run exit (EOF observed, entering reap poll): pid=94875 poll=0 returncode=0
```

The wedge child (SIGTERM-ignoring `epy_block`) is indistinguishable from a
normal graph: `poll=0` within ~0.5 s of SIGTERM, and on the unified loop it
terminated **naturally** (`HANDLE Done-marker sighting #4`, `rc=0`, no
synthesis, 4.55 s total). The F7 SIG_IGN shape does not wedge `tb.wait()`
here — the top block still shuts down and exits 0. A true `tb.wait()` hang
(e.g. an uninterruptible driver call) remains possible but was not produced
by this probe: negative below the detection threshold for 10 runs (5 plain +
5 unified, incl. 2 wedge runs), never "disproven" (KTD3).

## M2a — context-mismatch starvation: reproduced (plain loop)

Plain mode (`WATCHER loop=_UnixSelectorEventLoop policy=GtkEventLoopPolicy`
— the running loop predates `install()`, so it is not unified):

- 5/5 cycles: Done never dispatched — `REPRO summary: Done-markers seen=0
  send_end_exec emissions=0` — while the thread proved the child was gone.
  Console instrumentation shows the escalation SIGKILLing an **already-dead**
  child every time, then synthesizing:

```text
REPRO-INST _escalate_stop grace expired, SIGKILLing (t=6638.757 pid=93608 poll=0)
REPRO-INST _escalate_stop settle end: t=6643.769 tracking=True poll=0
REPRO-INST synthesize_terminal_state entry: t=6643.769 signal=9 tracking=True epoch=1
REPRO-INST synthesize_terminal_state exit -> True: t=6643.769 epoch=1
```

  Zero `REPRO-INST exec_monitor Done sighting` lines in 35 instrumentation
  lines. Round trips: bounded 29.06 s / explicit-stop 25.04 s (4 s budget +
  10 s native wait + 10 s grace + 5 s settle), vs 4.54 s / 0.50 s unified.

- Discriminator (cycle 3): `STARVE probe DONE: glib_ticks=0
  asyncio_ticks=48` — asyncio advanced 48 ticks while a HIGH-priority
  always-ready GLib source recorded **zero**, i.e. the driving loop is alive
  but GLib's default context is never iterated. `done()` at idle priority
  cannot fire on a context nobody pumps.

Control (unified loop, `WATCHER loop=GtkEventLoop`): 5/5 natural terminals,
`REPRO summary: Done-markers seen=5 send_end_exec emissions=5
synth_calls=[]`, e.g. `CYCLE 2 stop -> {'status': 'stopped', ...} in 0.50s`
with `HANDLE Done-marker sighting #2 ... text='\n>>> Done\n'` and
`page.process=None` immediately after `Executor.done()`.

## M2b — priority preemption: negative (qualified)

Unified cycle 3 armed a permanently-ready `PRIORITY_HIGH` (-100) timeout
source across the stop: `glib_ticks=5998074 asyncio_ticks=9` — the main
context pumped ~6M starver ticks — yet the stop completed in **0.51 s** with
a natural Done (`HANDLE Done-marker sighting #3`). A higher-priority busy
source delays idle `done()` by ~0.1 s at most here; it never produces the
wedge. (Corollary: the plain-mode probe's `glib_ticks=0` also makes any
priority-preemption question unanswerable in that mode — the probe source
itself never dispatched. Recorded as a probe limitation, not a finding.)

## M3 — child-watcher reap race: negative (qualified)

`WATCHER child_watcher=GLibChildWatcher` in both modes, but Executor reaps
via worker-thread `poll()`, never via an asyncio subprocess transport, so
the watcher has no surface here: 10/10 cycles show consistent
`poll()`/`returncode` pairs, zero `NotImplementedError`/`ChildProcessError`,
zero `poll()`-vs-`waitpid` disagreements. The addendum-3 sandbox
`NotImplementedError` (flowgraph reaped mid-teardown) was not observed.
Qualified-negative per KTD3 (10 runs, this environment).

## Tripwire report

The harness tripwire (`runner.py::_stop_run`: process reaped but monitor
still tracking → `RunnerContractError`) **never fired**: 10/10 launcher
tripwire checks report `no fire`. In plain mode the synthesis always healed
(`tracking True->False`, `page.process` cleared) before the condition could
hold; in unified mode the natural terminal did. Per the packet this is
recorded as a finding-equivalent statement: there was nothing to work around.

## Harness reconciliation (R5)

- **Stale docstring corrected** (`collection/contract.py::run_tracking`):
  the old "no terminal marker arrives" now reads scoped — natural Done
  arrives on the production unified loop; under the campaign's `asyncio.run`
  loop it structurally cannot arrive, and the app's kill-escalation
  synthesis owns the terminal accounting (5/5 healed, tripwire never fired).
- **Ladder disposition: KEEP as defense-in-depth** (`runner.py::_stop_run`
  docstring records the decision). The app terminal path proved complete in
  both modes, which would license collapsing to the loud-halt tripwire —
  but the campaign loop makes the synthesis load-bearing (natural markers
  never arrive there), so the `process.kill()` rung stays as the backstop
  if synthesis ever regresses. Steady-state cost is zero: the rung never
  engaged in 10 instrumented runs, and the tripwire stays loud (no repair,
  halt on `monitor still tracking a reaped run`).
- **A-33 mechanism note**: "the retained run log NEVER materializes" is now
  explained, not just observed — verbose child output rides the same
  `GLib.idle_add` path as Done (`Executor.py:114`), so under the campaign
  loop the retained log holds the start marker plus synthesis disclosure
  (never child stdout). `flowgraph_ran` remains the correct acceptance kind
  in this environment.

Recorded in-harness: `docs/AUDIT-2026-09-09.md` Addendum 10.

## Qualified-negative disclaimer (KTD3)

M1, M2b and M3 are **below the detection threshold for these runs, not
disproven**: 10 start/stop cycles (5 per loop mode, incl. 2 forced-hang
wedges) on one host (PyGObject 3.48.2 / gbulb path, GNU Radio 3.10.9.2,
qt_gui null→throttle→file-sink + SIG_IGN wedge shapes). A different child
(a hanging driver call), a different loop build (`gi.events` path), or
sustained main-thread saturation could still produce a loss this probe did
not. The escalation synthesis in the app is the designed bounded fallback
for exactly that residual.

## Verification appendix

- `uv run pytest tests/test_exec_monitor.py tests/test_run_stop_tools.py -q` → **95 passed**.
- `uv run pytest experiments/dataset_collection/tests -q` (repo root) → **153 passed**.
- `uv run ruff check` (outer) → clean; harness tree `ruff check` → clean.
- `grep -rn REPRO-INST src/ --include='*.py'` → 0; `git diff --name-only` → empty (instrumentation removed).
- `sha256sum .grc_agent/chat_sessions.db` → `5738f1482503de02ffb155337d41d883719dc3581430ae742f68391596d33c17`.
- Memo + launcher exist; harness edits present (`contract.py`, `runner.py`, `AUDIT-2026-09-09.md` Addendum 10).
