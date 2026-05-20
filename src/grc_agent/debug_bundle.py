"""Redacted support bundle helpers for package and ops debugging."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
from typing import Any

from grc_agent.config import AppConfig, resolve_config_path


DEBUG_BUNDLE_SCHEMA_VERSION = "2026-05-20.phase18-debug-bundle-v1"
_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|ollama[_-]?key|token|secret|password|credential|authorization|auth[_-]?header|bearer)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE_RE = re.compile(
    r"(bearer\s+[a-z0-9._~+/=-]+|authorization\s*[:=]\s*[^\s,;}]+|sk-[a-z0-9_-]{12,})",
    re.IGNORECASE,
)


def redact_for_debug_bundle(value: Any) -> Any:
    """Return a recursively redacted JSON-safe value."""

    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        redacted_index = 0
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if _SENSITIVE_KEY_RE.search(key):
                redacted_index += 1
                redacted[f"redacted_sensitive_field_{redacted_index}"] = _REDACTED
                continue
            redacted[key] = redact_for_debug_bundle(raw_value)
        return redacted
    if isinstance(value, list):
        return [redact_for_debug_bundle(item) for item in value]
    if isinstance(value, tuple):
        return [redact_for_debug_bundle(item) for item in value]
    if isinstance(value, str):
        if _SENSITIVE_VALUE_RE.search(value):
            return _REDACTED
        return value
    return value


def _package_version() -> str | None:
    try:
        return version("grc-agent")
    except PackageNotFoundError:
        return None


def _git_output(repo_root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return None
    return completed.stdout.strip()


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _is_git_ignored(repo_root: Path, relative_path: str) -> bool | None:
    try:
        completed = subprocess.run(
            ["git", "check-ignore", "--quiet", relative_path],
            cwd=repo_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None
    return completed.returncode == 0


def _is_git_tracked(repo_root: Path, relative_path: str) -> bool | None:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--error-unmatch", relative_path],
            cwd=repo_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None
    return completed.returncode == 0


def build_artifact_hygiene_summary(repo_root: Path) -> dict[str, Any]:
    """Summarize local artifact ignore/tracking status without reading contents."""

    paths = {
        "env_file": ".env",
        "local_state": ".grc_agent",
        "llama_eval_store": ".llama_eval",
        "reports": "reports",
        "tmp": "tmp",
    }
    rows: dict[str, Any] = {}
    for label, relative in paths.items():
        rows[label] = {
            "path": relative,
            "exists": _path_exists(repo_root / relative),
            "git_ignored": _is_git_ignored(repo_root, relative),
            "git_tracked": _is_git_tracked(repo_root, relative),
        }
    return {
        "checked_paths": rows,
        "note": "Summary only; file contents are not read for the debug bundle.",
    }


def summarize_recent_traces(repo_root: Path) -> dict[str, Any]:
    """Return counts and latest mtimes for trace-like local stores."""

    stores = {
        "llama_eval_store": repo_root / ".llama_eval",
        "reports": repo_root / "reports",
        "history": repo_root / ".grc_agent" / "history",
    }
    summary: dict[str, Any] = {}
    for label, root in stores.items():
        if not root.exists():
            summary[label] = {"exists": False, "file_count": 0}
            continue
        files = [path for path in root.rglob("*") if path.is_file()]
        latest = max((path.stat().st_mtime for path in files), default=None)
        summary[label] = {
            "exists": True,
            "file_count": len(files),
            "latest_mtime_utc": (
                datetime.fromtimestamp(latest, UTC).isoformat() if latest else None
            ),
        }
    return {
        "stores": summary,
        "raw_prompt_history_included": False,
        "raw_graph_content_included": False,
    }


def summarize_vector_stats(vector_stats: dict[str, Any]) -> dict[str, Any]:
    """Keep vector diagnostics useful without dumping source-hash/path maps."""

    if vector_stats.get("ok") is not True:
        return {
            "ok": False,
            "error_type": vector_stats.get("error_type"),
            "message": vector_stats.get("message"),
        }
    keys = (
        "ok",
        "collection_alias",
        "active_collection",
        "previous_collection",
        "record_count",
        "points_count",
        "embedding_model",
        "embedding_size",
        "docs_only",
        "gnuradio_version",
        "catalog_root",
        "records_by_source_type",
        "corpus_hash",
        "corpus_version",
        "index_schema_version",
        "build_timestamp",
    )
    return {key: vector_stats.get(key) for key in keys if key in vector_stats}


def build_debug_bundle(
    *,
    config: AppConfig,
    config_path: str | None,
    doctor_report: dict[str, Any],
    health_report: dict[str, Any],
    release_manifest: dict[str, Any],
    vector_stats: dict[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    """Build a redacted debug bundle payload."""

    resolved_config = resolve_config_path(config_path)
    git_dirty_files = (_git_output(repo_root, "status", "--porcelain") or "").splitlines()
    payload = {
        "schema_version": DEBUG_BUNDLE_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "ok": True,
        "package": {
            "name": "grc-agent",
            "version": _package_version(),
        },
        "git": {
            "commit": _git_output(repo_root, "rev-parse", "HEAD"),
            "short_commit": _git_output(repo_root, "rev-parse", "--short=12", "HEAD"),
            "branch": _git_output(repo_root, "rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(git_dirty_files),
            "dirty_files": git_dirty_files,
        },
        "environment": {
            "python_version": sys.version,
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "config": {
            "source": str(resolved_config) if resolved_config else "built-in defaults",
            "effective": redact_for_debug_bundle(config),
        },
        "doctor": redact_for_debug_bundle(doctor_report),
        "health": redact_for_debug_bundle(health_report),
        "release_manifest": redact_for_debug_bundle(release_manifest),
        "tool_surface": redact_for_debug_bundle(release_manifest.get("tool_surface", {})),
        "hashes": redact_for_debug_bundle(release_manifest.get("hashes", {})),
        "vector_index": redact_for_debug_bundle(summarize_vector_stats(vector_stats)),
        "gnu_radio": {
            "doctor_checks": [
                check
                for check in doctor_report.get("checks", [])
                if isinstance(check, dict)
                and str(check.get("name", "")).lower()
                in {"grcc on path", "gnu radio import/version"}
            ],
        },
        "llama": {
            "server_url": health_report.get("llama_server_url")
            or release_manifest.get("runtime", {}).get("server_url"),
            "model_ready": health_report.get("llama_model_ready"),
            "context_verified": health_report.get("llama_context_verified"),
            "actual_context_tokens": health_report.get("llama_actual_context_tokens"),
            "desired_context_tokens": health_report.get("llama_desired_context_tokens"),
            "status": health_report.get("status"),
            "status_reasons": health_report.get("status_reasons", []),
        },
        "recent_trace_summaries": summarize_recent_traces(repo_root),
        "artifact_hygiene": build_artifact_hygiene_summary(repo_root),
        "recent_errors": {
            "included": False,
            "reason": "No structured application error log path is configured.",
        },
        "exclusions": {
            "env_contents_included": False,
            "raw_prompt_history_included": False,
            "raw_graph_content_included": False,
        },
    }
    return redact_for_debug_bundle(payload)


def write_debug_bundle(path: str | Path, payload: dict[str, Any]) -> Path:
    """Write a debug bundle JSON file."""

    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path


def debug_bundle_summary(payload: dict[str, Any], output_path: Path) -> dict[str, Any]:
    """Return a compact CLI summary for a generated bundle."""

    health = payload.get("health", {}) if isinstance(payload.get("health"), dict) else {}
    vector_index = (
        payload.get("vector_index", {})
        if isinstance(payload.get("vector_index"), dict)
        else {}
    )
    return {
        "ok": True,
        "output": str(output_path),
        "schema_version": payload.get("schema_version"),
        "health_status": health.get("status"),
        "health_status_reasons": health.get("status_reasons", []),
        "vector_index_ok": vector_index.get("ok"),
        "secrets_redacted": True,
    }


__all__ = [
    "DEBUG_BUNDLE_SCHEMA_VERSION",
    "build_debug_bundle",
    "debug_bundle_summary",
    "redact_for_debug_bundle",
    "summarize_vector_stats",
    "write_debug_bundle",
]
