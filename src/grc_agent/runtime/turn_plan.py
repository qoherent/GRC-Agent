"""Typed turn planning for bounded model-tool execution.

The planner classifies user wording into a small runtime policy. It does not
create transactions, infer graph repairs, or decide GNU Radio validity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from grc_agent.runtime.tool_schemas import PUBLIC_TOOL_NAMES
from grc_agent.turn_guard import parse_required_actions

INTENT_UNKNOWN = "unknown"
INTENT_PARAM_EDIT = "param_edit"
INTENT_STATE_EDIT = "state_edit"
INTENT_REMOVE_BLOCK = "remove_block"
INTENT_PREVIEW = "preview"

MUTATION_TOOL_BY_PREVIEW = {
    False: "apply_edit",
    True: "propose_edit",
}

_PREVIEW_PHRASES = (
    "preview",
    "dry run",
    "dry-run",
    "what would happen",
    "would it work",
    "before changing",
)

_STATE_EDIT_PHRASES = (
    "disable",
    "enable",
    "turn off",
    "turn on",
    "shut off",
    "switch off",
    "switch on",
    "deactivate",
    "activate",
    "re-enable",
    "reenable",
)

_REMOVE_PHRASES = (
    "remove",
    "delete",
    "get rid of",
)

_PARAM_EDIT_PHRASES = (
    "change",
    "set",
    "update",
    "bump",
)


@dataclass(frozen=True)
class TurnPlan:
    """Finite policy for one user turn."""

    intent: str = INTENT_UNKNOWN
    allowed_tools: tuple[str, ...] = PUBLIC_TOOL_NAMES
    expected_op_types: tuple[str, ...] = ()
    required_actions: tuple[str, ...] = ()
    requires_clarification: bool = False
    unsupported_reason: str | None = None
    evidence_span: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_turn_plan(user_message: str) -> TurnPlan:
    """Classify one user request into a small execution policy."""
    text = user_message.lower()
    required_actions = set(parse_required_actions(user_message))
    preview = _contains_any(text, _PREVIEW_PHRASES)

    state_phrase = _first_phrase(text, _STATE_EDIT_PHRASES)
    if state_phrase and not _looks_like_disable_flag_edit(text):
        mutation_tool = MUTATION_TOOL_BY_PREVIEW[preview]
        required_actions.discard("apply_edit")
        required_actions.discard("propose_edit")
        required_actions.add(mutation_tool)
        return _plan(
            intent=INTENT_STATE_EDIT,
            allowed_tools=_ordered_tools((mutation_tool, *required_actions)),
            expected_op_types=("update_states",),
            required_actions=required_actions,
            evidence_span=state_phrase,
        )

    remove_phrase = _first_phrase(text, _REMOVE_PHRASES)
    if remove_phrase and _looks_like_block_removal(text):
        mutation_tool = MUTATION_TOOL_BY_PREVIEW[preview]
        required_actions.discard("apply_edit")
        required_actions.discard("propose_edit")
        required_actions.add(mutation_tool)
        return _plan(
            intent=INTENT_REMOVE_BLOCK,
            allowed_tools=_ordered_tools((mutation_tool, *required_actions)),
            expected_op_types=("remove_block",),
            required_actions=required_actions,
            evidence_span=remove_phrase,
        )

    param_phrase = _first_phrase(text, _PARAM_EDIT_PHRASES)
    if param_phrase:
        mutation_tool = MUTATION_TOOL_BY_PREVIEW[preview]
        required_actions.discard("apply_edit")
        required_actions.discard("propose_edit")
        required_actions.add(mutation_tool)
        return _plan(
            intent=INTENT_PARAM_EDIT if not preview else INTENT_PREVIEW,
            allowed_tools=_ordered_tools((mutation_tool, *required_actions)),
            expected_op_types=("update_params",),
            required_actions=required_actions,
            evidence_span=param_phrase,
        )

    if required_actions:
        return _plan(
            intent=INTENT_UNKNOWN,
            allowed_tools=_ordered_tools(required_actions),
            expected_op_types=(),
            required_actions=required_actions,
            evidence_span="",
        )

    return TurnPlan()


def _plan(
    *,
    intent: str,
    allowed_tools: tuple[str, ...],
    expected_op_types: tuple[str, ...],
    required_actions: set[str],
    evidence_span: str,
) -> TurnPlan:
    return TurnPlan(
        intent=intent,
        allowed_tools=allowed_tools,
        expected_op_types=expected_op_types,
        required_actions=_ordered_tools(required_actions),
        evidence_span=evidence_span,
    )


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def _first_phrase(text: str, phrases: tuple[str, ...]) -> str:
    for phrase in phrases:
        if phrase in text:
            return phrase
    return ""


def _looks_like_disable_flag_edit(text: str) -> bool:
    return "disable flag" in text or "disabled flag" in text


def _looks_like_block_removal(text: str) -> bool:
    if "do not remove" in text or "don't remove" in text:
        return False
    return "block" in text or "_0" in text or "variable" in text


def _ordered_tools(tool_names: set[str] | tuple[str, ...]) -> tuple[str, ...]:
    requested = set(tool_names)
    return tuple(name for name in PUBLIC_TOOL_NAMES if name in requested)
