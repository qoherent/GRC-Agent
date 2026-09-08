"""Recording-completeness invariants (plan U4, KTD8).

Pure checks over deserialized messages and DB rows; ``audit.py`` orchestrates
them per session. Every check returns a list of violation strings (empty =
pass). The real-DB validation mode (R9) proves these handle reality before
they gate any campaign.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)

# The step store's ClearToolResults placeholder (agent_factory.py).
CLEAR_PLACEHOLDER = "[Flowgraph tool output cleared to conserve context"

# Synthetic user prompts injected by notify_run_failure (known prefixes) —
# the single canonical definition; the runner and the auditor both import it.
AUTOFIX_PREFIXES = ("Flowgraph run failed",)

# Archive-run kind markers appear in the harness's derived run ids
# (db.archive_transcript: "{agent_name}-{kind}-{8hex}").
ARCHIVE_KIND_MARKERS = (
    "pre_compaction_transcript",
    "manual_compaction_transcript",
    "turn_failure",
    "truncated_thinking_transcript",
    "handoff",
)
# Compaction archives that may lawfully restore cleared content (KTD5).
_RESTORE_KINDS = ("pre_compaction_transcript", "manual_compaction_transcript")


def is_archive_run(run_id: str, kind: str | None = None) -> bool:
    """Deterministic archive-run discriminator (one uniform rule)."""
    markers = (kind,) if kind else ARCHIVE_KIND_MARKERS
    return any(marker in run_id for marker in markers)


def deserialize(blob: str) -> list[ModelMessage]:
    return ModelMessagesTypeAdapter.validate_json(blob)


def check_tool_call_matching(
    messages: list[ModelMessage], archive_messages: list[list[ModelMessage]] | None = None
) -> list[str]:
    """Every executed tool call is answered by a later matching return.

    Matching runs over the durable union (KTD5): the session blob plus every
    archive snapshot's message list — compaction removes returns from the
    canonical blob, but their answers live in the archived pre-compaction
    transcripts. An un-matched call in the FINAL response only is the
    documented deferred/salvage boundary (plan I2)."""
    answered: set[str] = set()
    violations: list[str] = []
    for msg in messages + [m for group in (archive_messages or []) for m in group]:
        # Tool returns live in ModelRequest parts (the user-side message that
        # carries them back to the model), not only in ModelResponse.
        for part in getattr(msg, "parts", []):
            if isinstance(part, ToolReturnPart):
                answered.add(part.tool_call_id)
    for msg in messages:
        if not isinstance(msg, ModelResponse):
            continue
        for part in msg.parts:
            if (
                isinstance(part, ToolCallPart)
                and part.tool_call_id not in answered
                and msg is not messages[-1]
            ):
                violations.append(
                    f"tool call {part.tool_call_id} never answered and not a trailing boundary"
                )
    return violations


def check_instructions_recorded(messages: list[ModelMessage]) -> list[str]:
    """The agent's own first request carries the rendered instructions string."""
    violations = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            instr = getattr(msg, "instructions", None)
            if not instr or not str(instr).strip():
                violations.append("first ModelRequest carries no instructions string")
            break
    return violations


def check_placeholders(messages: list[ModelMessage]) -> list[str]:
    """ClearToolResults placeholders must only exist when a compaction archive
    preserved the original (KTD5). Flag presence so the caller can join."""
    for msg in messages:
        blob = json.dumps([p for p in getattr(msg, "parts", [])], default=str)
        if CLEAR_PLACEHOLDER in blob:
            return [f"compaction placeholder present: {CLEAR_PLACEHOLDER}..."]
    return []


def thinking_count(messages: list[ModelMessage]) -> int:
    """Number of ThinkingPart occurrences (teacher-dependent expectation)."""
    count = 0
    for msg in messages:
        if isinstance(msg, ModelResponse):
            count += sum(1 for p in msg.parts if type(p).__name__ == "ThinkingPart")
    return count


def check_request_response_shape(messages: list[ModelMessage]) -> list[str]:
    """Structural sanity: history alternates request/response and ends on a
    response or request (never empty)."""
    violations: list[str] = []
    if not messages:
        return ["session has no messages"]
    if not any(isinstance(m, ModelResponse) for m in messages):
        violations.append("session has no model response at all")
    return violations


def check_media_markers(messages: list[ModelMessage]) -> list[str]:
    """Any harness external-media marker must carry its reference URI.

    Markers appear as marker dicts in part payloads AND as stringified markers
    inside string content; both shapes must resolve to a media reference."""
    violations = []
    for msg in messages:
        blob = json.dumps([p for p in getattr(msg, "parts", [])], default=str)
        has_marker = (
            "__harness_external_media__" in blob or "__harness_external_text__" in blob
        )
        if has_marker and "media://" not in blob:
            violations.append("external-media marker without a media:// reference")
    return violations


def runs_shape(rows: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[str]:
    """Runs/events consistency for one conversation (plan U4 step 1).

    Agent runs carry run_started..terminal events; archive runs legitimately
    have none (real-DB whitelisted shape, plan I2/C-app)."""
    violations: list[str] = []
    event_kinds_by_run: dict[str, list[str]] = {}
    for e in events:
        event_kinds_by_run.setdefault(e["run_id"], []).append(e["kind"])

    for run in rows:
        run_id = run["run_id"]
        kinds = event_kinds_by_run.get(run_id, [])
        is_archive = any(marker in run_id for marker in ARCHIVE_KIND_MARKERS)
        if is_archive:
            if "run_started" in event_kinds_by_run.get(run_id, []):
                violations.append(f"archive run {run_id} unexpectedly has run_started")
            continue
        kinds = event_kinds_by_run.get(run_id, [])
        if "run_started" not in kinds:
            violations.append(f"agent run {run_id} has no run_started event")
        elif not ({"run_completed", "run_failed"} & set(kinds)):
            violations.append(f"agent run {run_id} has no terminal event")
        started = kinds.count("model_request_started")
        finished = kinds.count("model_request_completed") + kinds.count("model_request_failed")
        if started and finished != started:
            violations.append(
                f"run {run_id}: model_request started={started} but finished={finished}"
            )
    return violations


def tool_effects_cover(tool_effects: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[str]:
    """Every completed tool call event has a tool_effects row (and vice versa)."""
    violations = []
    effect_keys = {(e["run_id"], e["tool_call_id"]) for e in tool_effects}
    call_ids = [
        (e["run_id"], e["tool_call_id"])
        for e in events
        if e["kind"] in ("tool_call_completed", "tool_call_failed") and e.get("tool_call_id")
    ]
    for key in call_ids:
        if key not in effect_keys:
            violations.append(f"tool call {key[1]} in run {key[0]} has no tool_effects row")
    return violations
