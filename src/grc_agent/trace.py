"""Structured trace helpers for live eval runs."""

from __future__ import annotations

import copy
from typing import Any


def classify_failure_category(*, model_contract_pass: Any, runtime_safety_pass: Any, semantic_pass: Any, passed: Any) -> str:
    """Classify a turn-level failure bucket for reporting without changing scoring."""

    if passed is True:
        return "none"
    if model_contract_pass is False:
        return "model_contract"
    if runtime_safety_pass is False:
        return "runtime_safety"
    if semantic_pass is False:
        return "semantic"
    return "other"


def build_live_eval_turn_trace(
    *,
    prompt: str,
    active_tool_surface: str,
    raw_requested_tool_calls: list[dict[str, Any]],
    requested_tool_calls: list[dict[str, Any]],
    executed_tool_calls: list[dict[str, Any]],
    state_revision_before: Any,
    state_revision_after: Any,
    graph_delta: dict[str, Any],
    model_contract_pass: Any,
    runtime_safety_pass: Any,
    semantic_pass: Any,
    passed: Any,
) -> dict[str, Any]:
    """Build a fixed-shape trace blob for one live turn result."""

    tool_results: list[dict[str, Any]] = []
    validation_result: dict[str, Any] | None = None
    for call in executed_tool_calls:
        if not isinstance(call, dict):
            continue
        name = str(call.get("name", ""))
        arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            continue
        row = {
            "name": name,
            "ok": arguments.get("ok"),
            "error_type": arguments.get("error_type"),
            "arguments": copy.deepcopy(arguments),
        }
        tool_results.append(row)
        if validation_result is None:
            nested_validation = arguments.get("validation")
            if isinstance(nested_validation, dict):
                validation_result = copy.deepcopy(nested_validation)
            nested_validation_result = arguments.get("validation_result")
            if validation_result is None and isinstance(nested_validation_result, dict):
                validation_result = copy.deepcopy(nested_validation_result)

    normalized_args: list[dict[str, Any]] = []
    for call in requested_tool_calls:
        if not isinstance(call, dict):
            continue
        normalized_args.append(
            {
                "name": str(call.get("name", "")),
                "arguments": copy.deepcopy(call.get("arguments", {})),
            }
        )

    return {
        "prompt": prompt,
        "active_tool_surface": active_tool_surface,
        "raw_requested_tool_calls": copy.deepcopy(raw_requested_tool_calls),
        "normalized_args": normalized_args,
        "executed_tools": [str(call.get("name", "")) for call in executed_tool_calls if isinstance(call, dict)],
        "tool_results": tool_results,
        "state_revision_before": state_revision_before,
        "state_revision_after": state_revision_after,
        "graph_delta": copy.deepcopy(graph_delta),
        "validation_result": validation_result,
        "model_contract_pass": model_contract_pass,
        "runtime_safety_pass": runtime_safety_pass,
        "semantic_pass": semantic_pass,
        "failure_category": classify_failure_category(
            model_contract_pass=model_contract_pass,
            runtime_safety_pass=runtime_safety_pass,
            semantic_pass=semantic_pass,
            passed=passed,
        ),
    }
