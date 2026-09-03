"""Model-facing formatting: tool-call labels, transcript fragments, token counts.

Pure functions extracted from ``chat_sidebar.py`` — no GTK, no ``self``, so
each one is testable without a display. Shared by the streaming and history
render paths, which used to build the same strings independently and had
already drifted (the same gear glyph spelled two different ways in two
places, the same check mark a third).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai.messages import NativeToolCallPart, ToolCallPart

from ..agent import GrcAgentResponse

_SUMMARY_ACTIONS_FIELD = "actions_taken"
_SUMMARY_EXPLANATION_FIELD = "explanation"
assert {_SUMMARY_ACTIONS_FIELD, _SUMMARY_EXPLANATION_FIELD} <= set(GrcAgentResponse.model_fields), (
    "GrcAgentResponse fields changed; _parse_final_summary must be updated to match"
)

_MAX_TOOL_DISPLAY_CHARS = 8000


def _format_tool_display(text: str, max_chars: int = _MAX_TOOL_DISPLAY_CHARS) -> str:
    """Format tool argument/result text for Gtk.Expander display labels, keeping Pango bounded."""
    if len(text) <= max_chars:
        return text
    half = max_chars // 2
    return f"{text[:half]}\n\n... [truncated {len(text) - max_chars} chars] ...\n\n{text[-half:]}"


# Expander labels and plain-text transcript fragments. One definition each,
# shared by the streaming and history render paths, which previously built the
# same strings independently and had already drifted in spelling: the same gear
# was written both as "\u2699" and as a literal "⚙", the same check as
# "\u2713" and "✓", the same cross as "\u2717" and "✗".
_GEAR = "\u2699"
_CHECK = "\u2713"
_CROSS = "\u2717"
_WARN = "\u26a0"


def _tool_label(
    name: str,
    *,
    ok: bool = True,
    retry: bool = False,
    result: Any = None,
) -> str:
    """The expander title for a settled tool call."""
    label_name = name
    if name == "query_knowledge" and result is not None:
        res_str = str(result)
        if (
            '"search_mode": "lexical"' in res_str
            or "'search_mode': 'lexical'" in res_str
            or '"search_mode":"lexical"' in res_str
        ):
            label_name = f"{name} (lexical)"
        elif (
            '"search_mode": "hybrid"' in res_str
            or "'search_mode': 'hybrid'" in res_str
            or '"search_mode":"hybrid"' in res_str
        ):
            label_name = f"{name} (hybrid)"
        elif (
            '"search_mode": "vector"' in res_str
            or "'search_mode': 'vector'" in res_str
            or '"search_mode":"vector"' in res_str
        ):
            label_name = f"{name} (vector)"
    if retry:
        return f"{_WARN} {label_name} retry"
    return f"{_GEAR} {label_name} {_CHECK if ok else _CROSS}"


def _tool_label_running(name: str) -> str:
    return f"{_GEAR} {name} ..."


def _transcript_tool_call(name: str, args: str, result: str | None = None) -> str:
    """The Copy-action transcript fragment for one tool call."""
    head = f"<Tool Call: {name}>\nArgs: {args}\n"
    return head if result is None else f"{head}Result: {result}\n"


def _transcript_tool_result(result: str) -> str:
    """The standalone fallback fragment for a tool result whose call fragment
    is no longer patchable in the streaming accumulator's window. The normal
    path renders the result inside its own ``<Tool Call: ...>`` block, as the
    history path does."""
    return f"<Tool Result: {result}>\n"


_THINKING_OPEN = "<Thinking>\n"
_THINKING_CLOSE = "\n</Thinking>\n"


def _transcript_thinking(text: str) -> str:
    """The Copy-action fragment for one complete thinking part.

    One owner of the wrapped form: the history renderer and the streaming
    accumulator both build the copy transcript through it, so a reasoning
    turn copies identically mid-stream and after re-render. The canonical
    form is the wrapped one — it matches the persistent post-render copy
    users keep once the turn completes.
    """
    return f"{_THINKING_OPEN}{text}{_THINKING_CLOSE}"


def _transcript_thinking_open(text: str = "") -> str:
    """The streaming path grows a thinking region incrementally: the opener
    carries any content that arrived with the part start; deltas append raw
    and the closer lands when the part closes. Open + deltas + close is
    exactly ``_transcript_thinking``."""
    return f"{_THINKING_OPEN}{text}"


def _transcript_thinking_close() -> str:
    return _THINKING_CLOSE


def _transcript_summary(actions: list[str], explanation: str) -> str:
    return f"<Summary>\n{actions}\n{explanation}\n</Summary>\n"


def _tool_args_text(part: ToolCallPart | NativeToolCallPart) -> str:
    """The tool call's arguments as the model actually sent them.

    ``args_as_json_str()`` rather than ``str(part.args)``: the latter renders a
    dict through ``repr``, so the panel showed Python literal syntax
    (``{'k': 'v'}``) for a payload that was JSON on the wire (``{"k":"v"}``).
    """
    if not part.args:
        return ""
    return part.args_as_json_str()


def _parse_final_summary(args: Any) -> tuple[list[str], str] | None:
    """Recover the model's final structured output from a `final_result` tool call.

    The agent's output type is `[GrcAgentResponse, str]`, so a structured turn
    ends with a call to pydantic-ai's generated `final_result` tool whose args
    are the GrcAgentResponse JSON (`actions_taken` + `explanation`). Returns
    (actions, explanation) when the args carry that shape, else None — the
    caller then renders the call as an ordinary tool expander instead.

    Deliberately NOT `GrcAgentResponse.model_validate`: this also runs on
    partially-streamed args, where `explanation` has not arrived yet, and both
    strict validation and pydantic's `experimental_allow_partial` reject a
    missing required field (verified) — so the summary card would collapse back
    to a raw-JSON expander mid-stream. The keys are taken from the model's own
    field names so a rename cannot silently desync the two.
    """
    if not args:
        return None
    if isinstance(args, str):
        try:
            data = json.loads(args)
        except (ValueError, TypeError):
            return None
    elif isinstance(args, dict):
        data = args
    else:
        return None
    actions = data.get(_SUMMARY_ACTIONS_FIELD)
    explanation = data.get(_SUMMARY_EXPLANATION_FIELD)
    if not isinstance(actions, list) or not all(isinstance(a, str) for a in actions):
        return None
    if not isinstance(explanation, str):
        explanation = ""
    return actions, explanation


def format_tokens(n: int) -> str:
    """Format token count for display (e.g. 1.2k, 14.7k, 128k, 1M)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n / 1_000:.1f}k".replace(".0k", "k")
    return str(n)

