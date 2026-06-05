"""Public Phase 2 block description entry point."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from grc_agent._payload import ErrorCode

from .errors import BlockNotFoundError, CatalogError, CatalogLoadError, build_error_payload
from .loaders import find_block_source
from .normalize import (
    build_signature,
    hierarchy_warnings,
    normalize_parameter,
    normalize_port,
    optional_string,
    preserved_string_values,
    select_category_path,
    string_values,
)
from .schema import BlockDescription, RawCatalogBlock


def describe_block(block_id: str) -> dict[str, Any]:
    """Return structured GNU catalog truth for one block id."""
    return _describe_block_with_root(block_id)


def _describe_block_with_root(
    block_id: str,
    *,
    catalog_root: str | Path | None = None,
) -> dict[str, Any]:
    normalized_block_id = _normalize_block_id(block_id)
    if normalized_block_id is None:
        return build_error_payload(
            error_type=ErrorCode.TOOL_CALL_INVALID,
            message="block_id must be a non-empty string.",
        )

    try:
        raw_block = find_block_source(normalized_block_id, catalog_root=catalog_root)
        description = _build_block_description(raw_block)
    except BlockNotFoundError as exc:
        return build_error_payload(
            error_type=ErrorCode.BLOCK_NOT_FOUND,
            message=str(exc),
            details={
                "block_id": exc.block_id,
            },
        )
    except CatalogError as exc:
        return build_error_payload(
            error_type=ErrorCode.CATALOG_LOAD_ERROR,
            message=str(exc),
        )

    return description.to_payload()


def _normalize_block_id(block_id: Any) -> str | None:
    if not isinstance(block_id, str):
        return None
    normalized = " ".join(block_id.split())
    return normalized or None


def _build_block_description(raw_block: RawCatalogBlock) -> BlockDescription:
    payload = raw_block.payload
    label = optional_string(payload.get("label"))
    if label is None:
        raise CatalogLoadError(f"{raw_block.path} is missing a non-empty 'label' field.")

    parameter_payloads = _mapping_list_or_error(payload, "parameters", raw_block.path)
    input_payloads = _mapping_list_or_error(payload, "inputs", raw_block.path)
    output_payloads = _mapping_list_or_error(payload, "outputs", raw_block.path)

    category_path, warnings = select_category_path(raw_block)
    warnings.extend(hierarchy_warnings(raw_block))

    parameters = [
        normalize_parameter(parameter_payload, source_path=raw_block.path)
        for parameter_payload in parameter_payloads
    ]
    inputs = [normalize_port(port_payload) for port_payload in input_payloads]
    outputs = [normalize_port(port_payload) for port_payload in output_payloads]

    return BlockDescription(
        block_id=raw_block.block_id,
        label=label,
        category_path=category_path,
        flags=string_values(payload.get("flags")),
        loaded_from=str(raw_block.path),
        parameters=parameters,
        inputs=inputs,
        outputs=outputs,
        asserts=preserved_string_values(payload.get("asserts")),
        documentation=optional_string(payload.get("documentation")),
        doc_url=optional_string(payload.get("doc_url")),
        warnings=warnings,
        signature=build_signature(raw_block.block_id, parameters),
    )


def _mapping_list_or_error(
    payload: dict[str, Any],
    key: str,
    source_path: Path,
) -> list[dict[str, Any]]:
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise CatalogLoadError(f"{source_path} has an invalid '{key}' section.")

    normalized_items: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise CatalogLoadError(
                f"{source_path} has a non-mapping item in '{key}' at index {index}."
            )
        normalized_items.append(item)
    return normalized_items
