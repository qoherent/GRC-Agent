import asyncio
import mimetypes
import os
import subprocess
import sys
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
# start_control_server.
CANVAS_RESIZE_PORT = 7933


def _notify_canvas_reload() -> None:
    """Ping the running canvas_app.py to reload the flowgraph from disk.
    Without this, an agent tool call that edits the flowgraph (change_graph)
    only updates this process's in-memory copy — the live GTK canvas has no
    way to learn about it and silently keeps showing stale content, even
    though the chat just told the user the edit succeeded. Best effort: if
    no canvas process is listening (none started yet, or the file was
    closed), this is a harmless no-op."""
    try:
        httpx.post(f"http://127.0.0.1:{CANVAS_RESIZE_PORT}/reload", timeout=0.5)
    except Exception:
        pass


class FlowgraphProxy:
    """Transparent stand-in for the active flowgraph so it can be swapped
    (e.g. via /grc/open) without rebuilding the Agent/web app. Every
    adapter call does plain attribute/method access on `ctx.deps`, so
    forwarding __getattr__/__setattr__ to whichever flowgraph is currently
    targeted is enough — no changes needed to agent.py's tool code. Starts
    empty: the session always begins with no file loaded, and any tool
    call before one is chosen gets a clear error instead of a crash."""

    def __init__(self, flowgraph: Any = None, on_bump: Any = None) -> None:
        object.__setattr__(self, "_target", flowgraph)
        object.__setattr__(self, "_version", 0)
        object.__setattr__(self, "_on_bump", on_bump)

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
        v = object.__getattribute__(self, "_version")
        object.__setattr__(self, "_version", v + 1)
        on_bump = object.__getattribute__(self, "_on_bump")
        if on_bump:
            on_bump()

    def get_version(self) -> int:
        return object.__getattribute__(self, "_version")

    def is_loaded(self) -> bool:
        return object.__getattribute__(self, "_target") is not None


# 1. No flowgraph is loaded at startup — the user must Browse and choose one.
active = FlowgraphProxy(None, on_bump=_notify_canvas_reload)
active_path: str | None = None
canvas_proc: subprocess.Popen | None = None
# Guards every mutation of active/active_path/canvas_proc — without it,
# two concurrent /grc/open (or an open racing a close) calls interleave
# their terminate-old/spawn-new sequences non-deterministically; each
# still reports {"ok": true} to its own caller, but only one's file
# actually ends up loaded. Serializing makes the outcome deterministic
# (last request in wins) instead of a silent race.
_flowgraph_state_lock = asyncio.Lock()


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
    # A blocking socket connect + time.sleep(1.0) here used to stall the
    # entire server on every in-flight request (this app runs a single
    # uvicorn worker / single event loop) for a full second any time
    # broadwayd needed a cold start — e.g. after it crashed or was killed
    # mid-session. Non-blocking equivalents so a broadway cold-start only
    # delays this one request, not every concurrent one.
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", 8085), timeout=0.5
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    except Exception:
        # Not running, start it
        try:
            subprocess.run(["killall", "broadwayd"], capture_output=True)
        except Exception:
            pass
        try:
            subprocess.Popen(
                ["broadwayd", "-p", "8085", ":5"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid
            )
            print("[grc-agent] Started broadwayd daemon on port 8085 display :5 (lazy startup)")
            await asyncio.sleep(1.0)  # give it a second to bind
        except Exception as e:
            print("[grc-agent] Failed to start broadwayd daemon lazily:", e)


def _terminate_canvas_proc() -> None:
    """Best-effort teardown of both the tracked subprocess and any stray
    canvas_app.py this process doesn't know about (e.g. orphaned by a
    crashed previous server instance, or left behind by the test suite) —
    a stray one still holding the control-server port would otherwise
    crash the next canvas_app.py's startup outright."""
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
    try:
        subprocess.run(["pkill", "-f", "canvas_app.py"], capture_output=True)
    except Exception:
        pass


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

        # Terminate any previously running canvas process (tracked or stray)
        _terminate_canvas_proc()

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
                    [sys.executable, "-u", str(Path(__file__).parent / "canvas_app.py"), path],
                    env=env,
                    stdout=canvas_log,
                    stderr=canvas_log,
                    preexec_fn=os.setsid
                )
            print(f"[grc-agent] Started GRC Broadway canvas app for: {path}")
        except Exception as e:
            print(f"[grc-agent] Failed to start GRC Broadway canvas app: {e}")

    return JSONResponse({"ok": True, "path": path})


async def grc_status(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "path": active_path, "version": active.get_version()})


async def grc_close(request: Request) -> JSONResponse:
    global active_path
    async with _flowgraph_state_lock:
        active.swap(None)
        active_path = None
        _terminate_canvas_proc()
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
        proc = subprocess.run(cmd, capture_output=True, check=True)
        return Response(proc.stdout, media_type="image/png")
    except Exception as e:
        stderr_msg = proc.stderr.decode() if 'proc' in locals() and proc.stderr else str(e)
        return Response(f"Rendering failed: {stderr_msg}", status_code=500)


async def grc_panel(request: Request) -> HTMLResponse:
    panel_html = (Path(__file__).parent / "panel.html").read_text(encoding="utf-8")
    return HTMLResponse(panel_html)


async def grc_reload(request: Request) -> JSONResponse:
    global active_path
    if not active_path:
        return JSONResponse({"ok": False, "message": "No active path"}, status_code=400)
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
            await client.post(
                f"http://127.0.0.1:{CANVAS_RESIZE_PORT}/resize",
                json={"width": width, "height": height},
            )
    except Exception:
        # No canvas process listening (not started yet, or closed) —
        # not an error; the next resize once one is running will catch up.
        pass
    return JSONResponse({"ok": True})


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
    Route("/grc/settings", grc_settings_get, methods=["GET"]),
    Route("/grc/settings", grc_settings_post, methods=["POST"]),
]


def main() -> None:
    import threading
    import time
    import webbrowser

    import uvicorn

    # Clean up any canvas_app.py / broadwayd left running from a previous
    # server process (e.g. a crashed or force-killed run) — an orphaned
    # canvas_app.py surviving a restart would keep its own connection to
    # the new broadwayd instance, and a second GTK app connected to the
    # same Broadway display shows up as extra top-level windows in the
    # client, i.e. the "dual window" bug back again.
    try:
        subprocess.run(["pkill", "-f", "canvas_app.py"], capture_output=True)
    except Exception:
        pass
    try:
        subprocess.run(["killall", "broadwayd"], capture_output=True)
    except Exception:
        pass
    try:
        subprocess.Popen(
            ["broadwayd", "-p", "8085", ":5"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=os.setsid
        )
        print("[grc-agent] Started broadwayd daemon on port 8085 display :5")
    except Exception as e:
        print("[grc-agent] Failed to start broadwayd daemon:", e)

    host = os.environ.get("GRC_AGENT_HOST", "127.0.0.1")
    port = int(os.environ.get("GRC_AGENT_PORT", "7932"))
    url = f"http://{host}:{port}/grc/panel"

    def open_browser() -> None:
        time.sleep(1.0)
        print(f"\n[grc-agent] Opening dashboard at: {url}\n")
        webbrowser.open(url)

    # Start the daemon thread so it doesn't block startup
    threading.Thread(target=open_browser, daemon=True).start()

    print(f"\n[grc-agent] Starting server. Dashboard URL: {url}\n")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
