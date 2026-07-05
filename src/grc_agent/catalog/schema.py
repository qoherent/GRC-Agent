"""Structured schema records for Phase 2 catalog description."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from grc_agent.domain_models import join_non_empty


def clean_template_string(val: Any) -> str:
    if val is None:
        return ""
    val_str = str(val)
    import re
    # 1. ${expression}=fallback -> fallback
    val_str = re.sub(r'\$\{[^}]*\}=' , "", val_str)
    # 2. ${expression} -> expression
    val_str = re.sub(r'\$\{\s*([^}]+?)\s*\}', r'\1', val_str)
    return val_str.strip()


@dataclass(frozen=True)
class CatalogFiles:
    """Resolved GNU catalog file sets under one catalog root."""

    block: tuple[Path, ...]
    tree: tuple[Path, ...]
    domain: tuple[Path, ...]

    def counts(self) -> dict[str, int]:
        return {
            "block": len(self.block),
            "tree": len(self.tree),
            "domain": len(self.domain),
        }


@dataclass(frozen=True)
class RawCatalogBlock:
    """One raw `.block.yml` payload plus any tree-derived category paths."""

    block_id: str
    path: Path
    payload: dict[str, Any]
    category_paths: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class CatalogSnapshot:
    """One cached catalog snapshot used by retrieval and block description."""

    root: Path
    files: CatalogFiles
    blocks: dict[str, RawCatalogBlock]


@dataclass(frozen=True)
class NormalizedParameter:
    """One normalized GNU block parameter."""

    id: str
    label: str | None = None
    dtype: str | None = None
    default: Any = None
    category: str | None = None
    hide: str | None = None
    options: list[Any] = field(default_factory=list)
    option_labels: list[Any] = field(default_factory=list)
    option_attributes: dict[str, list[Any]] = field(default_factory=dict)
    base_key: str | None = None

    def to_compact_dict(self) -> dict[str, Any]:
        """Discovery shape: id/dtype/default only — no options, no label."""
        payload = {
            "id": self.id,
            "dtype": self.dtype,
            "default": self.default,
        }
        return {k: v for k, v in payload.items() if v is not None}


@dataclass(frozen=True)
class NormalizedPort:
    """One normalized GNU block input or output port."""

    label: str | None = None
    domain: str | None = None
    id: str | None = None
    dtype: str | None = None
    vlen: int | str | None = None
    multiplicity: int | str | None = None
    optional: bool | int | str | None = None
    hide: bool | str | None = None
    color: str | None = None

    def to_compact_dict(self) -> dict[str, Any]:
        """Discovery shape: id/domain/dtype only — no multiplicity/optional."""
        payload = {
            "id": self.id,
            "domain": self.domain if self.domain != "stream" else None,
            "dtype": clean_template_string(self.dtype),
        }
        return {k: v for k, v in payload.items() if v is not None}


@dataclass(frozen=True)
class BlockDescription:
    """The normalized Phase 2 public block description payload."""

    block_id: str
    label: str
    category_path: list[str]
    flags: list[str]
    parameters: list[NormalizedParameter]
    inputs: list[NormalizedPort]
    outputs: list[NormalizedPort]
    asserts: list[str]
    documentation: str | None
    warnings: list[str]
    signature: str

    def to_payload(
        self,
        *,
        hides: dict[str, str] | None = None,
        param_categories: dict[str, str] | None = None,
        mode: str = "details",
    ) -> dict[str, Any]:
        """Build the discovery-shape payload (what the model sees).

        Parameter filtering and ordering are delegated to the unified filter
        in :mod:`grc_agent.runtime.param_filter` (Stage A only: drop
        ``hide='all'``, Advanced, Config, ``dtype='gui_hint'``). Surviving
        params are sorted by GRC overview rank and returned with ``id``,
        ``dtype``, ``default`` only — no ``options``/``option_labels``
        (editing context; ``inspect_graph`` provides them when needed).
        """
        from grc_agent.catalog.loaders import evaluated_param_hides_for_block
        from grc_agent.runtime.param_filter import (
            DEFAULT_PARAM_TAB,
            keep_param,
            overview_rank,
        )
        from grc_agent.runtime.param_filter import (
            categories as platform_categories,
        )

        if hides is None:
            hides = evaluated_param_hides_for_block(self.block_id)
        if not hides:
            hides = {parameter.id: parameter.hide or "none" for parameter in self.parameters}
        if param_categories is None:
            param_categories = platform_categories(self.block_id)

        visible_params = [
            parameter
            for parameter in self.parameters
            if keep_param(
                hide=hides.get(parameter.id, "all"),
                category=param_categories.get(parameter.id)
                or parameter.category
                or DEFAULT_PARAM_TAB,
                dtype=parameter.dtype or "",
                value=parameter.default or "",
                default=parameter.default or "",
                mode=mode,
                param_key=parameter.id,
            )
        ]
        visible_params.sort(key=lambda p: (overview_rank(hides.get(p.id, "all")), p.id))

        # Determine block role consistently with live ``classify_role``.
        # Single source of truth: ``grc_native_adapter._classify_role_core``.
        from grc_agent.grc_native_adapter import classify_role_from_catalog

        role = classify_role_from_catalog(
            self.block_id,
            self.flags or (),
            has_sources=bool(self.outputs),
            has_sinks=bool(self.inputs),
        )

        payload: dict[str, Any] = {
            "ok": True,
            "block_id": self.block_id,
            "role": role.value,
            "params": {
                p.id: (
                    f"bool={clean_template_string(p.default)}"
                    if p.dtype == "enum" and p.options and set(str(o) for o in p.options) == {"True", "False"}
                    else f"enum=[{','.join(str(o) for o in p.options)}]={clean_template_string(p.default)}"
                    if p.dtype == "enum" and p.options
                    else f"{clean_template_string(p.dtype) or '?'}={clean_template_string(p.default)}"
                )
                for p in visible_params
            },
            "inputs": [port.to_compact_dict() for port in self.inputs],
            "outputs": [port.to_compact_dict() for port in self.outputs],
        }
        return payload


def compact_text(value: Any) -> str:
    """Collapse arbitrary values into compact single-line text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        parts = [join_non_empty(str(key), compact_text(item)) for key, item in value.items()]
        return "; ".join(part for part in parts if part)
    if isinstance(value, (list, tuple, set)):
        parts = [compact_text(item) for item in value]
        return ", ".join(part for part in parts if part)
    return str(value)


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
    from .loaders import CatalogLoadError

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
    domain = optional_string(payload.get("domain"))
    dtype = optional_string(payload.get("dtype"))
    color = _map_port_color(domain, dtype)

    return NormalizedPort(
        label=optional_string(payload.get("label")),
        domain=domain,
        id=optional_string(payload.get("id")),
        dtype=dtype,
        vlen=payload.get("vlen"),
        multiplicity=payload.get("multiplicity"),
        optional=payload.get("optional"),
        hide=payload.get("hide"),
        color=color,
    )


def _map_port_color(domain: str | None, dtype: str | None) -> str | None:
    """Map GNU Radio port domain and dtype to their canonical GUI colors."""
    if domain == "message":
        return "grey"

    if not dtype:
        return None

    # Canonical GNU Radio port colors from tutorials and source
    mapping = {
        "complex": "blue",
        "complex64": "blue",
        "fc32": "blue",
        "float": "orange",
        "float32": "orange",
        "f32": "orange",
        "byte": "purple",
        "char": "purple",
        "uint8": "purple",
        "u8": "purple",
        "short": "yellow",
        "int16": "yellow",
        "s16": "yellow",
        "int": "green",
        "int32": "green",
        "i32": "green",
        "complex128": "dark blue",
        "fc64": "dark blue",
        "float64": "dark orange",
        "f64": "dark orange",
        "int64": "dark green",
        "i64": "dark green",
    }
    return mapping.get(dtype.lower())


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


@lru_cache(maxsize=128)
def _resolves_to_hierarchical_class(imports_text: str | None, make_text: str | None) -> bool:
    """Resolve the make() target through the imports and check MRO for hier_block2.

    Note: ``platform.block_classes[id]`` returns GRC's metadata Block class,
    not the runtime ``gnuradio.gr.hier_block2`` subclass. The MRO check for
    ``hier_block2`` only works against the actual imported Python module,
    so the importlib chain is the correct path here (not the platform
    registry).
    """
    import ast
    import importlib
    import re

    if not imports_text or not make_text:
        return False

    match = re.compile(r"([A-Za-z_][\w\.]*)\s*\(").search(make_text)
    if match is None:
        return False
    target_expression = match.group(1)

    try:
        tree = ast.parse(imports_text)
        aliases: dict[str, tuple[str, str, str | None]] = {}
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local_name = alias.asname or alias.name.split(".")[0]
                    aliases[local_name] = ("import", alias.name, None)
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    aliases[local_name] = ("from", node.module, alias.name)

        parts = target_expression.split(".")
        if not parts:
            return False
        binding = aliases.get(parts[0])
        if binding is None:
            return False
        kind, mod_name, imp_name = binding
        if kind == "import":
            resolved: object = importlib.import_module(mod_name)
        else:
            mod = importlib.import_module(mod_name)
            if imp_name and hasattr(mod, imp_name):
                resolved = getattr(mod, imp_name)
            else:
                resolved = importlib.import_module(f"{mod_name}.{imp_name}")
        for part in parts[1:]:
            resolved = getattr(resolved, part)
    except Exception:
        return False

    if not inspect.isclass(resolved):
        return False
    return any(
        base.__name__ == "hier_block2" and base.__module__.startswith("gnuradio")
        for base in resolved.__mro__
    )
