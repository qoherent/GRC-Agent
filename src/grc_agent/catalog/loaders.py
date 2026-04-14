"""Shared GNU catalog discovery and raw metadata loading."""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import yaml

from .errors import BlockNotFoundError, CatalogLoadError
from .schema import CatalogFiles, CatalogSnapshot, RawCatalogBlock

DEFAULT_GRC_CATALOG_ROOTS = (
    Path("/usr/share/gnuradio/grc/blocks"),
    Path("/usr/local/share/gnuradio/grc/blocks"),
)

try:
    _YAML_SAFE_LOADER = yaml.CSafeLoader
except AttributeError:  # pragma: no cover - depends on libyaml availability.
    _YAML_SAFE_LOADER = yaml.SafeLoader

_CategoryCallback = Callable[[tuple[str, ...]], None]
_BlockCallback = Callable[[tuple[str, ...], str], None]


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


def clear_catalog_snapshot_cache() -> None:
    """Clear the cached raw GNU catalog snapshot."""
    _get_cached_catalog_snapshot.cache_clear()


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
