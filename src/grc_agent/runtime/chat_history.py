"""Compact a :class:`ChatHistory` against a character budget.

The native :class:`ToolAgents.data_models.chat_history.ChatHistory` does not
ship a compactor, but our runtime enforces a hard ``history_compact_budget``
so small local models do not blow past their context window. The compactor
preserves ``TextContent`` and ``ToolCallContent`` verbatim (the model needs
to see what it asked for) and shortens ``ToolCallResultContent`` payloads
(the result text is what bloats the context). ``ChatMessage`` objects are
mutated in place; ordering, role, and ids are preserved.

Truncated payloads are wrapped with a clear sentinel
``[... TRUNCATED by chat-history compactor: was {N} chars, kept {M} ...]``
so the model can tell the JSON or text was cut off and not hallucinate
missing closing brackets.
"""

from __future__ import annotations

import uuid

from ToolAgents.data_models.chat_history import ChatHistory
from ToolAgents.data_models.messages import (
    ChatMessage,
    ToolCallResultContent,
)

_TRUNCATION_SENTINEL_TEMPLATE = (
    "... [TRUNCATED by chat-history compactor: was {original} chars, "
    "kept {kept}]"
)


def _shorten_tool_result(
    content: ToolCallResultContent,
    *,
    max_chars: int,
    original_length: int | None = None,
) -> ToolCallResultContent:
    original = content.tool_call_result
    if len(original) <= max_chars:
        return content
    if original_length is None:
        original_length = len(original)
    sentinel = _TRUNCATION_SENTINEL_TEMPLATE.format(
        original=original_length, kept=max_chars - 1
    )
    budget = max(0, max_chars - len(sentinel) - 1)
    kept = original[:budget].rstrip()
    return ToolCallResultContent(
        tool_call_result_id=content.tool_call_result_id or str(uuid.uuid4()),
        tool_call_id=content.tool_call_id,
        tool_call_name=content.tool_call_name,
        tool_call_result=f"{kept} {sentinel}",
    )


def _replace_tool_result(
    message: ChatMessage,
    original: ToolCallResultContent,
    replacement: ToolCallResultContent,
) -> ChatMessage:
    new_content = []
    for item in message.content:
        if item is original:
            new_content.append(replacement)
        else:
            new_content.append(item)
    if new_content == list(message.content):
        return message
    return ChatMessage(
        id=message.id,
        role=message.role,
        content=new_content,
        created_at=message.created_at,
        updated_at=message.updated_at,
        additional_fields=message.additional_fields,
        additional_information=message.additional_information,
    )


def compact_chat_history(
    chat_history: ChatHistory,
    *,
    budget_chars: int,
    max_tool_result_chars: int = 4000,
) -> bool:
    """Shorten tool results in ``chat_history`` until the total is under budget.

    Returns ``True`` if any message was rewritten, ``False`` if the history
    already fit. The function is idempotent: calling it twice with the same
    arguments is a no-op on the second call.

    ``max_tool_result_chars`` caps any individual ``ToolCallResultContent``
    payload. The previous hard-coded cap of 800 was starving the model of
    ``query_knowledge`` results: a single GNU Radio catalog block
    definition (name, IO signature, full param list) can easily exceed
    800 chars, so the compactor was deleting the exact block ID the
    model had just retrieved. 4000 chars is roughly 1,000 tokens — large
    enough to fit a full catalog JSON object, small enough that even
    ten such payloads still fit comfortably inside the 100K-char
    ``history_compact_budget`` and the 256K-token context window of
    even the smallest local model.

    Algorithm: one pass. We split the history into an immutable
    "shell" (system message, user messages, assistant text, tool-call
    arguments) and a mutable sum of tool-result payload lengths. We
    compute the exact new payload size for each candidate by
    proportional allocation against the remaining budget, then slice
    each payload once. No iteration, no per-cycle length re-computation.
    """
    if budget_chars <= 0:
        return False
    messages = chat_history.get_messages()
    if not messages:
        return False

    floor = 64

    def _payload_length(content: ToolCallResultContent) -> int:
        return len(content.tool_call_result)

    def _shell_length(message: ChatMessage) -> int:
        # ``get_as_text`` includes both shell and payload text; the
        # shell is the full text minus the sum of payload text.
        full = len(message.get_as_text())
        payload_total = sum(
            _payload_length(c)
            for c in message.content
            if isinstance(c, ToolCallResultContent)
        )
        return full - payload_total

    # 1. Compute shell length and current payload length per candidate.
    shell_total = 0
    candidate_payloads: list[tuple[ChatMessage, ToolCallResultContent, int]] = []
    candidate_total = 0
    for message in messages:
        shell_total += _shell_length(message)
        for content in message.content:
            if (
                isinstance(content, ToolCallResultContent)
                and len(content.tool_call_result) > floor
            ):
                length = _payload_length(content)
                candidate_payloads.append((message, content, length))
                candidate_total += length

    if not candidate_payloads:
        return False

    total = shell_total + candidate_total
    if total <= budget_chars:
        return False

    # 2. The room we have for the mutable sum.
    mutable_budget = max(
        0, budget_chars - shell_total
    )
    # Floor: every payload keeps at least ``floor`` chars.
    floor_total = floor * len(candidate_payloads)
    if mutable_budget < floor_total:
        # Even at the floor we can't fit. Best effort: clamp each
        # payload to the floor. The history will exceed the budget.
        mutable_budget = floor_total

    # 3. Proportional allocation. New size of each payload is
    #    floor + share_of_remaining, where share is proportional to
    #    the current size. This keeps the relative weight of payloads
    #    stable (a 4000-char result is still ~4x a 1000-char one).
    extra = mutable_budget - floor_total
    new_sizes: list[int] = []
    if candidate_total == 0:
        new_sizes = [floor] * len(candidate_payloads)
    else:
        for _, _, current_length in candidate_payloads:
            share = int(round(extra * (current_length / candidate_total)))
            new_sizes.append(floor + share)

    # 4. Apply exactly once per candidate. Track whether anything changed.
    changed = False
    for (message, content, current_length), new_length in zip(
        candidate_payloads, new_sizes
    ):
        # Cap at max_tool_result_chars (don't *grow* a previously
        # compacted result).
        target = min(max_tool_result_chars, new_length)
        if target >= current_length:
            continue
        if target < floor:
            target = floor
        new_content = _shorten_tool_result(
            content,
            max_chars=target,
            original_length=current_length,
        )
        if new_content is content:
            continue
        # Replace the content item inside the message.
        idx_in_history = chat_history.messages.index(message)
        old_message = chat_history.messages[idx_in_history]
        new_message = _replace_tool_result(old_message, content, new_content)
        if new_message is not old_message:
            chat_history.messages[idx_in_history] = new_message
            changed = True
    return changed


__all__ = ["compact_chat_history"]


if __name__ == "__main__":
    import doctest

    doctest.testmod()
