import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic_ai import Agent, ModelSettings, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig
from pydantic_ai_harness.compaction import (
    ClearToolResults,
    SlidingWindowCompaction,
    TieredCompaction,
)
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from grc_agent.agent import (
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
        timeout=httpx.Timeout(connect=15.0, read=1800.0, write=60.0, pool=30.0),
        transport=AsyncTenacityTransport(
            config=RetryConfig(
                retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
                wait=wait_exponential(multiplier=1, max=10),
                stop=stop_after_attempt(3),
                reraise=True,
            ),
            validate_response=lambda r: r.raise_for_status(),
        ),
    )


def _build_model(cfg: dict, http_client: httpx.AsyncClient):
    provider = cfg.get("provider", "ollama")
    if provider == "openai_codex":
        # Returns before the /v1 suffixing below: the Codex base URL is
        # https://chatgpt.com/backend-api/codex, and the OpenAI SDK appends
        # the literal /responses to it. It also brings its own http client,
        # because `http_client` raises for status inside its retry transport,
        # which would fire before the 401-refresh path could see it.
        from grc_agent.providers.openai_codex import build_model as build_codex_model

        return build_codex_model(cfg["model"])
    if provider == "openai_compatible":
        key = (
            get_env_value("OPENAI_COMPATIBLE_API_KEY")
            or get_env_value("OPENROUTER_API_KEY")
            or os.environ.get("OPENAI_COMPATIBLE_API_KEY")
            or os.environ.get("OPENROUTER_API_KEY")
            or cfg.get("openai_compatible_api_key")
            or "not-required"
        )
        raw_url = (
            cfg.get("openai_compatible_base_url")
            or get_env_value("OPENAI_COMPATIBLE_BASE_URL")
            or "https://openrouter.ai/api/v1"
        ).rstrip("/")
        base_url = raw_url if raw_url.endswith("/v1") else f"{raw_url}/v1"
        return OpenAIChatModel(
            cfg["model"],
            provider=OpenAIProvider(base_url=base_url, api_key=key, http_client=http_client),
        )

    # Ollama (local or remote/cloud)
    raw_url = (
        cfg.get("ollama_base_url") or get_env_value("OLLAMA_BASE_URL") or "http://localhost:11434"
    ).rstrip("/")
    base_url = raw_url if raw_url.endswith("/v1") else f"{raw_url}/v1"
    key = (
        get_env_value("OLLAMA_API_KEY")
        or get_env_value("OLLAMA_CLOUD_API_KEY")
        or os.environ.get("OLLAMA_API_KEY")
        or os.environ.get("OLLAMA_CLOUD_API_KEY")
        or cfg.get("ollama_api_key")
    )
    if "ollama.com" in base_url and not key:
        raise ValueError(
            "An API key is required when connecting to Ollama Cloud (https://ollama.com). "
            "Set it in Settings or the .env file."
        )
    return OllamaModel(
        cfg["model"],
        provider=OllamaProvider(base_url=base_url, api_key=key, http_client=http_client),
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
        _log.info(
            "model request -> provider=%s base_url=%s model=%s", provider_name, base_url, model_name
        )
        return request_context


def _build_compaction_capability(cfg: dict) -> TieredCompaction:
    """Build a tiered context compaction capability tailored to the active provider.

    Evicts bulky older tool return contents (e.g. inspect_graph 10k JSONs, generate_python previews)
    when approaching the context budget, keeping the last 2 tool return pairs and dialogue history intact.
    """
    provider = cfg.get("provider", "ollama")
    if provider == "openai_codex":
        # The Codex transport is always remote (chatgpt.com OAuth, no base
        # URL of its own) — reading openai_compatible_base_url here would
        # pick up whatever local vLLM URL a user left configured for the
        # other provider and wrongly classify a 272k-window model as local.
        base_url = ""
    else:
        base_url = cfg.get("ollama_base_url", "") if provider == "ollama" else cfg.get("openai_compatible_base_url", "")
    # One uniform rule: any plain-HTTP endpoint is a self-hosted server
    # (every cloud provider — ollama.com, openrouter.ai, chatgpt.com — is
    # https). This covers localhost, 127.0.0.1, LAN IPs, and custom http
    # endpoints alike, and errs conservative: premature compaction is a
    # mild cost, while treating a small-window local model as cloud would
    # overflow its context.
    is_local = base_url.startswith("http://")

    # Target threshold: 24,000 tokens for 32k local models (~75% context), 96,000 for cloud models.
    default_target = 24_000 if is_local else 96_000
    env_override = get_env_value("GRC_COMPACTION_TARGET_TOKENS") or os.environ.get("GRC_COMPACTION_TARGET_TOKENS")
    try:
        target_tokens = int(env_override) if env_override else default_target
    except (ValueError, TypeError):
        target_tokens = default_target

    return TieredCompaction(
        tiers=[
            ClearToolResults(
                max_tokens=1,
                keep_pairs=2,
                placeholder="[Flowgraph tool output cleared to conserve context window]",
            ),
            SlidingWindowCompaction(
                max_tokens=1,
                keep_messages=20,
                preserve_first_user_message=True,
            ),
        ],
        target_tokens=target_tokens,
    )


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
        _log.warning(
            "Failed to build chat model from cfg (provider=%s): %s", cfg.get("provider"), e
        )
        model_build_error = str(e)
        cfg = default_settings()
        model = _build_model(cfg, http_client)

    is_ollama = cfg["provider"] == "ollama"
    thinking = cfg.get("ollama_thinking_enabled", True)
    if is_ollama:
        model_settings = ModelSettings(extra_body={"think": thinking})
    elif cfg["provider"] == "openai_codex":
        from grc_agent.providers.openai_codex.model import CODEX_MODEL_SETTINGS

        # Codex rejects store:true outright ("Store must be set to false").
        model_settings = ModelSettings(**CODEX_MODEL_SETTINGS)
    else:
        model_settings = ModelSettings()

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
            _build_compaction_capability(cfg),
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


def _preflight_target(provider: str, api_key: str, ollama_base_url: str) -> tuple[str, dict] | str:
    """Resolve the provider's /models endpoint to (url, headers), or return an
    error string when a required key is missing."""
    if provider == "openai_compatible":
        base = (
            ollama_base_url
            or get_env_value("OPENAI_COMPATIBLE_BASE_URL")
            or "https://openrouter.ai/api/v1"
        ).rstrip("/")
        if "openrouter.ai" in base and not api_key:
            return "API key is required for OpenRouter"
        models_url = (
            base
            if base.endswith("/models")
            else f"{base}/models"
            if base.endswith("/v1")
            else f"{base}/v1/models"
        )
        headers = (
            {"Authorization": f"Bearer {api_key}"}
            if (api_key and api_key != "not-required")
            else {}
        )
        return models_url, headers

    # Ollama (local or cloud)
    base_url = (
        ollama_base_url or get_env_value("OLLAMA_BASE_URL") or "http://localhost:11434"
    ).rstrip("/")
    if "ollama.com" in base_url:
        if not api_key:
            return "API key is required for Ollama Cloud"
        return "https://ollama.com/v1/models", {"Authorization": f"Bearer {api_key}"}
    return f"{base_url}/api/tags", {}


def _preflight_status_error(r) -> str:
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
    (which is itself sync up to the unified loop's run_forever()). Bounded at
    `timeout` so a hung host fails fast instead of blocking the UI.

    Takes provider + api_key explicitly so the Save handler can validate a
    NEW config BEFORE writing it to .env (no save/restore dance), while
    startup resolves them from the already-loaded cfg/env.
    """
    if provider == "openai_codex":
        # There is no /models endpoint on the Codex transport, so there is no
        # URL to probe. The equivalent question is whether a usable credential
        # exists — the first real request refreshes it if needed.
        from grc_agent.providers.openai_codex import is_signed_in

        if not is_signed_in():
            return "Not signed in to ChatGPT — use Sign in with ChatGPT in Settings"
        return None

    target = _preflight_target(provider, api_key, ollama_base_url)
    if isinstance(target, str):
        return target
    url, headers = target
    try:
        r = httpx.get(url, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        return f"connection failed: {exc}"
    if r.status_code >= 400:
        return _preflight_status_error(r)
    return None


def preflight_from_cfg(cfg: dict, *, timeout: float = 5.0) -> str | None:
    """Startup-path convenience: resolve provider + key from a loaded cfg/env,
    then call `preflight_connection`. Used by desktop_app.py after
    build_interactive_agent() to warn (not block) on an unreachable backend."""
    provider = cfg.get("provider", "ollama")
    if provider == "openai_compatible":
        key = (
            get_env_value("OPENAI_COMPATIBLE_API_KEY") or get_env_value("OPENROUTER_API_KEY") or ""
        )
        url = (
            cfg.get("openai_compatible_base_url")
            or get_env_value("OPENAI_COMPATIBLE_BASE_URL")
            or "https://openrouter.ai/api/v1"
        )
    else:
        key = get_env_value("OLLAMA_API_KEY") or get_env_value("OLLAMA_CLOUD_API_KEY") or ""
        url = cfg.get("ollama_base_url") or ""
    return preflight_connection(provider, key, ollama_base_url=url, timeout=timeout)
