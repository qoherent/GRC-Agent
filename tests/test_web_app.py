import shutil
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from grc_agent import web as web_app
from grc_agent.adapter import change_graph

client = TestClient(web_app.app)


@pytest.fixture(autouse=True)
def reset_active():
    """web_app's active flowgraph is a module-level singleton (mirrors the
    real single-session server) — reset it around each test so they don't
    depend on execution order. Tests that hit /grc/open (e.g.
    test_open_valid_path_then_inspect, test_grc_render_endpoint) spawn a
    genuine canvas_app.py + broadwayd subprocess — TestClient runs the real
    app, not a mock. Teardown terminates only the processes this test run
    spawned (tracked PIDs), never a global `pkill`/`killall` that would
    kill a concurrent dev session on the default ports."""
    web_app.active.swap(None)
    web_app.active_path = None
    yield
    web_app.active.swap(None)
    web_app.active_path = None
    web_app._terminate_canvas_proc()


@pytest.fixture(scope="session", autouse=True)
def cleanup_tracked_processes_at_session_end():
    yield
    # Scoped: only the broadwayd daemons this test session spawned (on the
    # test-isolated port set in conftest.py). The canvas subprocess is
    # already terminated per-test by reset_active above.
    web_app._terminate_broadway()


def test_status_and_inspect_when_not_loaded():
    assert client.get("/grc/status").json()["path"] is None
    res = client.get("/grc/inspect").json()
    assert res["ok"] is False
    assert res["not_loaded"] is True


def test_open_valid_path_then_inspect():
    res = client.post("/grc/open", json={"path": "tests/data/dial_tone.grc"}).json()
    assert res["ok"] is True
    assert res["path"].endswith("dial_tone.grc")

    status = client.get("/grc/status").json()
    assert status["path"] == res["path"]

    inspect = client.get("/grc/inspect").json()
    assert inspect["ok"] is True
    block_names = {b["instance_name"] for b in inspect["graph"]["blocks"]}
    assert "samp_rate" in block_names


def test_open_second_file_replaces_first():
    """Lifecycle edge case: opening a different file without an explicit close
    must replace the active flowgraph and terminate the old canvas."""
    res1 = client.post("/grc/open", json={"path": str(Path.cwd() / "tests" / "data" / "dial_tone.grc")}).json()
    assert res1["ok"] is True

    res2 = client.post("/grc/open", json={"path": str(Path.cwd() / "tests" / "data" / "resampler_demo.grc")}).json()
    assert res2["ok"] is True

    status = client.get("/grc/status").json()
    assert status["path"] == res2["path"]
    assert "resampler_demo" in status["path"]


def test_inspect_during_open_is_serialized():
    """Lifecycle edge case: /grc/inspect during an in-flight /grc/open must
    not crash; serialization is handled by the shared state lock."""
    res = client.post("/grc/open", json={"path": "tests/data/dial_tone.grc"})
    assert res.status_code == 200
    inspect = client.get("/grc/inspect").json()
    assert inspect["ok"] is True



    res = client.post("/grc/open", json={"path": "tests/data/does_not_exist.grc"})
    assert res.status_code == 400
    assert res.json()["ok"] is False
def test_open_invalid_path():
    res = client.post("/grc/open", json={"path": "tests/data/does_not_exist.grc"})
    assert res.status_code == 400
    assert res.json()["ok"] is False


def test_open_malformed_json_returns_400():
    """Regression for P3-9: a malformed body must return 400, not 500."""
    res = client.post(
        "/grc/open",
        data="not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 400


def test_settings_malformed_json_returns_400():
    """Regression for P3-9: a malformed body must return 400, not 500."""
    res = client.post(
        "/grc/settings",
        data="not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 400


def test_browse_empty_directory(tmp_path):
    """Regression for P3-12: an empty directory must render an empty-state
    message rather than a blank list."""
    res = client.get(f"/grc/browse?dir={tmp_path}")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["entries"] == []


def test_browse_lists_directory():
    res = client.get("/grc/browse", params={"dir": str(Path.cwd() / "tests" / "data")}).json()
    assert res["ok"] is True
    names = {e["name"]: e["is_dir"] for e in res["entries"]}
    assert names.get("dial_tone.grc") is False


def test_panel_serves_dashboard():
    res = client.get("/grc/panel")
    assert res.status_code == 200
    assert "grc-pane" in res.text
    assert "chat-frame" in res.text
    # The dashboard logic is extracted into panel.js (no inline script) —
    # the HTML must reference it rather than ship the logic inline.
    assert "/grc/panel.js" in res.text
    assert "const state" not in res.text


def test_panel_js_served():
    res = client.get("/grc/panel.js")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/javascript")
    # Sanity: the consolidated state object + a known function are present.
    assert "const state = {" in res.text
    assert "function refresh(" in res.text


def test_root_serves_chat_widget_not_dashboard():
    """Regression guard: '/' must stay the chat widget's own route, not the
    dashboard — the vendor bundle hardcodes pathname === '/' as its own
    "fresh conversation" sentinel and silently renders blank at any other
    path (confirmed by inspecting the vendor JS directly — see web.py's own
    comment on the route-swap below). Swapping this back would reintroduce
    that bug."""
    res = client.get("/")
    assert res.status_code == 200
    assert web_app.BRAND_NAME in res.text
    assert "grc-pane" not in res.text


def test_settings_endpoints(tmp_path, monkeypatch):
    # Route settings path to a temp location
    tmp_config_file = tmp_path / "settings.json"
    monkeypatch.setenv("GRC_AGENT_CONFIG_PATH", str(tmp_config_file))

    # 1. Get default settings
    res = client.get("/grc/settings").json()
    assert res["ok"] is True
    assert res["provider"] == "ollama"
    assert res["model"] == "qwen3.6:35b-a3b-q4_K_M"
    assert res["ollama_model"] == "qwen3.6:35b-a3b-q4_K_M"
    assert res["openrouter_model"] == "deepseek/deepseek-v4-flash"
    assert res["ollama_cloud_model"] == "deepseek-v4-flash:cloud"

    # 2. Save settings for openrouter
    post_res = client.post(
        "/grc/settings", json={"provider": "openrouter", "model": "google/gemini-2.5-flash"}
    ).json()
    assert post_res["ok"] is True

    # 3. Get updated settings and verify they are stored correctly
    updated = client.get("/grc/settings").json()
    assert updated["ok"] is True
    assert updated["provider"] == "openrouter"
    assert updated["model"] == "google/gemini-2.5-flash"
    assert updated["openrouter_model"] == "google/gemini-2.5-flash"
    # ollama_model should remain at its default/previous value
    assert updated["ollama_model"] == "qwen3.6:35b-a3b-q4_K_M"

    # 4. Save settings for ollama (verify independence)
    post_res = client.post("/grc/settings", json={"provider": "ollama", "model": "gemma2"}).json()
    assert post_res["ok"] is True

    updated2 = client.get("/grc/settings").json()
    assert updated2["provider"] == "ollama"
    assert updated2["model"] == "gemma2"
    assert updated2["ollama_model"] == "gemma2"
    assert updated2["openrouter_model"] == "google/gemini-2.5-flash"


def test_undo_redo_endpoints(tmp_path):
    src = Path(__file__).parent / "data" / "dial_tone.grc"
    dst = tmp_path / "dial_tone.grc"
    shutil.copy2(src, dst)

    res = client.post("/grc/open", json={"path": str(dst)})
    assert res.status_code == 200

    status = client.get("/grc/status").json()
    assert status["can_undo"] is False
    assert status["can_redo"] is False

    # Mutate the same way agent.py's change_graph tool does: through the
    # active FlowgraphProxy, not a raw flow_graph object.
    result = change_graph(
        web_app.active, update_params=[{"instance_name": "samp_rate", "params": {"value": "48000"}}]
    )
    assert result["ok"] is True

    status = client.get("/grc/status").json()
    assert status["can_undo"] is True
    assert status["can_redo"] is False

    undo_res = client.post("/grc/undo").json()
    assert undo_res["ok"] is True
    assert undo_res["can_undo"] is False
    assert undo_res["can_redo"] is True

    inspect = client.get("/grc/inspect").json()
    samp_rate = next(
        b for b in inspect["graph"]["blocks"] if b["instance_name"] == "samp_rate"
    )
    assert samp_rate["params"]["value"] == "32000"

    redo_res = client.post("/grc/redo").json()
    assert redo_res["ok"] is True
    assert redo_res["can_undo"] is True
    assert redo_res["can_redo"] is False

    inspect = client.get("/grc/inspect").json()
    samp_rate = next(
        b for b in inspect["graph"]["blocks"] if b["instance_name"] == "samp_rate"
    )
    assert samp_rate["params"]["value"] == "48000"


def test_undo_with_no_active_path_fails():
    res = client.post("/grc/undo")
    assert res.status_code == 400
    assert res.json()["ok"] is False


def test_canvas_app_control_server_bind_failure_surfaces_to_caller():
    """Regression for P2-4: if canvas_app.py cannot bind its control server,
    the canvas process must fail visibly (not silently continue and leave
    web.py reporting a misleading 'timed out' error)."""
    import socket
    import threading

    from grc_agent.canvas_app import start_control_server

    class Ctx:
        """Minimal stand-in so the bind-failure test doesn't need a real GRC file."""

        def __init__(self, path):
            self.grc_file_path = path
            self.window = None
            self.drawing_area = None
            self.platform = None
            self.pending_size = None
            self.ready = False
            self.last_disk_hash = None
            self.last_synced_export_hash = None

        def apply_resize(self, width, height):
            return False

        def apply_pending_size(self):
            pass

        def apply_reload(self):
            return False

    # Occupy the control port with a stub TCP listener so the real canvas_app
    # server cannot bind.
    occupied_port = 18999  # isolated test port, unlikely to collide
    stub = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    stub.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    stub.bind(("127.0.0.1", occupied_port))
    stub.listen(1)
    exit_code_captured = {"value": None}

    def run_server():
        ctx = Ctx("/tmp/nonexistent.grc")
        try:
            bound = start_control_server(ctx, occupied_port)
            exit_code_captured["value"] = 0 if bound else 1
        except SystemExit as e:
            exit_code_captured["value"] = e.code

    t = threading.Thread(target=run_server)
    t.start()
    t.join(timeout=10)

    stub.close()
    assert exit_code_captured["value"] == 1, (
        "canvas_app.py must report failure when its control server cannot bind"
    )


def test_canvas_app_pending_reload_is_buffered_before_window_exists():
    """Regression for P2-6: a /reload request that arrived before the GTK
    window/drawing_area existed used to be silently dropped. It should be
    buffered and drained once the canvas finishes loading, mirroring the
    existing pending_size behavior."""
    from grc_agent.canvas_app import CanvasControlContext

    ctx = CanvasControlContext("/tmp/nonexistent.grc")
    ctx.window = None

    # Before the window exists, apply_reload should record a pending reload
    # rather than doing nothing.
    result = ctx.apply_reload()
    assert result is False  # GLib.idle_add callbacks return False to not repeat
    assert ctx.pending_reload is True

    # Simulate the window becoming available.
    class FakeWindow:
        def resize(self, w, h):
            pass

        def move(self, x, y):
            pass

    class FakeDrawingArea:
        pass

    class FakePlatform:
        pass

    ctx.window = FakeWindow()
    ctx.drawing_area = FakeDrawingArea()
    ctx.platform = FakePlatform()
    # Without a real flow_graph this call will catch and log, but the key
    # assertion is that pending_reload was consumed.
    ctx.apply_pending_reload()
    assert ctx.pending_reload is False


def test_canvas_app_reload_uses_loopback_address():
    """Regression for P1-3: canvas_app.py pinged 'localhost' for /grc/reload
    while the web server binds IPv4-only 127.0.0.1. On IPv6-first localhost
    resolution the reload ping would silently fail and leave the web process
    stale after a manual canvas edit."""
    import grc_agent.canvas_app as canvas_app_module

    source = Path(canvas_app_module.__file__).read_text(encoding="utf-8")
    assert 'url = f"http://127.0.0.1:{web_port}/grc/reload"' in source, (
        "canvas_app.py must use the IPv4 loopback explicitly to match web.py"
    )
    assert 'url = f"http://localhost:{web_port}/grc/reload"' not in source, (
        "canvas_app.py must not rely on localhost resolution for the reload ping"
    )


def test_grc_render_endpoint():
    # Try calling render when no file is loaded (should fail with 400)
    res = client.get("/grc/render")
    assert res.status_code == 400

    # Load a file first
    path = str((Path(__file__).parent / "data" / "dial_tone.grc").resolve())
    res = client.post("/grc/open", json={"path": path})
    assert res.status_code == 200

    # Call render (should succeed with 200 and return a png image)
    res = client.get("/grc/render")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/png"
    assert len(res.content) > 1000  # valid image size
