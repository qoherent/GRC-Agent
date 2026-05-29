"""Shared helpers for native MVP R0 and R1 release eval cases."""

from __future__ import annotations

from typing import Any

from tests.llama_eval.harness import ToolExpectation


def _inspect(view: str) -> tuple[ToolExpectation, ...]:
    if view in {"summarize", "summary", "overview", "validate"}:
        return (
            ToolExpectation(
                "inspect_graph",
            ),
        )
    if view in {"context", "details"}:
        return (
            ToolExpectation(
                "inspect_graph",
            ),
        )
    return (ToolExpectation("inspect_graph"),)


def _search(query: str) -> tuple[ToolExpectation, ...]:
    return (ToolExpectation("search_blocks", arguments={"query": query}),)


def _docs(question: str) -> tuple[ToolExpectation, ...]:
    return (ToolExpectation("ask_grc_docs"),)


def _set_param(instance_name: str, param: str, value: str) -> tuple[ToolExpectation, ...]:
    return (
        ToolExpectation(
            "change_graph",
            arguments={
                "update_params": [
                    {"instance_name": instance_name, "params": {param: value}}
                ],
            },
        ),
    )


def _set_state(instance_name: str, state: str) -> tuple[ToolExpectation, ...]:
    return (
        ToolExpectation(
            "change_graph",
            arguments={
                "update_states": [
                    {"instance_name": instance_name, "state": state}
                ],
            },
        ),
    )


def _add_variable(name: str, value: str) -> tuple[ToolExpectation, ...]:
    return (
        ToolExpectation(
            "change_graph",
            arguments={
                "add_variables": [{"instance_name": name, "value": value}],
            },
        ),
    )


def _set_param_delta(instance_name: str, param: str, value: str) -> dict[str, Any]:
    return {
        "block_params": {instance_name: {param: value}},
        "dirty": True,
        "validation_status": "valid",
        "validation_returncode": 0,
    }


def _variable_delta(name: str, value: str) -> dict[str, Any]:
    return {
        "variables": {name: value},
        "block_params": {name: {"value": value}} if name == "samp_rate" else {},
        "dirty": True,
        "validation_status": "valid",
        "validation_returncode": 0,
    }


def READ_ONLY_CHECKS() -> tuple[dict[str, Any], ...]:
    return ({"kind": "no_mutation"},)
