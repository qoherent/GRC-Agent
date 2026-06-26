"""ToolAgents-backed model/runtime harness for GRC Agent."""

from __future__ import annotations

import copy
import datetime
import json
import logging
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx as _httpx
from openai import APIConnectionError, OpenAI
from ToolAgents import FunctionTool, ToolRegistry
from ToolAgents.agents import ChatToolAgent
from ToolAgents.data_models.chat_history import ChatHistory
from ToolAgents.data_models.messages import (
    ChatMessage,
    ChatMessageRole,
    TextContent,
    ToolCallContent,
    ToolCallResultContent,
)
from ToolAgents.provider import OpenAIChatAPI
from ToolAgents.provider.llm_provider import ProviderSettings

from grc_agent.chat_roles import (
    ASSISTANT_MODEL_ROLE,
    TOOL_MODEL_ROLE,
    chat_message_payload,
)
from grc_agent.domain_models import ErrorCode
from grc_agent.runtime.model_context import MVP_TOOL_SURFACE

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent


class ToolAgentsRuntimeError(RuntimeError):
    """Raised when the ToolAgents runtime cannot complete a turn."""


# Catches the OpenAI SDK's ``APIConnectionError`` (the one bubbled up by
# ``openai.OpenAI`` chat completions) as well as the underlying ``httpx``
# transport errors that surface from a missing local Ollama/OpenRouter
# endpoint. Kept as a tuple so the runner can ``except (_BACKEND_...)``
# once and stay flat against the SDK version.
_BACKEND_CONNECTION_ERRORS = (
    APIConnectionError,
    _httpx.ConnectError,
    _httpx.ConnectTimeout,
    _httpx.ReadTimeout,
)


def _backend_unreachable_hint(server_url: str) -> str:
    """Return the platform-agnostic hint shown to the user.

    No ``systemctl`` / ``journalctl`` / Linux-specific text. Applies on
    macOS and Windows where Ollama is a desktop application that the
    user starts from the menu bar / Start menu. The string is a fact
    (refused connection at URL) — no in-band behavioral directives.
    """
    return f"Connection refused at {server_url}."


def _backend_unreachable_payload(
    *,
    exc: BaseException,
    model: str,
    server_url: str,
    assistant_turns: int = 0,
) -> dict[str, Any]:
    """Build the typed result returned when the backend is unreachable."""
    return {
        "ok": False,
        "error_type": "backend_unreachable",
        "model": model,
        "steps": assistant_turns,
        "tool_rounds_used": 0,
        "tool_calls_requested": 0,
        "tool_calls_executed": 0,
        "assistant_text": _backend_unreachable_hint(server_url),
        "message": _backend_unreachable_hint(server_url),
        "details": {
            "server_url": server_url,
            "exception_type": type(exc).__name__,
        },
    }


def _assistant_text_message(text: str) -> ChatMessage:
    """Build a typed ``ChatMessage`` carrying plain assistant text."""
    now = datetime.datetime.now()
    return ChatMessage(
        id=str(uuid.uuid4()),
        role=ChatMessageRole.Assistant,
        content=[TextContent(content=text)],
        created_at=now,
        updated_at=now,
    )


class GrcOpenAIChatAPI(OpenAIChatAPI):
    """OpenAI-compatible provider.

    Delegates ``get_response``/``get_streaming_response`` to the parent,
    which calls ``self.client.chat.completions.create(**request_kwargs)``.
    The OpenAI SDK natively supports OpenAI-compatible backends (Ollama,
    OpenRouter) via ``base_url`` and forwards ``extra_body`` (used by
    OpenRouter routing controls set in ``create_settings``).
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
    ) -> None:
        super().__init__(api_key=api_key, model=model, base_url=base_url)
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )


@dataclass
class ToolAgentsLlamaProviderConfig:
    """Configuration for the ToolAgents OpenAI-compatible provider."""

    base_url: str
    model: str = ""
    api_key: str | None = None
    timeout_seconds: float = 60.0
    max_tokens: int = 4096
    temperature: float = 0.0
    enable_thinking: bool = False

    @property
    def openai_base_url(self) -> str:
        base = self.base_url.rstrip("/")
        if base.endswith("/v1"):
            return base
        return f"{base}/v1"

    def create_provider(self) -> GrcOpenAIChatAPI:
        """Create the ToolAgents OpenAI-compatible provider."""
        return GrcOpenAIChatAPI(
            api_key=self.api_key or "not-needed",
            model=self.model,
            base_url=self.openai_base_url,
            timeout_seconds=self.timeout_seconds,
        )

    def create_settings(
        self,
        provider: GrcOpenAIChatAPI,
        *,
        tool_choice: str | dict[str, Any] = "auto",
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        enable_thinking: bool | None = None,
    ) -> ProviderSettings:
        """Return request settings."""
        settings = provider.get_default_settings()
        settings.set_value("temperature", self.temperature if temperature is None else float(temperature))
        settings.set_value("tool_choice", tool_choice)
        settings.set_value("response_format", response_format)
        settings.add_request_setting("max_tokens", max_tokens or self.max_tokens)
        settings.add_request_setting("parallel_tool_calls", True)
        think = self.enable_thinking if enable_thinking is None else bool(enable_thinking)
        extra_body: dict[str, Any] = {}
        is_openrouter = "openrouter" in (self.base_url or "").lower()
        # Thinking models (e.g. ornith-9b, qwq, deepseek-r1) put their reasoning
        # in a separate ``thinking`` field and emit EMPTY ``content`` until
        # reasoning finishes — which breaks a tool-calling agent loop that reads
        # ``content``. For the local Ollama /v1 path, ``enable_thinking=False``
        # (the default) sends ``think:false`` so the model answers directly in
        # ``content``. Ignored by non-thinking models; not sent to OpenRouter.
        # Context-window sizing is NOT a per-request concern: Ollama's
        # OpenAI-compatible /v1 endpoint silently ignores per-request num_ctx
        # (the native /api endpoint honors it). Configure num_ctx on the
        # model via a Modelfile (PARAMETER num_ctx ...) instead — see
        # docs/AGENT_FLOW_FINDINGS.md.
        if not is_openrouter and not think:
            extra_body["think"] = False
        if is_openrouter:
            import os

            provider_order = os.getenv("OPENROUTER_PROVIDER_ORDER")
            allow_fallbacks = os.getenv("OPENROUTER_ALLOW_FALLBACKS")

            provider_dict: dict[str, Any] = {}
            if provider_order:
                provider_dict["order"] = [p.strip() for p in provider_order.split(",")]
            if allow_fallbacks is not None:
                provider_dict["allow_fallbacks"] = allow_fallbacks.lower() in ("true", "1", "yes")

            if provider_dict:
                extra_body["provider"] = provider_dict

        if extra_body:
            settings.set_value("extra_body", extra_body)
        return settings


class ToolAgentsHistoryAdapter:
    """Adapter for the OpenAI-shaped helper path that still uses dict messages.

    The main run_turn path no longer needs an adapter: the agent holds a
    typed :class:`ChatHistory` and ``render_model_messages`` returns
    :class:`ChatMessage` objects directly. This shim is kept for the
    JSON-only helper path (``ToolAgentsJsonClient.create_chat_completion``)
    and for any call site that still speaks OpenAI-shaped dicts.
    """

    @staticmethod
    def from_openai_messages(messages: list[dict[str, Any]]) -> list[ChatMessage]:
        out: list[ChatMessage] = []
        for message in messages:
            converted = ChatMessage.from_dictionaries([message])
            if converted:
                out.append(converted[0])
        return out


@dataclass(frozen=True)
class ToolDelegateResult:
    """Result of executing a ToolAgents-requested wrapper."""

    result: dict[str, Any]
    executed: bool


class ToolAgentsToolDelegate:
    def __init__(
        self,
        agent: GrcAgent,
        tool_name: str,
        *,
        on_tool_start: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_end: Callable[[str, Any], None] | None = None,
    ) -> None:
        self.agent = agent
        self.tool_name = tool_name
        self.on_tool_start = on_tool_start
        self.on_tool_end = on_tool_end

    def invoke(
        self,
        arguments: Any,
        *,
        allowed_tool_names: set[str] | tuple[str, ...] | None = None,
    ) -> ToolDelegateResult:
        """Validate route/schema before executing the underlying GRC wrapper."""
        normalized = self.agent.normalize_tool_call_arguments(
            self.tool_name,
            arguments,
            model_tool_call=True,
        )
        route_result = self.agent.validate_turn_route(
            self.tool_name,
            normalized,
            allowed_tool_names=allowed_tool_names,
        )
        validation_result = (
            route_result
            if route_result is not None
            else self.agent.validate_tool_call(
                self.tool_name,
                normalized,
                model_tool_call=True,
            )
        )
        if validation_result is not None:
            return ToolDelegateResult(result=validation_result, executed=False)

        # Trigger tool start callback
        if self.on_tool_start:
            try:
                self.on_tool_start(self.tool_name, normalized)
            except Exception as e:
                logger.error(f"Error in on_tool_start callback: {e}")

        result = self.agent.execute_tool(
            self.tool_name,
            normalized,
            model_tool_call=True,
        )

        # Trigger tool end callback
        if self.on_tool_end:
            try:
                self.on_tool_end(self.tool_name, result)
            except Exception as e:
                logger.error(f"Error in on_tool_end callback: {e}")

        return ToolDelegateResult(result=result, executed=True)


class ToolAgentsRegistryBuilder:
    """Build a ToolAgents registry from the current GRC turn schemas."""

    def __init__(
        self,
        agent: GrcAgent,
        *,
        on_tool_start: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_end: Callable[[str, Any], None] | None = None,
    ) -> None:
        self.agent = agent
        self.on_tool_start = on_tool_start
        self.on_tool_end = on_tool_end
        self.delegates: dict[str, ToolAgentsToolDelegate] = {}

    def build(
        self,
        allowed_tool_names: set[str] | tuple[str, ...] | None = None,
    ) -> ToolRegistry:
        registry = ToolRegistry()
        self.delegates = {}
        for schema in self.agent.get_tool_schemas_for_turn(allowed_tool_names):
            name = str(schema["function"]["name"])
            delegate = ToolAgentsToolDelegate(
                self.agent,
                name,
                on_tool_start=self.on_tool_start,
                on_tool_end=self.on_tool_end,
            )
            registry.add_tool(_function_tool_from_openai_tool(schema, delegate))
            self.delegates[name] = delegate
        return registry


class ToolAgentsRunner:
    """Bounded model turn runner using ToolAgents step calls."""

    def __init__(
        self,
        provider_config: ToolAgentsLlamaProviderConfig,
        *,
        chat_agent: ChatToolAgent | None = None,
    ) -> None:
        self.provider_config = provider_config
        self.provider = (
            chat_agent.chat_api if chat_agent is not None else provider_config.create_provider()
        )
        self.chat_agent = chat_agent or ChatToolAgent(chat_api=self.provider)

    def run_turn(
        self,
        agent: GrcAgent,
        user_message: str,
        *,
        model: str | None = None,
        mvp_tool_profile: bool = True,
        max_tool_rounds: int | None = None,
        on_tool_start: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_end: Callable[[str, Any], None] | None = None,
    ) -> dict[str, Any]:
        """Run one bounded ToolAgents-backed model turn.

        Returns the same dict shape as ``stream_turn``'s final event, for
        callers that want a single blocking call. The internal loop
        yields events through ``_run_turn_events``; this method consumes
        them and assembles the structured result.
        """
        del mvp_tool_profile
        if not isinstance(user_message, str) or not user_message.strip():
            raise ValueError("user_message must be a non-empty string.")

        for event in self._run_turn_events(
            agent,
            user_message,
            model=model,
            max_tool_rounds=max_tool_rounds,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
        ):
            if event.get("event") == "final":
                return event.get("result", {})
        return {
            "ok": False,
            "error_type": "no_final",
            "assistant_text": "Turn loop ended without a final event.",
        }

    def stream_turn(
        self,
        agent: GrcAgent,
        user_message: str,
        *,
        model: str | None = None,
        mvp_tool_profile: bool = True,
        max_tool_rounds: int | None = None,
        on_tool_start: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_end: Callable[[str, Any], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield events from one bounded turn. Same loop as ``run_turn``.

        Events:

        * ``{"event": "chunk", "text": "..."}`` — model output chunk.
          Emitted with real provider streaming when the final assistant
          message has no tool calls; emitted in one chunk otherwise.
        * ``{"event": "tool_start", "name": "..."}`` — pre-tool hook.
        * ``{"event": "tool_end", "name": "...", "result": {...}}`` —
          post-tool hook.
        * ``{"event": "model_message", "role": "...", "payload": {...}}``
          — typed ``ChatMessage`` added to ``agent.chat_history`` during
          the turn. ``role`` is ``assistant_model`` or ``tool_model``.
        * ``{"event": "final", "result": {...}}`` — terminal structured
          result, same shape as ``run_turn``'s return value.
        """
        del mvp_tool_profile
        yield from self._run_turn_events(
            agent,
            user_message,
            model=model,
            max_tool_rounds=max_tool_rounds,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
        )

    def _run_turn_events(
        self,
        agent: GrcAgent,
        user_message: str,
        *,
        model: str | None,
        max_tool_rounds: int | None,
        on_tool_start: Callable[[str, dict[str, Any]], None] | None,
        on_tool_end: Callable[[str, Any], None] | None,
    ) -> Iterator[dict[str, Any]]:
        resolved_model = model or self.provider_config.model
        if not isinstance(resolved_model, str) or not resolved_model.strip():
            no_model_text = "No model configured."
            agent.chat_history.add_assistant_message(no_model_text)
            yield {"event": "chunk", "text": no_model_text}
            yield {
                "event": "final",
                "result": {
                    "ok": False,
                    "error_type": ErrorCode.MODEL_NOT_FOUND,
                    "model": "",
                    "steps": 0,
                    "tool_rounds_used": 0,
                    "tool_calls_requested": 0,
                    "tool_calls_executed": 0,
                    "assistant_text": no_model_text,
                    "message": no_model_text,
                },
            }
            return
        if max_tool_rounds is None:
            max_tool_rounds = MVP_TOOL_SURFACE.default_max_tool_rounds
        if (
            hasattr(agent, "config")
            and hasattr(agent.config, "llama")
            and agent.config.llama.max_tool_rounds
        ):
            max_tool_rounds = max(max_tool_rounds, agent.config.llama.max_tool_rounds)

        from grc_agent.runtime.model_context import _prune_completed_episodes

        agent.chat_history.messages = _prune_completed_episodes(agent.chat_history.messages)
        agent.compact_history()
        if any(m.role == ChatMessageRole.User for m in agent.chat_history.get_messages()):
            agent._record_active_session_history(reason="turn_refresh")
        agent.chat_history.add_user_message(user_message)

        agent._turn_user_message = user_message

        tool_calls_executed = 0
        tool_calls_requested = 0
        tool_names_requested: list[str] = []
        tool_rounds_used = 0
        assistant_turns = 0
        tool_context_chars = 0
        truncated_tool_output = False

        logger.info("turn_start model=%s message=%s", resolved_model, user_message[:80])
        active_allowed_tools = set(MVP_TOOL_SURFACE.model_tool_names)

        while True:
            settings = self.provider_config.create_settings(
                self.provider,
                tool_choice="auto",
            )
            registry_builder = ToolAgentsRegistryBuilder(
                agent,
                on_tool_start=on_tool_start,
                on_tool_end=on_tool_end,
            )
            registry = registry_builder.build(active_allowed_tools)
            messages = _model_messages_with_reminder(agent, reminder=None)
            try:
                assistant_message = self.chat_agent.step(
                    messages,
                    tool_registry=registry,
                    settings=settings,
                )
            except _BACKEND_CONNECTION_ERRORS as exc:
                logger.warning(
                    "Backend unreachable during turn: model=%s url=%s error=%s",
                    resolved_model,
                    self.provider_config.base_url,
                    exc,
                )
                payload = _backend_unreachable_payload(
                    exc=exc,
                    model=resolved_model,
                    server_url=self.provider_config.base_url,
                    assistant_turns=assistant_turns,
                )
                agent.chat_history.add_assistant_message(payload["assistant_text"])
                yield {
                    "event": "model_message",
                    "role": ASSISTANT_MODEL_ROLE,
                    "payload": chat_message_payload(
                        _assistant_text_message(payload["assistant_text"])
                    ),
                }
                yield {"event": "final", "result": payload}
                return
            assistant_turns += 1
            tool_calls = assistant_message.get_tool_calls()
            tool_calls_requested += len(tool_calls)
            tool_names_requested.extend(tool_call.tool_call_name for tool_call in tool_calls)

            if tool_calls and tool_rounds_used >= max_tool_rounds:
                ceiling_text = (
                    f"Tool-round ceiling reached ({max_tool_rounds} rounds) without a final answer."
                )
                yield {"event": "chunk", "text": ceiling_text}
                yield {
                    "event": "final",
                    "result": {
                        "ok": False,
                        "error_type": ErrorCode.SAFETY_CEILING,
                        "model": resolved_model,
                        "steps": assistant_turns,
                        "tool_rounds_used": tool_rounds_used,
                        "tool_calls_requested": tool_calls_requested,
                        "tool_calls_executed": tool_calls_executed,
                        "assistant_text": ceiling_text,
                        "message": (
                            "Safety tool-round ceiling reached before the model "
                            "produced a final answer."
                        ),
                    },
                }
                return

            agent.chat_history.add_message(assistant_message)
            yield {
                "event": "model_message",
                "role": ASSISTANT_MODEL_ROLE,
                "payload": chat_message_payload(assistant_message),
            }

            if tool_calls:
                tool_rounds_used += 1
                stopping_failure: dict[str, Any] | None = None
                for tool_call in tool_calls:
                    tool_name = tool_call.tool_call_name
                    logger.info(
                        "tool_call name=%s args=%s",
                        tool_name,
                        str(tool_call.tool_call_arguments)[:120],
                    )
                    delegate = registry_builder.delegates.get(tool_name)
                    if delegate is None:
                        result = {
                            "tool": tool_name,
                            "ok": False,
                            "error_type": ErrorCode.TOOL_NOT_ALLOWED_FOR_SURFACE,
                            "message": f"Tool '{tool_name}' is not available through the model-facing surface.",
                            "allowed_tools": sorted(active_allowed_tools),
                        }
                        executed = False
                    else:
                        delegate_result = delegate.invoke(
                            tool_call.tool_call_arguments,
                            allowed_tool_names=active_allowed_tools,
                        )
                        result = delegate_result.result
                        executed = delegate_result.executed
                    if executed:
                        tool_calls_executed += 1
                        tool_context_chars += len(str(result))
                        truncated_tool_output = truncated_tool_output or bool(
                            result.get("output_truncated")
                        )
                    tool_result_message = _tool_result_message(tool_call, result)
                    agent.chat_history.add_message(tool_result_message)
                    yield {
                        "event": "model_message",
                        "role": TOOL_MODEL_ROLE,
                        "payload": chat_message_payload(tool_result_message),
                    }
                if stopping_failure is not None:
                    assistant_text = _tool_failure_text(stopping_failure)
                    agent.chat_history.add_assistant_message(assistant_text)
                    yield {
                        "event": "final",
                        "result": {
                            "ok": False,
                            "model": resolved_model,
                            "steps": assistant_turns,
                            "tool_rounds_used": tool_rounds_used,
                            "tool_calls_requested": tool_calls_requested,
                            "tool_calls_executed": tool_calls_executed,
                            "assistant_text": assistant_text,
                            "error_type": stopping_failure.get("error_type"),
                            "message": assistant_text,
                        },
                    }
                    return
                continue

            assistant_text = _resolve_final_assistant_text(
                agent.chat_history, _message_text(assistant_message)
            )
            if assistant_text:
                _replace_last_assistant_text(agent.chat_history, assistant_text)
            logger.info(
                "turn_end ok=True steps=%d tool_rounds=%d tool_calls=%d",
                assistant_turns,
                tool_rounds_used,
                tool_calls_executed,
            )
            yield {
                "event": "chunk",
                "text": assistant_text,
            }
            yield {
                "event": "final",
                "result": {
                    "ok": True,
                    "model": resolved_model,
                    "steps": assistant_turns,
                    "tool_rounds_used": tool_rounds_used,
                    "tool_calls_requested": tool_calls_requested,
                    "tool_calls_executed": tool_calls_executed,
                    "assistant_text": assistant_text,
                },
            }
            return


class ToolAgentsJsonClient:
    """Small JSON-response helper backed by ToolAgents, not custom HTTP chat."""

    def __init__(
        self,
        provider_config: ToolAgentsLlamaProviderConfig,
        *,
        timeout_seconds: float | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
        enable_thinking: bool = False,
    ) -> None:
        self.provider_config = ToolAgentsLlamaProviderConfig(
            base_url=provider_config.base_url,
            model=provider_config.model,
            api_key=provider_config.api_key,
            timeout_seconds=timeout_seconds or provider_config.timeout_seconds,
            max_tokens=max_tokens or provider_config.max_tokens,
            temperature=temperature,
            enable_thinking=enable_thinking,
        )
        self.timeout_seconds = self.provider_config.timeout_seconds
        self.provider = self.provider_config.create_provider()
        self.agent = ChatToolAgent(chat_api=self.provider)


def run_bounded_toolagents_turn(
    agent: GrcAgent,
    provider_config: ToolAgentsLlamaProviderConfig | None = None,
    user_message: str = "",
    *,
    client: ToolAgentsLlamaProviderConfig | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compatibility-free entry point for one ToolAgents-backed turn."""
    if provider_config is None:
        provider_config = client
    if provider_config is None:
        raise ValueError("provider_config is required.")
    requested_model = kwargs.get("model")
    if not provider_config.model and isinstance(requested_model, str):
        provider_config.model = requested_model
    return ToolAgentsRunner(provider_config).run_turn(agent, user_message, **kwargs)


def _function_tool_from_openai_tool(
    schema: dict[str, Any],
    delegate: ToolAgentsToolDelegate,
) -> FunctionTool:
    """Create a FunctionTool while preserving the original OpenAI schema.

    ToolAgents 0.3.0 has a narrow JSON Schema to Pydantic converter. The runtime
    still sends our exact schema to the provider by overriding to_openai_tool.
    """
    compatible_schema = _toolagents_compatible_schema(schema)
    tool = FunctionTool.from_openai_tool(compatible_schema, delegate)
    tool.to_openai_tool = lambda schema=schema: copy.deepcopy(schema)  # type: ignore[method-assign]
    return tool


def _toolagents_compatible_schema(schema: dict[str, Any]) -> dict[str, Any]:
    compatible = copy.deepcopy(schema)
    parameters = compatible.get("function", {}).get("parameters")
    if isinstance(parameters, dict):
        parameters["properties"] = _compatible_properties(parameters.get("properties", {}))
    return compatible


def _compatible_properties(properties: Any) -> dict[str, Any]:
    if not isinstance(properties, dict):
        return {}
    return {
        str(name): _compatible_property_schema(value)
        for name, value in properties.items()
        if isinstance(value, dict)
    }


def _compatible_property_schema(value: dict[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(value)
    copied.pop("description", None)
    field_type = copied.get("type", "string")
    if isinstance(field_type, list):
        copied["type"] = _first_supported_schema_type(field_type)
    if copied.get("type") == "array":
        items = copied.get("items")
        if not isinstance(items, dict) or _is_primitive_item_schema(items):
            copied["items"] = {}
        else:
            copied["items"] = _compatible_properties(items)
    elif copied.get("type") == "object":
        copied["properties"] = _compatible_properties(copied.get("properties", {}))
    return copied


def _first_supported_schema_type(values: list[Any]) -> str:
    for value in values:
        if value in {"string", "number", "integer", "boolean", "array", "object"}:
            return str(value)
    return "string"


def _is_primitive_item_schema(items: dict[str, Any]) -> bool:
    return "type" in items and "properties" not in items


def _message_text(message: ChatMessage) -> str:
    parts = [
        content.content
        for content in message.content
        if isinstance(content, TextContent) and isinstance(content.content, str)
    ]
    return "\n".join(part for part in parts if part)


def _model_messages_with_reminder(agent: GrcAgent, *, reminder: str | None) -> list[ChatMessage]:
    return agent.get_model_messages(reminder=reminder)


def _tool_result_message(tool_call: ToolCallContent, result: Any) -> ChatMessage:
    now = datetime.datetime.now()
    serialized = (
        result if isinstance(result, str) else json.dumps(result, sort_keys=True, default=str)
    )
    return ChatMessage(
        id=str(uuid.uuid4()),
        role=ChatMessageRole.Tool,
        content=[
            ToolCallResultContent(
                tool_call_result_id=str(uuid.uuid4()),
                tool_call_id=tool_call.tool_call_id,
                tool_call_name=tool_call.tool_call_name,
                tool_call_result=serialized,
            )
        ],
        created_at=now,
        updated_at=now,
    )


def _replace_last_assistant_text(chat_history: ChatHistory, text: str) -> None:
    messages = chat_history.get_messages()
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role == ChatMessageRole.Assistant:
            new_content = [
                TextContent(content=text) if isinstance(item, TextContent) else item
                for item in messages[index].content
            ]
            if not any(isinstance(item, TextContent) for item in new_content):
                new_content.insert(0, TextContent(content=text))
            messages[index] = ChatMessage(
                id=messages[index].id,
                role=messages[index].role,
                content=new_content,
                created_at=messages[index].created_at,
                updated_at=messages[index].updated_at,
                additional_fields=messages[index].additional_fields,
                additional_information=messages[index].additional_information,
            )
            return


def _tool_failure_text(result: dict[str, Any]) -> str:
    if _is_native_validation_refusal(result):
        lines = [
            "I attempted the requested edit, but did not commit it because "
            "native GRC validation rejected the candidate graph."
        ]
        lines.append("No changes were committed.")
        native_errors = _native_validation_errors_from_result(result)
        if native_errors:
            lines.append("Native GRC validation reported:")
            _MAX_NATIVE_ERRORS = 3
            shown = [e for e in native_errors[:_MAX_NATIVE_ERRORS] if str(e)]
            if len(native_errors) > _MAX_NATIVE_ERRORS:
                from grc_agent.runtime.text_utils import format_truncation_flag

                shown.append(
                    format_truncation_flag(
                        "native_validation_errors",
                        len(native_errors),
                        _MAX_NATIVE_ERRORS,
                        unit="items",
                    )
                )
            lines.extend(f"- {error}" for error in shown)
        return "\n".join(lines)
    message = result.get("message")
    if isinstance(message, str) and message.strip():
        return message
    error_type = result.get("error_type")
    if isinstance(error_type, str) and error_type.strip():
        return f"Tool call failed: {error_type}."
    return "I could not complete that request with the available tools."


def _is_native_validation_refusal(result: dict[str, Any]) -> bool:
    return bool(
        result.get("ok") is False
        and result.get("error_type") == ErrorCode.GNU_VALIDATION_FAILED
    )


def _native_validation_errors_from_result(result: dict[str, Any]) -> list[str]:
    # New minimal shape: validation errors surface as errors[].code == "gnu_validation".
    for entry in result.get("errors") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("code") == "gnu_validation" and entry.get("message"):
            return [str(entry["message"])]
    return []


def _resolve_final_assistant_text(
    chat_history: ChatHistory,
    assistant_content: str,
) -> str:
    if assistant_content.strip():
        return assistant_content

    # The model emitted no final text. Return empty — the tool results
    # are already in the history as tool messages and are visible to the
    # model on the next turn. Per AGENTS.md 'no silent transformation',
    # we must not substitute a tool's message field as the model's own
    # words (that would make the model "see itself saying" something it
    # never said on the next turn).
    return ""


def model_name_matches(name: str, available_ids: list[str]) -> bool:
    """Check if a model name exists in available IDs with :latest suffix tolerance."""
    if name in available_ids:
        return True
    stripped = name.removesuffix(":latest")
    return any(
        id_.removesuffix(":latest") == stripped or id_ == f"{stripped}:latest"
        for id_ in available_ids
    )


__all__ = [
    "GrcOpenAIChatAPI",
    "ToolAgentsHistoryAdapter",
    "ToolAgentsJsonClient",
    "ToolAgentsLlamaProviderConfig",
    "ToolAgentsRegistryBuilder",
    "ToolAgentsRunner",
    "ToolAgentsRuntimeError",
    "run_bounded_toolagents_turn",
]
