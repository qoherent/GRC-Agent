"""Generic turn-completion guard for explicit user-requested tool actions.

Builds a small required-action checklist from user wording at the start of
each turn.  If the model emits final text while required actions remain, one
neutral continuation nudge is injected.  The guard never decides graph design,
never rewrites transactions, and never auto-calls tools.

This module is owned and called through ``GrcAgent``, not directly by the
transport adapter.
"""

from __future__ import annotations

_PREVIEW_PHRASES: tuple[str, ...] = (
    "preview",
    "before changing",
    "what would happen",
)

_EDIT_KEYWORDS: tuple[str, ...] = (
    "edit",
    "change",
    "update",
    "remove",
    "add",
)

_VALIDATE_KEYWORDS: tuple[str, ...] = (
    "validate",
    "check",
)

_SAVE_PHRASES: tuple[str, ...] = (
    "save",
    "write it out",
    "write a copy",
    "copy to path",
)

_SUMMARY_KEYWORDS: tuple[str, ...] = (
    "summary",
    "overview",
    "describe graph",
)

_NEGATION_PREFIXES: tuple[str, ...] = (
    "not",
    "don't",
    "do not",
    "doesn't",
    "how to",
    "how do",
    "how can",
)


def _keyword_is_negated(text: str, keyword: str) -> bool:
    """Return True if *keyword* is negated or questioned in *text*."""
    lower = text.lower()
    idx = lower.find(keyword)
    if idx < 0:
        return False
    prefix = lower[:idx].rstrip()
    return any(prefix.endswith(cue) for cue in _NEGATION_PREFIXES)


def _any_affirmative(text: str, keywords: tuple[str, ...]) -> bool:
    """Return True if any keyword matches in an affirmative (non-negated) context."""
    for kw in keywords:
        if kw in text and not _keyword_is_negated(text, kw):
            return True
    return False


def parse_required_actions(user_message: str) -> set[str]:
    """Return the set of tool names implied by explicit user wording.

    Only matches the supported generic keyword-to-tool mappings.
    Returns an empty set when no action keywords are found.
    """
    text = user_message.lower()
    required: set[str] = set()

    has_preview = any(phrase in text for phrase in _PREVIEW_PHRASES)

    if has_preview:
        required.add("propose_edit")
    else:
        if _any_affirmative(text, _EDIT_KEYWORDS):
            required.add("apply_edit")

    if _any_affirmative(text, _VALIDATE_KEYWORDS):
        required.add("validate_graph")

    if _any_affirmative(text, _SAVE_PHRASES):
        required.add("save_graph")

    if _any_affirmative(text, _SUMMARY_KEYWORDS):
        required.add("summarize_graph")

    return required


def build_continuation_prompt(remaining: set[str]) -> str:
    """Build a neutral continuation nudge listing the remaining required tools."""
    tools = ", ".join(sorted(remaining))
    return (
        "The user requested additional supported tool actions that are not "
        f"complete yet: {tools}. Call the remaining required tool(s) before answering."
    )
