import shutil
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent, ModelSettings
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.providers.ollama import OllamaProvider

# Import components from grc_agent.agent
from grc_agent.agent import (
    MODEL,
    OLLAMA_V1,
    SCENARIOS,
    GrcAgentResponse,
    StopGracefully,
    build_system_prompt,
    check_expect,
    fresh_agent,
    grc_tools,
    prune_history,
    render_scenario_markdown,
    validate_flowgraph_state,
    web_fetch_cap,
    web_search_cap,
)

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
def test_scenario_execution(sc_name):
    # Find the target scenario by name
    sc = next((s for s in SCENARIOS if s["name"] == sc_name), None)
    assert sc is not None, f"Scenario {sc_name} not found in SCENARIOS list."

    # Track the raw GRC file content before running the agent
    grc_before = Path(sc["fixture"]).read_text(encoding="utf-8")
    fg, fixture_path, tmp_dir = fresh_agent(sc["fixture"])

    try:
        # Initialize Ollama model agent with clean-room capability bundle
        agent = Agent(
            OllamaModel(MODEL, provider=OllamaProvider(base_url=OLLAMA_V1)),
            deps_type=Any,
            output_type=[GrcAgentResponse, str],
            name="grc_scenario_test_agent",
            instructions=build_system_prompt("pai-experiment-test"),
            tools=grc_tools(),
            capabilities=[
                ProcessHistory(prune_history),
                StopGracefully(),
                web_search_cap,
                web_fetch_cap,
            ],
            model_settings=ModelSettings(extra_body={"think": True}),
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
        (output_dir / f"{sc['name']}.md").write_text(md_log, encoding="utf-8")

        assert verdict["pass"] is True, (
            f"Scenario expectation check failed. Reasons: {verdict['reasons']}"
        )

    finally:
        shutil.rmtree(tmp_dir)
