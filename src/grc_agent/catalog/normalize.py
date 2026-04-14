"""Normalization helpers for catalog description and retrieval reuse."""

from __future__ import annotations

import ast
import importlib
import inspect
import re
from pathlib import Path
from typing import Any

from .errors import CatalogLoadError
from .schema import NormalizedParameter, NormalizedPort, RawCatalogBlock

_MAKE_TARGET_PATTERN = re.compile(r"([A-Za-z_][\w\.]*)\s*\(")


def compact_text(value: Any) -> str:
    """Collapse arbitrary values into compact single-line text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        parts = [_join_non_empty(str(key), compact_text(item)) for key, item in value.items()]
        return "; ".join(part for part in parts if part)
    if isinstance(value, (list, tuple, set)):
        parts = [compact_text(item) for item in value]
        return ", ".join(part for part in parts if part)
    return str(value)


def coerce_mapping_list(value: Any) -> list[dict[str, Any]]:
    """Return only mapping items from an optional list payload."""
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def split_category_path(value: Any) -> list[str]:
    """Split a GNU category string into normalized path parts."""
    if not isinstance(value, str) or not value.strip():
        return []

    parts: list[str] = []
    for raw_part in value.split("/"):
        part = raw_part.strip()
        if not part:
            continue
        if part.startswith("[") and part.endswith("]"):
            part = part[1:-1].strip()
        if part:
            parts.append(part)
    return parts


def string_values(value: Any) -> list[str]:
    """Normalize an optional string-or-list-of-strings field into a string list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        values = [compact_text(item) for item in value]
        return [item for item in values if item]
    compact = compact_text(value)
    return [compact] if compact else []


def optional_string(value: Any) -> str | None:
    """Return one stripped string while preserving internal newlines and spacing."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def preserved_string_values(value: Any) -> list[str]:
    """Normalize a string-or-list field while preserving expression text."""
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            text = item.strip() if isinstance(item, str) else str(item).strip()
            if text:
                values.append(text)
        return values
    text = str(value).strip()
    return [text] if text else []


def normalize_parameter(payload: dict[str, Any], *, source_path: Path) -> NormalizedParameter:
    """Normalize one GNU parameter payload into the public structured shape."""
    parameter_id = optional_string(payload.get("id"))
    if parameter_id is None:
        raise CatalogLoadError(f"{source_path} has a parameter missing a non-empty 'id' field.")

    option_attributes: dict[str, list[Any]] = {}
    raw_option_attributes = payload.get("option_attributes")
    if isinstance(raw_option_attributes, dict):
        for key, raw_values in raw_option_attributes.items():
            key_text = str(key).strip()
            if not key_text:
                continue
            if isinstance(raw_values, list):
                option_attributes[key_text] = list(raw_values)
            else:
                option_attributes[key_text] = [raw_values]

    raw_options = payload.get("options")
    raw_option_labels = payload.get("option_labels")
    return NormalizedParameter(
        id=parameter_id,
        label=optional_string(payload.get("label")),
        dtype=optional_string(payload.get("dtype")),
        default=payload.get("default"),
        category=optional_string(payload.get("category")),
        hide=optional_string(payload.get("hide")),
        options=list(raw_options) if isinstance(raw_options, list) else [],
        option_labels=list(raw_option_labels) if isinstance(raw_option_labels, list) else [],
        option_attributes=option_attributes,
        base_key=optional_string(payload.get("base_key")),
    )


def normalize_port(payload: dict[str, Any]) -> NormalizedPort:
    """Normalize one GNU input or output payload into the public structured shape."""
    return NormalizedPort(
        label=optional_string(payload.get("label")),
        domain=optional_string(payload.get("domain")),
        id=optional_string(payload.get("id")),
        dtype=optional_string(payload.get("dtype")),
        vlen=payload.get("vlen"),
        multiplicity=payload.get("multiplicity"),
        optional=payload.get("optional"),
        hide=payload.get("hide"),
    )


def select_category_path(raw_block: RawCatalogBlock) -> tuple[list[str], list[str]]:
    """Choose one stable category path for the public payload and return any caveats."""
    direct_path = split_category_path(raw_block.payload.get("category"))
    if direct_path:
        return direct_path, []

    if not raw_block.category_paths:
        return [], []

    selected_path = list(raw_block.category_paths[0])
    warnings: list[str] = []
    if len(raw_block.category_paths) > 1:
        alternate_paths = [" > ".join(path) for path in raw_block.category_paths[1:3]]
        extra_count = max(0, len(raw_block.category_paths) - 3)
        alternate_suffix = ""
        if alternate_paths:
            alternate_suffix = f"; alternates: {', '.join(alternate_paths)}"
        if extra_count:
            alternate_suffix += f", +{extra_count} more"
        warnings.append(
            f"Multiple GNU tree categories available; selected {' > '.join(selected_path)}"
            f"{alternate_suffix}."
        )
    return selected_path, warnings


def hierarchy_warnings(raw_block: RawCatalogBlock) -> list[str]:
    """Return lightweight hierarchical-block caveats when GNU metadata proves them."""
    grc_source = optional_string(raw_block.payload.get("grc_source"))
    if grc_source is not None:
        return [f"Generated hierarchical block from {grc_source}."]

    templates = raw_block.payload.get("templates")
    if not isinstance(templates, dict):
        return []

    imports_text = optional_string(templates.get("imports"))
    make_text = optional_string(templates.get("make"))
    if imports_text and "grc-generated hier_block" in imports_text:
        return ["Generated hierarchical block wrapper."]

    label = optional_string(raw_block.payload.get("label"))
    if not _looks_hierarchical(raw_block.block_id, label, make_text):
        return []

    if _resolves_to_hierarchical_class(imports_text, make_text):
        return ["Hierarchical GNU Radio wrapper block."]
    return []


def build_signature(
    block_id: str,
    parameters: list[NormalizedParameter],
    *,
    max_parameters: int = 6,
) -> str:
    """Build a compact instantiation skeleton from block id and parameter defaults."""
    rendered: list[str] = []
    for parameter in parameters[:max_parameters]:
        if parameter.default is None:
            rendered.append(parameter.id)
            continue

        value_text = compact_text(parameter.default)
        if not value_text and isinstance(parameter.default, str):
            value_text = '""'
        rendered.append(f"{parameter.id}={value_text}")

    remaining = len(parameters) - max_parameters
    if remaining > 0:
        rendered.append(f"... +{remaining} more")
    return f"{block_id}({', '.join(rendered)})"


def _looks_hierarchical(block_id: str, label: str | None, make_text: str | None) -> bool:
    haystack = " ".join(part for part in (block_id, label or "", make_text or "") if part).lower()
    return "_hier" in haystack or "hierarchical" in haystack or "hier_block" in haystack


def _resolves_to_hierarchical_class(imports_text: str | None, make_text: str | None) -> bool:
    if not imports_text or not make_text:
        return False

    target_expression = _extract_make_target(make_text)
    if target_expression is None:
        return False

    try:
        aliases = _parse_import_aliases(imports_text)
        resolved = _resolve_target(aliases, target_expression)
    except (AttributeError, ImportError, ModuleNotFoundError, SyntaxError, TypeError, ValueError):
        return False
    return _is_hierarchical_class(resolved)


def _extract_make_target(make_text: str) -> str | None:
    match = _MAKE_TARGET_PATTERN.search(make_text)
    if match is None:
        return None
    return match.group(1)


def _parse_import_aliases(imports_text: str) -> dict[str, tuple[str, str, str | None]]:
    aliases: dict[str, tuple[str, str, str | None]] = {}
    tree = ast.parse(imports_text)
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".")[0]
                aliases[local_name] = ("import", alias.name, None)
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                local_name = alias.asname or alias.name
                aliases[local_name] = ("from", node.module, alias.name)
    return aliases


def _resolve_target(
    aliases: dict[str, tuple[str, str, str | None]],
    target_expression: str,
) -> object:
    target_parts = target_expression.split(".")
    if not target_parts:
        raise ValueError("Missing target expression.")

    binding = aliases.get(target_parts[0])
    if binding is None:
        raise ValueError(f"Unknown import alias: {target_parts[0]}")

    import_kind, module_name, imported_name = binding
    if import_kind == "import":
        resolved: object = importlib.import_module(module_name)
    else:
        module = importlib.import_module(module_name)
        if imported_name is None:
            raise ValueError("Missing imported symbol name.")
        if hasattr(module, imported_name):
            resolved = getattr(module, imported_name)
        else:
            resolved = importlib.import_module(f"{module_name}.{imported_name}")

    for part in target_parts[1:]:
        resolved = getattr(resolved, part)
    return resolved


def _is_hierarchical_class(candidate: object) -> bool:
    if not inspect.isclass(candidate):
        return False
    return any(
        base.__name__ == "hier_block2" and base.__module__.startswith("gnuradio")
        for base in candidate.__mro__
    )


def _join_non_empty(*parts: str) -> str:
    return " ".join(part for part in parts if part).strip()
