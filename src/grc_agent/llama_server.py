"""Thin llama.cpp server adapter over the narrowed GRC runtime."""

import ast
import json
import logging
import re
import socket
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from grc_agent._payload import ErrorCode
from grc_agent.agent import GrcAgent
from grc_agent.recovery import RECOVERABLE_MISSING_ARGUMENTS, classify_tool_result_for_recovery
from grc_agent.runtime.tool_schemas import PUBLIC_TOOL_NAMES

logger = logging.getLogger(__name__)


class LlamaServerError(RuntimeError):
    """Raised when a llama.cpp server request or response is invalid."""


@dataclass(frozen=True)
class LlamaToolCall:
    """Normalized tool call returned by llama.cpp chat completions."""

    id: str
    name: str
    arguments: dict[str, Any]

    def as_history_tool_call(self) -> dict[str, Any]:
        """Return the OpenAI-style assistant tool-call payload for chat history."""
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, sort_keys=True),
            },
        }


class LlamaServerClient:
    """Call the documented llama.cpp HTTP server endpoints with stdlib only."""

    _LEADING_CONTROL_TOKEN_PATTERN = re.compile(r"^(?:\s*<eos>)+\s*")

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        enable_thinking: bool = False,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero.")
        if (
            not isinstance(max_tokens, int)
            or isinstance(max_tokens, bool)
            or max_tokens < 1
        ):
            raise ValueError("max_tokens must be an integer greater than zero.")
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, int | float)
            or temperature < 0
        ):
            raise ValueError(
                "temperature must be a number greater than or equal to zero."
            )
        if not isinstance(enable_thinking, bool):
            raise ValueError("enable_thinking must be true or false.")

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.temperature = float(temperature)
        self.enable_thinking = enable_thinking

    def require_ready(self) -> None:
        """Fail fast when the llama.cpp server is not ready to answer requests."""
        response = self._request_json("GET", "/health")
        if response.get("status") != "ok":
            raise LlamaServerError("llama.cpp server did not report status=ok.")

    def get_model_id(self) -> str:
        """Return the single configured model id from the models endpoint."""
        return self._get_single_model_entry()["id"]

    def require_model_alias(self, expected_alias: str) -> None:
        """Fail fast when the server alias does not match the configured model id."""
        if not isinstance(expected_alias, str) or not expected_alias.strip():
            raise ValueError("expected_alias must be a non-empty string.")

        discovered_alias = self.get_model_id()
        if discovered_alias != expected_alias:
            raise LlamaServerError(
                "llama.cpp server alias mismatch: "
                f"configured '{expected_alias}', discovered '{discovered_alias}'."
            )

    def get_server_properties(self) -> dict[str, Any]:
        """Return llama.cpp server properties from `/props` when supported."""
        return self._request_json("GET", "/props")

    def create_chat_completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Call the OpenAI-compatible chat completions route with fixed tool settings."""
        payload = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "parse_tool_calls": True,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "chat_template_kwargs": {
                "enable_thinking": self.enable_thinking,
            },
        }
        return self._request_json("POST", "/v1/chat/completions", payload=payload)

    def parse_assistant_message(
        self,
        response: dict[str, Any],
        *,
        fallback_transaction_checker: Any = None,
    ) -> tuple[str | None, list[LlamaToolCall]]:
        """Extract assistant text and normalized tool calls from one completion."""
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LlamaServerError(
                "llama.cpp chat completion did not return any choices."
            )

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise LlamaServerError(
                "llama.cpp chat completion returned an invalid choice entry."
            )

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise LlamaServerError(
                "llama.cpp chat completion choice is missing a message object."
            )

        content = self._strip_leading_control_tokens(
            self._normalize_content(message.get("content"))
        )
        tool_calls = self._parse_tool_calls(message.get("tool_calls"))
        if not tool_calls:
            fallback_tool_calls = self._parse_tool_calls_from_content(
                content,
                transaction_checker=fallback_transaction_checker,
            )
            if fallback_tool_calls:
                return None, fallback_tool_calls
        return content, tool_calls

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send one JSON request and decode one JSON response."""
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        data: bytes | None = None

        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        request_object = request.Request(url, headers=headers, data=data, method=method)

        try:
            response = request.urlopen(request_object, timeout=self.timeout_seconds)
        except error.HTTPError as exc:
            raw_body = exc.read().decode("utf-8")
            raise LlamaServerError(self._format_http_error(exc.code, raw_body)) from exc
        except error.URLError as exc:
            if self._is_timeout_reason(exc.reason):
                raise LlamaServerError(
                    f"Timed out connecting to llama.cpp server at {url}."
                ) from exc
            raise LlamaServerError(
                f"Failed to reach llama.cpp server at {url}: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise LlamaServerError(
                f"Timed out connecting to llama.cpp server at {url}."
            ) from exc
        except socket.timeout as exc:
            raise LlamaServerError(
                f"Timed out connecting to llama.cpp server at {url}."
            ) from exc

        try:
            with response:
                raw_body = response.read().decode("utf-8")
        except TimeoutError as exc:
            raise LlamaServerError(
                f"Timed out waiting for llama.cpp server response from {path}."
            ) from exc
        except socket.timeout as exc:
            raise LlamaServerError(
                f"Timed out waiting for llama.cpp server response from {path}."
            ) from exc

        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise LlamaServerError(
                f"llama.cpp server returned non-JSON response from {path}."
            ) from exc

        if not isinstance(parsed, dict):
            raise LlamaServerError(
                f"llama.cpp server returned non-object JSON from {path}."
            )
        return parsed

    def _get_single_model_entry(self) -> dict[str, Any]:
        """Return the single model entry the adapter expects from `/v1/models`."""
        response = self._request_json("GET", "/v1/models")
        data = response.get("data")
        if not isinstance(data, list):
            raise LlamaServerError(
                "llama.cpp /v1/models response is missing the data list."
            )
        if len(data) != 1:
            raise LlamaServerError(
                "llama.cpp /v1/models response must contain exactly one model entry."
            )

        first_model = data[0]
        if not isinstance(first_model, dict) or not isinstance(
            first_model.get("id"), str
        ):
            raise LlamaServerError(
                "llama.cpp /v1/models response is missing the single model id."
            )
        return first_model

    @staticmethod
    def _format_http_error(status_code: int, raw_body: str) -> str:
        """Convert a llama.cpp HTTP error response into a compact exception message."""
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            return f"llama.cpp server returned HTTP {status_code}: {raw_body.strip()}"

        if isinstance(payload, dict):
            error_payload = payload.get("error")
            if isinstance(error_payload, dict):
                message = error_payload.get("message")
                if isinstance(message, str):
                    return f"llama.cpp server returned HTTP {status_code}: {message}"

        return f"llama.cpp server returned HTTP {status_code}."

    @staticmethod
    def _parse_tool_calls_from_content(
        content: str | None,
        *,
        transaction_checker: Any = None,
    ) -> list[LlamaToolCall]:
        if not isinstance(content, str) or not content.strip():
            return []
        normalized = re.sub(r"<eos>\s*", " ", content).strip()
        calls: list[LlamaToolCall] = []

        json_prefix, remainder = LlamaServerClient._split_leading_json_value(normalized)
        if json_prefix is not None:
            parsed = LlamaServerClient._parse_transaction_json(
                json_prefix, transaction_checker=transaction_checker,
            )
            if parsed is not None:
                calls.append(parsed)
                normalized = remainder.strip()

        if not calls:
            single_call = LlamaServerClient._parse_single_tool_call_from_content(
                normalized, transaction_checker=transaction_checker,
            )
            if single_call is not None:
                return [single_call]

        if normalized:
            for line in normalized.splitlines():
                single_call = LlamaServerClient._parse_single_tool_call_from_content(
                    line.strip(), transaction_checker=transaction_checker,
                )
                if single_call is not None:
                    calls.append(single_call)

        return calls

    @staticmethod
    def _parse_transaction_json(
        content: str,
        *,
        transaction_checker: Any = None,
    ) -> LlamaToolCall | None:
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            repaired = LlamaServerClient._repair_unclosed_json_stub(content)
            if repaired is None:
                return None
            try:
                parsed = json.loads(repaired)
            except json.JSONDecodeError:
                return None
        if transaction_checker is not None and isinstance(parsed, dict) and transaction_checker(
            parsed.get("transaction")
        ):
            return LlamaToolCall(
                id="fallback_tool_call",
                name="apply_edit",
                arguments={"transaction": parsed["transaction"]},
            )
        if transaction_checker is not None and transaction_checker(parsed):
            return LlamaToolCall(
                id="fallback_tool_call",
                name="apply_edit",
                arguments={"transaction": parsed},
            )
        return None

    @staticmethod
    def _repair_unclosed_json_stub(content: str) -> str | None:
        stripped = content.strip()
        if not stripped or stripped[0] not in "[{":
            return None

        stack: list[str] = []
        repaired: list[str] = []
        in_string = False
        escape = False
        closing = {"{": "}", "[": "]"}

        for char in stripped:
            repaired.append(char)
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue
            if char in closing:
                stack.append(char)
                continue
            if char in "}]":
                while stack and closing[stack[-1]] != char:
                    repaired.insert(len(repaired) - 1, closing[stack.pop()])
                if not stack:
                    return None
                stack.pop()

        if in_string:
            return None
        while stack:
            repaired.append(closing[stack.pop()])
        return "".join(repaired)

    @staticmethod
    def _parse_single_tool_call_from_content(
        content: str | None,
        *,
        transaction_checker: Any = None,
    ) -> LlamaToolCall | None:
        if not isinstance(content, str) or not content.strip():
            return None
        normalized = re.sub(r"<eos>\s*", " ", content).strip()
        transaction_call = LlamaServerClient._parse_transaction_json(
            normalized, transaction_checker=transaction_checker,
        )
        if transaction_call is not None:
            return transaction_call
        match = re.search(
            r"([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)",
            normalized,
            re.DOTALL,
        )
        if match is None:
            return None
        call_text = match.group(0)
        try:
            expression = ast.parse(call_text, mode="eval").body
        except SyntaxError:
            return None
        if not isinstance(expression, ast.Call) or not isinstance(expression.func, ast.Name):
            return None
        if expression.func.id not in PUBLIC_TOOL_NAMES or expression.args:
            return None
        arguments: dict[str, Any] = {}
        for keyword in expression.keywords:
            if keyword.arg is None:
                return None
            try:
                arguments[keyword.arg] = ast.literal_eval(keyword.value)
            except (ValueError, SyntaxError):
                return None
        return LlamaToolCall(
            id="fallback_tool_call",
            name=expression.func.id,
            arguments=arguments,
        )

    @staticmethod
    def _split_leading_json_value(content: str) -> tuple[str | None, str]:
        stripped = content.lstrip()
        if not stripped or stripped[0] not in "[{":
            return None, content

        stack = [stripped[0]]
        in_string = False
        escape = False
        closing = {"{": "}", "[": "]"}

        for index in range(1, len(stripped)):
            char = stripped[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue
            if char in closing:
                stack.append(char)
                continue
            if char in "}]":
                if not stack or closing[stack[-1]] != char:
                    break
                stack.pop()
                if not stack:
                    return stripped[: index + 1], stripped[index + 1 :]

        return None, content

    @staticmethod
    def _normalize_content(content: Any) -> str | None:
        """Tolerate both string and structured content payloads from chat responses."""
        if content is None:
            return None
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                    continue
                nested_content = item.get("content")
                if isinstance(nested_content, str):
                    parts.append(nested_content)
            return "".join(parts) or None
        return str(content)

    @classmethod
    def _strip_leading_control_tokens(cls, content: str | None) -> str | None:
        """Remove repeated leading control tokens that should not reach the user."""
        if content is None:
            return None
        sanitized = cls._LEADING_CONTROL_TOKEN_PATTERN.sub("", content)
        return sanitized or None

    @staticmethod
    def _parse_tool_calls(tool_calls: Any) -> list[LlamaToolCall]:
        """Normalize tool calls from either native or generic llama.cpp formats."""
        if tool_calls is None:
            return []
        if not isinstance(tool_calls, list):
            raise LlamaServerError(
                "llama.cpp tool_calls field must be a list when present."
            )

        normalized_calls: list[LlamaToolCall] = []
        for index, call in enumerate(tool_calls, start=1):
            if not isinstance(call, dict):
                raise LlamaServerError("llama.cpp tool call entries must be objects.")

            function_payload = call.get("function")
            if isinstance(function_payload, dict):
                name = function_payload.get("name")
                arguments = function_payload.get("arguments")
            else:
                name = call.get("name")
                arguments = call.get("arguments")

            if not isinstance(name, str) or not name:
                raise LlamaServerError(
                    f"llama.cpp tool call {index} is missing a valid name."
                )

            call_id = call.get("id")
            parsed_arguments = LlamaServerClient._parse_tool_arguments(
                arguments,
                index,
            )
            normalized_calls.append(
                LlamaToolCall(
                    id=str(call_id) if call_id is not None else f"tool_call_{index}",
                    name=name,
                    arguments=parsed_arguments,
                )
            )

        return normalized_calls

    @staticmethod
    def _parse_tool_arguments(arguments: Any, call_index: int) -> dict[str, Any]:
        """Parse one tool-call argument payload into a JSON object."""
        if arguments is None or arguments == "":
            return {}

        if isinstance(arguments, dict):
            parsed_arguments = arguments
        elif isinstance(arguments, str):
            try:
                parsed_arguments = json.loads(arguments)
            except json.JSONDecodeError:
                repaired = LlamaServerClient._repair_unclosed_json_stub(arguments)
                if repaired is None:
                    return {
                        "__invalid_json_arguments__": LlamaServerClient._argument_preview(
                            arguments
                        )
                    }
                try:
                    parsed_arguments = json.loads(repaired)
                except json.JSONDecodeError:
                    return {
                        "__invalid_json_arguments__": LlamaServerClient._argument_preview(
                            arguments
                        )
                    }
        else:
            raise LlamaServerError(
                f"llama.cpp tool call {call_index} returned unsupported argument shape."
            )

        if not isinstance(parsed_arguments, dict):
            raise LlamaServerError(
                f"llama.cpp tool call {call_index} arguments must decode to an object."
            )
        return parsed_arguments

    @staticmethod
    def _argument_preview(arguments: str) -> str:
        """Keep malformed tool arguments visible but bounded for schema rejection."""
        normalized = arguments.strip()
        if len(normalized) <= 200:
            return normalized
        return f"{normalized[:200]}..."

    @staticmethod
    def _is_timeout_reason(reason: Any) -> bool:
        """Return whether a `URLError.reason` value represents a timeout."""
        return isinstance(reason, TimeoutError | socket.timeout)


_SAFETY_MAX_TOOL_ROUNDS = 50


def run_bounded_llama_turn(
    agent: GrcAgent,
    client: LlamaServerClient,
    user_message: str,
    *,
    model: str | None = None,
    track_turn_requirements: bool = True,
) -> dict[str, Any]:
    """Run a bounded llama.cpp -> runtime loop against one loaded flowgraph."""
    if not isinstance(user_message, str) or not user_message.strip():
        raise ValueError("user_message must be a non-empty string.")

    if model is None:
        resolved_model = client.get_model_id()
    else:
        client.require_model_alias(model)
        resolved_model = model
    agent.compact_history()
    if any(turn.get("role") == "user" for turn in agent.history):
        agent._record_active_session_history(reason="turn_refresh")
    agent.history.append({"role": "user", "content": user_message})

    unsupported = agent.check_unsupported_request(user_message)
    if unsupported is not None:
        agent.history.append({"role": "assistant", "content": unsupported["assistant_text"]})
        logger.info("unsupported_request_blocked message=%s", user_message[:80])
        return unsupported

    ambiguous_connection_edit = agent.check_ambiguous_connection_edit(user_message)
    if ambiguous_connection_edit is not None:
        agent.history.append(
            {
                "role": "assistant",
                "content": ambiguous_connection_edit["assistant_text"],
            }
        )
        logger.info("ambiguous_connection_edit_blocked message=%s", user_message[:80])
        return ambiguous_connection_edit

    tool_calls_executed = 0
    tool_rounds_used = 0
    assistant_turns = 0
    correction_retries_used = 0
    correction_allowed_tools: set[str] | None = None

    if track_turn_requirements:
        agent.init_turn_requirements(user_message)

    logger.info("turn_start model=%s message=%s", resolved_model, user_message[:80])

    while True:
        response = client.create_chat_completion(
            model=resolved_model,
            messages=agent.get_model_messages(),
            tools=agent.get_tool_schemas(),
        )
        assistant_turns += 1
        assistant_content, tool_calls = client.parse_assistant_message(
            response,
            fallback_transaction_checker=GrcAgent.looks_like_transaction_payload,
        )

        if tool_calls:
            tool_calls = [
                LlamaToolCall(
                    id=tool_call.id,
                    name=tool_call.name,
                    arguments=agent.normalize_tool_call_arguments(
                        tool_call.name,
                        tool_call.arguments,
                    ),
                )
                for tool_call in tool_calls
            ]

        if tool_calls and tool_rounds_used >= _SAFETY_MAX_TOOL_ROUNDS:
            return {
                "ok": False,
                "error_type": ErrorCode.SAFETY_CEILING,
                "model": resolved_model,
                "steps": assistant_turns,
                "tool_rounds_used": tool_rounds_used,
                "tool_calls_executed": tool_calls_executed,
                "message": "Safety tool-round ceiling reached before the model produced a final answer.",
            }

        assistant_entry: dict[str, Any] = {
            "role": "assistant",
            "content": assistant_content,
        }
        if tool_calls:
            assistant_entry["tool_calls"] = [
                tool_call.as_history_tool_call() for tool_call in tool_calls
            ]
        agent.history.append(assistant_entry)

        if tool_calls:
            tool_rounds_used += 1
            stopping_failure: dict[str, Any] | None = None
            correction_turn_succeeded = False
            for tool_call in tool_calls:
                logger.info(
                    "tool_call name=%s args=%s",
                    tool_call.name,
                    str(tool_call.arguments)[:120],
                )
                if (
                    correction_allowed_tools is not None
                    and tool_call.name not in correction_allowed_tools
                ):
                    stopping_failure = {
                        "tool": tool_call.name,
                        "ok": False,
                        "error_type": "recovery_disallowed_tool",
                        "message": (
                            f"Recovery tool '{tool_call.name}' is not allowed for this "
                            "bounded correction."
                        ),
                        "allowed_tools": sorted(correction_allowed_tools),
                    }
                    agent.record_tool_completion(tool_call.name, False)
                    agent.history.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                            "content": stopping_failure,
                        }
                    )
                    break
                # Pre-validate here (before execute_tool) so that schema-rejected calls
                # are NOT counted in tool_calls_executed.  execute_tool also validates,
                # but that path would increment the counter first.
                validation_result = agent.validate_tool_call(
                    tool_call.name, tool_call.arguments
                )
                if validation_result is not None:
                    agent.record_tool_completion(tool_call.name, False)
                    agent.history.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                            "content": validation_result,
                        }
                    )
                    if agent.should_stop_batch_after_result(tool_call.name, validation_result):
                        stopping_failure = validation_result
                        break
                    continue
                result = agent.execute_tool(tool_call.name, tool_call.arguments)
                tool_calls_executed += 1
                agent.record_tool_completion(tool_call.name, result.get("ok") is True)
                if correction_allowed_tools is not None and result.get("ok") is True:
                    correction_turn_succeeded = True
                agent.history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": result,
                    }
                )
                if agent.should_stop_batch_after_result(tool_call.name, result):
                    stopping_failure = result
                    break
            if stopping_failure is not None:
                recovery_decision = classify_tool_result_for_recovery(
                    str(stopping_failure.get("tool", "")),
                    stopping_failure,
                )
                if (
                    recovery_decision.recovery_class == RECOVERABLE_MISSING_ARGUMENTS
                    and correction_retries_used < recovery_decision.max_mutation_retries
                    and recovery_decision.prompt
                ):
                    correction_retries_used += 1
                    correction_allowed_tools = set(recovery_decision.allowed_tools)
                    agent.history.append(
                        {"role": "user", "content": recovery_decision.prompt}
                    )
                    logger.info(
                        "turn_recovery_retry class=%s retry=%d",
                        recovery_decision.recovery_class,
                        correction_retries_used,
                    )
                    continue
                assistant_text = _tool_failure_text(stopping_failure)
                agent.history.append({"role": "assistant", "content": assistant_text})
                return {
                    "ok": False,
                    "model": resolved_model,
                    "steps": assistant_turns,
                    "tool_rounds_used": tool_rounds_used,
                    "tool_calls_executed": tool_calls_executed,
                    "assistant_text": assistant_text,
                    "error_type": stopping_failure.get("error_type"),
                    "message": stopping_failure.get("message", assistant_text),
                    "correction_retries_used": correction_retries_used,
                }
            if correction_turn_succeeded:
                agent.mark_turn_recovery_success()
            correction_allowed_tools = None
            continue

        should_nudge, nudge = (
            agent.check_turn_continuation() if track_turn_requirements else (False, "")
        )
        if should_nudge:
            agent.history[-1]["content"] = assistant_content or ""
            agent.history.append({"role": "user", "content": nudge})
            logger.info("turn_guard nudge remaining=%s", sorted(
                agent._turn_required_actions - agent._turn_completed_actions
            ))
            continue

        assistant_text = _resolve_final_assistant_text(
            agent.history, assistant_content or ""
        )
        agent.history[-1]["content"] = assistant_text
        logger.info(
            "turn_end ok=True steps=%d tool_rounds=%d tool_calls=%d",
            assistant_turns, tool_rounds_used, tool_calls_executed,
        )
        return {
            "ok": True,
            "model": resolved_model,
            "steps": assistant_turns,
            "tool_rounds_used": tool_rounds_used,
            "tool_calls_executed": tool_calls_executed,
            "correction_retries_used": correction_retries_used,
            "assistant_text": assistant_text,
        }


def _tool_failure_text(result: dict[str, Any]) -> str:
    message = result.get("message")
    if isinstance(message, str) and message.strip():
        return message
    error_type = result.get("error_type")
    if isinstance(error_type, str) and error_type.strip():
        return f"Tool call failed: {error_type}."
    return "I could not complete that request with the available tools."


def _resolve_final_assistant_text(
    history: list[dict[str, Any]], assistant_text: str
) -> str:
    """Deterministically finalize supported runtime outcomes from tool results."""
    tool_turns = [turn for turn in history[:-1] if turn.get("role") == "tool"]
    latest_tool_turn = tool_turns[-1] if tool_turns else None

    summarize_summary = None
    if (
        isinstance(latest_tool_turn, dict)
        and latest_tool_turn.get("name") == "summarize_graph"
        and isinstance(latest_tool_turn.get("content"), dict)
        and isinstance(latest_tool_turn["content"].get("summary"), str)
    ):
        summarize_summary = latest_tool_turn["content"]["summary"]

    if _looks_like_tool_call_text(assistant_text):
        if summarize_summary is not None:
            return summarize_summary
        if (
            isinstance(latest_tool_turn, dict)
            and isinstance(latest_tool_turn.get("content"), dict)
            and isinstance(latest_tool_turn["content"].get("message"), str)
        ):
            return latest_tool_turn["content"]["message"]
        return "I could not complete that request with the available tools."

    if assistant_text.strip():
        return assistant_text

    if summarize_summary is not None:
        return summarize_summary
    if (
        isinstance(latest_tool_turn, dict)
        and isinstance(latest_tool_turn.get("content"), dict)
        and isinstance(latest_tool_turn["content"].get("message"), str)
    ):
        return latest_tool_turn["content"]["message"]
    return "I could not complete that request with the available tools."


def _looks_like_tool_call_text(text: str) -> bool:
    """Return whether the final model text looks like a raw tool call stub."""
    stripped = text.strip()
    if not stripped:
        return False
    if bool(
        re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*\s*(\{.*\}|\(.*\))",
            stripped,
            re.DOTALL,
        )
    ):
        return True
    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError):
        return False
    if not isinstance(parsed, dict):
        return False
    return "name" in parsed or "function" in parsed
