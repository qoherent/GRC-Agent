import logging
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic_ai import Agent, ModelSettings, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from grc_agent.agent import (
    OLLAMA_V1,
    GrcAgentResponse,
    StopGracefully,
    grc_tools,
    validate_flowgraph_state,
    web_fetch_cap,
    web_search_cap,
)
from grc_agent.prompts import build_system_prompt
from grc_agent.settings import default_settings, get_env_value, load_settings

_log = logging.getLogger(__name__)


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
            ),
            validate_response=lambda r: r.raise_for_status(),
        )
    )


def _build_model(cfg: dict, http_client: httpx.AsyncClient):
    if cfg["provider"] == "openrouter":
        key = get_env_value("OPENROUTER_API_KEY") or ""
        return OpenRouterModel(cfg["model"], provider=OpenRouterProvider(api_key=key, http_client=http_client))
    if cfg["provider"] == "ollama_cloud":
        key = get_env_value("OLLAMA_CLOUD_API_KEY") or ""
        if not key:
            # OllamaProvider itself never raises on a missing key — it silently
            # substitutes a placeholder ('api-key-not-set') and the failure only
            # surfaces as an HTTP 401 on the first real chat call. Raise here so
            # this degrades the same way the openrouter branch already does
            # (OpenRouterProvider raises UserError on an empty key, caught below).
            raise ValueError(
                "OLLAMA_CLOUD_API_KEY is not set. Configure it in Settings or the .env file to use Ollama Cloud."
            )
        return OllamaModel(
            cfg["model"],
            provider=OllamaProvider(base_url="https://ollama.com/v1", api_key=key, http_client=http_client),
        )
    if cfg["provider"] == "openai_compatible":
        key = get_env_value("OPENAI_COMPATIBLE_API_KEY") or cfg.get("openai_compatible_api_key") or "not-required"
        raw_url = (cfg.get("openai_compatible_base_url") or get_env_value("OPENAI_COMPATIBLE_BASE_URL") or "http://localhost:8080/v1").rstrip("/")
        base_url = raw_url if raw_url.endswith("/v1") else f"{raw_url}/v1"
        return OpenAIChatModel(
            cfg["model"],
            provider=OpenAIProvider(base_url=base_url, api_key=key, http_client=http_client),
        )
    raw_url = (cfg.get("ollama_base_url") or get_env_value("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
    base_url = raw_url if raw_url.endswith("/v1") else f"{raw_url}/v1"
    return OllamaModel(
        cfg["model"],
        provider=OllamaProvider(base_url=base_url, http_client=http_client),
    )


@dataclass
class ModelRequestLogger(AbstractCapability[Any]):
    """Logs the active provider name, base_url, and model name once per model
    request. Makes a `ModelAPIError: Connection error.` debuggable — the next
    log line says exactly which backend was attempted, so a stale-Agent-after-
    settings-swap (or any other provider/endpoint confusion) is visible
    immediately instead of being inferred from a stack trace.

    Uses `before_model_request` (the cheapest model-lifecycle hook — pure
    observation, no wrap) and reads provider/base_url off the live Model via
    its Provider, which both OllamaProvider and OpenRouterProvider expose as
    `name`/`base_url` properties.
    """

    async def before_model_request(  # type: ignore[override]
        self,
        ctx: RunContext[Any],  # noqa: ARG002
        request_context: Any,
    ) -> Any:
        model = request_context.model
        provider_name = "<unknown>"
        base_url = "<unknown>"
        model_name = getattr(model, "_model_name", getattr(model, "model_name", "<unknown>"))
        provider = getattr(model, "_provider", None) or getattr(model, "provider", None)
        if provider is not None:
            provider_name = getattr(provider, "name", provider_name)
            base_url = getattr(provider, "base_url", base_url)
        _log.info("model request -> provider=%s base_url=%s model=%s", provider_name, base_url, model_name)
        return request_context


def build_agent_from_cfg(cfg: dict) -> tuple[Agent, str | None]:
    """Construct a fresh Agent from an already-loaded settings dict.

    Shared between startup (`build_interactive_agent`) and live-swap (the
    Settings dialog's Save handler). Returns `(agent, model_build_error)` —
    on a model-construction failure, falls back to defaults and surfaces the
    error string so the caller can warn the user without crashing the app.
    """
    http_client = _retrying_http_client()
    model_build_error: str | None = None
    try:
        model = _build_model(cfg, http_client)
    except Exception as e:
        _log.warning("Failed to build chat model from cfg (provider=%s): %s", cfg.get("provider"), e)
        model_build_error = str(e)
        cfg = default_settings()
        model = _build_model(cfg, http_client)

    is_ollama = cfg["provider"] in ("ollama", "ollama_cloud")
    thinking = cfg.get("ollama_thinking_enabled", True)
    model_settings = ModelSettings(extra_body={"think": thinking}) if is_ollama else ModelSettings()

    from grc_agent.native_canvas import NativeFlowgraphProxy

    agent: Agent[NativeFlowgraphProxy, Any] = Agent(
        model=model,
        deps_type=NativeFlowgraphProxy,
        output_type=[GrcAgentResponse, str],
        name="grc_desktop_chat_agent",
        instructions=build_system_prompt("pai-desktop-chat"),
        tools=grc_tools(),
        capabilities=[
            StopGracefully(),
            ModelRequestLogger(),
            web_search_cap,
            web_fetch_cap,
        ],
        model_settings=model_settings,
        retries={"tools": 3, "output": 3},
    )

    @agent.instructions
    def add_active_flowgraph_context(ctx: RunContext[NativeFlowgraphProxy]) -> str | None:
        if ctx.deps is not None:
            cm = getattr(ctx.deps, "_canvas_manager", None)
            if cm and getattr(cm, "path", None):
                return f"Active flowgraph file path: {cm.path}"
        return None

    agent.output_validator(validate_flowgraph_state)
    return agent, model_build_error


def build_interactive_agent() -> tuple[Agent, str | None]:
    """Startup path — read .env via load_settings() and build the Agent.

    Kept as a thin wrapper over `build_agent_from_cfg` so `desktop_app.py`'s
    call site stays unchanged. Live-swap callers use `build_agent_from_cfg`
    directly so they can show a before/after diff to the user."""
    return build_agent_from_cfg(load_settings())


def preflight_connection(
    provider: str,
    api_key: str = "",
    *,
    ollama_base_url: str = "",
    timeout: float = 5.0,
) -> str | None:
    """Cheap sync reachability check against the configured provider's
    `GET /models`-equivalent. Returns None on success, an error string on any
    failure (connection refused, bad status, missing key, etc.).

    Sync intentionally — runs from the GTK Save handler and from startup
    (which is itself sync up to the gbulb loop.run_forever()). Bounded at
    `timeout` so a hung host fails fast instead of blocking the UI.

    Takes provider + api_key explicitly so the Save handler can validate a
    NEW config BEFORE writing it to .env (no save/restore dance), while
    startup resolves them from the already-loaded cfg/env.

    Endpoints:
      - openrouter:        GET https://openrouter.ai/api/v1/models (Bearer key)
      - ollama_cloud:      GET https://ollama.com/v1/models        (Bearer key)
      - openai_compatible: GET {base_url}/models                   (Optional Bearer key)
      - ollama:            GET {ollama_base_url}/api/tags         (no key)
    """
    try:
        if provider == "openrouter":
            if not api_key:
                return "OPENROUTER_API_KEY is not set"
            r = httpx.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
            )
        elif provider == "ollama_cloud":
            if not api_key:
                return "OLLAMA_CLOUD_API_KEY is not set"
            r = httpx.get(
                "https://ollama.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
            )
        elif provider == "openai_compatible":
            base = (ollama_base_url or get_env_value("OPENAI_COMPATIBLE_BASE_URL") or "http://localhost:8080/v1").rstrip("/")
            models_url = base if base.endswith("/models") else f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            r = httpx.get(models_url, headers=headers, timeout=timeout)
        else:
            base_url = (ollama_base_url or get_env_value("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
            r = httpx.get(f"{base_url}/api/tags", timeout=timeout)
    except httpx.HTTPError as exc:
        return f"connection failed: {exc}"
    if r.status_code >= 400:
        detail = ""
        try:
            body = r.text.strip()
            if body:
                first = body.split("\n", 1)[0].strip()
                if first:
                    detail = f": {first}"
        except Exception:
            pass
        return f"HTTP {r.status_code}{detail}"
    return None


def preflight_from_cfg(cfg: dict, *, timeout: float = 5.0) -> str | None:
    """Startup-path convenience: resolve provider + key from a loaded cfg/env,
    then call `preflight_connection`. Used by desktop_app.py after
    build_interactive_agent() to warn (not block) on an unreachable backend."""
    provider = cfg.get("provider", "ollama")
    if provider == "openrouter":
        key = get_env_value("OPENROUTER_API_KEY") or ""
        ollama_url = ""
    elif provider == "ollama_cloud":
        key = get_env_value("OLLAMA_CLOUD_API_KEY") or ""
        ollama_url = ""
    elif provider == "openai_compatible":
        key = get_env_value("OPENAI_COMPATIBLE_API_KEY") or ""
        ollama_url = cfg.get("openai_compatible_base_url") or get_env_value("OPENAI_COMPATIBLE_BASE_URL") or "http://localhost:8080/v1"
    else:
        key = ""
        ollama_url = cfg.get("ollama_base_url") or ""
    return preflight_connection(provider, key, ollama_base_url=ollama_url, timeout=timeout)

