"""Pending clarification state mechanics."""

from __future__ import annotations

import copy
from typing import Any

from grc_agent.runtime.clarification import ClarificationRequest


def parse_clarification_option_label(raw: str, *, labels: set[str]) -> str | None:
    token = raw.strip().upper()
    if not token:
        return None
    if len(token) == 2 and token[1] in ").":
        token = token[0]
    if len(token) == 1 and token in {label.upper() for label in labels}:
        return token
    return None


def pending_clarification_reminder(pending_clarification: dict[str, Any] | None) -> str:
    if pending_clarification is None:
        return ""
    opts = pending_clarification.get("options", [])
    lines = ["A pending choice requires your response:"]
    for option in opts:
        lines.append(f"  {option['label']}) {option['title']}: {option['description']}")
    lines.append("  D) Other / custom (free text)")
    return "\n".join(lines)


def normalize_pending_clarification(
    payload: dict[str, Any],
    *,
    current_state_revision: int,
) -> tuple[dict[str, Any], int]:
    """Copy and normalize a tool clarification payload for later resolution."""
    stored = copy.deepcopy(payload)
    revision = stored.get("state_revision")
    if not isinstance(revision, int):
        revision = current_state_revision
        stored["state_revision"] = revision
    for option in stored.get("options", []):
        if not isinstance(option, dict):
            continue
        metadata = option.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            option["metadata"] = metadata
        metadata.setdefault("state_revision", revision)
    return stored, revision


def resolve_pending_clarification_state(
    *,
    pending_clarification: dict[str, Any] | None,
    pending_revision: int | None,
    current_state_revision: int,
    user_message: str,
) -> dict[str, Any]:
    """Resolve user text against pending clarification without executing tools."""
    if pending_clarification is None:
        return {"mode": "none"}

    if pending_revision is not None and current_state_revision != pending_revision:
        return {
            "mode": "expired",
            "clear_pending": True,
            "text": "The pending question is no longer valid because the graph has changed.",
        }

    raw = user_message.strip()
    if not raw:
        return {
            "mode": "reminder",
            "text": pending_clarification_reminder(pending_clarification),
        }

    req = ClarificationRequest.from_dict(pending_clarification)
    selected_label = parse_clarification_option_label(
        raw,
        labels={opt.label for opt in req.options},
    )

    if selected_label is not None:
        for opt in req.options:
            if opt.label.upper() == selected_label:
                return {
                    "mode": "selected",
                    "raw_reply": raw,
                    "option": opt,
                }

    label_reply = parse_clarification_option_label(
        raw,
        labels={"A", "B", "C"},
    )
    if label_reply is not None:
        return {
            "mode": "reminder",
            "text": (
                f"'{label_reply}' is not a valid option. "
                f"Choose one of: {', '.join(o.label for o in req.options)}. "
                f"Or use D / free text to describe what you want instead."
            ),
        }

    custom_label = req.custom_option.label.upper()
    custom_selected = parse_clarification_option_label(raw, labels={custom_label})
    if custom_selected == custom_label or len(raw) > 1:
        return {
            "mode": "custom",
            "clear_pending": True,
            "text": "Continuing with custom request.",
            "custom_hint": raw,
        }

    return {
        "mode": "reminder",
        "text": pending_clarification_reminder(pending_clarification),
    }
