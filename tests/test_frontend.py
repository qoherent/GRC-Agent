"""Browser-driven regression tests for panel.js's frontend state lifecycle.

Unlike test_web_app.py (HTTP/string-level only), these drive a real Chromium
browser via Playwright against a real running server subprocess — the races
these guard against (see AGENTS.md's "preserveGrcOnReset"/"getChatFramePath"
entries) only ever reproduce with an actual DOM, iframe, and timer, not
through curl/TestClient-level checks (this is exactly the gap an independent
frontend-lifecycle audit flagged: six-plus rounds of "fix the state machine"
in this project's history with no automated test able to catch the next one).

No live LLM backend is needed. The vendor chat bundle transitions off its own
"/" fresh-conversation sentinel via a client-side `window.history.pushState`
(confirmed by directly inspecting the shipped bundle) — panel.js's own polling
logic only ever reads `frame.contentWindow.location.pathname`, and never
distinguishes a real vendor-issued conversation id from any other pathname,
so simulating that same pushState call exercises the identical code path a
real chat message would, deterministically and without a chat round-trip.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
from playwright.sync_api import sync_playwright

FIXTURES_DIR = Path(__file__).parent / "data"
HOST = "127.0.0.1"
# Distinct from both the default ports (8085/7933/7932, a possible live dev
# session) and conftest.py's own reserved ports (18085/17933/17932, possibly
# in use by a concurrent test_web_app.py run) — this suite spawns a real,
# separate server subprocess, so it needs its own untouched port triple.
PORT = 19932
BROADWAY_PORT = 19085
CANVAS_CONTROL_PORT = 19933
BASE_URL = f"http://{HOST}:{PORT}"

# A second, dedicated server instance for tests that need a deliberately
# forced canvas_ready=false — its own port triple, distinct from the ones
# above (and the defaults/conftest.py/.claude/launch.json's isolated
# config), so it can run alongside any of them without colliding.
CANVAS_FAILURE_PORT = 19942
CANVAS_FAILURE_BROADWAY_PORT = 19086
CANVAS_FAILURE_CANVAS_CONTROL_PORT = 19943
CANVAS_FAILURE_BASE_URL = f"http://{HOST}:{CANVAS_FAILURE_PORT}"


def _http_json(method: str, path: str, body: dict = None, base_url: str = BASE_URL) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = Request(
        f"{base_url}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _wait_for_server(port: int = PORT, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    config_path = tmp_path_factory.mktemp("frontend_server") / "settings.json"
    env = {
        **os.environ,
        "GRC_BROADWAY_PORT": str(BROADWAY_PORT),
        "GRC_CANVAS_CONTROL_PORT": str(CANVAS_CONTROL_PORT),
        "GRC_AGENT_PORT": str(PORT),
        "GRC_AGENT_CONFIG_PATH": str(config_path),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "grc_agent.web:app", "--host", HOST, "--port", str(PORT)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        assert _wait_for_server(), "server did not start in time"
        yield BASE_URL
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, live_server):
    # Fresh isolated browsing context per test (own localStorage/cookies) —
    # and a clean server-side slate, since these tests don't depend on order.
    _http_json("POST", "/grc/close")
    pg = browser.new_page()
    yield pg
    pg.close()


@pytest.fixture(scope="module")
def canvas_failure_server(tmp_path_factory):
    # GRC_CANVAS_READY_TIMEOUT near zero forces _wait_for_canvas_ready to give
    # up before its very first /ready poll — deterministically reproducing a
    # canvas_ready=false response (the "Timed out waiting for canvas to
    # become ready" path) without needing to actually break canvas_app.py or
    # wait out a real 20s deadline.
    config_path = tmp_path_factory.mktemp("frontend_server_canvas_failure") / "settings.json"
    env = {
        **os.environ,
        "GRC_BROADWAY_PORT": str(CANVAS_FAILURE_BROADWAY_PORT),
        "GRC_CANVAS_CONTROL_PORT": str(CANVAS_FAILURE_CANVAS_CONTROL_PORT),
        "GRC_AGENT_PORT": str(CANVAS_FAILURE_PORT),
        "GRC_AGENT_CONFIG_PATH": str(config_path),
        "GRC_CANVAS_READY_TIMEOUT": "0.01",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "grc_agent.web:app", "--host", HOST, "--port", str(CANVAS_FAILURE_PORT)],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        assert _wait_for_server(CANVAS_FAILURE_PORT), "server did not start in time"
        yield CANVAS_FAILURE_BASE_URL
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture
def canvas_failure_page(browser, canvas_failure_server):
    _http_json("POST", "/grc/close", base_url=canvas_failure_server)
    pg = browser.new_page()
    yield pg
    pg.close()


@pytest.fixture
def grc_file(tmp_path):
    dst = tmp_path / "frontend_test.grc"
    shutil.copy2(FIXTURES_DIR / "dial_tone.grc", dst)
    return dst


def _simulate_conversation_start(page, conv_id="/fake-conv-1"):
    page.evaluate(
        """(convId) => {
            const win = document.getElementById('chat-frame').contentWindow;
            win.history.pushState({}, '', convId);
        }""",
        conv_id,
    )


def test_cold_load_no_console_errors_and_undo_redo_disabled(live_server, page):
    errors = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.on("pageerror", lambda exc: errors.append(str(exc)))

    page.goto(f"{BASE_URL}/grc/panel")
    page.wait_for_timeout(1500)

    assert errors == []
    assert page.is_disabled("#undo-btn")
    assert page.is_disabled("#redo-btn")


def test_reset_conversation_preserves_loaded_flowgraph(live_server, grc_file, page):
    # Regression test for the audit's Critical finding: resetting a broken-
    # looking chat conversation (auto-heal, or the "chat looks stuck?"
    # button) must never close the loaded flowgraph or kill its live canvas
    # process — the two are unrelated pieces of state that used to be
    # conflated by both routing through startNewConversation().
    _http_json("POST", "/grc/open", {"path": str(grc_file)})
    page.goto(f"{BASE_URL}/grc/panel")
    page.wait_for_timeout(1000)  # initial refresh()/poll settles, isGrcLoaded -> true

    _simulate_conversation_start(page, "/fake-conv-reset-test")
    page.wait_for_timeout(1000)  # let pollConversationState cache this as state.lastPath once

    page.click("#reset-conversation-btn")
    page.wait_for_timeout(1600)  # a couple of 750ms poll ticks for a regression to reveal itself

    status = _http_json("GET", "/grc/status")
    assert status["path"] == str(grc_file), (
        "resetting the chat widget must never close the loaded flowgraph"
    )
    iframe_path = page.evaluate(
        "document.getElementById('chat-frame').contentWindow.location.pathname"
    )
    assert iframe_path == "/", "the reset button must still actually reset the chat"


def test_new_conversation_button_still_unloads_flowgraph(live_server, grc_file, page):
    # The "+" button is the one control that SHOULD close the loaded file —
    # confirms the fix above didn't overcorrect into never unloading at all.
    _http_json("POST", "/grc/open", {"path": str(grc_file)})
    page.goto(f"{BASE_URL}/grc/panel")
    page.wait_for_timeout(1000)

    page.click("#new-conversation-btn")
    page.wait_for_timeout(1000)

    status = _http_json("GET", "/grc/status")
    assert status["path"] is None


def test_clear_history_overlay_hides_promptly_after_conversation_starts(live_server, page):
    # Regression test for the audit's ~734ms-measured race: integrateSettings
    # (300ms poll) used to trust state.lastPath (written only by the slower,
    # independently-scheduled 750ms pollConversationState), so the overlay
    # could show over an already-active conversation until that other timer
    # next happened to fire. Racing the two real setInterval timers against
    # a wall-clock wait is inherently non-deterministic (their relative phase
    # depends on exact page-load timing, not just the interval lengths) — so
    # this instead calls pushState and integrateSettings() back to back in
    # one synchronous evaluate(), guaranteeing no timer can slip in between,
    # to test the actual logic distinction (fresh read vs. stale cache)
    # rather than hoping to win a race against it.
    page.goto(f"{BASE_URL}/grc/panel")
    page.wait_for_timeout(1200)  # let integrateSettings' sidebar probe find real vendor content

    overlay_before = page.eval_on_selector(
        "#left-sidebar-overlay", "el => getComputedStyle(el).display"
    )
    assert overlay_before == "flex", (
        "expected Clear History to show on a genuinely fresh load — "
        "otherwise this test would pass for the wrong reason"
    )

    overlay_after = page.evaluate(
        """() => {
            const win = document.getElementById('chat-frame').contentWindow;
            win.history.pushState({}, '', '/fake-conv-race-test');
            integrateSettings();
            return getComputedStyle(document.getElementById('left-sidebar-overlay')).display;
        }"""
    )
    assert overlay_after == "none"


def test_url_reflects_active_conversation_and_restores_on_reload(live_server, page):
    # Regression test for the audit's finding that the dashboard's own URL
    # never changed at all, so a reload or shared link could only ever fall
    # back to localStorage. Confirms the URL is now genuinely authoritative
    # by clearing localStorage before reloading.
    page.goto(f"{BASE_URL}/grc/panel")
    page.wait_for_timeout(500)

    _simulate_conversation_start(page, "/fake-conv-url-test")
    page.wait_for_timeout(1000)  # a poll tick to sync the parent URL

    assert "conv=fake-conv-url-test" in page.url

    page.evaluate("localStorage.removeItem('grc_active_conv_id')")
    reload_url = page.url
    page.goto(reload_url)
    page.wait_for_timeout(800)

    iframe_path = page.evaluate(
        "document.getElementById('chat-frame').contentWindow.location.pathname"
    )
    assert iframe_path == "/fake-conv-url-test"


def test_canvas_timeout_leaves_chat_enabled_and_clears_banner_on_close(
    canvas_failure_server, grc_file, canvas_failure_page
):
    # Regression test for the audit's core finding: a canvas that fails to
    # become ready must never be conflated with "nothing loaded". The
    # flowgraph really did load server-side (grc_open's ok=true is
    # independent of canvas_ready), so chat must stay usable, the
    # placeholder must say so distinctly rather than reusing "No flowgraph
    # loaded", and — since canvas_ready_state/canvas_error_state are now
    # mirrored into /grc/status, not just /grc/open's one-shot response —
    # this must hold even discovered via a plain page load, not just the
    # immediate open. Closing the file afterward must actually clear the
    # error banner (renderEmptyState() previously never did).
    page = canvas_failure_page
    open_res = _http_json(
        "POST", "/grc/open", {"path": str(grc_file)}, base_url=canvas_failure_server
    )
    assert open_res["ok"] is True
    assert open_res["canvas_ready"] is False, (
        "expected the near-zero GRC_CANVAS_READY_TIMEOUT to force a canvas_ready=false response"
    )

    # Discovered via a fresh page load (not the /grc/open response itself)
    # — this only works because canvas_ready/canvas_error are now persisted
    # server-side and re-exposed via /grc/status, not just a one-shot field.
    page.goto(f"{canvas_failure_server}/grc/panel")
    page.wait_for_timeout(1200)  # initial poll tick's version-bump refresh() settles

    banner = page.eval_on_selector("#msg", "el => el.textContent")
    assert "Timed out waiting for canvas to become ready" in banner

    placeholder_text = page.eval_on_selector("#canvas-placeholder-text", "el => el.textContent")
    assert "Flowgraph loaded, but the canvas failed to connect" in placeholder_text

    is_disabled = page.evaluate(
        """() => {
            const doc = document.getElementById('chat-frame').contentDocument;
            const textarea = doc && doc.querySelector('textarea');
            return textarea ? textarea.disabled : null;
        }"""
    )
    assert is_disabled is False, "chat must stay enabled — the flowgraph itself did load"

    _http_json("POST", "/grc/close", base_url=canvas_failure_server)
    page.click("#validate-btn")  # forces an immediate refresh() rather than waiting on a poll tick
    page.wait_for_timeout(500)

    banner_after_close = page.eval_on_selector("#msg", "el => el.textContent")
    assert banner_after_close == "", "closing the file must clear a stale canvas-timeout banner"

    placeholder_after_close = page.eval_on_selector(
        "#canvas-placeholder-text", "el => el.textContent"
    )
    assert placeholder_after_close == "No flowgraph loaded. Click Browse to choose one."


def test_canvas_ready_state_recovers_after_successful_reload_ping(
    canvas_failure_server, grc_file, canvas_failure_page
):
    # Regression for P1-1: once canvas_ready_state flipped to false, a later
    # successful canvas reload ping must clear it so /grc/status stops lying.
    # The canvas process is still alive and will finish its GTK startup even
    # though web.py's short timeout gave up; poll its control server directly
    # until it is actually ready, then drive a reload through the web API.
    open_res = _http_json(
        "POST", "/grc/open", {"path": str(grc_file)}, base_url=canvas_failure_server
    )
    assert open_res["ok"] is True
    assert open_res["canvas_ready"] is False
    assert open_res["canvas_error"] is not None

    control_url = f"http://127.0.0.1:{CANVAS_FAILURE_CANVAS_CONTROL_PORT}/ready"
    deadline = time.time() + 20.0
    while time.time() < deadline:
        try:
            with urlopen(Request(control_url, method="GET"), timeout=0.5) as resp:
                if resp.status == 200:
                    break
        except OSError:
            pass
        time.sleep(0.3)
    else:
        pytest.fail("canvas control server never became ready")

    reload_res = _http_json("GET", "/grc/reload", base_url=canvas_failure_server)
    assert reload_res["ok"] is True
    assert reload_res["canvas_synced"] is True

    status = _http_json("GET", "/grc/status", base_url=canvas_failure_server)
    assert status["canvas_ready"] is True, (
        "a successful reload ping must recover canvas_ready_state"
    )
    assert status["canvas_error"] is None


def test_poll_conversation_state_guards_against_duplicate_open(live_server, grc_file, page):
    # Regression test: pollConversationState only wrote state.lastPath AFTER
    # its own await fetch("/grc/open", ...) resolved, and that call can
    # legitimately take up to the ~20s canvas-ready deadline — so a later
    # 750ms tick firing while an earlier call was still pending used to see
    # the same "path changed" condition and fire ANOTHER /grc/open for the
    # same path, each one killing whatever canvas the previous call had just
    # spawned before it could ever become ready (web.py's own
    # "last-writer-wins" semantics for a concurrent /grc/open). Fires a
    # second call directly, after giving the first just enough time to reach
    # its own slow /grc/open await, rather than waiting on the real
    # setInterval — a synchronous race is deterministic in a way a
    # wall-clock one against a real timer isn't (see this file's own
    # docstring/other tests for the same preference).
    open_requests = []
    page.on(
        "request",
        lambda req: open_requests.append(req)
        if req.url.endswith("/grc/open") and req.method == "POST"
        else None,
    )

    page.goto(f"{BASE_URL}/grc/panel")
    page.wait_for_timeout(500)

    conv_id = "/fake-conv-reentrancy-test"
    page.evaluate(
        """([convId, path]) => {
            const mapping = JSON.parse(localStorage.getItem('grc_session_mapping') || '{}');
            mapping[convId] = path;
            localStorage.setItem('grc_session_mapping', JSON.stringify(mapping));
            const win = document.getElementById('chat-frame').contentWindow;
            win.history.pushState({}, '', convId);
        }""",
        [conv_id, str(grc_file)],
    )

    # Kick off the first tick without awaiting its completion — it should
    # reach the mapped-path branch, notice the transition, and start its own
    # (slow) /grc/open call.
    page.evaluate("() => { window.__firstPoll = pollConversationState(); }")
    page.wait_for_timeout(300)  # let it reach and start the slow /grc/open

    assert len(open_requests) == 1, "expected the first tick's own /grc/open to have started"

    # A second tick, as if the next 750ms interval had fired while the first
    # is still pending.
    page.evaluate("() => pollConversationState()")
    page.wait_for_timeout(300)

    assert len(open_requests) == 1, (
        "a second tick firing while the first /grc/open is still pending must not start a duplicate"
    )
