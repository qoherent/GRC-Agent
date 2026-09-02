"""Tests for the scenario harness that drives the integration suites.

The harness itself is test-side (``tests/scenarios/harness.py``); this file
covers the parts of it that need no live backend, so a regression in the
model builder fails the fast gate rather than only surfacing when someone
runs the integration suite with keys configured.
"""

from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel
from scenarios.harness import build_scenario_model


def test_scenario_model_builder_uses_provider(monkeypatch):
    """Regression for P2-7: the scenario harness must be able to build a model
    for either backend so integration tests can run against Ollama or OpenAI-compatible."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy-test-key")
    ollama = build_scenario_model("ollama")
    assert isinstance(ollama, OllamaModel)

    ollama_cloud = build_scenario_model("ollama_cloud", "deepseek-v4-flash:0731")
    assert isinstance(ollama_cloud, OllamaModel)
    assert ollama_cloud.model_name == "deepseek-v4-flash:0731"

    openrouter = build_scenario_model("openrouter", "z-ai/glm-5.3-flash")
    assert isinstance(openrouter, OpenAIChatModel)
    assert openrouter.model_name == "z-ai/glm-5.3-flash"

    openai_compat = build_scenario_model("openai_compatible", "my-custom-model")
    assert isinstance(openai_compat, OpenAIChatModel)
    assert openai_compat.model_name == "my-custom-model"
