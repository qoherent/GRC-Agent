"""Integration tests for agent tools and flowgraph operations.

Uses Ollama Cloud as the LLM backend. Each test exercises a different
tool/button pathway: inspect, modify, validate, query_knowledge.

Run:  GRC_TEST_BACKEND=ollama_cloud uv run pytest tests/test_button_integration.py -v
"""

import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv
from pydantic_ai import Agent, ModelSettings

from grc_agent.settings import env_path

load_dotenv(env_path())

from grc_agent.adapter import (  # noqa: E402
    change_graph,
    inspect_graph,
)
from grc_agent.agent import (  # noqa: E402
    GrcAgentResponse,
    StopGracefully,
    build_scenario_model,
    fresh_agent,
    grc_tools,
    validate_flowgraph_state,
    web_fetch_cap,
    web_search_cap,
)
from grc_agent.prompts import build_system_prompt  # noqa: E402


def _ollama_cloud_available() -> bool:
    return bool(os.getenv("OLLAMA_CLOUD_API_KEY"))


pytestmark = pytest.mark.skipif(
    not _ollama_cloud_available(),
    reason="OLLAMA_CLOUD_API_KEY not set — skipping Ollama Cloud integration tests.",
)

_CLOUD_MODEL = os.getenv("OLLAMA_CLOUD_MODEL", "deepseek-v4-flash:cloud")
_DIAL_TONE = str(Path("tests/data/dial_tone.grc").resolve())
_EMPTY = str(Path("tests/data/empty.grc").resolve())


def _build_cloud_agent(fixture: str):
    """Build an agent with Ollama Cloud and a fresh copy of the fixture."""
    fg, tmp, tmp_dir = fresh_agent(fixture)
    model = build_scenario_model("ollama_cloud", _CLOUD_MODEL)
    agent = Agent(
        model,
        deps_type=Any,
        output_type=[GrcAgentResponse, str],
        name="grc_button_integration_test",
        instructions=build_system_prompt("pai-button-test"),
        tools=grc_tools(),
        capabilities=[StopGracefully(), web_search_cap, web_fetch_cap],
        model_settings=ModelSettings(extra_body={"think": True}),
        retries={"tools": 3, "output": 3},
    )
    agent.output_validator(validate_flowgraph_state)
    return agent, fg, tmp, tmp_dir


# --- inspect_graph (Validate button pathway) ---


def test_inspect_graph_returns_topology():
    """The agent sees the loaded graph's blocks and connections."""
    fg, _tmp, tmp_dir = fresh_agent(_DIAL_TONE)
    try:
        result = inspect_graph(fg, view="overview")
        assert result["ok"] is True
        graph = result["graph"]
        assert len(graph["blocks"]) > 0
        names = [b["instance_name"] for b in graph["blocks"]]
        assert any("analog" in n.lower() or "sig" in n.lower() for n in names), (
            f"Expected signal source blocks, got: {names}"
        )
    finally:
        shutil.rmtree(tmp_dir)


def test_inspect_graph_targets_filter():
    """inspect_graph with targets returns only the named blocks."""
    fg, _tmp, tmp_dir = fresh_agent(_DIAL_TONE)
    try:
        full = inspect_graph(fg, view="overview")
        first_block = full["graph"]["blocks"][0]["instance_name"]
        targeted = inspect_graph(fg, targets=[first_block], view="overview")
        assert targeted["ok"] is True
        assert len(targeted["graph"]["blocks"]) == 1
        assert targeted["graph"]["blocks"][0]["instance_name"] == first_block
    finally:
        shutil.rmtree(tmp_dir)


# --- change_graph ---


def test_change_graph_add_block():
    """Agent adds a block via change_graph and it appears in the graph."""
    fg, _tmp, tmp_dir = fresh_agent(_EMPTY)
    try:
        result = change_graph(
            fg,
            add_blocks=[{"block_id": "variable", "instance_name": "my_new_var", "params": {"value": "42"}}],
        )
        assert result["ok"] is True
        names = {b.name for b in fg.blocks}
        assert "my_new_var" in names
    finally:
        shutil.rmtree(tmp_dir)


def test_change_graph_update_param():
    """Agent updates a parameter value on an existing block."""
    fg, _tmp, tmp_dir = fresh_agent(_DIAL_TONE)
    try:
        result = change_graph(
            fg,
            update_params=[{"instance_name": "samp_rate", "param": "value", "value": "48000"}],
        )
        assert result["ok"] is True
    finally:
        shutil.rmtree(tmp_dir)


def test_change_graph_remove_block():
    """Agent removes a variable block (safe — force bypasses validation)."""
    fg, _tmp, tmp_dir = fresh_agent(_DIAL_TONE)
    try:
        result = change_graph(fg, remove_blocks=["ampl"], force=True)
        assert result["ok"] is True
        remaining = {b.name for b in fg.blocks}
        assert "ampl" not in remaining
    finally:
        shutil.rmtree(tmp_dir)


# --- Validation ---


def test_validate_valid_graph():
    """A known-good graph validates cleanly."""
    fg, _tmp, tmp_dir = fresh_agent(_DIAL_TONE)
    try:
        fg.validate()
        assert fg.is_valid() is True
        errors = list(fg.iter_error_messages())
        assert len(errors) == 0
    finally:
        shutil.rmtree(tmp_dir)


def test_validate_broken_graph():
    """A known-broken graph reports errors."""
    fixture = str(Path("tests/data/broken_unconnected_sink.grc").resolve())
    fg, _tmp, tmp_dir = fresh_agent(fixture)
    try:
        fg.validate()
        assert fg.is_valid() is False
        errors = list(fg.iter_error_messages())
        assert len(errors) > 0
    finally:
        shutil.rmtree(tmp_dir)


# --- Agent end-to-end (Send button pathway) ---


def test_agent_inspects_graph_via_chat():
    """Full agent.run_sync: user asks to inspect the graph, agent uses inspect_graph tool."""
    agent, fg, _tmp, tmp_dir = _build_cloud_agent(_DIAL_TONE)
    try:
        res = agent.run_sync("List the blocks in this flowgraph.", deps=fg)
        assert res.output is not None
        assert len(res.all_messages()) > 1
    finally:
        shutil.rmtree(tmp_dir)


def test_agent_modifies_graph_via_chat():
    """Full agent.run_sync: user asks to add a variable, agent uses change_graph."""
    agent, fg, _tmp, tmp_dir = _build_cloud_agent(_EMPTY)
    try:
        res = agent.run_sync(
            "Add a variable block named 'center_freq' with value 2400000000.",
            deps=fg,
        )
        assert res.output is not None
        names = {b.name for b in fg.blocks}
        assert "center_freq" in names, f"center_freq not in graph. Blocks: {names}"
    finally:
        shutil.rmtree(tmp_dir)
