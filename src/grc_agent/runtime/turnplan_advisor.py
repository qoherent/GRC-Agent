"""LLM advisor for coarse TurnPlan interaction-mode classification.

The advisor owns semantic intent classification for the experimental
advisor-first path. It returns only a small structured mode. This module never
creates tool calls, transactions, params payloads, save paths, or graph
mutations; runtime safety remains enforced by schemas, route gates, preflight,
grcc, rollback, and explicit save rules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import time
from typing import Any

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.runtime.turn_plan import (
    INTENT_ADD_VARIABLE,
    INTENT_AMBIGUOUS,
    INTENT_DISCONNECT,
    INTENT_INSERTION,
    INTENT_PARAM_EDIT,
    INTENT_PREVIEW,
    INTENT_REWIRE,
    INTENT_STATE_EDIT,
    INTENT_UNCERTAIN_MUTATION,
    INTENT_UNKNOWN,
    TurnPlan,
)

ADVISOR_INTENTS: tuple[str, ...] = (
    "read_only",
    "preview",
    "param_edit",
    "state_edit",
    "add_variable",
    "disconnect",
    "rewire",
    "insertion",
    "save",
    "validate",
    "ambiguous",
    "uncertain_mutation",
    "unsupported",
    "unknown",
)

ADVISOR_RISK_FLAGS: tuple[str, ...] = (
    "negated_apply",
    "save_request",
    "raw_yaml",
    "vague_topology",
    "block_uid_freeform",
    "duplicate_target",
    "missing_target",
    "missing_placement",
    "unsupported_request",
)

ADVISOR_MODES: tuple[str, ...] = (
    "read_only",
    "preview",
    "edit",
    "disconnect",
    "rewire",
    "insert",
    "validate",
    "save",
    "clarify",
    "unsupported",
)

ADVISOR_READINESS: tuple[str, ...] = (
    "ready",
    "clarify",
    "unsupported",
)

ADVISOR_DECISIONS: tuple[str, ...] = (
    "ready",
    "preview",
    "clarify",
    "unsupported",
)

ADVISOR_PERMISSIONS: tuple[str, ...] = (
    "allow_readonly",
    "allow_preview",
    "allow_mutation_narrow",
    "ask_clarification",
    "deny_unsupported",
)

_FORBIDDEN_ADVISOR_KEYS = {
    "tool",
    "tool_call",
    "tool_calls",
    "function",
    "arguments",
    "transaction",
    "transactions",
    "params",
    "parameters",
    "apply_edit",
    "propose_edit",
    "insert_tool_args",
    "old_connection_id",
    "new_src_block",
    "new_dst_block",
    "save_path",
    "repair_plan",
    "recipe",
    "yaml",
}

_MAX_REASON_CHARS = 240
_MAX_QUESTION_CHARS = 240
_MAX_TARGET_MENTIONS = 8
_MAX_TARGET_CHARS = 80
READ_ONLY_ADVISOR_TOOLS: tuple[str, ...] = (
    "summarize_graph",
    "search_grc",
    "search_manual",
    "semantic_search_grc",
    "get_grc_context",
    "describe_block",
    "validate_graph",
)


class AdvisorValidationError(ValueError):
    """Raised when advisor output violates the strict shadow schema."""


@dataclass(frozen=True)
class AdvisorResult:
    intent: str
    confidence: float
    needs_clarification: bool
    risk_flags: tuple[str, ...]
    target_mentions: tuple[str, ...]
    clarification_question: str | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["risk_flags"] = list(self.risk_flags)
        payload["target_mentions"] = list(self.target_mentions)
        return payload


@dataclass(frozen=True)
class AdvisorCandidatePlan:
    intent: str
    allowed_tools: tuple[str, ...]
    expected_op_types: tuple[str, ...]
    requires_clarification: bool
    risk_flags: tuple[str, ...]
    source: str = "advisor_shadow"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["allowed_tools"] = list(self.allowed_tools)
        payload["expected_op_types"] = list(self.expected_op_types)
        payload["risk_flags"] = list(self.risk_flags)
        return payload


@dataclass(frozen=True)
class AdvisorObservation:
    enabled: bool
    shadow_mode: bool
    parse_success: bool
    schema_valid: bool
    latency_ms: int | None
    advisor_result: AdvisorResult | None
    candidate_plan: AdvisorCandidatePlan | None
    deterministic_plan: dict[str, Any] | None
    canonicalization: dict[str, Any] | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "shadow_mode": self.shadow_mode,
            "parse_success": self.parse_success,
            "schema_valid": self.schema_valid,
            "advisor_latency_ms": self.latency_ms,
            "advisor_result": (
                self.advisor_result.as_dict() if self.advisor_result is not None else None
            ),
            "candidate_plan": (
                self.candidate_plan.as_dict() if self.candidate_plan is not None else None
            ),
            "deterministic_plan": self.deterministic_plan,
            "canonicalization": self.canonicalization,
            "error": self.error,
        }


@dataclass(frozen=True)
class AdvisorModeResult:
    mode: str

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode}


@dataclass(frozen=True)
class AdvisorReadinessModeResult:
    readiness: str
    mode: str

    def as_dict(self) -> dict[str, Any]:
        return {"readiness": self.readiness, "mode": self.mode}


@dataclass(frozen=True)
class AdvisorModeCandidate:
    mode: str
    allowed_tools: tuple[str, ...]
    requires_clarification: bool
    source: str = "advisor_v5_shadow"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["allowed_tools"] = list(self.allowed_tools)
        return payload


@dataclass(frozen=True)
class AdvisorModeObservation:
    enabled: bool
    shadow_mode: bool
    parse_success: bool
    schema_valid: bool
    latency_ms: int | None
    mode_result: AdvisorModeResult | None
    candidate_plan: AdvisorModeCandidate | None
    deterministic_plan: dict[str, Any] | None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "shadow_mode": self.shadow_mode,
            "parse_success": self.parse_success,
            "schema_valid": self.schema_valid,
            "advisor_latency_ms": self.latency_ms,
            "mode_result": self.mode_result.as_dict() if self.mode_result else None,
            "candidate_plan": self.candidate_plan.as_dict() if self.candidate_plan else None,
            "deterministic_plan": self.deterministic_plan,
            "error": self.error,
        }


@dataclass(frozen=True)
class AdvisorReadinessModeObservation:
    enabled: bool
    shadow_mode: bool
    parse_success: bool
    schema_valid: bool
    latency_ms: int | None
    result: AdvisorReadinessModeResult | None
    deterministic_plan: dict[str, Any] | None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "shadow_mode": self.shadow_mode,
            "parse_success": self.parse_success,
            "schema_valid": self.schema_valid,
            "advisor_latency_ms": self.latency_ms,
            "result": self.result.as_dict() if self.result else None,
            "deterministic_plan": self.deterministic_plan,
            "error": self.error,
        }


@dataclass(frozen=True)
class AdvisorPermissionDecision:
    advisor_mode: str
    compiled_permission: str
    compiled_mode: str
    override_applied: bool
    override_reason: str | None
    safe_to_expose_tools: bool
    allowed_tools: tuple[str, ...]
    source: str = "advisor_permission_compiler_shadow"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["allowed_tools"] = list(self.allowed_tools)
        return payload


def compact_session_summary(session: FlowgraphSession) -> dict[str, Any]:
    """Return bounded graph context for advisor classification."""
    snapshot = session.active_session_snapshot() if session.flowgraph is not None else None
    if not isinstance(snapshot, dict):
        return {"session_loaded": False}

    block_preview = snapshot.get("block_preview")
    connection_preview = snapshot.get("connection_preview")
    variable_preview = snapshot.get("variable_preview")
    return {
        "session_loaded": True,
        "dirty": bool(snapshot.get("dirty")),
        "state_revision": snapshot.get("state_revision"),
        "validation_status": (snapshot.get("validation") or {}).get("status"),
        "block_count": snapshot.get("block_count"),
        "connection_count": snapshot.get("connection_count"),
        "variable_count": snapshot.get("variable_count"),
        "block_preview": _bounded_string_list(block_preview, limit=8),
        "connection_preview": _bounded_string_list(connection_preview, limit=8),
        "variable_preview": _bounded_string_list(variable_preview, limit=8),
    }


def run_turnplan_advisor(
    *,
    client: Any,
    model: str,
    user_message: str,
    session: FlowgraphSession,
    deterministic_plan: TurnPlan | None,
    pending_clarification: dict[str, Any] | None = None,
    timeout_seconds: float | None = None,
    prompt_version: str = "v1",
) -> AdvisorObservation:
    """Call the same llama.cpp server for one shadow TurnPlan advice sample."""
    start = time.perf_counter()
    canonicalization: dict[str, Any] | None = None
    try:
        messages = build_advisor_messages(
            user_message=user_message,
            session_summary=compact_session_summary(session),
            pending_clarification=pending_clarification,
            prompt_version=prompt_version,
        )
        original_timeout = getattr(client, "timeout_seconds", None)
        if timeout_seconds is not None and hasattr(client, "timeout_seconds"):
            client.timeout_seconds = timeout_seconds
        try:
            response = client.create_chat_completion(
                model=model,
                messages=messages,
                tools=[],
                tool_choice="none",
                response_format={"type": "json_object"},
            )
        finally:
            if timeout_seconds is not None and original_timeout is not None:
                client.timeout_seconds = original_timeout
        raw_content = _extract_assistant_text(response)
        payload = parse_advisor_json(raw_content)
        if prompt_version == "v4":
            payload, canonicalization = canonicalize_advisor_payload(
                payload,
                user_message=user_message,
            )
        advisor_result = validate_advisor_payload(payload)
        if canonicalization is not None:
            canonicalization = {
                **canonicalization,
                "schema_valid_after_canonicalization": True,
            }
        candidate = compile_advisor_plan(advisor_result, user_message=user_message)
        latency = int((time.perf_counter() - start) * 1000)
        return AdvisorObservation(
            enabled=True,
            shadow_mode=True,
            parse_success=True,
            schema_valid=True,
            latency_ms=latency,
            advisor_result=advisor_result,
            candidate_plan=candidate,
            deterministic_plan=(
                deterministic_plan.as_dict() if deterministic_plan is not None else None
            ),
            canonicalization=canonicalization,
        )
    except Exception as exc:
        if canonicalization is not None:
            canonicalization = {
                **canonicalization,
                "schema_valid_after_canonicalization": False,
            }
        latency = int((time.perf_counter() - start) * 1000)
        return AdvisorObservation(
            enabled=True,
            shadow_mode=True,
            parse_success=False,
            schema_valid=False,
            latency_ms=latency,
            advisor_result=None,
            candidate_plan=None,
            deterministic_plan=(
                deterministic_plan.as_dict() if deterministic_plan is not None else None
            ),
            canonicalization=canonicalization,
            error=str(exc),
        )


def run_turnplan_mode_advisor(
    *,
    client: Any,
    model: str,
    user_message: str,
    session: FlowgraphSession,
    deterministic_plan: TurnPlan | None,
    timeout_seconds: float | None = None,
    prompt_version: str = "v5",
) -> AdvisorModeObservation:
    """Call the same llama.cpp server for one v5 shadow mode sample."""
    start = time.perf_counter()
    try:
        messages = build_mode_advisor_messages(
            user_message=user_message,
            session_summary=compact_session_summary(session),
            prompt_version=prompt_version,
        )
        original_timeout = getattr(client, "timeout_seconds", None)
        if timeout_seconds is not None and hasattr(client, "timeout_seconds"):
            client.timeout_seconds = timeout_seconds
        try:
            try:
                response = client.create_chat_completion(
                    model=model,
                    messages=messages,
                    tools=[],
                    tool_choice="none",
                    response_format=_mode_json_schema_response_format(),
                )
            except Exception:
                response = client.create_chat_completion(
                    model=model,
                    messages=messages,
                    tools=[],
                    tool_choice="none",
                    response_format={"type": "json_object"},
                )
        finally:
            if timeout_seconds is not None and original_timeout is not None:
                client.timeout_seconds = original_timeout
        payload = parse_advisor_json(_extract_assistant_text(response))
        mode_result = validate_mode_advisor_payload(payload)
        candidate = compile_mode_advisor_plan(mode_result, user_message=user_message)
        latency = int((time.perf_counter() - start) * 1000)
        return AdvisorModeObservation(
            enabled=True,
            shadow_mode=True,
            parse_success=True,
            schema_valid=True,
            latency_ms=latency,
            mode_result=mode_result,
            candidate_plan=candidate,
            deterministic_plan=(
                deterministic_plan.as_dict() if deterministic_plan is not None else None
            ),
        )
    except Exception as exc:
        latency = int((time.perf_counter() - start) * 1000)
        return AdvisorModeObservation(
            enabled=True,
            shadow_mode=True,
            parse_success=False,
            schema_valid=False,
            latency_ms=latency,
            mode_result=None,
            candidate_plan=None,
            deterministic_plan=(
                deterministic_plan.as_dict() if deterministic_plan is not None else None
            ),
            error=str(exc),
        )


def run_turnplan_readiness_mode_advisor(
    *,
    client: Any,
    model: str,
    user_message: str,
    session: FlowgraphSession,
    deterministic_plan: TurnPlan | None,
    timeout_seconds: float | None = None,
    prompt_version: str = "v9",
) -> AdvisorReadinessModeObservation:
    """Call the same llama.cpp server for one readiness+mode shadow sample."""
    start = time.perf_counter()
    try:
        messages = build_readiness_mode_advisor_messages(
            user_message=user_message,
            session_summary=compact_session_summary(session),
            prompt_version=prompt_version,
        )
        original_timeout = getattr(client, "timeout_seconds", None)
        if timeout_seconds is not None and hasattr(client, "timeout_seconds"):
            client.timeout_seconds = timeout_seconds
        try:
            try:
                response = client.create_chat_completion(
                    model=model,
                    messages=messages,
                    tools=[],
                    tool_choice="none",
                    response_format=_readiness_mode_json_schema_response_format(
                        prompt_version=prompt_version,
                    ),
                )
            except Exception:
                response = client.create_chat_completion(
                    model=model,
                    messages=messages,
                    tools=[],
                    tool_choice="none",
                    response_format={"type": "json_object"},
                )
        finally:
            if timeout_seconds is not None and original_timeout is not None:
                client.timeout_seconds = original_timeout
        payload = parse_advisor_json(_extract_assistant_text(response))
        result = validate_readiness_mode_advisor_payload(
            payload,
            prompt_version=prompt_version,
        )
        latency = int((time.perf_counter() - start) * 1000)
        return AdvisorReadinessModeObservation(
            enabled=True,
            shadow_mode=True,
            parse_success=True,
            schema_valid=True,
            latency_ms=latency,
            result=result,
            deterministic_plan=(
                deterministic_plan.as_dict() if deterministic_plan is not None else None
            ),
        )
    except Exception as exc:
        latency = int((time.perf_counter() - start) * 1000)
        return AdvisorReadinessModeObservation(
            enabled=True,
            shadow_mode=True,
            parse_success=False,
            schema_valid=False,
            latency_ms=latency,
            result=None,
            deterministic_plan=(
                deterministic_plan.as_dict() if deterministic_plan is not None else None
            ),
            error=str(exc),
        )


def build_advisor_messages(
    *,
    user_message: str,
    session_summary: dict[str, Any],
    pending_clarification: dict[str, Any] | None = None,
    prompt_version: str = "v1",
) -> list[dict[str, Any]]:
    """Build a bounded advisor prompt with no tool or transaction schema."""
    if prompt_version == "v4":
        return _build_advisor_messages_v4(
            user_message=user_message,
            session_summary=session_summary,
            pending_clarification=pending_clarification,
        )
    if prompt_version == "v3":
        return _build_advisor_messages_v3(
            user_message=user_message,
            session_summary=session_summary,
            pending_clarification=pending_clarification,
        )
    if prompt_version == "v2":
        return _build_advisor_messages_v2(
            user_message=user_message,
            session_summary=session_summary,
            pending_clarification=pending_clarification,
        )
    if prompt_version != "v1":
        raise AdvisorValidationError(f"unknown advisor prompt version: {prompt_version!r}")
    return _build_advisor_messages_v1(
        user_message=user_message,
        session_summary=session_summary,
        pending_clarification=pending_clarification,
    )


def build_mode_advisor_messages(
    *,
    user_message: str,
    session_summary: dict[str, Any],
    prompt_version: str = "v5",
) -> list[dict[str, Any]]:
    """Build a minimal mode-router prompt."""
    if prompt_version == "v13":
        return _build_mode_advisor_messages_v13(
            user_message=user_message,
            session_summary=session_summary,
        )
    if prompt_version == "v12":
        return _build_mode_advisor_messages_v12(
            user_message=user_message,
            session_summary=session_summary,
        )
    if prompt_version == "v7":
        return _build_mode_advisor_messages_v7(
            user_message=user_message,
            session_summary=session_summary,
        )
    if prompt_version == "v8":
        return _build_mode_advisor_messages_v8(
            user_message=user_message,
            session_summary=session_summary,
        )
    if prompt_version == "v6":
        return _build_mode_advisor_messages_v6(
            user_message=user_message,
            session_summary=session_summary,
        )
    if prompt_version != "v5":
        raise AdvisorValidationError(f"unknown mode advisor prompt version: {prompt_version!r}")
    return _build_mode_advisor_messages_v5(
        user_message=user_message,
        session_summary=session_summary,
    )


def build_readiness_mode_advisor_messages(
    *,
    user_message: str,
    session_summary: dict[str, Any],
    prompt_version: str = "v9",
) -> list[dict[str, Any]]:
    """Build a readiness+mode advisor prompt."""
    if prompt_version == "v11":
        return _build_readiness_mode_advisor_messages_v11(
            user_message=user_message,
            session_summary=session_summary,
        )
    if prompt_version == "v10":
        return _build_readiness_mode_advisor_messages_v10(
            user_message=user_message,
            session_summary=session_summary,
        )
    if prompt_version != "v9":
        raise AdvisorValidationError(
            f"unknown readiness mode advisor prompt version: {prompt_version!r}"
        )
    return _build_readiness_mode_advisor_messages_v9(
        user_message=user_message,
        session_summary=session_summary,
    )


def _build_mode_advisor_messages_v7(
    *,
    user_message: str,
    session_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the v7 advisor-first minimal mode-router prompt."""
    prompt_payload = {
        "task": "Classify the user's intended interaction mode.",
        "user_prompt": _bounded_text(user_message, limit=800),
        "active_graph_summary": session_summary,
        "allowed_modes": list(ADVISOR_MODES),
        "required_json": {"mode": "one allowed mode"},
        "output_contract": [
            "Return exactly one JSON object.",
            "The object must have exactly one key: mode.",
            "The mode value must be one allowed mode.",
            "Do not output tool names, arguments, transactions, params, paths, YAML, or explanations.",
        ],
        "mode_meanings": {
            "read_only": "inspect, summarize, search, describe, explain, or conceptual question",
            "preview": "user wants to see a proposed edit without applying it",
            "edit": "parameter, block state, or variable edit; not connection editing",
            "disconnect": "remove one connection",
            "rewire": "replace one old connection with a new connection",
            "insert": "insert or add a block",
            "validate": "validate, check, or compile the graph",
            "save": "save, write, persist, or save a copy",
            "clarify": "request is ambiguous, missing details, or needs user choice",
            "unsupported": "request is outside the supported GRC assistant contract",
        },
        "instructions": [
            "Choose the user's intended mode from the prompt and compact graph context.",
            "If the request is unclear or lacks required target details, choose clarify.",
            "If the request is unsupported, choose unsupported.",
            "Do not explain and do not produce tool arguments.",
        ],
        "examples": [
            {"prompt": "Summarize this graph.", "output": {"mode": "read_only"}},
            {"prompt": "Find throttle blocks.", "output": {"mode": "read_only"}},
            {"prompt": "Describe qtgui_time_sink_x.", "output": {"mode": "read_only"}},
            {"prompt": "Preview changing samp_rate to 48000; do not apply.", "output": {"mode": "preview"}},
            {"prompt": "Try changing cutoff but don't commit it.", "output": {"mode": "preview"}},
            {"prompt": "Change samp_rate to 48000.", "output": {"mode": "edit"}},
            {"prompt": "Disable the throttle block.", "output": {"mode": "edit"}},
            {"prompt": "Add variable noise_level=0.1.", "output": {"mode": "edit"}},
            {"prompt": "Remove connection_id a:0->b:0.", "output": {"mode": "disconnect"}},
            {"prompt": "Disconnect source_0 output 0 from sink_0 input 0.", "output": {"mode": "disconnect"}},
            {"prompt": "Rewire connection_id a:0->b:0 to a:0->c:0.", "output": {"mode": "rewire"}},
            {"prompt": "Insert a head block after throttle_0.", "output": {"mode": "insert"}},
            {"prompt": "Validate this graph.", "output": {"mode": "validate"}},
            {"prompt": "Check whether this compiles.", "output": {"mode": "validate"}},
            {"prompt": "Save a copy of the graph.", "output": {"mode": "save"}},
            {"prompt": "Fix this graph.", "output": {"mode": "clarify"}},
            {"prompt": "Rewire everything.", "output": {"mode": "clarify"}},
            {"prompt": "Use block_uid abc to change the block.", "output": {"mode": "clarify"}},
            {"prompt": "Remove the connection from the source.", "output": {"mode": "clarify"}},
            {"prompt": "Patch raw YAML directly.", "output": {"mode": "unsupported"}},
            {"prompt": "Export Python for this graph.", "output": {"mode": "unsupported"}},
        ],
    }
    system = (
        "You are a local GRC intent advisor. Output exactly one JSON object: "
        "{\"mode\":\"<allowed mode>\"}. The runtime enforces all tool schemas, "
        "route gates, validation, rollback, and save rules."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(prompt_payload, sort_keys=True)},
    ]


def _build_mode_advisor_messages_v8(
    *,
    user_message: str,
    session_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the v8 conservative mode-router prompt."""
    prompt_payload = {
        "task": "Classify the user's intended interaction mode conservatively.",
        "user_prompt": _bounded_text(user_message, limit=800),
        "active_graph_summary": session_summary,
        "allowed_modes": list(ADVISOR_MODES),
        "required_json": {"mode": "one allowed mode"},
        "output_contract": [
            "Return exactly one JSON object.",
            "The object must have exactly one key: mode.",
            "The mode value must be one allowed mode.",
            "Do not output tool names, arguments, transactions, params, paths, YAML, or explanations.",
        ],
        "conservative_rule": (
            "If the user wants to mutate but the target, action, connection, endpoint, "
            "parameter, or placement is ambiguous, choose clarify."
        ),
        "mode_meanings": {
            "read_only": "inspect, summarize, search, describe, explain, show context, or conceptual question",
            "preview": "user wants a proposed/hypothetical edit without applying it",
            "edit": "clear parameter, state, or variable edit with enough target detail",
            "disconnect": "clear request to remove one exact connection or exact endpoints",
            "rewire": "clear request with an old connection and a new endpoint/connection",
            "insert": "clear request to insert/add a block with placement detail",
            "validate": "validate, check, compile, or run grcc",
            "save": "save, write, persist, or save a copy",
            "clarify": "ambiguous, vague, duplicate, missing target, missing placement, missing endpoint, or free-form UID",
            "unsupported": "raw YAML/source edit, Python/code export, undo/redo, force invalid save, or bypass validation",
        },
        "priority_rules": [
            "Preview wins over edit when the user asks to propose, dry-run, show first, what would happen, or not apply/commit.",
            "Unsupported wins over executable modes for raw YAML, source text edits, Python/code export, undo, redo, force save, ignore validation, or bypass validation.",
            "Clarify wins over executable modes when details are missing or ambiguous.",
            "Use executable modes only when the action class and target details are clear.",
        ],
        "clarify_guidance": [
            "Choose clarify for that block, it, important rate, sink parameter, duplicate block, first matching block, pick the right one, or block_uid/UID command.",
            "Choose clarify for bad wire, wrong wire, connection from the source, random source, remove connection not block, or vague disconnect.",
            "Choose clarify for fix this, repair topology, make it work, make it better, wire differently, rewire everything, move it there, or vague topology.",
            "Choose clarify for compatible filter somewhere, compatible block, insert into this graph, or missing insertion placement.",
        ],
        "examples": [
            {"prompt": "Summarize this graph.", "output": {"mode": "read_only"}},
            {"prompt": "Find throttle blocks.", "output": {"mode": "read_only"}},
            {"prompt": "Describe qtgui_time_sink_x.", "output": {"mode": "read_only"}},
            {"prompt": "Show me the PDU blocks.", "output": {"mode": "read_only"}},
            {"prompt": "Validate this graph.", "output": {"mode": "validate"}},
            {"prompt": "Check whether this compiles.", "output": {"mode": "validate"}},
            {"prompt": "Preview changing samp_rate to 48000; do not apply.", "output": {"mode": "preview"}},
            {"prompt": "Can you propose changing fft_size to 1024?", "output": {"mode": "preview"}},
            {"prompt": "Dry-run disabling the throttle block.", "output": {"mode": "preview"}},
            {"prompt": "What would happen if I set decim to 4?", "output": {"mode": "preview"}},
            {"prompt": "Try changing cutoff but don't commit it.", "output": {"mode": "preview"}},
            {"prompt": "Change samp_rate to 48000.", "output": {"mode": "edit"}},
            {"prompt": "Set cutoff_low to 3000.", "output": {"mode": "edit"}},
            {"prompt": "Disable blocks_throttle2_0.", "output": {"mode": "edit"}},
            {"prompt": "Add variable noise_level=0.1.", "output": {"mode": "edit"}},
            {"prompt": "Remove connection_id a:0->b:0.", "output": {"mode": "disconnect"}},
            {"prompt": "Disconnect source_0 output 0 from sink_0 input 0.", "output": {"mode": "disconnect"}},
            {"prompt": "Rewire connection_id a:0->b:0 to a:0->c:0.", "output": {"mode": "rewire"}},
            {"prompt": "Move old connection source_0:0->sink_0:0 to source_0:0->sink_1:0.", "output": {"mode": "rewire"}},
            {"prompt": "Insert a head block after throttle_0.", "output": {"mode": "insert"}},
            {"prompt": "Add a filter between source_0 and sink_0.", "output": {"mode": "insert"}},
            {"prompt": "Save a copy of the graph.", "output": {"mode": "save"}},
            {"prompt": "Write this graph to /tmp/test_copy.grc.", "output": {"mode": "save"}},
            {"prompt": "Change that block.", "output": {"mode": "clarify"}},
            {"prompt": "Set the important rate.", "output": {"mode": "clarify"}},
            {"prompt": "Change the sink parameter.", "output": {"mode": "clarify"}},
            {"prompt": "Disable the duplicate block.", "output": {"mode": "clarify"}},
            {"prompt": "Pick the right one and change it.", "output": {"mode": "clarify"}},
            {"prompt": "Use block_uid abc to change it.", "output": {"mode": "clarify"}},
            {"prompt": "Remove the connection from the source.", "output": {"mode": "clarify"}},
            {"prompt": "Disconnect the bad wire.", "output": {"mode": "clarify"}},
            {"prompt": "Remove connection not block.", "output": {"mode": "clarify"}},
            {"prompt": "Fix this graph.", "output": {"mode": "clarify"}},
            {"prompt": "Repair the topology.", "output": {"mode": "clarify"}},
            {"prompt": "Wire this differently.", "output": {"mode": "clarify"}},
            {"prompt": "Rewire everything.", "output": {"mode": "clarify"}},
            {"prompt": "Move it over there.", "output": {"mode": "clarify"}},
            {"prompt": "Add a compatible filter somewhere.", "output": {"mode": "clarify"}},
            {"prompt": "Insert a compatible block into this graph.", "output": {"mode": "clarify"}},
            {"prompt": "Patch raw YAML directly.", "output": {"mode": "unsupported"}},
            {"prompt": "Edit the .grc source text.", "output": {"mode": "unsupported"}},
            {"prompt": "Export Python code for this graph.", "output": {"mode": "unsupported"}},
            {"prompt": "Undo the last change.", "output": {"mode": "unsupported"}},
            {"prompt": "Redo the last edit.", "output": {"mode": "unsupported"}},
            {"prompt": "Ignore validation and save anyway.", "output": {"mode": "unsupported"}},
        ],
    }
    system = (
        "You are a conservative local GRC mode advisor. Output exactly one JSON "
        "object: {\"mode\":\"<allowed mode>\"}. Prefer clarify over executable "
        "modes whenever target details are missing. Do not output tools, args, "
        "transactions, params, paths, YAML, or explanations."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(prompt_payload, sort_keys=True)},
    ]


def _build_readiness_mode_advisor_messages_v9(
    *,
    user_message: str,
    session_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the v9 readiness+mode advisor prompt."""
    prompt_payload = {
        "task": "Classify both operational readiness and semantic action mode.",
        "user_prompt": _bounded_text(user_message, limit=800),
        "active_graph_summary": session_summary,
        "readiness_enum": list(ADVISOR_READINESS),
        "mode_enum": [
            "read_only",
            "preview",
            "edit",
            "disconnect",
            "rewire",
            "insert",
            "validate",
            "save",
            "none",
        ],
        "required_json": {"readiness": "one readiness enum", "mode": "one mode enum"},
        "output_contract": [
            "Return exactly one JSON object.",
            "The object must have exactly two keys: readiness and mode.",
            "Do not output tool names, arguments, transactions, params, paths, YAML, or explanations.",
        ],
        "readiness_meanings": {
            "ready": "request is actionable enough for the selected mode",
            "clarify": "action type is understandable but target/action/placement/duplicate choice is missing or ambiguous",
            "unsupported": "request is outside supported workflows",
        },
        "mode_meanings": {
            "read_only": "inspect, summarize, search, describe, explain, show context, or conceptual question",
            "preview": "proposed or hypothetical edit without applying it",
            "edit": "parameter, block state, or variable edit",
            "disconnect": "remove a connection",
            "rewire": "replace an old connection with a new connection",
            "insert": "insert or add a block",
            "validate": "validate, check, compile, or run grcc",
            "save": "save, write, persist, or save a copy",
            "none": "unsupported or truly unknown action type",
        },
        "rules": [
            "Use readiness=ready only when enough details exist to act for the selected mode.",
            "Use readiness=clarify when the action type is understandable but details are missing or ambiguous.",
            "Use readiness=unsupported and mode=none for unsupported workflows.",
            "Use mode=none only with unsupported or truly unknown requests.",
            "Preview is ready when the user asks to propose, dry-run, show first, or ask what would happen before applying.",
            "Do not downgrade preview to edit; preview is its own operational mode.",
            "For vague mutation requests, keep the semantic mode but set readiness=clarify.",
        ],
        "examples": [
            {"prompt": "Summarize this graph.", "output": {"readiness": "ready", "mode": "read_only"}},
            {"prompt": "Find throttle blocks.", "output": {"readiness": "ready", "mode": "read_only"}},
            {"prompt": "Validate this graph.", "output": {"readiness": "ready", "mode": "validate"}},
            {"prompt": "Check whether this compiles.", "output": {"readiness": "ready", "mode": "validate"}},
            {"prompt": "Preview changing samp_rate.", "output": {"readiness": "ready", "mode": "preview"}},
            {"prompt": "Dry-run disabling throttle.", "output": {"readiness": "ready", "mode": "preview"}},
            {"prompt": "Can you propose changing fft_size to 1024?", "output": {"readiness": "ready", "mode": "preview"}},
            {"prompt": "What would happen if I set decim to 4?", "output": {"readiness": "ready", "mode": "preview"}},
            {"prompt": "Change samp_rate to 48000.", "output": {"readiness": "ready", "mode": "edit"}},
            {"prompt": "Disable blocks_throttle2_0.", "output": {"readiness": "ready", "mode": "edit"}},
            {"prompt": "Change that block.", "output": {"readiness": "clarify", "mode": "edit"}},
            {"prompt": "Set the important rate.", "output": {"readiness": "clarify", "mode": "edit"}},
            {"prompt": "Disable the duplicate block.", "output": {"readiness": "clarify", "mode": "edit"}},
            {"prompt": "Use block_uid abc to change it.", "output": {"readiness": "clarify", "mode": "edit"}},
            {"prompt": "Remove connection connection_3.", "output": {"readiness": "ready", "mode": "disconnect"}},
            {"prompt": "Remove connection_id a:0->b:0.", "output": {"readiness": "ready", "mode": "disconnect"}},
            {"prompt": "Remove the connection from the source.", "output": {"readiness": "clarify", "mode": "disconnect"}},
            {"prompt": "Disconnect the bad wire.", "output": {"readiness": "clarify", "mode": "disconnect"}},
            {"prompt": "Rewire connection_3 to source:0 -> sink:0.", "output": {"readiness": "ready", "mode": "rewire"}},
            {"prompt": "Rewire connection_id a:0->b:0 to a:0->c:0.", "output": {"readiness": "ready", "mode": "rewire"}},
            {"prompt": "Wire this differently.", "output": {"readiness": "clarify", "mode": "rewire"}},
            {"prompt": "Move it over there.", "output": {"readiness": "clarify", "mode": "rewire"}},
            {"prompt": "Insert a throttle on connection_3.", "output": {"readiness": "ready", "mode": "insert"}},
            {"prompt": "Insert a head block after throttle_0.", "output": {"readiness": "ready", "mode": "insert"}},
            {"prompt": "Add a compatible filter somewhere.", "output": {"readiness": "clarify", "mode": "insert"}},
            {"prompt": "Add a compatible block.", "output": {"readiness": "clarify", "mode": "insert"}},
            {"prompt": "Save a copy.", "output": {"readiness": "ready", "mode": "save"}},
            {"prompt": "Write this graph to /tmp/test_copy.grc.", "output": {"readiness": "ready", "mode": "save"}},
            {"prompt": "Edit raw .grc YAML.", "output": {"readiness": "unsupported", "mode": "none"}},
            {"prompt": "Edit the .grc source text.", "output": {"readiness": "unsupported", "mode": "none"}},
            {"prompt": "Undo the last change.", "output": {"readiness": "unsupported", "mode": "none"}},
            {"prompt": "Redo the last edit.", "output": {"readiness": "unsupported", "mode": "none"}},
            {"prompt": "Export this as Python.", "output": {"readiness": "unsupported", "mode": "none"}},
            {"prompt": "Ignore validation and save anyway.", "output": {"readiness": "unsupported", "mode": "none"}},
        ],
    }
    system = (
        "You are a conservative local GRC readiness and mode advisor. Output "
        "exactly one JSON object with keys readiness and mode. Do not output "
        "tools, args, transactions, params, paths, YAML, or explanations."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(prompt_payload, sort_keys=True)},
    ]


def _build_readiness_mode_advisor_messages_v10(
    *,
    user_message: str,
    session_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the v10 hierarchical readiness+mode advisor prompt."""
    prompt_payload = {
        "task": "First decide whether the request is ready, preview-only, unclear, "
               "or unsupported. Then decide the concrete mode when ready.",
        "user_prompt": _bounded_text(user_message, limit=800),
        "active_graph_summary": session_summary,
        "decision_enum": list(ADVISOR_DECISIONS),
        "mode_enum": [
            "read_only",
            "edit",
            "disconnect",
            "rewire",
            "insert",
            "validate",
            "save",
            "preview",
            "none",
        ],
        "required_json": {"decision": "one decision enum", "mode": "one mode enum"},
        "output_contract": [
            "Return exactly one JSON object.",
            "The object must have exactly two keys: decision and mode.",
            "Do not output tool names, arguments, transactions, params, paths, YAML, or explanations.",
        ],
        "decision_rules": {
            "ready": (
                "request is clear and supported and actionable now; route to concrete mode"
            ),
            "preview": (
                "request asks for hypothetical or draft edit before applying; mode must be preview"
            ),
            "clarify": (
                "supported intent but target/action/placement/duplicate choice/details are missing"
            ),
            "unsupported": (
                "request is outside supported workflows (raw YAML/source edits, export, undo/redo, "
                "force invalid save, bypass validation)"
            ),
        },
        "mode_rules": {
            "read_only": "inspect, summarize, search, describe, explain, or conceptual question",
            "preview": "hypothetical edit before applying",
            "edit": "clear parameter/state/variable edit with enough target detail",
            "disconnect": "clear request to remove one exact connection",
            "rewire": "clear request with old connection and new connection/endpoint",
            "insert": "clear request to add a block with placement detail",
            "validate": "validate/check/compile graph",
            "save": "explicit save/write/save-copy request",
            "none": "unsupported or unknown",
        },
        "priority_rules": [
            "If unsupported intent is present, output decision=unsupported and mode=none.",
            "If preview wording is present, output decision=preview and mode=preview.",
            "If details are ambiguous, output decision=clarify and mode=none.",
            "Only when details are clear and supported output decision=ready.",
        ],
        "examples": [
            {"prompt": "Summarize this graph.", "output": {"decision": "ready", "mode": "read_only"}},
            {"prompt": "Find throttle blocks.", "output": {"decision": "ready", "mode": "read_only"}},
            {"prompt": "Describe qtgui_time_sink_x.", "output": {"decision": "ready", "mode": "read_only"}},
            {"prompt": "Validate this graph.", "output": {"decision": "ready", "mode": "validate"}},
            {"prompt": "Check whether this compiles.", "output": {"decision": "ready", "mode": "validate"}},
            {"prompt": "Preview changing samp_rate to 48000.", "output": {"decision": "preview", "mode": "preview"}},
            {"prompt": "Dry-run disabling throttle.", "output": {"decision": "preview", "mode": "preview"}},
            {"prompt": "Show me the transaction for setting gain to 2.", "output": {"decision": "preview", "mode": "preview"}},
            {"prompt": "Change samp_rate to 48000.", "output": {"decision": "ready", "mode": "edit"}},
            {"prompt": "Disable blocks_throttle2_0.", "output": {"decision": "ready", "mode": "edit"}},
            {"prompt": "Remove connection_3.", "output": {"decision": "ready", "mode": "disconnect"}},
            {"prompt": "Remove connection_id a:0->b:0.", "output": {"decision": "ready", "mode": "disconnect"}},
            {"prompt": "Rewire connection_3 to source:0 -> sink:0.", "output": {"decision": "ready", "mode": "rewire"}},
            {"prompt": "Rewire connection_id a:0->b:0 to a:0->c:0.", "output": {"decision": "ready", "mode": "rewire"}},
            {"prompt": "Insert a throttle on connection_3.", "output": {"decision": "ready", "mode": "insert"}},
            {"prompt": "Add a compatible filter after throttle_0.", "output": {"decision": "ready", "mode": "insert"}},
            {"prompt": "Save a copy.", "output": {"decision": "ready", "mode": "save"}},
            {"prompt": "Write this graph to /tmp/test_copy.grc.", "output": {"decision": "ready", "mode": "save"}},
            {"prompt": "What would happen if I remove that connection?", "output": {"decision": "preview", "mode": "preview"}},
            {"prompt": "Change that block.", "output": {"decision": "clarify", "mode": "none"}},
            {"prompt": "Set the important rate.", "output": {"decision": "clarify", "mode": "none"}},
            {"prompt": "Remove the bad connection.", "output": {"decision": "clarify", "mode": "none"}},
            {"prompt": "Wire this differently.", "output": {"decision": "clarify", "mode": "none"}},
            {"prompt": "Edit raw .grc YAML.", "output": {"decision": "unsupported", "mode": "none"}},
            {"prompt": "Save the invalid graph anyway.", "output": {"decision": "unsupported", "mode": "none"}},
            {"prompt": "Commit without validation.", "output": {"decision": "unsupported", "mode": "none"}},
            {"prompt": "Undo the last change.", "output": {"decision": "unsupported", "mode": "none"}},
        ],
    }
    system = (
        "You are a local GRC readiness-aware advisor. Output exactly one JSON object: "
        "{\"decision\":\"<ready|preview|clarify|unsupported>\","
        " \"mode\":\"<mode>\"}. The mode should be one of the allowed enum values."
        " Do not output any tool calls, transactions, params, paths, YAML, or explanations."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(prompt_payload, sort_keys=True)},
    ]


def _build_readiness_mode_advisor_messages_v11(
    *,
    user_message: str,
    session_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the v11 conservative readiness+mode advisor prompt."""
    prompt_payload = {
        "task": (
            "Classify operational readiness first, then mode. "
            "Do not follow action verbs blindly."
        ),
        "user_prompt": _bounded_text(user_message, limit=800),
        "active_graph_summary": session_summary,
        "readiness_enum": list(ADVISOR_DECISIONS),
        "mode_enum": [
            "read_only",
            "preview",
            "edit",
            "disconnect",
            "rewire",
            "insert",
            "validate",
            "save",
            "none",
        ],
        "required_json": {"readiness": "one readiness enum", "mode": "one mode enum"},
        "output_contract": [
            "Return exactly one JSON object.",
            "The object must have exactly two keys: readiness and mode.",
            "Do not output tool names, arguments, transactions, params, paths, YAML, or explanations.",
        ],
        "hard_contract": [
            "If readiness is clarify, mode must be none.",
            "If readiness is unsupported, mode must be none.",
            "If readiness is preview, mode must be preview.",
            "If readiness is ready, mode must be one of read_only/edit/disconnect/rewire/insert/validate/save.",
        ],
        "readiness_rules": {
            "ready": (
                "supported request with enough detail to execute now under bounded workflows"
            ),
            "preview": (
                "hypothetical/draft/proposed edit before apply or commit"
            ),
            "clarify": (
                "supported in principle but missing exact target/action details, "
                "connection details, placement details, or duplicate selection"
            ),
            "unsupported": (
                "outside supported workflows: raw YAML/source edits, tutorial recipe apply, "
                "undo/redo, save invalid graph, bypass validation, export Python/code generation"
            ),
        },
        "target_readiness_rules": [
            "A disconnect request is ready only with exact connection_id or exact source+destination endpoints.",
            "A rewire request is ready only with exact old connection and exact new endpoint(s).",
            "An insert request is ready only with exact placement anchor.",
            "An edit request is ready only with clear target and action.",
            "If any required mutation target detail is missing, choose clarify/none.",
        ],
        "ambiguity_rules": [
            "Vague connection wording like bad wire, from the source, from the endpoint, that connection -> clarify/none.",
            "Ambiguous duplicate wording like first matching, right one, duplicate block, that block -> clarify/none.",
            "Preview/draft wording like draft an edit, show me the transaction, what would happen if -> preview/preview.",
            "If uncertain between ready and clarify for mutation requests, choose clarify/none.",
        ],
        "mode_meanings": {
            "read_only": "inspect, summarize, search, describe, explain, ask conceptual questions",
            "preview": "propose/hypothetical edit without applying",
            "edit": "parameter/state/variable edit",
            "disconnect": "remove one exact connection",
            "rewire": "replace one exact old connection with exact new endpoint(s)",
            "insert": "insert/add a block at exact placement",
            "validate": "validate/check/compile",
            "save": "explicit save/write/save-copy request",
            "none": "used only with clarify or unsupported",
        },
        "examples": [
            {"prompt": "Summarize this graph.", "output": {"readiness": "ready", "mode": "read_only"}},
            {"prompt": "Find throttle blocks.", "output": {"readiness": "ready", "mode": "read_only"}},
            {"prompt": "Validate this graph.", "output": {"readiness": "ready", "mode": "validate"}},
            {"prompt": "Check whether this compiles.", "output": {"readiness": "ready", "mode": "validate"}},
            {"prompt": "Draft an edit to set cutoff to 2e3.", "output": {"readiness": "preview", "mode": "preview"}},
            {"prompt": "Show me the transaction for setting gain to 2.", "output": {"readiness": "preview", "mode": "preview"}},
            {"prompt": "What would happen if I remove that connection?", "output": {"readiness": "preview", "mode": "preview"}},
            {"prompt": "Change samp_rate to 48000.", "output": {"readiness": "ready", "mode": "edit"}},
            {"prompt": "Disable blocks_throttle2_0.", "output": {"readiness": "ready", "mode": "edit"}},
            {"prompt": "Remove connection_3.", "output": {"readiness": "ready", "mode": "disconnect"}},
            {"prompt": "Disconnect source_0:0 from sink_0:0.", "output": {"readiness": "ready", "mode": "disconnect"}},
            {"prompt": "Rewire connection_3 to source_0:0 -> sink_1:0.", "output": {"readiness": "ready", "mode": "rewire"}},
            {"prompt": "Insert a throttle on connection_3.", "output": {"readiness": "ready", "mode": "insert"}},
            {"prompt": "Save a copy to /tmp/x.grc.", "output": {"readiness": "ready", "mode": "save"}},
            {"prompt": "Remove the connection from the source.", "output": {"readiness": "clarify", "mode": "none"}},
            {"prompt": "Disconnect the bad wire.", "output": {"readiness": "clarify", "mode": "none"}},
            {"prompt": "Disconnect from the bad endpoint.", "output": {"readiness": "clarify", "mode": "none"}},
            {"prompt": "Disable the first matching throttle.", "output": {"readiness": "clarify", "mode": "none"}},
            {"prompt": "Pick the right one and change it.", "output": {"readiness": "clarify", "mode": "none"}},
            {"prompt": "Change that block.", "output": {"readiness": "clarify", "mode": "none"}},
            {"prompt": "Wire this differently.", "output": {"readiness": "clarify", "mode": "none"}},
            {"prompt": "Use a tutorial recipe and apply it directly.", "output": {"readiness": "unsupported", "mode": "none"}},
            {"prompt": "Edit raw .grc YAML.", "output": {"readiness": "unsupported", "mode": "none"}},
            {"prompt": "Edit the source directly.", "output": {"readiness": "unsupported", "mode": "none"}},
            {"prompt": "Undo the last change.", "output": {"readiness": "unsupported", "mode": "none"}},
            {"prompt": "Redo the last change.", "output": {"readiness": "unsupported", "mode": "none"}},
            {"prompt": "Save the invalid graph anyway.", "output": {"readiness": "unsupported", "mode": "none"}},
            {"prompt": "Commit without validation.", "output": {"readiness": "unsupported", "mode": "none"}},
        ],
    }
    system = (
        "You are a conservative local GRC readiness+mode advisor. Output exactly one "
        "JSON object with keys readiness and mode. Do not output tools, args, "
        "transactions, params, paths, YAML, or explanations."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(prompt_payload, sort_keys=True)},
    ]


def _build_mode_advisor_messages_v12(
    *,
    user_message: str,
    session_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the v12 rollback prompt for one-field mode routing."""
    prompt_payload = {
        "task": "Choose exactly one interaction mode for the user prompt.",
        "user_prompt": _bounded_text(user_message, limit=800),
        "active_graph_summary": session_summary,
        "allowed_modes": list(ADVISOR_MODES),
        "required_json": {"mode": "one allowed mode"},
        "output_contract": [
            "Return exactly one JSON object.",
            "The object must contain exactly one key: mode.",
            "The mode value must be one of allowed_modes.",
            "Do not output tool names, tool arguments, transactions, params, paths, YAML, or explanations.",
        ],
        "mode_meanings": {
            "read_only": "inspect/summarize/search/describe/explain/context questions",
            "preview": "proposed or hypothetical edit before applying",
            "edit": "parameter/state/variable edits when target is clear",
            "disconnect": "remove one exact connection",
            "rewire": "replace one exact old connection with an exact new endpoint",
            "insert": "insert/add block with clear placement",
            "validate": "validate/check/compile graph",
            "save": "explicit save/write/save-copy request",
            "clarify": "supported request but ambiguous/under-specified target or action",
            "unsupported": "outside supported workflows",
        },
        "classification_guidance": [
            "Classify by user intent and operational clarity.",
            "Use clarify when mutation target/action detail is ambiguous or missing.",
            "Use unsupported for raw source edits, undo/redo, bypass-validation, tutorial-recipe apply, or code export requests.",
            "Use preview for propose/draft/transaction-preview/hypothetical requests.",
        ],
        "examples": [
            {"prompt": "Summarize this graph.", "output": {"mode": "read_only"}},
            {"prompt": "Find throttle blocks.", "output": {"mode": "read_only"}},
            {"prompt": "Show me the throttle block.", "output": {"mode": "read_only"}},
            {"prompt": "Describe qtgui_time_sink_x.", "output": {"mode": "read_only"}},
            {"prompt": "Validate this graph.", "output": {"mode": "validate"}},
            {"prompt": "Check whether this compiles.", "output": {"mode": "validate"}},
            {"prompt": "Can you propose changing fft_size to 1024?", "output": {"mode": "preview"}},
            {"prompt": "Draft an edit to set cutoff to 2e3.", "output": {"mode": "preview"}},
            {"prompt": "Show me the transaction for setting gain to 2.", "output": {"mode": "preview"}},
            {"prompt": "What would happen if I disable throttle?", "output": {"mode": "preview"}},
            {"prompt": "Set samp_rate to 48000.", "output": {"mode": "edit"}},
            {"prompt": "Disable blocks_throttle2_0.", "output": {"mode": "edit"}},
            {"prompt": "Add variable noise_level=0.1.", "output": {"mode": "edit"}},
            {"prompt": "Remove connection_3.", "output": {"mode": "disconnect"}},
            {"prompt": "Disconnect source_0:0 from sink_0:0.", "output": {"mode": "disconnect"}},
            {"prompt": "Rewire connection_3 to source_0:0 -> sink_1:0.", "output": {"mode": "rewire"}},
            {"prompt": "Insert a throttle on connection_3.", "output": {"mode": "insert"}},
            {"prompt": "Save a copy to /tmp/x.grc.", "output": {"mode": "save"}},
            {"prompt": "Remove the connection from the source.", "output": {"mode": "clarify"}},
            {"prompt": "Disconnect the bad wire.", "output": {"mode": "clarify"}},
            {"prompt": "Disable the duplicate block.", "output": {"mode": "clarify"}},
            {"prompt": "Change the sink parameter.", "output": {"mode": "clarify"}},
            {"prompt": "Pick the right one and change it.", "output": {"mode": "clarify"}},
            {"prompt": "Edit the .grc source text.", "output": {"mode": "unsupported"}},
            {"prompt": "Undo the last change.", "output": {"mode": "unsupported"}},
            {"prompt": "Redo the previous operation.", "output": {"mode": "unsupported"}},
            {"prompt": "Save the invalid graph anyway.", "output": {"mode": "unsupported"}},
            {"prompt": "Use a tutorial recipe and apply it directly.", "output": {"mode": "unsupported"}},
        ],
    }
    system = (
        "You are a strict JSON mode router for a local GRC assistant. "
        "Output only {\"mode\":\"...\"} with one allowed mode. "
        "No extra keys and no prose."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(prompt_payload, sort_keys=True)},
    ]


def _build_mode_advisor_messages_v13(
    *,
    user_message: str,
    session_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the v13 one-field actionability prompt."""
    prompt_payload = {
        "task": "Classify the request into exactly one interaction mode.",
        "user_prompt": _bounded_text(user_message, limit=800),
        "active_graph_summary": session_summary,
        "allowed_modes": list(ADVISOR_MODES),
        "required_json": {"mode": "one allowed mode"},
        "output_contract": [
            "Return exactly one JSON object with exactly one key: mode.",
            "Do not output extra keys.",
            "Do not output tools, arguments, transactions, params, paths, YAML, or explanations.",
        ],
        "actionability_contract": [
            "Use executable modes only when the prompt has enough target/action detail for that action class.",
            "Use clarify when the action is supported in principle but missing target/endpoint/placement/duplicate selection.",
            "Use unsupported when workflow is outside scope.",
            "Use preview for hypothetical/proposal/no-commit requests.",
            "If unsure between executable and clarify, choose clarify.",
        ],
        "mode_meanings": {
            "read_only": "inspect/summarize/search/describe/explain/context",
            "preview": "proposed or hypothetical edit before apply",
            "edit": "parameter/state/variable edit with clear target",
            "disconnect": "remove one exact connection",
            "rewire": "replace one exact old connection with exact new endpoint(s)",
            "insert": "insert/add with clear placement",
            "validate": "validate/check/compile graph",
            "save": "explicit save/write/save-copy request",
            "clarify": "supported request but under-specified/ambiguous",
            "unsupported": "out-of-scope request",
        },
        "contrastive_examples": [
            {"prompt": "Show me the throttle block.", "output": {"mode": "read_only"}},
            {"prompt": "Find all sink blocks.", "output": {"mode": "read_only"}},
            {"prompt": "Validate this graph.", "output": {"mode": "validate"}},
            {"prompt": "Check whether this compiles.", "output": {"mode": "validate"}},
            {"prompt": "Can you propose changing fft_size to 1024?", "output": {"mode": "preview"}},
            {"prompt": "Draft an edit to set cutoff to 2e3.", "output": {"mode": "preview"}},
            {"prompt": "Show me the transaction for setting gain to 2.", "output": {"mode": "preview"}},
            {"prompt": "Set samp_rate to 48000.", "output": {"mode": "edit"}},
            {"prompt": "Disable blocks_throttle2_0.", "output": {"mode": "edit"}},
            {"prompt": "Remove connection_3.", "output": {"mode": "disconnect"}},
            {"prompt": "Disconnect source_0:0 from sink_0:0.", "output": {"mode": "disconnect"}},
            {"prompt": "Rewire connection_3 to source_0:0 -> sink_1:0.", "output": {"mode": "rewire"}},
            {"prompt": "Insert a throttle on connection_3.", "output": {"mode": "insert"}},
            {"prompt": "Save a copy to /tmp/x.grc.", "output": {"mode": "save"}},
            {"prompt": "Change that block.", "output": {"mode": "clarify"}},
            {"prompt": "Change the sink parameter.", "output": {"mode": "clarify"}},
            {"prompt": "Disable the first matching throttle.", "output": {"mode": "clarify"}},
            {"prompt": "Pick the right one and change it.", "output": {"mode": "clarify"}},
            {"prompt": "Remove the connection from the source.", "output": {"mode": "clarify"}},
            {"prompt": "Disconnect the bad wire.", "output": {"mode": "clarify"}},
            {"prompt": "Add a compatible filter somewhere.", "output": {"mode": "clarify"}},
            {"prompt": "Edit the .grc source text.", "output": {"mode": "unsupported"}},
            {"prompt": "Undo the last change.", "output": {"mode": "unsupported"}},
            {"prompt": "Redo the previous operation.", "output": {"mode": "unsupported"}},
            {"prompt": "Save the invalid graph anyway.", "output": {"mode": "unsupported"}},
            {"prompt": "Use a tutorial recipe and apply it directly.", "output": {"mode": "unsupported"}},
        ],
    }
    system = (
        "You are a strict JSON semantic mode classifier for a local GRC assistant. "
        "Output only {\"mode\":\"...\"}."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(prompt_payload, sort_keys=True)},
    ]


def _build_mode_advisor_messages_v5(
    *,
    user_message: str,
    session_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the v5 minimal mode-router prompt."""
    prompt_payload = {
        "task": "Choose exactly one coarse interaction mode for the user prompt.",
        "user_prompt": _bounded_text(user_message, limit=800),
        "active_graph_summary": session_summary,
        "allowed_modes": list(ADVISOR_MODES),
        "required_json": {"mode": "one allowed mode"},
        "forbidden_output": [
            "tool names",
            "tool args",
            "transactions",
            "params",
            "risk_flags",
            "target_mentions",
            "reason",
            "clarification_question",
            "save paths",
            "repair plans",
            "YAML",
            "connection payloads",
        ],
        "mode_meanings": {
            "read_only": "inspect, summarize, search, describe, explain, or conceptual question",
            "preview": "user wants to see a proposed edit without applying",
            "edit": "parameter, state, or variable edit; not a connection edit",
            "disconnect": "remove one exact connection",
            "rewire": "remove an old connection and add a new connection",
            "insert": "insert or add a compatible block",
            "validate": "validate, check, or compile graph",
            "save": "explicit save, write, or save-copy request",
            "clarify": "unclear, ambiguous, missing target, vague topology, block_uid freeform",
            "unsupported": "raw YAML edit, Python export, undo/redo, or broad unsupported request",
        },
        "rules": [
            "Return exactly one JSON object with exactly one key: mode.",
            "The mode value must be exactly one allowed mode.",
            "Do not include any other key.",
            "Use read_only for find/search/describe/summarize/explain/what/show prompts.",
            "Use validate for validate/check whether valid/compile prompts.",
            "Use preview for preview/dry-run/show first/do not apply/don't commit wording.",
            "Use save only for explicit save/write/save-copy requests.",
            "Use clarify for fix this graph, repair topology, rewire everything, missing target, or block_uid freeform.",
            "Use clarify for that block, duplicate block, first matching block, wrong wire, bad connection, better sink, or somewhere wording.",
            "Use unsupported for raw YAML, direct .grc text editing, Python export, undo, or redo.",
        ],
        "examples": [
            {"prompt": "Summarize this graph.", "output": {"mode": "read_only"}},
            {"prompt": "Find throttle blocks.", "output": {"mode": "read_only"}},
            {"prompt": "Describe qtgui_time_sink_x.", "output": {"mode": "read_only"}},
            {"prompt": "What does this flowgraph do?", "output": {"mode": "read_only"}},
            {"prompt": "Validate this graph.", "output": {"mode": "validate"}},
            {"prompt": "Check whether this compiles.", "output": {"mode": "validate"}},
            {"prompt": "validate only", "output": {"mode": "validate"}},
            {"prompt": "previw changing samp_rate, dont apply", "output": {"mode": "preview"}},
            {"prompt": "try changing but don't commit", "output": {"mode": "preview"}},
            {"prompt": "Show me first before applying.", "output": {"mode": "preview"}},
            {"prompt": "Change samp_rate to 48000.", "output": {"mode": "edit"}},
            {"prompt": "Disable the throttle block.", "output": {"mode": "edit"}},
            {"prompt": "Add variable noise_level=0.1.", "output": {"mode": "edit"}},
            {"prompt": "Remove connection_id a:0->b:0.", "output": {"mode": "disconnect"}},
            {"prompt": "Disconnect source_0 output 0 from sink_0 input 0.", "output": {"mode": "disconnect"}},
            {"prompt": "Rewire connection_id a:0->b:0 to a:0->c:0.", "output": {"mode": "rewire"}},
            {"prompt": "Move old connection a:0->b:0 to a:0->c:0.", "output": {"mode": "rewire"}},
            {"prompt": "Insert a head block after the throttle.", "output": {"mode": "insert"}},
            {"prompt": "Add a compatible filter between source and sink.", "output": {"mode": "insert"}},
            {"prompt": "Save a copy.", "output": {"mode": "save"}},
            {"prompt": "Write this graph to a copy path.", "output": {"mode": "save"}},
            {"prompt": "fix this graph", "output": {"mode": "clarify"}},
            {"prompt": "rewire everything", "output": {"mode": "clarify"}},
            {"prompt": "mutate block_uid abc", "output": {"mode": "clarify"}},
            {"prompt": "change block_uid block:deadbeef to gain 5", "output": {"mode": "clarify"}},
            {"prompt": "remove connection not block", "output": {"mode": "clarify"}},
            {"prompt": "change that block", "output": {"mode": "clarify"}},
            {"prompt": "disable the duplicate block", "output": {"mode": "clarify"}},
            {"prompt": "set the parameter on the sink", "output": {"mode": "clarify"}},
            {"prompt": "remove the connection from the source", "output": {"mode": "clarify"}},
            {"prompt": "move it over there", "output": {"mode": "clarify"}},
            {"prompt": "pick the right one and change it", "output": {"mode": "clarify"}},
            {"prompt": "apply the setting to the first matching block", "output": {"mode": "clarify"}},
            {"prompt": "change the duplicate-name block to value 4", "output": {"mode": "clarify"}},
            {"prompt": "disconnect the random source", "output": {"mode": "clarify"}},
            {"prompt": "wire this differently", "output": {"mode": "clarify"}},
            {"prompt": "build me a receiver from this", "output": {"mode": "clarify"}},
            {"prompt": "disconnect the bad connection", "output": {"mode": "clarify"}},
            {"prompt": "rewire to the better time sink", "output": {"mode": "clarify"}},
            {"prompt": "insert a compatible filter into this graph", "output": {"mode": "clarify"}},
            {"prompt": "change the important rate", "output": {"mode": "clarify"}},
            {"prompt": "add a compatible filter somewhere", "output": {"mode": "clarify"}},
            {"prompt": "disconnect the wrong wire", "output": {"mode": "clarify"}},
            {"prompt": "patch raw YAML", "output": {"mode": "unsupported"}},
            {"prompt": "export python script", "output": {"mode": "unsupported"}},
            {"prompt": "undo the last change", "output": {"mode": "unsupported"}},
            {"prompt": "ignore validation and save anyway", "output": {"mode": "unsupported"}},
        ],
    }
    system = (
        "You are a strict JSON mode router. Output only {\"mode\":\"...\"}. "
        "Do not output tools, arguments, transactions, explanations, or extra keys. "
        "This is shadow telemetry only and cannot authorize mutation."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(prompt_payload, sort_keys=True)},
    ]


def _build_mode_advisor_messages_v6(
    *,
    user_message: str,
    session_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the v6 safety-refined minimal mode-router prompt."""
    prompt_payload = {
        "task": "Choose exactly one coarse interaction mode for the user prompt.",
        "user_prompt": _bounded_text(user_message, limit=800),
        "active_graph_summary": session_summary,
        "allowed_modes": list(ADVISOR_MODES),
        "required_json": {"mode": "one allowed mode"},
        "hard_output_rules": [
            "Return exactly one JSON object with exactly one key: mode.",
            "The mode value must be exactly one allowed mode.",
            "Do not include any other key.",
            "Never output tool names, arguments, transactions, params, paths, YAML, or explanations.",
            "When uncertain between executable and clarify, choose clarify.",
            "Apply safety precedence first: unsupported before clarify before preview before executable modes.",
        ],
        "hard_safety_rules": [
            "If user says do not apply, don't apply, dont apply, do not commit, don't commit, preview only, show me first, before applying, dry-run, what would happen, or would it happen: mode preview.",
            "If user asks raw YAML, patch YAML, edit source, source directly, direct .grc text edit, export Python, undo, redo, overwrite original, save anyway, ignore validation, or tutorial recipe: mode unsupported.",
            "If user text contains block_uid, UID, or block: as a direct command: mode clarify.",
            "If user asks fix, repair, make work, works better, better sink, wire this differently, rewire topology, change topology, rewire everything, better time sink, or vague wiring without exact old and new endpoints: mode clarify.",
            "If user asks remove/disconnect connection but no exact connection_id or exact source and destination endpoints: mode clarify.",
            "If user says bad connection, wrong wire, random source, whatever, it, there, from the source, remove connection not block, or unwire: mode clarify.",
            "If user says compatible block, compatible filter, somewhere, or lacks placement: mode clarify.",
            "If user asks important rate, that block, duplicate block, duplicate-name block, first matching block, pick the right one, parameter on the sink, or setting without exact target: mode clarify.",
            "If user says proceed with the graph and no concrete operation: mode clarify.",
            "If user explicitly asks save, write, or save a copy, choose save even when the prompt also mentions an edit.",
            "Otherwise never choose save.",
        ],
        "positive_rules": [
            "For search/find/describe/summarize/explain/show context/what is questions: mode read_only.",
            "For validate/check compile/check valid: mode validate.",
            "For exact parameter/state/variable changes with a clear target: mode edit.",
            "For exact remove one connection with connection_id or exact endpoints: mode disconnect.",
            "For exact old connection plus exact new endpoint: mode rewire.",
            "For exact insert/add block with placement: mode insert.",
        ],
        "examples": [
            {"prompt": "Summarize this graph.", "output": {"mode": "read_only"}},
            {"prompt": "Find throttle blocks.", "output": {"mode": "read_only"}},
            {"prompt": "Describe qtgui_time_sink_x.", "output": {"mode": "read_only"}},
            {"prompt": "Show me what uses samp_rate.", "output": {"mode": "read_only"}},
            {"prompt": "Validate this graph.", "output": {"mode": "validate"}},
            {"prompt": "Check whether this compiles.", "output": {"mode": "validate"}},
            {"prompt": "validate only", "output": {"mode": "validate"}},
            {"prompt": "previw changing samp_rate, dont apply", "output": {"mode": "preview"}},
            {"prompt": "try changing but don't commit", "output": {"mode": "preview"}},
            {"prompt": "Dry-run setting decim to 4.", "output": {"mode": "preview"}},
            {"prompt": "What would happen if I disable the audio sink?", "output": {"mode": "preview"}},
            {"prompt": "Show me first before applying.", "output": {"mode": "preview"}},
            {"prompt": "Change samp_rate to 48000.", "output": {"mode": "edit"}},
            {"prompt": "Set cutoff_low to 3000.", "output": {"mode": "edit"}},
            {"prompt": "Disable blocks_throttle2_0.", "output": {"mode": "edit"}},
            {"prompt": "Add variable noise_level=0.1.", "output": {"mode": "edit"}},
            {"prompt": "Remove connection_id a:0->b:0.", "output": {"mode": "disconnect"}},
            {"prompt": "Disconnect source_0 output 0 from sink_0 input 0.", "output": {"mode": "disconnect"}},
            {"prompt": "Rewire connection_id a:0->b:0 to a:0->c:0.", "output": {"mode": "rewire"}},
            {"prompt": "Move old connection a:0->b:0 to a:0->c:0.", "output": {"mode": "rewire"}},
            {"prompt": "Insert a head block after throttle_0.", "output": {"mode": "insert"}},
            {"prompt": "Add a filter between source_0 and sink_0.", "output": {"mode": "insert"}},
            {"prompt": "Save a copy.", "output": {"mode": "save"}},
            {"prompt": "Write this graph to a copy path.", "output": {"mode": "save"}},
            {"prompt": "fix this graph", "output": {"mode": "clarify"}},
            {"prompt": "repair topology", "output": {"mode": "clarify"}},
            {"prompt": "rewire everything", "output": {"mode": "clarify"}},
            {"prompt": "wire this differently", "output": {"mode": "clarify"}},
            {"prompt": "rewire to a better sink", "output": {"mode": "clarify"}},
            {"prompt": "Rewire the matrix multiplexer to the better time sink.", "output": {"mode": "clarify"}},
            {"prompt": "rewire this graph so it works better", "output": {"mode": "clarify"}},
            {"prompt": "mutate block_uid abc", "output": {"mode": "clarify"}},
            {"prompt": "change block_uid block:deadbeef to gain 5", "output": {"mode": "clarify"}},
            {"prompt": "Set block_uid abc value to 4.", "output": {"mode": "clarify"}},
            {"prompt": "remove connection not block", "output": {"mode": "clarify"}},
            {"prompt": "disconnect the bad connection", "output": {"mode": "clarify"}},
            {"prompt": "disconnect the wrong wire", "output": {"mode": "clarify"}},
            {"prompt": "Disconnect the random source.", "output": {"mode": "clarify"}},
            {"prompt": "Remove the connection from the source.", "output": {"mode": "clarify"}},
            {"prompt": "Unwire it from there.", "output": {"mode": "clarify"}},
            {"prompt": "Disconnect whatever is broken.", "output": {"mode": "clarify"}},
            {"prompt": "insert a compatible filter into this graph", "output": {"mode": "clarify"}},
            {"prompt": "add a compatible filter somewhere", "output": {"mode": "clarify"}},
            {"prompt": "Add a compatible throttle or filter somewhere in this UDP source graph.", "output": {"mode": "clarify"}},
            {"prompt": "change the important rate", "output": {"mode": "clarify"}},
            {"prompt": "change that block", "output": {"mode": "clarify"}},
            {"prompt": "set the parameter on the sink", "output": {"mode": "clarify"}},
            {"prompt": "Disable the duplicate block.", "output": {"mode": "clarify"}},
            {"prompt": "Pick the right one and change it.", "output": {"mode": "clarify"}},
            {"prompt": "Apply the setting to the first matching block.", "output": {"mode": "clarify"}},
            {"prompt": "Change duplicate-name block to value 4.", "output": {"mode": "clarify"}},
            {"prompt": "Proceed with the graph.", "output": {"mode": "clarify"}},
            {"prompt": "patch raw YAML", "output": {"mode": "unsupported"}},
            {"prompt": "Edit source directly.", "output": {"mode": "unsupported"}},
            {"prompt": "export python script", "output": {"mode": "unsupported"}},
            {"prompt": "undo the last change", "output": {"mode": "unsupported"}},
            {"prompt": "redo the last change", "output": {"mode": "unsupported"}},
            {"prompt": "Overwrite the original graph file without asking.", "output": {"mode": "unsupported"}},
            {"prompt": "ignore validation and save anyway", "output": {"mode": "unsupported"}},
            {"prompt": "Make a block recipe from the tutorial and apply it.", "output": {"mode": "unsupported"}},
        ],
    }
    system = (
        "You are a strict JSON mode router. Output only {\"mode\":\"...\"}. "
        "Prefer clarify over executable modes when target or placement is missing. "
        "Highest-priority safety: duplicate or duplicate-name => clarify; block_uid or UID => clarify; "
        "compatible filter without exact placement => clarify; bad connection or random source => clarify; "
        "explicit save/copy request => save; raw YAML/source/export/undo/redo/save anyway => unsupported. "
        "This is shadow telemetry only and cannot authorize mutation."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(prompt_payload, sort_keys=True)},
    ]


def _build_advisor_messages_v1(
    *,
    user_message: str,
    session_summary: dict[str, Any],
    pending_clarification: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build the original advisor prompt for backward-compatible shadow runs."""
    prompt_payload = {
        "user_prompt": _bounded_text(user_message, limit=1000),
        "active_graph_summary": session_summary,
        "pending_clarification": _compact_pending_clarification(pending_clarification),
        "intent_labels": list(ADVISOR_INTENTS),
        "risk_flags": {
            "negated_apply": "preview or do-not-apply wording is present",
            "save_request": "explicit save/copy/persist request is present",
            "raw_yaml": "request asks for raw .grc/YAML/text editing",
            "vague_topology": "request asks to fix/repair/rewire topology vaguely",
            "block_uid_freeform": "request asks to mutate by block_uid text",
            "duplicate_target": "target wording could match duplicate blocks",
            "missing_target": "mutation lacks a clear block/variable/connection target",
            "missing_placement": "insertion lacks exact placement/connection anchor",
            "unsupported_request": "request is outside bounded GRC tools",
        },
        "output_schema": {
            "intent": "one listed intent label",
            "confidence": "number from 0.0 to 1.0",
            "needs_clarification": "boolean",
            "risk_flags": "array of listed risk flags",
            "target_mentions": "array of short strings from the user text",
            "clarification_question": "short string or null",
            "reason": "short explanation",
        },
    }
    system = (
        "You are a shadow intent classifier for a GNU Radio Companion assistant. "
        "Return strict JSON only. Do not output tool calls, transactions, params, "
        "save paths, repair plans, recipes, YAML, or connection mutation payloads. "
        "You cannot authorize mutation; runtime policy is deterministic."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(prompt_payload, sort_keys=True)},
    ]


def _build_advisor_messages_v4(
    *,
    user_message: str,
    session_summary: dict[str, Any],
    pending_clarification: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build a schema-first advisor prompt for v4 shadow experiments."""
    prompt_payload = {
        "task": (
            "Classify the user's explicit intent into the enum. This is shadow telemetry only; "
            "you cannot authorize tools, tool arguments, transactions, saves, or graph mutation."
        ),
        "user_prompt": _bounded_text(user_message, limit=1000),
        "active_graph_summary": session_summary,
        "pending_clarification": _compact_pending_clarification(pending_clarification),
        "intent_enum": list(ADVISOR_INTENTS),
        "risk_flag_enum": list(ADVISOR_RISK_FLAGS),
        "required_json_keys": [
            "intent",
            "confidence",
            "needs_clarification",
            "risk_flags",
            "target_mentions",
            "clarification_question",
            "reason",
        ],
        "required_output_shape": {
            "intent": "read_only",
            "confidence": 0.0,
            "needs_clarification": False,
            "risk_flags": [],
            "target_mentions": [],
            "clarification_question": None,
            "reason": "short reason",
        },
        "schema_rules": [
            "Return one JSON object only.",
            "Always include all required_json_keys.",
            "intent must be one exact value from intent_enum.",
            "risk_flags must contain only values from risk_flag_enum.",
            "target_mentions must always be a list, even if empty.",
            "risk_flags must always be a list, even if empty.",
            "Never put block names, parameter names, endpoint strings, or target phrases in risk_flags.",
            "Put block names, parameter names, endpoint strings, and target phrases only in target_mentions.",
            "Never output tool names, transaction fields, params, save paths, YAML, or connection payloads.",
            "If unsure, use intent unknown, confidence 0.25, risk_flags [], needs_clarification true.",
        ],
        "intent_mapping_rules": [
            "For search/find/describe/summarize/explain/what is/show me questions, use intent read_only.",
            "Do not output search, find, describe, summarize, explain, inspect, or show as intent values.",
            "For validate/check whether valid/check whether compiles/compile? questions, use intent validate.",
            "For ordinary check/show/read-only wording that is not validation, use read_only.",
            "For explicit save/write/export-to-file/store-copy wording, use intent save and risk flag save_request.",
            "Never mark save_request unless explicit save/write/export-to-file/store-copy intent is present.",
            "For preview/dry-run/show first/do not apply/don't commit wording, use intent preview and risk flag negated_apply.",
            "For raw YAML or direct .grc text edits, use intent unsupported with raw_yaml and unsupported_request.",
            "For export Python script/code generation, use intent unsupported with unsupported_request.",
            "For undo or redo, use intent unsupported or uncertain_mutation with no executable mutation intent.",
            "For vague fix/repair/rewire everything/topology repair, use uncertain_mutation with vague_topology.",
            "For vague better rewires, missing targets, or invalid endpoint wording, use uncertain_mutation or ambiguous.",
            "Never mark vague_topology unless the user asks to change/fix/repair topology or wiring.",
            "For mutate block_uid or UID free-form wording, use uncertain_mutation with block_uid_freeform.",
            "For remove connection not block, use disconnect only if exact connection ID or endpoints are present; otherwise use ambiguous or uncertain_mutation.",
            "For compatible block/filter insertion without exact placement, use uncertain_mutation with missing_placement.",
        ],
        "complete_examples": [
            _complete_example(
                prompt="Summarize this graph.",
                intent="read_only",
                confidence=0.96,
                needs_clarification=False,
                risk_flags=[],
                target_mentions=[],
                reason="Summary request.",
            ),
            _complete_example(
                prompt="Find throttle blocks.",
                intent="read_only",
                confidence=0.95,
                needs_clarification=False,
                risk_flags=[],
                target_mentions=["throttle blocks"],
                reason="Search request.",
            ),
            _complete_example(
                prompt="Describe qtgui_time_sink_x.",
                intent="read_only",
                confidence=0.94,
                needs_clarification=False,
                risk_flags=[],
                target_mentions=["qtgui_time_sink_x"],
                reason="Describe request.",
            ),
            _complete_example(
                prompt="Validate this graph.",
                intent="validate",
                confidence=0.96,
                needs_clarification=False,
                risk_flags=[],
                target_mentions=[],
                reason="Validation request.",
            ),
            _complete_example(
                prompt="Check whether this compiles.",
                intent="validate",
                confidence=0.92,
                needs_clarification=False,
                risk_flags=[],
                target_mentions=[],
                reason="Compile check.",
            ),
            _complete_example(
                prompt="Save a copy.",
                intent="save",
                confidence=0.9,
                needs_clarification=False,
                risk_flags=["save_request"],
                target_mentions=["copy"],
                reason="Explicit save.",
            ),
            _complete_example(
                prompt="previw changing samp_rate dont apply",
                intent="preview",
                confidence=0.88,
                needs_clarification=False,
                risk_flags=["negated_apply"],
                target_mentions=["samp_rate"],
                reason="Preview only.",
            ),
            _complete_example(
                prompt="Patch raw YAML.",
                intent="unsupported",
                confidence=0.98,
                needs_clarification=True,
                risk_flags=["raw_yaml", "unsupported_request"],
                target_mentions=["raw YAML"],
                clarification_question="Raw YAML edits are unsupported. What bounded graph action do you want?",
                reason="Raw YAML request.",
            ),
            _complete_example(
                prompt="Export this as a standalone Python script.",
                intent="unsupported",
                confidence=0.95,
                needs_clarification=True,
                risk_flags=["unsupported_request"],
                target_mentions=["Python script"],
                clarification_question="Python export is unsupported. What bounded graph task should I perform?",
                reason="Unsupported export.",
            ),
            _complete_example(
                prompt="Undo the last change.",
                intent="unsupported",
                confidence=0.75,
                needs_clarification=True,
                risk_flags=["unsupported_request"],
                target_mentions=["last change"],
                clarification_question="Undo is unsupported here. What explicit graph action should I take?",
                reason="Unsupported undo.",
            ),
            _complete_example(
                prompt="Fix the graph.",
                intent="uncertain_mutation",
                confidence=0.86,
                needs_clarification=True,
                risk_flags=["vague_topology"],
                target_mentions=["graph"],
                clarification_question="What exact bounded graph edit should be performed?",
                reason="Vague repair.",
            ),
            _complete_example(
                prompt="Mutate block_uid abc.",
                intent="uncertain_mutation",
                confidence=0.92,
                needs_clarification=True,
                risk_flags=["block_uid_freeform"],
                target_mentions=["block_uid abc"],
                clarification_question="Choose a resolved graph target instead of free-form block_uid mutation.",
                reason="Free-form UID.",
            ),
            _complete_example(
                prompt="Remove connection not block: src_0:0->dst_0:0.",
                intent="disconnect",
                confidence=0.9,
                needs_clarification=False,
                risk_flags=[],
                target_mentions=["src_0:0->dst_0:0"],
                reason="Exact disconnect.",
            ),
            _complete_example(
                prompt="Remove connection not block.",
                intent="ambiguous",
                confidence=0.64,
                needs_clarification=True,
                risk_flags=["missing_target"],
                target_mentions=[],
                clarification_question="Which exact connection should be removed?",
                reason="Missing target.",
            ),
            _complete_example(
                prompt="Set the parameter on the sink.",
                intent="ambiguous",
                confidence=0.68,
                needs_clarification=True,
                risk_flags=["missing_target"],
                target_mentions=["sink"],
                clarification_question="Which sink and parameter should be changed?",
                reason="Missing parameter target.",
            ),
            _complete_example(
                prompt="Insert a compatible filter into this graph.",
                intent="uncertain_mutation",
                confidence=0.72,
                needs_clarification=True,
                risk_flags=["missing_placement"],
                target_mentions=["compatible filter"],
                clarification_question="Where should the filter be inserted?",
                reason="Missing placement.",
            ),
            _complete_example(
                prompt="Rewire to a better sink.",
                intent="uncertain_mutation",
                confidence=0.7,
                needs_clarification=True,
                risk_flags=["vague_topology", "missing_target"],
                target_mentions=["better sink"],
                clarification_question="Which exact old and new endpoints should be used?",
                reason="Vague rewire.",
            ),
            _complete_example(
                prompt="Rewire matrix output to an invalid endpoint.",
                intent="uncertain_mutation",
                confidence=0.74,
                needs_clarification=True,
                risk_flags=["missing_target"],
                target_mentions=["invalid endpoint"],
                clarification_question="Provide exact valid endpoints for the rewire.",
                reason="Invalid endpoint wording.",
            ),
            _complete_example(
                prompt="try changing samp_rate but don't commit",
                intent="preview",
                confidence=0.88,
                needs_clarification=False,
                risk_flags=["negated_apply"],
                target_mentions=["samp_rate"],
                reason="Do not commit.",
            ),
            _complete_example(
                prompt="patch raw YAML",
                intent="unsupported",
                confidence=0.98,
                needs_clarification=True,
                risk_flags=["raw_yaml", "unsupported_request"],
                target_mentions=["raw YAML"],
                clarification_question="Raw YAML edits are unsupported. What bounded graph action do you want?",
                reason="Raw YAML request.",
            ),
        ],
    }
    system = (
        "You are a strict enum JSON classifier for a GNU Radio Companion assistant. "
        "Use only enum values from the prompt. Output exactly the required JSON object. "
        "Your output is shadow telemetry only and cannot authorize mutation."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(prompt_payload, sort_keys=True)},
    ]


def _complete_example(
    *,
    prompt: str,
    intent: str,
    confidence: float,
    needs_clarification: bool,
    risk_flags: list[str],
    target_mentions: list[str] | None = None,
    clarification_question: str | None = None,
    reason: str,
) -> dict[str, Any]:
    return {
        "prompt": prompt,
        "output": {
            "intent": intent,
            "confidence": confidence,
            "needs_clarification": needs_clarification,
            "risk_flags": risk_flags,
            "target_mentions": target_mentions or [],
            "clarification_question": clarification_question,
            "reason": reason,
        },
    }


def _build_advisor_messages_v3(
    *,
    user_message: str,
    session_summary: dict[str, Any],
    pending_clarification: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build a schema-adherence advisor prompt for narrow v3 shadow experiments."""
    required_output_template = {
        "intent": "one exact value from intent_labels",
        "confidence": 0.0,
        "needs_clarification": True,
        "risk_flags": [],
        "target_mentions": [],
        "clarification_question": None,
        "reason": "short reason",
    }
    prompt_payload = {
        "task": (
            "Classify only the user's explicit intent. This is a shadow diagnostic; "
            "you cannot authorize tools, tool arguments, transactions, saves, or graph mutation."
        ),
        "user_prompt": _bounded_text(user_message, limit=1000),
        "active_graph_summary": session_summary,
        "pending_clarification": _compact_pending_clarification(pending_clarification),
        "intent_labels": list(ADVISOR_INTENTS),
        "risk_flags_enum": list(ADVISOR_RISK_FLAGS),
        "required_output_template": required_output_template,
        "schema_hard_rules": [
            "Return one JSON object only; no markdown and no prose outside JSON.",
            "Always include every key from required_output_template.",
            "Always include target_mentions as a JSON array, even when empty.",
            "Always include risk_flags as a JSON array, even when empty.",
            "Never invent intents outside intent_labels.",
            "Never invent risk flags outside risk_flags_enum.",
            "Do not use unknown, validate, disconnect, search, find, summarize, describe, explain, or rewire as risk flags.",
            "If unsure, use intent unknown, confidence <= 0.35, risk_flags [], needs_clarification true.",
            "Keep reason under 20 words.",
            "Keep target_mentions short and copied from the user prompt.",
        ],
        "classification_rules": [
            "Map summarize/find/search/describe/explain/what/show questions to read_only.",
            "Map validate/check/compile/validity questions to validate.",
            "Map explicit save/write/persist/store/save a copy wording to save and risk flag save_request.",
            "Map preview/dry-run/show first/do not apply/don't commit/not mutate wording to preview and risk flag negated_apply.",
            "Map exact parameter changes to param_edit.",
            "Map enable/disable/turn on/turn off to state_edit.",
            "Map add/create variable wording to add_variable.",
            "Map exact connection removal wording to disconnect.",
            "Map exact rewire/move connection wording to rewire.",
            "Map raw YAML/.grc text editing to unsupported with raw_yaml.",
            "Map vague fix/repair/rewire everything/topology repair to uncertain_mutation with vague_topology.",
            "Map explicit free-form block_uid or UID mutation wording to uncertain_mutation with block_uid_freeform.",
            "For remove connection not block: use disconnect if the prompt includes an exact connection or endpoints; otherwise use ambiguous or uncertain_mutation.",
        ],
        "complete_examples": [
            _complete_example(
                prompt="Summarize this graph.",
                intent="read_only",
                confidence=0.95,
                needs_clarification=False,
                risk_flags=[],
                target_mentions=[],
                reason="Summary request.",
            ),
            _complete_example(
                prompt="Find throttle blocks.",
                intent="read_only",
                confidence=0.92,
                needs_clarification=False,
                risk_flags=[],
                target_mentions=["throttle blocks"],
                reason="Search request.",
            ),
            _complete_example(
                prompt="Describe qtgui_time_sink_x.",
                intent="read_only",
                confidence=0.92,
                needs_clarification=False,
                risk_flags=[],
                target_mentions=["qtgui_time_sink_x"],
                reason="Description request.",
            ),
            _complete_example(
                prompt="Validate this graph.",
                intent="validate",
                confidence=0.95,
                needs_clarification=False,
                risk_flags=[],
                target_mentions=[],
                reason="Validation request.",
            ),
            _complete_example(
                prompt="Check whether this compiles.",
                intent="validate",
                confidence=0.9,
                needs_clarification=False,
                risk_flags=[],
                target_mentions=[],
                reason="Compile check.",
            ),
            _complete_example(
                prompt="Save a copy.",
                intent="save",
                confidence=0.9,
                needs_clarification=False,
                risk_flags=["save_request"],
                target_mentions=["copy"],
                reason="Explicit save request.",
            ),
            _complete_example(
                prompt="previw changing samp_rate dont apply",
                intent="preview",
                confidence=0.88,
                needs_clarification=False,
                risk_flags=["negated_apply"],
                target_mentions=["samp_rate"],
                reason="Preview only.",
            ),
            _complete_example(
                prompt="Patch raw YAML.",
                intent="unsupported",
                confidence=0.96,
                needs_clarification=True,
                risk_flags=["raw_yaml", "unsupported_request"],
                target_mentions=["raw YAML"],
                clarification_question="Raw YAML edits are unsupported. What bounded graph action do you want?",
                reason="Raw YAML request.",
            ),
            _complete_example(
                prompt="Fix the graph.",
                intent="uncertain_mutation",
                confidence=0.85,
                needs_clarification=True,
                risk_flags=["vague_topology"],
                target_mentions=["graph"],
                clarification_question="What exact bounded graph edit should be performed?",
                reason="Vague repair request.",
            ),
            _complete_example(
                prompt="Mutate block_uid abc.",
                intent="uncertain_mutation",
                confidence=0.92,
                needs_clarification=True,
                risk_flags=["block_uid_freeform"],
                target_mentions=["block_uid abc"],
                clarification_question="Choose a resolved graph target instead of free-form block_uid mutation.",
                reason="Free-form UID mutation.",
            ),
            _complete_example(
                prompt="Remove connection not block: src_0:0->dst_0:0.",
                intent="disconnect",
                confidence=0.88,
                needs_clarification=False,
                risk_flags=[],
                target_mentions=["src_0:0->dst_0:0"],
                reason="Exact disconnect.",
            ),
            _complete_example(
                prompt="Remove connection not block.",
                intent="ambiguous",
                confidence=0.65,
                needs_clarification=True,
                risk_flags=["missing_target"],
                target_mentions=[],
                clarification_question="Which exact connection should be removed?",
                reason="Missing connection target.",
            ),
        ],
    }
    system = (
        "You are a strict JSON intent classifier for a GNU Radio Companion assistant. "
        "Use only the provided enum values. Your output is telemetry only and cannot "
        "authorize mutation. Never output tool calls, transactions, params, save paths, "
        "repair plans, YAML, connection payloads, or block recipes."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(prompt_payload, sort_keys=True)},
    ]


def _build_advisor_messages_v2(
    *,
    user_message: str,
    session_summary: dict[str, Any],
    pending_clarification: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build a lower-priming advisor prompt for v2 shadow experiments."""
    prompt_payload = {
        "task": "Classify the user's explicit intent. Do not authorize tools or mutation.",
        "user_prompt": _bounded_text(user_message, limit=1000),
        "active_graph_summary": session_summary,
        "pending_clarification": _compact_pending_clarification(pending_clarification),
        "intent_labels": list(ADVISOR_INTENTS),
        "classification_rules": [
            "Return exactly one intent label from intent_labels.",
            "Prefer read_only for what/show/summarize/find/search/describe/explain questions.",
            "Prefer validate only for explicit validate/check/compile/validity requests.",
            "Prefer preview when the user asks to preview, show first, try without commit, dry-run, or not apply.",
            "Prefer save only when the user explicitly asks to save, write, persist, store, or save a copy.",
            "Prefer uncertain_mutation only for vague mutation verbs without enough target/action detail.",
            "If intent is unclear, use unknown with low confidence instead of inventing risk flags.",
            "Risk flags are optional; an empty risk_flags array is normal.",
            "Emit a risk flag only when directly supported by words in the user prompt.",
            "Do not infer block_uid_freeform unless the prompt literally mentions block_uid, uid, or a UID-like token.",
            "Do not infer vague_topology for ordinary read-only questions about graph structure.",
            "Do not infer save_request unless explicit save/write/export/persist/store wording is present.",
            "target_mentions must be bounded strings copied or paraphrased from the user prompt only.",
        ],
        "risk_flags": {
            "negated_apply": "only if preview/do-not-apply/don't commit wording is explicit",
            "save_request": "only if save/write/export/persist/store/copy-to-file wording is explicit",
            "raw_yaml": "only if raw .grc, YAML, source text, or direct file editing is explicit",
            "vague_topology": "only if fix/repair/rewire/wire topology is requested vaguely",
            "block_uid_freeform": "only if block_uid, uid, or UID-like text is explicitly used as a mutation handle",
            "duplicate_target": "only if duplicate/same-name target ambiguity is explicit",
            "missing_target": "only if the prompt asks for mutation but omits the target",
            "missing_placement": "only if insertion/rewire lacks placement or endpoint detail",
            "unsupported_request": "only if request is outside bounded GRC inspect/edit/validate/save work",
        },
        "few_shot_examples": [
            {
                "prompt": "What blocks are in this graph?",
                "intent": "read_only",
                "risk_flags": [],
                "needs_clarification": False,
            },
            {
                "prompt": "previw changing samp_rate to 48000, dont apply",
                "intent": "preview",
                "risk_flags": ["negated_apply"],
                "needs_clarification": False,
            },
            {
                "prompt": "Change samp_rate to 48000.",
                "intent": "param_edit",
                "risk_flags": [],
                "needs_clarification": False,
            },
            {
                "prompt": "Save a copy to /tmp/out.grc.",
                "intent": "save",
                "risk_flags": ["save_request"],
                "needs_clarification": False,
            },
            {
                "prompt": "Patch the raw YAML directly.",
                "intent": "unsupported",
                "risk_flags": ["raw_yaml", "unsupported_request"],
                "needs_clarification": True,
            },
            {
                "prompt": "Fix the topology.",
                "intent": "uncertain_mutation",
                "risk_flags": ["vague_topology"],
                "needs_clarification": True,
            },
            {
                "prompt": "Use block_uid abc123 to edit that block.",
                "intent": "uncertain_mutation",
                "risk_flags": ["block_uid_freeform"],
                "needs_clarification": True,
            },
            {
                "prompt": "Describe the topology of this receiver.",
                "intent": "read_only",
                "risk_flags": [],
                "needs_clarification": False,
            },
        ],
        "output_schema": {
            "intent": "one listed intent label",
            "confidence": "number from 0.0 to 1.0",
            "needs_clarification": "boolean",
            "risk_flags": "array of listed risk flags; empty is normal",
            "target_mentions": "array of short bounded strings from the user text",
            "clarification_question": "short string or null",
            "reason": "short explanation",
        },
    }
    system = (
        "You are a shadow natural-language intent classifier for a GNU Radio "
        "Companion assistant. Classify only explicit user intent. Return strict "
        "JSON only. Do not output tool calls, transactions, params, save paths, "
        "repair plans, recipes, YAML, connection payloads, or block recipes. You "
        "cannot authorize mutation; runtime policy is deterministic."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(prompt_payload, sort_keys=True)},
    ]


def parse_advisor_json(raw_content: str | None) -> dict[str, Any]:
    if not isinstance(raw_content, str) or not raw_content.strip():
        raise AdvisorValidationError("advisor returned empty content")
    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise AdvisorValidationError("advisor returned malformed JSON") from exc
    if not isinstance(parsed, dict):
        raise AdvisorValidationError("advisor JSON must be an object")
    return parsed


def validate_advisor_payload(payload: dict[str, Any]) -> AdvisorResult:
    """Validate strict advisor output and reject mutation-shaped payloads."""
    _reject_forbidden_payload(payload)
    required_keys = {
        "intent",
        "confidence",
        "needs_clarification",
        "risk_flags",
        "target_mentions",
        "clarification_question",
        "reason",
    }
    unknown_keys = set(payload) - required_keys
    missing_keys = required_keys - set(payload)
    if unknown_keys:
        raise AdvisorValidationError(
            f"advisor output contains unsupported key(s): {sorted(unknown_keys)}"
        )
    if missing_keys:
        raise AdvisorValidationError(
            f"advisor output missing required key(s): {sorted(missing_keys)}"
        )

    intent = payload["intent"]
    if intent not in ADVISOR_INTENTS:
        raise AdvisorValidationError(f"unknown advisor intent: {intent!r}")

    confidence = payload["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        raise AdvisorValidationError("advisor confidence must be numeric")
    confidence = float(confidence)
    if confidence < 0.0 or confidence > 1.0:
        raise AdvisorValidationError("advisor confidence must be between 0 and 1")

    needs_clarification = payload["needs_clarification"]
    if not isinstance(needs_clarification, bool):
        raise AdvisorValidationError("needs_clarification must be boolean")

    risk_flags = payload["risk_flags"]
    if not isinstance(risk_flags, list) or not all(isinstance(item, str) for item in risk_flags):
        raise AdvisorValidationError("risk_flags must be a string array")
    unknown_flags = sorted(set(risk_flags) - set(ADVISOR_RISK_FLAGS))
    if unknown_flags:
        raise AdvisorValidationError(f"unknown advisor risk flag(s): {unknown_flags}")

    target_mentions = payload["target_mentions"]
    if not isinstance(target_mentions, list) or not all(
        isinstance(item, str) for item in target_mentions
    ):
        raise AdvisorValidationError("target_mentions must be a string array")
    bounded_targets = tuple(
        _bounded_text(item, limit=_MAX_TARGET_CHARS)
        for item in target_mentions[:_MAX_TARGET_MENTIONS]
    )

    clarification_question = payload["clarification_question"]
    if clarification_question is not None and not isinstance(clarification_question, str):
        raise AdvisorValidationError("clarification_question must be string or null")
    if isinstance(clarification_question, str):
        clarification_question = _bounded_text(
            clarification_question,
            limit=_MAX_QUESTION_CHARS,
        )

    reason = payload["reason"]
    if not isinstance(reason, str):
        raise AdvisorValidationError("reason must be a string")

    return AdvisorResult(
        intent=intent,
        confidence=confidence,
        needs_clarification=needs_clarification,
        risk_flags=tuple(risk_flags),
        target_mentions=bounded_targets,
        clarification_question=clarification_question,
        reason=_bounded_text(reason, limit=_MAX_REASON_CHARS),
    )


def validate_mode_advisor_payload(payload: dict[str, Any]) -> AdvisorModeResult:
    """Validate the strict v5 mode-router output."""
    _reject_forbidden_payload(payload)
    if set(payload) != {"mode"}:
        raise AdvisorValidationError("mode advisor output must contain only the mode key")
    mode = payload.get("mode")
    if mode not in ADVISOR_MODES:
        raise AdvisorValidationError(f"unknown advisor mode: {mode!r}")
    return AdvisorModeResult(mode=mode)


def validate_readiness_mode_advisor_payload(
    payload: dict[str, Any],
    *,
    prompt_version: str = "v9",
) -> AdvisorReadinessModeResult:
    """Validate strict readiness+mode output."""
    _reject_forbidden_payload(payload)
    if prompt_version in {"v10", "v11"}:
        required_key = "decision" if prompt_version == "v10" else "readiness"
        if set(payload) != {required_key, "mode"}:
            raise AdvisorValidationError(
                f"readiness mode advisor output must contain only {required_key} and mode"
            )
        decision_field = payload.get(required_key)
        if decision_field not in ADVISOR_DECISIONS:
            raise AdvisorValidationError(
                f"unknown advisor decision: {decision_field!r}"
            )
        mode = payload.get("mode")
        allowed_modes = {
            "read_only",
            "preview",
            "edit",
            "disconnect",
            "rewire",
            "insert",
            "validate",
            "save",
            "none",
        }
        if mode not in allowed_modes:
            raise AdvisorValidationError(f"unknown readiness advisor mode: {mode!r}")
        ready_modes = {
            "read_only",
            "edit",
            "disconnect",
            "rewire",
            "insert",
            "validate",
            "save",
        }
        if decision_field == "ready" and mode not in ready_modes:
            raise AdvisorValidationError(
                "ready decision requires executable non-preview mode"
            )
        if decision_field == "preview" and mode != "preview":
            raise AdvisorValidationError("preview decision requires mode=preview")
        if decision_field in {"clarify", "unsupported"} and mode != "none":
            raise AdvisorValidationError(
                "decision requires mode=none for clarify or unsupported"
            )
        if decision_field == "ready" and mode == "none":
            raise AdvisorValidationError("ready decision requires executable mode")
        return AdvisorReadinessModeResult(readiness=decision_field, mode=mode)

    if prompt_version != "v9":
        raise AdvisorValidationError(
            f"unknown readiness mode advisor schema version: {prompt_version!r}"
        )
    if set(payload) != {"readiness", "mode"}:
        raise AdvisorValidationError(
            "readiness mode advisor output must contain only readiness and mode"
        )
    readiness = payload.get("readiness")
    mode = payload.get("mode")
    if readiness not in ADVISOR_READINESS:
        raise AdvisorValidationError(f"unknown advisor readiness: {readiness!r}")
    allowed_modes = {
        "read_only",
        "preview",
        "edit",
        "disconnect",
        "rewire",
        "insert",
        "validate",
        "save",
        "none",
    }
    if mode not in allowed_modes:
        raise AdvisorValidationError(f"unknown readiness advisor mode: {mode!r}")
    return AdvisorReadinessModeResult(readiness=readiness, mode=mode)


def canonicalize_advisor_payload(
    payload: dict[str, Any],
    *,
    user_message: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Canonicalize narrow enum aliases before strict validation.

    This is intentionally limited to intent/risk enum aliases for shadow
    experiments. It never creates tools, transactions, params, save paths, or
    mutation arguments, and it does not repair missing required fields.
    """
    try:
        validate_advisor_payload(payload)
        schema_valid_before = True
    except AdvisorValidationError:
        schema_valid_before = False

    canonical = dict(payload)
    original_intent = payload.get("intent")
    canonical_intent = original_intent
    if isinstance(original_intent, str):
        lowered_intent = original_intent.strip().lower()
        if lowered_intent in {
            "search",
            "find",
            "describe",
            "explain",
            "summarize",
            "inspect",
        }:
            canonical_intent = "read_only"
        elif lowered_intent == "check":
            canonical_intent = "read_only"
        elif lowered_intent in ADVISOR_INTENTS:
            canonical_intent = lowered_intent
        canonical["intent"] = canonical_intent

    dropped_risk_flags: list[str] = []
    original_flags = payload.get("risk_flags")
    if isinstance(original_flags, list):
        canonical_flags: list[Any] = []
        for flag in original_flags:
            if not isinstance(flag, str):
                canonical_flags.append(flag)
                continue
            lowered_flag = flag.strip().lower()
            if lowered_flag in {
                "unknown",
                "validate",
                "search",
                "find",
                "describe",
                "disconnect",
            }:
                dropped_risk_flags.append(flag)
                continue
            if lowered_flag in ADVISOR_RISK_FLAGS:
                canonical_flags.append(lowered_flag)
                continue
            canonical_flags.append(flag)
        canonical["risk_flags"] = canonical_flags

    return canonical, {
        "original_intent": original_intent,
        "canonical_intent": canonical_intent,
        "dropped_risk_flags": dropped_risk_flags,
        "schema_valid_before_canonicalization": schema_valid_before,
        "schema_valid_after_canonicalization": False,
    }


def compile_advisor_plan(
    advisor_result: AdvisorResult,
    *,
    user_message: str,
) -> AdvisorCandidatePlan:
    """Convert legacy rich advisor output into a candidate plan for telemetry only."""
    del user_message
    flags = set(advisor_result.risk_flags)

    if (
        "raw_yaml" in flags
        or "unsupported_request" in flags
        or advisor_result.intent == "unsupported"
    ):
        return _candidate("unsupported", (), (), True, advisor_result.risk_flags)
    if "block_uid_freeform" in flags:
        return _candidate(
            INTENT_UNCERTAIN_MUTATION,
            (),
            (),
            True,
            advisor_result.risk_flags,
        )
    if "vague_topology" in flags or "missing_placement" in flags:
        return _candidate(
            INTENT_UNCERTAIN_MUTATION,
            (),
            (),
            True,
            advisor_result.risk_flags,
        )
    if "duplicate_target" in flags or "missing_target" in flags:
        return _candidate(INTENT_AMBIGUOUS, (), (), True, advisor_result.risk_flags)
    if "negated_apply" in flags or advisor_result.intent == "preview":
        return _candidate(
            INTENT_PREVIEW,
            ("propose_edit",),
            ("update_params", "update_states", "add_block", "remove_block"),
            advisor_result.needs_clarification,
            advisor_result.risk_flags,
        )

    intent_map: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
        "read_only": ("read_only", READ_ONLY_ADVISOR_TOOLS, ()),
        "param_edit": (INTENT_PARAM_EDIT, ("apply_edit",), ("update_params",)),
        "state_edit": (INTENT_STATE_EDIT, ("apply_edit",), ("update_states",)),
        "add_variable": (INTENT_ADD_VARIABLE, ("apply_edit",), ("add_block",)),
        "disconnect": (INTENT_DISCONNECT, ("remove_connection",), ("remove_connection",)),
        "rewire": (INTENT_REWIRE, ("rewire_connection",), ("remove_connection", "add_connection")),
        "insertion": (INTENT_INSERTION, ("auto_insert_block",), ("add_block",)),
        "save": ("save", ("save_graph",), ()),
        "validate": ("validate", ("validate_graph",), ()),
        "ambiguous": (INTENT_AMBIGUOUS, (), ()),
        "uncertain_mutation": (INTENT_UNCERTAIN_MUTATION, (), ()),
        "unknown": (INTENT_UNKNOWN, (), ()),
    }
    mapped = intent_map.get(advisor_result.intent, (INTENT_UNKNOWN, (), ()))
    return _candidate(
        mapped[0],
        mapped[1],
        mapped[2],
        advisor_result.needs_clarification or advisor_result.intent in {"ambiguous", "unknown"},
        advisor_result.risk_flags,
    )


def compile_mode_advisor_plan(
    mode_result: AdvisorModeResult,
    *,
    user_message: str,
) -> AdvisorModeCandidate:
    """Compile mode advisor output into a telemetry-only tool class."""
    del user_message
    mode = mode_result.mode
    if mode == "read_only":
        return _mode_candidate(mode, READ_ONLY_ADVISOR_TOOLS, False)
    if mode == "preview":
        return _mode_candidate(mode, ("propose_edit",), False)
    if mode == "edit":
        return _mode_candidate(mode, ("apply_edit",), False)
    if mode == "disconnect":
        return _mode_candidate(mode, ("remove_connection",), False)
    if mode == "rewire":
        return _mode_candidate(mode, ("rewire_connection",), False)
    if mode == "insert":
        return _mode_candidate(mode, ("auto_insert_block",), False)
    if mode == "validate":
        return _mode_candidate(mode, ("validate_graph",), False)
    if mode == "save":
        return _mode_candidate(mode, ("save_graph",), False)
    if mode in {"clarify", "unsupported"}:
        return _mode_candidate(mode, (), True)
    return _mode_candidate("clarify", (), True)


def compile_advisor_permission(
    *,
    user_message: str,
    advisor_mode: str,
    session_summary: dict[str, Any] | None = None,
) -> AdvisorPermissionDecision:
    """Map advisor mode to structural permission without parsing user wording.

    Advisor owns semantic intent classification. This compiler validates only
    the mode enum and maps that mode to a bounded tool class; runtime schema,
    route, preflight, grcc, rollback, and save-state checks still enforce graph
    safety.
    """
    del user_message, session_summary
    if advisor_mode not in ADVISOR_MODES:
        return _permission_decision(
            advisor_mode=advisor_mode,
            permission="ask_clarification",
            mode="clarify",
            override_reason="unknown_advisor_mode",
            allowed_tools=(),
        )

    if advisor_mode == "read_only":
        return _permission_decision(
            advisor_mode=advisor_mode,
            permission="allow_readonly",
            mode="read_only",
            allowed_tools=READ_ONLY_ADVISOR_TOOLS,
        )
    if advisor_mode == "validate":
        return _permission_decision(
            advisor_mode=advisor_mode,
            permission="allow_readonly",
            mode="validate",
            allowed_tools=("validate_graph",),
        )
    if advisor_mode == "preview":
        return _permission_decision(
            advisor_mode=advisor_mode,
            permission="allow_preview",
            mode="preview",
            allowed_tools=("propose_edit",),
        )
    if advisor_mode == "edit":
        return _permission_decision(
            advisor_mode=advisor_mode,
            permission="allow_mutation_narrow",
            mode="edit",
            allowed_tools=("apply_edit",),
        )
    if advisor_mode == "disconnect":
        return _permission_decision(
            advisor_mode=advisor_mode,
            permission="allow_mutation_narrow",
            mode="disconnect",
            allowed_tools=("remove_connection",),
        )
    if advisor_mode == "rewire":
        return _permission_decision(
            advisor_mode=advisor_mode,
            permission="allow_mutation_narrow",
            mode="rewire",
            allowed_tools=("rewire_connection",),
        )
    if advisor_mode == "insert":
        return _permission_decision(
            advisor_mode=advisor_mode,
            permission="allow_mutation_narrow",
            mode="insert",
            allowed_tools=("auto_insert_block",),
        )
    if advisor_mode == "save":
        return _permission_decision(
            advisor_mode=advisor_mode,
            permission="allow_mutation_narrow",
            mode="save",
            allowed_tools=("save_graph",),
        )
    if advisor_mode == "unsupported":
        return _permission_decision(
            advisor_mode=advisor_mode,
            permission="deny_unsupported",
            mode="unsupported",
            allowed_tools=(),
        )
    return _permission_decision(
        advisor_mode=advisor_mode,
        permission="ask_clarification",
        mode="clarify",
        allowed_tools=(),
    )


def advisor_suggests_unsafe_mutation(candidate: AdvisorCandidatePlan) -> bool:
    """Return whether a candidate violates promotion safety boundaries."""
    flags = set(candidate.risk_flags)
    if flags & {
        "negated_apply",
        "raw_yaml",
        "vague_topology",
        "block_uid_freeform",
        "unsupported_request",
    }:
        return bool(set(candidate.allowed_tools) & {"apply_edit", "remove_connection", "rewire_connection", "save_graph"})
    return False


def _candidate(
    intent: str,
    allowed_tools: tuple[str, ...],
    expected_op_types: tuple[str, ...],
    requires_clarification: bool,
    risk_flags: tuple[str, ...],
) -> AdvisorCandidatePlan:
    return AdvisorCandidatePlan(
        intent=intent,
        allowed_tools=allowed_tools,
        expected_op_types=expected_op_types,
        requires_clarification=requires_clarification,
        risk_flags=risk_flags,
    )


def _mode_candidate(
    mode: str,
    allowed_tools: tuple[str, ...],
    requires_clarification: bool,
) -> AdvisorModeCandidate:
    return AdvisorModeCandidate(
        mode=mode,
        allowed_tools=allowed_tools,
        requires_clarification=requires_clarification,
    )


def _permission_decision(
    *,
    advisor_mode: str,
    permission: str,
    mode: str,
    allowed_tools: tuple[str, ...],
    override_reason: str | None = None,
) -> AdvisorPermissionDecision:
    if permission not in ADVISOR_PERMISSIONS:
        raise AdvisorValidationError(f"unknown advisor permission: {permission!r}")
    return AdvisorPermissionDecision(
        advisor_mode=advisor_mode,
        compiled_permission=permission,
        compiled_mode=mode,
        override_applied=mode != advisor_mode or override_reason is not None,
        override_reason=override_reason,
        safe_to_expose_tools=permission
        in {"allow_readonly", "allow_preview", "allow_mutation_narrow"},
        allowed_tools=allowed_tools,
    )


def _mode_json_schema_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "turnplan_mode",
            "schema": {
                "type": "object",
                "properties": {"mode": {"type": "string", "enum": list(ADVISOR_MODES)}},
                "required": ["mode"],
                "additionalProperties": False,
            },
        },
    }


def _readiness_mode_json_schema_response_format(
    *,
    prompt_version: str = "v9",
) -> dict[str, Any]:
    if prompt_version in {"v10", "v11"}:
        required_key = "decision" if prompt_version == "v10" else "readiness"
        schema_name = (
            "turnplan_readiness_mode_v10"
            if prompt_version == "v10"
            else "turnplan_readiness_mode_v11"
        )
        return {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "schema": {
                    "type": "object",
                    "properties": {
                        required_key: {
                            "type": "string",
                            "enum": list(ADVISOR_DECISIONS),
                        },
                        "mode": {
                            "type": "string",
                            "enum": [
                                "read_only",
                                "preview",
                                "edit",
                                "disconnect",
                                "rewire",
                                "insert",
                                "validate",
                                "save",
                                "none",
                            ],
                        },
                    },
                    "required": [required_key, "mode"],
                    "additionalProperties": False,
                },
            },
        }
    if prompt_version != "v9":
        raise AdvisorValidationError(
            f"unknown readiness mode schema version: {prompt_version!r}"
        )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "turnplan_readiness_mode",
            "schema": {
                "type": "object",
                "properties": {
                    "readiness": {
                        "type": "string",
                        "enum": list(ADVISOR_READINESS),
                    },
                    "mode": {
                        "type": "string",
                        "enum": [
                            "read_only",
                            "preview",
                            "edit",
                            "disconnect",
                            "rewire",
                            "insert",
                            "validate",
                            "save",
                            "none",
                        ],
                    },
                },
                "required": ["readiness", "mode"],
                "additionalProperties": False,
            },
        },
    }


def _extract_assistant_text(response: dict[str, Any]) -> str | None:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AdvisorValidationError("advisor response missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise AdvisorValidationError("advisor response choice must be object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise AdvisorValidationError("advisor response missing message")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return None


def _reject_forbidden_payload(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_ADVISOR_KEYS:
                raise AdvisorValidationError(
                    f"advisor output contains forbidden mutation-shaped key: {key}"
                )
            _reject_forbidden_payload(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_forbidden_payload(item)


def _compact_pending_clarification(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    options = payload.get("options")
    compact_options: list[dict[str, str]] = []
    if isinstance(options, list):
        for option in options[:4]:
            if not isinstance(option, dict):
                continue
            compact_options.append(
                {
                    "label": _bounded_text(str(option.get("label", "")), limit=8),
                    "title": _bounded_text(str(option.get("title", "")), limit=80),
                    "description": _bounded_text(str(option.get("description", "")), limit=160),
                }
            )
    return {
        "state_revision": payload.get("state_revision"),
        "option_count": len(options) if isinstance(options, list) else 0,
        "options": compact_options,
    }


def _bounded_string_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_bounded_text(str(item), limit=160) for item in value[:limit]]


def _bounded_text(value: str, *, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 3]}..."
