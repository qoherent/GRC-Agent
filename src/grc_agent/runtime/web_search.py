"""Ollama web_search and web_fetch tool wrappers.

Thin REST clients around Ollama's hosted search APIs. The
wrappers return the standard ``ok / results`` payload used across
the runtime so downstream formatters (``tool_history_content_as_text``)
can consume them the same way as catalog/docs results.

Authentication
--------------
The OLLAMA_API_KEY env var (loaded by
:func:`grc_agent.config._ensure_dotenv_loaded` at import time) is
sent as a Bearer token. The wrapper fails soft (returns
``ok=False`` with ``error_type="missing_api_key"``) if the key is
absent — the runtime never raises Python exceptions for tool
errors.

API contract
------------
See https://docs.ollama.com/cloud (Web search + Web fetch sections).
The request body for search is ``{"query": ..., "max_results": N}``
(max_results default 5, max 10). The response is
``{"results": [{title, url, content}, ...]}``. The fetch body is
``{"url": "..."}`` and the response is
``{"title", "content", "links"}``.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

OLLAMA_WEB_SEARCH_URL = "https://ollama.com/api/web_search"
OLLAMA_WEB_FETCH_URL = "https://ollama.com/api/web_fetch"
_MAX_RESULTS = 10
_DEFAULT_MAX_RESULTS = 5
_REQUEST_TIMEOUT = 30.0


def _api_key() -> str | None:
    """Return the OLLAMA_API_KEY (or None) without raising."""
    return os.getenv("OLLAMA_API_KEY")


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }


def _ok_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the standard ok/result payload that other runtime tools emit."""
    result = dict(payload)
    result["ok"] = True
    result.setdefault("message", "")
    return result


def _err_payload(message: str, *, error_type: str) -> dict[str, Any]:
    """Build a typed ok=False payload — the runtime convention for tool errors."""
    return {
        "ok": False,
        "message": message,
        "error_type": error_type,
    }


def web_search(query: str, max_results: int = _DEFAULT_MAX_RESULTS) -> dict[str, Any]:
    """Search the web via Ollama's hosted search API.

    Returns the standard ``ok / results`` payload. ``results`` is a
    list of ``{title, url, content}`` dicts. On error (missing
    key, network failure, non-2xx response) returns ``ok=False``
    with a typed ``error_type``.
    """
    if not _api_key():
        return _err_payload(
            "OLLAMA_API_KEY is not set; web search is unavailable.",
            error_type="missing_api_key",
        )
    clamped = max(1, min(int(max_results), _MAX_RESULTS))
    body: dict[str, Any] = {"query": query, "max_results": clamped}
    try:
        response = httpx.post(
            OLLAMA_WEB_SEARCH_URL,
            headers=_auth_headers(),
            json=body,
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("web_search http_error query=%r error=%s", query, exc)
        return _err_payload(f"web_search failed: {exc}", error_type="network_error")
    except Exception as exc:
        logger.exception("web_search unexpected_error query=%r", query)
        return _err_payload(f"web_search failed: {exc}", error_type="network_error")
    return _ok_payload({"results": payload.get("results", [])})


def web_fetch(url: str) -> dict[str, Any]:
    """Fetch a single web page via Ollama's hosted fetch API.

    Returns ``ok / title / content / links``. On error returns
    ``ok=False`` with a typed ``error_type``.
    """
    if not _api_key():
        return _err_payload(
            "OLLAMA_API_KEY is not set; web fetch is unavailable.",
            error_type="missing_api_key",
        )
    try:
        response = httpx.post(
            OLLAMA_WEB_FETCH_URL,
            headers=_auth_headers(),
            json={"url": url},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("web_fetch http_error url=%r error=%s", url, exc)
        return _err_payload(f"web_fetch failed: {exc}", error_type="network_error")
    except Exception as exc:
        logger.exception("web_fetch unexpected_error url=%r", url)
        return _err_payload(f"web_fetch failed: {exc}", error_type="network_error")
    return _ok_payload(
        {
            "title": payload.get("title", ""),
            "content": payload.get("content", ""),
            "links": payload.get("links", []),
        }
    )


__all__ = ["web_search", "web_fetch"]
