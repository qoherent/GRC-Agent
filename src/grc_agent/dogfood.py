"""Structured dogfooding evidence intake for real GRC Agent use."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grc_agent.domain_models import build_error_payload

DOGFOOD_SCHEMA_VERSION = "2026-04-29-dogfood-v1"

VALID_DOGFOOD_SOURCES = frozenset(
    {
        "installed_example",
        "user_graph",
        "real_user",
        "eval",
        "manual_review",
    }
)
VALID_TASK_TYPES = frozenset(
    {
        "inspect",
        "validate",
        "param_edit",
        "add_variable",
        "state_edit",
        "disable_enable",
        "disconnect",
        "rewire",
        "save_copy",
        "preview",
        "retrieval",
        "clarification",
        "negative",
        "block_uid_mutation",
        "duplicate_safety",
        "other",
    }
)
VALID_FAILURE_CATEGORIES = frozenset(
    {
        "no_failure",
        "routing_failure",
        "argument_copying_failure",
        "safe_preflight_rejection",
        "preflight_false_reject",
        "unsafe_mutation_risk",
        "grcc_failure",
        "save_reload_mismatch",
        "confusing_clarification",
        "retrieval_miss",
        "tool_error",
        "other",
    }
)
VALID_SEVERITIES = frozenset({"info", "low", "medium", "high", "stop_the_line"})

_PATH_RE = re.compile(r"(?:~|/|\b[A-Za-z]:[\\/])(?:[^\s\"'<>|:]+[\\/])*[^\s\"'<>|:]+")
_GRC_FILE_RE = re.compile(r"\b[\w.-]+\.grc\b")
_WHITESPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[a-z0-9_]+")


class DogfoodIntakeError(RuntimeError):
    """Raised for invalid dogfooding intake/report operations."""


def record_dogfood_case(
    *,
    prompt: str,
    graph: str = "",
    source: str = "manual_review",
    task_type: str = "other",
    failure_category: str = "no_failure",
    severity: str = "info",
    expected: str = "",
    actual: str = "",
    actual_tools: list[str] | tuple[str, ...] | None = None,
    graph_delta: str = "",
    validation_state: str = "",
    save_state: str = "",
    reproducible: bool = False,
    notes: str = "",
    intake_path: str | Path | None = None,
) -> dict[str, Any]:
    """Append a sanitized dogfooding case to JSONL evidence.

    This is evidence intake only. It does not execute model turns, mutate graphs,
    update evals, or promote fixes.
    """
    if source not in VALID_DOGFOOD_SOURCES:
        return build_error_payload(
            error_type="invalid_dogfood_source",
            message=f"Unsupported dogfood source: {source}",
            details={"valid_sources": sorted(VALID_DOGFOOD_SOURCES)},
        )
    if task_type not in VALID_TASK_TYPES:
        return build_error_payload(
            error_type="invalid_dogfood_task_type",
            message=f"Unsupported dogfood task type: {task_type}",
            details={"valid_task_types": sorted(VALID_TASK_TYPES)},
        )
    if failure_category not in VALID_FAILURE_CATEGORIES:
        return build_error_payload(
            error_type="invalid_dogfood_failure_category",
            message=f"Unsupported dogfood failure category: {failure_category}",
            details={"valid_failure_categories": sorted(VALID_FAILURE_CATEGORIES)},
        )
    if severity not in VALID_SEVERITIES:
        return build_error_payload(
            error_type="invalid_dogfood_severity",
            message=f"Unsupported dogfood severity: {severity}",
            details={"valid_severities": sorted(VALID_SEVERITIES)},
        )

    record = {
        "schema_version": DOGFOOD_SCHEMA_VERSION,
        "timestamp": datetime.now(UTC).isoformat(),
        "source": source,
        "graph_ref": _safe_graph_ref(graph, source=source),
        "task_type": task_type,
        "failure_category": failure_category,
        "severity": severity,
        "prompt": _bounded_text(_sanitize_text(prompt), limit=1000),
        "prompt_key": _prompt_key(prompt),
        "expected": _bounded_text(_sanitize_text(expected), limit=1000),
        "actual": _bounded_text(_sanitize_text(actual), limit=1000),
        "actual_tools": _clean_tools(actual_tools or ()),
        "graph_delta": _bounded_text(_sanitize_text(graph_delta), limit=1000),
        "validation_state": _bounded_text(_sanitize_text(validation_state), limit=200),
        "save_state": _bounded_text(_sanitize_text(save_state), limit=200),
        "reproducible": bool(reproducible),
        "notes": _bounded_text(_sanitize_text(notes), limit=1000),
    }
    path = resolve_dogfood_intake_path(intake_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    return {
        "tool": "record_dogfood_case",
        "ok": True,
        "intake_path": str(path),
        "record": record,
    }


def summarize_dogfood_cases(*, intake_path: str | Path | None = None) -> dict[str, Any]:
    """Summarize structured dogfooding records into conservative clusters."""
    path = resolve_dogfood_intake_path(intake_path)
    if not path.exists():
        return {
            "tool": "summarize_dogfood_cases",
            "ok": True,
            "intake_path": str(path),
            "total_records": 0,
            "cluster_count": 0,
            "clusters": [],
            "counts": _empty_counts(),
            "warnings": ["dogfood_intake_empty"],
        }
    records = _read_records(path)
    clusters: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        clusters.setdefault(_cluster_key(record), []).append(record)
    summaries = [
        _summarize_cluster(cluster_id, items) for cluster_id, items in sorted(clusters.items())
    ]
    summaries.sort(key=lambda item: (-item["count"], item["cluster_id"]))
    return {
        "tool": "summarize_dogfood_cases",
        "ok": True,
        "intake_path": str(path),
        "schema_version": DOGFOOD_SCHEMA_VERSION,
        "total_records": len(records),
        "cluster_count": len(summaries),
        "counts": {
            "by_source": dict(Counter(record["source"] for record in records)),
            "by_task_type": dict(Counter(record["task_type"] for record in records)),
            "by_failure_category": dict(Counter(record["failure_category"] for record in records)),
            "by_severity": dict(Counter(record["severity"] for record in records)),
        },
        "clusters": summaries,
    }


def resolve_dogfood_intake_path(intake_path: str | Path | None = None) -> Path:
    """Resolve the dogfooding intake JSONL path."""
    if intake_path is not None:
        return Path(intake_path).expanduser()
    return resolve_workspace_root() / "reports" / "dogfood" / "intake.jsonl"


def resolve_workspace_root(start: str | Path | None = None) -> Path:
    """Resolve this project workspace root."""
    current = Path.cwd() if start is None else Path(start).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        pyproject = candidate / "pyproject.toml"
        if pyproject.is_file() and 'name = "grc-agent"' in pyproject.read_text(encoding="utf-8"):
            return candidate
    raise DogfoodIntakeError("Could not resolve the GRC Agent workspace root.")


def _read_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DogfoodIntakeError(f"Invalid dogfood JSONL at {path}:{line_number}") from exc
        if isinstance(payload, dict):
            records.append(_normalize_record(payload))
    return records


def _normalize_record(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = _bounded_text(_sanitize_text(payload.get("prompt", "")), limit=1000)
    source = (
        payload.get("source") if payload.get("source") in VALID_DOGFOOD_SOURCES else "manual_review"
    )
    task_type = (
        payload.get("task_type") if payload.get("task_type") in VALID_TASK_TYPES else "other"
    )
    failure_category = (
        payload.get("failure_category")
        if payload.get("failure_category") in VALID_FAILURE_CATEGORIES
        else "other"
    )
    severity = payload.get("severity") if payload.get("severity") in VALID_SEVERITIES else "info"
    return {
        "schema_version": str(payload.get("schema_version") or DOGFOOD_SCHEMA_VERSION),
        "timestamp": str(payload.get("timestamp") or ""),
        "source": source,
        "graph_ref": _safe_graph_ref(str(payload.get("graph_ref") or ""), source=source),
        "task_type": task_type,
        "failure_category": failure_category,
        "severity": severity,
        "prompt": prompt,
        "prompt_key": str(payload.get("prompt_key") or _prompt_key(prompt)),
        "expected": _bounded_text(_sanitize_text(payload.get("expected", "")), limit=1000),
        "actual": _bounded_text(_sanitize_text(payload.get("actual", "")), limit=1000),
        "actual_tools": _clean_tools(payload.get("actual_tools") or ()),
        "graph_delta": _bounded_text(_sanitize_text(payload.get("graph_delta", "")), limit=1000),
        "validation_state": _bounded_text(
            _sanitize_text(payload.get("validation_state", "")), limit=200
        ),
        "save_state": _bounded_text(_sanitize_text(payload.get("save_state", "")), limit=200),
        "reproducible": bool(payload.get("reproducible")),
        "notes": _bounded_text(_sanitize_text(payload.get("notes", "")), limit=1000),
    }


def _summarize_cluster(cluster_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    failure_categories = Counter(record["failure_category"] for record in records)
    severities = Counter(record["severity"] for record in records)
    sources = Counter(record["source"] for record in records)
    task_types = Counter(record["task_type"] for record in records)
    prompts = []
    expected = []
    actual = []
    notes = []
    for record in records:
        prompt = record["prompt"]
        if prompt and prompt not in prompts and len(prompts) < 3:
            prompts.append(prompt)
        expected_text = record["expected"]
        if expected_text and expected_text not in expected and len(expected) < 3:
            expected.append(expected_text)
        actual_text = record["actual"]
        if actual_text and actual_text not in actual and len(actual) < 3:
            actual.append(actual_text)
        note = record["notes"]
        if note and note not in notes and len(notes) < 3:
            notes.append(note)
    return {
        "cluster_id": cluster_id,
        "count": len(records),
        "sources": dict(sources),
        "task_types": dict(task_types),
        "failure_categories": dict(failure_categories),
        "severities": dict(severities),
        "reproducible_count": sum(1 for record in records if record["reproducible"]),
        "representative_prompts": prompts,
        "expected_preview": expected[:3],
        "actual_preview": actual[:3],
        "notes_count": sum(1 for record in records if record["notes"]),
        "notes_preview": notes[:3],
        "graph_refs": sorted({record["graph_ref"] for record in records if record["graph_ref"]})[
            :5
        ],
        "actual_tools": sorted({tool for record in records for tool in record["actual_tools"]})[
            :10
        ],
        "recommendation": _cluster_recommendation(records),
    }


def _cluster_recommendation(records: list[dict[str, Any]]) -> str:
    if any(record["severity"] == "stop_the_line" for record in records):
        return "stop_and_investigate"
    categories = {record["failure_category"] for record in records}
    if categories <= {"no_failure", "safe_preflight_rejection"}:
        return "baseline_observation"
    if len(records) >= 3 or len({record["source"] for record in records}) >= 2:
        return "candidate_generic_gap"
    return "needs_more_evidence"


def _cluster_key(record: dict[str, Any]) -> str:
    return "|".join(
        (
            record["failure_category"],
            record["task_type"],
            _topic_key(record["prompt_key"]),
        )
    )


def _topic_key(prompt_key: str) -> str:
    tokens = _TOKEN_RE.findall(prompt_key.lower())
    if not tokens:
        return "empty"
    stop = {"the", "a", "an", "to", "and", "or", "it", "this", "that", "graph"}
    useful = [token for token in tokens if token not in stop]
    return "-".join(useful[:5]) or "empty"


def _prompt_key(prompt: str) -> str:
    normalized = _WHITESPACE_RE.sub(" ", _sanitize_text(prompt).strip().lower())
    return normalized[:200]


def _safe_graph_ref(graph: str, *, source: str) -> str:
    if not graph:
        return ""
    text = str(graph).strip()
    path = Path(text).expanduser()
    installed_root = Path("/usr/share/gnuradio/examples")
    try:
        resolved = path.resolve()
        if source == "installed_example" and resolved.is_relative_to(installed_root):
            return str(resolved.relative_to(installed_root))
    except (OSError, RuntimeError):
        pass
    sanitized = _sanitize_text(text)
    if source == "user_graph":
        return "<user_graph>"
    return _bounded_text(sanitized, limit=200)


def _sanitize_text(value: Any) -> str:
    text = str(value or "")
    text = _PATH_RE.sub("<path>", text)
    text = _GRC_FILE_RE.sub("<grc_file>", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def _bounded_text(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _clean_tools(values: list[str] | tuple[str, ...] | Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list | tuple):
        return []
    cleaned: list[str] = []
    for value in values:
        tool = re.sub(r"[^A-Za-z0-9_]", "", str(value))[:80]
        if tool and tool not in cleaned:
            cleaned.append(tool)
    return cleaned[:20]


def _empty_counts() -> dict[str, dict[str, int]]:
    return {
        "by_source": {},
        "by_task_type": {},
        "by_failure_category": {},
        "by_severity": {},
    }


__all__ = [
    "DOGFOOD_SCHEMA_VERSION",
    "VALID_DOGFOOD_SOURCES",
    "VALID_FAILURE_CATEGORIES",
    "VALID_SEVERITIES",
    "VALID_TASK_TYPES",
    "DogfoodIntakeError",
    "record_dogfood_case",
    "resolve_dogfood_intake_path",
    "summarize_dogfood_cases",
]
