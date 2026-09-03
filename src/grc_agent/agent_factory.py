from __future__ import annotations

import asyncio
import dataclasses
import importlib
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

import httpx
import httpx2
from pydantic import BeforeValidator
from pydantic_ai import Agent, ModelRequestContext, ModelRetry, ModelSettings, RunContext
from pydantic_ai.capabilities import AbstractCapability, PrepareTools
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.retries import (
    AsyncHTTPX2TenacityTransport,
    AsyncTenacityTransport,
    RetryConfig,
)
from pydantic_ai.tools import DeferredToolRequests, ToolDefinition
from pydantic_ai_harness.compaction import (
    ClampOversizedMessages,
    ClearToolResults,
    CompactionStrategy,
    SlidingWindowCompaction,
    SummarizingCompaction,
    TieredCompaction,
)
from pydantic_ai_harness.filesystem import READ_ONLY_TOOL_NAMES
from pydantic_ai_harness.planning import (
    InMemoryPlanStore,
    PlanItem,
    Planning,
    PlanStore,
    SqlitePlanStore,
    render_plan,
)
from pydantic_ai_harness.step_persistence import StepPersistence
from pydantic_ai_harness.system_reminders import SystemReminders
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from grc_agent.agent import (
    GrcAgentResponse,
    StopGracefully,
    grc_tools,
    json_repair_cap,
    prompt_injection_cap,
    validate_flowgraph_state,
    web_fetch_cap,
    web_search_cap,
)
from grc_agent.db import archive_transcript, get_db_path, get_step_store, init_db
from grc_agent.fs_tools import GrcFileSystem
from grc_agent.prompts import build_planner_prompt, build_system_prompt
from grc_agent.settings import default_settings, get_env_value, load_settings, resolve_key
from grc_agent.shell_tools import GrcShell
from grc_agent.ui.providers import PROVIDER_LABELS

# genai-prices registry errors documented in the harness compaction docs:
# an OVER-recorded window means compaction never fires before the provider
# rejects the request (the dangerous direction). `context_window` overrides
# resolution outright, per the docs' own remedy. Keyed by substring so
# prefixed ids (e.g. OpenRouter's 'anthropic/claude-sonnet-4-5') match too.


if TYPE_CHECKING:  # native_canvas imports gi/GTK; keep it out of the runtime path
    from grc_agent.native_canvas import NativeFlowgraphProxy

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentBundle:
    """The two explicit roles available in the desktop chat.

    Both agents are built over `NativeFlowgraphProxy` deps; the annotations say
    so rather than leaving `Agent` bare, which made every construction site an
    unchecked `Agent[object, str]` mismatch.
    """

    executor: Agent[NativeFlowgraphProxy, Any]
    planner: Agent[NativeFlowgraphProxy, Any]
    model_build_error: str | None = None


# Fail-closed allowlist: a role that must never mutate cannot be expressed as a
# denylist, since any newly-added mutation tool would be admitted by default. The
# filesystem half comes from the harness's own READ_ONLY_TOOL_NAMES rather than a
# hand-copied list, so it cannot drift when the harness adds a read-only fs tool.
_PLANNER_FUNCTION_TOOLS = frozenset(
    {
        "inspect_graph",
        "query_knowledge",
        "generate_python",
        "get_run_log",
        "duckduckgo_search",
        "web_search",  # defensive: native tools bypass PrepareTools today
        "web_fetch",
        "write_plan",
    }
    | READ_ONLY_TOOL_NAMES
)


async def _prepare_planner_tools(
    _ctx: RunContext[Any], tool_defs: list[ToolDefinition]
) -> list[ToolDefinition]:
    """Expose only read operations plus atomic plan read/write to the planner."""
    return [tool for tool in tool_defs if tool.name in _PLANNER_FUNCTION_TOOLS]


async def _execution_plan_reminder(ctx: RunContext[Any]) -> str | None:
    """Hand the executor the durable plan without giving it planning tools."""
    items = await _plan_store_resolver(ctx).get_items()
    if not items:
        return None
    return (
        "<execution-plan>\n"
        "The user prepared this plan in Planner mode. Treat it as read-only. Execute it only "
        "when the current user request explicitly asks for implementation; otherwise use it as "
        "reference. Before edits, re-inspect live state and ask before materially changing scope.\n\n"
        f"{render_plan(items)}\n"
        "</execution-plan>"
    )


def _plan_store_resolver(ctx: RunContext[Any]) -> PlanStore:
    """Resolve one run's plan store from its chat conversation id.

    Saved chat sessions use ``session-{id}``, the same key passed to
    ``Agent.iter`` and StepPersistence. Those plans live in the shared chat DB
    and therefore survive turns, restarts, and agent live-swaps. Runs without
    an owning session stay in memory so they cannot leave orphaned plan rows.
    """
    conversation_id = ctx.conversation_id
    if not (isinstance(conversation_id, str) and conversation_id.startswith("session-")):
        return InMemoryPlanStore()
    init_db()
    return SqlitePlanStore(str(get_db_path()), session=conversation_id)


def coerce_plan_items(v: Any) -> list[Any]:
    """Uniform normalization for write_plan tool input.

    Coerces JSON-stringified arrays, plain string lists, and stringifies numeric IDs.
    Validates items against PlanItem and raises ModelRetry with actionable compiler feedback.
    """
    if isinstance(v, str):
        v = v.strip()
        try:
            v = json.loads(v)
        except Exception as exc:
            raise ModelRetry(
                "Invalid JSON for plan items. Expected a list of steps, e.g.:\n"
                '[{"content": "First step description", "status": "pending"}]\n'
                f"Error: {exc}"
            ) from exc

    if isinstance(v, list):
        parsed: list[dict[str, Any] | Any] = []
        for item in v:
            if isinstance(item, str):
                parsed.append({"content": item.strip()})
            elif isinstance(item, dict):
                d = dict(item)
                if "id" in d and d["id"] is not None:
                    d["id"] = str(d["id"])
                if "content" not in d:
                    for alias in ("title", "name", "step", "description", "desc"):
                        if alias in d and isinstance(d[alias], str):
                            d["content"] = d.pop(alias)
                            break
                try:
                    PlanItem.model_validate(d)
                except Exception as exc:
                    raise ModelRetry(
                        f"Invalid plan item {d}. Expected:\n"
                        '{"content": "Step description", "status": "pending"}\n'
                        f"Validation error: {exc}"
                    ) from exc
                parsed.append(d)
            else:
                raise ModelRetry(
                    "Plan items must be objects or strings. Expected:\n"
                    '[{"content": "Step description", "status": "pending"}]'
                )
        return parsed

    raise ModelRetry(
        "Invalid plan items. Expected a list of plan steps, e.g.:\n"
        '[{"content": "Step description", "status": "pending"}]'
    )


CoercedPlanItems = Annotated[list[PlanItem], BeforeValidator(coerce_plan_items)]


async def write_plan_func(
    ctx: RunContext[Any],
    items: CoercedPlanItems,
) -> str:
    """Create or replace the whole plan.

    Args:
        items: The complete ordered list of plan steps.
    """
    store = _plan_store_resolver(ctx)
    await store.set_items(items)
    return f"Plan updated: {len(items)} step(s).\n\n{render_plan(items)}"


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
    if provider == "anthropic":
        return "https://api.anthropic.com"
    if provider == "google":
        return "https://generativelanguage.googleapis.com"
    if provider == "groq":
        return "https://api.groq.com/openai/v1"
    if provider == "mistral":
        return "https://api.mistral.ai/v1"
    if provider == "cohere":
        return "https://api.cohere.com/v2"
    if provider == "xai":
        return "https://api.x.ai/v1"
    if provider == "ollama_local":
        return cfg.get("ollama_base_url", "") or ""
    return cfg.get("openai_compatible_base_url", "") or ""


# pydantic-ai 2.37 is mid-migration between HTTP stacks and the providers are
# split across both: Anthropic rejects an httpx.AsyncClient outright, Groq
# rejects an httpx2.AsyncClient outright, and every other provider the
# Settings UI exposes accepts either (the OpenAI-compatible ones warn on
# httpx and drop it in v3). Verified against all eleven providers.
#
# So the default is httpx2 — where the framework is going — with one named
# exception rather than a per-provider table. Delete the exception when Groq
# migrates; the assertion in tests/test_agent_factory.py will say when.
_HTTPX1_ONLY_PROVIDERS = frozenset({"groq"})

_HTTP_TIMEOUT = {"connect": 15.0, "read": 120.0, "write": 60.0, "pool": 30.0}


def _retry_config(transport_error: type[Exception], status_error: type[Exception]) -> RetryConfig:
    return RetryConfig(
        retry=retry_if_exception_type((transport_error, status_error)),
        wait=wait_exponential(multiplier=1, max=10),
        stop=stop_after_attempt(3),
        reraise=True,
    )


def _retrying_http_client(provider: str = "") -> httpx.AsyncClient | httpx2.AsyncClient:
    """An HTTP client on the stack the given provider's SDK accepts."""
    if provider in _HTTPX1_ONLY_PROVIDERS:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(**_HTTP_TIMEOUT),
            transport=AsyncTenacityTransport(
                config=_retry_config(httpx.TransportError, httpx.HTTPStatusError),
                validate_response=lambda r: r.raise_for_status(),
            ),
        )
    return httpx2.AsyncClient(
        timeout=httpx2.Timeout(**_HTTP_TIMEOUT),
        transport=AsyncHTTPX2TenacityTransport(
            config=_retry_config(httpx2.TransportError, httpx2.HTTPStatusError),
            validate_response=lambda r: r.raise_for_status(),
        ),
    )


# pydantic-ai's dedicated model/provider classes for the native cloud
# providers the Settings UI exposes. One uniform row per provider: model
# module/class, provider module/class, and the .env key for the API key.
_NATIVE_MODEL_BUILDERS = {
    "anthropic": (
        "pydantic_ai.models.anthropic",
        "AnthropicModel",
        "pydantic_ai.providers.anthropic",
        "AnthropicProvider",
        "ANTHROPIC_API_KEY",
        True,
    ),
    "google": (
        "pydantic_ai.models.google",
        "GoogleModel",
        "pydantic_ai.providers.google",
        "GoogleProvider",
        "GOOGLE_API_KEY",
        True,
    ),
    "groq": (
        "pydantic_ai.models.groq",
        "GroqModel",
        "pydantic_ai.providers.groq",
        "GroqProvider",
        "GROQ_API_KEY",
        True,
    ),
    "mistral": (
        "pydantic_ai.models.mistral",
        "MistralModel",
        "pydantic_ai.providers.mistral",
        "MistralProvider",
        "MISTRAL_API_KEY",
        True,
    ),
    "cohere": (
        "pydantic_ai.models.cohere",
        "CohereModel",
        "pydantic_ai.providers.cohere",
        "CohereProvider",
        "COHERE_API_KEY",
        True,
    ),
    "xai": (
        "pydantic_ai.models.xai",
        "XaiModel",
        "pydantic_ai.providers.xai",
        "XaiProvider",
        "XAI_API_KEY",
        False,  # XaiProvider takes xai_client, not http_client
    ),
}


def _build_model(cfg: dict, http_client: httpx.AsyncClient | httpx2.AsyncClient):
    provider = cfg.get("provider", "ollama_local")
    if provider == "openai_codex":
        # Returns before the /v1 suffixing below: the Codex base URL is
        # https://chatgpt.com/backend-api/codex, and the OpenAI SDK appends
        # the literal /responses to it. It also brings its own http client,
        # because `http_client` raises for status inside its retry transport,
        # which would fire before the 401-refresh path could see it.
        from grc_agent.providers.openai_codex import build_model as build_codex_model

        return build_codex_model(cfg["model"])
    if provider == "openrouter":
        # pydantic-ai's dedicated OpenRouter model/provider: adds the
        # OpenRouter error taxonomy, attribution headers (HTTP-Referer /
        # X-Title), and model profiles — the generic OpenAI path silently
        # discards all of it.
        key = resolve_key("OPENROUTER_API_KEY")
        return OpenRouterModel(
            cfg["model"],
            provider=OpenRouterProvider(api_key=key, http_client=http_client),
        )
    if provider == "openai":
        key = resolve_key("OPENAI_API_KEY")
        return OpenAIChatModel(
            cfg["model"],
            provider=OpenAIProvider(api_key=key, http_client=http_client),
        )
    if provider in _NATIVE_MODEL_BUILDERS:
        # pydantic-ai's dedicated model/provider classes, one uniform table
        # row per provider (lazy importlib so startup stays light). The API
        # key is read from the provider's own env var (see ui/providers.py).
        mod_name, cls_name, prov_mod, prov_cls, key_var, http_client_ok = _NATIVE_MODEL_BUILDERS[
            provider
        ]
        key = resolve_key(key_var)
        model_cls = getattr(importlib.import_module(mod_name), cls_name)
        provider_cls = getattr(importlib.import_module(prov_mod), prov_cls)
        kwargs: dict[str, Any] = {"api_key": key}
        if http_client_ok:
            kwargs["http_client"] = http_client
        return model_cls(cfg["model"], provider=provider_cls(**kwargs))
    if provider == "openai_compatible":
        # The user's own endpoint; api_key=None lets OpenAIProvider apply its
        # own 'api-key-not-set' placeholder instead of our hand-rolled
        # "not-required" sentinel.
        raw_url = (
            cfg.get("openai_compatible_base_url")
            or get_env_value("OPENAI_COMPATIBLE_BASE_URL")
            or "http://localhost:8080/v1"
        ).rstrip("/")
        base_url = raw_url if raw_url.endswith("/v1") else f"{raw_url}/v1"
        key = (
            get_env_value("OPENAI_COMPATIBLE_API_KEY")
            or os.environ.get("OPENAI_COMPATIBLE_API_KEY")
            or None
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


def describe_model(model: Any) -> tuple[str, str, str]:
    """``(provider_name, base_url, model_name)`` for a live pydantic-ai model.

    All three are public on `Model` in pydantic-ai 2.31 — `provider`,
    `base_url` (which delegates to the provider) and `model_name`. Two call
    sites used to reach through `_model_name`/`_provider` private fallbacks
    with their own copy of the same getattr chain, and disagreed on the missing
    value ("" here, "<unknown>" there). Returns "" for anything absent; the
    test models (`TestModel`/`FunctionModel`) have no provider and answer
    `None` rather than raising, so callers get "" for those too.
    """
    if model is None:
        return "", "", ""
    provider = getattr(model, "provider", None)
    return (
        str(getattr(provider, "name", "") or ""),
        str(getattr(model, "base_url", "") or ""),
        str(getattr(model, "model_name", "") or ""),
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

    async def before_model_request(
        self,
        ctx: RunContext[Any],  # noqa: ARG002
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        provider_name, base_url, model_name = describe_model(request_context.model)
        _log.info(
            "model request -> provider=%s base_url=%s model=%s",
            provider_name or "<unknown>",
            base_url or "<unknown>",
            model_name or "<unknown>",
        )
        return request_context


_context_length_cache: dict[tuple[str, str], int] = {}
# Failed/unresolvable probes are negative-cached with a TTL so a down backend
# doesn't re-block the main loop (each probe is a sync HTTP call) on every
# label update during a turn. Entries age out so a recovered backend is re-tried.
_context_negative_cache: dict[tuple[str, str], float] = {}
_CONTEXT_NEGATIVE_TTL = 60.0


def _google_context_length(model: str) -> int | None:
    """GET the Gemini /v1beta/models catalog -> inputTokenLimit for the model
    (the context window). Returns None if unresolvable."""

    from grc_agent.settings import get_env_value

    key_var = _PREFLIGHT_ENDPOINTS["google"][1]
    api_key = get_env_value(key_var) or ""
    if not api_key:
        return None
    url = _PREFLIGHT_ENDPOINTS["google"][0].format(key=api_key)
    with httpx.Client(timeout=3.0) as client:
        r = client.get(url)
    if r.status_code != 200:
        return None
    for m in r.json().get("models", []):
        if m.get("name", "").endswith(model):
            limit = m.get("inputTokenLimit")
            if isinstance(limit, (int, float)):
                return int(limit)
    return None


def _ollama_context_length(model: str) -> int | None:
    """POST {base_url}/api/show -> model_info context_length, falling back to
    parsing num_ctx from the parameters blob. Returns None if unresolvable.

    The endpoint is the resolved `ollama_base_url` from load_settings() —
    the canonical URL for each provider (ollama.com for ollama_cloud, the
    configured daemon URL for ollama_local), so a cloud user hits ollama.com
    with the key and a local user hits their own daemon. Never keyed on an
    env-var name: the resolved provider decides the URL.
    """

    from grc_agent.settings import load_settings, resolve_key

    cfg = load_settings()
    if cfg.get("provider") == "ollama_cloud":
        base_url = "https://ollama.com"
        api_key = resolve_key("OLLAMA_API_KEY") or resolve_key("OLLAMA_CLOUD_API_KEY") or ""
    else:
        base_url = (cfg.get("ollama_base_url") or "http://localhost:11434").rstrip("/")
        api_key = ""
        if "ollama.com" in base_url:
            api_key = resolve_key("OLLAMA_API_KEY") or resolve_key("OLLAMA_CLOUD_API_KEY") or ""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with httpx.Client(timeout=3.0) as client:
        r = client.post(f"{base_url}/api/show", json={"name": model}, headers=headers)
    if r.status_code != 200:
        return None
    data = r.json()
    for k, v in data.get("model_info", {}).items():
        if "context_length" in k and isinstance(v, (int, float)):
            return int(v)
    for line in str(data.get("parameters", "")).splitlines():
        if "num_ctx" in line:
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    return None


def _openai_shaped_context_length(provider: str, model: str) -> int | None:
    """GET the provider's /v1/models catalog -> context_length for the model.
    Works for OpenRouter and plain OpenAI (both expose context_length in the
    OpenAI models shape); a custom endpoint without the field yields None.

    The openai_compatible branch probes the CONFIGURED endpoint (the same
    source of truth `_preflight_target` uses) — never a hardcoded host, or a
    LAN model's window would be silently overridden by OpenRouter's upstream
    spec (the compaction target is now probe-driven)."""

    from grc_agent.settings import get_env_value, load_settings, resolve_key

    if provider in _OPENAI_SHAPED_PROVIDER_IDS:
        url_t, key_var, header_fn = _PREFLIGHT_ENDPOINTS[provider]
        api_key = resolve_key(key_var) if key_var else None
        url = url_t.format(key=api_key or "")
        headers = header_fn(api_key) if (header_fn and api_key) else ({"Authorization": f"Bearer {api_key}"} if api_key else {})
    else:  # openai_compatible — the user's own endpoint
        base = (
            load_settings().get("openai_compatible_base_url")
            or get_env_value("OPENAI_COMPATIBLE_BASE_URL")
            or "http://localhost:8080/v1"
        ).rstrip("/")
        url = _models_url(base)
        api_key = resolve_key("OPENAI_COMPATIBLE_API_KEY")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with httpx.Client(timeout=3.0) as client:
        r = client.get(url, headers=headers)
    if r.status_code != 200:
        return None
    for m in r.json().get("data", []):
        m_id = m.get("id", "")
        if m_id == model or m_id.endswith(model):
            ctx_len = m.get("context_length")
            if isinstance(ctx_len, (int, float)):
                return int(ctx_len)
    return None


# Provider -> context-window probe (single-arg: the model id). Anthropic's
# /v1/models carries no context length — the genai-prices registry knows the
# Claude windows, so it maps to None. Codex has no /v1/models either; its
# window comes from the Codex catalog fetch in its own module, imported
# lazily so a non-Codex startup never pays for that provider's import.
_CTX_PROBES = {
    "ollama_local": _ollama_context_length,
    "ollama_cloud": _ollama_context_length,
    "openai_codex": lambda m: importlib.import_module(
        "grc_agent.providers.openai_codex.model"
    ).context_window(m),
    "google": _google_context_length,
    "anthropic": lambda _m: None,
}


async def aresolve_model_context_length(provider: str, model: str) -> int | None:
    """Off-loop wrapper around the synchronous probe.

    The probe opens a blocking httpx.Client with a 3s timeout. It used to be
    called straight from the sidebar's per-node context-label refresh, which
    runs on the unified GTK+asyncio loop — so on a cache miss the whole UI,
    canvas sync included, stalled mid-stream, and did it again every time the
    60s negative-cache TTL expired.
    """
    return await asyncio.to_thread(resolve_model_context_length, provider, model)


def resolve_model_context_length(provider: str, model: str) -> int | None:
    """Dynamically query the active provider's API for the model's exact context
    length. Cached in-memory per (provider, model) pair; returns None if
    unresolvable, so callers render exact token counts without hardcoded guesses.
    """
    key = (provider or "", model or "")
    if key in _context_length_cache:
        return _context_length_cache[key]
    if not provider or not model:
        return None
    neg_at = _context_negative_cache.get(key)
    if neg_at is not None and time.monotonic() - neg_at < _CONTEXT_NEGATIVE_TTL:
        return None

    try:
        probe = _CTX_PROBES.get(provider)
        if probe is not None:
            ctx_len = probe(model)
        elif provider in _OPENAI_SHAPED_PROVIDER_IDS or provider == "openai_compatible":
            ctx_len = _openai_shaped_context_length(provider, model)
        else:
            ctx_len = None
    except Exception as e:
        _log.debug(
            "Failed to resolve dynamic context length for provider=%s model=%s: %s",
            provider,
            model,
            e,
        )
        _context_negative_cache[key] = time.monotonic()
        return None

    if ctx_len is None:
        _context_negative_cache[key] = time.monotonic()
        return None
    _context_length_cache[key] = ctx_len
    return ctx_len


class ResilientSummarizingCompaction(SummarizingCompaction):
    """SummarizingCompaction whose summary-call failures degrade gracefully.

    D2: the harness does NOT catch summarizer failures — `_summarize` has no
    try/except and core re-raises hook errors, so an uncaught summary error
    would hard-fail the turn (verified in pydantic-ai-harness 0.21.0 and
    0.23.0:
    `_summarizing_compaction.py:_summarize`, `_agent_graph.py:1515`,
    `capabilities/abstract.py:652`). On failure the pre-compact history is
    kept unchanged; TieredCompaction then escalates to the zero-LLM
    SlidingWindow backstop (verified: `_tiered_compaction.py:_escalate`
    re-measures after each tier and continues past an unchanged one — also
    why upstream `FallbackCompaction` was evaluated and not adopted: no
    semantic gain over this return-unchanged path, only added nesting).
    Also covers Codex: its transport only accepts
    streaming requests, so the non-streaming summarizer always fails there —
    graceful by design.
    """

    async def compact(self, messages, ctx):  # noqa: ANN001
        try:
            return await super().compact(messages, ctx)
        except Exception:
            _log.warning("summarization failed; keeping message history unchanged", exc_info=True)
            return messages


def make_summarizing_strategy() -> ResilientSummarizingCompaction:
    """Single source of truth for the summarizing tier — used both by the
    in-run tier list and by the compact_now button so they can never drift.

    model=None inherits the request's model (D1); keep_messages=20 matches
    the sliding-window tail; keep_user_messages=True keeps retention copies
    of summarized user turns so ConversationSearch can recover them (D3).
    """
    return ResilientSummarizingCompaction(
        max_messages=1,  # inert under TieredCompaction; satisfies __post_init__
        keep_messages=20,
        keep_user_messages=True,
    )


@dataclass
class TranscriptPreservingTieredCompaction(TieredCompaction):
    """Archive the exact pre-compaction transcript before replacing history.

    StepPersistence normally snapshots settled tool boundaries and run ends.
    Automatic compaction can instead fire on the first model request, before
    either boundary exists. When a tier actually changes the request history,
    persist the untouched live history under the same conversation in the
    shared step store first. A store failure fails the turn, so compaction can
    never silently destroy the only durable copy used for dataset collection.
    """

    # Real dataclass fields, not class attributes mutated after construction:
    # the parent is a dataclass, so an undecorated subclass silently turns
    # these into shared class state that survives neither dataclasses.replace
    # nor __eq__.
    archive_agent_name: str = "grc_chat"
    # (provider, model) to re-probe lazily, or None when the window is already
    # known or deliberately fixed.
    pending_window_probe: tuple[str, str] | None = None

    async def _resolve_window_once(self) -> None:
        """Fill in the real context window on the first request that can.

        The window used to be resolved once, at agent-build time. A probe that
        failed because the backend was still starting froze the conservative
        fallback for the entire life of the agent — a local model with a
        131,072-token window stayed capped at 0.85 x 32,000 = 27,200 tokens
        until the app was restarted. The registry cannot rescue that case:
        resolve_context_window returns None for every self-hosted model id, so
        switching to fallback_context_window alone changes nothing. Only the
        backend knows, so ask it again.
        """
        if self.pending_window_probe is None:
            return
        provider, model = self.pending_window_probe
        probed = await aresolve_model_context_length(provider, model)
        if probed is not None:
            self.context_window = probed
            self.pending_window_probe = None

    async def before_model_request(self, ctx, request_context):  # noqa: ANN001
        await self._resolve_window_once()
        before = list(request_context.messages)
        transcript = list(ctx.messages)
        processed = await super().before_model_request(ctx, request_context)
        if processed.messages == before or ctx.conversation_id is None:
            return processed

        await archive_transcript(
            transcript,
            conversation_id=ctx.conversation_id,
            agent_name=self.archive_agent_name,
            kind="pre_compaction_transcript",
            step_index=ctx.run_step,
        )
        return processed


def _build_compaction_capability(
    cfg: dict, *, agent_name: str = "grc_chat"
) -> TranscriptPreservingTieredCompaction:
    """Build a tiered context compaction capability tailored to the active provider.

    Evicts bulky older tool return contents (e.g. inspect_graph 10k JSONs, generate_python previews)
    when the history exceeds a fraction of the model's context window, keeping the last 3 tool
    return pairs and dialogue history intact. `min_clear_tokens` is a TOTAL-reclaim gate: when the
    clearable set's combined size is below it, nothing is cleared at all.

    The target is one uniform fraction (85%) of the model's REAL context
    window, probed from the backend itself (Ollama /api/show, OpenRouter/
    OpenAI /v1/models, Codex context_window) — the same probe the sidebar's
    context label uses. The genai-prices registry and the old 128k/32k
    guesses are only fallbacks when the probe cannot answer.
    """
    def tagged(capability: TranscriptPreservingTieredCompaction):
        # archive_agent_name is a constructor field now; this only keeps the
        # call sites below from repeating it eight times.
        return dataclasses.replace(capability, archive_agent_name=agent_name)

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
    tiers: list[CompactionStrategy[Any]] = [
        ClampOversizedMessages(max_part_tokens=clamp_tokens),
        # TieredCompaction drives the tiers itself (each tier's own trigger is
        # bypassed), so the knobs that matter here are keep_pairs and
        # min_clear_tokens — NOT max_tokens. Verified live (session-14 run,
        # 2026-08-18): with keep_pairs=2 and no min_clear_tokens, a
        # query_knowledge answer (~100-500 tokens) was blanked within one or
        # two tool calls, so the model re-asked the same catalog question 18
        # times and StopGracefully hit the 40-request ceiling. keep_pairs=3 is
        # the harness default (the model keeps 3 tool pairs of working room);
        # min_clear_tokens=2000 is a TOTAL-reclaim gate (harness semantics:
        # nothing is cleared when the clearable set's combined size is
        # trivial — a turn of only small answers keeps them all).
        ClearToolResults(
            max_tokens=1,
            keep_pairs=3,
            min_clear_tokens=2_000,
            placeholder=(
                "[Flowgraph tool output cleared to conserve context — "
                "call the tool again if you still need this data]"
            ),
        ),
        make_summarizing_strategy(),
        # Zero-LLM final backstop: runs only when the summary tier returned
        # the history unchanged (D2 failure — e.g. Codex can never run the
        # non-streaming summarizer) or when the summary alone didn't reclaim
        # enough. Keeps bounded compaction working on every provider.
        SlidingWindowCompaction(
            max_tokens=1,
            keep_messages=20,
            preserve_first_user_message=True,
        ),
    ]

    # Absolute escape hatch for deployments where the fraction is wrong.
    env_override = resolve_key("GRC_COMPACTION_TARGET_TOKENS")
    try:
        target_tokens = int(env_override) if env_override else None
    except (ValueError, TypeError):
        target_tokens = None
    if target_tokens is not None:
        return tagged(
            TranscriptPreservingTieredCompaction(tiers=tiers, target_tokens=target_tokens)
        )

    # One uniform rule: the REAL context window, probed from the backend
    # itself (Ollama /api/show -> model_info.context_length, OpenRouter/OpenAI
    # /v1/models -> context_length, Codex -> context_window), cached per
    # (provider, model). The genai-prices registry is only a secondary source
    # for models the probe cannot answer (e.g. a custom OpenAI-compatible
    # endpoint without the field), and the old hardcoded 128k/32k guesses are
    # now the LAST resort, used only when the probe fails (backend down at
    # build time) — never the primary path.
    model_id = str(cfg.get("model", ""))
    provider = str(cfg.get("provider", ""))

    # Ask the backend first — it is the only source that knows a self-hosted
    # deployment's real window.
    probed = resolve_model_context_length(provider, model_id)

    # There used to be a two-entry model-name substring table correcting
    # genai-prices, which recorded claude-sonnet-4-5 at 1,000,000 against a
    # real 200,000 and claude-opus-4-6 at 200,000 against a real 1,000,000.
    # Both rows were fixed upstream in genai-prices 0.1.6, verified against
    # the installed registry, so the workaround is gone rather than kept as
    # folklore. tests/test_context_compaction.py pins the registry values so a
    # regression there fails loudly instead of silently mis-budgeting.
    if probed is not None:
        return tagged(
            TranscriptPreservingTieredCompaction(
                tiers=tiers, target_fraction=0.85, context_window=probed
            )
        )

    # Nothing could answer yet. Start on the conservative denominator, but
    # keep the probe pending so a backend that was merely slow to start is
    # picked up on a later request rather than frozen out for the session.
    pending = (provider, model_id) if provider and model_id else None
    if is_local:
        return tagged(
            TranscriptPreservingTieredCompaction(
                tiers=tiers,
                target_fraction=0.85,
                context_window=32_000,
                pending_window_probe=pending,
            )
        )
    return tagged(
        TranscriptPreservingTieredCompaction(
            tiers=tiers,
            target_fraction=0.85,
            fallback_context_window=128_000,
            pending_window_probe=pending,
        )
    )


def build_agents_from_cfg(cfg: dict) -> AgentBundle:
    """Construct fresh executor and planner agents from loaded settings.

    Both roles share the selected model and canonical message history, but
    their model-visible tools are disjoint. On model-construction failure the
    bundle falls back to defaults and carries the error for the GUI to surface.
    """
    http_client = _retrying_http_client(str(cfg.get("provider", "")))
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
        # Copied, not shared, so a per-agent mutation can't leak into the constant.
        model_settings: ModelSettings = dict(CODEX_MODEL_SETTINGS)  # type: ignore[assignment]
    else:
        # Ollama and plain OpenAI-compatible endpoints: no thinking request
        # knobs at all — the provider's native default stands. Verified live:
        # current Ollama /v1 ignores `think`/`reasoning_effort` either way
        # (hybrid models think by default), and older servers only know the
        # native-API `think` flag, not an OpenAI-compat equivalent.
        model_settings = ModelSettings()

    from grc_agent.native_canvas import NativeFlowgraphProxy

    persistence_metadata = {
        "provider": str(cfg.get("provider", "")),
        "model": str(cfg.get("model", "")),
        "base_url": _provider_base_url(cfg),
    }

    executor: Agent[NativeFlowgraphProxy, Any] = Agent(
        model=model,
        deps_type=NativeFlowgraphProxy,
        output_type=[GrcAgentResponse, str, DeferredToolRequests],
        name="grc_desktop_executor_agent",
        instructions=build_system_prompt("pai-desktop-chat"),
        tools=grc_tools(),
        capabilities=[
            json_repair_cap,
            StopGracefully(),
            ModelRequestLogger(),
            StepPersistence(
                store=get_step_store(),
                agent_name="grc_executor",
                metadata=persistence_metadata,
            ),
            SystemReminders(dynamic_reminders=[_execution_plan_reminder]),
            _build_compaction_capability(cfg, agent_name="grc_executor"),
            web_search_cap,
            web_fetch_cap,
            GrcFileSystem(),
            GrcShell(),
            prompt_injection_cap,
        ],
        model_settings=model_settings,
        retries={"tools": 3, "output": 3},
    )

    planner: Agent[NativeFlowgraphProxy, str] = Agent(
        model=model,
        deps_type=NativeFlowgraphProxy,
        output_type=str,
        name="grc_desktop_planner_agent",
        instructions=build_planner_prompt("pai-desktop-planner"),
        tools=grc_tools(),
        capabilities=[
            json_repair_cap,
            StopGracefully(),
            ModelRequestLogger(),
            StepPersistence(
                store=get_step_store(),
                agent_name="grc_planner",
                metadata=persistence_metadata,
            ),
            Planning(
                store_resolver=_plan_store_resolver,
                tools=[],
                guidance=(
                    "You have a planning tool, `write_plan`. For multi-step work, call it first to lay out the steps, "
                    "then keep it current: mark exactly one step `in_progress`, and mark a step `completed` as soon as "
                    "it is fully done. Pass the full plan every time you call `write_plan`."
                ),
            ),
            _build_compaction_capability(cfg, agent_name="grc_planner"),
            web_search_cap,
            web_fetch_cap,
            GrcFileSystem(),
            prompt_injection_cap,
            PrepareTools(_prepare_planner_tools),
        ],
        model_settings=model_settings,
        retries={"tools": 3, "output": 3},
    )
    planner.tool(write_plan_func, name="write_plan")

    def add_active_flowgraph_context(ctx: RunContext[NativeFlowgraphProxy]) -> str | None:
        if ctx.deps is not None:
            cm = getattr(ctx.deps, "_canvas_manager", None)
            if cm and getattr(cm, "path", None):
                return f"Active flowgraph file path: {cm.path}"
        return None

    executor.instructions(add_active_flowgraph_context)
    planner.instructions(add_active_flowgraph_context)
    executor.output_validator(validate_flowgraph_state)
    return AgentBundle(executor, planner, model_build_error)


def build_interactive_agents() -> AgentBundle:
    """Read persisted settings and construct both interactive roles."""
    return build_agents_from_cfg(load_settings())


# One fixed table for every fixed-endpoint provider: the models-URL template,
# the .env key var, and the header builder. `{key}` in a template is replaced
# with the API key (Google's query-string form); headers are per-provider
# (Anthropic's x-api-key + version, everything else Bearer). The context-length
# probes (_openai_shaped_context_length, _google_context_length), the preflight
# probe (_preflight_target) and the Settings Load button all read THIS table —
# one source of truth, never a duplicated copy.
_PREFLIGHT_ENDPOINTS = {
    "openrouter": (
        "https://openrouter.ai/api/v1/models",
        "OPENROUTER_API_KEY",
        lambda k: {"Authorization": f"Bearer {k}"},
    ),
    "openai": (
        "https://api.openai.com/v1/models",
        "OPENAI_API_KEY",
        lambda k: {"Authorization": f"Bearer {k}"},
    ),
    "anthropic": (
        "https://api.anthropic.com/v1/models",
        "ANTHROPIC_API_KEY",
        lambda k: {"x-api-key": k, "anthropic-version": "2023-06-01"},
    ),
    "google": (
        "https://generativelanguage.googleapis.com/v1beta/models?key={key}",
        "GOOGLE_API_KEY",
        lambda _k: {},
    ),
    "groq": (
        "https://api.groq.com/openai/v1/models",
        "GROQ_API_KEY",
        lambda k: {"Authorization": f"Bearer {k}"},
    ),
    "mistral": (
        "https://api.mistral.ai/v1/models",
        "MISTRAL_API_KEY",
        lambda k: {"Authorization": f"Bearer {k}"},
    ),
    "cohere": (
        "https://api.cohere.com/v1/models",
        "COHERE_API_KEY",
        lambda k: {"Authorization": f"Bearer {k}"},
    ),
    "xai": (
        "https://api.x.ai/v1/models",
        "XAI_API_KEY",
        lambda k: {"Authorization": f"Bearer {k}"},
    ),
}

# OpenAI-shaped /v1/models providers whose catalogs carry per-model
# context_length — DERIVED from the table above (everything except the
# nonstandard Anthropic/Google shapes), never a second copy of the URLs.
_OPENAI_SHAPED_PROVIDER_IDS = frozenset(_PREFLIGHT_ENDPOINTS) - {"anthropic", "google"}


def _models_url(base: str) -> str:
    """Normalize a base URL to the OpenAI-shaped /models endpoint."""
    b = base.rstrip("/")
    if b.endswith("/models"):
        return b
    return f"{b}/models" if b.endswith("/v1") else f"{b}/v1/models"


def _preflight_target(provider: str, api_key: str, base_url: str) -> tuple[str, dict] | str:
    """Resolve the provider's /models endpoint to (url, headers), or return an
    error string when a required key is missing. One table
    (_PREFLIGHT_ENDPOINTS) plus this resolver — the only place the endpoints
    are addressed."""
    if provider in _PREFLIGHT_ENDPOINTS:
        url_t, _key_var, headers_fn = _PREFLIGHT_ENDPOINTS[provider]
        if not api_key:
            return f"API key is required for {PROVIDER_LABELS[provider]}"
        return url_t.format(key=api_key), headers_fn(api_key)

    if provider == "openai_compatible":
        base = (
            base_url
            or get_env_value("OPENAI_COMPATIBLE_BASE_URL")
            or "http://localhost:8080/v1"
        ).rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        return _models_url(base), headers
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
