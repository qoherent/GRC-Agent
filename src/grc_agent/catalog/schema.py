"""Structured schema records for Phase 2 catalog description."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def clean_template_string(val: Any) -> str:
    if val is None:
        return ""
    val_str = str(val)
    import re

    # 1. ${expression}=fallback -> fallback
    val_str = re.sub(r"\$\{[^}]*\}=", "", val_str)
    # 2. ${expression} -> expression
    val_str = re.sub(r"\$\{\s*([^}]+?)\s*\}", r"\1", val_str)
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
    dtype: str | None = None
    default: Any = None
    category: str | None = None
    hide: str | None = None
    options: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class NormalizedPort:
    """One normalized GNU block input or output port."""

    domain: str | None = None
    port_id: str | None = None
    dtype: str | None = None

    def to_compact_dict(self) -> dict[str, Any]:
        """Discovery shape: port_id/domain/dtype only — no multiplicity/optional."""
        payload = {
            "port_id": self.port_id,
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
            hidden_port_keys,
            keep_param,
            overview_rank,
        )
        from grc_agent.runtime.param_filter import (
            categories as platform_categories,
        )
        from grc_agent.runtime.param_filter import (
            type_controlling_params as platform_type_controlling_params,
        )

        if hides is None:
            hides = evaluated_param_hides_for_block(self.block_id)
        if not hides:
            hides = {parameter.id: parameter.hide or "none" for parameter in self.parameters}
        if param_categories is None:
            param_categories = platform_categories(self.block_id)
        type_controlling = platform_type_controlling_params(self.block_id)

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
                is_type_controlling=parameter.id in type_controlling,
            )
        ]
        visible_params.sort(key=lambda p: (overview_rank(hides.get(p.id, "all")), p.id))

        def _default_display(p: NormalizedParameter) -> str:
            """The suggested default shown for one param's value.

            A type-controlling param with no native default (the common
            case for polymorphic blocks — GRC's YAML rarely declares one)
            shows ``"auto"`` instead of a blank string: a real, working
            value the model can pass straight back to ``change_graph``,
            not an invitation to invent one (see ``change_graph``'s
            ``"auto"`` sentinel support).
            """
            cleaned = clean_template_string(p.default)
            if not cleaned and p.id in type_controlling:
                return "auto"
            return cleaned

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
            # Every entry ends in a trailing "=<default value>" token, with a
            # bracketed type descriptor before it (e.g.
            # "enum=[complex,float]=complex", "[raw]=analog.GR_GAUSSIAN").
            # The bracket is not decorative: the model reliably extracts the
            # trailing "=value" segment for the enum case (which has always
            # used a bracketed options list) but was observed copying the
            # WHOLE compact string verbatim for the plain, unbracketed
            # "dtype=default" shape non-enum/non-bool params previously used
            # (e.g. sending the literal "raw=analog.GR_GAUSSIAN" as a
            # parameter value instead of just "analog.GR_GAUSSIAN" — see
            # change_graph's "Value \"raw=analog.GR_GAUSSIAN\" cannot be
            # evaluated" rejection). Bracketing every type descriptor (not
            # just enum's options list) makes the shape uniform, with no
            # per-block exception, and leaves the already-correct enum shape
            # untouched.
            "params": {
                p.id: (
                    f"[bool]={_default_display(p)}"
                    if p.dtype == "enum"
                    and p.options
                    and set(str(o) for o in p.options) == {"True", "False"}
                    else f"enum=[{','.join(str(o) for o in p.options)}]={_default_display(p)}"
                    if p.dtype == "enum" and p.options
                    else f"[{clean_template_string(p.dtype) or '?'}]={_default_display(p)}"
                )
                for p in visible_params
            },
            "inputs": [
                port.to_compact_dict()
                for port in self.inputs
                if port.port_id not in hidden_port_keys(self.block_id, direction="inputs")
            ],
            "outputs": [
                port.to_compact_dict()
                for port in self.outputs
                if port.port_id not in hidden_port_keys(self.block_id, direction="outputs")
            ],
        }
        return payload


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

    raw_options = payload.get("options")
    return NormalizedParameter(
        id=parameter_id,
        dtype=optional_string(payload.get("dtype")),
        default=payload.get("default"),
        category=optional_string(payload.get("category")),
        hide=optional_string(payload.get("hide")),
        options=list(raw_options) if isinstance(raw_options, list) else [],
    )


def normalize_port(payload: dict[str, Any]) -> NormalizedPort:
    """Normalize one GNU input or output payload into the public structured shape."""
    domain = optional_string(payload.get("domain"))
    dtype = optional_string(payload.get("dtype"))

    return NormalizedPort(
        domain=domain,
        port_id=optional_string(payload.get("id")),
        dtype=dtype,
    )


def select_category_path(raw_block: RawCatalogBlock) -> list[str]:
    """Choose one stable category path for the public payload.

    A block can appear in multiple `.tree.yml` categories; the first
    listed category wins. (Previously also returned a warning listing
    the alternates, but every caller discarded it — see git history.)
    """
    direct_path = split_category_path(raw_block.payload.get("category"))
    if direct_path:
        return direct_path

    if not raw_block.category_paths:
        return []

    return list(raw_block.category_paths[0])


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

    from grc_agent.grc_native_adapter import resolves_to_hierarchical_class

    if resolves_to_hierarchical_class(imports_text, make_text):
        return ["Hierarchical GNU Radio wrapper block."]
    return []


def _looks_hierarchical(block_id: str, label: str | None, make_text: str | None) -> bool:
    haystack = " ".join(part for part in (block_id, label or "", make_text or "") if part).lower()
    return "_hier" in haystack or "hierarchical" in haystack or "hier_block" in haystack
