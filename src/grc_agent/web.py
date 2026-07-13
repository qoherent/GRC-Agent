import asyncio
import atexit
import mimetypes
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from pydantic_ai import Agent, ModelSettings
from pydantic_ai.capabilities import (
    ProcessHistory,
)
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

# Local imports
from grc_agent.adapter import (
    inspect_graph,
    load_flow_graph,
    redo_flowgraph,
    undo_flowgraph,
    undo_status,
)
from grc_agent.agent import (
    OLLAMA_V1,
    GrcAgentResponse,
    StopGracefully,
    grc_tools,
    prune_history,
    validate_flowgraph_state,
    web_fetch_cap,
    web_search_cap,
)
from grc_agent.prompts import build_system_prompt
from grc_agent.settings import default_settings, load_settings, save_settings

load_dotenv()

BRAND_NAME = "Qoherent GRC Agent"

# The canvas_app.py subprocess (spawned per /grc/open, see below) runs a
# small local HTTP control server on this port for resize (dashboard pane
# size changes) and reload (an agent-driven edit changed the file on disk
# and the live GTK canvas needs to catch up) — see canvas_app.py's
# start_control_server. Env-overridable so two instances (e.g. a dev session
# and the test suite) can coexist without one stomping the other's ports.
BROADWAY_PORT = int(os.environ.get("GRC_BROADWAY_PORT", "8085"))
CANVAS_CONTROL_PORT = int(os.environ.get("GRC_CANVAS_CONTROL_PORT", "7933"))
GRC_AGENT_PORT = int(os.environ.get("GRC_AGENT_PORT", "7932"))
# How long /grc/open's _wait_for_canvas_ready waits for the canvas subprocess
# before giving up and reporting canvas_error instead of blocking the
# response forever. Env-overridable (mirroring the ports above) so tests can
# force a fast, deterministic timeout instead of waiting out a real 20s.
CANVAS_READY_TIMEOUT = float(os.environ.get("GRC_CANVAS_READY_TIMEOUT", "20.0"))


async def _notify_canvas_reload() -> dict:
    """Ping the running canvas_app.py to reload the flowgraph from disk.
    Without this, an agent tool call that edits the flowgraph (change_graph)
    only updates this process's in-memory copy — the live GTK canvas has no
    way to learn about it and silently keeps showing stale content, even
    though the chat just told the user the edit succeeded. Async so a slow
    or unreachable canvas control server only delays this one call, not the
    whole single-worker event loop (the old sync httpx.post blocked every
    concurrent request for up to its full timeout). Returns the outcome
    rather than swallowing it, so a desync is surfaced to the caller."""
    try:
        async with httpx.AsyncClient(timeout=0.5) as client:
            await client.post(f"http://127.0.0.1:{CANVAS_CONTROL_PORT}/reload")
        return {"ok": True}
    except Exception as e:
        # Without a log line here, an agent-edit-desync-from-canvas is
        # completely invisible in server logs — change_graph_func folds the
        # outcome into its JSON result, but nothing else ever prints it.
        print(f"[grc-agent] Canvas reload notification failed: {e}")
        return {"ok": False, "error": str(e)}


class FlowgraphProxy:
    """Transparent stand-in for the active flowgraph so it can be swapped
    (e.g. via /grc/open) without rebuilding the Agent/web app. Every
    adapter call does plain attribute/method access on `ctx.deps`, so
    forwarding __getattr__/__setattr__ to whichever flowgraph is currently
    targeted is enough — no changes needed to agent.py's tool code. Starts
    empty: the session always begins with no file loaded, and any tool
    call before one is chosen gets a clear error instead of a crash.

    `on_edit` is an async hook fired only after an actual agent edit
    (change_graph) — not on open/close/reload swaps — to tell the live GTK
    canvas to reload. Decoupled from the version counter so loading/closing
    a file doesn't waste a reload ping on a canvas that doesn't exist yet."""

    def __init__(self, flowgraph: Any = None, on_edit: Any = None, state_lock: Any = None) -> None:
        object.__setattr__(self, "_target", flowgraph)
        object.__setattr__(self, "_version", 0)
        object.__setattr__(self, "_on_edit", on_edit)
        object.__setattr__(self, "_state_lock", state_lock)

    def __getattr__(self, name: str) -> Any:
        target = object.__getattribute__(self, "_target")
        if target is None:
            raise RuntimeError(
                "No .grc file is loaded yet. Ask the user to click Browse "
                "and choose a flowgraph file before using this tool."
            )
        return getattr(target, name)

    def __setattr__(self, name: str, value: Any) -> None:
        target = object.__getattribute__(self, "_target")
        if target is None:
            raise RuntimeError(
                "No .grc file is loaded yet. Ask the user to click Browse "
                "and choose a flowgraph file before using this tool."
            )
        setattr(target, name, value)

    def swap(self, flowgraph: Any) -> None:
        object.__setattr__(self, "_target", flowgraph)
        self.bump_version()

    def bump_version(self) -> None:
        """Increment the version counter the dashboard polls (/grc/status)
        to detect that the graph changed. Pure counter — the canvas-reload
        ping is a separate async step (notify_edit) fired only after an
        actual edit, not on every open/close/reload swap."""
        v = object.__getattribute__(self, "_version")
        object.__setattr__(self, "_version", v + 1)

    async def notify_edit(self) -> dict:
        """After an agent edit (change_graph), tell the live GTK canvas to
        reload from disk. No-op (returns ok) when no on_edit hook is wired
        (e.g. the scenario harness passes a raw flowgraph, not a proxy)."""
        on_edit = object.__getattribute__(self, "_on_edit")
        if on_edit:
            return await on_edit()
        return {"ok": True, "skipped": True}

    def get_version(self) -> int:
        return object.__getattribute__(self, "_version")

    def is_loaded(self) -> bool:
        return object.__getattribute__(self, "_target") is not None

    def get_state_lock(self) -> Any:
        """Expose the lock that guards open/close/reload swaps of `_target`
        (bypasses __getattr__'s forwarding, same as get_version/is_loaded)
        so agent.py's tool functions can serialize their multi-step reads/
        mutations against a concurrent swap. Returns None when constructed
        without one (no caller currently does this, but keeps the pattern
        symmetric with `on_edit`)."""
        return object.__getattribute__(self, "_state_lock")


# Guards every mutation of active/active_path/canvas_proc — without it,
# two concurrent /grc/open (or an open racing a close) calls interleave
# their terminate-old/spawn-new sequences non-deterministically; each
# still reports {"ok": true} to its own caller, but only one's file
# actually ends up loaded. Serializing makes the outcome deterministic
# (last request in wins) instead of a silent race. Also exposed to
# agent.py's tool functions (via FlowgraphProxy.get_state_lock()) so a
# change_graph/inspect_graph call can't have its target identity swap out
# from under it mid-call.
_flowgraph_state_lock = asyncio.Lock()

# 1. No flowgraph is loaded at startup — the user must Browse and choose one.
active = FlowgraphProxy(None, on_edit=_notify_canvas_reload, state_lock=_flowgraph_state_lock)
active_path: str | None = None
canvas_proc: subprocess.Popen | None = None
# Last-known canvas outcome for the currently active flowgraph, mirrored into
# /grc/status so any poll — not just the one-shot /grc/open response that
# produced it — can learn the canvas is unavailable. Without this, a bare
# refresh() call made well after a real canvas failure (doUndo, doRedo,
# doValidate, the version-bump-triggered refresh in pollConversationState)
# has no way to know not to re-point the dashboard's iframe at a canvas
# already known to be dead — refresh()'s own canvasReady param defaults to
# true, since those call sites never had this signal available before.
canvas_ready_state: bool = True
canvas_error_state: str | None = None
# broadwayd daemons this process spawned (eagerly in main(), lazily in
# ensure_broadway) — tracked so teardown terminates only OUR daemons, never
# another instance's (which a global `killall broadwayd` would stomp).
broadway_procs: list[subprocess.Popen] = []


def _broadway_pidfile() -> Path:
    """Port-scoped PID file so a restart can reclaim THIS port's stale
    broadwayd without a global killall (multi-instance safe: each port gets
    its own file)."""
    return Path(tempfile.gettempdir()) / f"grc_agent_broadway_{BROADWAY_PORT}.pid"


def _canvas_pidfile() -> Path:
    """Control-port-scoped PID file so a restart can reclaim THIS instance's
    stale canvas_app.py without a global pkill (multi-instance safe, mirrors
    _broadway_pidfile)."""
    return Path(tempfile.gettempdir()) / f"grc_agent_canvas_{CANVAS_CONTROL_PORT}.pid"


def _proc_comm(pid: int) -> str | None:
    """Identity check via /proc (Linux) — safe against PID reuse: a PID is
    only treated as a reclaimable broadwayd if its comm still matches."""
    try:
        return (Path("/proc") / str(pid) / "comm").read_text().strip()
    except OSError:
        return None


def _proc_cmdline(pid: int) -> str | None:
    """Like _proc_comm but for canvas_app.py: it runs as a generic `python3`
    process, so /proc/<pid>/comm alone can't distinguish it from any other
    Python process that might reuse the PID — check the NUL-separated
    argv in /proc/<pid>/cmdline for the script name instead."""
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except OSError:
        return None
    return raw.replace(b"\0", b" ").decode(errors="replace")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _killpg_with_fallback(pid: int, wait_s: float = 2.0, poll_interval: float = 0.1) -> None:
    """Signal a (possibly no-longer-tracked) process's whole group with
    SIGTERM, then SIGKILL if it's still alive after a brief wait. Works from
    a bare PID read out of a pidfile — os.getpgid() operates on any live PID,
    not just ones we hold a Popen object for. Used by the orphan-reclaim
    functions, which never had a Popen handle for these PIDs to begin with
    (they belong to a previous run of this process)."""
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(poll_interval)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


def _reclaim_broadway_orphan() -> None:
    """Kill a *previous* run's broadwayd still holding our port. Without this,
    a restart reuses the orphan broadwayd while spawning a fresh canvas ->
    two GTK windows on the same Broadway display (the 'dual window' bug).
    Skips PIDs this process spawned (tracked in broadway_procs) and any PID
    whose comm no longer looks like broadwayd (i.e. reused by an unrelated
    process), so it never kills the wrong thing.

    Waits for confirmed death (via _killpg_with_fallback, same as
    _reclaim_canvas_orphan) rather than firing a bare SIGTERM and returning
    immediately — a live-reproduced bug found post-fix: ensure_broadway()'s
    port connect-check runs right after this call, and a broadwayd that's
    still mid-shutdown (SIGTERM sent but not yet exited) still accepts that
    connection, so ensure_broadway() concluded "already running" and skipped
    spawning a fresh one — leaving the canvas subprocess pointed at a display
    whose broadwayd was dying moments later, crashing GTK's style-provider
    setup with 'Argument 0 does not allow None as a value'. The caller
    (ensure_broadway) now runs this via asyncio.to_thread precisely so this
    wait doesn't block the event loop."""
    try:
        old_pid = int(_broadway_pidfile().read_text().strip())
    except (OSError, ValueError):
        return
    if old_pid in {p.pid for p in broadway_procs}:
        return  # ours, still alive this session — keep it
    comm = _proc_comm(old_pid)
    if not comm or "broadway" not in comm:
        return  # process is gone, or PID reused by something unrelated
    print(f"[grc-agent] Reclaiming stale broadwayd (pid {old_pid}) on port {BROADWAY_PORT}")
    _killpg_with_fallback(old_pid)


def _reclaim_canvas_orphan() -> None:
    """Kill a *previous* run's canvas_app.py still holding our control port
    (e.g. orphaned by a SIGKILL crash, or — before the SIGTERM handler below
    existed — a SIGTERM that killed this process before cleanup ran).
    Without this, a restart leaves the orphan running (a stray GTK window /
    dead control-port squatter) while a fresh canvas is spawned alongside it.
    Skips the PID this process still tracks in `canvas_proc` (still alive
    this session — keep it) and any PID whose cmdline no longer looks like
    canvas_app.py (reused by an unrelated process), so it never kills the
    wrong thing."""
    try:
        old_pid = int(_canvas_pidfile().read_text().strip())
    except (OSError, ValueError):
        return
    if canvas_proc is not None and old_pid == canvas_proc.pid:
        return  # ours, still alive this session — keep it
    cmdline = _proc_cmdline(old_pid)
    if not cmdline or "canvas_app.py" not in cmdline:
        return  # process is gone, or PID reused by something unrelated
    print(
        f"[grc-agent] Reclaiming stale canvas process (pid {old_pid}) on "
        f"control port {CANVAS_CONTROL_PORT}"
    )
    _killpg_with_fallback(old_pid)


def _spawn_broadway() -> None:
    """Start broadwayd on BROADWAY_PORT, track it for scoped teardown, and
    record its PID so the next launch can reclaim it if this run crashes.
    Verifies the process is still running a moment after spawn before
    committing its PID to the pidfile — if it exited immediately (e.g. the
    TCP port is already held by something unrelated), the previous pidfile
    entry is left as-is instead of being overwritten with a dead PID."""
    _reclaim_broadway_orphan()
    try:
        # broadwayd isn't tied to any flowgraph's directory, so its log lives
        # alongside its pidfile in tempdir. Truncated (not appended) on every
        # launch, matching canvas.log's own "debug log for the current run"
        # rationale — a bind failure (the main reason this log exists) used
        # to leave zero diagnostic trail with stdout/stderr routed to DEVNULL.
        log_path = Path(tempfile.gettempdir()) / f"grc_agent_broadwayd_{BROADWAY_PORT}.log"
        with open(log_path, "wb") as broadway_log:
            proc = subprocess.Popen(
                ["broadwayd", "-p", str(BROADWAY_PORT), f":{BROADWAY_PORT}"],
                stdout=broadway_log,
                stderr=broadway_log,
                preexec_fn=os.setsid,
            )
        time.sleep(0.3)
        if proc.poll() is not None:
            print(
                f"[grc-agent] broadwayd failed to start (exited immediately, "
                f"code {proc.returncode}) — see {log_path}"
            )
            return
        broadway_procs.append(proc)
        _broadway_pidfile().write_text(str(proc.pid))
        print(
            f"[grc-agent] Started broadwayd daemon on port {BROADWAY_PORT} "
            f"display :{BROADWAY_PORT}"
        )
    except Exception as e:
        print("[grc-agent] Failed to start broadwayd daemon:", e)


def _terminate_broadway() -> None:
    """Terminate only the broadwayd daemons this process spawned. Signals
    the whole process group (not just the leader PID) since broadwayd is
    spawned with preexec_fn=os.setsid — any child it spawned would otherwise
    survive a plain terminate()/kill() on the leader alone."""
    had_tracked = bool(broadway_procs)
    for proc in broadway_procs:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
    broadway_procs.clear()
    if had_tracked:
        # Only unlink what THIS instance actually owned and just terminated.
        # If we never tracked anything here (e.g. startup found an
        # already-live broadwayd and deferred to it without spawning our
        # own), the pidfile may be the only remaining record of an EARLIER,
        # still-orphaned instance's PID — unlinking it unconditionally on
        # every uninvolved shutdown erased that trail and permanently
        # defeated _reclaim_broadway_orphan() for that earlier orphan
        # (live-reproduced: crash -> no-op restart -> clean shutdown left
        # the original orphan unreclaimable by any future launch).
        try:
            _broadway_pidfile().unlink(missing_ok=True)
        except OSError:
            pass


def _cleanup_procs() -> None:
    """Reap tracked subprocesses (broadwayd + canvas) on shutdown so they
    don't orphan across restarts. Idempotent."""
    _terminate_canvas_proc()
    _terminate_broadway()


def _handle_sigterm(signum: int, frame: Any) -> None:
    """Explicit SIGTERM handler — required because atexit alone does NOT run
    on SIGTERM. uvicorn's capture_signals()/handle_exit() does its own
    graceful HTTP shutdown, then RESTORES whatever signal disposition was
    active when it started and re-raises the captured signal via
    signal.raise_signal(). For SIGINT, Python's own built-in default handler
    turns that re-raise into a KeyboardInterrupt — a normal exception unwind
    that reaches atexit. SIGTERM has no such built-in Python handler, so
    without this, the restored disposition is the raw OS default (SIG_DFL,
    immediate kernel-level termination), killing the process before atexit
    ever runs and orphaning canvas+broadwayd. Registering this at module
    level (before uvicorn.run() is ever called, in both the console-script
    and a bare `uvicorn grc_agent.web:app` entry path) makes THIS the
    handler uvicorn treats as "the original" and restores before its
    re-raise, so it fires instead of SIG_DFL. Does not touch SIGINT, which
    already works correctly via the path described above."""
    _cleanup_procs()
    sys.exit(0)




# Ollama connection errors get pydantic_ai's own sanctioned retry-with-backoff
# transport instead of failing the whole turn on a transient hiccup.
_retrying_http_client = httpx.AsyncClient(
    transport=AsyncTenacityTransport(
        config=RetryConfig(
            retry=retry_if_exception_type(
                (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException)
            ),
            wait=wait_exponential(multiplier=1, max=10),
            stop=stop_after_attempt(3),
            reraise=True,
        )
    )
)

# 2. Build the Agent's model from the user's saved provider/model preference
# (see grc_agent.settings) — defaults to a local Ollama model, switchable to
# OpenRouter from the GUI settings panel. Changes take effect on next restart.
_cfg = load_settings()


def _build_model():
    if _cfg["provider"] == "openrouter":
        return OpenRouterModel(_cfg["model"])
    if _cfg["provider"] == "ollama_cloud":
        return OllamaModel(
            _cfg["model"],
            provider=OllamaProvider(
                base_url="https://ollama.com/v1",
                api_key=os.environ.get("OLLAMA_CLOUD_API_KEY", ""),
            ),
        )
    return OllamaModel(
        _cfg["model"],
        provider=OllamaProvider(base_url=OLLAMA_V1, http_client=_retrying_http_client),
    )


try:
    model = _build_model()
except Exception as e:
    # A saved provider/model config that fails at construction time (e.g.
    # OpenRouter selected with no OPENROUTER_API_KEY set — the settings
    # panel lets a user save that combination with no validation, and its
    # own success message invites an app restart) must not crash the whole
    # process at import — the dashboard and /grc/* API have nothing to do
    # with the chat model and should still come up. Fall back to the
    # documented Ollama defaults; /grc/settings still reports what's
    # actually saved (it re-reads load_settings() fresh), so the user can
    # see and fix the misconfiguration from the GUI, and the chat itself
    # will surface the real error on first use instead of the app never
    # starting at all.
    print(f"[grc-agent] Failed to build chat model from saved settings: {e}")
    print("[grc-agent] Falling back to Ollama defaults so the app can still start.")
    _cfg = default_settings()
    model = _build_model()
agent = Agent(
    model=model,
    deps_type=Any,
    output_type=[GrcAgentResponse, str],
    name="grc_web_chat_agent",
    instructions=build_system_prompt("pai-web-chat"),
    tools=grc_tools(),
    capabilities=[ProcessHistory(prune_history), StopGracefully(), web_search_cap, web_fetch_cap],
    model_settings=ModelSettings(extra_body={"think": True}),
    retries={'tools': 3, 'output': 3},
)
agent.output_validator(validate_flowgraph_state)

# Set base URL for Ollama provider discovery
os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"

# 3. Expose the agent via the built-in web chat Starlette application
# Exposes the tool calling, streaming responses, and real-time validations.
# Pass the already-built model object directly (not a "<provider>:<model>"
# string) because pydantic_ai's infer_provider doesn't know custom providers
# like ollama_cloud — the model object already has the right provider wired in.
app = agent.to_web(models=[model], deps=active)


# 4. GNU Radio side panel: load a .grc file, inspect blocks/params/connections.
# Routes live under /grc/* (two path segments) so they can't collide with
# to_web()'s own '/' and '/{id}' chat routes.
async def grc_inspect(request: Request) -> JSONResponse:
    if not active.is_loaded():
        return JSONResponse(
            {"ok": False, "not_loaded": True, "message": "No .grc file loaded yet."}
        )
    return JSONResponse(inspect_graph(active))


async def ensure_broadway() -> None:
    # Reclaim a previous run's orphan broadwayd on OUR port *before* the
    # port check — otherwise this connect succeeds against the orphan and we
    # silently reuse it, spawning a fresh canvas onto a display an orphan
    # canvas is still connected to (the "dual window" bug). Safe to call on
    # every open: it no-ops once our own broadwayd owns the pidfile.
    # Off the event loop: _reclaim_broadway_orphan() now waits for the old
    # process's confirmed death (see its own docstring for why a bare,
    # unwaited SIGTERM here let the port-connect check below race against a
    # dying-but-still-listening orphan and skip spawning a fresh one).
    await asyncio.to_thread(_reclaim_broadway_orphan)
    # A blocking socket connect + time.sleep(1.0) here used to stall the
    # entire server on every in-flight request (this app runs a single
    # uvicorn worker / single event loop) for a full second any time
    # broadwayd needed a cold start — e.g. after it crashed or was killed
    # mid-session. Non-blocking equivalents so a broadway cold-start only
    # delays this one request, not every concurrent one.
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", BROADWAY_PORT), timeout=0.5
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    except Exception:
        # Not running on our port — start one. No global `killall broadwayd`:
        # that would stomp another instance's broadwayd (e.g. a concurrent dev
        # session or the test suite on a different port). If our port is held
        # by a stale half-dead process, the start simply fails and is logged.
        # Off the event loop: _spawn_broadway's post-Popen bind-verification
        # (a brief sleep + poll, so a failed spawn doesn't overwrite the
        # pidfile with a dead PID) would otherwise block every concurrent
        # request for that long.
        await asyncio.to_thread(_spawn_broadway)
        await asyncio.sleep(1.0)  # give it a second to bind


def _terminate_canvas_proc() -> None:
    """Teardown of the tracked canvas subprocess only. Deliberately does NOT
    do a global `pkill -f canvas_app.py` — that would kill another instance's
    canvas (e.g. a concurrent dev session or the test suite). A stray
    orphan holding the control port is handled by _reclaim_canvas_orphan
    (called from grc_open) instead of a destructive sweep. Signals the whole
    process group (not just the leader PID) since canvas is spawned with
    preexec_fn=os.setsid."""
    global canvas_proc
    had_tracked = canvas_proc is not None
    if canvas_proc:
        try:
            os.killpg(os.getpgid(canvas_proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        try:
            canvas_proc.wait(timeout=2)
        except Exception:
            try:
                os.killpg(os.getpgid(canvas_proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            try:
                canvas_proc.wait(timeout=2)
            except Exception:
                pass
        canvas_proc = None
    if had_tracked:
        # Same reasoning as _terminate_broadway's unlink guard: only erase
        # the pidfile if this instance actually owned/killed what it refers
        # to — otherwise a normal shutdown of an instance that never called
        # /grc/open (so never tracked or reclaimed anything here) would
        # destroy the only record of an earlier, still-orphaned canvas,
        # permanently defeating _reclaim_canvas_orphan() for it.
        try:
            _canvas_pidfile().unlink(missing_ok=True)
        except OSError:
            pass


# Registered here — after every function _cleanup_procs transitively calls
# is already defined — rather than right below _handle_sigterm's own
# definition: if something later in this module's import (e.g. building the
# chat Agent's model from a bad saved config) were to raise, atexit would
# still fire during interpreter shutdown, but only against fully-defined
# dependencies — registering earlier once left a window where a partial
# import failure made _cleanup_procs's own call to a not-yet-defined
# _terminate_canvas_proc raise a second, unrelated NameError on top of the
# real error, obscuring it (live-reproduced).
#
# signal.signal() only works from the main thread of the main interpreter —
# guard it (mirroring uvicorn's own capture_signals) so importing this
# module from a worker thread (e.g. an embedding harness) doesn't crash at
# import time. Both real entry paths (the grc-agent-web console script and
# a bare `uvicorn grc_agent.web:app`) import on the main thread, so this is
# a no-op there and only prevents the non-main-thread case.
if threading.current_thread() is threading.main_thread():
    signal.signal(signal.SIGTERM, _handle_sigterm)

# Covers every entry path (main()'s uvicorn and a direct
# `uvicorn grc_agent.web:app`). Runs on normal interpreter shutdown —
# Ctrl+C (SIGINT), via Python's built-in default handler raising
# KeyboardInterrupt — and on SIGTERM, via the explicit handler registered
# just above (which calls _cleanup_procs itself and then exits; _cleanup_procs
# is idempotent, so atexit invoking it again here too is harmless). A SIGKILL
# crash is recovered by the pidfile reclaim on the next launch.
atexit.register(_cleanup_procs)


async def grc_open(request: Request) -> JSONResponse:
    global active_path, canvas_proc, canvas_ready_state, canvas_error_state
    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse({"ok": False, "message": f"Invalid JSON body: {e}"}, status_code=400)
    path = str(body.get("path", "")).strip()
    if not path:
        return JSONResponse({"ok": False, "message": "path is required"}, status_code=400)
    if not Path(path).is_absolute():
        path = str(Path.cwd() / path)

    canvas_ready = False
    canvas_error: str | None = None
    canvas_launched = False

    # Serializes against concurrent /grc/open and /grc/close calls — see
    # _flowgraph_state_lock's own comment for why this matters. Released
    # before the (up to 20s) canvas-readiness wait below: that poll only
    # talks to a separate process over a fixed control port and touches no
    # shared in-process state, so holding the lock across it would stall
    # every other operation that serializes on this same lock (agent tool
    # calls via FlowgraphProxy.get_state_lock(), /grc/close, /grc/reload,
    # /grc/render) for the full timeout whenever a single canvas is
    # slow/crashed — live-reproduced pre-fix.
    async with _flowgraph_state_lock:
        try:
            new_fg = load_flow_graph(path)
        except Exception as e:
            return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
        active.swap(new_fg)
        active_path = path

        # Ensure broadwayd is running
        await ensure_broadway()

        # Reclaim an orphan from a crashed/killed prior run of this process
        # (nothing tracked in canvas_proc this session, but a stale one may
        # still hold our control port) BEFORE terminating/unlinking below —
        # _terminate_canvas_proc unconditionally unlinks the pidfile at its
        # end (mirroring _terminate_broadway), so calling it first would
        # destroy the only record of a stale orphan's PID before this
        # function ever got to read it.
        await asyncio.to_thread(_reclaim_canvas_orphan)
        # Terminate any previously running canvas process (tracked or stray).
        # Off the event loop: canvas_proc.wait() can block up to its timeout.
        await asyncio.to_thread(_terminate_canvas_proc)

        # Launch new canvas process under Broadway
        env = os.environ.copy()
        env["GDK_BACKEND"] = "broadway"
        # Must match the display number _spawn_broadway() started broadwayd
        # on (derived from BROADWAY_PORT, not a hardcoded number) — otherwise
        # this canvas would connect to a different broadwayd's socket.
        env["BROADWAY_DISPLAY"] = f":{BROADWAY_PORT}"
        try:
            # Silently swallowing this process's output makes a silent GTK/Broadway
            # crash indistinguishable from a slow load — route it to a log file
            # next to the flowgraph (same .grc_agent convention as backups/locks)
            # so it's inspectable after the fact. Truncated (not appended) on
            # every launch — this is a debug log for the current run, not a
            # permanent record, and appending forever grows it unbounded.
            log_dir = Path(path).parent / ".grc_agent"
            log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            with open(log_dir / "canvas.log", "wb") as canvas_log:
                canvas_proc = subprocess.Popen(
                    [
                        sys.executable, "-u",
                        str(Path(__file__).parent / "canvas_app.py"),
                        path,
                        str(CANVAS_CONTROL_PORT),
                        str(GRC_AGENT_PORT),
                    ],
                    env=env,
                    stdout=canvas_log,
                    stderr=canvas_log,
                    preexec_fn=os.setsid
                )
            canvas_launched = True
            print(f"[grc-agent] Started GRC Broadway canvas app for: {path}")
            # Record the PID immediately so a subsequent launch (after a
            # crash/SIGKILL orphans this one) can reclaim it — mirrors
            # _spawn_broadway's own pidfile write. Isolated in its own
            # try/except: a write failure here (e.g. tempdir full) shouldn't
            # be conflated with a canvas-launch failure in canvas_error (the
            # canvas is still up and usable this session), just logged
            # distinctly — cross-session reclaimability is what's lost.
            try:
                _canvas_pidfile().write_text(str(canvas_proc.pid))
            except OSError as e:
                print(
                    f"[grc-agent] Failed to write canvas pidfile for pid "
                    f"{canvas_proc.pid} (won't be reclaimable if this process "
                    f"crashes before a clean shutdown): {e}"
                )
        except Exception as e:
            canvas_error = str(e)
            print(f"[grc-agent] Failed to start GRC Broadway canvas app: {e}")

    if canvas_launched:
        # Wait for the canvas to signal readiness (GTK client connected to
        # the Broadway display) before returning — otherwise the dashboard
        # points the Broadway iframe at a display with no GTK client yet,
        # and broadway.js fires an unrecoverable alert("disconnected"). The
        # control server binds before the heavy platform build, so the
        # probe gets 503 (not connection-refused) until /ready flips to 200.
        # Outside the lock (see above) — a concurrent /grc/open racing this
        # wait could terminate/replace this canvas mid-poll; that's
        # acceptable last-writer-wins behavior (the poll would then just
        # reflect whichever canvas currently holds the control port).
        canvas_ready, canvas_crashed = await _wait_for_canvas_ready(canvas_proc)
        if not canvas_ready:
            canvas_error = (
                "Canvas process exited unexpectedly — see canvas.log"
                if canvas_crashed
                else "Timed out waiting for canvas to become ready"
            )

    # Mirrored into module state (not just this response) so a later /grc/status
    # poll can still learn the canvas is unavailable — see canvas_ready_state's
    # own comment. Covers all three outcomes above: launch failed outright,
    # launched but crashed/timed out, or launched and became ready.
    canvas_ready_state = canvas_ready
    canvas_error_state = canvas_error

    return JSONResponse(
        {
            "ok": True,
            "path": path,
            # The flowgraph itself did load (hence ok=true above) even when
            # the canvas failed/timed out — these two fields give the caller
            # something to act on instead of silent success either way.
            "canvas_ready": canvas_ready,
            "canvas_error": canvas_error,
        }
    )


async def _wait_for_canvas_ready(
    proc: subprocess.Popen | None, deadline_s: float = CANVAS_READY_TIMEOUT
) -> tuple[bool, bool]:
    """Poll the canvas control server's /ready endpoint until it reports 200
    (Gtk.main pumping) or the deadline expires. Also checks the subprocess's
    own exit status each iteration — an uncaught exception during GRC's
    platform build (canvas_app.py's get_gui_platform()/Application(...) calls
    have no try/except around them) or its own sys.exit(1) when it can't find
    a MainWindow would otherwise still cost the caller the full deadline
    before reporting the same generic timeout as a merely-slow canvas.
    Returns (ready, crashed) so the caller (grc_open) can distinguish a
    dead-on-arrival process from one that simply never became ready in time,
    instead of reporting success regardless of the outcome."""
    loop = asyncio.get_running_loop()
    end = loop.time() + deadline_s
    url = f"http://127.0.0.1:{CANVAS_CONTROL_PORT}/ready"
    async with httpx.AsyncClient(timeout=0.5) as client:
        while loop.time() < end:
            if proc is not None and proc.poll() is not None:
                return False, True
            try:
                if (await client.get(url)).status_code == 200:
                    return True, False
            except Exception:
                pass
            await asyncio.sleep(0.2)
    return False, False


async def grc_status(request: Request) -> JSONResponse:
    undo_state = undo_status(active_path) if active_path else {"can_undo": False, "can_redo": False}
    return JSONResponse(
        {
            "ok": True,
            "path": active_path,
            "version": active.get_version(),
            # Surfaced so the dashboard doesn't hardcode the Broadway URL —
            # the port is env-overridable for multi-instance coexistence.
            "broadway_url": f"http://localhost:{BROADWAY_PORT}/",
            "can_undo": undo_state["can_undo"],
            "can_redo": undo_state["can_redo"],
            # The last-known outcome for the active canvas — see
            # canvas_ready_state's own comment for why this can't just be a
            # one-shot field on /grc/open's response.
            "canvas_ready": canvas_ready_state,
            "canvas_error": canvas_error_state,
        }
    )


async def grc_close(request: Request) -> JSONResponse:
    global active_path, canvas_ready_state, canvas_error_state
    async with _flowgraph_state_lock:
        active.swap(None)
        active_path = None
        canvas_ready_state = True
        canvas_error_state = None
        await asyncio.to_thread(_terminate_canvas_proc)
    return JSONResponse({"ok": True})


GRC_EXTENSIONS = (".grc", ".yml", ".yaml")


async def grc_browse(request: Request) -> JSONResponse:
    """List a server-side directory so the panel can offer a real
    filesystem browse dialog. A browser can never hand back an absolute
    path from its own file picker (that's sandboxed by design), but this
    server runs on the same machine as the files, so browsing server-side
    and loading by the real path it already knows is the actual analog of
    the old desktop GUI's native QFileDialog."""
    requested = request.query_params.get("dir") or (
        str(Path(active_path).parent) if active_path else str(Path.cwd())
    )
    directory = Path(requested).resolve()
    if not directory.is_dir():
        return JSONResponse(
            {"ok": False, "message": f"Not a directory: {directory}"}, status_code=400
        )

    entries = []
    for child in directory.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_dir():
            entries.append({"name": child.name, "path": str(child), "is_dir": True})
        elif child.suffix.lower() in GRC_EXTENSIONS:
            entries.append({"name": child.name, "path": str(child), "is_dir": False})
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))

    parent = str(directory.parent) if directory.parent != directory else None
    return JSONResponse({"ok": True, "dir": str(directory), "parent": parent, "entries": entries})


async def grc_render(request: Request) -> Response:
    # Snapshot the path while holding the same lock open/close/reload use —
    # without it, a concurrent close/open could change the target file out
    # from under an in-flight render. The slow subprocess render itself runs
    # outside the lock so it doesn't block other requests.
    async with _flowgraph_state_lock:
        path = active_path
    if not path:
        return Response("No .grc file loaded", status_code=400)

    cmd = [
        sys.executable,
        "-c",
        """
import sys
import cairo
from grc_agent.adapter import get_gui_platform, gui_application_cls

# Reuse the adapter's own GUI Platform/Application accessors instead of
# hand-duplicating what they already do (build_library(), version/prefix
# wiring) — this subprocess is still the sole importer of gnuradio here,
# just via adapter.py rather than re-deriving it inline.
p = get_gui_platform()
app_cls = gui_application_cls()
app = app_cls([], p)
app.register(None)
app.activate()

fg = p.make_flow_graph(sys.argv[1])
fg.update_elements_to_draw()

temp_surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
temp_cr = cairo.Context(temp_surf)
fg.create_labels(temp_cr)
fg.create_shapes()

x1, y1, x2, y2 = fg.get_extents()
padding = 30
width = int(x2 - x1 + 2 * padding)
height = int(y2 - y1 + 2 * padding)

width = max(10, min(width, 5000))
height = max(10, min(height, 5000))

surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
cr = cairo.Context(surf)
cr.set_source_rgb(1.0, 1.0, 1.0)
cr.rectangle(0, 0, width, height)
cr.fill()

cr.translate(-x1 + padding, -y1 + padding)
fg.create_labels(cr)
fg.create_shapes()
fg.draw(cr)

surf.write_to_png(sys.stdout.buffer)
""",
        path,
    ]

    try:
        # Off the event loop: this spawns a fresh Python that imports gnuradio
        # and builds the whole GUI platform + cairo render — multi-second work
        # that would freeze every concurrent request on the single worker.
        proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, check=True)
        return Response(proc.stdout, media_type="image/png")
    except subprocess.CalledProcessError as e:
        # check=True raises before `proc =` above ever binds, so the failing
        # process's own captured stderr has to come from the exception object
        # itself, not a local `proc` (a bare "if 'proc' in locals()" guard
        # here was always False on this exact path and always fell through
        # to the less useful str(e)).
        stderr_msg = e.stderr.decode() if e.stderr else str(e)
        return Response(f"Rendering failed: {stderr_msg}", status_code=500)
    except Exception as e:
        return Response(f"Rendering failed: {e}", status_code=500)


async def grc_panel(request: Request) -> HTMLResponse:
    panel_html = (Path(__file__).parent / "panel.html").read_text(encoding="utf-8")
    return HTMLResponse(panel_html)


async def grc_panel_js(request: Request) -> Response:
    # The dashboard's logic lives in panel.js (extracted from panel.html).
    # no-cache: the file is patched across deploys without a content hash in
    # its name, so a stale browser cache would silently run an old version.
    panel_js = (Path(__file__).parent / "panel.js").read_text(encoding="utf-8")
    return Response(
        panel_js, media_type="text/javascript", headers={"Cache-Control": "no-cache"}
    )


async def grc_reload(request: Request) -> JSONResponse:
    global active_path, canvas_ready_state, canvas_error_state
    if not active_path:
        return JSONResponse({"ok": False, "message": "No active path"}, status_code=400)
    # Same shared state as open/close — serialize against them so a reload
    # can't interleave with a concurrent open/close swap.
    async with _flowgraph_state_lock:
        try:
            new_fg = load_flow_graph(active_path)
            active.swap(new_fg)
            # Tell the live GTK canvas to reload too — otherwise a disk-reload
            # here leaves it stale relative to the freshly-reloaded in-memory
            # graph, the same desync change_graph already guards against.
            # notify_edit() never raises (its own try/except folds a failure
            # into the returned dict), so this can't turn a reload into a
            # failure response — best-effort, surfaced but non-fatal.
            canvas_synced = (await active.notify_edit()).get("ok", False)
            # A successful reload ping proves the canvas control server is
            # alive and responding. If a prior open had timed out, this means
            # the canvas recovered (or was slow, not dead), so mirror that
            # into /grc/status instead of leaving a stale "canvas failed" error
            # permanently stuck.
            if canvas_synced:
                canvas_ready_state = True
                canvas_error_state = None
            return JSONResponse({"ok": True, "canvas_synced": canvas_synced})
        except Exception as e:
            return JSONResponse({"ok": False, "message": str(e)}, status_code=400)


async def _grc_undo_redo(op) -> JSONResponse:
    """Shared body for /grc/undo and /grc/redo — op is adapter.undo_flowgraph
    or adapter.redo_flowgraph. Neither touches any in-memory flow_graph
    object (they're pure disk operations, see their own docstrings), so
    this reloads from disk afterward exactly like grc_reload already does
    for a canvas-triggered disk change."""
    global active_path, canvas_ready_state, canvas_error_state
    if not active_path:
        return JSONResponse({"ok": False, "message": "No active path"}, status_code=400)
    async with _flowgraph_state_lock:
        # Off the event loop: op() does blocking file I/O under fcntl.flock.
        result = await asyncio.to_thread(op, active_path)
        if not result.get("ok"):
            return JSONResponse(result, status_code=400)
        try:
            new_fg = load_flow_graph(active_path)
            active.swap(new_fg)
            canvas_synced = (await active.notify_edit()).get("ok", False)
            # See grc_reload's matching block: a successful reload ping means
            # the canvas process recovered from an earlier timeout/crash and
            # should no longer be reported as permanently failed.
            if canvas_synced:
                canvas_ready_state = True
                canvas_error_state = None
        except Exception as e:
            return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
        return JSONResponse(
            {
                "ok": True,
                "can_undo": result["can_undo"],
                "can_redo": result["can_redo"],
                "canvas_synced": canvas_synced,
            }
        )


async def grc_undo(request: Request) -> JSONResponse:
    return await _grc_undo_redo(undo_flowgraph)


async def grc_redo(request: Request) -> JSONResponse:
    return await _grc_undo_redo(redo_flowgraph)


async def grc_canvas_resize(request: Request) -> JSONResponse:
    """Forward the dashboard's actual canvas-pane size to the running
    canvas_app.py process so its GTK window matches it exactly instead of a
    fixed guess — see canvas_app.py's start_resize_server for why a size
    mismatch there both clips the flowgraph and pushes its scrollbars
    outside the visible iframe viewport."""
    try:
        body = await request.json()
        width = int(body.get("width", 0))
        height = int(body.get("height", 0))
    except Exception:
        return JSONResponse({"ok": False, "message": "invalid body"}, status_code=400)
    if width <= 0 or height <= 0:
        return JSONResponse(
            {"ok": False, "message": "width/height must be positive"}, status_code=400
        )
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            r = await client.post(
                f"http://127.0.0.1:{CANVAS_CONTROL_PORT}/resize",
                json={"width": width, "height": height},
            )
        return JSONResponse({"ok": 200 <= r.status_code < 300})
    except Exception:
        # No canvas process listening (not started yet, or closed) — report
        # the real outcome instead of pretending success, so a desynced
        # canvas size is diagnosable rather than silent. The dashboard
        # ignores this body, so a false here is harmless to the UI.
        return JSONResponse({"ok": False, "message": "canvas control server unreachable"})


async def grc_settings_get(request: Request) -> JSONResponse:
    cfg = load_settings()
    return JSONResponse(
        {
            "ok": True,
            "provider": cfg["provider"],
            "model": cfg["model"],
            "ollama_model": cfg.get("ollama_model"),
            "openrouter_model": cfg.get("openrouter_model"),
            "ollama_cloud_model": cfg.get("ollama_cloud_model"),
            "openrouter_api_key_set": bool(os.getenv("OPENROUTER_API_KEY")),
            "ollama_cloud_api_key_set": bool(os.getenv("OLLAMA_CLOUD_API_KEY")),
            # What the running chat Agent's model ACTUALLY is right now — built
            # once from _cfg at import (see _build_model()'s fallback comment)
            # and never live-swapped. Distinct from provider/model above (the
            # saved-to-disk preference) so the dashboard can tell a saved
            # change apart from one that's actually taken effect, rather than
            # a save just silently appearing to do nothing until the user
            # happens to restart and notice.
            "active_provider": _cfg["provider"],
            "active_model": _cfg["model"],
        }
    )


async def grc_settings_post(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse({"ok": False, "message": f"Invalid JSON body: {e}"}, status_code=400)
    try:
        save_settings(str(body.get("provider", "")), str(body.get("model", "")))
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "message": "Saved. Restart the app to use this model."})


async def grc_health(request: Request) -> JSONResponse:
    """Check connectivity for the currently selected provider. Async so it
    never blocks the event loop — the dashboard calls this on load and on
    provider switch to show a live status indicator."""
    cfg = load_settings()
    provider = cfg["provider"]
    result: dict[str, Any] = {"provider": provider, "ok": False, "message": ""}

    if provider == "ollama":
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get("http://localhost:11434/api/tags")
                if r.status_code == 200:
                    result["ok"] = True
                    result["message"] = "Ollama server is running"
                else:
                    result["message"] = f"Ollama returned status {r.status_code}"
        except httpx.ConnectError:
            result["message"] = "Ollama server is not running. Start it with: ollama serve"
        except Exception as e:
            result["message"] = f"Ollama check failed: {e}"
    elif provider == "ollama_cloud":
        key = os.environ.get("OLLAMA_CLOUD_API_KEY", "")
        if not key:
            result["message"] = "OLLAMA_CLOUD_API_KEY is not set. Click the key button to set it."
        else:
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    r = await client.get(
                        "https://ollama.com/v1/models",
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    if r.status_code == 200:
                        result["ok"] = True
                        result["message"] = "Ollama Cloud is reachable"
                    else:
                        result["message"] = f"Ollama Cloud returned status {r.status_code}"
            except httpx.ConnectError:
                result["message"] = "Cannot reach Ollama Cloud (https://ollama.com)"
            except Exception as e:
                result["message"] = f"Ollama Cloud check failed: {e}"
    elif provider == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            result["message"] = "OPENROUTER_API_KEY is not set. Click the key button to set it."
        else:
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    r = await client.get(
                        "https://openrouter.ai/api/v1/models",
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    if r.status_code == 200:
                        result["ok"] = True
                        result["message"] = "OpenRouter is reachable"
                    else:
                        result["message"] = f"OpenRouter returned status {r.status_code}"
            except httpx.ConnectError:
                result["message"] = "Cannot reach OpenRouter (https://openrouter.ai)"
            except Exception as e:
                result["message"] = f"OpenRouter check failed: {e}"

    return JSONResponse(result)


async def grc_apikey_post(request: Request) -> JSONResponse:
    """Write an API key to .env for the given provider. The .env file is
    read at startup, so a restart is required for the new key to take effect."""
    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse({"ok": False, "message": f"Invalid JSON body: {e}"}, status_code=400)

    provider = str(body.get("provider", ""))
    api_key = str(body.get("api_key", "")).strip()
    if provider not in ("ollama_cloud", "openrouter"):
        return JSONResponse(
            {"ok": False, "message": f"Unknown provider: {provider!r}"},
            status_code=400,
        )
    if not api_key:
        return JSONResponse({"ok": False, "message": "API key must be non-empty"}, status_code=400)

    env_key = "OLLAMA_CLOUD_API_KEY" if provider == "ollama_cloud" else "OPENROUTER_API_KEY"
    env_path = Path(".env")
    if not env_path.exists():
        env_path.write_text(f"{env_key}={api_key}\n", encoding="utf-8")
    else:
        content = env_path.read_text(encoding="utf-8")
        if re.search(rf"^{re.escape(env_key)}=.*", content, re.MULTILINE):
            content = re.sub(
                rf"^{re.escape(env_key)}=.*", f"{env_key}={api_key}", content, flags=re.MULTILINE
            )
        else:
            content += f"\n{env_key}={api_key}\n"
        env_path.write_text(content, encoding="utf-8")

    # Also set in the current process so the health check can see it immediately
    os.environ[env_key] = api_key

    return JSONResponse(
        {"ok": True, "message": f"{env_key} saved. Restart the app for it to take effect."}
    )


# to_web() registered its own chat UI at '/' (and '/{id}'), fetched at runtime
# from the @pydantic/ai-chat-ui CDN bundle, which hardcodes "Pydantic AI" as
# its page title and sidebar label. There's no source to fork (only the
# minified build is published), so relabeling it means proxying the CDN
# assets ourselves and patching that one string in transit.
#
# The bundle also hardcodes an assumption that it owns the site root: it
# reads window.location.pathname itself and treats exactly "/" as its
# "show a fresh conversation" sentinel (confirmed by inspecting the vendor
# JS directly — any other pathname makes it look up a nonexistent
# conversation and silently render nothing, no error). So '/' must stay
# the chat widget's own route — the GNU Radio dashboard lives at
# /grc/panel instead, and the iframe in panel.html points at '/'.
_original_index_route = next(r for r in app.router.routes if getattr(r, "path", None) == "/")
_original_catchall_route = next(r for r in app.router.routes if getattr(r, "path", None) == "/{id}")
_original_index_endpoint = _original_index_route.endpoint
app.router.routes.remove(_original_index_route)
app.router.routes.remove(_original_catchall_route)

CHAT_UI_CDN_BASE = "https://cdn.jsdelivr.net/npm/@pydantic/ai-chat-ui/dist"
_asset_cache: dict[str, bytes] = {}


async def chat_ui_index(request: Request) -> HTMLResponse:
    original = await _original_index_endpoint(request)
    html = original.body.decode("utf-8")
    html = html.replace(CHAT_UI_CDN_BASE + "/", "/chat-ui-assets/")
    html = html.replace("Pydantic AI", BRAND_NAME)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


async def chat_ui_asset(request: Request) -> Response:
    rel_path = request.path_params["path"]
    url = f"{CHAT_UI_CDN_BASE}/{rel_path}"
    if url not in _asset_cache:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content = resp.content
        except httpx.HTTPError as e:
            return Response(
                f"// Failed to load {rel_path} from CDN: {e}",
                media_type="application/javascript",
                status_code=502,
            )
        if rel_path.endswith((".js", ".css")):
            content = content.replace(b"Pydantic AI", BRAND_NAME.encode())
        _asset_cache[url] = content
    media_type = mimetypes.guess_type(rel_path)[0] or "application/octet-stream"
    # These filenames are content-hashed by the upstream build, but our own
    # patch can change their bytes across deploys without the name changing
    # — never let the browser treat them as long-lived/immutable.
    return Response(_asset_cache[url], media_type=media_type, headers={"Cache-Control": "no-cache"})


# Inserted at the front (not appended) so these are matched before any
# remaining to_web() routes.
app.router.routes[0:0] = [
    Route("/", chat_ui_index, methods=["GET"]),
    Route("/{conv_id}", chat_ui_index, methods=["GET"]),
    Route("/chat-ui-assets/{path:path}", chat_ui_asset, methods=["GET"]),
    Route("/grc/inspect", grc_inspect, methods=["GET"]),
    Route("/grc/open", grc_open, methods=["POST"]),
    Route("/grc/status", grc_status, methods=["GET"]),
    Route("/grc/close", grc_close, methods=["POST"]),
    Route("/grc/browse", grc_browse, methods=["GET"]),
    Route("/grc/render", grc_render, methods=["GET"]),
    Route("/grc/reload", grc_reload, methods=["GET", "POST"]),
    Route("/grc/undo", grc_undo, methods=["POST"]),
    Route("/grc/redo", grc_redo, methods=["POST"]),
    Route("/grc/canvas/resize", grc_canvas_resize, methods=["POST"]),
    Route("/grc/panel", grc_panel, methods=["GET"]),
    Route("/grc/panel.js", grc_panel_js, methods=["GET"]),
    Route("/grc/settings", grc_settings_get, methods=["GET"]),
    Route("/grc/settings", grc_settings_post, methods=["POST"]),
    Route("/grc/health", grc_health, methods=["GET"]),
    Route("/grc/apikey", grc_apikey_post, methods=["POST"]),
]


def main() -> None:
    import socket
    import webbrowser

    import uvicorn

    # Eagerly start broadwayd on our port (lazy startup also covers this in
    # ensure_broadway, but starting here avoids the first /grc/open paying
    # the cold-start delay). No global pkill/killall sweep: those would
    # stomp another instance's processes (e.g. a concurrent dev session or
    # the test suite on different ports). Tracked termination + canvas_app's
    # control-port bind-retry handle orphans from a crashed previous run.
    # ensure_broadway() (used by /grc/open) checks whether one is already
    # listening before spawning — do the same connect-first check here
    # (blocking is fine: the event loop hasn't started yet) so a broadwayd
    # already healthily serving the port isn't wastefully (and riskily —
    # a second spawn attempt on an already-served display would just fail
    # to bind) replaced by a second one.
    broadway_already_running = False
    try:
        with socket.create_connection(("127.0.0.1", BROADWAY_PORT), timeout=0.5):
            broadway_already_running = True
    except OSError:
        pass
    if not broadway_already_running:
        _spawn_broadway()

    host = os.environ.get("GRC_AGENT_HOST", "127.0.0.1")
    url = f"http://{host}:{GRC_AGENT_PORT}/grc/panel"

    def open_browser() -> None:
        time.sleep(1.0)
        print(f"\n[grc-agent] Opening dashboard at: {url}\n")
        webbrowser.open(url)

    # Start the daemon thread so it doesn't block startup
    threading.Thread(target=open_browser, daemon=True).start()

    print(f"\n[grc-agent] Starting server. Dashboard URL: {url}\n")
    uvicorn.run(app, host=host, port=GRC_AGENT_PORT)


if __name__ == "__main__":
    main()
