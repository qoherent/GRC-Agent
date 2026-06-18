"""Structured MCQ clarification contract, state, and payload builders.

Consolidated from clarification.py + clarification_state.py + clarification_payloads.py.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any

from grc_agent.session_ops import connection_id as render_connection_id


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
        _MAX_PARAMS_PREVIEW = 3
        params_items = list(params.items())[:_MAX_PARAMS_PREVIEW]
        params_summary = ", ".join(f"{k}={v!r}" for k, v in params_items)
        if len(params) > _MAX_PARAMS_PREVIEW:
            from grc_agent.runtime.text_utils import format_truncation_flag
            params_summary += format_truncation_flag(
                "params_preview", len(params), _MAX_PARAMS_PREVIEW, unit="items"
            )
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
    lines.append("Reply format: A/B/C/D or free text.")
    return "\n".join(lines)


# -- state (was clarification_state.py) --


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


def rewire_new_endpoint_clarification_payload(
    agent: Any,
    *,
    old_connection_id: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    revision = agent.session.state_revision
    options: list[ClarificationOption] = []
    for label, candidate in zip(("A", "B", "C"), candidates, strict=False):
        new_connection_id = render_connection_id(
            candidate["new_src_block"],
            candidate["new_src_port"],
            candidate["new_dst_block"],
            candidate["new_dst_port"],
        )
        options.append(
            ClarificationOption(
                label=label,
                title=new_connection_id,
                description=f"replace {old_connection_id} with {new_connection_id}",
                tool_name="rewire_connection",
                tool_args={
                    "old_connection_id": old_connection_id,
                    "new_src_block": candidate["new_src_block"],
                    "new_src_port": candidate["new_src_port"],
                    "new_dst_block": candidate["new_dst_block"],
                    "new_dst_port": candidate["new_dst_port"],
                },
                metadata={
                    "state_revision": revision,
                    "old_connection_id": old_connection_id,
                    "new_connection_id": new_connection_id,
                },
            )
        )
    request = ClarificationRequest(
        kind="rewire_new_endpoint_disambiguation",
        question="Multiple executable new endpoints match the provided hints.",
        options=options,
        state_revision=revision,
    )
    payload = request.to_dict()
    payload.update(
        {
            "ok": False,
            "message": "Multiple executable new endpoints match the provided hints.",
            "error_type": "ambiguous_rewire_endpoint",
        }
    )
    return payload


def rewire_clarification_payload(
    agent: Any,
    candidates: list[dict[str, Any]],
    *,
    new_src_block: str,
    new_src_port: int | str | None,
    new_dst_block: str,
    new_dst_port: int | str | None,
) -> dict[str, Any]:
    revision = agent.session.state_revision
    options: list[ClarificationOption] = []
    for label, candidate in zip(("A", "B", "C"), candidates, strict=False):
        old_connection_id = candidate["connection_id"]
        options.append(
            ClarificationOption(
                label=label,
                title=old_connection_id,
                description=(
                    f"replace {candidate['src_block']}:{candidate['src_port']} -> "
                    f"{candidate['dst_block']}:{candidate['dst_port']} with "
                    f"{new_src_block}:{new_src_port} -> {new_dst_block}:{new_dst_port}"
                ),
                tool_name="rewire_connection",
                tool_args={
                    "old_connection_id": old_connection_id,
                    "new_src_block": new_src_block,
                    "new_src_port": new_src_port,
                    "new_dst_block": new_dst_block,
                    "new_dst_port": new_dst_port,
                },
                metadata={
                    "state_revision": revision,
                    "old_connection_id": old_connection_id,
                },
            )
        )
    request = ClarificationRequest(
        kind="rewire_connection_disambiguation",
        question="Multiple old connections match the provided endpoint hints.",
        options=options,
        state_revision=revision,
    )
    payload = request.to_dict()
    payload.update(
        {
            "ok": False,
            "message": "Multiple old connections match the provided endpoint hints.",
            "error_type": "ambiguous_connection",
        }
    )
    return payload


def duplicate_block_clarification_payload(
    agent: Any,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    errors = payload.get("errors")
    operations = payload.get("normalized_operations")
    if not isinstance(errors, list) or not isinstance(operations, list):
        return None

    duplicate_errors = [
        error
        for error in errors
        if isinstance(error, dict)
        and error.get("code") == "block_name_not_unique"
        and isinstance(error.get("op_index"), int)
    ]
    if len(duplicate_errors) != 1 or len(operations) != 1:
        return None

    op_index = duplicate_errors[0]["op_index"]
    if op_index < 0 or op_index >= len(operations):
        return None
    operation = operations[op_index]
    if not isinstance(operation, dict):
        return None
    if operation.get("op_type") not in {"update_params", "update_states", "remove_block"}:
        return None
    if "block_type" in operation:
        return None
    instance_name = operation.get("instance_name")
    if not isinstance(instance_name, str) or not instance_name:
        return None

    resolved = agent.session.resolve_block_reference(instance_name)
    candidates = resolved.get("candidates", [])
    if not isinstance(candidates, list) or len(candidates) < 2 or len(candidates) > 3:
        return None

    block_types = [
        candidate.get("block_type")
        for candidate in candidates
        if isinstance(candidate, dict) and isinstance(candidate.get("block_type"), str)
    ]
    if len(block_types) != len(candidates):
        return None
    block_types_are_unique = len(set(block_types)) == len(block_types)

    revision = agent.session.state_revision
    options: list[ClarificationOption] = []
    for label, candidate in zip(("A", "B", "C"), candidates, strict=False):
        block_type = candidate["block_type"]
        transaction = copy.deepcopy(operation)
        if block_types_are_unique:
            transaction["block_type"] = block_type
        options.append(
            ClarificationOption(
                label=label,
                title=f"{instance_name} ({block_type})",
                description=(
                    f"state={candidate.get('state')}; "
                    f"coordinate={candidate.get('coordinate')}"
                ),
                tool_name="apply_edit",
                tool_args={"transaction": transaction},
                metadata={
                    "state_revision": revision,
                    "block_uid": candidate.get("block_uid"),
                    "block_type": block_type,
                },
            )
        )

    request = ClarificationRequest(
        kind="block_disambiguation",
        question=f"Multiple blocks match the requested instance_name `{instance_name}`.",
        options=options,
        state_revision=revision,
    )
    clarification = request.to_dict()
    clarification.update(
        {
            "ok": False,
            "message": "Multiple blocks match the requested instance_name.",
            "error_type": "ambiguous_block",
            "errors": copy.deepcopy(errors),
            "normalized_operations": copy.deepcopy(operations),
        }
    )
    return clarification
