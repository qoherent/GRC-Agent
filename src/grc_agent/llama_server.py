"""Thin llama.cpp server adapter over the narrowed GRC runtime."""

import json
import re
import socket
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from grc_agent.agent import GrcAgent


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
        max_tokens: int = 12000,
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
            normalized_calls.append(
                LlamaToolCall(
                    id=str(call_id) if call_id is not None else f"tool_call_{index}",
                    name=name,
                    arguments=LlamaServerClient._parse_tool_arguments(arguments, index),
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
            except json.JSONDecodeError as exc:
                raise LlamaServerError(
                    f"llama.cpp tool call {call_index} returned invalid JSON arguments."
                ) from exc
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
    def _is_timeout_reason(reason: Any) -> bool:
        """Return whether a `URLError.reason` value represents a timeout."""
        return isinstance(reason, TimeoutError | socket.timeout)


_SAFETY_MAX_TOOL_ROUNDS = 50
_VALIDATION_INTENT_TERMS = (
    "validate",
    "validation",
    "valid",
    "compile",
    "run",
    "work",
    "works",
)
_DESCRIBE_INTENT_TERMS = (
    "describe",
    "what does",
    "tell me about",
    "explain",
    "parameters",
    "ports",
    "inputs",
    "outputs",
)
_SAVE_INTENT_TERMS = (
    "save",
    "write",
    "persist",
    "write out",
    "dump",
)
_INSPECT_BEFORE_EDIT_TERMS = (
    "look",
    "inspect",
    "check",
    "show",
    "see",
)
_PREVIEW_INTENT_TERMS = (
    "preview",
    "dry-run",
    "dry run",
    "what-if",
    "what if",
    "would it work",
    "what would happen if",
)
_PREVIEW_FOLLOW_UP_TERMS = (
    "apply",
    "validate",
    "validation",
    "save",
    "persist",
    "commit",
)
_SUMMARY_INTENT_TERMS = (
    "summary",
    "summarize",
    "give me a summary",
    "overview",
)


def run_bounded_llama_turn(
    agent: GrcAgent,
    client: LlamaServerClient,
    user_message: str,
    *,
    model: str | None = None,
) -> dict[str, Any]:
    """Run an unbounded llama.cpp -> runtime loop against one loaded flowgraph."""
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

    tool_calls_executed = 0
    tool_rounds_used = 0
    assistant_turns = 0

    while True:
        response = client.create_chat_completion(
            model=resolved_model,
            messages=agent.get_model_messages(),
            tools=agent.get_tool_schemas(),
        )
        assistant_turns += 1
        assistant_content, tool_calls = client.parse_assistant_message(response)

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
            if tool_rounds_used >= _SAFETY_MAX_TOOL_ROUNDS:
                return {
                    "ok": False,
                    "model": resolved_model,
                    "steps": assistant_turns,
                    "tool_rounds_used": tool_rounds_used,
                    "tool_calls_executed": tool_calls_executed,
                    "message": "Safety tool-round ceiling reached before the model produced a final answer.",
                }

            tool_rounds_used += 1
            for tool_call in tool_calls:
                validation_result = agent.validate_tool_call(
                    tool_call.name, tool_call.arguments
                )
                if validation_result is not None:
                    agent.history.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                            "content": validation_result,
                        }
                    )
                    continue
                result = agent.execute_tool(tool_call.name, tool_call.arguments)
                tool_calls_executed += 1
                agent.history.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": result,
                    }
                )
            continue

        follow_up_reminder = _build_follow_up_reminder(user_message, agent.history)
        if follow_up_reminder is not None:
            agent.history.append(
                {
                    "role": "reminder",
                    "code": follow_up_reminder["code"],
                    "content": follow_up_reminder["message"],
                }
            )
            continue

        assistant_text = _resolve_final_assistant_text(
            agent.history, assistant_content or ""
        )
        agent.history[-1]["content"] = assistant_text
        return {
            "ok": True,
            "model": resolved_model,
            "steps": assistant_turns,
            "tool_rounds_used": tool_rounds_used,
            "tool_calls_executed": tool_calls_executed,
            "assistant_text": assistant_text,
        }


def _resolve_final_assistant_text(
    history: list[dict[str, Any]], assistant_text: str
) -> str:
    """Deterministically finalize supported runtime outcomes from tool results."""
    tool_turns = [turn for turn in history[:-1] if turn.get("role") == "tool"]
    latest_tool_turn = tool_turns[-1] if tool_turns else None
    unsupported_message = _unsupported_request_message(history)
    if (
        isinstance(latest_tool_turn, dict)
        and latest_tool_turn.get("name") == "summarize_graph"
        and isinstance(latest_tool_turn.get("content"), dict)
        and isinstance(latest_tool_turn["content"].get("summary"), str)
    ):
        return latest_tool_turn["content"]["summary"]

    if _looks_like_tool_call_text(assistant_text):
        if (
            isinstance(latest_tool_turn, dict)
            and isinstance(latest_tool_turn.get("content"), dict)
            and isinstance(latest_tool_turn["content"].get("message"), str)
        ):
            return latest_tool_turn["content"]["message"]
        if unsupported_message is not None:
            return unsupported_message
        return "I could not complete that request with the available tools."

    if assistant_text.strip():
        return assistant_text

    if (
        isinstance(latest_tool_turn, dict)
        and isinstance(latest_tool_turn.get("content"), dict)
        and isinstance(latest_tool_turn["content"].get("message"), str)
    ):
        return latest_tool_turn["content"]["message"]
    if unsupported_message is not None and latest_tool_turn is None:
        return unsupported_message
    return "I could not complete that request with the available tools."


def _looks_like_tool_call_text(text: str) -> bool:
    """Return whether the final model text looks like a raw tool call stub."""
    stripped = text.strip()
    if not stripped:
        return False
    return bool(
        re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*\s*(\{.*\}|\(.*\))",
            stripped,
            re.DOTALL,
        )
    )


def _unsupported_request_message(history: list[dict[str, Any]]) -> str | None:
    user_turns = [turn for turn in history if turn.get("role") == "user"]
    if not user_turns:
        return None
    content = user_turns[-1].get("content")
    if not isinstance(content, str):
        return None
    lowered = content.lower()
    if "undo" in lowered:
        return "Undo is unsupported."
    if "redo" in lowered:
        return "Redo is unsupported."
    if ("export" in lowered and "python" in lowered) or "standalone python script" in lowered:
        return "Exporting as standalone Python is unsupported."
    if "yaml" in lowered and ("edit" in lowered or "raw" in lowered):
        return "Editing raw YAML directly is unsupported."
    if "generate code" in lowered or "generate python" in lowered:
        return "Code generation is unsupported."
    return None


def _build_follow_up_reminder(
    user_message: str, history: list[dict[str, Any]]
) -> dict[str, str] | None:
    """Return one unmet follow-up requirement inferred from the user request."""
    lowered = user_message.lower()
    successful_tool_names = _successful_tool_names(history)
    last_success = _last_successful_tool_indices(history)
    existing_reminders = {
        turn.get("code")
        for turn in history
        if turn.get("role") == "reminder" and isinstance(turn.get("code"), str)
    }

    if _requests_validation(lowered) and not _is_preview_only_request(lowered):
        last_validate = last_success.get("validate_graph", -1)
        last_change_or_load = max(
            last_success.get("apply_edit", -1),
            last_success.get("load_grc", -1),
        )
        if (
            ("validate_graph" not in successful_tool_names)
            or last_validate < last_change_or_load
        ) and "validate_graph_required" not in existing_reminders:
            return {
                "code": "validate_graph_required",
                "message": (
                    "Reminder: the user asked you to validate the graph. "
                    "Call `validate_graph` before you finish."
                ),
            }

    if _requests_summary(lowered):
        if (
            "summarize_graph" not in successful_tool_names
            and "summarize_graph_required" not in existing_reminders
        ):
            return {
                "code": "summarize_graph_required",
                "message": (
                    "Reminder: the user asked for a graph summary. "
                    "Call `summarize_graph` before you finish."
                ),
            }

    if _requests_description(lowered):
        last_search = last_success.get("search_grc", -1)
        last_describe = last_success.get("describe_block", -1)
        if (
            last_search > last_describe
            and "describe_block_required" not in existing_reminders
        ):
            return {
                "code": "describe_block_required",
                "message": (
                    "Reminder: the user asked for a block description. "
                    "After `search_grc`, call `describe_block` with the chosen result's `block_id`, not its `node_id`."
                ),
            }

    if _requests_save(lowered):
        last_save = last_success.get("save_graph", -1)
        last_edit = last_success.get("apply_edit", -1)
        last_validate = last_success.get("validate_graph", -1)
        if (
            last_edit > last_save
            and last_validate >= last_edit
            and "save_graph_required" not in existing_reminders
        ):
            return {
                "code": "save_graph_required",
                "message": (
                    "Reminder: the user asked to save the graph. "
                    "The edit was applied and validated. Call `save_graph` before you finish."
                ),
            }

    if _needs_inspect_before_edit(lowered):
        edit_tools_used = {
            "apply_edit",
            "propose_edit",
        } & successful_tool_names
        inspect_tools_used = {
            "summarize_graph",
            "get_grc_context",
            "search_grc",
            "describe_block",
        } & successful_tool_names
        if (
            edit_tools_used
            and not inspect_tools_used
            and "inspect_before_edit" not in existing_reminders
        ):
            return {
                "code": "inspect_before_edit",
                "message": (
                    "Reminder: the user asked to inspect or look at something before making a change. "
                    "Call an inspection tool first. If the user named a specific loaded block or variable, "
                    "prefer `get_grc_context` over `summarize_graph`."
                ),
            }

    return None


def _successful_tool_names(history: list[dict[str, Any]]) -> set[str]:
    return {
        str(turn.get("name"))
        for turn in history
        if turn.get("role") == "tool"
        and isinstance(turn.get("name"), str)
        and isinstance(turn.get("content"), dict)
        and turn["content"].get("ok") is True
    }


def _last_successful_tool_indices(history: list[dict[str, Any]]) -> dict[str, int]:
    indices: dict[str, int] = {}
    for index, turn in enumerate(history):
        if (
            turn.get("role") == "tool"
            and isinstance(turn.get("name"), str)
            and isinstance(turn.get("content"), dict)
            and turn["content"].get("ok") is True
        ):
            indices[str(turn["name"])] = index
    return indices


def _requests_validation(lowered_user_message: str) -> bool:
    return any(term in lowered_user_message for term in _VALIDATION_INTENT_TERMS)


def _requests_description(lowered_user_message: str) -> bool:
    return any(term in lowered_user_message for term in _DESCRIBE_INTENT_TERMS)


def _requests_summary(lowered_user_message: str) -> bool:
    return any(term in lowered_user_message for term in _SUMMARY_INTENT_TERMS)


def _requests_save(lowered_user_message: str) -> bool:
    return any(term in lowered_user_message for term in _SAVE_INTENT_TERMS)


def _needs_inspect_before_edit(lowered_user_message: str) -> bool:
    has_inspect_intent = any(
        term in lowered_user_message for term in _INSPECT_BEFORE_EDIT_TERMS
    )
    has_edit_intent = any(
        term in lowered_user_message
        for term in (
            "change",
            "edit",
            "update",
            "set",
            "modify",
            "apply",
            "remove",
            "add",
            "disconnect",
            "connect",
        )
    )
    return has_inspect_intent and has_edit_intent


def _is_preview_only_request(lowered_user_message: str) -> bool:
    has_preview_intent = any(
        term in lowered_user_message for term in _PREVIEW_INTENT_TERMS
    )
    if not has_preview_intent:
        return False
    return not any(term in lowered_user_message for term in _PREVIEW_FOLLOW_UP_TERMS)
