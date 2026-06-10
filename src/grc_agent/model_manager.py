"""Ollama model discovery, tool-support probing, and model pulling.

The mutating counterpart (``pull_ollama_model``) is the only model-download
path. All models are discovered and pulled directly from Ollama's native APIs.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import Any

import httpx

logger = logging.getLogger(__name__)


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
            model_name, url, exc,
        )
        return None
    finally:
        if own_client:
            client.close()


def pull_ollama_model(
    model_name: str,
    *,
    server_url: str = "http://localhost:11434",
) -> dict[str, Any]:
    """Pull an Ollama model from the registry using ``ollama pull``.

    This is a blocking call. For GUI use, wrap it in a background thread with
    progress reporting via :func:`stream_ollama_pull`.

    Returns a dict with ``ok`` and either ``model`` or ``error``.
    """
    env: dict[str, str] = {}
    if server_url and server_url != "http://localhost:11434":
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


def stream_ollama_pull(
    model_name: str,
    *,
    server_url: str = "http://localhost:11434",
) -> Any:
    """Stream ``ollama pull`` progress as a generator of status dicts.

    Yields parsed JSON status objects like:
        ``{"status": "pulling manifest"}``
        ``{"status": "downloading", "total": 123, "completed": 50}``
        ``{"status": "success"}``

    Callers iterate until exhaustion; the final yield is always an ``ok`` dict.
    """
    env: dict[str, str] = {}
    if server_url and server_url != "http://localhost:11434":
        env["OLLAMA_HOST"] = server_url

    try:
        proc = subprocess.Popen(
            ["ollama", "pull", model_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, **env},
        )
    except FileNotFoundError:
        yield {"status": "error", "error": "ollama binary not found on PATH."}
        return

    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                yield {"status": "progress", "raw": line}
    finally:
        proc.wait()
        if proc.returncode != 0:
            yield {"status": "error", "error": "Pull command failed."}


__all__ = [
    "check_ollama_tool_support",
    "discover_ollama_models",
    "pull_ollama_model",
    "stream_ollama_pull",
]
