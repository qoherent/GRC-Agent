"""Ollama model discovery, tool-support probing, and model pulling.

The mutating counterpart (``pull_ollama_model``) is the only model-download
path. All models are discovered and pulled directly from Ollama's native APIs.
Per AGENTS.md, GRC Agent does not own the Ollama lifecycle; the user is
responsible for running the Ollama server and pulling models themselves.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Any

import httpx

from grc_agent.config import DEFAULT_OLLAMA_URL, DEFAULT_OPENROUTER_URL

logger = logging.getLogger(__name__)


def get_ollama_context_length(
    server_url: str,
    model_name: str,
    *,
    client: httpx.Client | None = None,
) -> int | None:
    """Return ``model_name``'s real context window, or ``None`` on any failure.

    Read from the same ``/api/tags`` payload ``discover_ollama_models`` uses
    (``details.context_length`` on the matching entry) — no separate probe,
    no guessing.
    """
    url = f"{server_url.rstrip('/')}/api/tags"
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=3.0)
    try:
        response = client.get(url, headers={"Accept": "application/json"})
        data = response.json()
        models = data.get("models", []) if isinstance(data, dict) else []
        for entry in models:
            if isinstance(entry, dict) and entry.get("name") == model_name:
                context_length = entry.get("details", {}).get("context_length")
                return context_length if isinstance(context_length, int) else None
    except Exception as exc:
        logger.debug("Ollama context-length lookup failed on %s: %s", url, exc)
        return None
    finally:
        if own_client:
            client.close()
    return None


def get_openrouter_context_length(
    model_name: str,
    *,
    client: httpx.Client | None = None,
) -> int | None:
    """Return ``model_name``'s real context window from OpenRouter's public
    model catalog, or ``None`` on any failure. No API key required."""
    url = f"{DEFAULT_OPENROUTER_URL.rstrip('/')}/v1/models"
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=5.0)
    try:
        response = client.get(url, headers={"Accept": "application/json"})
        data = response.json()
        models = data.get("data", []) if isinstance(data, dict) else []
        for entry in models:
            if isinstance(entry, dict) and entry.get("id") == model_name:
                context_length = entry.get("context_length")
                return context_length if isinstance(context_length, int) else None
    except Exception as exc:
        logger.debug("OpenRouter context-length lookup failed on %s: %s", url, exc)
        return None
    finally:
        if own_client:
            client.close()
    return None


def discover_ollama_models(
    server_url: str,
    *,
    client: httpx.Client | None = None,
) -> list[str]:
    """Fetch available models from the local Ollama server tags endpoint."""
    url = f"{server_url.rstrip('/')}/api/tags"
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=3.0)
    try:
        response = client.get(url, headers={"Accept": "application/json"})
        data = response.json()
        if isinstance(data, dict) and "models" in data:
            return [m["name"] for m in data["models"] if isinstance(m, dict) and "name" in m]
    except Exception as exc:
        logger.debug("Ollama model discovery failed on %s: %s", url, exc)
        return []
    finally:
        if own_client:
            client.close()
    return []


def check_ollama_tool_support(
    server_url: str,
    model_name: str,
    *,
    timeout_seconds: float = 5.0,
    client: httpx.Client | None = None,
) -> bool | None:
    """Check whether an Ollama model likely supports tool calling.

    Uses ``POST /api/show`` to retrieve model metadata. Native Ollama
    models (pulled from the registry) include a ``requires`` field
    indicating the minimum Ollama version they were built for. Models
    created manually from raw GGUF files lack this field and may not
    support tool calling through Ollama's OpenAI-compatible API.

    Returns:
    * ``True`` — native Ollama model (has ``requires`` field).
    * ``False`` — manually created model (no ``requires`` field).
    * ``None`` — the probe failed (server unreachable, model not found, etc.).
    """
    url = f"{server_url.rstrip('/')}/api/show"
    payload = {"model": model_name}
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=timeout_seconds)
    try:
        response = client.post(url, json=payload)
        data = response.json()
        # Native Ollama models include a "requires" field with the minimum
        # Ollama version. Manually created models (ollama create from raw
        # GGUF) lack this field and may not support tool calling.
        return "requires" in data
    except Exception as exc:
        logger.warning(
            "Ollama tool-support probe failed for model %r at %s: %s. "
            "The backend may be down; the GUI will render in degraded mode.",
            model_name,
            url,
            exc,
        )
        return None
    finally:
        if own_client:
            client.close()


def pull_ollama_model(
    model_name: str,
    *,
    server_url: str = DEFAULT_OLLAMA_URL,
) -> dict[str, Any]:
    """Pull an Ollama model from the registry using ``ollama pull``.

    This is a blocking call. For GUI use, wrap it in a background thread.

    Returns a dict with ``ok`` and either ``model`` or ``error``.
    """
    env: dict[str, str] = {}
    if server_url and server_url != DEFAULT_OLLAMA_URL:
        env["OLLAMA_HOST"] = server_url
    try:
        proc = subprocess.run(
            ["ollama", "pull", model_name],
            capture_output=True,
            text=True,
            timeout=600.0,
            env={**os.environ, **env},
        )
    except FileNotFoundError:
        return {"ok": False, "error": "ollama binary not found on PATH."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timed out pulling model '{model_name}'."}
    if proc.returncode != 0:
        error = proc.stderr.strip() or proc.stdout.strip() or "Unknown error"
        return {"ok": False, "error": error}
    return {"ok": True, "model": model_name}


__all__ = [
    "check_ollama_tool_support",
    "discover_ollama_models",
    "pull_ollama_model",
]
