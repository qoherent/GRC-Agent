"""Integration tests for agent tools and flowgraph operations.

Uses Ollama Cloud as the LLM backend. Each test exercises a different
tool/button pathway: inspect, modify, validate, query_knowledge.

Run:  GRC_TEST_BACKEND=ollama_cloud uv run pytest tests/test_button_integration.py -v
"""

import contextlib
import json
import os
import shutil
import tempfile
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


# --- query_knowledge / RAG (SQLite FTS5 lexical fallback) ---
#
# These exercise the real RAG backing store (grc_agent.adapter.rag), not just
# the agent loop: query_catalog()/query_docs() embed the query via a real
# local Ollama server (embed_query -> _embed_endpoint(), hardcoded to
# http://localhost:11434/v1 regardless of chat provider — Ollama Cloud's API
# has no /v1/embeddings) and fall back to a real SQLite FTS5/BM25 keyword
# search only when that real embedding call genuinely fails or no vector
# index exists yet. Every test below parses the tool's actual JSON return
# from the real RunResult message history (a ToolReturnPart), not the
# agent's final text answer, so `search_mode` is checked against what the
# model actually saw, not inferred from prose.


def _find_tool_calls(run_result, tool_name: str) -> list[dict]:
    """Parse every real JSON return for `tool_name` out of the actual message
    history (ToolReturnPart), not just the agent's final text answer."""
    from pydantic_ai.messages import ToolReturnPart

    calls = []
    for msg in run_result.all_messages():
        if hasattr(msg, "parts"):
            for part in msg.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name == tool_name:
                    content = part.content
                    if isinstance(content, str):
                        with contextlib.suppress(json.JSONDecodeError):
                            calls.append(json.loads(content))
    return calls


@contextlib.contextmanager
def _broken_embedding_env(monkeypatch, bad_model="model-that-does-not-exist-xyz:latest"):
    """Make rag.py's embedding calls genuinely fail for the duration of the
    block — zero Python code mocked. rag.py always embeds via a *local*
    Ollama server regardless of chat provider (see rag._embed_endpoint():
    hardcoded to http://localhost:11434/v1, since Ollama Cloud's API exposes
    no /v1/embeddings), and that base URL has no independent env-var
    override. So the cleanest all-real trigger is:
      1. A temp `.env` (via GRC_AGENT_ENV) naming an embedding model the
         local Ollama server genuinely does not have — every real HTTP call
         to it 404s for real.
      2. A fresh, empty GRC_AGENT_VECTORS_DIR so no pre-built (working-model)
         vector index masks the failure — the catalog/docs DB gets rebuilt
         from scratch, lexical (FTS5) only, within this same run.
    Also resets rag.py's small settings/env-value/freshness memoization
    caches around the swap so the real settings module actually re-reads the
    temp `.env` instead of serving a value cached under the previous file's
    mtime.
    """
    from grc_agent.adapter import rag

    tmp_dir = tempfile.mkdtemp()
    fake_env = Path(tmp_dir) / "broken_embedding.env"
    fake_env.write_text(f"GRC_PROVIDER=ollama_cloud\nOLLAMA_EMBEDDING_MODEL={bad_model}\n")
    vectors_dir = Path(tmp_dir) / "vectors"
    vectors_dir.mkdir()

    monkeypatch.setenv("GRC_AGENT_ENV", str(fake_env))
    monkeypatch.setenv("GRC_AGENT_VECTORS_DIR", str(vectors_dir))
    rag._settings_cache = None
    rag._env_value_cache = None
    rag._FRESHNESS_CACHE = {}
    rag._embed_client_state = None
    try:
        yield
    finally:
        rag._settings_cache = None
        rag._env_value_cache = None
        rag._FRESHNESS_CACHE = {}
        rag._embed_client_state = None
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_query_knowledge_catalog_vector_search_real():
    """Baseline: with the real local Ollama embedding backend reachable, a
    real Ollama-Cloud-driven agent's catalog query_knowledge call comes back
    search_mode == "vector" with a genuinely relevant top hit."""
    agent, fg, _tmp, tmp_dir = _build_cloud_agent(_DIAL_TONE)
    try:
        res = agent.run_sync(
            "Use query_knowledge with the catalog domain to find the GNU"
            " Radio block for AGC (automatic gain control). Just tell me the"
            " block id you find — don't change the graph.",
            deps=fg,
        )
        calls = _find_tool_calls(res, "query_knowledge")
        assert calls, "agent never called query_knowledge"
        assert any(c.get("search_mode") == "vector" for c in calls), (
            f"expected a vector-mode query_knowledge result, got: {calls}"
        )
        block_ids = [
            r.get("block_id", "")
            for c in calls
            if c.get("search_mode") == "vector"
            for r in c.get("results", [])
        ]
        assert any("agc" in b.lower() for b in block_ids), (
            f"no AGC block among vector-mode results: {block_ids}"
        )
    finally:
        shutil.rmtree(tmp_dir)


def test_query_knowledge_catalog_lexical_fallback_real(monkeypatch):
    """With the local embedding backend genuinely unreachable (bad model name
    + fresh vector-DB dir, zero code mocked), the real Ollama-Cloud agent's
    catalog query_knowledge call still returns a real, if lower-quality,
    result tagged search_mode == "lexical", and the agent still completes a
    sensible response using that fallback data. This is the primary gap this
    task targets: real end-to-end coverage of the SQLite FTS5 lexical
    fallback added to rag.py's query_catalog/query_docs."""
    with _broken_embedding_env(monkeypatch):
        agent, fg, _tmp, tmp_dir = _build_cloud_agent(_DIAL_TONE)
        try:
            res = agent.run_sync(
                "Use query_knowledge with the catalog domain to look up the"
                " GNU Radio block that computes the complex conjugate of a"
                " complex signal. Just tell me the block id — don't change"
                " the graph.",
                deps=fg,
            )
            calls = _find_tool_calls(res, "query_knowledge")
            assert calls, "agent never called query_knowledge"
            assert any(c.get("search_mode") == "lexical" for c in calls), (
                f"expected a lexical-mode result under embedding outage, got: {calls}"
            )
            block_ids = [
                r.get("block_id", "")
                for c in calls
                if c.get("search_mode") == "lexical"
                for r in c.get("results", [])
            ]
            assert any("conjugate" in b for b in block_ids), (
                f"no conjugate block among lexical-mode results: {block_ids}"
            )
            assert res.output is not None
        finally:
            shutil.rmtree(tmp_dir)


def test_query_knowledge_docs_lexical_fallback_real(monkeypatch):
    """Mirror of the catalog lexical-fallback test for the docs domain: with
    the embedding backend genuinely unreachable, the real agent's docs
    query_knowledge call comes back search_mode == "lexical" with real
    keyword-matched content, and the agent still answers sensibly from it."""
    with _broken_embedding_env(monkeypatch):
        agent, fg, _tmp, tmp_dir = _build_cloud_agent(_DIAL_TONE)
        try:
            res = agent.run_sync(
                "Use query_knowledge with the docs domain to explain what a"
                " 'stream tag' is in GNU Radio. Don't change the graph.",
                deps=fg,
            )
            calls = _find_tool_calls(res, "query_knowledge")
            assert calls, "agent never called query_knowledge"
            assert any(c.get("search_mode") == "lexical" for c in calls), (
                f"expected a lexical-mode result under embedding outage, got: {calls}"
            )
            answers = [c.get("answer", "") for c in calls if c.get("search_mode") == "lexical"]
            assert any("tag" in a.lower() for a in answers), (
                f"lexical docs answer doesn't mention tags: {answers}"
            )
            assert res.output is not None
        finally:
            shutil.rmtree(tmp_dir)


def test_query_knowledge_catalog_lexical_bm25_quality_real(monkeypatch):
    """Exercises real BM25 ranking quality specifically, not just that the
    fallback triggers: an acronym-heavy query ("AGC") is exactly the kind of
    literal-keyword input BM25 handles well but embeddings can blur. A
    correct top hit here under a forced embedding outage is evidence the
    lexical index is a genuinely usable fallback, not merely a
    triggered-but-useless code path."""
    with _broken_embedding_env(monkeypatch):
        agent, fg, _tmp, tmp_dir = _build_cloud_agent(_DIAL_TONE)
        try:
            res = agent.run_sync(
                "Use query_knowledge with the catalog domain to find the GNU"
                " Radio block for AGC (automatic gain control). Just tell me"
                " the block id — don't change the graph.",
                deps=fg,
            )
            calls = _find_tool_calls(res, "query_knowledge")
            assert calls, "agent never called query_knowledge"
            assert any(c.get("search_mode") == "lexical" for c in calls), (
                f"expected a lexical-mode result under embedding outage, got: {calls}"
            )
            block_ids = [
                r.get("block_id", "")
                for c in calls
                if c.get("search_mode") == "lexical"
                for r in c.get("results", [])
            ]
            assert any("agc" in b.lower() for b in block_ids), (
                f"no AGC block among lexical-mode BM25 results: {block_ids}"
            )
        finally:
            shutil.rmtree(tmp_dir)
