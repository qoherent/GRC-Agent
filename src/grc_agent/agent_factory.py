from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from pydantic_ai import Agent, ModelRequestContext, ModelSettings, RunContext
from pydantic_ai.capabilities import AbstractCapability, PrepareTools
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig
from pydantic_ai.tools import ToolDefinition
from pydantic_ai_harness import ToolOutputLimits
from pydantic_ai_harness.compaction import (
    ClampOversizedMessages,
    ClearToolResults,
    CompactionStrategy,
    SlidingWindowCompaction,
    SummarizingCompaction,
    TieredCompaction,
)
from pydantic_ai_harness.conversation_search import ConversationSearch, SnapshotHistorySource
from pydantic_ai_harness.filesystem import READ_ONLY_TOOL_NAMES
from pydantic_ai_harness.planning import (
    InMemoryPlanStore,
    Planning,
    PlanStore,
    SqlitePlanStore,
    render_plan,
)
from pydantic_ai_harness.step_persistence import StepPersistence
from pydantic_ai_harness.system_reminders import SystemReminders
from pydantic_ai_harness.tool_output_limits import Band, LocalFileStore, Spill, Truncate
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from grc_agent.agent import (
    GrcAgentResponse,
    StopGracefully,
    grc_tools,
    prompt_injection_cap,
    validate_flowgraph_state,
    web_fetch_cap,
    web_search_cap,
)
from grc_agent.db import archive_transcript, get_db_path, get_step_store, init_db
from grc_agent.fs_tools import GrcFileSystem
from grc_agent.prompts import build_planner_prompt, build_system_prompt
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
        "read_tool_result",
        "search_conversation_history",
        "duckduckgo_search",
        "web_fetch",
        "write_plan",
        "read_plan",
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
    if provider == "openrouter":
        # pydantic-ai's dedicated OpenRouter model/provider: adds the
        # OpenRouter error taxonomy, attribution headers (HTTP-Referer /
        # X-Title), and model profiles — the generic OpenAI path silently
        # discards all of it.
        key = get_env_value("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or None
        return OpenRouterModel(
            cfg["model"],
            provider=OpenRouterProvider(api_key=key, http_client=http_client),
        )
    if provider == "openai":
        key = get_env_value("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY") or None
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
        key = get_env_value(key_var) or os.environ.get(key_var) or None
        import importlib

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
    import httpx

    from grc_agent.settings import get_env_value

    api_key = get_env_value("GOOGLE_API_KEY") or ""
    if not api_key:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
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
    import httpx

    from grc_agent.settings import get_env_value, load_settings

    cfg = load_settings()
    base_url = (cfg.get("ollama_base_url") or "http://localhost:11434").rstrip("/")
    api_key = ""
    if "ollama.com" in base_url:
        api_key = get_env_value("OLLAMA_API_KEY") or get_env_value("OLLAMA_CLOUD_API_KEY") or ""
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


# OpenAI-shaped /v1/models endpoints that carry context_length per model.
_OPENAI_SHAPED_PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1/models", "OPENROUTER_API_KEY"),
    "openai": ("https://api.openai.com/v1/models", "OPENAI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1/models", "GROQ_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1/models", "MISTRAL_API_KEY"),
    "cohere": ("https://api.cohere.com/v1/models", "COHERE_API_KEY"),
    "xai": ("https://api.x.ai/v1/models", "XAI_API_KEY"),
}


def _openai_shaped_context_length(provider: str, model: str) -> int | None:
    """GET the provider's /v1/models catalog -> context_length for the model.
    Works for OpenRouter and plain OpenAI (both expose context_length in the
    OpenAI models shape); a custom endpoint without the field yields None.

    The openai_compatible branch probes the CONFIGURED endpoint (the same
    source of truth `_preflight_target` uses) — never a hardcoded host, or a
    LAN model's window would be silently overridden by OpenRouter's upstream
    spec (the compaction target is now probe-driven)."""
    import httpx

    from grc_agent.settings import get_env_value, load_settings

    if provider in _OPENAI_SHAPED_PROVIDERS:
        url, key_var = _OPENAI_SHAPED_PROVIDERS[provider]
    else:  # openai_compatible — the user's own endpoint
        base = (
            load_settings().get("openai_compatible_base_url")
            or get_env_value("OPENAI_COMPATIBLE_BASE_URL")
            or "http://localhost:8080/v1"
        ).rstrip("/")
        url = base if base.endswith("/models") else f"{base}/models"
        key_var = "OPENAI_COMPATIBLE_API_KEY"
    api_key = get_env_value(key_var) or ""
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


def _codex_context_length(model: str) -> int | None:
    from grc_agent.providers.openai_codex.model import context_window

    return context_window(model)


# Provider -> context-window probe (single-arg: the model id). Anthropic's
# /v1/models carries no context length — the genai-prices registry knows the
# Claude windows, so it maps to None.
_CTX_PROBES = {
    "ollama": _ollama_context_length,
    "ollama_local": _ollama_context_length,
    "ollama_cloud": _ollama_context_length,
    "openai_codex": _codex_context_length,
    "google": _google_context_length,
    "anthropic": lambda _m: None,
}


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
        elif provider in _OPENAI_SHAPED_PROVIDERS or provider == "openai_compatible":
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


class TranscriptPreservingTieredCompaction(TieredCompaction):
    """Archive the exact pre-compaction transcript before replacing history.

    StepPersistence normally snapshots settled tool boundaries and run ends.
    Automatic compaction can instead fire on the first model request, before
    either boundary exists. When a tier actually changes the request history,
    persist the untouched live history under the same conversation in the
    shared step store first. A store failure fails the turn, so compaction can
    never silently destroy the only durable copy used for dataset collection.
    """

    archive_agent_name = "grc_chat"

    async def before_model_request(self, ctx, request_context):  # noqa: ANN001
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


def _build_tool_output_limits() -> ToolOutputLimits:
    """Spill oversized tool returns losslessly instead of flooding context.

    Default band (one uniform rule, no per-tool folklore): any return over
    20k characters is persisted whole and replaced with a handle + preview;
    the model reads slices back on demand via the registered
    ``read_tool_result`` tool. ``then=Truncate()`` covers the case where the
    spill store itself errors (bounded, never a hard failure).

    The spill store is rooted next to the chat DB under ``.grc_agent/``
    (0700, same per-user data area as the DB itself) rather than the
    library default under /tmp — spills must survive restarts to be
    readable from a later session, and a shared tmpfs is the wrong place
    for agent data. ``cleanup_after`` is deliberately unset: the whole
    point is that a handle stays readable for as long as the session lives.
    """
    return ToolOutputLimits(
        # One uniform band, sized by measured tool outputs: a 23-block graph
        # inspection is ~20k chars, so 10k (the library default) would spill
        # routine inspections; 20k chars (~5k tokens, ~2% of a 262k window)
        # keeps typical results inline and still bounds the 100KB flood case.
        bands=[Band(over=20_000, action=Spill(then=Truncate()))],
        store=LocalFileStore(base_dir=get_db_path().parent / "tool_overflow"),
    )


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
        capability.archive_agent_name = agent_name
        return capability

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
    env_override = get_env_value("GRC_COMPACTION_TARGET_TOKENS") or os.environ.get(
        "GRC_COMPACTION_TARGET_TOKENS"
    )
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
    for key, window in _MODEL_WINDOW_OVERRIDES.items():
        if key in model_id:
            return tagged(
                TranscriptPreservingTieredCompaction(
                    tiers=tiers, target_fraction=0.85, context_window=window
                )
            )

    probed = resolve_model_context_length(str(cfg.get("provider", "")), model_id)
    if probed is not None:
        return tagged(
            TranscriptPreservingTieredCompaction(
                tiers=tiers, target_fraction=0.85, context_window=probed
            )
        )

    # Probe failed or impossible: let the harness resolve the registry per
    # request, with the old conservative guesses as the fallback denominator.
    if is_local:
        return tagged(
            TranscriptPreservingTieredCompaction(
                tiers=tiers, target_fraction=0.85, context_window=32_000
            )
        )
    return tagged(
        TranscriptPreservingTieredCompaction(
            tiers=tiers, target_fraction=0.85, fallback_context_window=128_000
        )
    )


def build_agents_from_cfg(cfg: dict) -> AgentBundle:
    """Construct fresh executor and planner agents from loaded settings.

    Both roles share the selected model and canonical message history, but
    their model-visible tools are disjoint. On model-construction failure the
    bundle falls back to defaults and carries the error for the GUI to surface.
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
        output_type=[GrcAgentResponse, str],
        name="grc_desktop_executor_agent",
        instructions=build_system_prompt("pai-desktop-chat"),
        tools=grc_tools(),
        capabilities=[
            StopGracefully(),
            ModelRequestLogger(),
            StepPersistence(
                store=get_step_store(),
                agent_name="grc_executor",
                metadata=persistence_metadata,
            ),
            ConversationSearch(
                SnapshotHistorySource(get_step_store()),
                scope="conversation",
            ),
            SystemReminders(dynamic_reminders=[_execution_plan_reminder]),
            _build_compaction_capability(cfg, agent_name="grc_executor"),
            web_search_cap,
            web_fetch_cap,
            GrcFileSystem(),
            prompt_injection_cap,
            _build_tool_output_limits(),
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
            StopGracefully(),
            ModelRequestLogger(),
            StepPersistence(
                store=get_step_store(),
                agent_name="grc_planner",
                metadata=persistence_metadata,
            ),
            ConversationSearch(
                SnapshotHistorySource(get_step_store()),
                scope="conversation",
            ),
            Planning(
                store_resolver=_plan_store_resolver,
                tools=["write_plan", "read_plan"],
                # Explicit, because the auto-assembled default is wrong for this
                # narrowed tool set: Planning.get_instructions gates its granular
                # sentence on `registered & {'read_plan', 'add_task',
                # 'update_task_status', 'update_task_statuses'}`, so registering
                # `read_plan` alone trips it and the planner is told to call three
                # tools it does not have. `guidance` is used verbatim.
                guidance=(
                    "You have two planning tools. Call `read_plan` to see the current plan, and "
                    "`write_plan` to replace it atomically with the complete plan — pass every step "
                    "each time, marking at most one step `in_progress`."
                ),
            ),
            _build_compaction_capability(cfg, agent_name="grc_planner"),
            web_search_cap,
            web_fetch_cap,
            GrcFileSystem(),
            prompt_injection_cap,
            _build_tool_output_limits(),
            PrepareTools(_prepare_planner_tools),
        ],
        model_settings=model_settings,
        retries={"tools": 3, "output": 3},
    )

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


# /models endpoints for the native cloud providers (the probe + the Load
# button). One uniform table: a callable from api_key -> (url, headers).
_PREFLIGHT_ENDPOINTS = {
    "anthropic": lambda k: (
        "https://api.anthropic.com/v1/models",
        {"x-api-key": k, "anthropic-version": "2023-06-01"},
    ),
    "google": lambda k: (
        f"https://generativelanguage.googleapis.com/v1beta/models?key={k}",
        {},
    ),
    "groq": lambda k: (
        "https://api.groq.com/openai/v1/models",
        {"Authorization": f"Bearer {k}"},
    ),
    "mistral": lambda k: (
        "https://api.mistral.ai/v1/models",
        {"Authorization": f"Bearer {k}"},
    ),
    "cohere": lambda k: (
        "https://api.cohere.com/v1/models",
        {"Authorization": f"Bearer {k}"},
    ),
    "xai": lambda k: (
        "https://api.x.ai/v1/models",
        {"Authorization": f"Bearer {k}"},
    ),
}
_PREFLIGHT_LABELS = {
    "anthropic": "Anthropic",
    "google": "Google (Gemini)",
    "groq": "Groq",
    "mistral": "Mistral",
    "cohere": "Cohere",
    "xai": "xAI",
}


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
                or "http://localhost:8080/v1"
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
            if api_key
            else {}
        )
        return models_url, headers

    if provider in _PREFLIGHT_ENDPOINTS:
        if not api_key:
            return f"API key is required for {_PREFLIGHT_LABELS[provider]}"
        return _PREFLIGHT_ENDPOINTS[provider](api_key)
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
