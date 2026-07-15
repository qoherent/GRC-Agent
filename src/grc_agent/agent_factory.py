from typing import Any

import httpx
from pydantic_ai import Agent, ModelSettings
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from grc_agent.agent import (
    OLLAMA_V1,
    GrcAgentResponse,
    StopGracefully,
    grc_tools,
    prune_history,
    validate_flowgraph_state,
    web_fetch_cap,
    web_search_cap,
)
from grc_agent.prompts import build_system_prompt
from grc_agent.settings import default_settings, get_env_value, load_settings


def _retrying_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=AsyncTenacityTransport(
            config=RetryConfig(
                retry=retry_if_exception_type(
                    (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException, httpx.HTTPStatusError)
                ),
                wait=wait_exponential(multiplier=1, max=10),
                stop=stop_after_attempt(3),
                reraise=True,
            )
        )
    )


def _build_model(cfg: dict, http_client: httpx.AsyncClient):
    if cfg["provider"] == "openrouter":
        key = get_env_value("OPENROUTER_API_KEY") or ""
        return OpenRouterModel(cfg["model"], provider=OpenRouterProvider(api_key=key))
    if cfg["provider"] == "ollama_cloud":
        key = get_env_value("OLLAMA_CLOUD_API_KEY") or ""
        return OllamaModel(
            cfg["model"],
            provider=OllamaProvider(base_url="https://ollama.com/v1", api_key=key),
        )
    return OllamaModel(
        cfg["model"],
        provider=OllamaProvider(base_url=OLLAMA_V1, http_client=http_client),
    )


def build_interactive_agent() -> tuple[Agent, str | None]:
    http_client = _retrying_http_client()
    cfg = load_settings()
    model_build_error: str | None = None
    try:
        model = _build_model(cfg, http_client)
    except Exception as e:
        print(f"[grc-agent] Failed to build chat model from saved settings: {e}")
        print("[grc-agent] Falling back to Ollama defaults so the app can still start.")
        model_build_error = str(e)
        cfg = default_settings()
        model = _build_model(cfg, http_client)

    is_ollama = cfg["provider"] in ("ollama", "ollama_cloud")
    model_settings = ModelSettings(extra_body={"think": True}) if is_ollama else ModelSettings()

    agent = Agent(
        model=model,
        deps_type=Any,
        output_type=[GrcAgentResponse, str],
        name="grc_desktop_chat_agent",
        instructions=build_system_prompt("pai-desktop-chat"),
        tools=grc_tools(),
        capabilities=[
            ProcessHistory(prune_history),
            StopGracefully(),
            web_search_cap,
            web_fetch_cap,
        ],
        model_settings=model_settings,
        retries={"tools": 3, "output": 3},
    )
    agent.output_validator(validate_flowgraph_state)
    return agent, model_build_error
