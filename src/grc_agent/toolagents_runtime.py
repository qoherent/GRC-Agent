"""ToolAgents-backed model/runtime harness for GRC Agent."""

from __future__ import annotations

import copy
import datetime
import json
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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

from grc_agent._payload import ErrorCode
from grc_agent.runtime.model_context import MVP_TOOL_SURFACE
from grc_agent.session_ops import (
    ASSISTANT_MODEL_ROLE,
    TOOL_MODEL_ROLE,
    chat_message_payload,
)

logger = logging.getLogger(__name__)

_AGENTIC_TOOL_REMINDER = "Current tool evidence may be insufficient for this answer."
_MUTATION_TOOL_REMINDER = "The user requested a graph mutation that has not been executed."
_INVALID_CHANGE_GRAPH_REMINDER = "The previous change_graph call had invalid or missing arguments."
_MUTATION_NOT_COMMITTED_REMINDER = "No graph edit has succeeded yet for this request."
_WRONG_INSERT_REPAIR_REMINDER = "Wire insertion validation failed. Review occupied ports."
_TOOL_NEED_PATTERNS = (
    re.compile(
        r"\b(?:need|needs|needed|would need|must|should)\b.{0,80}\b(?:inspect|search|look up|check|query)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:inspect|search|look up|check|query)\b.{0,80}\b(?:would be needed|is needed|are needed|required)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:use|call|run)\b.{0,40}\b(?:inspect_graph|search_blocks|ask_grc_docs)\b",
        re.IGNORECASE,
    ),
)
_GRAPH_LOCAL_FACT_TERMS = (
    "active graph",
    "this graph",
    "loaded graph",
    "current graph",
    "flowgraph",
    "block",
    "blocks",
    "source",
    "sink",
    "connection",
    "wire",
    "port",
    "parameter",
    "param",
    "value",
    "frequency",
    "freq",
    "sample rate",
    "sampling rate",
    "waveform",
    "cosine",
    "sine",
    "amplitude",
    "variable",
)
_GRAPH_MUTATION_TERMS = (
    "add",
    "change",
    "connect",
    "delete",
    "disable",
    "disconnect",
    "edit",
    "enable",
    "insert",
    "make",
    "modify",
    "remove",
    "rewire",
    "set",
    "turn off",
    "turn on",
)
_GRAPH_MUTATION_CONTEXT_TERMS = (
    "block",
    "connection",
    "freq",
    "frequency",
    "graph",
    "parameter",
    "param",
    "sample rate",
    "samp_rate",
    "signal source",
    "source",
    "variable",
    "wire",
)

if TYPE_CHECKING:
    from grc_agent.agent import GrcAgent


class ToolAgentsRuntimeError(RuntimeError):
    """Raised when the ToolAgents runtime cannot complete a turn."""


# Catches the OpenAI SDK's ``APIConnectionError`` (the one bubbled up by
# ``openai.OpenAI`` chat completions) as well as the underlying ``httpx``
# transport errors that surface from a missing local Ollama/OpenRouter
# endpoint. Kept as a tuple so the runner can ``except (_BACKEND_...)``
# once and stay flat against the SDK version.
import httpx as _httpx

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
    user starts from the menu bar / Start menu.
    """
    return (
        "Connection refused. Is Ollama running? "
        "Ensure the Ollama application is active or check the system "
        f"service at {server_url}."
    )


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
    """OpenAI-compatible provider."""

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
        self._is_openrouter = "openrouter" in (base_url or "").lower()
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )

    def get_response(
        self,
        messages: list[ChatMessage],
        settings=None,
        tools: list[FunctionTool] | None = None,
    ) -> ChatMessage:
        if self._is_openrouter:
            request_kwargs = self._prepare_request(messages, settings, tools)
            request_kwargs["stream"] = False

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

            json_payload = {}
            for k, v in request_kwargs.items():
                if k == "extra_body" and isinstance(v, dict):
                    json_payload.update(v)
                else:
                    json_payload[k] = v

            import requests
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=json_payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()

            # Mock classes to mimic OpenAI's API responses for ToolAgents' response converter
            class MockFunction:
                def __init__(self, name: str, arguments: str) -> None:
                    self.name = name
                    self.arguments = arguments

            class MockToolCall:
                def __init__(self, tc_id: str, function: MockFunction) -> None:
                    self.id = tc_id
                    self.function = function

            class MockChatCompletion:
                def __init__(self, response_data: dict[str, Any]) -> None:
                    self.raw_data = response_data
                    message_data = response_data["choices"][0]["message"]

                    tcs = []
                    if "tool_calls" in message_data and message_data["tool_calls"]:
                        for tc in message_data["tool_calls"]:
                            tcs.append(
                                MockToolCall(
                                    tc_id=tc["id"],
                                    function=MockFunction(
                                        name=tc["function"]["name"],
                                        arguments=tc["function"]["arguments"],
                                    ),
                                )
                            )
                def model_dump(self) -> dict[str, Any]:
                    return self.raw_data

            mock_completion = MockChatCompletion(data)
            return self.response_converter.from_provider_response(mock_completion)
        else:
            return super().get_response(messages, settings=settings, tools=tools)

    def get_streaming_response(
        self,
        messages: list[ChatMessage],
        settings=None,
        tools: list[FunctionTool] | None = None,
    ):
        if self._is_openrouter:
            raise NotImplementedError("Streaming is not supported for openrouter backend.")
        return super().get_streaming_response(messages, settings=settings, tools=tools)


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
        base = self.base_url.rstrip('/')
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
        settings.set_value(
            "temperature",
            self.temperature if temperature is None else float(temperature),
        )
        settings.set_value("tool_choice", tool_choice)
        settings.set_value("response_format", response_format)
        settings.add_request_setting("max_tokens", max_tokens or self.max_tokens)
        settings.add_request_setting("parallel_tool_calls", True)
        if "openrouter" in (self.base_url or "").lower():
            extra_body = {}
            import os
            provider_order = os.getenv("OPENROUTER_PROVIDER_ORDER")
            allow_fallbacks = os.getenv("OPENROUTER_ALLOW_FALLBACKS")

            provider_dict = {}
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
        wrapper_eval_telemetry: bool = False,
        on_tool_start: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_end: Callable[[str, Any], None] | None = None,
    ) -> None:
        self.agent = agent
        self.tool_name = tool_name
        self.wrapper_eval_telemetry = wrapper_eval_telemetry
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
        normalized = _maybe_enable_wrapper_eval_telemetry(
            self.tool_name,
            normalized,
            enabled=self.wrapper_eval_telemetry,
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
        wrapper_eval_telemetry: bool = False,
        on_tool_start: Callable[[str, dict[str, Any]], None] | None = None,
        on_tool_end: Callable[[str, Any], None] | None = None,
    ) -> None:
        self.agent = agent
        self.wrapper_eval_telemetry = wrapper_eval_telemetry
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
                wrapper_eval_telemetry=self.wrapper_eval_telemetry,
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
        wrapper_eval_telemetry: bool = False,
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
            wrapper_eval_telemetry=wrapper_eval_telemetry,
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
        wrapper_eval_telemetry: bool = False,
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
            wrapper_eval_telemetry=wrapper_eval_telemetry,
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
        wrapper_eval_telemetry: bool,
        max_tool_rounds: int | None,
        on_tool_start: Callable[[str, dict[str, Any]], None] | None,
        on_tool_end: Callable[[str, Any], None] | None,
    ) -> Iterator[dict[str, Any]]:
        resolved_model = model or self.provider_config.model
        if max_tool_rounds is None:
            max_tool_rounds = MVP_TOOL_SURFACE.default_max_tool_rounds
        if hasattr(agent, 'config') and hasattr(agent.config, 'llama') and agent.config.llama.max_tool_rounds:
            max_tool_rounds = max(max_tool_rounds, agent.config.llama.max_tool_rounds)

        from grc_agent.runtime.model_context import _prune_completed_episodes
        agent.chat_history.messages = _prune_completed_episodes(agent.chat_history.messages)

        pre_compact_chars = _chat_history_chars(agent.chat_history)
        agent.compact_history()
        post_compact_chars = _chat_history_chars(agent.chat_history)
        history_truncated = post_compact_chars < pre_compact_chars
        if any(m.role == ChatMessageRole.User for m in agent.chat_history.get_messages()):
            agent._record_active_session_history(reason="turn_refresh")
        agent.chat_history.add_user_message(user_message)

        unsupported = agent.check_unsupported_request(user_message)
        if unsupported is not None:
            agent.chat_history.add_assistant_message(unsupported["assistant_text"])
            logger.info("unsupported_request_blocked message=%s", user_message[:80])
            yield {"event": "final", "result": unsupported}
            return

        agent._turn_user_message = user_message

        tool_calls_executed = 0
        tool_calls_requested = 0
        tool_names_requested: list[str] = []
        tool_rounds_used = 0
        assistant_turns = 0
        correction_retries_used = 0
        retry_reminders_used: set[str] = set()
        seen_tool_calls: dict[tuple[str, str], dict[str, Any]] = {}
        change_graph_schema_failure_pending = False
        change_graph_committed = False
        change_graph_control_response = False
        change_graph_wrong_insert_pending = False
        change_graph_missing_evidence_pending = False
        graph_ambiguity_pending = False
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
                wrapper_eval_telemetry=wrapper_eval_telemetry,
                on_tool_start=on_tool_start,
                on_tool_end=on_tool_end,
            )
            registry = registry_builder.build(active_allowed_tools)
            messages = _model_messages_with_reminder(
                agent, reminder=None
            )
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
                    "The model ran for the maximum number of tool rounds "
                    f"({max_tool_rounds}) without producing a final answer. "
                    "This can happen when a small local model loops on the "
                    "same question. Please rephrase your request or be more "
                    "specific."
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
                    before_revision = agent.session.state_revision
                    before_session_id = id(agent.session)
                    delegate = registry_builder.delegates.get(tool_name)
                    dedup_key = (
                        tool_name,
                        _canonicalize_args(tool_call.tool_call_arguments),
                    )
                    if (
                        delegate is not None
                        and dedup_key in seen_tool_calls
                    ):
                        dedup_result = {
                            "tool": tool_name,
                            "ok": False,
                            "deduplicated": True,
                            "message": (
                                "Duplicate tool call detected with identical arguments. "
                                "Request ignored. Reformulate your query or change parameters."
                            ),
                        }
                        logger.info(
                            "tool_call_dedup name=%s", tool_name
                        )
                        if on_tool_start:
                            try:
                                on_tool_start(tool_name, tool_call.tool_call_arguments)
                            except Exception as e:
                                logger.error(f"Error in on_tool_start callback: {e}")
                        if on_tool_end:
                            try:
                                on_tool_end(tool_name, dedup_result)
                            except Exception as e:
                                logger.error(f"Error in on_tool_end callback: {e}")
                        result = dedup_result
                        executed = False
                    elif delegate is None:
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
                    if tool_name == "change_graph":
                        if executed:
                            change_graph_schema_failure_pending = False
                            if isinstance(result, dict):
                                if result.get("ok") is True and result.get("committed") is True:
                                    change_graph_committed = True
                                error_type = result.get("error_type")
                                wrong_insert = _is_repairable_insert_in_connection_response(
                                    result
                                )
                                if wrong_insert:
                                    change_graph_wrong_insert_pending = True
                                if (
                                    not wrong_insert
                                    and (
                                        error_type
                                        in {
                                            "clarification_required",
                                            ErrorCode.UNSUPPORTED_OP,
                                        }
                                        or _is_terminal_change_graph_failure(result)
                                    )
                                ):
                                    change_graph_control_response = True
                                    if _is_terminal_change_graph_failure(result):
                                        stopping_failure = result
                                        break
                                if _is_missing_graph_evidence_response(result):
                                    change_graph_missing_evidence_pending = True
                        elif isinstance(result, dict) and result.get("ok") is False:
                            change_graph_schema_failure_pending = True
                    if executed:
                        tool_calls_executed += 1
                        tool_context_chars += len(str(result))
                        truncated_tool_output = truncated_tool_output or bool(
                            result.get("output_truncated")
                        )
                        if isinstance(result, dict) and _is_ambiguous_tool_result(result):
                            graph_ambiguity_pending = True
                        if (
                            isinstance(result, dict)
                            and result.get("ok") is True
                        ):
                            seen_tool_calls[dedup_key] = result

                        if (
                            agent.session.state_revision != before_revision
                            or id(agent.session) != before_session_id
                        ):
                            seen_tool_calls.clear()
                    tool_result_message = _tool_result_message(tool_call, result)
                    agent.chat_history.add_message(tool_result_message)
                    yield {
                        "event": "model_message",
                        "role": TOOL_MODEL_ROLE,
                        "payload": chat_message_payload(tool_result_message),
                    }
                    if agent.should_stop_batch_after_result(tool_name, result):
                        stopping_failure = result
                        break
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
                            "correction_retries_used": correction_retries_used,
                        },
                    }
                    return
                continue

            assistant_text = _resolve_final_assistant_text(
                agent.chat_history, _message_text(assistant_message)
            )
            _replace_last_assistant_text(agent.chat_history, assistant_text)
            retry_reminder = _tool_retry_reminder(
                user_message=user_message,
                assistant_text=assistant_text,
                tool_calls_requested=tool_calls_requested,
                tool_calls_executed=tool_calls_executed,
                tool_names_requested=tool_names_requested,
                change_graph_schema_failure_pending=change_graph_schema_failure_pending,
                change_graph_committed=change_graph_committed,
                change_graph_control_response=change_graph_control_response,
                change_graph_wrong_insert_pending=change_graph_wrong_insert_pending,
                change_graph_missing_evidence_pending=change_graph_missing_evidence_pending,
                graph_ambiguity_pending=graph_ambiguity_pending,
            )
            retry_key = _retry_reminder_key(retry_reminder)
            if (
                retry_key not in retry_reminders_used
                and tool_rounds_used < max_tool_rounds
                and retry_reminder is not None
            ):
                retry_reminders_used.add(retry_key)
                correction_retries_used += 1
                wrapped = f"<runtime_directive>\n{retry_reminder}\n</runtime_directive>"
                agent.chat_history.add_user_message(wrapped)
                logger.info(
                    "tool_evidence_retry retries=%d message=%s",
                    correction_retries_used,
                    user_message[:80],
                )
                continue
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
                "result": _attach_context_budget_telemetry(
                    {
                        "ok": True,
                        "model": resolved_model,
                        "steps": assistant_turns,
                        "tool_rounds_used": tool_rounds_used,
                        "tool_calls_requested": tool_calls_requested,
                        "tool_calls_executed": tool_calls_executed,
                        "correction_retries_used": correction_retries_used,
                        "assistant_text": assistant_text,
                    },
                    enabled=wrapper_eval_telemetry,
                    model_context_limit=None,
                    history_chars=post_compact_chars,
                    tool_context_chars=tool_context_chars,
                    truncated_history=history_truncated,
                    truncated_tool_output=truncated_tool_output,
                ),
            }
            return


def _tool_retry_reminder(
    *,
    user_message: str,
    assistant_text: str,
    tool_calls_requested: int,
    tool_calls_executed: int,
    tool_names_requested: list[str],
    change_graph_schema_failure_pending: bool,
    change_graph_committed: bool,
    change_graph_control_response: bool,
    change_graph_wrong_insert_pending: bool,
    change_graph_missing_evidence_pending: bool,
    graph_ambiguity_pending: bool,
) -> str | None:
    if (
        graph_ambiguity_pending
        and not change_graph_committed
        and _looks_like_graph_mutation_request(user_message)
        and _assistant_asks_for_clarification(assistant_text)
    ):
        return None
    if (
        tool_calls_executed > 0
        and not change_graph_committed
        and _looks_like_graph_mutation_request(user_message)
        and _assistant_asks_for_clarification(assistant_text)
    ):
        return None
    if (
        change_graph_missing_evidence_pending
        and not change_graph_committed
        and _looks_like_graph_mutation_request(user_message)
    ):
        return _AGENTIC_TOOL_REMINDER
    if (
        change_graph_schema_failure_pending
        and _looks_like_graph_mutation_request(user_message)
    ):
        return _INVALID_CHANGE_GRAPH_REMINDER
    if (
        change_graph_wrong_insert_pending
        and not change_graph_committed
        and _looks_like_graph_mutation_request(user_message)
    ):
        return _WRONG_INSERT_REPAIR_REMINDER
    if (
        "change_graph" in tool_names_requested
        and not change_graph_committed
        and not change_graph_control_response
        and _looks_like_graph_mutation_request(user_message)
    ):
        return _MUTATION_NOT_COMMITTED_REMINDER
    if (
        tool_calls_executed == 0
        and _looks_like_graph_mutation_request(user_message)
        and _assistant_asks_for_clarification(assistant_text)
    ):
        return _AGENTIC_TOOL_REMINDER
    if (
        "change_graph" not in tool_names_requested
        and _looks_like_graph_mutation_request(user_message)
    ):
        return _MUTATION_TOOL_REMINDER
    if _assistant_says_tool_needed(assistant_text):
        if _assistant_says_missing_runtime_prerequisite(assistant_text):
            return None
        if tool_calls_executed > 0 or tool_calls_requested == 0:
            return _AGENTIC_TOOL_REMINDER
        return None
    if tool_calls_requested > 0 or tool_calls_executed > 0:
        return None
    if _looks_like_graph_local_fact_question(user_message):
        return _AGENTIC_TOOL_REMINDER
    return None


def _is_ambiguous_tool_result(result: dict[str, Any]) -> bool:
    ambiguity = result.get("ambiguity")
    if isinstance(ambiguity, dict) and ambiguity.get("has_ambiguity") is True:
        return True
    for key in ("errors", "validation_errors"):
        rows = result.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and str(row.get("code") or "").startswith("ambiguous"):
                return True
    return False


def _assistant_asks_for_clarification(text: str) -> bool:
    lowered = text.lower()
    return (
        "?" in text
        or "please specify" in lowered
        or "please provide" in lowered
        or "which " in lowered
        or "clarify" in lowered
    )


def _retry_reminder_key(reminder: str | None) -> str:
    if reminder is None:
        return ""
    if reminder == _AGENTIC_TOOL_REMINDER:
        return "missing_tool_evidence"
    if reminder == _MUTATION_TOOL_REMINDER:
        return "missing_change_graph"
    if reminder == _INVALID_CHANGE_GRAPH_REMINDER:
        return "invalid_change_graph_args"
    if reminder == _MUTATION_NOT_COMMITTED_REMINDER:
        return "mutation_not_committed"
    if reminder == _WRONG_INSERT_REPAIR_REMINDER:
        return "wrong_insert_operation"
    return reminder


def _is_repairable_insert_in_connection_response(result: dict[str, Any]) -> bool:
    """Return true when the tool itself says insertion is the wrong edit shape."""
    if result.get("ok") is True:
        return False
    if result.get("error_type") != "clarification_required":
        return False
    haystack_parts = [str(result.get("message") or "")]
    options = result.get("clarification_options")
    if isinstance(options, list):
        haystack_parts.extend(str(option) for option in options)
    haystack = " ".join(haystack_parts).lower()
    return "parallel source" in haystack and "not the same operation" in haystack


def _is_missing_graph_evidence_response(result: dict[str, Any]) -> bool:
    if result.get("ok") is True:
        return False
    if result.get("error_type") not in {
        "clarification_required",
        ErrorCode.PREFLIGHT_REJECTED,
        ErrorCode.INVALID_REQUEST,
    }:
        return False
    haystack_parts = [str(result.get("message") or "")]
    options = result.get("clarification_options")
    if isinstance(options, list):
        haystack_parts.extend(str(option) for option in options)
    for key in ("errors", "validation_errors"):
        rows = result.get(key)
        if isinstance(rows, list):
            haystack_parts.extend(str(row) for row in rows[:6])
    hint = result.get("hint")
    if isinstance(hint, str):
        haystack_parts.append(hint)
    haystack = " ".join(haystack_parts).lower()
    return (
        "no editable parameter target matched" in haystack
        or "inspect parameters/details" in haystack
        or "inspect the target block details" in haystack
        or "run search_blocks" in haystack
        or "unknown_block_id" in haystack
        or "parameter_not_found" in haystack
        or "port_out_of_range" in haystack
        or (
            "block_not_found" in haystack
            and "include add_blocks and add_connections in the same" in haystack
        )
    )


def _is_terminal_change_graph_failure(result: dict[str, Any]) -> bool:
    if result.get("ok") is True or result.get("committed") is True:
        return False
    if _is_missing_graph_evidence_response(result):
        return False
    error_type = result.get("error_type")
    if error_type == ErrorCode.GNU_VALIDATION_FAILED and _has_clear_change_graph_repair(result):
        return False
    if error_type in {
        ErrorCode.VALIDATION_ERROR,
        ErrorCode.VALIDATION_TIMEOUT,
        ErrorCode.STALE_REVISION,
    }:
        return True
    if error_type == ErrorCode.INVALID_REQUEST:
        message = str(result.get("message") or "").lower()
        return any(term in message for term in ("stale", "invalid", "unknown"))
    validation_result = result.get("validation_result")
    if isinstance(validation_result, dict):
        status = str(validation_result.get("status") or "").lower()
        if status and status not in {"valid", "pass", "ok"}:
            return False
    return False


def _has_clear_change_graph_repair(result: dict[str, Any]) -> bool:
    hint = result.get("hint")
    if not isinstance(hint, str):
        return False
    lowered = hint.lower()
    return "retry with" in lowered and "add_blocks" in lowered and ".params." in lowered


def _assistant_says_tool_needed(text: str) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    return any(pattern.search(text) for pattern in _TOOL_NEED_PATTERNS)


def _assistant_says_missing_runtime_prerequisite(text: str) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    lowered = text.lower()
    return (
        "loaded graph" in lowered
        and any(word in lowered for word in ("need", "needs", "required"))
    )


def _looks_like_graph_local_fact_question(text: str) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    lowered = text.lower()
    if not any(term in lowered for term in _GRAPH_LOCAL_FACT_TERMS):
        return False
    if "?" in text:
        return True
    return any(
        lowered.startswith(prefix)
        for prefix in (
            "tell me",
            "show me",
            "summarize",
            "explain",
            "what",
            "which",
            "where",
            "is ",
            "are ",
            "does ",
            "do ",
        )
    )


def _looks_like_graph_mutation_request(text: str) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    lowered = text.lower()
    if lowered.startswith(("how do i ", "how can i ", "how would i ")):
        return False
    if "?" in text and not any(
        marker in lowered
        for marker in ("please", "can you", "could you", "i want", "do it")
    ):
        return False
    if not any(term in lowered for term in _GRAPH_MUTATION_TERMS):
        return False
    return any(term in lowered for term in _GRAPH_MUTATION_CONTEXT_TERMS)


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
        parameters["properties"] = _compatible_properties(
            parameters.get("properties", {})
        )
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


def _chat_history_chars(chat_history: ChatHistory) -> int:
    return sum(len(m.get_as_text()) for m in chat_history.get_messages())


def _canonicalize_args(arguments: Any) -> str:
    """Return a stable string key for a tool call's argument bag.

    Used to detect retry-storms: if the model issues the same
    ``(tool_name, canonical_args)`` pair twice in a turn, we reuse
    the prior result instead of re-executing.
    """
    try:
        return json.dumps(arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(arguments)


def _model_messages_with_reminder(
    agent: GrcAgent, *, reminder: str | None
) -> list[ChatMessage]:
    return agent.get_model_messages(reminder=reminder)


def _tool_result_message(
    tool_call: ToolCallContent, result: Any
) -> ChatMessage:
    now = datetime.datetime.now()
    serialized = (
        result
        if isinstance(result, str)
        else json.dumps(result, sort_keys=True, default=str)
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
                TextContent(content=text)
                if isinstance(item, TextContent)
                else item
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


def _maybe_enable_wrapper_eval_telemetry(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    enabled: bool,
) -> dict[str, Any]:
    """Inject debug telemetry flag for MVP wrappers during eval-only runs."""
    if not enabled:
        return arguments
    if tool_name not in MVP_TOOL_SURFACE.model_tool_names:
        return arguments
    if bool(arguments.get("debug")):
        return arguments
    injected = dict(arguments)
    injected["debug"] = True
    return injected


def _tool_failure_text(result: dict[str, Any]) -> str:
    if _is_native_validation_refusal(result):
        lines = [
            "I attempted the requested edit, but did not commit it because "
            "native GRC validation rejected the candidate graph."
        ]
        if result.get("graph_unchanged") is True:
            lines.append("The graph is unchanged.")
        else:
            lines.append("No changes were committed.")
        native_errors = _native_validation_errors_from_result(result)
        if native_errors:
            lines.append("Native GRC validation reported:")
            lines.extend(f"- {error}" for error in native_errors[:3] if str(error))
        lines.append(
            "Please choose the intended next step: adjust related graph objects "
            "to keep the graph valid, or explicitly force an invalid intermediate graph."
        )
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
        result.get("committed") is False
        and (
            result.get("rejected_phase") == "native_grc_validation"
            or result.get("error_type") == ErrorCode.GNU_VALIDATION_FAILED
        )
    )


def _native_validation_errors_from_result(result: dict[str, Any]) -> list[str]:
    errors = _string_list(result.get("native_validation_errors"))
    if errors:
        return errors

    for key in ("validation_result", "validation"):
        validation = result.get(key)
        if not isinstance(validation, dict):
            continue
        native = validation.get("native")
        if isinstance(native, dict):
            errors = _string_list(native.get("errors"))
            if errors:
                return errors
        errors = _string_list(validation.get("errors"))
        if errors:
            return errors
    return []


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _resolve_final_assistant_text(
    chat_history: ChatHistory,
    assistant_content: str,
) -> str:
    if assistant_content.strip():
        return assistant_content

    tool_payloads: list[dict[str, Any]] = []
    for message in reversed(chat_history.get_messages()):
        if message.role != ChatMessageRole.Tool:
            continue
        for content in message.content:
            if isinstance(content, ToolCallResultContent):
                try:
                    payload = json.loads(content.tool_call_result)
                except (TypeError, ValueError):
                    continue
                if isinstance(payload, dict):
                    tool_payloads.append(payload)
                    message_text = payload.get("message")
                    if isinstance(message_text, str) and message_text.strip():
                        return message_text

    if not tool_payloads:
        return "Request completed."

    last_payload = tool_payloads[-1]
    tool_name = str(last_payload.get("tool") or "the tool")
    is_ok = last_payload.get("ok") is True
    error_type = str(last_payload.get("error_type") or "").strip()
    raw_message = str(last_payload.get("message") or "").strip()

    if not is_ok:
        detail = raw_message or error_type or "the tool reported a failure"
        return f"I attempted to call {tool_name} but it failed: {detail}."

    return (
        f"{tool_name} completed. Let me know what to do next "
        "(e.g. inspect a block, change a parameter, or save the graph)."
    )


def _attach_context_budget_telemetry(
    result: dict[str, Any],
    *,
    enabled: bool,
    model_context_limit: int | None,
    history_chars: int,
    tool_context_chars: int,
    truncated_history: bool,
    truncated_tool_output: bool,
) -> dict[str, Any]:
    """Attach coarse budget telemetry in eval/debug flows only."""
    if not enabled:
        return result
    telemetry = {
        "model_context_limit": model_context_limit,
        "history_tokens_estimated": _estimate_tokens(history_chars),
        "tool_context_tokens_estimated": _estimate_tokens(tool_context_chars),
        "prompt_tokens_estimated": _estimate_tokens(history_chars + tool_context_chars),
        "truncated_history": bool(truncated_history),
        "truncated_tool_output": bool(truncated_tool_output),
    }
    result["context_budget"] = telemetry
    return result


def _estimate_tokens(chars: int) -> int:
    return max(1, int(chars / 4)) if chars > 0 else 0


def model_name_matches(
    name: str, available_ids: list[str]
) -> bool:
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
