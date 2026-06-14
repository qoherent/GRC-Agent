"""Typed recovery policy for failed tool outcomes.

Relocated from src/grc_agent/recovery.py — only used by test harness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from grc_agent._payload import ErrorCode

RECOVERABLE_MISSING_ARGUMENTS = "recoverable_missing_arguments"
RECOVERABLE_SAVE_REFUSED = "recoverable_save_refused"
RECOVERABLE_CLARIFICATION = "recoverable_clarification"
NONRECOVERABLE_INVALID_END_STATE = "nonrecoverable_invalid_end_state"
NONRECOVERABLE_UNSUPPORTED = "nonrecoverable_unsupported"
NONRECOVERABLE_FAILED_MUTATION = "nonrecoverable_failed_mutation"
NO_RECOVERY_NEEDED = "no_recovery_needed"

READ_ONLY_INSPECTION_TOOLS = ("summarize_graph", "get_grc_context")


@dataclass(frozen=True)
class RecoveryDecision:
    recovery_class: str
    recoverable: bool
    allowed_tools: tuple[str, ...] = ()
    max_mutation_retries: int = 0
    prompt: str = ""
    reason: str = ""


def classify_tool_result_for_recovery(
    tool_name: str,
    result: dict[str, Any],
) -> RecoveryDecision:
    if result.get("ok") is True:
        return RecoveryDecision(
            recovery_class=NO_RECOVERY_NEEDED,
            recoverable=False,
            reason="tool succeeded",
        )

    if result.get("clarification_required") is True:
        return RecoveryDecision(
            recovery_class=RECOVERABLE_CLARIFICATION,
            recoverable=True,
            reason="tool returned a stored clarification request",
        )

    error_type = result.get("error_type")
    if error_type in {"unsupported", ErrorCode.INVALID_REQUEST}:
        return RecoveryDecision(
            recovery_class=NONRECOVERABLE_UNSUPPORTED,
            recoverable=False,
            reason="unsupported request",
        )

    if tool_name == "save_graph" and (
        error_type == ErrorCode.SAVE_REFUSED or result.get("requires_validation") is True
    ):
        return RecoveryDecision(
            recovery_class=RECOVERABLE_SAVE_REFUSED,
            recoverable=True,
            allowed_tools=("validate_graph", "save_graph"),
            max_mutation_retries=0,
            prompt=(
                "Save refused — graph has unvalidated changes."
            ),
            reason="dirty graph requires validation before save",
        )

    if tool_name in {"apply_edit", "remove_connection"}:
        if _is_invalid_end_state_failure(result):
            return RecoveryDecision(
                recovery_class=NONRECOVERABLE_INVALID_END_STATE,
                recoverable=False,
                reason="grcc rejected the requested end state",
            )
        if _has_missing_field_error(result):
            mutation_tool = "remove_connection" if tool_name == "remove_connection" else "apply_edit"
            return RecoveryDecision(
                recovery_class=RECOVERABLE_MISSING_ARGUMENTS,
                recoverable=True,
                allowed_tools=(*READ_ONLY_INSPECTION_TOOLS, mutation_tool),
                max_mutation_retries=1,
                prompt=(
                    "Previous mutation call was missing required arguments."
                ),
                reason="mutation arguments were incomplete",
            )
        return RecoveryDecision(
            recovery_class=NONRECOVERABLE_FAILED_MUTATION,
            recoverable=False,
            reason="failed mutation does not match a bounded recovery class",
        )

    return RecoveryDecision(
        recovery_class=NONRECOVERABLE_FAILED_MUTATION,
        recoverable=False,
        reason="no recovery policy for failed tool",
    )


def _has_missing_field_error(result: dict[str, Any]) -> bool:
    errors = result.get("errors")
    if isinstance(errors, list) and any(
        isinstance(error, dict) and error.get("code") == "missing_field"
        for error in errors
    ):
        return True
    validation_errors = result.get("validation_errors")
    return isinstance(validation_errors, list) and any(
        isinstance(error, dict) and error.get("code") == "missing_required"
        for error in validation_errors
    )


def _is_invalid_end_state_failure(result: dict[str, Any]) -> bool:
    if result.get("error_type") != ErrorCode.GNU_VALIDATION_FAILED:
        return False
    validation = result.get("validation")
    stdout = ""
    if isinstance(validation, dict):
        raw_stdout = validation.get("stdout")
        if isinstance(raw_stdout, str):
            stdout = raw_stdout.lower()
    return "port is not connected" in stdout
