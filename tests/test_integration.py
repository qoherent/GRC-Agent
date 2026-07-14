import os
import shutil
import socket
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv
from pydantic_ai import Agent, ModelSettings
from pydantic_ai.capabilities import ProcessHistory

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
    prune_history,
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
    GRC_TEST_BACKEND=ollama|openrouter to force one."""
    forced = os.getenv("GRC_TEST_BACKEND")
    if forced:
        return [forced]
    return [name for name, check in _BACKEND_AVAILABILITY.items() if check()]


_AVAILABLE_BACKENDS = _selected_backends()
if not _AVAILABLE_BACKENDS:
    pytest.skip(
        "No LLM backend available. Set OPENROUTER_API_KEY or start Ollama on "
        "127.0.0.1:11434, or force one with GRC_TEST_BACKEND=ollama|openrouter.",
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
    "22_fm_rx_filter_squelch",
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
                ProcessHistory(prune_history),
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
