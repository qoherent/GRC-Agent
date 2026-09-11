"""Instrumented Done-marker reproduction (plan U1).

Boots the REAL app via ``build_app()`` under xvfb, opens a qt_gui graph
with a file sink, and runs three consecutive start/stop cycles plus a
forced SIGTERM-ignoring child-hang run (the dry-run F7 wedge shape).

Logs timestamped events to a file: child PID + exit status per signal
stage (SIGTERM sent, grace expiry, SIGKILL sent, reap), ExecFlowGraphThread
EOF/done() dispatch observations, monitor ``handle_message`` Done-marker
sightings, ``_tracking``/``_run_epoch`` transitions,
``synthesize_terminal_state`` invocations and outcomes, per cycle. Includes
a starvation probe: a permanently-ready higher-priority GLib source during
one stop, observing whether ``done()`` still dispatches.

Usage (no LLM spend, no commits, real DB untouched — GRC_AGENT_ENV is
redirected to a temp dir before any app import)::

    xvfb-run -a uv run python playground/repro_done_marker.py \
        --log /tmp/repro_done_marker.log [--mode plain|unified]

``plain`` (default) drives the run with ``asyncio.run``, mirroring the
dry-run F7 harness loop; ``unified`` drives it with production
``event_loop.main_event_loop()`` on GLib's default context as a control.

The verdict memo ``docs/investigation/2026-09-11-run-marker-root-cause.md``
quotes this launcher's log lines as evidence; re-run it to reproduce.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# --- env isolation BEFORE any grc_agent import (never touch the real DB) ---
_REPRO_TMP = Path(tempfile.mkdtemp(prefix="repro_done_marker_"))
_REPRO_ENV_FILE = _REPRO_TMP / ".env"
_REPRO_ENV_FILE.write_text("", encoding="utf-8")
os.environ["GRC_AGENT_ENV"] = str(_REPRO_ENV_FILE)
os.environ["GRC_AGENT_VECTORS_DIR"] = str(_REPRO_TMP / "vectors")
os.environ["GRC_REPRO_DEBUG"] = "1"
(_REPRO_TMP / "vectors").mkdir(exist_ok=True)

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

_LOG_FH = None
_T0 = time.monotonic()


def log(msg: str) -> None:
    wall = time.strftime("%H:%M:%S", time.localtime())
    dt = time.monotonic() - _T0
    line = f"[{wall} +{dt:8.3f}s] {msg}"
    print(line, flush=True)
    if _LOG_FH is not None:
        _LOG_FH.write(line + "\n")
        _LOG_FH.flush()


# F7 wedge shape (mirrors experiments/dataset_collection/dryrun.py).
_SIG_IGN_SRC = (
    "import signal\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "from gnuradio import gr\n"
    "\n\n"
    "class blk(gr.sync_block):\n"
    '    """Repro wedge probe: SIGTERM-ignoring no-op block."""\n'
    "    def __init__(self):\n"
    '        gr.sync_block.__init__(self, name="sig_ign_wedge", in_sig=[], out_sig=[])\n'
    "\n"
    "    def work(self, input_items, output_items):\n"
    "        return 0\n"
)


def _describe_proc(proc) -> str:
    if proc is None:
        return "process=None"
    try:
        pid = getattr(proc, "pid", "?")
        rc = proc.poll()
        return f"pid={pid} poll={rc} returncode={getattr(proc, 'returncode', '?')}"
    except Exception as e:  # noqa: BLE001
        return f"proc-describe-failed: {e!r}"


def _snapshot(tag: str, monitor, page) -> None:
    proc = getattr(page, "process", None)
    log(
        f"SNAP {tag}: tracking={monitor.is_tracking} "
        f"epoch={monitor.run_epoch} last_run_id={getattr(monitor, '_last_run_id', '?')} "
        f"last_code={monitor.last_run_code} "
        f"synth={getattr(monitor, '_last_run_synthesized', '?')} "
        f"term_by_kill={getattr(monitor, '_last_run_terminated_by_kill', '?')} "
        f"term_sig={getattr(monitor, '_last_run_termination_signal', '?')} "
        f"{_describe_proc(proc)}"
    )


def _install_wrappers(monitor, proxy) -> dict:
    """Wrap monitor/proxy/Messages/Executor entry points with timestamped logs.

    NOTE: build_app() already registered the monitor's ORIGINAL bound
    ``handle_message`` with ``Messages`` (which holds that object), so
    reassigning ``monitor.handle_message`` would NOT intercept live traffic.
    Done-marker sightings are therefore observed via an ADDITIONAL
    ``Messages`` messenger (non-interfering tap), while synth/escalate/stop
    are wrapped on the live objects the app actually calls.
    """
    state = {"done_markers": 0, "synth_calls": [], "send_end_exec": 0}

    try:
        from gnuradio.grc.core import Messages as _MsgTap

        def _tap(text: str) -> None:
            if "\n>>> Done" in text:
                state["done_markers"] += 1
                log(
                    f"HANDLE Done-marker sighting #{state['done_markers']}: "
                    f"tracking={monitor.is_tracking} epoch={monitor.run_epoch} "
                    f"text={text[-160:]!r}"
                )

        _MsgTap.register_messenger(_tap)
        state["_tap"] = _tap
    except Exception as e:  # noqa: BLE001
        log(f"WARN could not register Done tap: {e!r}")

    orig_synth = monitor.synthesize_terminal_state

    def _wrapped_synth(*, signal=None):
        before = (monitor.is_tracking, monitor.run_epoch)
        rc = orig_synth(signal=signal)
        log(
            f"SYNTH synthesize_terminal_state(signal={signal}) -> {rc}: "  # noqa: F811

            f"tracking {before[0]}->{monitor.is_tracking} epoch {before[1]}->{monitor.run_epoch} "
            f"last_code={monitor.last_run_code} synth_flag={monitor._last_run_synthesized}"
        )
        state["synth_calls"].append((signal, rc))
        return rc

    monitor.synthesize_terminal_state = _wrapped_synth

    orig_esc = proxy._escalate_stop

    async def _wrapped_esc(monitor_a, page_a, gen_opts_a, **kw):
        proc = getattr(page_a, "process", None)
        log(f"ESC _escalate_stop entry: gen_opts={gen_opts_a} {_describe_proc(proc)}")
        rc = await orig_esc(monitor_a, page_a, gen_opts_a, **kw)
        log(f"ESC _escalate_stop exit -> {rc}: {_describe_proc(getattr(page_a, 'process', None))}")
        return rc

    proxy._escalate_stop = _wrapped_esc

    orig_stop = proxy.stop_flowgraph

    async def _wrapped_stop():
        from grc_agent.native_canvas import NativeCanvasManager  # noqa: F401

        cm = object.__getattribute__(proxy, "_canvas_manager")
        page = cm.current_page
        proc = getattr(page, "process", None) if page is not None else None
        log(f"STOP stop_flowgraph entry (SIGTERM stage): {_describe_proc(proc)}")
        res = await orig_stop()
        page2 = object.__getattribute__(proxy, "_canvas_manager").current_page
        log(f"STOP stop_flowgraph exit -> {res.get('status')!r}: {_describe_proc(getattr(page2, 'process', None))}")
        return res

    proxy.stop_flowgraph = _wrapped_stop

    try:
        from gnuradio.grc.core import Messages

        orig_end = Messages.send_end_exec

        def _wrapped_end(code=0):
            state["send_end_exec"] += 1
            log(f"GRC send_end_exec(code={code}) emission #{state['send_end_exec']} (Executor.done() dispatch)")
            return orig_end(code)

        Messages.send_end_exec = _wrapped_end
        state["_orig_end"] = orig_end

        orig_start = Messages.send_start_exec

        def _wrapped_start(path):
            log(f"GRC send_start_exec: {str(path)[:160]!r}")
            return orig_start(path)

        Messages.send_start_exec = _wrapped_start
        state["_orig_start"] = orig_start
    except Exception as e:  # noqa: BLE001
        log(f"WARN could not wrap Messages: {e!r}")

    try:
        from gnuradio.grc.gui import Executor as _Exec

        orig_run = _Exec.ExecFlowGraphThread.run
        orig_done = _Exec.ExecFlowGraphThread.done

        def _wrapped_run(self):
            log(f"THREAD ExecFlowGraphThread.run enter: {_describe_proc(getattr(self, 'process', None))}")
            try:
                return orig_run(self)
            finally:
                log(f"THREAD ExecFlowGraphThread.run exit (EOF observed, entering reap poll): {_describe_proc(getattr(self, 'process', None))}")

        def _wrapped_done(self):
            log(f"THREAD Executor.done() dispatch via GLib.idle_add firing: {_describe_proc(getattr(self, 'process', None))}")
            try:
                return orig_done(self)
            finally:
                log(f"THREAD Executor.done() completed: page.process={getattr(self.page, 'process', None)!r}")

        _Exec.ExecFlowGraphThread.run = _wrapped_run
        _Exec.ExecFlowGraphThread.done = _wrapped_done
        state["_orig_run"] = orig_run
        state["_orig_done"] = orig_done
    except Exception as e:  # noqa: BLE001
        log(f"WARN could not wrap ExecFlowGraphThread: {e!r}")

    return state


def _log_child_watcher() -> None:
    try:
        import asyncio as _aio

        loop = _aio.get_event_loop()
        log(f"WATCHER loop={type(loop).__name__} policy={type(_aio.get_event_loop_policy()).__name__}")
        try:
            w = _aio.get_child_watcher()
            log(f"WATCHER child_watcher={type(w).__name__}")
        except Exception as e:  # noqa: BLE001
            log(f"WATCHER get_child_watcher unavailable: {e!r}")
        try:
            import gi.events  # noqa: F401

            log("WATCHER gi.events present (PyGObject>=3.50 path)")
        except ImportError:
            log("WATCHER gi.events ABSENT -> gbulb path (PyGObject 3.48)")
            try:
                import gbulb  # noqa: F401

                log("WATCHER gbulb importable")
            except Exception as e:  # noqa: BLE001
                log(f"WATCHER gbulb import failed: {e!r}")
    except Exception as e:  # noqa: BLE001
        log(f"WATCHER probe failed: {e!r}")


async def _starvation_probe(monitor) -> dict:
    """Separate GLib-default-context progress from asyncio progress.

    Arms a permanently-ready HIGH-priority GLib timeout source (done() rides
    at idle priority) AND a concurrent asyncio ticker. If the driving loop
    never iterates GLib's default context, the GLib starver records 0 ticks
    while the asyncio ticker advances — pinning the starvation to the
    context, not to asyncio being wedged. A ticks==0 outcome ALSO makes any
    priority-preemption question unanswerable (the probe source itself never
    dispatched); that limitation is recorded, not papered over.
    """
    from gi.repository import GLib

    out: dict = {"glib_ticks": 0, "asyncio_ticks": 0, "done_while_armed": None, "removed": False}
    armed_at = time.monotonic()
    before_done = getattr(monitor, "_last_run_id", None)

    def _starver():
        out["glib_ticks"] += 1
        # Permanently ready: always return True (re-armed at HIGH priority).
        # Bounded by wall clock: self-remove after 2.0 s so the app cannot wedge.
        if time.monotonic() - armed_at > 2.0:
            out["removed"] = True
            return False
        return True

    src_id = GLib.timeout_add(0, _starver, priority=GLib.PRIORITY_HIGH)
    log(f"STARVE probe ARMED: HIGH-priority (={GLib.PRIORITY_HIGH}) always-ready timeout source id={src_id}; idle done() is at {GLib.PRIORITY_DEFAULT_IDLE}")

    async def _asyncio_ticker() -> None:
        t0 = time.monotonic()
        while time.monotonic() - t0 < 2.4:
            out["asyncio_ticks"] += 1
            await asyncio.sleep(0.05)

    await _asyncio_ticker()
    after_done = getattr(monitor, "_last_run_id", None)
    out["done_while_armed"] = (after_done != before_done)
    if not out["removed"]:
        try:
            GLib.source_remove(src_id)
            out["removed"] = True
        except Exception:  # noqa: BLE001
            pass
    log(
        f"STARVE probe DONE: glib_ticks={out['glib_ticks']} asyncio_ticks={out['asyncio_ticks']} "
        f"run_id {before_done}->{after_done} removed={out['removed']}"
    )
    return out


async def amain(log_path: Path) -> int:
    from grc_agent.adapter.graph import change_graph
    from grc_agent.desktop_app import build_app

    log(f"REPRO boot: repo={REPO_ROOT} tmp={_REPRO_TMP} log={log_path}")
    log(f"REPRO env: GRC_AGENT_ENV={os.environ.get('GRC_AGENT_ENV')} real-DB untouched by design")
    _log_child_watcher()

    window, canvas, sidebar, proxy = build_app()
    monitor = proxy._exec_monitor
    log("REPRO build_app OK")
    wrap_state = _install_wrappers(monitor, proxy)

    # --- open a saved qt_gui graph with a file sink (temp project dir) ---
    proj = _REPRO_TMP / "proj"
    proj.mkdir(exist_ok=True)
    sidebar.set_project_directory(proj)
    src = REPO_ROOT / "tests" / "data" / "empty.grc"
    starter = proj / "repro_done_marker.grc"
    shutil.copy2(src, starter)
    window.new_page(str(starter), show=True)
    await asyncio.sleep(0.5)
    page = canvas.current_page
    log(f"REPRO opened page file_path={getattr(page, 'file_path', None)!r}")
    _snapshot("after-open", monitor, page)

    fg = page.flow_graph
    sink_file = str(_REPRO_TMP / "repro_capture.dat")
    res = change_graph(
        fg,
        add_blocks=[
            {"block_id": "blocks_null_source", "instance_name": "blocks_null_source_0", "params": {"type": "float"}},
            {"block_id": "blocks_throttle", "instance_name": "blocks_throttle_0", "params": {"type": "float", "samples_per_second": "32000"}},
            {"block_id": "blocks_file_sink", "instance_name": "blocks_file_sink_0", "params": {"type": "float", "file": sink_file}},
        ],
        add_connections=[
            "blocks_null_source_0:0->blocks_throttle_0:0",
            "blocks_throttle_0:0->blocks_file_sink_0:0",
        ],
    )
    log(f"REPRO change_graph -> {res}")
    fg.validate()
    log(f"REPRO graph valid={fg.is_valid()} errors={list(fg.iter_error_messages())[:3]}")
    sres = await proxy.save_graph()
    log(f"REPRO save_graph -> {sres}")
    _snapshot("graph-ready", monitor, page)

    tripwire_fired: list[str] = []

    def _check_tripwire(cycle: str) -> None:
        proc = getattr(canvas.current_page, "process", None)
        reaped = proc is not None and proc.poll() is not None
        if reaped and monitor.is_tracking:
            msg = f"{cycle}: TRIPWIRE WOULD FIRE (process reaped but monitor still tracking)"
            log(msg)
            tripwire_fired.append(msg)
        else:
            log(f"{cycle}: tripwire check: reaped={reaped} tracking={monitor.is_tracking} -> no fire")

    # --- Cycle 1: bounded run (~4 s) then auto-stop ---
    log("=== CYCLE 1: bounded run stop_after_seconds=4 (wait=True) ===")
    _snapshot("c1-before-start", monitor, page)
    t0 = time.monotonic()
    r1 = await proxy.run_flowgraph(action="start", wait=True, stop_after_seconds=4)
    dt1 = time.monotonic() - t0
    log(f"CYCLE 1 run_flowgraph -> {r1} in {dt1:.2f}s")
    _snapshot("c1-after-stop", monitor, page)
    log(f"CYCLE 1 get_run_log -> {str(proxy.get_run_log())[:400]!r}")
    _check_tripwire("c1")

    # --- Cycle 2: unbounded start, sleep, explicit stop ---
    log("=== CYCLE 2: unbounded start (wait=False), sleep 3 s, then stop ===")
    r2s = await proxy.run_flowgraph(action="start", wait=False)
    log(f"CYCLE 2 start -> {r2s}")
    await asyncio.sleep(3.0)
    _snapshot("c2-running", monitor, page)
    t0 = time.monotonic()
    r2p = await proxy.stop_flowgraph()
    dt2 = time.monotonic() - t0
    log(f"CYCLE 2 stop -> {r2p} in {dt2:.2f}s")
    _snapshot("c2-after-stop", monitor, page)
    _check_tripwire("c2")

    # --- Cycle 3: unbounded start/stop WITH starvation probe armed at stop ---
    log("=== CYCLE 3: unbounded start, starvation probe armed across stop ===")
    r3s = await proxy.run_flowgraph(action="start", wait=False)
    log(f"CYCLE 3 start -> {r3s}")
    await asyncio.sleep(3.0)
    _snapshot("c3-running", monitor, page)
    starve_task = asyncio.ensure_future(_starvation_probe(monitor))
    await asyncio.sleep(0.2)  # let the HIGH-priority starver engage first
    t0 = time.monotonic()
    r3p = await proxy.stop_flowgraph()
    dt3 = time.monotonic() - t0
    log(f"CYCLE 3 stop (starver armed) -> {r3p} in {dt3:.2f}s")
    starve_out = await starve_task
    log(f"CYCLE 3 starvation probe outcome: {starve_out}")
    _snapshot("c3-after-stop", monitor, page)
    _check_tripwire("c3")

    # --- Cycle 4 (forced hang): SIGTERM-ignoring wedge block, bounded run ---
    log("=== CYCLE 4 (WEDGE): add SIGTERM-ignoring epy_block, bounded run 4 s ===")
    res_w = change_graph(
        fg,
        add_blocks=[
            {"block_id": "epy_block", "instance_name": "sig_ign_wedge", "params": {"_source_code": _SIG_IGN_SRC}},
        ],
    )
    log(f"WEDGE change_graph add -> {res_w}")
    fg.validate()
    log(f"WEDGE graph valid={fg.is_valid()}")
    await proxy.save_graph()
    _snapshot("wedge-ready", monitor, page)
    t0 = time.monotonic()
    r4 = await proxy.run_flowgraph(action="start", wait=True, stop_after_seconds=4)
    dt4 = time.monotonic() - t0
    log(f"WEDGE bounded run -> {r4} in {dt4:.2f}s (expect escalation: SIGTERM ignored -> SIGKILL)")
    _snapshot("wedge-after-stop", monitor, page)
    wlog = proxy.get_run_log() or {}
    log(f"WEDGE get_run_log keys={sorted(wlog.keys())} rc={wlog.get('return_code')} synth={wlog.get('terminated_by_kill')} note={str(wlog.get('termination_note'))[:200]!r}")
    _check_tripwire("wedge")

    # --- Heal: remove wedge, prove next cycle clean ---
    log("=== HEAL: remove wedge block, clean start/stop ===")
    change_graph(fg, remove_blocks=["sig_ign_wedge"])
    await proxy.save_graph()
    _snapshot("heal-ready", monitor, page)
    rh1 = await proxy.run_flowgraph(action="start", wait=False)
    log(f"HEAL start -> {rh1}")
    await asyncio.sleep(2.0)
    rh2 = await proxy.stop_flowgraph()
    log(f"HEAL stop -> {rh2}")
    _snapshot("heal-after-stop", monitor, page)
    _check_tripwire("heal")

    log(f"REPRO summary: Done-markers seen={wrap_state['done_markers']} send_end_exec emissions={wrap_state['send_end_exec']} synth_calls={wrap_state['synth_calls']}")
    log(f"REPRO summary: tripwire observations={tripwire_fired if tripwire_fired else 'NONE FIRED'}")
    log("REPRO complete")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", default="/tmp/repro_done_marker.log")
    ap.add_argument(
        "--mode",
        choices=("plain", "unified"),
        default="plain",
        help="plain = asyncio.run (mirrors the dry-run F7 harness loop); "
        "unified = production main_event_loop on GLib's default context (control).",
    )
    args = ap.parse_args()
    log_path = Path(args.log)
    global _LOG_FH
    with open(log_path, "w", encoding="utf-8") as fh:
        _LOG_FH = fh
        log(f"REPRO launcher start (argv={sys.argv!r} mode={args.mode})")
        try:
            if args.mode == "unified":
                from grc_agent import event_loop as _el

                _el.install()
                loop = _el.main_event_loop()
                return loop.run_until_complete(amain(log_path))
            return asyncio.run(amain(log_path))
        finally:
            with contextlib.suppress(Exception):
                fh.flush()
            _LOG_FH = None


if __name__ == "__main__":
    sys.exit(main())
