"""Typed recovery policy for failed tool outcomes.

This module classifies failures; it does not execute retries or synthesize graph
repairs. The live harness and any future runtime executor should share these
classes so recovery behavior stays measured and bounded.
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

READ_ONLY_INSPECTION_TOOLS = ("summarize_graph", "get_grc_context", "search_grc")


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
    """Classify one tool result for bounded recovery handling."""
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
                "The previous save was refused because the dirty graph needs validation. "
                "If save was explicitly requested, call validate_graph and then save_graph. "
                "Do not change graph structure or parameters."
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
        if tool_name != "apply_edit":
            return RecoveryDecision(
                recovery_class=NONRECOVERABLE_FAILED_MUTATION,
                recoverable=False,
                reason="failed mutation does not match a bounded recovery class",
            )
        if _has_missing_field_error(result):
            return RecoveryDecision(
                recovery_class=RECOVERABLE_MISSING_ARGUMENTS,
                recoverable=True,
                allowed_tools=(*READ_ONLY_INSPECTION_TOOLS, "apply_edit"),
                max_mutation_retries=1,
                prompt=(
                    "The previous mutation tool result was missing required arguments. "
                    "Use read-only inspection only if needed, then call apply_edit at most "
                    "once for the same user intent. Do not call propose_edit because the "
                    "user requested a real mutation, not a preview. Use exact graph "
                    "endpoints from tool output. If the corrected action is not clearly "
                    "valid, explain and stop. Do not persist the graph."
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
