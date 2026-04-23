"""Shared helpers for live llama eval runners."""

from __future__ import annotations

import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from grc_agent.config import load_app_config
from grc_agent.llama_launcher import LlamaLauncherError, LlamaServerLauncher
from grc_agent.llama_server import LlamaServerClient

DEFAULT_FIXTURE_NAME = "random_bit_generator.grc"


def fixture_path(name: str = DEFAULT_FIXTURE_NAME) -> Path:
    return Path(__file__).resolve().parents[1] / "data" / name


@contextmanager
def isolated_fixture_workspace(
    *fixture_names: str | None,
) -> Iterator[tuple[Path, dict[str, Path]]]:
    """Copy one or more fixtures into a temporary workspace and clean it up."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        copied: dict[str, Path] = {}
        for fixture_name in fixture_names:
            if not fixture_name or fixture_name in copied:
                continue
            src = fixture_path(fixture_name)
            dst = workspace / src.name
            shutil.copy2(src, dst)
            copied[fixture_name] = dst
        yield workspace, copied


def ensure_llama_server(
    server_url: str | None = None,
    model: str | None = None,
) -> tuple[str, str, LlamaServerClient]:
    """Ensure the llama.cpp server is reachable, starting it if necessary.

    Returns (server_url, model_alias, client).
    """
    config = load_app_config()
    resolved_url = (server_url or config.llama.server_url).rstrip("/")
    resolved_model = model or config.llama.model

    def _make_client(url: str) -> LlamaServerClient:
        return LlamaServerClient(
            url,
            timeout_seconds=config.llama.request_timeout_seconds,
            max_tokens=config.llama.max_tokens,
            temperature=config.llama.temperature,
            enable_thinking=config.llama.enable_thinking,
        )

    client = _make_client(resolved_url)
    try:
        client.require_ready()
        client.require_model_alias(resolved_model)
        print(f"Reusing llama.cpp server at {resolved_url}")
        return resolved_url, resolved_model, client
    except Exception:
        pass

    launcher = LlamaServerLauncher(
        config.llama,
        server_url=resolved_url,
        model_alias=resolved_model,
    )
    try:
        result = launcher.ensure_server_ready()
        print(
            f"{result.status.capitalize()} llama.cpp server at {result.server_url} (pid={result.pid})"
        )
        return result.server_url, result.model_alias, _make_client(result.server_url)
    except LlamaLauncherError as exc:
        print(f"Failed to start llama.cpp server: {exc}")
        raise


def restart_llama_server(
    server_url: str | None = None,
    model: str | None = None,
) -> tuple[str, str, LlamaServerClient]:
    """Force a fresh llama.cpp server instance and return a new client."""
    config = load_app_config()
    resolved_url = (server_url or config.llama.server_url).rstrip("/")
    resolved_model = model or config.llama.model

    launcher = LlamaServerLauncher(
        config.llama,
        server_url=resolved_url,
        model_alias=resolved_model,
    )

    with launcher._lock():
        launcher._cleanup_cached_state(launcher._prepare_matching_state())

    result = launcher.ensure_server_ready()
    print(f"Restarted llama.cpp server at {result.server_url} (pid={result.pid})")
    return (
        result.server_url,
        result.model_alias,
        LlamaServerClient(
            result.server_url,
            timeout_seconds=config.llama.request_timeout_seconds,
            max_tokens=config.llama.max_tokens,
            temperature=config.llama.temperature,
            enable_thinking=config.llama.enable_thinking,
        ),
    )



def extract_requested_tool_calls(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return normalized assistant-requested tool calls from chat history."""
    results = []
    for turn in history:
        if turn.get("role") != "assistant":
            continue
        raw_tool_calls = turn.get("tool_calls")
        if not isinstance(raw_tool_calls, list):
            continue
        for raw_call in raw_tool_calls:
            if not isinstance(raw_call, dict):
                continue
            function_payload = raw_call.get("function")
            if isinstance(function_payload, dict):
                name = function_payload.get("name")
                arguments = function_payload.get("arguments")
            else:
                name = raw_call.get("name")
                arguments = raw_call.get("arguments")
            results.append(
                {
                    "name": name,
                    "arguments": _parse_tool_arguments(arguments),
                }
            )
    return results


def extract_executed_tool_calls(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return executed tool results from chat history."""
    return [
        {
            "name": turn.get("name"),
            "arguments": turn.get("content"),
        }
        for turn in history
        if turn.get("role") == "tool"
    ]


def tools_appear_in_expected_order(
    actual_tool_names: list[str], expected_tool_names: list[str]
) -> bool:
    """Return whether expected tools appear in order without later expected tools arriving early."""
    if not expected_tool_names:
        return not actual_tool_names
    expected_index = 0
    for actual_tool_name in actual_tool_names:
        if expected_index >= len(expected_tool_names):
            break
        current_expected_tool = expected_tool_names[expected_index]
        if actual_tool_name == current_expected_tool:
            expected_index += 1
            continue
        if actual_tool_name in expected_tool_names[expected_index + 1 :]:
            return False
    return expected_index == len(expected_tool_names)


def tool_call_matches_transaction_checks(
    tool_call: dict[str, Any],
    expected_operations: list[dict[str, Any]],
    *,
    ordered: bool = True,
) -> bool:
    """Return whether the tool-call transaction matches the expected operations."""
    actual_operations = normalize_transaction_operations(tool_call.get("arguments"))
    if not actual_operations:
        return False
    if ordered:
        actual_index = 0
        for expected_operation in expected_operations:
            while actual_index < len(actual_operations):
                if _partial_match(actual_operations[actual_index], expected_operation):
                    actual_index += 1
                    break
                actual_index += 1
            else:
                return False
        return True
    return all(
        any(
            _partial_match(actual_operation, expected_operation)
            for actual_operation in actual_operations
        )
        for expected_operation in expected_operations
    )


def tool_call_matches_argument_checks(
    tool_call: dict[str, Any], expected_arguments: dict[str, Any]
) -> bool:
    """Return whether the raw tool-call arguments match a partial expectation."""
    return _partial_match(tool_call.get("arguments"), expected_arguments)


def normalize_transaction_operations(arguments: Any) -> list[dict[str, Any]]:
    """Normalize one tool-call argument payload into an ordered transaction list."""
    if not isinstance(arguments, dict):
        return []
    transaction = arguments.get("transaction", arguments)
    if isinstance(transaction, dict):
        return [transaction]
    if isinstance(transaction, list) and all(
        isinstance(item, dict) for item in transaction
    ):
        return list(transaction)
    return []


def text_contains_any(text: str, needles: list[str]) -> bool:
    """Return whether any expected lowercase fragment appears in the text."""
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def render_prompt(prompt: str, target_path: str, save_path: str) -> str:
    return prompt.format(target_path=target_path, save_path=save_path)


def render_value_templates(value: Any, *, target_path: str, save_path: str) -> Any:
    if isinstance(value, str):
        return value.format(target_path=target_path, save_path=save_path)
    if isinstance(value, dict):
        return {
            key: render_value_templates(
                nested_value, target_path=target_path, save_path=save_path
            )
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [
            render_value_templates(item, target_path=target_path, save_path=save_path)
            for item in value
        ]
    return value


def requested_tool_calls_since(
    history: list[dict[str, Any]], start_index: int
) -> list[dict[str, Any]]:
    return extract_requested_tool_calls(history[start_index:])


def executed_tool_calls_since(
    history: list[dict[str, Any]], start_index: int
) -> list[dict[str, Any]]:
    return extract_executed_tool_calls(history[start_index:])


def _parse_tool_arguments(arguments: Any) -> dict[str, Any]:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _partial_match(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(
            key in actual and _partial_match(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(
            _partial_match(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected)
        )
    return actual == expected or str(actual) == str(expected)
