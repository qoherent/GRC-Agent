"""Typed turn planning for bounded model-tool execution.

The planner classifies user wording into a small runtime policy. It does not
create transactions, infer graph repairs, or decide GNU Radio validity.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import re
from typing import Any

from grc_agent.runtime.tool_schemas import PUBLIC_TOOL_NAMES
from grc_agent.turn_guard import parse_required_actions

__version__ = "2026-04-29-atomic-rewire"

INTENT_UNKNOWN = "unknown"
INTENT_PARAM_EDIT = "param_edit"
INTENT_STATE_EDIT = "state_edit"
INTENT_REMOVE_BLOCK = "remove_block"
INTENT_PREVIEW = "preview"
INTENT_AMBIGUOUS = "ambiguous"
INTENT_INSERTION = "insertion"
INTENT_ADD_VARIABLE = "add_variable"
INTENT_UNCERTAIN_MUTATION = "uncertain_mutation"
INTENT_DISCONNECT = "disconnect"
INTENT_REWIRE = "rewire"
INTENT_LOAD = "load"
INTENT_SEMANTIC_SEARCH = "semantic_search"

UNCERTAIN_MUTATION_TOOLS: tuple[str, ...] = ()
DEFAULT_TURN_TOOLS: tuple[str, ...] = tuple(
    tool_name for tool_name in PUBLIC_TOOL_NAMES if tool_name != "semantic_search_grc"
)

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

_LOAD_PHRASES = (
    "load",
    "open",
    "switch to",
    "switch over to",
)

_SESSION_SEARCH_PHRASES = (
    "search the session",
    "search session",
    "search the graph",
    "search graph",
    "find in the session",
    "find in the graph",
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
    "mute",
    "unmute",
    "disabled",
    "enabled",
    "switch",
)

_REMOVE_PHRASES = (
    "remove",
    "delete",
    "get rid of",
    "drop",
)

_PARAM_EDIT_PHRASES = (
    "change",
    "changing",
    "set",
    "setting",
    "update",
    "bump",
)

_UNCERTAIN_MUTATION_PHRASES = (
    "fix",
    "repair",
    "swap",
    "replace",
    "rewire",
    "wire",
    "connect",
    "disconnect",
    "move",
    "rearrange",
    "clean up",
    "make it better",
    "drop samples",
    "turn this into",
    "use an audio smoother",
    "patch",
    "production-ready",
    "topology",
)

_INSERTION_VERBS = (
    "insert",
    "add",
    "put",
    "place",
)

_INSERTION_TARGET_WORDS = (
    "compatible block",
    "block",
    "filter",
    "throttle",
    "head",
    "rate limiter",
)

_INSERTION_ANCHOR_HINTS = (
    "main path",
    "signal path",
    "into the path",
    "into path",
    "between",
    "after",
    "before",
    "from",
    "to",
    "connection",
    "edge",
    "stream",
    "->",
)


@dataclass(frozen=True)
class TurnPlan:
    """Finite policy for one user turn."""

    intent: str = INTENT_UNKNOWN
    allowed_tools: tuple[str, ...] = DEFAULT_TURN_TOOLS
    expected_op_types: tuple[str, ...] = ()
    required_actions: tuple[str, ...] = ()
    requires_clarification: bool = False
    unsupported_reason: str | None = None
    evidence_span: str = ""
    target_ref: str = ""
    parameter_name: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_turn_plan(user_message: str) -> TurnPlan:
    """Classify one user request into a small execution policy."""
    text = user_message.lower()
    required_actions = set(parse_required_actions(user_message))
    preview = _contains_any(text, _PREVIEW_PHRASES)
    explicit_apply_after_preview = preview and _explicit_apply_after_preview(text)
    explicit_insertion_tool = _explicit_insertion_tool(text)
    explicit_change_graph_insert = _looks_like_change_graph_insert_request(text)
    if _looks_like_block_uid_mutation(text):
        return TurnPlan(
            intent=INTENT_UNCERTAIN_MUTATION,
            allowed_tools=UNCERTAIN_MUTATION_TOOLS,
            requires_clarification=True,
            expected_op_types=(),
            required_actions=(),
            unsupported_reason="block_uid_read_only",
            evidence_span="block_uid",
        )
    if explicit_insertion_tool:
        return TurnPlan(
            intent=INTENT_INSERTION,
            allowed_tools=(explicit_insertion_tool,),
            required_actions=(explicit_insertion_tool,),
            evidence_span=explicit_insertion_tool,
        )
    if explicit_change_graph_insert:
        return TurnPlan(
            intent=INTENT_INSERTION,
            allowed_tools=("change_graph",),
            required_actions=("change_graph",),
            expected_op_types=("insert_block_on_connection",),
            evidence_span="change_graph insert_block",
        )
    if "summarize" in text and not preview:
        required_actions.add("summarize_graph")
    if _context_requested(text):
        required_actions.add("get_grc_context")

    load_phrase = _load_phrase(text)
    if load_phrase:
        required_actions.add("load_grc")
        if _session_search_requested(text):
            required_actions.add("search_grc")
        return TurnPlan(
            intent=INTENT_LOAD,
            allowed_tools=_ordered_tools(required_actions),
            required_actions=_ordered_tools(required_actions),
            evidence_span=load_phrase,
        )

    state_phrase = _first_phrase(text, _STATE_EDIT_PHRASES)
    remove_phrase = _first_phrase(text, _REMOVE_PHRASES)
    block_removal = bool(remove_phrase and _looks_like_block_removal(text))
    if _looks_like_exact_rewire(text):
        required_actions.discard("apply_edit")
        required_actions.discard("propose_edit")
        required_actions.add("rewire_connection")
        return _plan(
            intent=INTENT_REWIRE,
            allowed_tools=_ordered_tools(("rewire_connection", *required_actions)),
            expected_op_types=("remove_connection", "add_connection"),
            required_actions=required_actions,
            evidence_span="rewire",
        )
    if _looks_like_connection_disconnect(text):
        mutation_tool = MUTATION_TOOL_BY_PREVIEW[preview]
        required_actions.discard("apply_edit")
        required_actions.discard("propose_edit")
        primary_tool = mutation_tool if preview else "remove_connection"
        required_actions.add(primary_tool)
        return _plan(
            intent=INTENT_DISCONNECT,
            allowed_tools=_ordered_tools((primary_tool, *required_actions)),
            expected_op_types=("remove_connection",),
            required_actions=required_actions,
            evidence_span="disconnect",
        )

    if state_phrase and block_removal and state_phrase not in {"disabled", "enabled"}:
        return TurnPlan(
            intent=INTENT_AMBIGUOUS,
            allowed_tools=(),
            requires_clarification=True,
            evidence_span=f"{state_phrase}; {remove_phrase}",
        )

    if remove_phrase and _looks_like_block_removal(text):
        mutation_tool = MUTATION_TOOL_BY_PREVIEW[preview]
        required_actions.discard("apply_edit")
        required_actions.discard("propose_edit")
        required_actions.add(mutation_tool)
        if explicit_apply_after_preview:
            required_actions.add("apply_edit")
        return _plan(
            intent=INTENT_REMOVE_BLOCK,
            allowed_tools=_ordered_tools((mutation_tool, *required_actions)),
            expected_op_types=(),
            required_actions=required_actions,
            evidence_span=remove_phrase,
        )

    if state_phrase and not _looks_like_disable_flag_edit(text):
        mutation_tool = MUTATION_TOOL_BY_PREVIEW[preview]
        required_actions.discard("apply_edit")
        required_actions.discard("propose_edit")
        required_actions.add(mutation_tool)
        if explicit_apply_after_preview:
            required_actions.add("apply_edit")
        return _plan(
            intent=INTENT_STATE_EDIT,
            allowed_tools=_ordered_tools((mutation_tool, *required_actions)),
            expected_op_types=("update_states",),
            required_actions=required_actions,
            evidence_span=state_phrase,
        )

    add_variable_phrase = _add_variable_phrase(text)
    if add_variable_phrase:
        mutation_tool = MUTATION_TOOL_BY_PREVIEW[preview]
        required_actions.discard("apply_edit")
        required_actions.discard("propose_edit")
        required_actions.add(mutation_tool)
        if explicit_apply_after_preview:
            required_actions.add("apply_edit")
        return _plan(
            intent=INTENT_ADD_VARIABLE,
            allowed_tools=_ordered_tools((mutation_tool, *required_actions)),
            expected_op_types=("add_block",),
            required_actions=required_actions,
            evidence_span=add_variable_phrase,
        )

    natural_insertion_phrase = _natural_insertion_phrase(text)
    if natural_insertion_phrase:
        if _has_insertion_anchor_hint(text):
            return TurnPlan(
                intent=INTENT_INSERTION,
                allowed_tools=("auto_insert_block",),
                required_actions=("auto_insert_block",),
                evidence_span=natural_insertion_phrase,
            )
        return TurnPlan(
            intent=INTENT_UNCERTAIN_MUTATION,
            allowed_tools=UNCERTAIN_MUTATION_TOOLS,
            requires_clarification=True,
            unsupported_reason="insertion_anchor_missing",
            evidence_span=natural_insertion_phrase,
        )

    if _looks_like_uncertain_mutation(text):
        return TurnPlan(
            intent=INTENT_UNCERTAIN_MUTATION,
            allowed_tools=UNCERTAIN_MUTATION_TOOLS,
            requires_clarification=True,
            expected_op_types=(),
            required_actions=(),
            unsupported_reason="uncertain_mutation",
            evidence_span=_first_phrase(text, _UNCERTAIN_MUTATION_PHRASES),
        )

    semantic_phrase = _semantic_search_phrase(text)
    if semantic_phrase:
        return TurnPlan(
            intent=INTENT_SEMANTIC_SEARCH,
            allowed_tools=("semantic_search_grc",),
            required_actions=("semantic_search_grc",),
            evidence_span=semantic_phrase,
        )

    param_phrase = _first_phrase(text, _PARAM_EDIT_PHRASES)
    if param_phrase:
        mutation_tool = MUTATION_TOOL_BY_PREVIEW[preview]
        required_actions.discard("apply_edit")
        required_actions.discard("propose_edit")
        required_actions.add(mutation_tool)
        if explicit_apply_after_preview:
            required_actions.add("apply_edit")
        return _plan(
            intent=INTENT_PARAM_EDIT if not preview else INTENT_PREVIEW,
            allowed_tools=_ordered_tools(required_actions) if preview else DEFAULT_TURN_TOOLS,
            expected_op_types=("update_params",),
            required_actions=required_actions,
            evidence_span=param_phrase,
        )

    if required_actions & {"apply_edit", "propose_edit"}:
        return TurnPlan(
            intent=INTENT_UNCERTAIN_MUTATION,
            allowed_tools=UNCERTAIN_MUTATION_TOOLS,
            expected_op_types=(),
            required_actions=(),
            requires_clarification=True,
            unsupported_reason="uncertain_mutation",
            evidence_span=_first_phrase(text, _UNCERTAIN_MUTATION_PHRASES),
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


def enrich_turn_plan_with_graph_context(
    plan: TurnPlan,
    user_message: str,
    blocks: list[Any],
) -> TurnPlan:
    """Add exact loaded target/parameter evidence when present in the prompt."""
    if plan.intent not in {INTENT_PARAM_EDIT, INTENT_PREVIEW}:
        return plan
    text = user_message.lower()
    for block in sorted(blocks, key=lambda item: len(item.instance_name), reverse=True):
        if block.instance_name.lower() not in text:
            continue
        parameters = block.params.get("parameters") if isinstance(block.params, dict) else None
        if not isinstance(parameters, dict):
            return replace(plan, target_ref=block.instance_name)
        if block.block_type == "variable" and block.instance_name.lower() in text:
            return replace(
                plan,
                target_ref=block.instance_name,
                parameter_name="value",
                allowed_tools=_narrowed_param_allowed_tools(plan),
            )
        for parameter_name in sorted(parameters, key=len, reverse=True):
            if _phrase_matches(text, str(parameter_name).lower()):
                return replace(
                    plan,
                    target_ref=block.instance_name,
                    parameter_name=str(parameter_name),
                    allowed_tools=_narrowed_param_allowed_tools(plan),
                )
        return replace(plan, target_ref=block.instance_name)
    return plan


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


def _load_phrase(text: str) -> str:
    phrase = _first_phrase(text, _LOAD_PHRASES)
    if not phrase:
        return ""
    if "graph" in text or "flowgraph" in text or ".grc" in text or "/" in text:
        return phrase
    return ""


def _session_search_requested(text: str) -> bool:
    return bool(_first_phrase(text, _SESSION_SEARCH_PHRASES))


def _context_requested(text: str) -> bool:
    if not (
        "show me" in text
        or "what uses" in text
        or "around" in text
        or "context" in text
    ):
        return False
    return "block" in text or "_0" in text or "variable" in text or "samp_rate" in text


def _first_phrase(text: str, phrases: tuple[str, ...]) -> str:
    for phrase in phrases:
        if _phrase_matches(text, phrase):
            return phrase
    return ""


def _phrase_matches(text: str, phrase: str) -> bool:
    if " " in phrase or "-" in phrase:
        return phrase in text
    return re.search(rf"\b{re.escape(phrase)}\b", text) is not None


def _explicit_apply_after_preview(text: str) -> bool:
    """Return True only for affirmative apply/applying mentions.

    Preview prompts often say "do not apply" or "without applying"; those words
    must not become a required `apply_edit` continuation.
    """
    for match in re.finditer(r"\bapply(?:ing)?\b", text):
        prefix = text[max(0, match.start() - 48):match.start()].strip()
        if re.search(r"(?:do\s+not|don't|dont|never|without|before|not|no)(?:\s+\w+){0,2}\s*$", prefix):
            continue
        return True
    return False


def _looks_like_disable_flag_edit(text: str) -> bool:
    return "disable flag" in text or "disabled flag" in text


def _looks_like_block_removal(text: str) -> bool:
    if "do not remove" in text or "don't remove" in text:
        return False
    return "block" in text or "_0" in text or "variable" in text


def _add_variable_phrase(text: str) -> str:
    for phrase in (
        "add variable",
        "add a variable",
        "add the variable",
        "create variable",
        "create a variable",
        "create the variable",
    ):
        if phrase in text:
            return phrase
    return ""


def _explicit_insertion_tool(text: str) -> str:
    for tool_name in (
        "auto_insert_block",
        "insert_block_on_connection",
        "suggest_compatible_insertions",
    ):
        if _phrase_matches(text, tool_name):
            return tool_name
    return ""


def _looks_like_change_graph_insert_request(text: str) -> bool:
    if not _phrase_matches(text, "change_graph"):
        return False
    return (
        _phrase_matches(text, "insert_block")
        or _phrase_matches(text, "operation_kind insert_block")
        or _phrase_matches(text, '"operation_kind": "insert_block"')
    )


def _natural_insertion_phrase(text: str) -> str:
    for verb in _INSERTION_VERBS:
        if not _phrase_matches(text, verb):
            continue
        for target in _INSERTION_TARGET_WORDS:
            if _phrase_matches(text, target):
                return f"{verb} {target}"
    return ""


def _has_insertion_anchor_hint(text: str) -> bool:
    return _contains_any(text, _INSERTION_ANCHOR_HINTS)


def _looks_like_uncertain_mutation(text: str) -> bool:
    return bool(_first_phrase(text, _UNCERTAIN_MUTATION_PHRASES))


def _looks_like_block_uid_mutation(text: str) -> bool:
    if "block_uid" not in text:
        return False
    return _contains_any(
        text,
        (
            "mutate",
            "change",
            "set",
            "update",
            "edit",
            "disable",
            "enable",
            "remove",
            "delete",
            "by block_uid",
            "use the block_uid",
            "use block_uid",
        ),
    )


def _looks_like_connection_disconnect(text: str) -> bool:
    has_disconnect_verb = (
        "disconnect" in text
        or "unwire" in text
        or "remove connection" in text
        or "delete connection" in text
        or "remove the exact connection_id" in text
        or "delete the exact connection_id" in text
    )
    if not has_disconnect_verb:
        return False
    return (
        "->" in text
        or "connection_id" in text
        or (" from " in text and " to " in text)
        or (" from " in text and "output" in text and "input" in text)
    )


def _looks_like_exact_rewire(text: str) -> bool:
    has_rewire_verb = _contains_any(
        text,
        ("rewire", "move connection", "move the connection", "move the old connection"),
    )
    if not has_rewire_verb:
        return False
    if (
        "rewire_connection" in text
        and ("old_connection_id" in text or "old_src_" in text)
        and "new_" in text
    ):
        return True
    if " to " not in text:
        return False
    # Exact connection_id rewires may provide either a full new edge or bounded
    # endpoint hints; the rewire wrapper resolves/clarifies, never first-picks.
    if "connection_id" in text and "->" in text:
        return _has_bounded_new_rewire_hint(text)
    # Old endpoint ambiguity is safe only when the new endpoint is exact enough
    # for runtime to build executable clarification options.
    return (
        (" from " in text or "old connection" in text)
        and "port" in text
        and "endpoint" in text
        and "->" in text
    )


def _has_bounded_new_rewire_hint(text: str) -> bool:
    to_index = text.find(" to ")
    if to_index < 0:
        return False
    suffix = text[to_index + 4 :]
    return (
        "->" in suffix
        or "endpoint" in suffix
        or "destination" in suffix
        or "source" in suffix
        or "port" in suffix
    )


def _semantic_search_phrase(text: str) -> str:
    for phrase in (
        "semantic search",
        "semantically search",
        "meaning-based search",
        "similar block",
        "similar docs",
        "similar documentation",
    ):
        if phrase in text:
            return phrase
    return ""


def _ordered_tools(tool_names: set[str] | tuple[str, ...]) -> tuple[str, ...]:
    requested = set(tool_names)
    return tuple(name for name in PUBLIC_TOOL_NAMES if name in requested)


def _narrowed_param_allowed_tools(plan: TurnPlan) -> tuple[str, ...]:
    if plan.required_actions:
        tools = list(_ordered_tools(set(plan.required_actions)))
        if "propose_edit" in tools and "apply_edit" in tools:
            tools.remove("propose_edit")
            tools.insert(tools.index("apply_edit"), "propose_edit")
        return tuple(tools)
    return plan.allowed_tools
