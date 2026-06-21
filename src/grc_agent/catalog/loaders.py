"""Shared GNU catalog discovery, raw metadata loading, and block description.

Consolidated from loaders.py + errors.py + describe.py.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from grc_agent._payload import ErrorCode, build_error_payload
from .schema import (
    BlockDescription,
    CatalogFiles,
    CatalogSnapshot,
    RawCatalogBlock,
    build_signature,
    hierarchy_warnings,
    normalize_parameter,
    normalize_port,
    optional_string,
    preserved_string_values,
    select_category_path,
)

DEFAULT_GRC_CATALOG_ROOTS = (
    Path("/usr/share/gnuradio/grc/blocks"),
    Path("/usr/local/share/gnuradio/grc/blocks"),
)

try:
    _YAML_SAFE_LOADER = yaml.CSafeLoader
except AttributeError:
    _YAML_SAFE_LOADER = yaml.SafeLoader

_CategoryCallback = Callable[[tuple[str, ...]], None]
_BlockCallback = Callable[[tuple[str, ...], str], None]


# -- errors --

class CatalogError(RuntimeError):
    """Base class for catalog metadata and description failures."""


class CatalogLoadError(CatalogError):
    """Raised when the GNU block catalog cannot be discovered or loaded."""


class BlockNotFoundError(CatalogError):
    """Raised when a block id is absent from the resolved GNU catalog."""

    def __init__(self, block_id: str, *, catalog_root: str) -> None:
        self.block_id = block_id
        self.catalog_root = catalog_root
        super().__init__(f"Block '{block_id}' not found in catalog.")


# -- loaders --

def discover_catalog_root(catalog_root: str | Path | None = None) -> Path:
    """Return the GNU catalog root used by retrieval and block description."""
    if catalog_root is not None:
        resolved_root = Path(catalog_root).expanduser()
        if not resolved_root.is_dir():
            raise CatalogLoadError(f"GNU Radio catalog root not found: {resolved_root}")
        return resolved_root

    for root in DEFAULT_GRC_CATALOG_ROOTS:
        if root.is_dir():
            return root

    checked_roots = ", ".join(str(root) for root in DEFAULT_GRC_CATALOG_ROOTS)
    raise CatalogLoadError(f"GNU Radio catalog root not found. Checked: {checked_roots}")


def collect_catalog_files(root: Path) -> CatalogFiles:
    """Collect all `.block.yml`, `.tree.yml`, and `.domain.yml` files under `root`."""
    try:
        return CatalogFiles(
            block=tuple(sorted(root.rglob("*.block.yml"))),
            tree=tuple(sorted(root.rglob("*.tree.yml"))),
            domain=tuple(sorted(root.rglob("*.domain.yml"))),
        )
    except OSError as exc:
        raise CatalogLoadError(f"Could not scan GNU metadata root: {root}") from exc


def validate_catalog_files(root: Path, files: CatalogFiles) -> None:
    """Reject catalog roots that are missing any required GNU metadata classes."""
    counts = files.counts()
    missing_kinds = [name for name, count in counts.items() if count == 0]
    if not missing_kinds:
        return

    missing_labels = ", ".join(f".{name}.yml" for name in missing_kinds)
    raise CatalogLoadError(
        f"GNU Radio catalog metadata is incomplete at {root}: missing {missing_labels} "
        f"(found {counts['block']} .block.yml, {counts['tree']} .tree.yml, "
        f"{counts['domain']} .domain.yml)."
    )


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    """Load one YAML document that must decode to a mapping."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogLoadError(f"Could not read GNU metadata file: {path}") from exc

    try:
        payload = yaml.load(content, Loader=_YAML_SAFE_LOADER)
    except yaml.YAMLError as exc:
        raise CatalogLoadError(f"Could not parse GNU metadata file: {path}") from exc

    if not isinstance(payload, dict):
        raise CatalogLoadError(f"YAML metadata must be a mapping: {path}")
    return payload


def require_string(payload: dict[str, Any], key: str, *, path: Path) -> str:
    """Return one required non-empty string field from a GNU metadata mapping."""
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogLoadError(f"{path} is missing a non-empty '{key}' field.")
    return value


def walk_tree_entries(
    payload: dict[str, Any],
    source_path: Path,
    *,
    on_category: _CategoryCallback | None = None,
    on_block: _BlockCallback | None = None,
    parent_path: tuple[str, ...] = (),
) -> None:
    """Walk one GNU `.tree.yml` payload and emit category and block placements."""
    for category_name, children in payload.items():
        normalized_name = str(category_name).strip()
        if normalized_name.startswith("[") and normalized_name.endswith("]"):
            normalized_name = normalized_name[1:-1].strip()
        category_path = parent_path + (normalized_name or str(category_name),)
        if on_category is not None:
            on_category(category_path)

        if isinstance(children, list):
            for item in children:
                if isinstance(item, str):
                    if on_block is not None:
                        on_block(category_path, item)
                    continue
                if isinstance(item, dict):
                    walk_tree_entries(
                        item,
                        source_path,
                        on_category=on_category,
                        on_block=on_block,
                        parent_path=category_path,
                    )
                    continue
                raise CatalogLoadError(
                    f"Unexpected category item in {source_path}: {type(item).__name__}"
                )
            continue

        if isinstance(children, dict):
            walk_tree_entries(
                children,
                source_path,
                on_category=on_category,
                on_block=on_block,
                parent_path=category_path,
            )
            continue

        raise CatalogLoadError(
            f"Unexpected category payload in {source_path}: {type(children).__name__}"
        )


def get_catalog_snapshot(catalog_root: str | Path | None = None) -> CatalogSnapshot:
    """Return the cached raw GNU catalog snapshot for the resolved root."""
    root = discover_catalog_root(catalog_root).resolve()
    return _get_cached_catalog_snapshot(str(root))


def build_catalog_snapshot(catalog_root: str | Path | None = None) -> CatalogSnapshot:
    """Build a fresh raw GNU catalog snapshot for the resolved root."""
    root = discover_catalog_root(catalog_root).resolve()
    return _build_catalog_snapshot_for_root(root)


def find_block_source(
    block_id: str,
    *,
    catalog_root: str | Path | None = None,
) -> RawCatalogBlock:
    """Return one raw block record from the resolved GNU catalog snapshot."""
    snapshot = get_catalog_snapshot(catalog_root)
    record = snapshot.blocks.get(block_id)
    if record is None:
        raise BlockNotFoundError(block_id, catalog_root=str(snapshot.root))
    return record


@lru_cache(maxsize=4)
def _get_cached_catalog_snapshot(root_text: str) -> CatalogSnapshot:
    return _build_catalog_snapshot_for_root(Path(root_text))


def _build_catalog_snapshot_for_root(root: Path) -> CatalogSnapshot:
    files = collect_catalog_files(root)
    validate_catalog_files(root, files)

    block_category_paths: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for tree_path in files.tree:
        tree_payload = load_yaml_mapping(tree_path)
        walk_tree_entries(
            tree_payload,
            tree_path,
            on_block=lambda category_path, block_id: block_category_paths[block_id].add(category_path),
        )

    blocks: dict[str, RawCatalogBlock] = {}
    for block_path in files.block:
        block_payload = load_yaml_mapping(block_path)
        block_id = require_string(block_payload, "id", path=block_path)
        if block_id in blocks:
            raise CatalogLoadError(f"Duplicate GNU block id in catalog: {block_id}")
        blocks[block_id] = RawCatalogBlock(
            block_id=block_id,
            path=block_path,
            payload=block_payload,
            category_paths=tuple(sorted(block_category_paths.get(block_id, set()))),
        )

    return CatalogSnapshot(root=root, files=files, blocks=blocks)


# -- describe --

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

    flags = preserved_string_values(payload.get("flags"))
    asserts = preserved_string_values(payload.get("asserts"))
    signature = build_signature(raw_block.block_id, parameters)

    return BlockDescription(
        block_id=raw_block.block_id,
        label=label,
        category_path=list(category_path),
        flags=list(flags),
        parameters=parameters,
        inputs=inputs,
        outputs=outputs,
        asserts=list(asserts),
        documentation=optional_string(payload.get("documentation")),
        doc_url=optional_string(payload.get("doc_url")),
        warnings=warnings,
        signature=signature,
    )


# -- native GRC param filtering helpers (see docs/GNU_NATIVE_METHODS.md) --


def evaluated_param_hides_for_block(
    block_id: str, param_values: dict[str, Any] | None = None
) -> dict[str, str]:
    """GRC-core-evaluated ``hide`` value per param key for a catalog-context lookup.

    Thin wrapper around :func:`grc_agent.runtime.block_semantics.evaluated_param_hides`
    for use by :meth:`BlockDescription.to_payload` — avoids leaking the
    runtime module into the catalog discovery layer's public API while
    still consulting the same GRC-evaluated signal.
    """
    from grc_agent.runtime.block_semantics import evaluated_param_hides

    return evaluated_param_hides(block_id, param_values or {})


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
