"""Graph-local editable parameter indexing and resolution."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from grc_agent.catalog.describe import _describe_block_with_root
from grc_agent.models import Block, Connection
from grc_agent.session_ops import connection_id as render_connection_id


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
