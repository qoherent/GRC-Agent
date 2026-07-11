import asyncio
import atexit
import mimetypes
import os
import signal
import subprocess
import sys
import tempfile
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
from grc_agent.adapter import inspect_graph, load_flow_graph
from grc_agent.agent import (
    OLLAMA_V1,
    GrcAgentResponse,
    StopGracefully,
    build_system_prompt,
    grc_tools,
    prune_history,
    validate_flowgraph_state,
    web_fetch_cap,
    web_search_cap,
)
from grc_agent.settings import load_settings, save_settings

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

    def __init__(self, flowgraph: Any = None, on_edit: Any = None) -> None:
        object.__setattr__(self, "_target", flowgraph)
        object.__setattr__(self, "_version", 0)
        object.__setattr__(self, "_on_edit", on_edit)

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


# 1. No flowgraph is loaded at startup — the user must Browse and choose one.
active = FlowgraphProxy(None, on_edit=_notify_canvas_reload)
active_path: str | None = None
canvas_proc: subprocess.Popen | None = None
# broadwayd daemons this process spawned (eagerly in main(), lazily in
# ensure_broadway) — tracked so teardown terminates only OUR daemons, never
# another instance's (which a global `killall broadwayd` would stomp).
broadway_procs: list[subprocess.Popen] = []
# Guards every mutation of active/active_path/canvas_proc — without it,
# two concurrent /grc/open (or an open racing a close) calls interleave
# their terminate-old/spawn-new sequences non-deterministically; each
# still reports {"ok": true} to its own caller, but only one's file
# actually ends up loaded. Serializing makes the outcome deterministic
# (last request in wins) instead of a silent race.
_flowgraph_state_lock = asyncio.Lock()


def _broadway_pidfile() -> Path:
    """Port-scoped PID file so a restart can reclaim THIS port's stale
    broadwayd without a global killall (multi-instance safe: each port gets
    its own file)."""
    return Path(tempfile.gettempdir()) / f"grc_agent_broadway_{BROADWAY_PORT}.pid"


def _proc_comm(pid: int) -> str | None:
    """Identity check via /proc (Linux) — safe against PID reuse: a PID is
    only treated as a reclaimable broadwayd if its comm still matches."""
    try:
        return (Path("/proc") / str(pid) / "comm").read_text().strip()
    except OSError:
        return None


def _reclaim_broadway_orphan() -> None:
    """Kill a *previous* run's broadwayd still holding our port. Without this,
    a restart reuses the orphan broadwayd while spawning a fresh canvas ->
    two GTK windows on the same Broadway display (the 'dual window' bug).
    Skips PIDs this process spawned (tracked in broadway_procs) and any PID
    whose comm no longer looks like broadwayd (i.e. reused by an unrelated
    process), so it never kills the wrong thing."""
    try:
        old_pid = int(_broadway_pidfile().read_text().strip())
    except (OSError, ValueError):
        return
    if old_pid in {p.pid for p in broadway_procs}:
        return  # ours, still alive this session — keep it
    comm = _proc_comm(old_pid)
    if not comm or "broadway" not in comm:
        return  # process is gone, or PID reused by something unrelated
    try:
        os.kill(old_pid, signal.SIGTERM)
        print(f"[grc-agent] Reclaimed stale broadwayd (pid {old_pid}) on port {BROADWAY_PORT}")
    except OSError:
        pass


def _spawn_broadway() -> None:
    """Start broadwayd on BROADWAY_PORT, track it for scoped teardown, and
    record its PID so the next launch can reclaim it if this run crashes."""
    _reclaim_broadway_orphan()
    try:
        proc = subprocess.Popen(
            ["broadwayd", "-p", str(BROADWAY_PORT), ":5"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid,
        )
        broadway_procs.append(proc)
        _broadway_pidfile().write_text(str(proc.pid))
        print(f"[grc-agent] Started broadwayd daemon on port {BROADWAY_PORT} display :5")
    except Exception as e:
        print("[grc-agent] Failed to start broadwayd daemon:", e)


def _terminate_broadway() -> None:
    """Terminate only the broadwayd daemons this process spawned."""
    for proc in broadway_procs:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    broadway_procs.clear()
    try:
        _broadway_pidfile().unlink(missing_ok=True)
    except OSError:
        pass


def _cleanup_procs() -> None:
    """Reap tracked subprocesses (broadwayd + canvas) on shutdown so they
    don't orphan across restarts. Idempotent."""
    _terminate_canvas_proc()
    _terminate_broadway()


# Registered at import so it covers every entry path (main()'s uvicorn and a
# direct `uvicorn grc_agent.web:app`). Runs on normal interpreter shutdown
# (Ctrl+C / SIGTERM caught by uvicorn); a SIGKILL crash is recovered by the
# pidfile reclaim on the next launch.
atexit.register(_cleanup_procs)


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
    return OllamaModel(
        _cfg["model"],
        provider=OllamaProvider(base_url=OLLAMA_V1, http_client=_retrying_http_client),
    )


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
)
agent.output_validator(validate_flowgraph_state)

# Set base URL for Ollama provider discovery
os.environ["OLLAMA_BASE_URL"] = "http://localhost:11434"

# 3. Expose the agent via the built-in web chat Starlette application
# Exposes the tool calling, streaming responses, and real-time validations.
app = agent.to_web(models=[f"{_cfg['provider']}:{_cfg['model']}"], deps=active)


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
    _reclaim_broadway_orphan()
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
        _spawn_broadway()
        await asyncio.sleep(1.0)  # give it a second to bind


def _terminate_canvas_proc() -> None:
    """Teardown of the tracked canvas subprocess only. Deliberately does NOT
    do a global `pkill -f canvas_app.py` — that would kill another instance's
    canvas (e.g. a concurrent dev session or the test suite). A stray
    orphan holding the control port is handled by canvas_app.py's own
    bind-retry instead of a destructive sweep."""
    global canvas_proc
    if canvas_proc:
        try:
            canvas_proc.terminate()
            canvas_proc.wait(timeout=2)
        except Exception:
            try:
                canvas_proc.kill()
            except Exception:
                pass
        canvas_proc = None


async def grc_open(request: Request) -> JSONResponse:
    global active_path, canvas_proc
    body = await request.json()
    path = str(body.get("path", "")).strip()
    if not path:
        return JSONResponse({"ok": False, "message": "path is required"}, status_code=400)
    if not Path(path).is_absolute():
        path = str(Path.cwd() / path)

    # Serializes against concurrent /grc/open and /grc/close calls — see
    # _flowgraph_state_lock's own comment for why this matters.
    async with _flowgraph_state_lock:
        try:
            new_fg = load_flow_graph(path)
        except Exception as e:
            return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
        active.swap(new_fg)
        active_path = path

        # Ensure broadwayd is running
        await ensure_broadway()

        # Terminate any previously running canvas process (tracked or stray).
        # Off the event loop: canvas_proc.wait() can block up to its timeout.
        await asyncio.to_thread(_terminate_canvas_proc)

        # Launch new canvas process under Broadway
        env = os.environ.copy()
        env["GDK_BACKEND"] = "broadway"
        env["BROADWAY_DISPLAY"] = ":5"
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
            print(f"[grc-agent] Started GRC Broadway canvas app for: {path}")
            # Wait for the canvas to signal readiness (GTK client connected to
            # the Broadway display) before returning — otherwise the dashboard
            # points the Broadway iframe at a display with no GTK client yet,
            # and broadway.js fires an unrecoverable alert("disconnected").
            # The control server binds before the heavy platform build, so the
            # probe gets 503 (not connection-refused) until /ready flips to 200.
            await _wait_for_canvas_ready()
        except Exception as e:
            print(f"[grc-agent] Failed to start GRC Broadway canvas app: {e}")

    return JSONResponse({"ok": True, "path": path})


async def _wait_for_canvas_ready(deadline_s: float = 20.0) -> None:
    """Poll the canvas control server's /ready endpoint until it reports 200
    (Gtk.main pumping) or the deadline expires. Best-effort: a timeout only
    means the dashboard may show the Broadway client connecting slightly
    before the canvas draws — it does not fail the open."""
    loop = asyncio.get_event_loop()
    end = loop.time() + deadline_s
    url = f"http://127.0.0.1:{CANVAS_CONTROL_PORT}/ready"
    async with httpx.AsyncClient(timeout=0.5) as client:
        while loop.time() < end:
            try:
                if (await client.get(url)).status_code == 200:
                    return
            except Exception:
                pass
            await asyncio.sleep(0.2)


async def grc_status(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "path": active_path,
            "version": active.get_version(),
            # Surfaced so the dashboard doesn't hardcode the Broadway URL —
            # the port is env-overridable for multi-instance coexistence.
            "broadway_url": f"http://localhost:{BROADWAY_PORT}/",
        }
    )


async def grc_close(request: Request) -> JSONResponse:
    global active_path
    async with _flowgraph_state_lock:
        active.swap(None)
        active_path = None
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
    global active_path
    if not active_path:
        return Response("No .grc file loaded", status_code=400)

    cmd = [
        sys.executable,
        "-c",
        """
import sys
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk
import cairo
from gnuradio import gr
from gnuradio.grc.gui.Platform import Platform
from gnuradio.grc.gui.Application import Application

p = Platform(
    version=gr.version(),
    version_parts=(gr.major_version(), gr.api_version(), gr.minor_version()),
    prefs=gr.prefs(),
    install_prefix=gr.prefix()
)
p.build_library()
app = Application([], p)
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
        active_path,
    ]

    try:
        # Off the event loop: this spawns a fresh Python that imports gnuradio
        # and builds the whole GUI platform + cairo render — multi-second work
        # that would freeze every concurrent request on the single worker.
        proc = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, check=True)
        return Response(proc.stdout, media_type="image/png")
    except Exception as e:
        stderr_msg = proc.stderr.decode() if 'proc' in locals() and proc.stderr else str(e)
        return Response(f"Rendering failed: {stderr_msg}", status_code=500)


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
    global active_path
    if not active_path:
        return JSONResponse({"ok": False, "message": "No active path"}, status_code=400)
    # Same shared state as open/close — serialize against them so a reload
    # can't interleave with a concurrent open/close swap.
    async with _flowgraph_state_lock:
        try:
            new_fg = load_flow_graph(active_path)
            active.swap(new_fg)
            return JSONResponse({"ok": True})
        except Exception as e:
            return JSONResponse({"ok": False, "message": str(e)}, status_code=400)


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
            "openrouter_api_key_set": bool(os.getenv("OPENROUTER_API_KEY")),
        }
    )


async def grc_settings_post(request: Request) -> JSONResponse:
    body = await request.json()
    try:
        save_settings(str(body.get("provider", "")), str(body.get("model", "")))
    except ValueError as e:
        return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "message": "Saved. Restart the app to use this model."})


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
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content = resp.content
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
    Route("/grc/canvas/resize", grc_canvas_resize, methods=["POST"]),
    Route("/grc/panel", grc_panel, methods=["GET"]),
    Route("/grc/panel.js", grc_panel_js, methods=["GET"]),
    Route("/grc/settings", grc_settings_get, methods=["GET"]),
    Route("/grc/settings", grc_settings_post, methods=["POST"]),
]


def main() -> None:
    import threading
    import time
    import webbrowser

    import uvicorn

    # Eagerly start broadwayd on our port (lazy startup also covers this in
    # ensure_broadway, but starting here avoids the first /grc/open paying
    # the cold-start delay). No global pkill/killall sweep: those would
    # stomp another instance's processes (e.g. a concurrent dev session or
    # the test suite on different ports). Tracked termination + canvas_app's
    # control-port bind-retry handle orphans from a crashed previous run.
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
