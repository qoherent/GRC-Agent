"""Catalog-backed block semantics for active graph inspection."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any

from grc_agent.catalog.loaders import _describe_block_with_root
from grc_agent._payload import Block, Connection
from grc_agent.session_ops import connection_id as render_connection_id

logger = logging.getLogger(__name__)


class BlockRole(StrEnum):
    """Semantic role of a GNU Radio block within a flowgraph."""

    VARIABLE_OR_CONTROL = "variable_or_control"
    SOURCE = "source"
    SINK = "sink"
    TRANSFORM = "transform"
    MESSAGE_OR_EVENT = "message_or_event"
    METADATA = "metadata"


class PortDomain(StrEnum):
    """Domain classification for a block port."""

    STREAM = "stream"
    MESSAGE = "message"


_CONTROL_CATEGORY_HINTS: frozenset[str] = frozenset({"variables", "gui widgets"})
_SEMANTIC_FLAG_NAMES: frozenset[str] = frozenset({"not_dsp", "disable_bypass", "throttle"})


def build_block_semantics_by_type(
    block_types: Any,
    *,
    catalog_root: str | None,
) -> dict[str, dict[str, Any]]:
    """Return compact semantic facts keyed by GNU Radio block type."""
    unique_types = tuple(sorted({item for item in block_types if isinstance(item, str)}))
    return {
        block_type: _block_semantics(block_type, catalog_root)
        for block_type in unique_types
    }


@lru_cache(maxsize=2048)
def _block_semantics(
    block_type: str,
    catalog_root: str | None,
) -> dict[str, Any]:
    catalog_payload = _describe_block_with_root(block_type, catalog_root=catalog_root)
    if not catalog_payload.get("ok"):
        return {"role": BlockRole.METADATA, "source": "fallback"}

    platform = _gnu_platform_block_metadata(block_type)
    flags = sorted(
        set(_string_list(catalog_payload.get("flags")))
        | set(_string_list(platform.get("flags")))
    )
    category_path = _string_list(
        platform.get("category_path") or catalog_payload.get("category_path")
    )
    inputs = _port_list(catalog_payload.get("inputs"))
    outputs = _port_list(catalog_payload.get("outputs"))
    input_domains = _domain_counts(inputs)
    output_domains = _domain_counts(outputs)
    role = _semantic_role(
        flags=flags,
        category_path=category_path,
        input_domains=input_domains,
        output_domains=output_domains,
    )
    evidence = {
        "source": "gnu_platform+catalog" if platform else "catalog",
        "category_path": category_path,
        "semantic_flags": [flag for flag in flags if flag in _SEMANTIC_FLAG_NAMES],
        "ports": {
            "inputs": input_domains,
            "outputs": output_domains,
        },
    }
    return {
        "label": catalog_payload.get("label"),
        "role": role,
        "evidence": _drop_empty(evidence),
    }


def _gnu_platform_block_metadata(block_type: str) -> dict[str, Any]:
    try:
        from grc_agent.session import _ensure_platform

        platform = _ensure_platform()
    except Exception as exc:
        logger.debug("GNU Radio platform metadata unavailable: %s", exc)
        return {}
    if platform is None:
        return {}
    block_class = getattr(platform, "block_classes", {}).get(block_type)
    if block_class is None:
        return {}
    return {
        "flags": _string_list(getattr(block_class, "flags", None)),
        "category_path": _string_list(getattr(block_class, "category", None)),
    }


def _semantic_role(
    *,
    flags: list[str],
    category_path: list[str],
    input_domains: dict[str, int],
    output_domains: dict[str, int],
) -> str:
    flag_set = {flag.lower() for flag in flags}
    category_set = {item.lower() for item in category_path}
    has_stream_input = input_domains.get(PortDomain.STREAM, 0) > 0
    has_stream_output = output_domains.get(PortDomain.STREAM, 0) > 0
    has_any_input = any(count > 0 for count in input_domains.values())
    has_any_output = any(count > 0 for count in output_domains.values())

    if "not_dsp" in flag_set:
        return BlockRole.VARIABLE_OR_CONTROL
    if not has_any_input and not has_any_output and category_set & _CONTROL_CATEGORY_HINTS:
        return BlockRole.VARIABLE_OR_CONTROL
    if has_stream_output and not has_stream_input:
        return BlockRole.SOURCE
    if has_stream_input and not has_stream_output:
        return BlockRole.SINK
    if has_stream_input and has_stream_output:
        return BlockRole.TRANSFORM
    if has_any_input or has_any_output:
        return BlockRole.MESSAGE_OR_EVENT
    return BlockRole.METADATA


def _domain_counts(ports: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for port in ports:
        domain = port.get("domain")
        if not isinstance(domain, str) or not domain.strip():
            domain = PortDomain.STREAM
        key = domain.strip().lower()
        counts[key] = counts.get(key, 0) + 1
    return counts


def _port_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is not None:
        return [item.strip() for item in str(value).split(",") if item.strip()]
    return []


def _drop_empty(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, [], {})}


# --- merged from editable_parameters.py ---

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
            "state_revision": self.state_revision,
        }

    def to_payload(
        self,
        *,
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
        if include_connections:
            payload["incoming_connections"] = list(self.incoming_connections)
            payload["outgoing_connections"] = list(self.outgoing_connections)
        return {key: value for key, value in payload.items() if value is not None}


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
