"""Read-only local vector retrieval backed by Qdrant local mode + FastEmbed."""

from __future__ import annotations

from collections import Counter
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import fcntl
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import TracebackType
from typing import Any
import uuid

from qdrant_client import QdrantClient, models

from grc_agent._payload import ErrorCode, build_error_payload, join_non_empty
from grc_agent.catalog.describe import _build_block_description
from grc_agent.catalog.loaders import build_catalog_snapshot, discover_catalog_root
from grc_agent.catalog.normalize import compact_text
from grc_agent.manual import DEFAULT_MANUAL_ROOT, clean_manual_page

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_VECTOR_COLLECTION_ALIAS = "grc_agent_retrieval_v1"
INDEX_SCHEMA_VERSION = "2026-05-22-vector-v2"
MISS_INTAKE_SCHEMA_VERSION = "2026-04-28-vector-miss-intake-v1"
CORPUS_VERSION = "2026-04-28"
SOURCE_TYPE_CATALOG_BLOCK = "catalog_block"
SOURCE_TYPE_MANUAL_CHUNK = "manual_chunk"
SOURCE_TYPE_TUTORIAL_CHUNK = "tutorial_chunk"
VALID_VECTOR_SCOPES = frozenset({"all", "catalog", "manual", "tutorial"})
VALID_MISS_SOURCES = frozenset({"real_user", "eval", "manual_review"})
VALID_MISS_CATEGORIES = frozenset(
    {
        "untriaged",
        "missing_metadata",
        "bad_record_text",
        "chunking_issue",
        "embedding_limitation",
        "ambiguous_wording",
        "bad_eval_expectation",
        "exact_id_regression",
        "false_positive_regression",
        "should_clarify",
        "should_remain_miss",
        "source_type_regression",
        "real_user",
    }
)
DEFAULT_VECTOR_LIMIT = 5
MAX_VECTOR_LIMIT = 10
MAX_QUERY_CHARS = 800
MAX_EXCERPT_CHARS = 700
RERANK_FETCH_MULTIPLIER = 4
MIN_RERANK_FETCH_LIMIT = 20
MAX_RERANK_FETCH_LIMIT = 50
MAX_RERANK_DOCS_PER_SOURCE = 2
DEFAULT_TUTORIAL_MANIFEST = DEFAULT_MANUAL_ROOT / "tutorial_manifest.txt"
FORBIDDEN_RESULT_KEYS = frozenset(
    {
        "transaction",
        "params",
        "insert_tool_args",
        "apply_edit",
        "save_graph",
        "block_recipe",
        "default_mutation_values",
        "repair_plan",
        "allowlist",
        "blacklist",
    }
)
CATALOG_SEMANTIC_METADATA: dict[str, dict[str, Any]] = {
    "low_pass_filter": {
        "field": "aliases",
        "aliases": ("audio smoother", "smooth audio", "smoothing filter"),
        "reason": "Low-pass filters attenuate rapid/high-frequency changes and are commonly used as smoothing filters.",
        "helped_queries": ("audio smoother", "smooth audio", "smoothing filter"),
        "false_positive_checks": ("low_pass_filter", "disable low pass filter"),
    },
    "high_pass_filter": {
        "field": "aliases",
        "aliases": (
            "reject low frequencies",
            "remove bass rumble",
            "keep high frequency content",
            "high frequency pass filter",
        ),
        "reason": "High-pass filters attenuate low-frequency content while passing higher-frequency signal components.",
        "helped_queries": ("reject low frequencies", "remove bass rumble"),
        "false_positive_checks": ("high_pass_filter", "delete high_pass_filter"),
    },
    "analog_agc_xx": {
        "field": "aliases",
        "aliases": (
            "automatic gain control",
            "auto gain",
            "gain stabilizer",
            "volume stabilizer",
            "stabilize volume",
            "normalize signal level",
            "keep amplitude steady",
            "amplitude leveler",
            "leveler block",
        ),
        "reason": "AGC blocks automatically adjust gain to stabilize signal amplitude around a reference level.",
        "helped_queries": ("automatic gain control", "stabilize volume", "amplitude regulator"),
        "false_positive_checks": ("analog_agc_xx", "transaction analog_agc_xx"),
    },
    "qtgui_freq_sink_x": {
        "field": "aliases",
        "aliases": (
            "spectrum display",
            "frequency display",
            "show spectrum",
            "spectral plot",
            "frequency sink",
            "visualize channels",
            "occupied bandwidth",
            "channel spectrum",
        ),
        "reason": "QT GUI frequency sinks visualize frequency-domain content and spectrum occupancy.",
        "helped_queries": ("spectrum display", "frequency display", "fft view"),
        "false_positive_checks": ("qtgui_freq_sink_x", "block recipe qtgui_freq_sink_x"),
    },
    "qtgui_waterfall_sink_x": {
        "field": "aliases",
        "aliases": (
            "spectrum waterfall",
            "waterfall display",
            "spectral plot",
            "visualize channels",
            "occupied bandwidth",
        ),
        "reason": "QT GUI waterfall sinks show spectrum content over time as a waterfall display.",
        "helped_queries": ("waterfall display", "spectral plot", "see occupied bandwidth"),
        "false_positive_checks": ("qtgui_waterfall_sink_x", "insert qtgui_waterfall_sink_x"),
    },
    "blocks_throttle2": {
        "field": "aliases",
        "aliases": ("rate limiter", "sample rate limiter", "throttle stream"),
        "reason": "Throttle blocks pace sample flow and limit processing rate in non-hardware flowgraphs.",
        "helped_queries": ("rate limiter", "sample rate limiter", "throttle stream"),
        "false_positive_checks": ("blocks_throttle2", "save blocks_throttle2"),
    },
    "blocks_throttle": {
        "field": "aliases",
        "aliases": ("rate limiter", "sample rate limiter", "throttle stream"),
        "reason": "Throttle blocks pace sample flow and limit processing rate in non-hardware flowgraphs.",
        "helped_queries": ("rate limiter", "sample rate limiter", "throttle stream"),
        "false_positive_checks": ("blocks_throttle", "save blocks_throttle"),
    },
    "qtgui_time_sink_x": {
        "field": "aliases",
        "aliases": (
            "scope trace",
            "oscilloscope",
            "time trace",
            "waveform display",
            "plot signal amplitude",
            "time domain graph",
            "sample waveform viewer",
        ),
        "reason": "QT GUI time sinks display sample amplitude over time like an oscilloscope trace.",
        "helped_queries": ("scope trace", "oscilloscope", "waveform display"),
        "false_positive_checks": ("qtgui_time_sink_x", "apply qtgui_time_sink_x"),
    },
    "blocks_file_source": {
        "field": "aliases",
        "aliases": ("read samples from a file", "file input source", "stream samples from file"),
        "reason": "File Source reads sample streams from a configured file and provides them to the flowgraph.",
        "helped_queries": ("read samples from a file", "file input source"),
        "false_positive_checks": ("blocks_file_source", "insert blocks_file_source"),
    },
    "blocks_head": {
        "field": "aliases",
        "aliases": (
            "stop after fixed number of samples",
            "limit stream length",
            "take first samples",
            "finite sample count",
        ),
        "reason": "Head passes only the first configured number of items and then stops forwarding stream data.",
        "helped_queries": ("stop after a fixed number of samples", "limit stream length"),
        "false_positive_checks": ("blocks_head", "delete blocks_head"),
    },
    "blocks_null_sink": {
        "field": "aliases",
        "aliases": ("drop output samples", "discard stream data", "throw away samples", "sink to nowhere"),
        "reason": "Null Sink consumes stream items and intentionally discards them without producing output.",
        "helped_queries": ("drop output samples", "discard stream data"),
        "false_positive_checks": ("blocks_null_sink", "insert_tool_args blocks_null_sink"),
    },
    "blocks_vector_source_x": {
        "field": "aliases",
        "aliases": ("repeat a known sample sequence", "constant vector source", "known sample sequence"),
        "reason": "Vector Source emits configured vector data, optionally repeating it as a deterministic sample sequence.",
        "helped_queries": ("repeat a known sample sequence", "constant vector source"),
        "false_positive_checks": ("blocks_vector_source_x", "delete block blocks_vector_source_x"),
    },
    "blocks_add_xx": {
        "field": "aliases",
        "aliases": ("sum signals together", "add two streams", "add signals"),
        "reason": "Add blocks sum corresponding stream items from multiple inputs.",
        "helped_queries": ("sum signals together", "add two streams"),
        "false_positive_checks": ("blocks_add_xx", "remove_connection blocks_add_xx"),
    },
    "blocks_message_strobe": {
        "field": "aliases",
        "aliases": ("send a PMT message repeatedly", "periodic message generator", "repeated PMT message"),
        "reason": "Message Strobe periodically emits a configured PMT message.",
        "helped_queries": ("send a PMT message repeatedly", "periodic message generator"),
        "false_positive_checks": ("blocks_message_strobe", "repair plan blocks_message_strobe"),
    },
    "digital_constellation_decoder_cb": {
        "field": "aliases",
        "aliases": ("map constellation points to bits", "decode constellation symbols"),
        "reason": "Constellation Decoder maps received constellation points to decoded symbol or bit decisions.",
        "helped_queries": ("map constellation points to bits", "decode constellation symbols"),
        "false_positive_checks": (
            "digital_constellation_decoder_cb",
            "delete digital_constellation_decoder_cb",
        ),
    },
}
CATALOG_SEMANTIC_ALIASES: dict[str, tuple[str, ...]] = {
    block_id: metadata["aliases"]
    for block_id, metadata in CATALOG_SEMANTIC_METADATA.items()
}
_POINT_NAMESPACE = uuid.UUID("f7ed52a5-8a1c-4ffd-a138-c3cf26d3121f")
_MANIFEST_FILE = "manifest.json"
_LOCK_FILE = "index.lock"
_INTAKE_PATH_PATTERN = re.compile(
    r"(?ix)(?:[a-z]:\\[^\s]+|(?:~|/)[^\s]+|\b[\w.-]+\.grc\b)"
)
_MISS_TOPIC_STOP_TOKENS = frozenset(
    {
        "show",
        "view",
        "viewer",
        "display",
        "graph",
        "plot",
        "make",
        "use",
        "need",
        "want",
        "find",
        "block",
        "signal",
    }
)
_RERANK_STOP_TOKENS = _MISS_TOPIC_STOP_TOKENS | frozenset(
    {
        "about",
        "between",
        "can",
        "does",
        "from",
        "give",
        "help",
        "how",
        "into",
        "tell",
        "that",
        "this",
        "what",
        "when",
        "with",
    }
)


class VectorIndexError(RuntimeError):
    """Raised when the local vector index cannot be built or queried."""


class VectorIndexBusyError(VectorIndexError):
    """Raised when another process holds the local vector index lock."""


@dataclass(frozen=True)
class VectorRecord:
    """One safe, read-only record stored in the vector index payload."""

    record_id: str
    source_type: str
    canonical_block_id: str | None
    title: str
    normalized_text: str
    provenance: dict[str, Any]
    metadata: dict[str, Any]
    source_hash: str
    corpus_version: str
    index_schema_version: str

    def payload(self) -> dict[str, Any]:
        payload = asdict(self)
        _strip_forbidden_keys(payload)
        return payload


@dataclass(frozen=True)
class _VectorCandidate:
    record: VectorRecord
    vector_score_raw: float
    original_rank: int
    rerank_score: float
    diversity_key: str


def point_id_for_record(record_id: str) -> str:
    """Return a deterministic Qdrant-compatible UUID point ID for a record."""
    return str(uuid.uuid5(_POINT_NAMESPACE, record_id))


def render_vector_result(
    record: VectorRecord,
    *,
    vector_score_raw: float,
) -> dict[str, Any]:
    """Render one safe public vector-search result."""
    result: dict[str, Any] = {
        "record_id": record.record_id,
        "source_type": record.source_type,
        "title": record.title,
        "excerpt": _bounded_excerpt(record.normalized_text),
        "provenance": dict(record.provenance),
        "vector_score_raw": round(float(vector_score_raw), 6),
        "match_reason": (
            "vector_similarity; "
            f"source_type={record.source_type}; "
            "embedded_fields=title,normalized_text,metadata"
        ),
    }
    if record.canonical_block_id:
        result["canonical_block_id"] = record.canonical_block_id
    _strip_forbidden_keys(result)
    return result


def build_manual_vector_records(
    *,
    corpus_root: str | Path = DEFAULT_MANUAL_ROOT,
    tutorial_manifest_path: str | Path = DEFAULT_TUTORIAL_MANIFEST,
    corpus_version: str = CORPUS_VERSION,
) -> list[VectorRecord]:
    """Build manual/tutorial vector records from cleaned Markdown pages."""
    root = Path(corpus_root)
    tutorial_names = _load_tutorial_manifest(tutorial_manifest_path, corpus_root=root)
    records: list[VectorRecord] = []
    for path in sorted(root.glob("*.md")):
        page = clean_manual_page(path)
        source_type = (
            SOURCE_TYPE_TUTORIAL_CHUNK
            if path.name in tutorial_names
            else SOURCE_TYPE_MANUAL_CHUNK
        )
        source_hash = _file_sha256(path)
        for chunk in page.chunks:
            section = " > ".join(chunk.heading_path) if chunk.heading_path else page.title
            chunk_text = _strip_repeated_chunk_headings(
                chunk.text,
                page_title=page.title,
                section=section,
            )
            normalized_text = _normalize_record_text(
                join_non_empty(page.title, section if section != page.title else "", chunk_text)
            )
            records.append(
                VectorRecord(
                    record_id=f"{source_type}:{path.name}:{chunk.chunk_id}",
                    source_type=source_type,
                    canonical_block_id=None,
                    title=page.title,
                    normalized_text=normalized_text,
                    provenance={
                        "path": page.source_path,
                        "line_start": chunk.line_start,
                        "line_end": chunk.line_end,
                        "url": page.source_url,
                        "oldid": page.oldid,
                    },
                    metadata={
                        "section": section,
                        "content_kind": chunk.content_kind,
                        "oldid": page.oldid,
                    },
                    source_hash=source_hash,
                    corpus_version=corpus_version,
                    index_schema_version=INDEX_SCHEMA_VERSION,
                )
            )
    return records


def build_catalog_vector_records(
    *,
    catalog_root: str | Path | None = None,
    corpus_version: str = CORPUS_VERSION,
) -> list[VectorRecord]:
    """Build catalog block vector records from installed GNU Radio metadata."""
    snapshot = build_catalog_snapshot(catalog_root)
    records: list[VectorRecord] = []
    for raw_block in sorted(snapshot.blocks.values(), key=lambda item: item.block_id):
        description = _build_block_description(raw_block).to_payload()
        block_id = str(description["block_id"])
        label = str(description["label"])
        documentation = compact_text(description.get("documentation"))
        categories = _catalog_category_labels(raw_block, description)
        parameters = [
            item for item in description.get("parameters", []) if isinstance(item, dict)
        ]
        inputs = [item for item in description.get("inputs", []) if isinstance(item, dict)]
        outputs = [item for item in description.get("outputs", []) if isinstance(item, dict)]
        parameter_names = [
            str(item.get("id")) for item in parameters if isinstance(item.get("id"), str)
        ]
        parameter_labels = [
            compact_text(item.get("label")) or str(item.get("id"))
            for item in parameters
            if isinstance(item.get("id"), str)
        ]
        input_signatures = [
            _catalog_port_signature("input", item, index)
            for index, item in enumerate(inputs)
        ]
        output_signatures = [
            _catalog_port_signature("output", item, index)
            for index, item in enumerate(outputs)
        ]
        port_domains = sorted(
            {
                compact_text(port.get("domain"))
                for port in [*inputs, *outputs]
                if compact_text(port.get("domain"))
            }
        )
        flags = [str(item) for item in description.get("flags", []) if item]
        field_summary = _catalog_field_summary(
            parameter_names=parameter_names,
            input_signatures=input_signatures,
            output_signatures=output_signatures,
            categories=categories,
            flags=flags,
        )
        port_summary = _catalog_field_summary(
            parameter_names=parameter_names[:6],
            input_signatures=input_signatures[:2],
            output_signatures=output_signatures[:2],
            categories=categories[:4],
            flags=(),
        )
        block_description = documentation or (
            f"{label} ({block_id}) with {len(inputs)} input port(s), "
            f"{len(outputs)} output port(s), and {len(parameters)} parameter(s)."
        )
        metadata = {
            "category": categories,
            "block_family": _block_family(block_id),
            "parameter_names": parameter_names,
            "port_signatures": port_summary,
            "aliases": list(CATALOG_SEMANTIC_ALIASES.get(block_id, ())),
        }
        normalized_text = _normalize_record_text(
            join_non_empty(
                label,
                block_id,
                " ".join(CATALOG_SEMANTIC_ALIASES.get(block_id, ())),
                block_description,
                field_summary,
                port_summary,
                " ".join(categories),
                " ".join(flags),
                " ".join(parameter_names),
                " ".join(parameter_labels),
                " ".join(input_signatures),
                " ".join(output_signatures),
                " ".join(port_domains),
                compact_text(description.get("doc_url")),
            )
        )
        records.append(
            VectorRecord(
                record_id=f"{SOURCE_TYPE_CATALOG_BLOCK}:{block_id}",
                source_type=SOURCE_TYPE_CATALOG_BLOCK,
                canonical_block_id=block_id,
                title=label,
                normalized_text=normalized_text,
                provenance={"path": str(raw_block.path), "pointer": f"blocks[{block_id}]"},
                metadata=metadata,
                source_hash=_file_sha256(raw_block.path),
                corpus_version=corpus_version,
                index_schema_version=INDEX_SCHEMA_VERSION,
            )
        )
    return records


def _catalog_category_labels(raw_block: Any, description: dict[str, Any]) -> list[str]:
    labels = {
        " > ".join(path)
        for path in getattr(raw_block, "category_paths", ())
        if isinstance(path, tuple) and path
    }
    category_path = description.get("category_path")
    if isinstance(category_path, list) and category_path:
        labels.add(" > ".join(str(item) for item in category_path if item))
    return sorted(label for label in labels if label)


def _catalog_port_signature(direction: str, port: dict[str, Any], index: int) -> str:
    label = compact_text(port.get("label")) or compact_text(port.get("id")) or str(index)
    details = [
        compact_text(port.get("domain")),
        compact_text(port.get("dtype")),
        f"vlen={compact_text(port.get('vlen'))}" if port.get("vlen") is not None else "",
        "optional" if port.get("optional") else "",
    ]
    suffix = " ".join(item for item in details if item)
    return f"{direction}:{label} {suffix}".strip()


def _catalog_field_summary(
    *,
    parameter_names: list[str],
    input_signatures: list[str],
    output_signatures: list[str],
    categories: list[str] | tuple[str, ...],
    flags: list[str] | tuple[str, ...],
) -> str:
    return _truncate(
        join_non_empty(
            _label_group("parameters", parameter_names[:8]),
            _label_group("inputs", input_signatures[:4]),
            _label_group("outputs", output_signatures[:4]),
            _label_group("categories", list(categories)[:4]),
            _label_group("flags", list(flags)[:4]),
        ),
        240,
    )


def _truncate(text: str, limit: int) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


def _label_group(label: str, values: list[str]) -> str:
    cleaned = [compact_text(value) for value in values if compact_text(value)]
    if not cleaned:
        return ""
    return f"{label}: {', '.join(cleaned)}"


def build_vector_records(
    *,
    catalog_root: str | Path | None = None,
    corpus_root: str | Path = DEFAULT_MANUAL_ROOT,
    tutorial_manifest_path: str | Path = DEFAULT_TUTORIAL_MANIFEST,
    docs_only: bool = False,
) -> tuple[list[VectorRecord], dict[str, Any]]:
    """Build all v1 vector records and corpus metadata."""
    resolved_catalog_root: Path | None = None
    records: list[VectorRecord] = []
    if not docs_only:
        try:
            resolved_catalog_root = discover_catalog_root(catalog_root)
        except Exception as exc:
            raise VectorIndexError(
                f"GNU Radio catalog root could not be resolved: {exc}. "
                "Vector index build did not create a partial docs-only index."
            ) from exc
        records.extend(build_catalog_vector_records(catalog_root=resolved_catalog_root))

    records.extend(
        build_manual_vector_records(
            corpus_root=corpus_root,
            tutorial_manifest_path=tutorial_manifest_path,
        )
    )
    source_hashes = {
        record.provenance.get("path", record.record_id): record.source_hash
        for record in records
    }
    metadata = {
        "catalog_root": str(resolved_catalog_root) if resolved_catalog_root else None,
        "docs_only": docs_only,
        "gnuradio_version": _gnuradio_version(),
        "source_hashes": source_hashes,
        "corpus_hash": _corpus_hash(records),
        "corpus_version": CORPUS_VERSION,
        "index_schema_version": INDEX_SCHEMA_VERSION,
        "records_by_source_type": dict(Counter(record.source_type for record in records)),
    }
    return records, metadata


def build_vector_index(
    *,
    index_dir: str | Path | None = None,
    catalog_root: str | Path | None = None,
    corpus_root: str | Path = DEFAULT_MANUAL_ROOT,
    tutorial_manifest_path: str | Path = DEFAULT_TUTORIAL_MANIFEST,
    docs_only: bool = False,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
) -> dict[str, Any]:
    """Build a staging vector index and atomically point the public alias to it."""
    qdrant_path = resolve_vector_index_dir(index_dir)
    with _VectorIndexLock(qdrant_path, exclusive=True):
        records, corpus_metadata = build_vector_records(
            catalog_root=catalog_root,
            corpus_root=corpus_root,
            tutorial_manifest_path=tutorial_manifest_path,
            docs_only=docs_only,
        )
        if not records:
            raise VectorIndexError("Vector index build produced no records.")
        qdrant_path.mkdir(parents=True, exist_ok=True)
        client = QdrantClient(path=str(qdrant_path))
        try:
            embedding_size = client.get_embedding_size(embedding_model)
            collection_name = _staging_collection_name(corpus_metadata["corpus_hash"])
            if client.collection_exists(collection_name):
                client.delete_collection(collection_name)
            client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=embedding_size,
                    distance=models.Distance.COSINE,
                ),
            )
            client.upload_collection(
                collection_name=collection_name,
                vectors=[
                    models.Document(text=record.normalized_text, model=embedding_model)
                    for record in records
                ],
                payload=[record.payload() for record in records],
                ids=[point_id_for_record(record.record_id) for record in records],
            )
            collection_info = client.get_collection(collection_name)
            if collection_info.points_count != len(records):
                raise VectorIndexError(
                    f"Staging collection has {collection_info.points_count} points; expected {len(records)}."
                )
            _validate_sample_query(
                client,
                collection_name=collection_name,
                embedding_model=embedding_model,
                sample_text=records[0].title,
            )
            old_collection = _collection_for_alias(client, DEFAULT_VECTOR_COLLECTION_ALIAS)
            _swap_alias(
                client,
                alias_name=DEFAULT_VECTOR_COLLECTION_ALIAS,
                new_collection=collection_name,
                old_collection=old_collection,
            )
            manifest = {
                **corpus_metadata,
                "ok": True,
                "collection_alias": DEFAULT_VECTOR_COLLECTION_ALIAS,
                "active_collection": collection_name,
                "previous_collection": old_collection,
                "embedding_model": embedding_model,
                "embedding_size": embedding_size,
                "record_count": len(records),
                "build_timestamp": datetime.now(UTC).isoformat(),
            }
            _write_manifest(qdrant_path, manifest)
            return manifest
        finally:
            client.close()


def vector_index_stats(index_dir: str | Path | None = None) -> dict[str, Any]:
    """Return persisted vector index stats."""
    qdrant_path = resolve_vector_index_dir(index_dir)
    try:
        with _VectorIndexLock(qdrant_path, exclusive=True):
            manifest = _read_manifest(qdrant_path)
            if manifest is None:
                return _missing_index_payload()
            stale_payload = _stale_index_payload(manifest)
            if stale_payload is not None:
                return stale_payload
            client = QdrantClient(path=str(qdrant_path))
            try:
                active_collection = manifest.get("active_collection")
                if isinstance(active_collection, str) and client.collection_exists(active_collection):
                    manifest["points_count"] = client.get_collection(active_collection).points_count
            finally:
                client.close()
            return {"ok": True, **manifest}
    except VectorIndexBusyError:
        return build_error_payload(
            error_type="index_busy",
            message="Vector index is busy. Another build or search is using the local index.",
        )


def prune_vector_collections(
    *,
    index_dir: str | Path | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Delete old local vector collections, keeping active and one previous collection.

    This is intentionally opt-in. Builds never garbage-collect collections implicitly;
    run this after persisted eval/dashboard evidence if local storage should be
    reclaimed. Eligible collections are old staging collections from this index
    family that are neither the active alias target nor the manifest's previous
    retired collection.
    """
    qdrant_path = resolve_vector_index_dir(index_dir)
    try:
        with _VectorIndexLock(qdrant_path, exclusive=True):
            manifest = _read_manifest(qdrant_path)
            if manifest is None:
                return _missing_index_payload()
            active = manifest.get("active_collection")
            previous = manifest.get("previous_collection")
            keep = {
                name
                for name in (active, previous)
                if isinstance(name, str) and name
            }
            client = QdrantClient(path=str(qdrant_path))
            try:
                existing = sorted(collection.name for collection in client.get_collections().collections)
                candidates = [
                    name
                    for name in existing
                    if name.startswith(f"{DEFAULT_VECTOR_COLLECTION_ALIAS}_staging_")
                    and name not in keep
                ]
                deleted: list[str] = []
                if not dry_run:
                    for name in candidates:
                        client.delete_collection(name)
                        deleted.append(name)
            finally:
                client.close()
            return {
                "ok": True,
                "dry_run": dry_run,
                "retention_policy": "keep active alias target plus one previous retired collection",
                "active_collection": active,
                "previous_collection": previous,
                "kept_collections": sorted(keep),
                "would_delete_collections": candidates,
                "deleted_collections": deleted if not dry_run else [],
            }
    except VectorIndexBusyError:
        return build_error_payload(
            error_type="index_busy",
            message="Vector index is busy. Try again after the current build/search finishes.",
        )
    except Exception as exc:
        return build_error_payload(error_type=ErrorCode.INTERNAL_ERROR, message=str(exc))


def semantic_search_grc(
    query: str,
    scope: str = "all",
    k: int = DEFAULT_VECTOR_LIMIT,
    *,
    index_dir: str | Path | None = None,
    embedding_model: str | None = None,
) -> dict[str, Any]:
    """Search the local vector index without mutating graph/session state."""
    normalized_query = _normalize_query(query)
    if not normalized_query:
        return build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST,
            message="Query must be a non-empty string.",
        )
    if len(normalized_query) > MAX_QUERY_CHARS:
        return build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST,
            message=f"Query must be at most {MAX_QUERY_CHARS} characters.",
        )
    if scope not in VALID_VECTOR_SCOPES:
        return build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST,
            message=f"Unsupported semantic search scope: {scope}",
            details={"supported_scopes": sorted(VALID_VECTOR_SCOPES)},
        )
    try:
        limit = _normalize_limit(k)
    except ValueError as exc:
        return build_error_payload(error_type=ErrorCode.INVALID_REQUEST, message=str(exc))

    fetch_limit = _rerank_fetch_limit(limit)
    qdrant_path = resolve_vector_index_dir(index_dir)
    try:
        with _VectorIndexLock(qdrant_path, exclusive=True):
            manifest = _read_manifest(qdrant_path)
            if manifest is None:
                return _missing_index_payload()
            stale_payload = _stale_index_payload(manifest)
            if stale_payload is not None:
                return stale_payload
            query_embedding_model = _query_embedding_model(
                manifest,
                explicit_embedding_model=embedding_model,
            )
            if scope == "tutorial" and manifest.get("records_by_source_type", {}).get(SOURCE_TYPE_TUTORIAL_CHUNK, 0) == 0:
                return {
                    "ok": True,
                    "tool": "semantic_search_grc",
                    "query": normalized_query,
                    "scope": scope,
                    "results": [],
                    "warnings": ["tutorial_manifest_empty"],
                    "reason": "tutorial_manifest_empty",
                }
            client = QdrantClient(path=str(qdrant_path))
            try:
                query_filter = _scope_filter(scope)
                hits = client.query_points(
                    collection_name=DEFAULT_VECTOR_COLLECTION_ALIAS,
                    query=models.Document(text=normalized_query, model=query_embedding_model),
                    query_filter=query_filter,
                    limit=fetch_limit,
                    with_payload=True,
                    with_vectors=False,
                )
            except Exception as exc:
                if "not found" in str(exc).lower():
                    return _missing_index_payload()
                raise
            finally:
                client.close()
    except VectorIndexBusyError:
        return build_error_payload(
            error_type="index_busy",
            message="Vector index is busy. Try again after the current build/search finishes.",
        )
    except Exception as exc:
        return build_error_payload(error_type=ErrorCode.INTERNAL_ERROR, message=str(exc))

    scored_records = [
        (_record_from_payload(point.payload), _point_score(point))
        for point in hits.points
        if isinstance(point.payload, dict)
    ]
    candidates = _rerank_vector_records(
        normalized_query,
        scored_records,
        limit=limit,
        scope=scope,
    )
    results = [
        render_vector_result(
            candidate.record,
            vector_score_raw=candidate.vector_score_raw,
        )
        for candidate in candidates
    ]
    return {
        "ok": True,
        "tool": "semantic_search_grc",
        "query": normalized_query,
        "scope": scope,
        "results": results,
        "warnings": [] if results else [f"No vector matches found for '{normalized_query}'."],
    }


def vector_index_version_token(index_dir: str | Path | None = None) -> str:
    """Return a lightweight manifest-derived version token for cache keys."""
    qdrant_path = resolve_vector_index_dir(index_dir)
    manifest_path = _manifest_path(qdrant_path)
    if not manifest_path.is_file():
        return "missing_index"
    try:
        manifest = _read_manifest(qdrant_path)
        if manifest is None:
            return "missing_index"
        mtime_ns = manifest_path.stat().st_mtime_ns
        return "|".join(
            [
                str(manifest.get("active_collection", "")),
                str(manifest.get("corpus_version", "")),
                str(manifest.get("index_schema_version", "")),
                str(manifest.get("build_timestamp", "")),
                str(mtime_ns),
            ]
        )
    except Exception:
        return "manifest_unavailable"


def record_vector_miss(
    query: str,
    *,
    expected_block_ids: list[str] | tuple[str, ...] | None = None,
    actual_top_ids: list[str] | tuple[str, ...] | None = None,
    observed_top_ids: list[str] | tuple[str, ...] | None = None,
    scope: str = "all",
    category: str = "untriaged",
    source: str = "real_user",
    notes: str = "",
    intake_path: str | Path | None = None,
) -> dict[str, Any]:
    """Append a sanitized real-user retrieval miss record to JSONL.

    This is evidence intake only. It does not update metadata, rebuild indexes,
    influence ranking, or authorize mutations.
    """
    normalized_query = _normalize_query(_sanitize_intake_text(query))
    if not normalized_query:
        return build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST,
            message="Query must be a non-empty string.",
        )
    if len(normalized_query) > MAX_QUERY_CHARS:
        return build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST,
            message=f"Query must be at most {MAX_QUERY_CHARS} characters.",
        )
    if scope not in VALID_VECTOR_SCOPES:
        return build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST,
            message=f"Unsupported semantic search scope: {scope}",
            details={"supported_scopes": sorted(VALID_VECTOR_SCOPES)},
        )
    if category not in VALID_MISS_CATEGORIES:
        return build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST,
            message=f"Unsupported miss category: {category}",
            details={"supported_categories": sorted(VALID_MISS_CATEGORIES)},
        )
    if source not in VALID_MISS_SOURCES:
        return build_error_payload(
            error_type=ErrorCode.INVALID_REQUEST,
            message=f"Unsupported miss source: {source}",
            details={"supported_sources": sorted(VALID_MISS_SOURCES)},
        )
    top_ids = actual_top_ids if actual_top_ids is not None else observed_top_ids
    record = {
        "schema_version": MISS_INTAKE_SCHEMA_VERSION,
        "timestamp": datetime.now(UTC).isoformat(),
        "query": normalized_query,
        "query_key": _miss_query_key(normalized_query),
        "scope": scope,
        "expected_block_ids": _sanitize_string_list(expected_block_ids),
        "actual_top_ids": _sanitize_string_list(top_ids),
        "category": category,
        "source": source,
        "notes": _bounded_note(_sanitize_intake_text(notes)),
    }
    _strip_forbidden_keys(record)
    path = resolve_miss_intake_path(intake_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return {
        "ok": True,
        "tool": "record_vector_miss",
        "intake_path": str(path),
        "record": record,
    }


def summarize_vector_misses(
    *,
    intake_path: str | Path | None = None,
) -> dict[str, Any]:
    """Summarize and deduplicate structured vector miss intake records."""
    path = resolve_miss_intake_path(intake_path)
    if not path.is_file():
        return {
            "ok": True,
            "tool": "summarize_vector_misses",
            "intake_path": str(path),
            "total_records": 0,
            "cluster_count": 0,
            "clusters": [],
            "warnings": ["miss_intake_empty"],
        }
    records = _read_miss_records(path)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(_miss_cluster_key(record), []).append(record)
    clusters = [
        _summarize_miss_cluster(cluster_id, items)
        for cluster_id, items in grouped.items()
    ]
    clusters.sort(
        key=lambda item: (
            -int(item["count"]),
            str(item.get("expected_block_ids", [])),
            str(item.get("query_key", "")),
        )
    )
    return {
        "ok": True,
        "tool": "summarize_vector_misses",
        "intake_path": str(path),
        "review_thresholds": _metadata_review_thresholds(),
        "total_records": len(records),
        "cluster_count": len(clusters),
        "clusters": clusters,
        "warnings": [],
    }


def propose_vector_metadata(
    *,
    intake_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return human-review metadata candidates without modifying code or indexes."""
    review = summarize_vector_misses(intake_path=intake_path)
    candidates: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for cluster in review.get("clusters", []):
        if cluster.get("recommendation") != "metadata_candidate":
            blocked.append(
                {
                    "cluster_id": cluster.get("cluster_id"),
                    "cluster_key": cluster.get("cluster_key"),
                    "recommendation": cluster.get("recommendation"),
                    "reason": "cluster_does_not_meet_metadata_threshold",
                }
            )
            continue
        expected = cluster.get("expected_block_ids", [])
        proposed_block = expected[0] if len(expected) == 1 else None
        if proposed_block is None:
            blocked.append(
                {
                    "cluster_id": cluster.get("cluster_id"),
                    "cluster_key": cluster.get("cluster_key"),
                    "recommendation": cluster.get("recommendation"),
                    "reason": "metadata_candidate_requires_one_expected_block",
                }
            )
            continue
        phrase = _candidate_capability_phrase(cluster)
        candidates.append(
            {
                "proposed_block": proposed_block,
                "proposed_stable_capability_phrase": phrase,
                "supporting_clusters": [cluster],
                "why_generally_true": (
                    "Human review required: accept only if this phrase describes "
                    "a stable block capability independent of the collected query wording."
                ),
                "required_negative_trap": f"delete {proposed_block}",
                "expected_eval_cases_to_add": [
                    {
                        "query": phrase,
                        "expected_block_ids": [proposed_block],
                        "case_type": "semantic_paraphrase",
                    },
                    {
                        "query": f"delete {proposed_block}",
                        "expected_block_ids": [proposed_block],
                        "case_type": "false_positive",
                    },
                ],
                "false_positive_risk": (
                    "Unknown until reviewed and rerun against retrieval eval; do not edit "
                    "CATALOG_SEMANTIC_METADATA automatically."
                ),
            }
        )
    return {
        "ok": bool(review.get("ok")),
        "tool": "propose_vector_metadata",
        "intake_path": review.get("intake_path"),
        "review_thresholds": _metadata_review_thresholds(),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "blocked_clusters": blocked,
        "warnings": review.get("warnings", []),
    }


def local_qdrant_alias_swap_smoke(index_dir: Path) -> dict[str, Any]:
    """Exercise local Qdrant alias bootstrap and swap without FastEmbed downloads."""
    client = QdrantClient(path=str(index_dir))
    try:
        first = "alias_smoke_first"
        second = "alias_smoke_second"
        for collection in (first, second):
            if client.collection_exists(collection):
                client.delete_collection(collection)
            client.create_collection(
                collection_name=collection,
                vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
            )
        client.upsert(
            collection_name=first,
            points=[
                models.PointStruct(id=1, vector=[1.0, 0.0], payload={"title": "first"})
            ],
        )
        client.upsert(
            collection_name=second,
            points=[
                models.PointStruct(id=2, vector=[1.0, 0.0], payload={"title": "second"})
            ],
        )
        _swap_alias(
            client,
            alias_name=DEFAULT_VECTOR_COLLECTION_ALIAS,
            new_collection=first,
            old_collection=None,
        )
        first_hit = client.query_points(
            collection_name=DEFAULT_VECTOR_COLLECTION_ALIAS,
            query=[1.0, 0.0],
            limit=1,
        ).points[0]
        _swap_alias(
            client,
            alias_name=DEFAULT_VECTOR_COLLECTION_ALIAS,
            new_collection=second,
            old_collection=first,
        )
        second_hit = client.query_points(
            collection_name=DEFAULT_VECTOR_COLLECTION_ALIAS,
            query=[1.0, 0.0],
            limit=1,
        ).points[0]
        return {
            "ok": True,
            "alias": DEFAULT_VECTOR_COLLECTION_ALIAS,
            "first_title": first_hit.payload["title"],
            "second_title": second_hit.payload["title"],
        }
    finally:
        client.close()


def resolve_workspace_root(start: str | Path | None = None) -> Path:
    """Resolve the GRC Agent workspace root from a starting path."""
    current = Path.cwd() if start is None else Path(start).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        pyproject = candidate / "pyproject.toml"
        if pyproject.is_file() and 'name = "grc-agent"' in pyproject.read_text(encoding="utf-8"):
            return candidate
    raise VectorIndexError(
        "Could not resolve the GRC Agent workspace root. Pass --index-dir explicitly."
    )


def resolve_vector_index_dir(index_dir: str | Path | None = None) -> Path:
    """Resolve the local Qdrant path for vector retrieval."""
    if index_dir is not None:
        return Path(index_dir).expanduser()
    return resolve_workspace_root() / ".grc_agent" / "vector_index" / "qdrant"


def resolve_miss_intake_path(intake_path: str | Path | None = None) -> Path:
    """Resolve the JSONL path used for real-user retrieval miss intake."""
    if intake_path is not None:
        return Path(intake_path).expanduser()
    return resolve_workspace_root() / "reports" / "retrieval" / "real_user_misses.jsonl"


def _read_miss_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise VectorIndexError(f"Invalid miss-intake JSONL at {path}:{line_number}") from exc
        if not isinstance(payload, dict):
            continue
        _strip_forbidden_keys(payload)
        records.append(_normalize_miss_record(payload))
    return records


def _load_tutorial_manifest(
    tutorial_manifest_path: str | Path,
    *,
    corpus_root: Path,
) -> frozenset[str]:
    path = Path(tutorial_manifest_path)
    if not path.exists():
        return frozenset()
    names = {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    missing = sorted(name for name in names if not (corpus_root / name).is_file())
    if missing:
        raise VectorIndexError(
            f"Tutorial manifest references missing files: {', '.join(missing)}"
        )
    return frozenset(names)


def _normalize_record_text(text: str) -> str:
    return " ".join(str(text).split())


def _strip_repeated_chunk_headings(
    text: str,
    *,
    page_title: str,
    section: str,
) -> str:
    headings = {
        _normalize_record_text(page_title).casefold(),
        *(
            _normalize_record_text(part).casefold()
            for part in section.split(" > ")
            if _normalize_record_text(part)
        ),
    }
    lines = text.splitlines()
    while lines:
        stripped = lines[0].strip()
        if not stripped.startswith("#"):
            break
        heading = _normalize_record_text(stripped.lstrip("#").strip()).casefold()
        if heading not in headings:
            break
        lines.pop(0)
    return "\n".join(lines).strip() or text


def _normalize_query(query: Any) -> str:
    if not isinstance(query, str):
        return ""
    return " ".join(query.split())


def _normalize_limit(k: Any) -> int:
    if isinstance(k, bool) or not isinstance(k, int):
        raise ValueError("k must be an integer.")
    if k < 1:
        raise ValueError("k must be greater than zero.")
    return min(k, MAX_VECTOR_LIMIT)


def _rerank_fetch_limit(limit: int) -> int:
    return min(
        MAX_RERANK_FETCH_LIMIT,
        max(limit, MIN_RERANK_FETCH_LIMIT, limit * RERANK_FETCH_MULTIPLIER),
    )


def _point_score(point: Any) -> float:
    try:
        return float(point.score)
    except (TypeError, ValueError):
        return 0.0


def _rerank_vector_records(
    query: str,
    scored_records: list[tuple[VectorRecord, float]],
    *,
    limit: int,
    scope: str,
) -> list[_VectorCandidate]:
    terms = _rerank_query_terms(query)
    candidates = [
        _VectorCandidate(
            record=record,
            vector_score_raw=score,
            original_rank=index,
            rerank_score=_rerank_score(
                query=query,
                terms=terms,
                record=record,
                vector_score_raw=score,
                original_rank=index,
            ),
            diversity_key=_rerank_diversity_key(record),
        )
        for index, (record, score) in enumerate(scored_records)
    ]
    return _select_reranked_candidates(candidates, limit=limit, scope=scope)


def _rerank_query_terms(query: str) -> tuple[str, ...]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[a-z0-9_]+", query.casefold()):
        if len(token) <= 1 or token in _RERANK_STOP_TOKENS:
            continue
        stemmed = _stem_query_token(token)
        if stemmed and stemmed not in seen:
            terms.append(stemmed)
            seen.add(stemmed)
    return tuple(terms)


def _rerank_score(
    *,
    query: str,
    terms: tuple[str, ...],
    record: VectorRecord,
    vector_score_raw: float,
    original_rank: int,
) -> float:
    title_terms = _rerank_text_terms(record.title)
    alias_terms = _rerank_text_terms(_metadata_alias_text(record.metadata))
    record_terms = _rerank_text_terms(
        " ".join(
            (
                record.record_id,
                record.canonical_block_id or "",
                record.title,
                record.normalized_text,
                _metadata_alias_text(record.metadata),
            )
        )
    )
    title_hits = sum(1 for term in terms if term in title_terms)
    alias_hits = sum(1 for term in terms if term in alias_terms)
    record_hits = sum(1 for term in terms if term in record_terms)
    term_coverage = (record_hits / len(terms)) if terms else 0.0
    all_terms_bonus = 0.05 if terms and record_hits == len(terms) else 0.0
    return (
        float(vector_score_raw)
        + _identity_bonus(query, record)
        + _phrase_bonus(query, record)
        + min(0.08, 0.025 * title_hits)
        + min(0.06, 0.02 * alias_hits)
        + min(0.06, 0.01 * record_hits)
        + min(0.08, 0.05 * term_coverage)
        + all_terms_bonus
        - min(original_rank, 50) * 0.0005
    )


def _rerank_text_terms(text: str) -> set[str]:
    return {
        _stem_query_token(token)
        for token in re.findall(r"[a-z0-9_]+", text.casefold())
        if len(token) > 1 and token not in _RERANK_STOP_TOKENS
    }


def _metadata_alias_text(metadata: dict[str, Any]) -> str:
    aliases = metadata.get("aliases")
    if not isinstance(aliases, list):
        return ""
    return " ".join(str(alias) for alias in aliases if isinstance(alias, str))


def _identity_bonus(query: str, record: VectorRecord) -> float:
    query_key = _identity_key(query)
    if not query_key:
        return 0.0
    identity_keys = {
        _identity_key(record.record_id),
        _identity_key(record.canonical_block_id or ""),
        _identity_key(record.title),
    }
    if query_key in identity_keys:
        return 0.18
    block_key = _identity_key(record.canonical_block_id or "")
    if block_key and len(query_key) >= 5 and (query_key in block_key or block_key in query_key):
        return 0.08
    return 0.0


def _phrase_bonus(query: str, record: VectorRecord) -> float:
    normalized_query = query.casefold()
    if len(normalized_query) < 8:
        return 0.0
    haystack = " ".join(
        (
            record.record_id,
            record.canonical_block_id or "",
            record.title,
            record.normalized_text,
            _metadata_alias_text(record.metadata),
        )
    ).casefold()
    return 0.04 if normalized_query in haystack else 0.0


def _identity_key(value: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _rerank_diversity_key(record: VectorRecord) -> str:
    if record.source_type == SOURCE_TYPE_CATALOG_BLOCK:
        return record.canonical_block_id or record.record_id
    provenance = record.provenance
    source = provenance.get("url") or provenance.get("path") or record.title
    return f"{record.source_type}:{source}"


def _select_reranked_candidates(
    candidates: list[_VectorCandidate],
    *,
    limit: int,
    scope: str,
) -> list[_VectorCandidate]:
    ranked = sorted(
        candidates,
        key=lambda item: (-item.rerank_score, item.original_rank, item.record.record_id),
    )
    if scope == "catalog":
        return ranked[:limit]

    selected: list[_VectorCandidate] = []
    selected_ids: set[str] = set()
    source_counts: Counter[str] = Counter()
    for candidate in ranked:
        if source_counts[candidate.diversity_key] >= MAX_RERANK_DOCS_PER_SOURCE:
            continue
        selected.append(candidate)
        selected_ids.add(candidate.record.record_id)
        source_counts[candidate.diversity_key] += 1
        if len(selected) >= limit:
            return selected

    for candidate in ranked:
        if candidate.record.record_id in selected_ids:
            continue
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


def _sanitize_string_list(values: list[str] | tuple[str, ...] | None) -> list[str]:
    if values is None:
        return []
    sanitized: list[str] = []
    for value in values[:20]:
        if not isinstance(value, str):
            continue
        compact = _normalize_record_text(_sanitize_intake_text(value))
        if compact:
            sanitized.append(compact[:200])
    return sanitized


def _bounded_note(notes: Any) -> str:
    if not isinstance(notes, str):
        return ""
    return _normalize_record_text(notes)[:1000]


def _sanitize_intake_text(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    return _INTAKE_PATH_PATTERN.sub("<path>", text)


def _miss_query_key(query: str) -> str:
    tokens = [
        _stem_query_token(token)
        for token in re.findall(r"[a-z0-9_]+", query.lower())
        if token not in {"a", "an", "the", "it", "to", "for", "me", "my", "please"}
    ]
    return " ".join(token for token in tokens if token)


def _stem_query_token(token: str) -> str:
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 4 and token.endswith("s"):
        return token[:-1]
    return token


def _normalize_miss_record(payload: dict[str, Any]) -> dict[str, Any]:
    query = _normalize_query(_sanitize_intake_text(payload.get("query", "")))
    source = payload.get("source") if payload.get("source") in VALID_MISS_SOURCES else "manual_review"
    category = (
        payload.get("category")
        if payload.get("category") in VALID_MISS_CATEGORIES
        else "untriaged"
    )
    actual_top_ids = payload.get("actual_top_ids", payload.get("observed_top_ids"))
    return {
        "schema_version": str(payload.get("schema_version", MISS_INTAKE_SCHEMA_VERSION)),
        "timestamp": str(payload.get("timestamp", "")),
        "query": query,
        "query_key": str(payload.get("query_key") or _miss_query_key(query)),
        "scope": payload.get("scope") if payload.get("scope") in VALID_VECTOR_SCOPES else "all",
        "expected_block_ids": _sanitize_string_list(payload.get("expected_block_ids")),
        "actual_top_ids": _sanitize_string_list(actual_top_ids),
        "category": category,
        "source": source,
        "notes": _bounded_note(_sanitize_intake_text(payload.get("notes", ""))),
    }


def _miss_cluster_key(record: dict[str, Any]) -> str:
    expected = tuple(record.get("expected_block_ids") or ())
    if expected:
        topic = _miss_topic_key(str(record.get("query_key", "")))
        return "|".join(
            (
                str(record.get("scope", "all")),
                str(record.get("category", "untriaged")),
                ",".join(expected),
                topic,
            )
        )
    return "|".join(
        (
            str(record.get("scope", "all")),
            str(record.get("category", "untriaged")),
            str(record.get("query_key", "")),
        )
    )


def _miss_topic_key(query_key: str) -> str:
    tokens = [
        token
        for token in query_key.split()
        if token and token not in _MISS_TOPIC_STOP_TOKENS
    ]
    if not tokens:
        return query_key
    return tokens[0]


def _summarize_miss_cluster(
    cluster_id: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    queries = sorted({str(record.get("query", "")) for record in records if record.get("query")})
    notes = sorted({str(record.get("notes", "")) for record in records if record.get("notes")})
    expected_block_ids = sorted(
        {
            block_id
            for record in records
            for block_id in record.get("expected_block_ids", [])
        }
    )
    actual_top_ids = [
        item
        for item, _ in Counter(
            top_id
            for record in records
            for top_id in record.get("actual_top_ids", [])
        ).most_common(10)
    ]
    categories = sorted({str(record.get("category", "untriaged")) for record in records})
    sources = dict(Counter(str(record.get("source", "manual_review")) for record in records))
    recommendation = _miss_recommendation(
        count=len(records),
        sources=sources,
        categories=categories,
        expected_block_ids=expected_block_ids,
    )
    return {
        "cluster_id": hashlib.sha1(cluster_id.encode("utf-8")).hexdigest()[:12],
        "cluster_key": cluster_id,
        "count": len(records),
        "query_key": str(records[0].get("query_key", "")),
        "queries": queries[:10],
        "scope": str(records[0].get("scope", "all")),
        "categories": categories,
        "sources": sources,
        "expected_block_ids": expected_block_ids,
        "actual_top_ids": actual_top_ids,
        "notes_count": len(notes),
        "notes_preview": notes[:3],
        "first_seen": min(str(record.get("timestamp", "")) for record in records),
        "last_seen": max(str(record.get("timestamp", "")) for record in records),
        "recommendation": recommendation,
        "recommended_action": recommendation,
    }


def _miss_recommendation(
    *,
    count: int,
    sources: dict[str, int],
    categories: list[str],
    expected_block_ids: list[str],
) -> str:
    category_set = set(categories)
    if category_set & {"exact_id_regression", "false_positive_regression", "source_type_regression"}:
        return "ignore"
    if category_set & {"ambiguous_wording", "should_clarify"}:
        return "ambiguity"
    if category_set & {"bad_eval_expectation", "should_remain_miss"}:
        return "eval_issue"
    meets_repetition_threshold = count >= 3 or sum(1 for value in sources.values() if value > 0) >= 2
    if meets_repetition_threshold and expected_block_ids and category_set <= {
        "missing_metadata",
        "bad_record_text",
        "embedding_limitation",
        "real_user",
        "untriaged",
    }:
        return "metadata_candidate"
    return "needs_more_evidence"


def _metadata_review_thresholds() -> dict[str, Any]:
    return {
        "minimum_clustered_misses": 3,
        "or_distinct_sources": 2,
        "one_off_metadata_proposals": "blocked",
        "ambiguous_wording": "ambiguity_or_needs_more_evidence",
        "protected_metric_regressions": "block_promotion",
    }


def _candidate_capability_phrase(cluster: dict[str, Any]) -> str:
    queries = cluster.get("queries", [])
    if isinstance(queries, list) and queries:
        return str(queries[0])[:120]
    query_key = cluster.get("query_key")
    return str(query_key or "review stable capability")[:120]


def _bounded_excerpt(text: str) -> str:
    compact = _normalize_record_text(text)
    if len(compact) <= MAX_EXCERPT_CHARS:
        return compact
    return compact[: MAX_EXCERPT_CHARS - 1].rstrip() + "…"


def _strip_forbidden_keys(payload: dict[str, Any]) -> None:
    for key in tuple(payload):
        if key in FORBIDDEN_RESULT_KEYS:
            payload.pop(key, None)
            continue
        value = payload[key]
        if isinstance(value, dict):
            _strip_forbidden_keys(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _strip_forbidden_keys(item)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _corpus_hash(records: list[VectorRecord]) -> str:
    digest = hashlib.sha256()
    for record in sorted(records, key=lambda item: item.record_id):
        digest.update(record.record_id.encode("utf-8"))
        digest.update(record.index_schema_version.encode("utf-8"))
        digest.update(record.source_hash.encode("utf-8"))
        digest.update(record.normalized_text.encode("utf-8"))
        digest.update(json.dumps(record.metadata, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()


def _block_family(block_id: str) -> str:
    if "_" not in block_id:
        return block_id
    return block_id.split("_", 1)[0]


def _extract_parameter_names(text: str) -> list[str]:
    match = re.search(r"parameters: ([^;]+)", text)
    if not match:
        return []
    return [item.strip() for item in match.group(1).split(",") if item.strip()]


def _gnuradio_version() -> str | None:
    executable = shutil.which("gnuradio-config-info")
    if executable is None:
        return None
    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    version = completed.stdout.strip() or completed.stderr.strip()
    return version or None


def _staging_collection_name(corpus_hash: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"{DEFAULT_VECTOR_COLLECTION_ALIAS}_staging_{timestamp}_{corpus_hash[:12]}"


def _validate_sample_query(
    client: QdrantClient,
    *,
    collection_name: str,
    embedding_model: str,
    sample_text: str,
) -> None:
    response = client.query_points(
        collection_name=collection_name,
        query=models.Document(text=sample_text, model=embedding_model),
        limit=1,
    )
    if not response.points:
        raise VectorIndexError("Staging vector collection failed sample query validation.")


def _collection_for_alias(client: QdrantClient, alias_name: str) -> str | None:
    aliases = client.get_aliases().aliases
    for alias in aliases:
        if alias.alias_name == alias_name:
            return alias.collection_name
    return None


def _swap_alias(
    client: QdrantClient,
    *,
    alias_name: str,
    new_collection: str,
    old_collection: str | None,
) -> None:
    operations: list[models.CreateAliasOperation | models.DeleteAliasOperation] = []
    existing_collection = _collection_for_alias(client, alias_name)
    if existing_collection is not None:
        operations.append(
            models.DeleteAliasOperation(
                delete_alias=models.DeleteAlias(alias_name=alias_name)
            )
        )
    operations.append(
        models.CreateAliasOperation(
            create_alias=models.CreateAlias(
                collection_name=new_collection,
                alias_name=alias_name,
            )
        )
    )
    client.update_collection_aliases(change_aliases_operations=operations)


def _scope_filter(scope: str) -> models.Filter | None:
    source_types: list[str]
    if scope == "all":
        return None
    if scope == "catalog":
        source_types = [SOURCE_TYPE_CATALOG_BLOCK]
    elif scope == "manual":
        source_types = [SOURCE_TYPE_MANUAL_CHUNK]
    elif scope == "tutorial":
        source_types = [SOURCE_TYPE_TUTORIAL_CHUNK]
    else:
        return None
    return models.Filter(
        must=[
            models.FieldCondition(
                key="source_type",
                match=models.MatchAny(any=source_types),
            )
        ]
    )


def _record_from_payload(payload: dict[str, Any]) -> VectorRecord:
    return VectorRecord(
        record_id=str(payload.get("record_id", "")),
        source_type=str(payload.get("source_type", "")),
        canonical_block_id=payload.get("canonical_block_id")
        if isinstance(payload.get("canonical_block_id"), str)
        else None,
        title=str(payload.get("title", "")),
        normalized_text=str(payload.get("normalized_text", "")),
        provenance=payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {},
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        source_hash=str(payload.get("source_hash", "")),
        corpus_version=str(payload.get("corpus_version", "")),
        index_schema_version=str(payload.get("index_schema_version", "")),
    )


def _manifest_path(qdrant_path: Path) -> Path:
    return qdrant_path.parent / _MANIFEST_FILE


def _write_manifest(qdrant_path: Path, manifest: dict[str, Any]) -> None:
    path = _manifest_path(qdrant_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _read_manifest(qdrant_path: Path) -> dict[str, Any] | None:
    path = _manifest_path(qdrant_path)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VectorIndexError(f"Vector index manifest is invalid: {path}") from exc
    return payload if isinstance(payload, dict) else None


def _stale_index_payload(manifest: dict[str, Any]) -> dict[str, Any] | None:
    actual = manifest.get("index_schema_version")
    if actual == INDEX_SCHEMA_VERSION:
        return None
    return build_error_payload(
        error_type="stale_index",
        message=(
            "Vector index schema is stale for this runtime. "
            "Run `grc-agent vector build` before semantic search."
        ),
        details={
            "index_schema_version": actual,
            "expected_index_schema_version": INDEX_SCHEMA_VERSION,
        },
    )


def _query_embedding_model(
    manifest: dict[str, Any],
    *,
    explicit_embedding_model: str | None,
) -> str:
    if explicit_embedding_model:
        return explicit_embedding_model
    manifest_model = manifest.get("embedding_model")
    if isinstance(manifest_model, str) and manifest_model.strip():
        return manifest_model.strip()
    return DEFAULT_EMBEDDING_MODEL


def _missing_index_payload() -> dict[str, Any]:
    return build_error_payload(
        error_type="missing_index",
        message="Vector index is missing. Run `grc-agent vector build` before semantic search.",
    )


class _VectorIndexLock(AbstractContextManager["_VectorIndexLock"]):
    def __init__(self, qdrant_path: Path, *, exclusive: bool) -> None:
        self._lock_path = qdrant_path.parent / _LOCK_FILE
        self._exclusive = exclusive
        self._handle: Any = None

    def __enter__(self) -> "_VectorIndexLock":
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._lock_path.open("a+", encoding="utf-8")
        mode = fcntl.LOCK_EX if self._exclusive else fcntl.LOCK_SH
        try:
            fcntl.flock(self._handle.fileno(), mode | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._handle.close()
            raise VectorIndexBusyError("Vector index is locked by another process.") from exc
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
        return None


__all__ = [
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_VECTOR_COLLECTION_ALIAS",
    "CATALOG_SEMANTIC_METADATA",
    "INDEX_SCHEMA_VERSION",
    "MISS_INTAKE_SCHEMA_VERSION",
    "SOURCE_TYPE_CATALOG_BLOCK",
    "SOURCE_TYPE_MANUAL_CHUNK",
    "SOURCE_TYPE_TUTORIAL_CHUNK",
    "VALID_MISS_CATEGORIES",
    "VALID_MISS_SOURCES",
    "VectorIndexError",
    "VectorRecord",
    "build_catalog_vector_records",
    "build_manual_vector_records",
    "build_vector_index",
    "build_vector_records",
    "local_qdrant_alias_swap_smoke",
    "point_id_for_record",
    "prune_vector_collections",
    "propose_vector_metadata",
    "record_vector_miss",
    "render_vector_result",
    "resolve_miss_intake_path",
    "resolve_vector_index_dir",
    "semantic_search_grc",
    "summarize_vector_misses",
    "vector_index_version_token",
    "vector_index_stats",
]
