"""Tests for the run_flowgraph / stop_flowgraph agent tools and their
NativeFlowgraphProxy wiring.

Hermetic: no display, no live LLM. The GRC Actions module is monkeypatched
(the proxy imports it lazily), the canvas manager and exec monitor are fakes
built the same way tests/test_native_canvas.py builds them, and the tool
functions are driven through fake deps objects the way
tests/test_exec_monitor.py drives get_run_log.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic_ai.exceptions import ModelRetry

from grc_agent.agent import run_flowgraph_func, stop_flowgraph_func
from grc_agent.native_canvas import NativeCanvasManager, NativeFlowgraphProxy


class _FakeExecMonitor:
    """The slice of ExecutionErrorMonitor the proxy relies on."""

    def __init__(self, outcome="completed", code=0):
        self.marked_agent_initiated = 0
        self._outcome = outcome
        self.last_run_code = code

    def mark_run_agent_initiated(self):
        self.marked_agent_initiated += 1

    async def wait_for_run_end(self, timeout):  # noqa: ARG002
        return self._outcome


@pytest.fixture(autouse=True)
def _default_loop_policy():
    """Force the plain asyncio policy — test_desktop_app installs the app's
    gbulb/GLib policy process-wide earlier in the full-suite order, and these
    tests call asyncio.run directly (same rationale as test_shell_toolset)."""
    import asyncio as _asyncio

    saved = _asyncio.get_event_loop_policy()
    _asyncio.set_event_loop_policy(_asyncio.DefaultEventLoopPolicy())
    yield
    _asyncio.set_event_loop_policy(saved)


_MONITOR_DEFAULT = object()


def _make_proxy(page, monitor=_MONITOR_DEFAULT, outcome="completed", code=0):
    cm = NativeCanvasManager.__new__(NativeCanvasManager)
    cm.window = SimpleNamespace(current_page=page)
    if monitor is _MONITOR_DEFAULT:
        monitor = _FakeExecMonitor(outcome=outcome, code=code)
    proxy = NativeFlowgraphProxy(cm, exec_monitor=monitor)
    return proxy, monitor


def _valid_page(process=None, file_path="/tmp/proj/flow.grc"):
    fg = MagicMock()
    fg.validate.return_value = None
    fg.is_valid.return_value = True
    fg.iter_error_messages.return_value = iter([])
    return SimpleNamespace(process=process, file_path=file_path, saved=True, flow_graph=fg)


@pytest.fixture
def fake_actions(monkeypatch):
    """Replace GRC's Actions singletons with recorders.

    The proxy resolves them lazily through adapter.gui_actions(), so
    patching the Actions namespace attributes covers every call site.
    """
    from grc_agent.adapter import gui_actions

    actions = gui_actions()
    exec_action = MagicMock(name="FLOW_GRAPH_EXEC")
    kill_action = MagicMock(name="FLOW_GRAPH_KILL")
    monkeypatch.setattr(actions, "FLOW_GRAPH_EXEC", exec_action, raising=False)
    monkeypatch.setattr(actions, "FLOW_GRAPH_KILL", kill_action, raising=False)
    return SimpleNamespace(exec_action=exec_action, kill_action=kill_action)


# --- proxy gates (each pre-empts a real GRC failure mode) ---


def test_run_gates_no_monitor():
    proxy, _ = _make_proxy(_valid_page(), monitor=None)
    with pytest.raises(ValueError, match="run monitor is not wired"):
        import asyncio

        asyncio.run(proxy.run_flowgraph())


def test_run_gates_no_page():
    proxy, _ = _make_proxy(None)
    with pytest.raises(ValueError, match="No flowgraph is open"):
        import asyncio

        asyncio.run(proxy.run_flowgraph())


def test_run_gates_already_running(fake_actions):
    proxy, _ = _make_proxy(_valid_page(process=object()))
    with pytest.raises(ValueError, match="already in progress"):
        import asyncio

        asyncio.run(proxy.run_flowgraph())
    fake_actions.exec_action.assert_not_called()


def test_run_gates_unsaved_page(fake_actions):
    proxy, _ = _make_proxy(_valid_page(file_path=""))
    with pytest.raises(ValueError, match="never been saved"):
        import asyncio

        asyncio.run(proxy.run_flowgraph())
    fake_actions.exec_action.assert_not_called()


def test_run_gates_invalid_graph(fake_actions):
    page = _valid_page()
    page.flow_graph.is_valid.return_value = False
    page.flow_graph.iter_error_messages.return_value = iter(
        ["src_0: Port is not connected", "sink_0: Port is not connected"]
    )
    proxy, _ = _make_proxy(page)
    with pytest.raises(ValueError, match="Port is not connected"):
        import asyncio

        asyncio.run(proxy.run_flowgraph())
    # validate() must run before is_valid() is trusted (Elements only populate
    # _error_messages on an explicit validate call).
    page.flow_graph.validate.assert_called_once()
    fake_actions.exec_action.assert_not_called()


# --- proxy happy paths ---


def test_run_no_wait_returns_started_and_marks_agent_initiated(fake_actions):
    proxy, monitor = _make_proxy(_valid_page())

    import asyncio

    res = asyncio.run(proxy.run_flowgraph(wait=False))

    assert res["status"] == "started"
    assert "get_run_log" in res["note"]
    assert monitor.marked_agent_initiated == 1
    fake_actions.exec_action.set_enabled.assert_called_once_with(True)
    fake_actions.exec_action.assert_called_once_with()


@pytest.mark.usefixtures("fake_actions")
def test_run_wait_completed():
    proxy, _ = _make_proxy(_valid_page(), outcome="completed", code=0)

    import asyncio

    res = asyncio.run(proxy.run_flowgraph(wait=True, timeout_seconds=5))

    assert res["status"] == "completed"
    assert res["return_code"] == 0
    assert res["ran_successfully"] is True
    assert "get_run_log" in res["note"]


@pytest.mark.usefixtures("fake_actions")
def test_run_wait_still_running():
    proxy, _ = _make_proxy(_valid_page(), outcome="still_running")

    import asyncio

    res = asyncio.run(proxy.run_flowgraph(wait=True, timeout_seconds=1))

    assert res["status"] == "still_running"
    assert "stop_flowgraph" in res["note"]


@pytest.mark.usefixtures("fake_actions")
def test_run_wait_not_started():
    proxy, _ = _make_proxy(_valid_page(), outcome="not_started")

    import asyncio

    res = asyncio.run(proxy.run_flowgraph(wait=True))

    assert res["status"] == "not_started"


def test_stop_no_process_is_clean_noop(fake_actions):
    proxy, _ = _make_proxy(_valid_page(process=None))

    import asyncio

    res = asyncio.run(proxy.stop_flowgraph())

    assert res["status"] == "not_running"
    fake_actions.kill_action.assert_not_called()


def test_stop_terminates_via_native_action(fake_actions):
    proxy, _ = _make_proxy(_valid_page(process=object()), outcome="completed")

    import asyncio

    res = asyncio.run(proxy.stop_flowgraph())

    assert res["status"] == "stopped"
    fake_actions.kill_action.set_enabled.assert_called_once_with(True)
    fake_actions.kill_action.assert_called_once_with()


@pytest.mark.usefixtures("fake_actions")
def test_stop_pending_when_shutdown_lingers():
    proxy, _ = _make_proxy(_valid_page(process=object()), outcome="still_running")

    import asyncio

    res = asyncio.run(proxy.stop_flowgraph())

    assert res["status"] == "stop_requested"


# --- tool functions ---


class _FakeDeps:
    def __init__(self, proxy):
        self._proxy = proxy

    async def run_flowgraph(self, wait=True, timeout_seconds=60.0):
        return await self._proxy.run_flowgraph(wait=wait, timeout_seconds=timeout_seconds)

    async def stop_flowgraph(self):
        return await self._proxy.stop_flowgraph()


def _ctx(deps):
    return SimpleNamespace(deps=deps)


def test_tool_run_flowgraph_requires_wired_deps():
    import asyncio

    with pytest.raises(ModelRetry, match="wiring"):
        asyncio.run(
            run_flowgraph_func(_ctx(SimpleNamespace()), wait=True, timeout_seconds=1)
        )


@pytest.mark.usefixtures("fake_actions")
def test_tool_run_flowgraph_wraps_gate_errors_as_modelretry():
    import asyncio

    proxy, _ = _make_proxy(_valid_page(file_path=""))
    with pytest.raises(ModelRetry, match="never been saved"):
        asyncio.run(run_flowgraph_func(_ctx(_FakeDeps(proxy)), wait=True, timeout_seconds=1))


@pytest.mark.usefixtures("fake_actions")
def test_tool_run_flowgraph_returns_json():
    import asyncio
    import json

    proxy, _ = _make_proxy(_valid_page(), outcome="completed", code=0)
    res = json.loads(
        asyncio.run(run_flowgraph_func(_ctx(_FakeDeps(proxy)), wait=True, timeout_seconds=1))
    )
    assert res["status"] == "completed"


def test_tool_stop_flowgraph_requires_wired_deps():
    import asyncio

    with pytest.raises(ModelRetry, match="wiring"):
        asyncio.run(stop_flowgraph_func(_ctx(SimpleNamespace())))


@pytest.mark.usefixtures("fake_actions")
def test_tool_stop_flowgraph_returns_json():
    import asyncio
    import json

    proxy, _ = _make_proxy(_valid_page(process=None))
    res = json.loads(asyncio.run(stop_flowgraph_func(_ctx(_FakeDeps(proxy)))))
    assert res["status"] == "not_running"
