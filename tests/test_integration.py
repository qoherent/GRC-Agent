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
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults, ToolApproved

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


def _run_with_auto_approvals(agent: Agent, prompt: str | None = None, **kwargs: Any):
    res = agent.run_sync(prompt, **kwargs)
    call_kwargs = {k: v for k, v in kwargs.items() if k != "message_history"}
    while isinstance(res.output, DeferredToolRequests):
        res = agent.run_sync(
            message_history=res.all_messages(),
            deferred_tool_results=DeferredToolResults(
                approvals={c.tool_call_id: ToolApproved() for c in res.output.approvals}
            ),
            **call_kwargs,
        )
    return res


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


def _openai_compatible_available() -> bool:
    return bool(os.getenv("OPENAI_COMPATIBLE_BASE_URL"))


_BACKEND_AVAILABILITY = {
    "ollama": _ollama_available,
    "openai_compatible": _openai_compatible_available,
    "ollama_cloud": _ollama_cloud_available,
    "openrouter": _openrouter_available,
}


def _selected_backends():
    """Backends the scenario suite can run against. Override via
    GRC_TEST_BACKEND=ollama|openai_compatible|ollama_cloud|openrouter to force one.

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
        if forced not in _BACKEND_AVAILABILITY:
            raise SystemExit(
                f"Unknown GRC_TEST_BACKEND={forced!r}; expected one of "
                f"{sorted(_BACKEND_AVAILABILITY)}. A typo must fail loudly,"
                " never silently fall back to local Ollama."
            )
        return [forced]
    if _BACKEND_AVAILABILITY["ollama_cloud"]():
        return ["ollama_cloud"]
    return [name for name, check in _BACKEND_AVAILABILITY.items() if check()]


_AVAILABLE_BACKENDS = _selected_backends()
if not _AVAILABLE_BACKENDS:
    pytest.skip(
        "No LLM backend available. Set OLLAMA_CLOUD_API_KEY (preferred), "
        "OPENROUTER_API_KEY, or start Ollama on 127.0.0.1:11434, or force one "
        "with GRC_TEST_BACKEND=ollama|ollama_cloud|openrouter|openai_compatible.",
        allow_module_level=True,
    )


# Default chat model for OpenRouter scenarios. The agent.py harness keeps its
# own fixed MODEL constant for Ollama; OpenRouter uses whatever the caller
# points at.
_OPENROUTER_DEFAULT_MODEL = os.getenv("GRC_OPENROUTER_MODEL", "openai/gpt-4o-mini")


def _build_model_for_backend(backend: str):
    if backend in ("openrouter", "openai_compatible"):
        return build_scenario_model(backend, _OPENROUTER_DEFAULT_MODEL)
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
    # "25_save_epy_block_to_library" is deliberately NOT in this generic
    # list: unlike every other scenario (confined to fresh_agent()'s
    # tempfile.mkdtemp()-copied .grc fixture, rmtree'd after), save_block
    # writes real files to Config.hier_block_lib_dir (~/.grc_gnuradio by
    # default) — a genuine external side effect this generic runner has no
    # isolation for. It gets its own dedicated test below (matching
    # test_scenario_generate_python_writes_nothing_to_disk's precedent for
    # a tool whose disk behavior itself needs asserting), which redirects
    # Config.hier_block_lib_dir to a temp dir for its duration.
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
            output_type=[GrcAgentResponse, str, DeferredToolRequests],
            name=f"grc_scenario_test_agent_{backend}",
            instructions=build_system_prompt("pai-experiment-test"),
            tools=grc_tools(),
            capabilities=[
                StopGracefully(),
                web_search_cap,
                web_fetch_cap,
            ],
            model_settings=ModelSettings(),
            retries={"tools": 3, "output": 3},
        )
        agent.output_validator(validate_flowgraph_state)

        # Run agent transaction loop
        res = _run_with_auto_approvals(agent, sc["prompt"], deps=fg)

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
def _broken_embedding_env(monkeypatch):
    """Make rag.py's embedding calls fail for the duration of the
    block — zero Python code mocked. Setting GRC_EMBED_BACKEND=llamacpp with an
    empty runtime directory means ensure_server fails and triggers the lexical
    (FTS5/BM25) fallback."""
    from grc_agent.adapter import rag

    tmp_dir = tempfile.mkdtemp()
    fake_env = Path(tmp_dir) / "broken_embedding.env"
    fake_env.write_text("GRC_PROVIDER=ollama_cloud\nGRC_EMBED_BACKEND=llamacpp\n")
    vectors_dir = Path(tmp_dir) / "vectors"
    vectors_dir.mkdir()
    runtime_dir = Path(tmp_dir) / "empty_runtime"
    runtime_dir.mkdir()

    monkeypatch.setenv("GRC_AGENT_ENV", str(fake_env))
    monkeypatch.setenv("GRC_AGENT_VECTORS_DIR", str(vectors_dir))
    monkeypatch.setenv("GRC_AGENT_RUNTIME_DIR", str(runtime_dir))
    rag._FRESHNESS_CACHE = {}
    rag._embed_client_state = None
    try:
        yield
    finally:
        rag._FRESHNESS_CACHE = {}
        rag._embed_client_state = None
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.parametrize("backend", _AVAILABLE_BACKENDS)
def test_scenario_generate_python_writes_nothing_to_disk(backend):
    """Dedicated verification for the generate_python tool, run through the
    full live-agent loop rather than calling adapter.preview_flowgraph_py()
    directly (the unit suite already covers that in isolation). Proves
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
            output_type=[GrcAgentResponse, str, DeferredToolRequests],
            name=f"grc_scenario_test_agent_{backend}_generate_python",
            instructions=build_system_prompt("pai-experiment-test"),
            tools=grc_tools(),
            capabilities=[StopGracefully(), web_search_cap, web_fetch_cap],
            model_settings=ModelSettings(),
            retries={"tools": 3, "output": 3},
        )
        agent.output_validator(validate_flowgraph_state)

        res = _run_with_auto_approvals(agent, sc["prompt"], deps=fg)

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


@contextlib.contextmanager
def _isolated_hier_block_lib_dir(tmp_path):
    """Redirects GNU Radio's Config.hier_block_lib_dir to a fresh tmp dir
    for the duration of the block, then restores it and rebuilds the real
    get_platform() singleton (see tests/conftest.py's
    temp_hier_block_lib_dir fixture): Platform.block_classes is a single
    ChainMap shared by every headless Platform instance (a Platform CLASS
    attribute) — leaving the singleton's registry pointed at a
    now-deleted tmp dir would silently corrupt every later test's view of
    get_platform().blocks.
    """
    from gnuradio.grc.core.Config import Config

    from grc_agent.adapter.graph import get_platform

    lib_dir = tmp_path / "grc_gnuradio"
    lib_dir.mkdir()
    original = Config.hier_block_lib_dir
    Config.hier_block_lib_dir = str(lib_dir)
    try:
        yield lib_dir
    finally:
        Config.hier_block_lib_dir = original
        get_platform().build_library()


@pytest.mark.parametrize("backend", _AVAILABLE_BACKENDS)
def test_scenario_save_block_writes_to_isolated_hier_dir(backend, tmp_path):
    """Dedicated verification for the save_block tool, run through the full
    live-agent loop (the unit suite already covers save_block_to_library
    in isolation). Every other scenario is confined to fresh_agent()'s
    tempfile.mkdtemp()-copied .grc fixture — save_block is the first tool
    with a genuine external side effect (writing into GNU Radio's hier-block
    library), so this redirects Config.hier_block_lib_dir to a temp dir for
    the run's duration instead of writing into whoever's real
    ~/.grc_gnuradio actually runs this suite.
    """
    sc = next(s for s in SCENARIOS if s["name"] == "25_save_epy_block_to_library")
    grc_before = Path(sc["fixture"]).read_text(encoding="utf-8")
    fg, fixture_path, tmp_dir = fresh_agent(sc["fixture"])

    try:
        with _isolated_hier_block_lib_dir(tmp_path) as lib_dir:
            model = _build_model_for_backend(backend)
            agent = Agent(
                model,
                deps_type=Any,
                output_type=[GrcAgentResponse, str, DeferredToolRequests],
                name=f"grc_scenario_test_agent_{backend}_save_block",
                instructions=build_system_prompt("pai-experiment-test"),
                tools=grc_tools(),
                capabilities=[StopGracefully(), web_search_cap, web_fetch_cap],
                model_settings=ModelSettings(),
                retries={"tools": 3, "output": 3},
            )
            agent.output_validator(validate_flowgraph_state)

            res = _run_with_auto_approvals(agent, sc["prompt"], deps=fg)

            calls = _find_tool_calls(res, "save_block")
            assert calls, "agent never called save_block"
            assert calls[-1].get("ok") is True, f"save_block call failed: {calls[-1]}"

            saved_yml = Path(calls[-1]["saved_to"]["block_yml"])
            assert saved_yml.parent == lib_dir, (
                f"save_block wrote outside the isolated tmp dir: {saved_yml}"
            )
            assert saved_yml.exists()

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
                output_type=[GrcAgentResponse, str, DeferredToolRequests],
                name="grc_scenario_test_agent_ollama_cloud_lexical_fallback",
                instructions=build_system_prompt("pai-experiment-test"),
                tools=grc_tools(),
                capabilities=[StopGracefully(), web_search_cap, web_fetch_cap],
                model_settings=ModelSettings(),
                retries={"tools": 3, "output": 3},
            )
            agent.output_validator(validate_flowgraph_state)

            res = _run_with_auto_approvals(agent, sc["prompt"], deps=fg)

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

