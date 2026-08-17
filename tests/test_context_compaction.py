import asyncio
import os
import sqlite3

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel

from grc_agent.agent_factory import _build_compaction_capability
from grc_agent.db import init_db, save_session
from grc_agent.trace import TraceRecorder, save_turn_trace


def test_compaction_under_budget_preserves_exact_history():
    """When message history is well within target_tokens, TieredCompaction
    leaves all messages and parts untouched, ensuring 100% KV cache hit rate."""
    compaction = _build_compaction_capability({"provider": "ollama", "ollama_base_url": "http://localhost:11434"})

    history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="Add a throttle block")]),
        ModelResponse(parts=[
            ThinkingPart(content="I should check the active flowgraph first."),
            ToolCallPart(tool_name="inspect_graph", args={"detail": "all"}, tool_call_id="call_1"),
        ]),
        ModelRequest(parts=[
            ToolReturnPart(tool_name="inspect_graph", content='{"blocks": ["samp_rate"]}', tool_call_id="call_1")
        ]),
        ModelResponse(parts=[TextPart(content="I see the flowgraph.")])
    ]

    agent = Agent(TestModel(), capabilities=[compaction])

    async def _run():
        result = await agent.run("Now wire it", message_history=history)
        all_msgs = result.all_messages()
        # Verify tool return in history was NOT cleared
        ret_part = next(p for m in all_msgs for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_call_id == "call_1")
        assert ret_part.content == '{"blocks": ["samp_rate"]}'

    asyncio.run(_run())


def test_compaction_over_budget_evicts_old_tool_returns_keeps_last_n():
    """When token budget is exceeded, Tier 1 (ClearToolResults) clears older tool
    return contents to placeholder while preserving the most recent 2 pairs intact."""
    # Set a small target_tokens so compaction triggers
    cfg = {"provider": "ollama", "ollama_base_url": "http://localhost:11434"}
    os.environ["GRC_COMPACTION_TARGET_TOKENS"] = "500"
    try:
        compaction = _build_compaction_capability(cfg)
    finally:
        os.environ.pop("GRC_COMPACTION_TARGET_TOKENS", None)

    # 3 turns of massive inspect_graph payloads (1,500 chars each)
    history: list[ModelMessage] = [
        # Turn 1
        ModelRequest(parts=[UserPromptPart(content="Turn 1: inspect")]),
        ModelResponse(parts=[ToolCallPart(tool_name="inspect_graph", args={}, tool_call_id="call_t1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="inspect_graph", content="A" * 1500, tool_call_id="call_t1")]),
        ModelResponse(parts=[TextPart(content="Turn 1 done.")]),
        # Turn 2
        ModelRequest(parts=[UserPromptPart(content="Turn 2: inspect")]),
        ModelResponse(parts=[ToolCallPart(tool_name="inspect_graph", args={}, tool_call_id="call_t2")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="inspect_graph", content="B" * 1500, tool_call_id="call_t2")]),
        ModelResponse(parts=[TextPart(content="Turn 2 done.")]),
        # Turn 3
        ModelRequest(parts=[UserPromptPart(content="Turn 3: inspect")]),
        ModelResponse(parts=[ToolCallPart(tool_name="inspect_graph", args={}, tool_call_id="call_t3")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="inspect_graph", content="C" * 1500, tool_call_id="call_t3")]),
        ModelResponse(parts=[TextPart(content="Turn 3 done.")]),
    ]

    agent = Agent(TestModel(), capabilities=[compaction])

    async def _run():
        result = await agent.run("Turn 4: do something", message_history=history)
        all_msgs = result.all_messages()

        returns = {
            p.tool_call_id: p.content
            for m in all_msgs
            for p in m.parts
            if isinstance(p, ToolReturnPart)
        }

        # Turn 1's tool return (oldest) should be cleared to placeholder
        assert "[Flowgraph tool output cleared to conserve context window]" in returns["call_t1"]

        # The last 2 tool returns (Turn 2 and Turn 3) must be preserved in full
        assert returns["call_t2"] == "B" * 1500
        assert returns["call_t3"] == "C" * 1500

    asyncio.run(_run())


def test_sliding_window_preserves_first_user_prompt():
    """When history is exceptionally long, Tier 2 (SlidingWindowCompaction)
    preserves the original user prompt (preserve_first_user_message=True)."""
    cfg = {"provider": "ollama", "ollama_base_url": "http://localhost:11434"}
    os.environ["GRC_COMPACTION_TARGET_TOKENS"] = "200"
    try:
        compaction = _build_compaction_capability(cfg)
    finally:
        os.environ.pop("GRC_COMPACTION_TARGET_TOKENS", None)

    # 15 conversation turns
    history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="INITIAL_USER_GOAL: Build an FM receiver")]),
        ModelResponse(parts=[TextPart(content="I will help you build an FM receiver.")]),
    ]
    for i in range(1, 15):
        history.append(ModelRequest(parts=[UserPromptPart(content=f"Step {i}: Add component {i}")]))
        history.append(ModelResponse(parts=[TextPart(content=f"Added component {i}.")]))

    agent = Agent(TestModel(), capabilities=[compaction])

    async def _run():
        result = await agent.run("Final step: generate python", message_history=history)
        all_msgs = result.all_messages()
        # Ensure the first user message is preserved
        first_user_part = next(
            p.content for m in all_msgs for p in m.parts if isinstance(p, UserPromptPart)
        )
        assert "INITIAL_USER_GOAL" in first_user_part

    asyncio.run(_run())


def test_compaction_target_is_local_for_lan_openai_compatible_endpoint(monkeypatch):
    """Regression: an openai_compatible endpoint on a LAN IP (plain http://)
    must be treated as local (24k target), not cloud (96k) — a 32k-window LAN
    model would otherwise never compact and overflow its context."""
    monkeypatch.delenv("GRC_COMPACTION_TARGET_TOKENS", raising=False)
    cap = _build_compaction_capability(
        {"provider": "openai_compatible", "openai_compatible_base_url": "http://192.168.1.5:8000/v1"}
    )
    assert cap.target_tokens == 24_000


def test_compaction_target_is_cloud_for_https_endpoints(monkeypatch):
    """https endpoints (ollama.com, openrouter.ai, custom proxies) are cloud
    — 96k target. The one uniform rule is the scheme, not the hostname."""
    monkeypatch.delenv("GRC_COMPACTION_TARGET_TOKENS", raising=False)
    for base_url in (
        "https://openrouter.ai/api/v1",
        "https://ollama.com/v1",
        "https://my-corp-proxy.example.com/v1",
    ):
        cap = _build_compaction_capability(
            {"provider": "openai_compatible", "openai_compatible_base_url": base_url}
        )
        assert cap.target_tokens == 96_000, f"{base_url} should be cloud"


def test_compaction_target_is_local_for_localhost_ollama(monkeypatch):
    monkeypatch.delenv("GRC_COMPACTION_TARGET_TOKENS", raising=False)
    cap = _build_compaction_capability(
        {"provider": "ollama", "ollama_base_url": "http://localhost:11434"}
    )
    assert cap.target_tokens == 24_000


def test_trace_isolation_preserves_uncompacted_events_in_sqlite(tmp_path, monkeypatch):
    """Regression & Architectural Guarantee:
    Even when wire messages are compacted, SQLite's turn_traces table MUST
    store the 100% full raw uncompacted tool inputs, tool returns, and reasoning
    events for future fine-tuning."""
    monkeypatch.setenv("GRC_AGENT_ENV", str(tmp_path / ".env"))
    from grc_agent.db import get_db_path
    db_file = get_db_path()
    init_db()

    # Create a session first so FK constraint is satisfied
    sid = save_session(None, "/tmp/test.grc", [])
    assert sid is not None

    # Create a TraceRecorder and record a massive 15,000-token tool return event
    recorder = TraceRecorder(
        session_id=sid,
        provider="ollama",
        model="qwen3.6:35b-a3b-q4_K_M",
        base_url="http://localhost:11434",
        user_prompt="Inspect the full flowgraph",
        origin_page_path="/tmp/test.grc",
    )

    full_payload = '{"blocks": [' + ', '.join(f'{{"id": "block_{i}", "type": "complex"}}' for i in range(500)) + ']}'
    assert len(full_payload) > 10_000

    from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent

    recorder.on_event(FunctionToolCallEvent(part=ToolCallPart(tool_name="inspect_graph", args={"detail": "all"}, tool_call_id="call_inspect_1")))
    recorder.on_event(FunctionToolResultEvent(part=ToolReturnPart(tool_name="inspect_graph", content=full_payload, tool_call_id="call_inspect_1")))
    row = recorder.finalize(run=None)
    row["run_id"] = "run_123"
    row["conversation_id"] = "conv_123"

    # Save to SQLite
    save_turn_trace(row)

    # Verify SQLite turn_traces table has the UNCOMPACTED 10k+ payload intact
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()
    cur.execute("SELECT events FROM turn_traces WHERE run_id = ?", ("run_123",))
    row = cur.fetchone()
    conn.close()

    assert row is not None
    events_json = row[0]
    assert len(events_json) > 10_000
    assert "block_499" in events_json, "turn_traces must store uncompacted ground truth"


def test_chat_sidebar_renders_compacted_messages_cleanly():
    """ChatSidebar._render_last_message_rich must cleanly render messages
    with cleared tool return placeholders without crashing or raising GTK errors."""
    from gi.repository import Gtk

    from grc_agent.chat_sidebar import ChatSidebar

    sidebar = ChatSidebar()

    # Simulate message history containing a cleared tool result
    sidebar._message_history = [
        ModelRequest(parts=[UserPromptPart(content="Inspect flowgraph")]),
        ModelResponse(parts=[ToolCallPart(tool_name="inspect_graph", args={"detail": "all"}, tool_call_id="call_c1")]),
        ModelRequest(parts=[
            ToolReturnPart(
                tool_name="inspect_graph",
                content="[Flowgraph tool output cleared to conserve context window]",
                tool_call_id="call_c1",
            )
        ]),
        ModelResponse(parts=[TextPart(content="Here is the flowgraph overview.")]),
    ]

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    # Render the ModelResponse containing the tool call
    sidebar._render_last_message_rich(box, sidebar._message_history[1])

    # Ensure the expander was created and placeholder text was populated
    children = box.get_children()
    assert len(children) >= 1
    expander = children[0]
    assert isinstance(expander, Gtk.Expander)
    assert "inspect_graph" in expander.get_label()
