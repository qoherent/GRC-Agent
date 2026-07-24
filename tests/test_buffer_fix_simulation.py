"""Live simulation test: agent must fix 'Buffer too small for min_noutput_items'
on ofdm_cyclic_prefixer by computing the correct minoutbuf from the graph's
parameters (packet_len * (fft_len + cp_len)), not by guessing a small multiplier.

Uses Ollama Cloud (deepseek-v4-flash:cloud). No mocking of the agent, tools,
or flowgraph — the agent really inspects the graph, reads the run log, and
applies a change_graph fix. We verify the fix matches or exceeds the actual
buffer need.

Prerequisites:
- OLLAMA_CLOUD_API_KEY set in .env
- playground/untitled.grc exists (the real OFDM flowgraph)
- Local Ollama running for embeddings (query_knowledge may be called)
"""
import shutil
import tempfile
from pathlib import Path

import pytest
from pydantic_ai import Agent, ModelSettings

from grc_agent.adapter import load_flow_graph
from grc_agent.agent import (
    GrcAgentResponse,
    StopGracefully,
    build_scenario_model,
    grc_tools,
    validate_flowgraph_state,
    web_fetch_cap,
    web_search_cap,
)
from grc_agent.exec_monitor import ExecutionErrorMonitor
from grc_agent.native_canvas import NativeFlowgraphProxy
from grc_agent.prompts import build_system_prompt


def _ollama_cloud_available():
    import os

    from dotenv import load_dotenv

    from grc_agent.settings import env_path
    load_dotenv(env_path())
    return bool(os.environ.get("OLLAMA_CLOUD_API_KEY"))


pytestmark = pytest.mark.skipif(
    not _ollama_cloud_available(),
    reason="OLLAMA_CLOUD_API_KEY not set — cannot run live Ollama Cloud test",
)


def _make_proxy(fg, exec_monitor):
    """Build a NativeFlowgraphProxy that resolves to the given flowgraph,
    with an exec_monitor wired for get_run_log."""
    class FakeCanvasManager:
        current_flow_graph = fg
        current_page = None
        path = getattr(fg, "file_path", None)
        window = None
        def after_agent_edit(self):
            if hasattr(fg, "update"):
                fg.update()
    proxy = NativeFlowgraphProxy(FakeCanvasManager(), exec_monitor=exec_monitor)
    return proxy


def _feed_simulated_run(monitor, output_text, code=0):
    """Feed messages into the monitor simulating a real GRC run."""
    monitor.handle_message("\nExecuting: /tmp/untitled.py\n")
    for ch in output_text:
        monitor.handle_message(ch)
    done = "\n>>> Done\n" if code == 0 else f"\n>>> Done (return code {code})\n"
    monitor.handle_message(done)


def test_agent_fixes_buffer_too_small_ollama_cloud():
    """The agent must:
    1. Read get_run_log and see 'Buffer too small for min_noutput_items'
    2. Inspect the graph to find packet_len, fft_len, cp_len
    3. Set minoutbuf to packet_len * (fft_len + cp_len) — NOT a small
       multiplier of fft_len
    4. The fix must be >= the actual buffer need (6576000 for this graph)
    """

    from dotenv import load_dotenv

    from grc_agent.settings import env_path

    load_dotenv(env_path())

    # 1. Load the real flowgraph from tests/data/
    fixture = Path("tests/data/ofdm_buffer_test.grc")
    if not fixture.exists():
        pytest.skip(f"{fixture} not found")

    tmp_dir = tempfile.mkdtemp()
    tmp = Path(tmp_dir) / "ofdm_buffer_test.grc"
    shutil.copy2(fixture, tmp)
    fg = load_flow_graph(str(tmp))

    # 2. Simulate a failed run with the buffer error
    simulated_log = (
        "QSocketNotifier: Can only be used with threads started with QThread\n"
        "ofdm_cyclic_prefixer :info: set_min_output_buffer on block 6 to 20480\n"
        "thread_body_wrapper :error: ERROR thread[thread-per-block[6]: "
        "<block ofdm_cyclic_prefixer(6)>]: Buffer too small for min_noutput_items\n"
    )
    exec_monitor = ExecutionErrorMonitor(on_error=lambda _code, _log: None)
    _feed_simulated_run(exec_monitor, simulated_log, code=0)

    # Verify the monitor detected the runtime error
    last_run = exec_monitor.get_last_run_log()
    assert last_run is not None, "exec_monitor should have retained the log"
    assert last_run["ran_successfully"] is False, (
        f"Expected ran_successfully=False due to :error: in log, "
        f"got ran_successfully={last_run['ran_successfully']}"
    )
    assert "Buffer too small" in last_run["log_text"]

    # 3. Build the agent exactly as the desktop app does
    proxy = _make_proxy(fg, exec_monitor)
    model = build_scenario_model("ollama_cloud", "deepseek-v4-flash:cloud")
    agent = Agent(
        model,
        deps_type=type(proxy),
        output_type=[GrcAgentResponse, str],
        name="grc_buffer_fix_test_agent",
        instructions=build_system_prompt("buffer-fix-test"),
        tools=grc_tools(),
        capabilities=[StopGracefully(), web_search_cap, web_fetch_cap],
        model_settings=ModelSettings(extra_body={"think": True}),
        retries={"tools": 3, "output": 3},
    )
    agent.output_validator(validate_flowgraph_state)

    # 4. Send the same notification the harness would send
    notification = (
        "Flowgraph run failed (return code 0). "
        "Use the get_run_log tool to read the console output and diagnose the error."
    )

    # 5. Run the agent — it should call get_run_log, inspect_graph, then
    #    change_graph with the correct minoutbuf
    result = agent.run_sync(notification, deps=proxy)

    # 6. Verify the agent actually fixed the graph
    #    Check ofdm_cp_0's minoutbuf in the live flowgraph
    fg_after = load_flow_graph(str(tmp))
    cp_block = fg_after.get_block("ofdm_cp_0")
    minoutbuf_param = cp_block.params.get("minoutbuf")
    assert minoutbuf_param is not None, "ofdm_cp_0 should have a minoutbuf param"

    minoutbuf_value = minoutbuf_param.value
    print(f"\nAgent set minoutbuf = {minoutbuf_value!r}")

    # The correct value is packet_len * (fft_len + cp_len) = 6000 * 1096 = 6576000
    # The agent may use the expression "packet_len * (fft_len + cp_len)" or a
    # numeric value. Either way, the EVALUATED value must be >= 6576000.
    evaluated = minoutbuf_param.get_evaluated()

    actual_need = 6000 * (1024 + 72)  # = 6576000
    print(f"Evaluated minoutbuf = {evaluated}")
    print(f"Actual buffer need = {actual_need}")

    assert evaluated >= actual_need, (
        f"minoutbuf ({evaluated}) must be >= actual buffer need ({actual_need}). "
        f"Agent set minoutbuf = {minoutbuf_value!r}"
    )

    # 7. Print the agent's full output for debugging
    print(f"\nAgent output: {result.output[:500] if isinstance(result.output, str) else str(result.output)[:500]}")
    print(f"\nAll messages: {len(result.all_messages())} messages")

    # Cleanup
    shutil.rmtree(tmp_dir)
