import contextlib
import json
import os
import shutil
import socket
import tempfile
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv
from pydantic_ai import Agent, ModelSettings

from grc_agent.settings import env_path

load_dotenv(env_path())

# Import components from grc_agent.agent
from grc_agent.agent import (  # noqa: E402
    SCENARIOS,
    GrcAgentResponse,
    StopGracefully,
    build_scenario_model,
    check_expect,
    fresh_agent,
    grc_tools,
    render_scenario_markdown,
    validate_flowgraph_state,
    web_fetch_cap,
    web_search_cap,
)
from grc_agent.prompts import build_system_prompt  # noqa: E402


def _ollama_available() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 11434), timeout=0.5):
            return True
    except OSError:
        return False


def _ollama_cloud_available() -> bool:
    return bool(os.getenv("OLLAMA_CLOUD_API_KEY"))


def _openrouter_available() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY"))


_BACKEND_AVAILABILITY = {
    "ollama": _ollama_available,
    "ollama_cloud": _ollama_cloud_available,
    "openrouter": _openrouter_available,
}


def _selected_backends():
    """Backends the scenario suite can run against. Override via
    GRC_TEST_BACKEND=ollama|ollama_cloud|openrouter to force one.

    With no override, this prefers Ollama Cloud alone (the project's standard
    real-LLM backend for tests) when it's configured — it deliberately does
    NOT union every detected backend by default, since a dev machine that
    also happens to have local Ollama running and/or OPENROUTER_API_KEY set
    would otherwise silently run the whole scenario suite 2-3x, against
    backends other than the intended one. Only falls back to running
    whatever combination of local ollama/openrouter is available if Ollama
    Cloud itself isn't configured.
    """
    forced = os.getenv("GRC_TEST_BACKEND")
    if forced:
        return [forced]
    if _BACKEND_AVAILABILITY["ollama_cloud"]():
        return ["ollama_cloud"]
    return [name for name, check in _BACKEND_AVAILABILITY.items() if check()]


_AVAILABLE_BACKENDS = _selected_backends()
if not _AVAILABLE_BACKENDS:
    pytest.skip(
        "No LLM backend available. Set OLLAMA_CLOUD_API_KEY (preferred), "
        "OPENROUTER_API_KEY, or start Ollama on 127.0.0.1:11434, or force one "
        "with GRC_TEST_BACKEND=ollama|ollama_cloud|openrouter.",
        allow_module_level=True,
    )


# Default chat model for OpenRouter scenarios. The agent.py harness keeps its
# own fixed MODEL constant for Ollama; OpenRouter uses whatever the caller
# points at.
_OPENROUTER_DEFAULT_MODEL = os.getenv("GRC_OPENROUTER_MODEL", "openai/gpt-4o-mini")


def _build_model_for_backend(backend: str):
    if backend == "openrouter":
        return build_scenario_model("openrouter", _OPENROUTER_DEFAULT_MODEL)
    if backend == "ollama_cloud":
        return build_scenario_model(
            "ollama_cloud", os.getenv("OLLAMA_CLOUD_MODEL", "deepseek-v4-flash:cloud")
        )
    return build_scenario_model("ollama")


SELECTED_SCENARIOS = [
    "01_add_throttle",
    "02_update_sample_rate",
    "03_disable_and_enable",
    "04_add_and_remove_variable",
    "05_full_rewire",
    "06_query_knowledge_multiply",
    "09_docs_stream_tags_concept",
    "10_bypass_source_block",
    "11_scoped_inspect_and_update",
    "14_build_chain_from_scratch",
    # "21_type_conversion_and_conjugate" was defined in SCENARIOS (agent.py) from
    # the very first commit that introduced this file (24f4417, "Complete
    # codebase reorganization...") but was never added to any run list —
    # neither the old PydanticAI_experiment/src/run.py's GRC_AGENT_PAI_SCENARIOS
    # filter (default "01,11") nor this SELECTED_SCENARIOS list, in any commit
    # since (`git log -S "21_type_conversion_and_conjugate"` across all
    # branches turns up no commit that mentions it being flaky/slow/excluded
    # on purpose — it simply never made either allowlist). Re-run 3/3 for real
    # against ollama_cloud here (~12-17s each, all passed) turned up no
    # flakiness or slowness that would explain the omission, so it reads as a
    # plain oversight rather than a deliberate exclusion. Included below.
    "21_type_conversion_and_conjugate",
    "22_fm_rx_filter_squelch",
    "24_generate_python_preview",
]


@pytest.mark.parametrize("sc_name", SELECTED_SCENARIOS)
@pytest.mark.parametrize("backend", _AVAILABLE_BACKENDS)
def test_scenario_execution(sc_name, backend):
    # Find the target scenario by name
    sc = next((s for s in SCENARIOS if s["name"] == sc_name), None)
    assert sc is not None, f"Scenario {sc_name} not found in SCENARIOS list."

    # Track the raw GRC file content before running the agent
    grc_before = Path(sc["fixture"]).read_text(encoding="utf-8")
    fg, fixture_path, tmp_dir = fresh_agent(sc["fixture"])

    try:
        # Initialize the model for the selected backend. Ollama keeps the
        # fixed MODEL constant for reproducibility; OpenRouter uses the
        # configured model name.
        model = _build_model_for_backend(backend)
        agent = Agent(
            model,
            deps_type=Any,
            output_type=[GrcAgentResponse, str],
            name=f"grc_scenario_test_agent_{backend}",
            instructions=build_system_prompt("pai-experiment-test"),
            tools=grc_tools(),
            capabilities=[
                StopGracefully(),
                web_search_cap,
                web_fetch_cap,
            ],
            model_settings=ModelSettings(extra_body={"think": True}),
            retries={"tools": 3, "output": 3},
        )
        agent.output_validator(validate_flowgraph_state)

        # Run agent transaction loop
        res = agent.run_sync(sc["prompt"], deps=fg)

        # Validate the expect constraints
        verdict = check_expect(fixture_path, sc["expect"], run_result=res)

        # Build output directory and save formatted markdown logs
        output_dir = Path("tests/output")
        output_dir.mkdir(parents=True, exist_ok=True)
        md_log = render_scenario_markdown(sc, grc_before, res, verdict)
        (output_dir / f"{sc['name']}_{backend}.md").write_text(md_log, encoding="utf-8")

        assert verdict["pass"] is True, (
            f"Scenario expectation check failed ({backend}). Reasons: {verdict['reasons']}"
        )

    finally:
        shutil.rmtree(tmp_dir)


# --- Lexical (FTS5/BM25) RAG fallback, exercised through the full scenario harness ---


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
         vector index masks the failure — the catalog DB gets rebuilt from
         scratch, lexical (FTS5) only, within this same run.
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


@pytest.mark.parametrize("backend", _AVAILABLE_BACKENDS)
def test_scenario_generate_python_writes_nothing_to_disk(backend):
    """Dedicated verification for the generate_python tool, run through the
    full live-agent loop rather than calling adapter.preview_flowgraph_py()
    directly (tests/test_unit.py already covers that in isolation). Proves
    three things a real LLM turn could still get wrong even though the unit
    tests pass: the model actually calls generate_python (not just some
    other read tool), the tool hands back real generated Python source (not
    an empty/placeholder result), and — the tool's actual load-bearing
    promise — the fixture's temp directory holds exactly the same files
    after the turn as before it, i.e. nothing was written to disk.
    """
    sc = next(s for s in SCENARIOS if s["name"] == "24_generate_python_preview")
    grc_before = Path(sc["fixture"]).read_text(encoding="utf-8")
    fg, fixture_path, tmp_dir = fresh_agent(sc["fixture"])
    before = set(Path(tmp_dir).iterdir())

    try:
        model = _build_model_for_backend(backend)
        agent = Agent(
            model,
            deps_type=Any,
            output_type=[GrcAgentResponse, str],
            name=f"grc_scenario_test_agent_{backend}_generate_python",
            instructions=build_system_prompt("pai-experiment-test"),
            tools=grc_tools(),
            capabilities=[StopGracefully(), web_search_cap, web_fetch_cap],
            model_settings=ModelSettings(extra_body={"think": True}),
            retries={"tools": 3, "output": 3},
        )
        agent.output_validator(validate_flowgraph_state)

        res = agent.run_sync(sc["prompt"], deps=fg)

        calls = _find_tool_calls(res, "generate_python")
        assert calls, "agent never called generate_python"
        files = calls[-1].get("files") or []
        assert files, f"generate_python returned no files: {calls[-1]}"
        assert any("import" in f.get("source", "") for f in files), (
            f"expected real generated Python source, got: {files}"
        )

        after = set(Path(tmp_dir).iterdir())
        assert before == after, (
            f"generate_python must never write to disk — new entries: {after - before}"
        )

        verdict = check_expect(fixture_path, sc["expect"], run_result=res)

        output_dir = Path("tests/output")
        output_dir.mkdir(parents=True, exist_ok=True)
        md_log = render_scenario_markdown(sc, grc_before, res, verdict)
        (output_dir / f"{sc['name']}_{backend}.md").write_text(md_log, encoding="utf-8")

        assert verdict["pass"] is True, (
            f"Scenario expectation check failed ({backend}). Reasons: {verdict['reasons']}"
        )
    finally:
        shutil.rmtree(tmp_dir)


def test_scenario_lexical_fallback_ollama_cloud_only(monkeypatch):
    """The heavier SCENARIOS/run_sync harness, run under a real embedding
    outage. Backend is hardcoded to ollama_cloud (never parametrized) so this
    can never silently run against local ollama/openrouter in another
    environment, per this task's requirement. Proves the "23_lexical_conjugate_insert"
    scenario completes a real graph edit end-to-end using only a lexically
    retrieved (search_mode == "lexical") catalog lookup — not just that
    query_catalog() in isolation falls back, but that the full agent loop
    (real Ollama Cloud chat model + real change_graph/inspect_graph tool
    execution against a real temp-copied .grc fixture) still succeeds using
    that lower-quality-but-real fallback data.
    """
    if not _ollama_cloud_available():
        pytest.skip("OLLAMA_CLOUD_API_KEY not set — skipping Ollama Cloud integration test.")

    sc = next(s for s in SCENARIOS if s["name"] == "23_lexical_conjugate_insert")
    grc_before = Path(sc["fixture"]).read_text(encoding="utf-8")
    fg, fixture_path, tmp_dir = fresh_agent(sc["fixture"])

    with _broken_embedding_env(monkeypatch):
        try:
            model = build_scenario_model(
                "ollama_cloud", os.getenv("OLLAMA_CLOUD_MODEL", "deepseek-v4-flash:cloud")
            )
            agent = Agent(
                model,
                deps_type=Any,
                output_type=[GrcAgentResponse, str],
                name="grc_scenario_test_agent_ollama_cloud_lexical_fallback",
                instructions=build_system_prompt("pai-experiment-test"),
                tools=grc_tools(),
                capabilities=[StopGracefully(), web_search_cap, web_fetch_cap],
                model_settings=ModelSettings(extra_body={"think": True}),
                retries={"tools": 3, "output": 3},
            )
            agent.output_validator(validate_flowgraph_state)

            res = agent.run_sync(sc["prompt"], deps=fg)

            calls = _find_tool_calls(res, "query_knowledge")
            assert calls, "agent never called query_knowledge"
            assert any(c.get("search_mode") == "lexical" for c in calls), (
                f"expected a lexical-mode query_knowledge result under embedding "
                f"outage, got: {calls}"
            )

            verdict = check_expect(fixture_path, sc["expect"], run_result=res)

            output_dir = Path("tests/output")
            output_dir.mkdir(parents=True, exist_ok=True)
            md_log = render_scenario_markdown(sc, grc_before, res, verdict)
            (output_dir / f"{sc['name']}_ollama_cloud_lexical_fallback.md").write_text(
                md_log, encoding="utf-8"
            )

            assert verdict["pass"] is True, (
                f"Scenario expectation check failed. Reasons: {verdict['reasons']}"
            )
        finally:
            shutil.rmtree(tmp_dir)
