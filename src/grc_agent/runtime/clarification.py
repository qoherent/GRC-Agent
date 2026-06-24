"""Structured MCQ clarification contract, state, and payload builders.

Consolidated from clarification.py + clarification_state.py + clarification_payloads.py.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ClarificationOption:
    """One MCQ option backed by a real executable candidate."""

    label: str
    title: str
    description: str
    tool_name: str
    tool_args: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CustomClarificationOption:
    """The fallback D option — free text."""

    label: str = "D"
    title: str = "Other / custom"
    free_text: bool = True


@dataclass
class ClarificationRequest:
    """Encapsulation of a pending clarification returned by a tool."""

    kind: str
    question: str
    options: list[ClarificationOption]
    custom_option: CustomClarificationOption = field(
        default_factory=lambda: CustomClarificationOption()
    )
    clarification_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state_revision: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Public payload shape exposed to callers and history."""
        payload = {
            "clarification_required": True,
            "clarification_id": self.clarification_id,
            "kind": self.kind,
            "question": self.question,
            "options": [
                {
                    "label": o.label,
                    "title": o.title,
                    "description": o.description,
                    "tool_name": o.tool_name,
                    "tool_args": o.tool_args,
                    "metadata": o.metadata,
                }
                for o in self.options
            ],
            "custom_option": {
                "label": self.custom_option.label,
                "title": self.custom_option.title,
                "free_text": self.custom_option.free_text,
            },
        }
        if self.state_revision is not None:
            payload["state_revision"] = self.state_revision
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ClarificationRequest:
        """Rehydrate from a dict stored in history or pending state."""
        opts = payload.get("options", [])
        options = [
            ClarificationOption(
                label=o["label"],
                title=o["title"],
                description=o["description"],
                tool_name=o["tool_name"],
                tool_args=o["tool_args"],
                metadata=o.get("metadata", {}),
            )
            for o in opts
        ]
        custom = payload.get("custom_option", {})
        return cls(
            kind=payload.get("kind", ""),
            question=payload.get("question", ""),
            options=options,
            custom_option=CustomClarificationOption(
                label=custom.get("label", "D"),
                title=custom.get("title", "Other / custom"),
                free_text=custom.get("free_text", True),
            ),
            clarification_id=payload.get("clarification_id", ""),
            state_revision=payload.get("state_revision"),
        )



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
                f"Valid options: {', '.join(o.label for o in req.options)}. "
                f"Custom reply: {req.custom_option.label} or free text."
            ),
        }

    # Uniform word-boundary search: if the reply contains any valid option
    # label as a standalone word, select that option. Handles natural-
    # language replies like "Option A", "The answer is B", "I'll go with C".
    # One rule applied to every reply — no magic-length heuristic.
    import re

    option_by_label = {opt.label.upper(): opt for opt in req.options}
    upper_raw = raw.upper()
    for label, opt in option_by_label.items():
        if re.search(rf"\b{re.escape(label)}\b", upper_raw):
            return {
                "mode": "selected",
                "raw_reply": raw,
                "option": opt,
            }

    # No option label found in the reply → treat as custom free text.
    custom_label = req.custom_option.label.upper()
    custom_selected = parse_clarification_option_label(raw, labels={custom_label})
    if custom_selected == custom_label or raw.strip():
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


# -- payloads (was clarification_payloads.py) --


def connection_clarification_payload(
    agent: Any,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    labels = ("A", "B", "C")
    options: list[ClarificationOption] = []
    revision = agent.session.state_revision
    for label, candidate in zip(labels, candidates, strict=False):
        connection_id = candidate["connection_id"]
        options.append(
            ClarificationOption(
                label=label,
                title=connection_id,
                description=(
                    f"{candidate['src_block']}:{candidate['src_port']} -> "
                    f"{candidate['dst_block']}:{candidate['dst_port']}"
                ),
                tool_name="remove_connection",
                tool_args={"connection_id": connection_id},
                metadata={
                    "state_revision": revision,
                    "connection_id": connection_id,
                },
            )
        )
    request = ClarificationRequest(
        kind="connection_disambiguation",
        question="Multiple existing connections match the provided endpoints.",
        options=options,
        state_revision=revision,
    )
    payload = request.to_dict()
    payload.update(
        {
            "ok": False,
            "message": "Multiple existing connections match the provided endpoints.",
            "error_type": "ambiguous_connection",
        }
    )
    return payload


