"""Non-chat llama.cpp health and metadata probes."""

from __future__ import annotations

import json
import socket
from typing import Any
from urllib import error, request


class LlamaServerError(RuntimeError):
    """Raised when a llama.cpp server probe fails."""


class LlamaHealthProbe:
    """Small stdlib client for non-chat llama.cpp endpoints."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero.")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def require_ready(self) -> None:
        """Fail unless `/health` reports status ok."""
        response = self._request_json("GET", "/health")
        if response.get("status") != "ok":
            raise LlamaServerError("llama.cpp server did not report status=ok.")

    def get_model_id(self) -> str:
        """Return the single configured model id from `/v1/models`."""
        response = self._request_json("GET", "/v1/models")
        data = response.get("data")
        if not isinstance(data, list):
            raise LlamaServerError("llama.cpp models response is missing data list.")
        if len(data) != 1:
            raise LlamaServerError(
                "llama.cpp server must expose exactly one model alias; "
                f"found {len(data)}."
            )
        entry = data[0]
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise LlamaServerError("llama.cpp models response has invalid model entry.")
        return entry["id"]

    def require_model_alias(self, expected_alias: str) -> None:
        """Fail when `/v1/models` does not match the configured model alias."""
        if not isinstance(expected_alias, str) or not expected_alias.strip():
            raise ValueError("expected_alias must be a non-empty string.")
        discovered_alias = self.get_model_id()
        if discovered_alias != expected_alias:
            raise LlamaServerError(
                "llama.cpp server alias mismatch: "
                f"configured '{expected_alias}', discovered '{discovered_alias}'."
            )

    def get_server_properties(self) -> dict[str, Any]:
        """Return llama.cpp server properties from `/props`."""
        return self._request_json("GET", "/props")

    def health_evidence(self, *, expected_alias: str) -> dict[str, Any]:
        """Return readiness evidence without weakening failure semantics."""
        self.require_ready()
        self.require_model_alias(expected_alias)
        props = self.get_server_properties()
        actual_context = extract_model_context_limit(props)
        if actual_context is None:
            raise LlamaServerError("llama.cpp server context is unknown from /props.")
        return {
            "llama_server_url": self.base_url,
            "llama_model": expected_alias,
            "llama_actual_context_tokens": actual_context,
            "llama_context_verified": True,
            "llama_model_ready": True,
        }

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send one JSON request and decode one object response."""
        url = f"{self.base_url}{path}"
        headers = {"Accept": "application/json"}
        data: bytes | None = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = request.Request(url, headers=headers, data=data, method=method)

        try:
            response = request.urlopen(req, timeout=self.timeout_seconds)
        except error.HTTPError as exc:
            raw_body = exc.read().decode("utf-8")
            raise LlamaServerError(_format_http_error(exc.code, raw_body)) from exc
        except error.URLError as exc:
            if _is_timeout_reason(exc.reason):
                raise LlamaServerError(
                    f"Timed out connecting to llama.cpp server at {url}."
                ) from exc
            raise LlamaServerError(
                f"Failed to reach llama.cpp server at {url}: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise LlamaServerError(
                f"Timed out connecting to llama.cpp server at {url}."
            ) from exc
        except socket.timeout as exc:
            raise LlamaServerError(
                f"Timed out connecting to llama.cpp server at {url}."
            ) from exc

        try:
            with response:
                raw_body = response.read().decode("utf-8")
        except TimeoutError as exc:
            raise LlamaServerError(
                f"Timed out waiting for llama.cpp server response from {path}."
            ) from exc
        except socket.timeout as exc:
            raise LlamaServerError(
                f"Timed out waiting for llama.cpp server response from {path}."
            ) from exc

        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise LlamaServerError(
                f"llama.cpp server returned non-JSON response from {path}."
            ) from exc
        if not isinstance(parsed, dict):
            raise LlamaServerError(
                f"llama.cpp server returned non-object JSON from {path}."
            )
        return parsed


def extract_model_context_limit(props: dict[str, Any]) -> int | None:
    """Extract server context window from `/props` payload when present."""
    if not isinstance(props, dict):
        return None
    default_settings = props.get("default_generation_settings")
    if isinstance(default_settings, dict):
        n_ctx = default_settings.get("n_ctx")
        if isinstance(n_ctx, int) and n_ctx > 0:
            return n_ctx
        params = default_settings.get("params")
        if isinstance(params, dict):
            n_ctx = params.get("n_ctx")
            if isinstance(n_ctx, int) and n_ctx > 0:
                return n_ctx
    settings = props.get("settings")
    if isinstance(settings, dict):
        n_ctx = settings.get("n_ctx")
        if isinstance(n_ctx, int) and n_ctx > 0:
            return n_ctx
    return None


def _format_http_error(status_code: int, raw_body: str) -> str:
    body = raw_body.strip()
    if len(body) > 500:
        body = f"{body[:500]}..."
    return f"llama.cpp server returned HTTP {status_code}: {body}"


def _is_timeout_reason(reason: Any) -> bool:
    return isinstance(reason, TimeoutError | socket.timeout)


__all__ = ["LlamaHealthProbe", "LlamaServerError", "extract_model_context_limit"]
