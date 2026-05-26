"""ToolAgents-backed model/runtime harness for GRC Agent."""

from __future__ import annotations

from dataclasses import dataclass
import datetime
import copy
import json
import logging
import re
import time
from typing import TYPE_CHECKING, Any
import uuid

from openai import OpenAI
from ToolAgents import FunctionTool, ToolRegistry
from ToolAgents.agents import ChatToolAgent
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
from grc_agent.runtime.tool_surface import MVP_TOOL_SURFACE

logger = logging.getLogger(__name__)

_AGENTIC_TOOL_REMINDER = (
    "Runtime reminder: you need current tool evidence for this answer. "
    "Do not ask the user for permission to inspect or search. "
    "Call the relevant available tool now, or answer only if current tool evidence is sufficient. "
    "For parameter edits, inspect_graph details gives exact param_id values. "
    "For adding blocks, search_blocks gives exact installed block_id and param_ids."
)
_MUTATION_TOOL_REMINDER = (
    "Runtime reminder: the user asked to change the active graph. "
    "Use change_graph now with flat batch fields such as add_blocks, update_params, "
    "add_connections, remove_connections, rewire_connections, or insert_blocks_on_connections. "
    "When adding a block, include initial params/states and known connections in the same call. "
    "If exact details are missing, inspect/search first or ask one concise clarification. "
    "Do not give manual GNU Radio Companion steps or claim you cannot edit without "
    "a change_graph result."
)
_INVALID_CHANGE_GRAPH_REMINDER = (
    "Runtime reminder: your previous change_graph call had invalid or missing args. "
    "Retry change_graph with one or more flat edit lists. For existing variables "
    "like samp_rate, use update_variables=[{instance_name, value}]. For existing block "
    "parameters, use update_params=[{instance_name, params:{param_id:value}}]. "
    "block_id is for GNU catalog block types, not graph instance names."
)
_MUTATION_NOT_COMMITTED_REMINDER = (
    "Runtime reminder: the user asked for a graph edit, but no validated "
    "change_graph commit has succeeded in this turn. If enough graph/catalog "
    "evidence is available, call change_graph with the relevant flat edit lists. "
    "If the edit is ambiguous, ask one concise clarification using graph candidates."
)
_WRONG_INSERT_REPAIR_REMINDER = (
    "Runtime reminder: inserting into an existing wire uses insert_blocks_on_connections. "
    "Adding a parallel path or source uses add_blocks plus add_connections in the same call."
)
_FORCED_CHANGE_GRAPH_REMINDER = (
    "Runtime reminder: this response must be a change_graph tool call, not "
    "assistant text. Use the flat edit lists; if exact graph/catalog evidence is "
    "missing, inspect or search first."
)
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


class GrcOpenAIChatAPI(OpenAIChatAPI):
    """OpenAI-compatible provider with llama.cpp request fields preserved."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_seconds: float,
    ) -> None:
        super().__init__(api_key=api_key, model=model, base_url=base_url)
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )


@dataclass
class ToolAgentsLlamaProviderConfig:
    """Configuration for the ToolAgents OpenAI-compatible llama.cpp provider."""

    base_url: str
    model: str = ""
    api_key: str | None = None
    timeout_seconds: float = 60.0
    max_tokens: int = 4096
    temperature: float = 0.0
    enable_thinking: bool = False

    @property
    def openai_base_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/v1"

    def create_provider(self) -> GrcOpenAIChatAPI:
        """Create the ToolAgents OpenAI-compatible provider."""
        return GrcOpenAIChatAPI(
            api_key=self.api_key or "not-needed",
            model=self.model,
            base_url=self.openai_base_url,
            timeout_seconds=self.timeout_seconds,
        )

    def require_ready(self) -> None:
        """Probe llama.cpp readiness for call sites that need an explicit check."""
        from grc_agent.llama_probe import LlamaHealthProbe

        LlamaHealthProbe(
            self.base_url,
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
        ).require_ready()

    def require_model_alias(self, expected_alias: str) -> None:
        """Probe the llama.cpp model alias and update this config on success."""
        from grc_agent.llama_probe import LlamaHealthProbe

        LlamaHealthProbe(
            self.base_url,
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
        ).require_model_alias(expected_alias)
        self.model = expected_alias

    def get_server_properties(self) -> dict[str, Any]:
        """Return llama.cpp `/props` for metadata/reporting callers."""
        from grc_agent.llama_probe import LlamaHealthProbe

        return LlamaHealthProbe(
            self.base_url,
            api_key=self.api_key,
            timeout_seconds=self.timeout_seconds,
        ).get_server_properties()

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
        """Return request settings with llama.cpp extra fields attached."""
        settings = provider.get_default_settings()
        settings.set_value(
            "temperature",
            self.temperature if temperature is None else float(temperature),
        )
        settings.set_value("tool_choice", tool_choice)
        settings.set_value("response_format", response_format)
        settings.add_request_setting("max_tokens", max_tokens or self.max_tokens)
        settings.add_request_setting("parallel_tool_calls", False)
        settings.set_value(
            "extra_body",
            {
                "parse_tool_calls": True,
                "chat_template_kwargs": {
                    "enable_thinking": (
                        self.enable_thinking
                        if enable_thinking is None
                        else bool(enable_thinking)
                    ),
                },
            },
        )
        return settings


class ToolAgentsHistoryAdapter:
    """Convert rendered GRC history dictionaries to ToolAgents messages."""

    @staticmethod
    def model_messages_from_agent(agent: GrcAgent) -> list[ChatMessage]:
        return [
            ToolAgentsHistoryAdapter.from_openai_message(message)
            for message in agent.get_model_messages()
        ]

    @staticmethod
    def from_openai_messages(messages: list[dict[str, Any]]) -> list[ChatMessage]:
        return [
            ToolAgentsHistoryAdapter.from_openai_message(message)
            for message in messages
        ]

    @staticmethod
    def from_openai_message(message: dict[str, Any]) -> ChatMessage:
        role = str(message.get("role") or "")
        date = datetime.datetime.now()
        content: list[Any] = []
        text = message.get("content")
        if isinstance(text, str) and text:
            content.append(TextContent(content=text))
        elif isinstance(text, list):
            joined = _content_list_as_text(text)
            if joined:
                content.append(TextContent(content=joined))

        if role == "assistant":
            raw_tool_calls = message.get("tool_calls")
            if isinstance(raw_tool_calls, list):
                for index, raw_call in enumerate(raw_tool_calls):
                    parsed = _parse_history_tool_call(raw_call, index=index)
                    if parsed is None:
                        continue
                    call_id, name, arguments = parsed
                    content.append(
                        ToolCallContent(
                            tool_call_id=call_id,
                            tool_call_name=name,
                            tool_call_arguments=arguments,
                        )
                    )

        if role == "tool":
            tool_call_id = str(message.get("tool_call_id") or uuid.uuid4())
            tool_name = str(message.get("name") or "")
            content.append(
                ToolCallResultContent(
                    tool_call_result_id=str(uuid.uuid4()),
                    tool_call_id=tool_call_id,
                    tool_call_name=tool_name,
                    tool_call_result=str(message.get("content") or ""),
                )
            )
            return ChatMessage(
                id=str(uuid.uuid4()),
                role=ChatMessageRole.Tool,
                content=content,
                created_at=date,
                updated_at=date,
            )

        role_map = {
            "system": ChatMessageRole.System,
            "user": ChatMessageRole.User,
            "assistant": ChatMessageRole.Assistant,
        }
        chat_role = role_map.get(role, ChatMessageRole.User)
        return ChatMessage(
            id=str(uuid.uuid4()),
            role=chat_role,
            content=content,
            created_at=date,
            updated_at=date,
        )

    @staticmethod
    def assistant_history_entry(message: ChatMessage) -> dict[str, Any]:
        """Convert one ToolAgents assistant message to this repo's trace shape."""
        entry: dict[str, Any] = {
            "role": "assistant",
            "content": _message_text(message),
        }
        tool_calls = []
        for tool_call in message.get_tool_calls():
            tool_calls.append(_tool_call_as_history_payload(tool_call))
        if tool_calls:
            entry["tool_calls"] = tool_calls
        return entry


@dataclass(frozen=True)
class ToolDelegateResult:
    """Result of executing a ToolAgents-requested wrapper."""

    result: dict[str, Any]
    executed: bool


class ToolAgentsToolDelegate:
    """Validation-preserving delegate for one model-facing wrapper."""

    def __init__(
        self,
        agent: GrcAgent,
        tool_name: str,
        *,
        wrapper_eval_telemetry: bool = False,
    ) -> None:
        self.agent = agent
        self.tool_name = tool_name
        self.wrapper_eval_telemetry = wrapper_eval_telemetry

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        """ToolAgents FunctionTool entry point."""
        return self.invoke(kwargs).result

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

        result = self.agent.execute_tool(
            self.tool_name,
            normalized,
            model_tool_call=True,
        )
        return ToolDelegateResult(result=result, executed=True)


class ToolAgentsRegistryBuilder:
    """Build a ToolAgents registry from the current GRC turn schemas."""

    def __init__(self, agent: GrcAgent, *, wrapper_eval_telemetry: bool = False) -> None:
        self.agent = agent
        self.wrapper_eval_telemetry = wrapper_eval_telemetry
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
    ) -> dict[str, Any]:
        """Run one bounded ToolAgents-backed model turn."""
        del mvp_tool_profile
        if not isinstance(user_message, str) or not user_message.strip():
            raise ValueError("user_message must be a non-empty string.")
        resolved_model = model or self.provider_config.model
        if resolved_model != self.provider_config.model:
            raise ToolAgentsRuntimeError(
                "llama.cpp server alias mismatch: "
                f"configured '{resolved_model}', discovered '{self.provider_config.model}'."
            )
        if max_tool_rounds is None:
            max_tool_rounds = MVP_TOOL_SURFACE.default_max_tool_rounds

        pre_compact_chars = sum(len(str(turn)) for turn in agent.history)
        agent.compact_history()
        post_compact_chars = sum(len(str(turn)) for turn in agent.history)
        history_truncated = post_compact_chars < pre_compact_chars
        if any(turn.get("role") == "user" for turn in agent.history):
            agent._record_active_session_history(reason="turn_refresh")
        agent.history.append({"role": "user", "content": user_message})

        unsupported = agent.check_unsupported_request(user_message)
        if unsupported is not None:
            agent.history.append(
                {"role": "assistant", "content": unsupported["assistant_text"]}
            )
            logger.info("unsupported_request_blocked message=%s", user_message[:80])
            return unsupported

        agent._turn_user_message = user_message

        tool_calls_executed = 0
        tool_calls_requested = 0
        tool_names_requested: list[str] = []
        tool_rounds_used = 0
        assistant_turns = 0
        correction_retries_used = 0
        retry_reminders_used: set[str] = set()
        change_graph_schema_failure_pending = False
        change_graph_committed = False
        change_graph_control_response = False
        change_graph_wrong_insert_pending = False
        change_graph_missing_evidence_pending = False
        tool_context_chars = 0
        truncated_tool_output = False
        forced_next_tool_name: str | None = None

        logger.info("turn_start model=%s message=%s", resolved_model, user_message[:80])
        active_allowed_tools = set(MVP_TOOL_SURFACE.model_tool_names)

        while True:
            forced_tool_for_step = forced_next_tool_name
            step_allowed_tools = (
                {forced_next_tool_name}
                if forced_next_tool_name is not None
                else active_allowed_tools
            )
            tool_choice: str | dict[str, Any]
            if forced_next_tool_name is None:
                tool_choice = "auto"
            else:
                tool_choice = {
                    "type": "function",
                    "function": {"name": forced_next_tool_name},
                }
            settings = self.provider_config.create_settings(
                self.provider,
                tool_choice=tool_choice,
            )
            registry_builder = ToolAgentsRegistryBuilder(
                agent,
                wrapper_eval_telemetry=wrapper_eval_telemetry,
            )
            registry = registry_builder.build(step_allowed_tools)
            messages = ToolAgentsHistoryAdapter.model_messages_from_agent(agent)
            assistant_message = self.chat_agent.step(
                messages,
                tool_registry=registry,
                settings=settings,
            )
            forced_next_tool_name = None
            assistant_turns += 1
            tool_calls = assistant_message.get_tool_calls()
            tool_calls_requested += len(tool_calls)
            tool_names_requested.extend(tool_call.tool_call_name for tool_call in tool_calls)

            if tool_calls and tool_rounds_used >= max_tool_rounds:
                return {
                    "ok": False,
                    "error_type": ErrorCode.SAFETY_CEILING,
                    "model": resolved_model,
                    "steps": assistant_turns,
                    "tool_rounds_used": tool_rounds_used,
                    "tool_calls_requested": tool_calls_requested,
                    "tool_calls_executed": tool_calls_executed,
                    "message": (
                        "Safety tool-round ceiling reached before the model "
                        "produced a final answer."
                    ),
                }

            agent.history.append(
                ToolAgentsHistoryAdapter.assistant_history_entry(assistant_message)
            )

            if tool_calls:
                tool_rounds_used += 1
                stopping_failure: dict[str, Any] | None = None
                committed_change_result: dict[str, Any] | None = None
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
                            allowed_tool_names=step_allowed_tools,
                        )
                        result = delegate_result.result
                        executed = delegate_result.executed
                    if tool_name == "change_graph":
                        if executed:
                            change_graph_schema_failure_pending = False
                            if isinstance(result, dict):
                                if result.get("ok") is True and result.get("committed") is True:
                                    change_graph_committed = True
                                    committed_change_result = result
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
                    agent.history.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.tool_call_id,
                            "name": tool_name,
                            "content": result,
                        }
                    )
                    if agent.should_stop_batch_after_result(tool_name, result):
                        stopping_failure = result
                        break
                    if committed_change_result is result:
                        break
                if stopping_failure is not None:
                    assistant_text = _tool_failure_text(stopping_failure)
                    agent.history.append({"role": "assistant", "content": assistant_text})
                    return {
                        "ok": False,
                        "model": resolved_model,
                        "steps": assistant_turns,
                        "tool_rounds_used": tool_rounds_used,
                        "tool_calls_requested": tool_calls_requested,
                        "tool_calls_executed": tool_calls_executed,
                        "assistant_text": assistant_text,
                        "error_type": stopping_failure.get("error_type"),
                        "message": stopping_failure.get("message", assistant_text),
                        "correction_retries_used": correction_retries_used,
                    }
                if committed_change_result is not None:
                    assistant_text = _committed_change_text(committed_change_result)
                    agent.history.append({"role": "assistant", "content": assistant_text})
                    return _attach_context_budget_telemetry(
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
                    )
                continue

            if forced_tool_for_step is not None:
                retry_key = f"forced_{forced_tool_for_step}_missing"
                if (
                    retry_key not in retry_reminders_used
                    and tool_rounds_used < max_tool_rounds
                ):
                    retry_reminders_used.add(retry_key)
                    correction_retries_used += 1
                    forced_next_tool_name = forced_tool_for_step
                    agent.history.append({"role": "user", "content": _FORCED_CHANGE_GRAPH_REMINDER})
                    logger.info(
                        "forced_tool_retry tool=%s retries=%d message=%s",
                        forced_tool_for_step,
                        correction_retries_used,
                        user_message[:80],
                    )
                    continue
                assistant_text = (
                    f"The model did not emit the required {forced_tool_for_step} "
                    "tool call. No graph change was made."
                )
                agent.history[-1]["content"] = assistant_text
                return _attach_context_budget_telemetry(
                    {
                        "ok": False,
                        "model": resolved_model,
                        "steps": assistant_turns,
                        "tool_rounds_used": tool_rounds_used,
                        "tool_calls_requested": tool_calls_requested,
                        "tool_calls_executed": tool_calls_executed,
                        "correction_retries_used": correction_retries_used,
                        "assistant_text": assistant_text,
                        "message": assistant_text,
                        "error_type": ErrorCode.TOOL_CALL_INVALID,
                    },
                    enabled=wrapper_eval_telemetry,
                    model_context_limit=None,
                    history_chars=post_compact_chars,
                    tool_context_chars=tool_context_chars,
                    truncated_history=history_truncated,
                    truncated_tool_output=truncated_tool_output,
                )

            assistant_text = _resolve_final_assistant_text(agent.history, _message_text(assistant_message))
            agent.history[-1]["content"] = assistant_text
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
            )
            retry_key = _retry_reminder_key(retry_reminder)
            if (
                retry_key not in retry_reminders_used
                and tool_rounds_used < max_tool_rounds
                and retry_reminder is not None
            ):
                retry_reminders_used.add(retry_key)
                correction_retries_used += 1
                forced_next_tool_name = _forced_tool_for_retry_reminder(retry_reminder)
                agent.history.append({"role": "user", "content": retry_reminder})
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
            return _attach_context_budget_telemetry(
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
            )


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
) -> str | None:
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


def _committed_change_text(result: dict[str, Any]) -> str:
    lines = ["Committed the graph change and validated it successfully."]
    effects = result.get("effects")
    if isinstance(effects, list) and effects:
        lines.append("Applied changes:")
        lines.extend(f"- {effect}" for effect in effects[:6] if str(effect))
    autosave = result.get("autosave")
    if isinstance(autosave, dict):
        if autosave.get("ok") is True:
            path = autosave.get("path")
            lines.append(f"Autosaved to {path}." if path else "Autosave succeeded.")
        elif autosave.get("skipped") is not True:
            message = autosave.get("message")
            lines.append(f"Autosave failed: {message}" if message else "Autosave failed.")
    return "\n".join(lines)


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


def _forced_tool_for_retry_reminder(reminder: str | None) -> str | None:
    if reminder in {
        _MUTATION_TOOL_REMINDER,
        _INVALID_CHANGE_GRAPH_REMINDER,
        _MUTATION_NOT_COMMITTED_REMINDER,
        _WRONG_INSERT_REPAIR_REMINDER,
    }:
        return "change_graph"
    return None


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
    if _is_missing_graph_evidence_response(result):
        return False
    error_type = result.get("error_type")
    if error_type == ErrorCode.GNU_VALIDATION_FAILED and _has_clear_change_graph_repair(result):
        return False
    if error_type in {
        ErrorCode.PREFLIGHT_REJECTED,
        ErrorCode.GNU_VALIDATION_FAILED,
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
            return True
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

    def create_chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "none",
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del tools
        started = time.perf_counter()
        settings = self.provider_config.create_settings(
            self.provider,
            tool_choice=tool_choice,
            response_format=response_format,
            max_tokens=self.provider_config.max_tokens,
        )
        chat_messages = ToolAgentsHistoryAdapter.from_openai_messages(messages)
        response = self.agent.step(
            chat_messages,
            tool_registry=ToolRegistry(),
            settings=settings,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _chat_message_as_openai_response(response, elapsed_ms=elapsed_ms, model=model)

    def create_chat_completion_raw(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        response = self.create_chat_completion(
            model=model,
            messages=messages,
            tools=[],
            tool_choice="none",
            response_format=response_format,
        )
        raw_response_text = json.dumps(response, sort_keys=True)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "raw_response_text": raw_response_text,
            "http_request_ms": 0,
            "generation_ms": elapsed_ms,
            "roundtrip_ms": elapsed_ms,
        }


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


def _parse_history_tool_call(
    raw_call: Any,
    *,
    index: int,
) -> tuple[str, str, dict[str, Any] | str] | None:
    if not isinstance(raw_call, dict):
        return None
    function_payload = raw_call.get("function")
    if isinstance(function_payload, dict):
        name = function_payload.get("name")
        arguments = function_payload.get("arguments")
    else:
        name = raw_call.get("name")
        arguments = raw_call.get("arguments")
    if not isinstance(name, str) or not name:
        return None
    if isinstance(arguments, str):
        try:
            parsed_arguments: dict[str, Any] | str = json.loads(arguments)
        except json.JSONDecodeError:
            parsed_arguments = arguments
    elif isinstance(arguments, dict):
        parsed_arguments = arguments
    else:
        parsed_arguments = {}
    call_id = str(raw_call.get("id") or f"tool_call_{index}")
    return call_id, name, parsed_arguments


def _tool_call_as_history_payload(tool_call: ToolCallContent) -> dict[str, Any]:
    arguments = tool_call.tool_call_arguments
    if isinstance(arguments, dict):
        argument_text = json.dumps(arguments, sort_keys=True)
    else:
        argument_text = str(arguments)
    return {
        "id": tool_call.tool_call_id,
        "type": "function",
        "function": {
            "name": tool_call.tool_call_name,
            "arguments": argument_text,
        },
    }


def _content_list_as_text(content: list[Any]) -> str:
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "".join(parts)


def _message_text(message: ChatMessage) -> str:
    parts = [
        content.content
        for content in message.content
        if isinstance(content, TextContent) and isinstance(content.content, str)
    ]
    return "\n".join(part for part in parts if part)


def _chat_message_as_openai_response(
    message: ChatMessage,
    *,
    elapsed_ms: int,
    model: str,
) -> dict[str, Any]:
    tool_calls = [
        _tool_call_as_history_payload(tool_call)
        for tool_call in message.get_tool_calls()
    ]
    response_message: dict[str, Any] = {
        "role": "assistant",
        "content": _message_text(message),
    }
    if tool_calls:
        response_message["tool_calls"] = tool_calls
    return {
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls" if tool_calls else "stop",
                "message": response_message,
            }
        ],
        "usage": {},
        "grc_agent_transport_ms": elapsed_ms,
    }


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
    message = result.get("message")
    if isinstance(message, str) and message.strip():
        return message
    error_type = result.get("error_type")
    if isinstance(error_type, str) and error_type.strip():
        return f"Tool call failed: {error_type}."
    return "I could not complete that request with the available tools."


def _resolve_final_assistant_text(
    history: list[dict[str, Any]],
    assistant_content: str,
) -> str:
    if assistant_content.strip():
        return assistant_content
    for turn in reversed(history):
        if turn.get("role") != "tool":
            continue
        content = turn.get("content")
        if isinstance(content, dict):
            message = content.get("message")
            if isinstance(message, str) and message.strip():
                return message
    return "Request completed."


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
