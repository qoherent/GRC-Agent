"""Structured MCQ clarification contract for ambiguous tool results.

No raw YAML. No prompt-specific wording. Options are always backed by real
candidates with executable tool arguments."""

from __future__ import annotations

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

    def to_dict(self) -> dict[str, Any]:
        """Public payload shape exposed to callers and history."""
        return {
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

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ClarificationRequest":
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
        )


@dataclass
class ClarificationResolution:
    """Result of matching a user reply against a pending ClarificationRequest."""

    mode: str  # "none" | "execute" | "custom" | "expired" | "reminder"
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    text: str | None = None
    clarification_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "text": self.text,
            "clarification_id": self.clarification_id,
        }


def render_clarification_prompt(payload: dict[str, Any]) -> str:
    """Render a stored clarification_required payload into a concise human MCQ.

    Rules:
    - No raw JSON dump.
    - Include block_type and connection_id per option.
    - Include short params summary.
    - Include D custom option.
    - No mutation occurs here.
    """
    question = payload.get("question", "Multiple valid options were found.")
    lines: list[str] = [question, ""]
    for opt in payload.get("options", []):
        label = opt.get("label", "?")
        title = opt.get("title", "Option")
        description = opt.get("description", "")
        tool_args = opt.get("tool_args", {}) or {}
        block_type = tool_args.get("block_type", "")
        conn_id = tool_args.get("connection_id", "")
        params = tool_args.get("params", {})
        params_summary = ", ".join(f"{k}={v!r}" for k, v in list(params.items())[:3])
        line = f"{label}) {title}"
        parts: list[str] = []
        if block_type:
            parts.append(f"block_type={block_type}")
        if conn_id:
            parts.append(f"connection_id={conn_id}")
        if params_summary:
            parts.append(f"params={{ {params_summary} }}")
        if description:
            parts.append(description)
        if parts:
            line += "\n   " + " | ".join(parts)
        lines.append(line)
    custom = payload.get("custom_option", {})
    custom_label = custom.get("label", "D")
    custom_title = custom.get("title", "Other / custom")
    lines.append(f"{custom_label}) {custom_title} (free text)")
    lines.append("")
    lines.append("Reply with the letter of your choice (A/B/C/D) or type free text.")
    return "\n".join(lines)
