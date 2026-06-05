"""Catalog-backed block semantics for active graph inspection."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from grc_agent.catalog.describe import _describe_block_with_root

logger = logging.getLogger(__name__)

_CONTROL_CATEGORY_HINTS = {"variables", "gui widgets"}
_SEMANTIC_FLAG_NAMES = {"not_dsp", "disable_bypass", "throttle"}


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
        return {"role": "metadata", "source": "fallback"}

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
        from grc_agent.session.gnu_loader import _ensure_platform

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
    has_stream_input = input_domains.get("stream", 0) > 0
    has_stream_output = output_domains.get("stream", 0) > 0
    has_any_input = any(count > 0 for count in input_domains.values())
    has_any_output = any(count > 0 for count in output_domains.values())

    if "not_dsp" in flag_set:
        return "variable_or_control"
    if not has_any_input and not has_any_output and category_set & _CONTROL_CATEGORY_HINTS:
        return "variable_or_control"
    if has_stream_output and not has_stream_input:
        return "source"
    if has_stream_input and not has_stream_output:
        return "sink"
    if has_stream_input and has_stream_output:
        return "transform"
    if has_any_input or has_any_output:
        return "message_or_event"
    return "metadata"


def _domain_counts(ports: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for port in ports:
        domain = port.get("domain")
        if not isinstance(domain, str) or not domain.strip():
            domain = "stream"
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
