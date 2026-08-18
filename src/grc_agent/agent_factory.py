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
    ClampOversizedMessages,
    ClearToolResults,
    SlidingWindowCompaction,
    TieredCompaction,
)
from pydantic_ai_harness.planning import Planning
from pydantic_ai_harness.step_persistence import StepPersistence
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from grc_agent.agent import (
    GrcAgentResponse,
    StopGracefully,
    grc_tools,
    validate_flowgraph_state,
    web_fetch_cap,
    web_search_cap,
)
from grc_agent.db import get_step_store
from grc_agent.prompts import build_system_prompt
from grc_agent.settings import default_settings, get_env_value, load_settings

# genai-prices registry errors documented in the harness compaction docs:
# an OVER-recorded window means compaction never fires before the provider
# rejects the request (the dangerous direction). `context_window` overrides
# resolution outright, per the docs' own remedy. Keyed by substring so
# prefixed ids (e.g. OpenRouter's 'anthropic/claude-sonnet-4-5') match too.
_MODEL_WINDOW_OVERRIDES = {
    "claude-sonnet-4-5": 200_000,  # registry records 1,000,000; real window 200,000
    "claude-opus-4-6": 1_000_000,  # registry records 200,000; real window 1,000,000 (safe but wasteful)
}


_log = logging.getLogger(__name__)


def _provider_base_url(cfg: dict) -> str:
    """The configured endpoint for the active chat provider ("" when there
    isn't one — ChatGPT/Codex is always chatgpt.com OAuth with no user-set
    base URL). One resolution shared by the compaction local/cloud rule and
    the StepPersistence run metadata, so the two can never disagree."""
    provider = cfg.get("provider", "ollama_local")
    if provider == "openai_codex":
        return ""
    if provider == "ollama_cloud":
        return "https://ollama.com/v1"
    if provider == "openrouter":
        return "https://openrouter.ai/api/v1"
    if provider == "openai":
        return "https://api.openai.com/v1"
    if provider == "ollama_local":
        return cfg.get("ollama_base_url", "") or ""
    return cfg.get("openai_compatible_base_url", "") or ""


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
    provider = cfg.get("provider", "ollama_local")
    if provider == "openai_codex":
        # Returns before the /v1 suffixing below: the Codex base URL is
        # https://chatgpt.com/backend-api/codex, and the OpenAI SDK appends
        # the literal /responses to it. It also brings its own http client,
        # because `http_client` raises for status inside its retry transport,
        # which would fire before the 401-refresh path could see it.
        from grc_agent.providers.openai_codex import build_model as build_codex_model

        return build_codex_model(cfg["model"])
    if provider in ("openrouter", "openai", "openai_compatible"):
        if provider == "openrouter":
            raw_url, key_var = "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"
        elif provider == "openai":
            raw_url, key_var = "https://api.openai.com/v1", "OPENAI_API_KEY"
        else:
            raw_url = (
                cfg.get("openai_compatible_base_url")
                or get_env_value("OPENAI_COMPATIBLE_BASE_URL")
                or "https://openrouter.ai/api/v1"
            )
            key_var = "OPENAI_COMPATIBLE_API_KEY"
        base_url = raw_url.rstrip("/")
        base_url = base_url if base_url.endswith("/v1") else f"{base_url}/v1"
        key = (
            get_env_value(key_var)
            or os.environ.get(key_var)
            # Legacy pre-split .env: the generic provider served OpenRouter.
            or (get_env_value("OPENROUTER_API_KEY") if provider == "openai_compatible" else None)
            or cfg.get("openai_compatible_api_key")
            or "not-required"
        )
        return OpenAIChatModel(
            cfg["model"],
            provider=OpenAIProvider(base_url=base_url, api_key=key, http_client=http_client),
        )

    # Ollama local / Ollama Cloud
    if provider == "ollama_cloud":
        base_url = "https://ollama.com/v1"
    else:
        raw_url = (
            cfg.get("ollama_base_url")
            or get_env_value("OLLAMA_BASE_URL")
            or "http://localhost:11434"
        ).rstrip("/")
        base_url = raw_url if raw_url.endswith("/v1") else f"{raw_url}/v1"
    key = (
        get_env_value("OLLAMA_API_KEY")
        or get_env_value("OLLAMA_CLOUD_API_KEY")
        or os.environ.get("OLLAMA_API_KEY")
        or os.environ.get("OLLAMA_CLOUD_API_KEY")
        or cfg.get("ollama_api_key")
    )
    if provider == "ollama_cloud" and not key:
        raise ValueError(
            "An API key is required for Ollama Cloud (https://ollama.com). "
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
    when the history exceeds a fraction of the model's context window, keeping the last 3 tool
    return pairs and dialogue history intact; small tool results (under 2000 tokens) are never
    evicted.

    The target is one uniform fraction (75%) of the model's window, resolved per
    request from the genai-prices registry pydantic-ai-harness already ships
    with. Only models the registry cannot resolve use a fallback denominator
    — no more hand-picked absolute budgets per deployment class.
    """
    base_url = _provider_base_url(cfg)
    # One uniform rule: any plain-HTTP endpoint is a self-hosted server
    # (every cloud provider — ollama.com, openrouter.ai, chatgpt.com — is
    # https). This covers localhost, 127.0.0.1, LAN IPs, and custom http
    # endpoints alike, and errs conservative: premature compaction is a
    # mild cost, while treating a small-window local model as cloud would
    # overflow its context.
    is_local = base_url.startswith("http://")

    # Tier 0: clamp a single runaway part (giant tool result or response)
    # before anything else — the only strategy that can reach the NEWEST
    # oversized part, which ClearToolResults (old results only) and
    # SlidingWindowCompaction (oldest messages only) cannot. Zero-LLM.
    # Threshold mirrors the window pins: half the assumed window, so one
    # part can never alone overflow it.
    clamp_tokens = 16_000 if is_local else 64_000
    tiers = [
        ClampOversizedMessages(max_part_tokens=clamp_tokens),
        # TieredCompaction drives the tiers itself (each tier's own trigger is
        # bypassed), so the knobs that matter here are keep_pairs and
        # min_clear_tokens — NOT max_tokens. Verified live (session-14 run,
        # 2026-08-18): with keep_pairs=2 and no min_clear_tokens, a
        # query_knowledge answer (~100-500 tokens) was blanked within one or
        # two tool calls, so the model re-asked the same catalog question 18
        # times and StopGracefully hit the 40-request ceiling. keep_pairs=3 is
        # the harness default; min_clear_tokens=2000 is one uniform rule —
        # small tool results are never worth reclaiming, only the bulky
        # inspect_graph/generate_python JSONs are.
        ClearToolResults(
            max_tokens=1,
            keep_pairs=3,
            min_clear_tokens=2_000,
            placeholder=(
                "[Flowgraph tool output cleared to conserve context — "
                "call the tool again if you still need this data]"
            ),
        ),
        SlidingWindowCompaction(
            max_tokens=1,
            keep_messages=20,
            preserve_first_user_message=True,
        ),
    ]

    # Absolute escape hatch for deployments where the fraction is wrong.
    env_override = get_env_value("GRC_COMPACTION_TARGET_TOKENS") or os.environ.get(
        "GRC_COMPACTION_TARGET_TOKENS"
    )
    try:
        target_tokens = int(env_override) if env_override else None
    except (ValueError, TypeError):
        target_tokens = None
    if target_tokens is not None:
        return TieredCompaction(tiers=tiers, target_tokens=target_tokens)

    if is_local:
        # A self-hosted model id says nothing about the window the server
        # actually serves — a registry entry describes the upstream spec,
        # not this deployment's --ctx / num_ctx — so pin the conservative
        # 32k local window outright: 0.75 x 32k = 24k target, the previous
        # fixed local budget, for every plain-HTTP endpoint.
        return TieredCompaction(tiers=tiers, target_fraction=0.75, context_window=32_000)
    # Cloud: the model's real window from the pricing registry (gpt-5.x,
    # claude, gemini, ... all carry one), corrected where the registry is
    # documented wrong; models the registry does not know fall back to 128k
    # — 0.75 x 128k = 96k, the previous fixed cloud budget.
    model_id = str(cfg.get("model", ""))
    for key, window in _MODEL_WINDOW_OVERRIDES.items():
        if key in model_id:
            return TieredCompaction(tiers=tiers, target_fraction=0.75, context_window=window)
    return TieredCompaction(tiers=tiers, target_fraction=0.75, fallback_context_window=128_000)


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

    if cfg["provider"] == "openai_codex":
        from grc_agent.providers.openai_codex.model import CODEX_MODEL_SETTINGS

        # Codex rejects store:true outright ("Store must be set to false").
        model_settings = ModelSettings(**CODEX_MODEL_SETTINGS)
    else:
        # Ollama and plain OpenAI-compatible endpoints: no thinking request
        # knobs at all — the provider's native default stands. Verified live:
        # current Ollama /v1 ignores `think`/`reasoning_effort` either way
        # (hybrid models think by default), and older servers only know the
        # native-API `think` flag, not an OpenAI-compat equivalent.
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
            StepPersistence(
                store=get_step_store(),
                agent_name="grc_chat",
                metadata={
                    "provider": str(cfg.get("provider", "")),
                    "model": str(cfg.get("model", "")),
                    "base_url": _provider_base_url(cfg),
                },
            ),
            Planning(),
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


def _preflight_target(provider: str, api_key: str, base_url: str) -> tuple[str, dict] | str:
    """Resolve the provider's /models endpoint to (url, headers), or return an
    error string when a required key is missing."""
    if provider in ("openrouter", "openai", "openai_compatible"):
        if provider == "openrouter":
            base = "https://openrouter.ai/api/v1"
            if not api_key:
                return "API key is required for OpenRouter"
        elif provider == "openai":
            base = "https://api.openai.com/v1"
            if not api_key:
                return "API key is required for OpenAI"
        else:
            base = (
                base_url
                or get_env_value("OPENAI_COMPATIBLE_BASE_URL")
                or "https://openrouter.ai/api/v1"
            ).rstrip("/")
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

    if provider == "ollama_cloud":
        if not api_key:
            return "API key is required for Ollama Cloud"
        return "https://ollama.com/v1/models", {"Authorization": f"Bearer {api_key}"}

    # ollama_local (base_url may point at a LAN host / custom port)
    base = (base_url or get_env_value("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
    return f"{base}/api/tags", {}


def probe_backend(
    provider: str,
    api_key: str,
    base_url: str,
    model: str,
    *,
    timeout: float = 5.0,
) -> tuple[str | None, str | None]:
    """ONE bounded probe that answers both reachability and model listing.

    Returns ``(reachability_error, model_warning)`` — at most one is set.
    The backend's /models endpoint is fetched once and the same response is
    parsed for both checks, so Save and startup pay one HTTP round trip with
    the same 5s bound the old preflight already had (never 15s, never two
    calls, never a freeze while a busy daemon pulls a missing tag).

    ``model_warning`` is set when the backend answers but does not list the
    configured model — the tag is either a typo or, on a local daemon, a tag
    it would have to pull first (a silent multi-GB download that reads as a
    hung chat: the request stays open with zero output for the whole pull).
    One uniform rule: the backend's own model list is the source of truth.

    Parsing lives in one place: `model_catalog._list_http_models`, the same
    parser the Settings dialog's Load button uses — this probe is just that
    parser plus a membership check, mapped to the tuple contract.
    """
    if provider == "openai_codex":
        # No /models endpoint on the Codex transport — the equivalent
        # question is whether a usable credential exists.
        from grc_agent.providers.openai_codex import is_signed_in

        if not is_signed_in():
            return ("Not signed in to ChatGPT — use Sign in with ChatGPT in Settings", None)
        return (None, None)

    from grc_agent.model_catalog import _list_http_models

    try:
        names = _list_http_models(provider, api_key, base_url, timeout=timeout)
    except RuntimeError as exc:
        return (str(exc), None)
    if not model:
        return (None, None)
    if model in names:
        return (None, None)
    listed = ", ".join(names[:5]) or "(none)"
    return (
        None,
        f"Model '{model}' is not served by this backend "
        f"(it lists {len(names)} models, e.g. {listed}). "
        "It may be a typo, or the backend may need to download it first — "
        "which can look like a hung chat.",
    )
