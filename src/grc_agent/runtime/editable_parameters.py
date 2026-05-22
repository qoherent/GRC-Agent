"""Graph-local editable parameter indexing and resolution."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from grc_agent.catalog.describe import _describe_block_with_root
from grc_agent.models import Block, Connection
from grc_agent.session_ops import connection_id as render_connection_id


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_NUMERIC_SHORTHAND_RE = re.compile(r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))([kKmM])\s*$")


@dataclass(frozen=True)
class EditableParameterCandidate:
    """One active graph parameter that can be offered as a mutation handle."""

    instance_name: str
    block_uid: str
    block_type: str
    block_label: str | None
    param_key: str
    param_label: str | None
    param_dtype: str | None
    param_default: Any
    param_options: tuple[Any, ...]
    param_option_labels: tuple[Any, ...]
    param_hide: str | None
    current_value: Any
    state_revision: int
    incoming_connections: tuple[str, ...]
    outgoing_connections: tuple[str, ...]

    @property
    def target_ref(self) -> dict[str, Any]:
        return {
            "block_uid": self.block_uid,
            "expected_instance_name": self.instance_name,
            "expected_block_type": self.block_type,
            "base_state_revision": self.state_revision,
        }

    def handle(self) -> dict[str, Any]:
        return {
            "instance_name": self.instance_name,
            "block_uid": self.block_uid,
            "block_type": self.block_type,
            "block_label": self.block_label,
            "param_key": self.param_key,
            "param_label": self.param_label,
            "param_dtype": self.param_dtype,
            "param_default": self.param_default,
            "param_options": list(self.param_options),
            "param_option_labels": list(self.param_option_labels),
            "param_hide": self.param_hide,
            "current_value": self.current_value,
            "target_ref": self.target_ref,
            "state_revision": self.state_revision,
        }

    def to_payload(
        self,
        *,
        include_target_refs: bool,
        include_connections: bool,
    ) -> dict[str, Any]:
        payload = {
            "instance_name": self.instance_name,
            "block_uid": self.block_uid,
            "block_type": self.block_type,
            "block_label": self.block_label,
            "param_key": self.param_key,
            "param_label": self.param_label,
            "current_value": self.current_value,
            "state_revision": self.state_revision,
        }
        if include_target_refs:
            payload["target_ref"] = self.target_ref
        if include_connections:
            payload["incoming_connections"] = list(self.incoming_connections)
            payload["outgoing_connections"] = list(self.outgoing_connections)
        return {key: value for key, value in payload.items() if value is not None}


@dataclass(frozen=True)
class SetParamResolution:
    """Resolution result for a natural set_param request."""

    instance_name: str | None
    param_key: str | None
    param_value: Any
    target_ref: dict[str, Any] | None
    expected_old_value: Any
    target_resolution: dict[str, Any] | None = None
    clarification: dict[str, Any] | None = None


def normalize_numeric_shorthand(value: Any) -> Any:
    """Normalize tightly scoped numeric shorthand used in graph parameters."""
    if not isinstance(value, str):
        return value
    match = _NUMERIC_SHORTHAND_RE.match(value)
    if match is None:
        return value
    number = Decimal(match.group(1))
    suffix = match.group(2).lower()
    multiplier = Decimal(1000 if suffix == "k" else 1000000)
    normalized = number * multiplier
    if normalized == normalized.to_integral_value():
        return str(int(normalized))
    return format(normalized.normalize(), "f")


def values_equivalent(left: Any, right: Any) -> bool:
    """Compare current/expected parameter values without broad unit conversion."""
    left_norm = normalize_numeric_shorthand(left)
    right_norm = normalize_numeric_shorthand(right)
    left_text = _compact_value(left_norm)
    right_text = _compact_value(right_norm)
    if left_text == right_text:
        return True
    try:
        return Decimal(left_text) == Decimal(right_text)
    except (InvalidOperation, ValueError):
        return False


def build_editable_parameter_candidates(
    session: Any,
    *,
    catalog_root: str | None = None,
    include_connections: bool = True,
) -> list[EditableParameterCandidate]:
    """Build mutation-ready parameter handles from loaded graph and catalog metadata."""
    flowgraph = getattr(session, "flowgraph", None)
    blocks = getattr(flowgraph, "blocks", None)
    connections = getattr(flowgraph, "connections", None)
    if not isinstance(blocks, list):
        return []
    if not isinstance(connections, list):
        connections = []

    metadata_by_block_type = _catalog_metadata_by_block_type(
        (block.block_type for block in blocks),
        catalog_root=catalog_root,
    )
    incoming, outgoing = _connection_summaries(connections) if include_connections else ({}, {})
    state_revision = getattr(session, "state_revision", 0)
    candidates: list[EditableParameterCandidate] = []
    for block in blocks:
        parameters = _parameter_map(block)
        if not parameters:
            continue
        metadata = metadata_by_block_type.get(block.block_type, {})
        param_metadata = metadata.get("__params__", {})
        for key, value in parameters.items():
            key_text = str(key)
            parameter_metadata = (
                param_metadata.get(key_text) if isinstance(param_metadata, dict) else {}
            )
            if not isinstance(parameter_metadata, dict):
                parameter_metadata = {}
            candidates.append(
                EditableParameterCandidate(
                    instance_name=block.instance_name,
                    block_uid=block.block_uid,
                    block_type=block.block_type,
                    block_label=_metadata_string(metadata.get("__block_label__")),
                    param_key=key_text,
                    param_label=_metadata_string(parameter_metadata.get("label")),
                    param_dtype=_metadata_string(parameter_metadata.get("dtype")),
                    param_default=parameter_metadata.get("default"),
                    param_options=tuple(_metadata_list(parameter_metadata.get("options"))),
                    param_option_labels=tuple(
                        _metadata_list(parameter_metadata.get("option_labels"))
                    ),
                    param_hide=_metadata_string(parameter_metadata.get("hide")),
                    current_value=value,
                    state_revision=state_revision,
                    incoming_connections=tuple(incoming.get(block.instance_name, ())),
                    outgoing_connections=tuple(outgoing.get(block.instance_name, ())),
                )
            )
    return candidates


def resolve_set_param_candidate(
    *,
    user_text: str,
    session: Any,
    catalog_root: str | None,
    operation_kind: str | None,
    instance_name: str | None,
    param_key: str | None,
    param_value: Any,
    target_ref: dict[str, Any] | None,
    expected_old_value: Any,
) -> SetParamResolution:
    """Resolve natural set_param targets only from active graph and catalog metadata."""
    normalized_value = normalize_numeric_shorthand(param_value)
    normalized_expected = normalize_numeric_shorthand(expected_old_value)
    if operation_kind != "set_param":
        return SetParamResolution(
            instance_name=instance_name,
            param_key=param_key,
            param_value=normalized_value,
            target_ref=target_ref,
            expected_old_value=normalized_expected,
        )
    if normalized_value is None or (
        isinstance(normalized_value, str) and not normalized_value.strip()
    ):
        return SetParamResolution(
            instance_name=instance_name,
            param_key=param_key,
            param_value=normalized_value,
            target_ref=target_ref,
            expected_old_value=normalized_expected,
            clarification=_clarification(
                "Parameter edits require an explicit new value before mutation.",
                ["Provide the exact new value for the parameter edit."],
                reason="missing_new_value",
            ),
        )
    if _has_exact_target(instance_name, param_key, target_ref):
        return SetParamResolution(
            instance_name=instance_name,
            param_key=param_key,
            param_value=normalized_value,
            target_ref=target_ref,
            expected_old_value=normalized_expected,
        )

    candidates = build_editable_parameter_candidates(
        session,
        catalog_root=catalog_root,
        include_connections=True,
    )
    candidate_matches = _matching_candidates(
        candidates,
        user_text=user_text,
        instance_name=instance_name,
        param_key=param_key,
        target_ref=target_ref,
        allow_value_only_match=normalized_expected is not None,
    )
    if normalized_expected is not None:
        matched = [
            candidate
            for candidate in candidate_matches
            if values_equivalent(candidate.current_value, normalized_expected)
        ]
    else:
        matched = candidate_matches
    resolution_base = {
        "source": "graph_local_metadata",
        "candidate_count": len(matched),
        "expected_old_value": normalized_expected,
        "param_value": normalized_value,
    }
    if len(matched) == 1:
        selected = matched[0]
        return SetParamResolution(
            instance_name=selected.instance_name,
            param_key=selected.param_key,
            param_value=normalized_value,
            target_ref=selected.target_ref,
            expected_old_value=normalized_expected,
            target_resolution={
                **resolution_base,
                "reason": "unique_graph_local_candidate",
                "selected": selected.handle(),
            },
        )
    if len(matched) > 1:
        return SetParamResolution(
            instance_name=instance_name,
            param_key=param_key,
            param_value=normalized_value,
            target_ref=target_ref,
            expected_old_value=normalized_expected,
            target_resolution={
                **resolution_base,
                "reason": "ambiguous_graph_local_candidates",
                "candidates": [candidate.handle() for candidate in matched[:8]],
            },
            clarification=_clarification(
                "Parameter target is ambiguous in the active graph.",
                [
                    (
                        f"{candidate.instance_name}.{candidate.param_key} "
                        f"(current={_compact_value(candidate.current_value)})"
                    )
                    for candidate in matched[:8]
                ],
                reason="ambiguous_target",
            ),
        )
    if normalized_expected is not None and candidate_matches:
        return SetParamResolution(
            instance_name=instance_name,
            param_key=param_key,
            param_value=normalized_value,
            target_ref=target_ref,
            expected_old_value=normalized_expected,
            target_resolution={
                **resolution_base,
                "candidate_count": len(candidate_matches),
                "reason": "expected_old_value_mismatch",
                "candidates": [candidate.handle() for candidate in candidate_matches[:8]],
            },
            clarification=_clarification(
                "The active graph value does not match the requested old-value guard.",
                [
                    (
                        f"{candidate.instance_name}.{candidate.param_key} "
                        f"is current={_compact_value(candidate.current_value)}"
                    )
                    for candidate in candidate_matches[:8]
                ],
                reason="expected_old_value_mismatch",
            ),
        )

    return SetParamResolution(
        instance_name=instance_name,
        param_key=param_key,
        param_value=normalized_value,
        target_ref=target_ref,
        expected_old_value=normalized_expected,
        target_resolution={**resolution_base, "reason": "no_graph_local_candidate"},
        clarification=_clarification(
            "No editable parameter target matched the active graph metadata.",
            ["Inspect parameters/details and provide an exact target."],
            reason="target_not_found",
        ),
    )


def _matching_candidates(
    candidates: list[EditableParameterCandidate],
    *,
    user_text: str,
    instance_name: str | None,
    param_key: str | None,
    target_ref: dict[str, Any] | None,
    allow_value_only_match: bool,
) -> list[EditableParameterCandidate]:
    user_blob = " ".join(
        text
        for text in (user_text, instance_name or "", param_key or "")
        if isinstance(text, str)
    )
    text_tokens = _tokens(user_blob)
    exact_instance_names = {candidate.instance_name for candidate in candidates}
    exact_param_keys = {candidate.param_key for candidate in candidates}
    exact_instance = instance_name if instance_name in exact_instance_names else None
    exact_param = param_key if param_key in exact_param_keys else None

    filtered = candidates
    if isinstance(target_ref, dict) and isinstance(target_ref.get("block_uid"), str):
        block_uid = target_ref["block_uid"]
        filtered = [candidate for candidate in filtered if candidate.block_uid == block_uid]
    if exact_instance is not None:
        filtered = [
            candidate for candidate in filtered if candidate.instance_name == exact_instance
        ]
    if exact_param is not None:
        filtered = [candidate for candidate in filtered if candidate.param_key == exact_param]

    param_matched = [
        candidate
        for candidate in filtered
        if exact_param is not None or _field_matches(
            (candidate.param_key, candidate.param_label),
            user_blob=user_blob,
            user_tokens=text_tokens,
        )
    ]
    if not param_matched:
        return []

    block_matched = [
        candidate
        for candidate in param_matched
        if exact_instance is not None or _field_matches(
            (candidate.instance_name, candidate.block_type, candidate.block_label),
            user_blob=user_blob,
            user_tokens=text_tokens,
        )
    ]
    if block_matched:
        return block_matched
    if allow_value_only_match:
        return param_matched
    return []


def _has_exact_target(
    instance_name: str | None,
    param_key: str | None,
    target_ref: dict[str, Any] | None,
) -> bool:
    has_param = isinstance(param_key, str) and bool(param_key.strip())
    has_named_target = isinstance(instance_name, str) and bool(instance_name.strip())
    has_ref_target = isinstance(target_ref, dict)
    return has_param and (has_named_target or has_ref_target)


def _field_matches(
    fields: tuple[str | None, ...],
    *,
    user_blob: str,
    user_tokens: set[str],
) -> bool:
    normalized_blob = f" {user_blob.lower()} "
    for field in fields:
        if not isinstance(field, str) or not field.strip():
            continue
        normalized_field = field.strip().lower()
        if f" {normalized_field} " in normalized_blob:
            return True
        field_tokens = _tokens(normalized_field)
        meaningful = {token for token in field_tokens if len(token) > 1}
        if meaningful and meaningful.issubset(user_tokens):
            return True
    return False


def _tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in _TOKEN_RE.finditer(text)}


def _catalog_metadata_by_block_type(
    block_types: Any,
    *,
    catalog_root: str | None,
) -> dict[str, dict[str, Any]]:
    metadata_by_type: dict[str, dict[str, Any]] = {}
    for block_type in sorted({value for value in block_types if isinstance(value, str)}):
        payload = _describe_block_with_root(block_type, catalog_root=catalog_root)
        if not payload.get("ok"):
            metadata_by_type[block_type] = {}
            continue
        metadata: dict[str, Any] = {"__params__": {}}
        block_label = payload.get("label")
        if isinstance(block_label, str) and block_label.strip():
            metadata["__block_label__"] = block_label.strip()
        for parameter in payload.get("parameters", []):
            if not isinstance(parameter, dict):
                continue
            parameter_id = parameter.get("id")
            if not isinstance(parameter_id, str) or not parameter_id.strip():
                continue
            metadata["__params__"][parameter_id] = {
                key: parameter.get(key)
                for key in (
                    "label",
                    "dtype",
                    "default",
                    "options",
                    "option_labels",
                    "hide",
                )
                if key in parameter
            }
        metadata_by_type[block_type] = metadata
    return metadata_by_type


def _metadata_string(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _metadata_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _connection_summaries(
    connections: list[Connection],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    incoming: dict[str, list[str]] = {}
    outgoing: dict[str, list[str]] = {}
    for connection in connections:
        conn_id = render_connection_id(
            connection.src_block,
            connection.src_port,
            connection.dst_block,
            connection.dst_port,
        )
        outgoing.setdefault(connection.src_block, []).append(conn_id)
        incoming.setdefault(connection.dst_block, []).append(conn_id)
    return incoming, outgoing


def _parameter_map(block: Block) -> dict[str, Any]:
    parameters = block.params.get("parameters")
    if not isinstance(parameters, dict):
        return {}
    return {str(key): value for key, value in parameters.items()}


def _compact_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return " ".join(value.split())
    return str(value)


def _clarification(
    message: str,
    options: list[str],
    *,
    reason: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error_type": "clarification_required",
        "message": message,
        "clarification_options": options,
        "clarification_reason": reason,
    }
