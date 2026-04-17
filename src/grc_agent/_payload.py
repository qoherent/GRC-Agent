"""Shared lightweight payload helpers used across the package."""

from typing import Any


def build_error_payload(
    *,
    error_type: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable structured error payload for public entry points."""
    payload: dict[str, Any] = {
        "ok": False,
        "error_type": error_type,
        "message": message,
    }
    if details:
        payload["details"] = details
    return payload


def join_non_empty(*parts: str) -> str:
    """Join non-empty string parts with single spaces."""
    return " ".join(part for part in parts if part).strip()
