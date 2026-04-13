"""Catalog and session graph construction for bounded retrieval."""

from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from grc_agent.flowgraph_session import FlowgraphSession
from grc_agent.models import Connection

from .graphify_adapter import GraphifyAdapterError, build_graph, graphify_status
from .provenance import catalog_provenance, session_provenance
from .schema import (
    GRAPHIFY_EDGE_CONFIDENCE,
    GRAPHIFY_NODE_FILE_TYPE,
    IndexedNode,
    PreparedSearchNode,
    RetrievalIndex,
    build_error_payload,
)
from .text import expand_terms, normalize_text, tokenize_text

DEFAULT_GRC_CATALOG_ROOTS = (
    Path("/usr/share/gnuradio/grc/blocks"),
    Path("/usr/local/share/gnuradio/grc/blocks"),
)

MAX_RELATED_LABELS = 8
MAX_SUMMARY_CHARS = 240
MAX_RESULT_SUMMARY_CHARS = 160

try:
    _YAML_SAFE_LOADER = yaml.CSafeLoader
except AttributeError:  # pragma: no cover - depends on libyaml availability.
    _YAML_SAFE_LOADER = yaml.SafeLoader


class RetrievalIndexError(RuntimeError):
    """Raised when retrieval indexes cannot be built or prepared."""


def discover_catalog_root(catalog_root: str | Path | None = None) -> Path:
    """Return the system GNU Radio catalog root used for Phase 1 retrieval."""
    if catalog_root is not None:
        resolved_root = Path(catalog_root).expanduser()
        if not resolved_root.is_dir():
            raise RetrievalIndexError(f"GNU Radio catalog root not found: {resolved_root}")
        return resolved_root

    for root in DEFAULT_GRC_CATALOG_ROOTS:
        if root.is_dir():
            return root

    checked_roots = ", ".join(str(root) for root in DEFAULT_GRC_CATALOG_ROOTS)
    raise RetrievalIndexError(f"GNU Radio catalog root not found. Checked: {checked_roots}")


def initialize_retrieval(
    *,
    catalog_root: str | Path | None = None,
    warm_catalog: bool = False,
) -> dict[str, Any]:
    """Run bounded retrieval readiness checks and optionally warm the catalog index."""
    status = graphify_status()
    if not status["ok"]:
        return build_error_payload(
            error_type="RetrievalNotReady",
            message=str(status["message"]),
            details={"graphify_version": status["version"]},
        )

    try:
        root = discover_catalog_root(catalog_root)
        files = _collect_catalog_files(root)
        _validate_catalog_files(root, files)
    except RetrievalIndexError as exc:
        return build_error_payload(error_type="RetrievalNotReady", message=str(exc))

    payload: dict[str, Any] = {
        "ok": True,
        "message": "Retrieval ready.",
        "graphify_version": status["version"],
        "catalog_root": str(root),
        "catalog_files": {
            "block": len(files["block"]),
            "tree": len(files["tree"]),
            "domain": len(files["domain"]),
        },
        "catalog_index_warmed": False,
    }

    if warm_catalog:
        try:
            index = get_catalog_index(root)
        except (GraphifyAdapterError, RetrievalIndexError) as exc:
            return build_error_payload(error_type="RetrievalNotReady", message=str(exc))
        payload["catalog_index_warmed"] = True
        payload["catalog_index"] = {
            "nodes": index.graph.number_of_nodes(),
            "edges": index.graph.number_of_edges(),
        }

    return payload


def clear_catalog_index_cache() -> None:
    """Clear the in-memory catalog index cache."""
    _get_cached_catalog_index.cache_clear()


def get_catalog_index(catalog_root: str | Path | None = None) -> RetrievalIndex:
    """Return the cached catalog retrieval index for the resolved root."""
    root = discover_catalog_root(catalog_root).resolve()
    return _get_cached_catalog_index(str(root))


def build_catalog_index(catalog_root: str | Path | None = None) -> RetrievalIndex:
    """Build a fresh catalog retrieval index for the resolved GNU Radio metadata root."""
    root = discover_catalog_root(catalog_root).resolve()
    return _build_catalog_index_for_root(root)


def build_session_index(
    session: FlowgraphSession,
    *,
    catalog_index: RetrievalIndex | None = None,
) -> RetrievalIndex:
    """Build a retrieval index for one loaded active `.grc` session."""
    flowgraph = session.flowgraph
    if flowgraph is None:
        raise RetrievalIndexError("Session scope requires a loaded flowgraph.")

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    records: dict[str, IndexedNode] = {}
    session_path = Path(session.path) if session.path is not None else None
    block_by_name = {block.instance_name: block for block in flowgraph.blocks}
    upstream_names, downstream_names = _build_session_adjacency(flowgraph.connections)
    catalog_blocks = _catalog_block_lookup(catalog_index) if catalog_index is not None else {}

    for block in flowgraph.blocks:
        catalog_record = catalog_blocks.get(block.block_type)
        parameter_map = _coerce_parameter_map(block.params.get("parameters"))
        parameter_pairs = [f"{key}={_compact_text(value)}" for key, value in parameter_map.items()]
        incoming = upstream_names.get(block.instance_name, [])
        outgoing = downstream_names.get(block.instance_name, [])
        catalog_categories = catalog_record.related_node_labels if catalog_record is not None else []
        block_description = (
            catalog_record.block_description
            if catalog_record is not None
            else f"Session block of type {block.block_type}."
        )
        field_summary = _truncate(
            _join_non_empty(
                _format_key_value_group("parameters", parameter_pairs[:6]),
                _format_label_group("incoming", incoming[:4]),
                _format_label_group("outgoing", outgoing[:4]),
                catalog_record.field_summary if catalog_record is not None else "",
            ),
            MAX_SUMMARY_CHARS,
        )
        adjacency_summary = _truncate(
            _join_non_empty(
                _format_label_group("incoming", incoming[:4]),
                _format_label_group("outgoing", outgoing[:4]),
                _format_label_group("catalog context", catalog_categories[:4]),
            ),
            MAX_SUMMARY_CHARS,
        )
        block_record = IndexedNode(
            node_id=f"session:block:{block.instance_name}",
            node_type="session_block",
            label=block.instance_name,
            source_scope="session",
            provenance=session_provenance(
                session_path,
                f"blocks[{block.instance_name}]",
                kind="session_block",
            ),
            search_fields={
                "label": block.instance_name,
                "identifier": f"{block.instance_name} {block.block_type}",
                "summary": _join_non_empty(
                    block_description,
                    field_summary,
                    " ".join(catalog_categories),
                ),
                "related": _join_non_empty(
                    " ".join(incoming),
                    " ".join(outgoing),
                    " ".join(parameter_map.keys()),
                    " ".join(_compact_text(value) for value in parameter_map.values()),
                    " ".join(parameter_pairs),
                    " ".join(catalog_categories),
                ),
            },
            block_id=block.block_type,
            summary=_build_result_summary(block_description, field_summary, adjacency_summary),
            block_description=_truncate(block_description, MAX_SUMMARY_CHARS),
            field_summary=field_summary or None,
            adjacency_summary=adjacency_summary or None,
        )
        _append_indexed_node(nodes, records, block_record)

    for index, connection in enumerate(flowgraph.connections):
        source_block = block_by_name.get(connection.src_block)
        target_block = block_by_name.get(connection.dst_block)
        if source_block is None or target_block is None:
            continue
        _append_edge(
            edges,
            source=f"session:block:{connection.src_block}",
            target=f"session:block:{connection.dst_block}",
            relation=f"connects:{connection.src_port}->{connection.dst_port}:{index}",
            source_file=session_path,
        )

    graph = build_graph({"nodes": nodes, "edges": edges}, directed=True)
    index = RetrievalIndex(
        scope="session",
        graph=graph,
        node_records=records,
        metadata={"flowgraph_path": str(session_path) if session_path is not None else None},
    )
    _populate_related_labels(index)
    index.prepared_records, index.token_index = _prepare_search_records(index.node_records)
    return index


@lru_cache(maxsize=4)
def _get_cached_catalog_index(root_text: str) -> RetrievalIndex:
    return _build_catalog_index_for_root(Path(root_text))


def _build_catalog_index_for_root(root: Path) -> RetrievalIndex:
    files = _collect_catalog_files(root)
    _validate_catalog_files(root, files)
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    records: dict[str, IndexedNode] = {}
    block_categories: dict[str, set[str]] = defaultdict(set)
    block_category_nodes: dict[str, set[str]] = defaultdict(set)

    for domain_path in files["domain"]:
        domain_payload = _load_yaml_mapping(domain_path)
        domain_id = _require_string(domain_payload, "id", path=domain_path)
        domain_label = _require_string(domain_payload, "label", path=domain_path)
        domain_record = IndexedNode(
            node_id=f"catalog:domain:{domain_id}",
            node_type="domain",
            label=domain_label,
            source_scope="catalog",
            provenance=catalog_provenance(
                domain_path,
                f"domains[{domain_id}]",
                kind="catalog_domain",
            ),
            search_fields={
                "label": domain_label,
                "identifier": domain_id,
                "summary": _join_non_empty(
                    f"input fan-in {_compact_text(domain_payload.get('multiple_connections_per_input'))}",
                    f"output fan-out {_compact_text(domain_payload.get('multiple_connections_per_output'))}",
                    _compact_text(domain_payload.get("templates")),
                ),
                "related": "",
            },
            summary=_build_result_summary(
                _join_non_empty(
                    f"multiple input connections: {_compact_text(domain_payload.get('multiple_connections_per_input'))}",
                    f"multiple output connections: {_compact_text(domain_payload.get('multiple_connections_per_output'))}",
                )
            ),
            field_summary=_truncate(
                _join_non_empty(
                    f"multiple input connections: {_compact_text(domain_payload.get('multiple_connections_per_input'))}",
                    f"multiple output connections: {_compact_text(domain_payload.get('multiple_connections_per_output'))}",
                ),
                MAX_SUMMARY_CHARS,
            )
            or None,
        )
        _append_indexed_node(nodes, records, domain_record)

    for tree_path in files["tree"]:
        tree_payload = yaml.load(tree_path.read_text(encoding="utf-8"), Loader=_YAML_SAFE_LOADER)
        if not isinstance(tree_payload, dict):
            raise RetrievalIndexError(f"Tree metadata must be a mapping: {tree_path}")
        _walk_tree_categories(
            tree_payload,
            tree_path,
            nodes=nodes,
            edges=edges,
            records=records,
            block_categories=block_categories,
            block_category_nodes=block_category_nodes,
        )

    for block_path in files["block"]:
        block_payload = _load_yaml_mapping(block_path)
        block_id = _require_string(block_payload, "id", path=block_path)
        block_label = _require_string(block_payload, "label", path=block_path)
        documentation = _truncate(_compact_text(block_payload.get("documentation")), MAX_SUMMARY_CHARS)
        parameters = _coerce_mapping_list(block_payload.get("parameters"))
        inputs = _coerce_mapping_list(block_payload.get("inputs"))
        outputs = _coerce_mapping_list(block_payload.get("outputs"))
        flags = _string_values(block_payload.get("flags"))
        parameter_ids = [_require_string(item, "id", path=block_path) for item in parameters]
        parameter_labels = [
            _compact_text(parameter.get("label")) or parameter_id
            for parameter, parameter_id in zip(parameters, parameter_ids, strict=False)
        ]
        input_signatures = [_port_signature("input", item, index) for index, item in enumerate(inputs)]
        output_signatures = [_port_signature("output", item, index) for index, item in enumerate(outputs)]
        port_domains = sorted(
            {
                _compact_text(port_payload.get("domain"))
                for port_payload in [*inputs, *outputs]
                if _compact_text(port_payload.get("domain"))
            }
        )
        category_parts = _split_category_path(block_payload.get("category"))
        if category_parts:
            category_node_id = _ensure_category_path(
                category_parts,
                block_path,
                nodes=nodes,
                edges=edges,
                records=records,
            )
            block_categories[block_id].add(" > ".join(category_parts))
            block_category_nodes[block_id].add(category_node_id)
        category_labels = sorted(block_categories.get(block_id, set()))
        block_description = documentation or _truncate(
            f"{block_label} ({block_id}) with {len(inputs)} input port(s), "
            f"{len(outputs)} output port(s), and {len(parameters)} parameter(s).",
            MAX_SUMMARY_CHARS,
        )
        field_summary = _truncate(
            _join_non_empty(
                _format_label_group("parameters", parameter_ids[:8]),
                _format_label_group("inputs", input_signatures[:4]),
                _format_label_group("outputs", output_signatures[:4]),
                _format_label_group("categories", category_labels[:4]),
                _format_label_group("flags", flags[:4]),
            ),
            MAX_SUMMARY_CHARS,
        )
        adjacency_summary = _truncate(
            _join_non_empty(
                _format_label_group("categories", category_labels[:4]),
                _format_label_group("parameters", parameter_ids[:6]),
                _format_label_group("ports", input_signatures[:2] + output_signatures[:2]),
            ),
            MAX_SUMMARY_CHARS,
        )
        block_record = IndexedNode(
            node_id=f"catalog:block:{block_id}",
            node_type="block",
            label=block_label,
            source_scope="catalog",
            provenance=catalog_provenance(
                block_path,
                f"blocks[{block_id}]",
                kind="catalog_block",
            ),
            search_fields={
                "label": block_label,
                "identifier": block_id,
                "summary": _join_non_empty(
                    block_description,
                    field_summary,
                    _compact_text(block_payload.get("doc_url")),
                ),
                "related": _join_non_empty(
                    " ".join(category_labels),
                    " ".join(flags),
                    " ".join(parameter_ids),
                    " ".join(parameter_labels),
                    " ".join(input_signatures[:6]),
                    " ".join(output_signatures[:6]),
                    " ".join(port_domains),
                ),
            },
            block_id=block_id,
            summary=_build_result_summary(block_description, field_summary, adjacency_summary),
            block_description=block_description,
            field_summary=field_summary or None,
            adjacency_summary=adjacency_summary or None,
        )
        _append_indexed_node(nodes, records, block_record)

        for category_node_id in sorted(block_category_nodes.get(block_id, set())):
            _append_edge(
                edges,
                source=category_node_id,
                target=block_record.node_id,
                relation="contains_block",
                source_file=block_path,
            )

        for domain_name in port_domains:
            domain_node_id = f"catalog:domain:{domain_name}"
            if domain_node_id in records:
                _append_edge(
                    edges,
                    source=block_record.node_id,
                    target=domain_node_id,
                    relation="uses_domain",
                    source_file=block_path,
                )

    graph = build_graph({"nodes": nodes, "edges": edges}, directed=True)
    index = RetrievalIndex(
        scope="catalog",
        graph=graph,
        node_records=records,
        metadata={
            "catalog_root": str(root),
            "file_counts": {
                "block": len(files["block"]),
                "tree": len(files["tree"]),
                "domain": len(files["domain"]),
            },
        },
    )
    _populate_related_labels(index)
    index.prepared_records, index.token_index = _prepare_search_records(index.node_records)
    return index


def _collect_catalog_files(root: Path) -> dict[str, list[Path]]:
    return {
        "block": sorted(root.rglob("*.block.yml")),
        "tree": sorted(root.rglob("*.tree.yml")),
        "domain": sorted(root.rglob("*.domain.yml")),
    }


def _validate_catalog_files(root: Path, files: dict[str, list[Path]]) -> None:
    counts = {name: len(paths) for name, paths in files.items()}
    missing_kinds = [name for name, count in counts.items() if count == 0]
    if not missing_kinds:
        return

    missing_labels = ", ".join(f".{name}.yml" for name in missing_kinds)
    raise RetrievalIndexError(
        f"GNU Radio catalog metadata is incomplete at {root}: missing {missing_labels} "
        f"(found {counts['block']} .block.yml, {counts['tree']} .tree.yml, "
        f"{counts['domain']} .domain.yml)."
    )


def _records_are_compatible(existing: IndexedNode, new: IndexedNode) -> bool:
    return (
        existing.node_type == new.node_type
        and existing.label == new.label
        and existing.source_scope == new.source_scope
        and existing.search_fields == new.search_fields
        and existing.block_id == new.block_id
        and existing.summary == new.summary
        and existing.block_description == new.block_description
        and existing.field_summary == new.field_summary
        and existing.adjacency_summary == new.adjacency_summary
    )


def _append_indexed_node(
    nodes: list[dict[str, Any]],
    records: dict[str, IndexedNode],
    record: IndexedNode,
) -> None:
    existing_record = records.get(record.node_id)
    if existing_record is not None:
        if _records_are_compatible(existing_record, record):
            return
        raise RetrievalIndexError(f"Conflicting duplicate retrieval node id: {record.node_id}")

    records[record.node_id] = record
    nodes.append(
        {
            "id": record.node_id,
            "label": record.label,
            "file_type": GRAPHIFY_NODE_FILE_TYPE,
            "source_file": record.provenance.path,
            "node_type": record.node_type,
            "source_scope": record.source_scope,
            "provenance": record.provenance.to_dict(),
            "search_fields": dict(record.search_fields),
            "block_id": record.block_id,
            "summary": record.summary,
            "block_description": record.block_description,
            "field_summary": record.field_summary,
            "adjacency_summary": record.adjacency_summary,
            "related_node_labels": list(record.related_node_labels),
        }
    )


def _append_edge(
    edges: list[dict[str, Any]],
    *,
    source: str,
    target: str,
    relation: str,
    source_file: Path | None,
) -> None:
    edges.append(
        {
            "source": source,
            "target": target,
            "relation": relation,
            "confidence": GRAPHIFY_EDGE_CONFIDENCE,
            "source_file": str(source_file) if source_file is not None else "<in-memory-flowgraph>",
        }
    )


def _walk_tree_categories(
    payload: dict[str, Any],
    source_path: Path,
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    records: dict[str, IndexedNode],
    block_categories: dict[str, set[str]],
    block_category_nodes: dict[str, set[str]],
    parent_path: list[str] | None = None,
) -> None:
    path_prefix = [] if parent_path is None else list(parent_path)
    for category_name, children in payload.items():
        child_path = path_prefix + [str(category_name)]
        category_node_id = _ensure_category_path(
            child_path,
            source_path,
            nodes=nodes,
            edges=edges,
            records=records,
        )
        if isinstance(children, list):
            for item in children:
                if isinstance(item, str):
                    category_path = " > ".join(child_path)
                    block_categories[item].add(category_path)
                    block_category_nodes[item].add(category_node_id)
                    continue
                if isinstance(item, dict):
                    _walk_tree_categories(
                        item,
                        source_path,
                        nodes=nodes,
                        edges=edges,
                        records=records,
                        block_categories=block_categories,
                        block_category_nodes=block_category_nodes,
                        parent_path=child_path,
                    )
                    continue
                raise RetrievalIndexError(
                    f"Unexpected category item in {source_path}: {type(item).__name__}"
                )
            continue
        if isinstance(children, dict):
            _walk_tree_categories(
                children,
                source_path,
                nodes=nodes,
                edges=edges,
                records=records,
                block_categories=block_categories,
                block_category_nodes=block_category_nodes,
                parent_path=child_path,
            )
            continue
        raise RetrievalIndexError(f"Unexpected category payload in {source_path}: {type(children).__name__}")


def _ensure_category_path(
    parts: list[str],
    source_path: Path,
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    records: dict[str, IndexedNode],
) -> str:
    parent_node_id: str | None = None
    current_node_id = ""
    for depth in range(len(parts)):
        current_parts = parts[: depth + 1]
        current_path = " > ".join(current_parts)
        current_node_id = "catalog:category:" + "/".join(current_parts)
        if current_node_id not in records:
            category_record = IndexedNode(
                node_id=current_node_id,
                node_type="category",
                label=current_parts[-1],
                source_scope="catalog",
                provenance=catalog_provenance(
                    source_path,
                    f"categories[{current_path}]",
                    kind="catalog_category",
                ),
                search_fields={
                    "label": current_parts[-1],
                    "identifier": current_path,
                    "summary": f"Block-tree category {current_path}",
                    "related": "",
                },
                summary=_build_result_summary(f"Block-tree category {current_path}"),
                field_summary=_truncate(f"Block-tree category {current_path}", MAX_SUMMARY_CHARS),
            )
            _append_indexed_node(nodes, records, category_record)
        if parent_node_id is not None:
            _append_edge(
                edges,
                source=parent_node_id,
                target=current_node_id,
                relation="contains_category",
                source_file=source_path,
            )
        parent_node_id = current_node_id
    return current_node_id


def _populate_related_labels(index: RetrievalIndex) -> None:
    for record in index.node_records.values():
        neighbor_ids = {
            *index.graph.predecessors(record.node_id),
            *index.graph.successors(record.node_id),
        }
        related_labels = sorted(
            {
                index.node_records[node_id].label
                for node_id in neighbor_ids
                if node_id in index.node_records and index.node_records[node_id].label != record.label
            }
        )
        record.related_node_labels = related_labels[:MAX_RELATED_LABELS]
        if record.related_node_labels:
            record.search_fields["related"] = _join_non_empty(
                record.search_fields.get("related", ""),
                " ".join(record.related_node_labels),
            )
            if not record.adjacency_summary:
                record.adjacency_summary = _truncate(
                    _format_label_group("related", record.related_node_labels[:5]),
                    MAX_SUMMARY_CHARS,
                )
        if not record.summary:
            record.summary = _build_result_summary(
                record.block_description or "",
                record.field_summary or "",
                record.adjacency_summary or "",
            )


def _prepare_search_records(
    records: dict[str, IndexedNode],
) -> tuple[dict[str, PreparedSearchNode], dict[str, frozenset[str]]]:
    prepared_records: dict[str, PreparedSearchNode] = {}
    token_index: dict[str, set[str]] = defaultdict(set)

    for node_id, record in records.items():
        normalized_fields: dict[str, str] = {}
        field_terms: dict[str, frozenset[str]] = {}
        for field_name, raw_value in record.search_fields.items():
            normalized_value = normalize_text(raw_value)
            if not normalized_value:
                continue
            normalized_fields[field_name] = normalized_value
            terms = frozenset(expand_terms(tokenize_text(normalized_value)))
            field_terms[field_name] = terms
            for term in terms:
                token_index[term].add(node_id)

        prepared_records[node_id] = PreparedSearchNode(
            normalized_fields=normalized_fields,
            field_terms=field_terms,
        )

    return prepared_records, {term: frozenset(node_ids) for term, node_ids in token_index.items()}


def _catalog_block_lookup(index: RetrievalIndex | None) -> dict[str, IndexedNode]:
    if index is None:
        return {}
    return {
        record.block_id: record
        for record in index.node_records.values()
        if record.node_type == "block" and record.block_id is not None
    }


def _build_session_adjacency(
    connections: list[Connection],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    upstream_names: dict[str, list[str]] = defaultdict(list)
    downstream_names: dict[str, list[str]] = defaultdict(list)
    for connection in connections:
        upstream_names[connection.dst_block].append(connection.src_block)
        downstream_names[connection.src_block].append(connection.dst_block)
    return (
        {key: sorted(set(value)) for key, value in upstream_names.items()},
        {key: sorted(set(value)) for key, value in downstream_names.items()},
    )


def _coerce_parameter_map(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _coerce_mapping_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    payload = yaml.load(path.read_text(encoding="utf-8"), Loader=_YAML_SAFE_LOADER)
    if not isinstance(payload, dict):
        raise RetrievalIndexError(f"YAML metadata must be a mapping: {path}")
    return payload


def _require_string(payload: dict[str, Any], key: str, *, path: Path) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RetrievalIndexError(f"{path} is missing a non-empty '{key}' field.")
    return value


def _compact_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        parts = [_join_non_empty(str(key), _compact_text(item)) for key, item in value.items()]
        return "; ".join(part for part in parts if part)
    if isinstance(value, (list, tuple, set)):
        parts = [_compact_text(item) for item in value]
        return ", ".join(part for part in parts if part)
    return str(value)


def _split_category_path(value: Any) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return []
    return [part.strip() for part in value.split("/") if part.strip()]


def _string_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [_compact_text(item) for item in value if _compact_text(item)]
    compact = _compact_text(value)
    return [compact] if compact else []


def _join_non_empty(*parts: str) -> str:
    return " ".join(part for part in parts if part).strip()


def _format_label_group(label: str, values: list[str]) -> str:
    cleaned_values = [value for value in values if value]
    if not cleaned_values:
        return ""
    return f"{label}: {', '.join(cleaned_values)}"


def _format_key_value_group(label: str, values: list[str]) -> str:
    cleaned_values = [value for value in values if value]
    if not cleaned_values:
        return ""
    return f"{label}: {', '.join(cleaned_values)}"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _build_result_summary(*parts: str) -> str | None:
    summary = _truncate(_join_non_empty(*parts), MAX_RESULT_SUMMARY_CHARS)
    return summary or None


def _port_signature(direction: str, payload: dict[str, Any], index: int) -> str:
    return _join_non_empty(
        direction,
        str(index),
        _compact_text(payload.get("label")),
        _compact_text(payload.get("id")),
        _compact_text(payload.get("domain")),
        _compact_text(payload.get("dtype")),
    )
