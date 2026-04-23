"""Structured schema records for Phase 2 catalog description."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "label": self.label,
            "dtype": self.dtype,
            "default": self.default,
            "category": self.category,
            "hide": self.hide,
            "options": list(self.options),
            "option_labels": list(self.option_labels),
            "option_attributes": dict(self.option_attributes),
            "base_key": self.base_key,
        }
        return {
            key: value
            for key, value in payload.items()
            if value not in (None, [], {})
        }


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

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "label": self.label,
            "domain": self.domain,
            "id": self.id,
            "dtype": self.dtype,
            "vlen": self.vlen,
            "multiplicity": self.multiplicity,
            "optional": self.optional,
            "hide": self.hide,
            "color": self.color,
        }
        return {
            key: value
            for key, value in payload.items()
            if value is not None
        }


@dataclass(frozen=True)
class BlockDescription:
    """The normalized Phase 2 public block description payload."""

    block_id: str
    label: str
    category_path: list[str]
    flags: list[str]
    loaded_from: str
    parameters: list[NormalizedParameter]
    inputs: list[NormalizedPort]
    outputs: list[NormalizedPort]
    asserts: list[str]
    documentation: str | None
    doc_url: str | None
    warnings: list[str]
    signature: str

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "ok": True,
            "block_id": self.block_id,
            "label": self.label,
            "category_path": list(self.category_path),
            "parameters": [parameter.to_dict() for parameter in self.parameters],
            "inputs": [port.to_dict() for port in self.inputs],
            "outputs": [port.to_dict() for port in self.outputs],
            "signature": self.signature,
        }
        if self.flags:
            payload["flags"] = list(self.flags)
        if self.asserts:
            payload["asserts"] = list(self.asserts)
        if self.documentation is not None:
            payload["documentation"] = self.documentation
        if self.doc_url is not None:
            payload["doc_url"] = self.doc_url
        if self.warnings:
            payload["warnings"] = list(self.warnings)
        return payload
