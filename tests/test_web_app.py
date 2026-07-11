import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from grc_agent import web as web_app

client = TestClient(web_app.app)


@pytest.fixture(autouse=True)
def reset_active():
    """web_app's active flowgraph is a module-level singleton (mirrors the
    real single-session server) — reset it around each test so they don't
    depend on execution order. Tests that hit /grc/open (e.g.
    test_open_valid_path_then_inspect, test_grc_render_endpoint) spawn a
    genuine canvas_app.py + broadwayd subprocess — TestClient runs the real
    app, not a mock. Left running after the test process exits, the next
    canvas_app.py to start (the next test run, or the real app) crashes
    trying to bind the control-server port an orphan is still holding."""
    web_app.active.swap(None)
    web_app.active_path = None
    yield
    web_app.active.swap(None)
    web_app.active_path = None
    web_app._terminate_canvas_proc()


@pytest.fixture(scope="session", autouse=True)
def cleanup_stray_processes_at_session_end():
    yield
    subprocess.run(["pkill", "-f", "canvas_app.py"], capture_output=True)
    subprocess.run(["killall", "broadwayd"], capture_output=True)


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


def test_open_invalid_path():
    res = client.post("/grc/open", json={"path": "tests/data/does_not_exist.grc"})
    assert res.status_code == 400
    assert res.json()["ok"] is False


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


def test_root_serves_chat_widget_not_dashboard():
    """Regression guard: '/' must stay the chat widget's own route, not the
    dashboard — the vendor bundle hardcodes pathname === '/' as its own
    "fresh conversation" sentinel and silently renders blank at any other
    path (see IMPLEMENTATION_REPORT.md). Swapping this back would
    reintroduce that bug."""
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
    assert res["openrouter_model"] == "openai/gpt-4o-mini"

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
