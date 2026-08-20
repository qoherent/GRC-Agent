"""Tests for the ToolOutputLimits capability wiring (spill + read-back).

Hermetic: a real pydantic-ai Agent driven by a scripted FunctionModel calls
a plain tool whose return exceeds the 10k-character band threshold. The
spill store is pointed at tmp_path so nothing touches the repo's
.grc_agent/. No GTK, no LLM.
"""

from __future__ import annotations

import json

from pydantic_ai import Agent, Tool
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai_harness import ToolOutputLimits
from pydantic_ai_harness.tool_output_limits import LocalFileStore

BIG_TEXT = "NEEDLE_AT_START " + ("x" * 30_000) + " NEEDLE_AT_END"
SMALL_TEXT = "small payload, stays inline"


def _tool_result(result, tool_name: str) -> ToolReturnPart:
    parts = [
        p
        for m in result.all_messages()
        for p in getattr(m, "parts", [])
        if isinstance(p, ToolReturnPart) and getattr(p, "tool_name", "") == tool_name
    ]
    assert parts, f"no {tool_name} return in message history"
    return parts[-1]


def _drive(tmp_path, payload, follow_up=None):
    """Scripted agent: calls big_tool once (optionally read_tool_result after)."""

    def _model(messages, info):  # noqa: ARG001
        called_big = any(
            getattr(p, "tool_name", None) == "big_tool" for m in messages for p in getattr(m, "parts", [])
        )
        called_read = any(
            getattr(p, "tool_name", None) == "read_tool_result" for m in messages for p in getattr(m, "parts", [])
        )
        if not called_big:
            return ModelResponse(parts=[ToolCallPart(tool_name="big_tool", args={})])
        if follow_up and not called_read:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="read_tool_result", args=json.loads(follow_up))]
            )
        return ModelResponse(parts=[TextPart(content="done")])

    agent = Agent(
        FunctionModel(_model),
        output_type=str,
        tools=[Tool(lambda: payload, name="big_tool")],
        capabilities=[ToolOutputLimits(store=LocalFileStore(base_dir=tmp_path / "overflow"))],
        retries={"tools": 0},
    )
    return agent.run_sync("use the tool")


def test_oversized_return_spilled_with_handle_and_preview(tmp_path):
    result = _drive(tmp_path, BIG_TEXT)
    ret = _tool_result(result, "big_tool")
    content = str(ret.content)
    assert "read_tool_result" in content  # the model is told how to read it back
    assert len(content) < 5_000  # 20k-char payload was replaced by a bounded notice
    assert "NEEDLE_AT_START" in content  # preview keeps the head
    # The 20k-char MIDDLE is what got spilled: the notice holds only the bounded
    # head+tail preview, never the full payload (head-tail by design, preview_chars=1000) - the bulk of 'x'*20000 must be gone from inline history.
    assert "x" * 2_000 not in content
    # the spill file exists on disk under the store root
    spills = list((tmp_path / "overflow").rglob("*"))
    assert any(p.is_file() for p in spills)
    spilled = next(p for p in spills if p.is_file()).read_text(encoding="utf-8")
    assert "NEEDLE_AT_END" in spilled  # lossless: nothing dropped
    assert "x" * 10_000 in spilled  # the full middle lives in the spill


def test_read_back_returns_slices_of_the_full_payload(tmp_path):
    # The handle is dynamic (run_id/tool_call_id) — first drive spills and we extract
    # the real handle from the notice, then a second run reads it back from_end.
    result = _drive(tmp_path, BIG_TEXT, follow_up=None)
    ret = _tool_result(result, "big_tool")
    # Extract the actual handle from the spilled notice, then read it back
    import re

    m = re.search(r"handle[:\s`\"']*([A-Za-z0-9_./-]+)", str(ret.content))
    assert m, f"no handle in notice: {content_preview(ret)}"
    handle = m.group(1)

    def _model2(messages, info):  # noqa: ARG001
        called_big = any(
            getattr(p, "tool_name", None) == "big_tool" for m in messages for p in getattr(m, "parts", [])
        )
        called_read = any(
            getattr(p, "tool_name", None) == "read_tool_result" for m in messages for p in getattr(m, "parts", [])
        )
        if not called_big:
            return ModelResponse(parts=[ToolCallPart(tool_name="big_tool", args={})])
        if not called_read:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="read_tool_result", args={"handle": handle, "from_end": True})]
            )
        return ModelResponse(parts=[TextPart(content="done")])

    agent = Agent(
        FunctionModel(_model2),
        output_type=str,
        tools=[Tool(lambda: BIG_TEXT, name="big_tool")],
        capabilities=[ToolOutputLimits(store=LocalFileStore(base_dir=tmp_path / "overflow"))],
        retries={"tools": 0},
    )
    result2 = agent.run_sync("use the tool then read it back")
    read_ret = _tool_result(result2, "read_tool_result")
    assert "NEEDLE_AT_END" in str(read_ret.content)  # from_end slice reaches the tail


def test_small_return_passes_through_untouched(tmp_path):
    result = _drive(tmp_path, SMALL_TEXT)
    ret = _tool_result(result, "big_tool")
    assert SMALL_TEXT in str(ret.content)
    assert "read_tool_result" not in str(ret.content)


def content_preview(ret) -> str:
    return str(ret.content)[:200]


def test_factory_agent_carries_the_capability(tmp_path, monkeypatch):
    from pydantic_ai_harness import ToolOutputLimits

    from grc_agent.agent_factory import build_agent_from_cfg

    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("GRC_AGENT_ENV", str(env_file))
    monkeypatch.setattr("grc_agent.agent_factory.resolve_model_context_length", lambda *_a, **_k: None)

    cfg = {
        "provider": "ollama_local",
        "model": "qwen3.6:35b-a3b-q4_K_M",
        "ollama_base_url": "http://localhost:11434",
        "api_key": "",
    }
    agent, _err = build_agent_from_cfg(cfg)
    caps = agent.root_capability.capabilities
    tol = next((c for c in caps if isinstance(c, ToolOutputLimits)), None)
    assert tol is not None
    # rooted next to the chat DB, under .grc_agent/tool_overflow — never /tmp
    assert str(tol._store._root).endswith(".grc_agent/tool_overflow")
