"""Ollama model discovery, tool-support probing, and model pulling.

The mutating counterpart (``pull_ollama_model``) is the only model-download
path. All models are discovered and pulled directly from Ollama's native APIs.

The module also exposes a detect-only :func:`probe_ollama_backend` helper
that the GUI and CLI both consume. The helper is strictly read-only — it
never starts, stops, or downloads anything. Per AGENTS.md, GRC Agent does
not own the Ollama lifecycle; the user is responsible for ``ollama serve``
and ``ollama pull <model>``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class OllamaBackendStatus:
    """Structured result of a single reachability + availability probe.

    Returned by :func:`probe_ollama_backend`. The GUI and CLI both consume
    this object so the same hint text reaches the user regardless of the
    front-end. All fields are pure data — no Qt types, no logging side
    effects — so the same status is renderable in a stderr block, a
    QStackedWidget page, or a unit test assertion.
    """

    server_url: str
    server_reachable: bool
    model_alias: str
    model_available: bool
    available_models: list[str] = field(default_factory=list)
    start_command: str = ""
    pull_command: str = ""
    hint: str = ""


def _fetch_ollama_tags(
    server_url: str,
    *,
    client: httpx.Client | None = None,
    timeout_seconds: float = 3.0,
) -> list[str] | None:
    """Return the list of model names from ``/api/tags`` or ``None`` on error.

    The shared HTTP error path collapses every transport / decode failure
    into a single ``None`` so the caller can branch on reachability
    without re-implementing the same ``try/except`` ladder.
    """
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=timeout_seconds)
    try:
        response = client.get(
            f"{server_url.rstrip('/')}/api/tags",
            headers={"Accept": "application/json"},
        )
        data = response.json()
        if isinstance(data, dict) and "models" in data:
            return [m["name"] for m in data["models"] if isinstance(m, dict) and "name" in m]
        return []
    except Exception as exc:
        logger.debug("Ollama tag probe failed on %s: %s", server_url, exc)
        return None
    finally:
        if own_client:
            client.close()


def probe_ollama_backend(
    server_url: str,
    model_alias: str,
    *,
    client: httpx.Client | None = None,
    timeout_seconds: float = 3.0,
) -> OllamaBackendStatus:
    """Detect-only probe of an Ollama server and the configured model.

    Performs a single ``GET /api/tags`` and returns a frozen
    :class:`OllamaBackendStatus`. The helper never starts the daemon,
    never pulls a model, and never touches the network beyond the
    probe itself. Both the GUI setup widget and the CLI startup
    consume the same status so the user sees identical wording in
    both entry points.

    The status is purely informational. A missing model is *not* an
    error: the user is expected to pick from whatever is installed or
    pull a new one themselves. ``model_available`` is ``False`` when
    no alias is supplied, when the alias is not on the server, or when
    the server is unreachable.
    """
    from grc_agent.toolagents_runtime import model_name_matches

    server_url = (server_url or "").rstrip("/")
    model_alias = (model_alias or "").strip()
    pull_command = f"ollama pull {model_alias}" if model_alias else "ollama pull <model_name>"

    tags = _fetch_ollama_tags(
        server_url,
        client=client,
        timeout_seconds=timeout_seconds,
    )

    if tags is None:
        hint = (
            f"Ollama server is not reachable at {server_url}. "
            "Run the following in a new terminal, then click Refresh:\n"
            f"  ollama serve\n"
            f"  {pull_command}"
        )
        return OllamaBackendStatus(
            server_url=server_url,
            server_reachable=False,
            model_alias=model_alias,
            model_available=False,
            available_models=[],
            pull_command=pull_command,
            hint=hint,
        )

    available = list(tags)
    model_available = bool(model_alias) and model_name_matches(model_alias, available)

    if model_available:
        hint = f"Ollama is running at {server_url} · model `{model_alias}` is ready."
    elif model_alias:
        hint = (
            f"Ollama is running at {server_url} · `{model_alias}` is not installed. "
            f"Use `ollama pull <model_name>` to download any ollama model, "
            f"e.g. `ollama pull qwen3.5:9b-q4_K_M`."
        )
    else:
        hint = (
            f"Ollama is running at {server_url}. "
            f"Use `ollama pull <model_name>` to download any ollama model, "
            f"e.g. `ollama pull qwen3.5:9b-q4_K_M`."
        )
    return OllamaBackendStatus(
        server_url=server_url,
        server_reachable=True,
        model_alias=model_alias,
        model_available=model_available,
        available_models=available,
        pull_command=pull_command,
        hint=hint,
    )


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

    This is a blocking call. For GUI use, wrap it in a background thread with
    progress reporting via :func:`stream_ollama_pull`.

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


def stream_ollama_pull(
    model_name: str,
    *,
    server_url: str = DEFAULT_OLLAMA_URL,
) -> Any:
    """Stream ``ollama pull`` progress as a generator of status dicts.

    Yields parsed JSON status objects like:
        ``{"status": "pulling manifest"}``
        ``{"status": "downloading", "total": 123, "completed": 50}``
        ``{"status": "success"}``

    Callers iterate until exhaustion; the final yield is always an ``ok`` dict.
    """
    env: dict[str, str] = {}
    if server_url and server_url != DEFAULT_OLLAMA_URL:
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
    "OllamaBackendStatus",
    "check_ollama_tool_support",
    "discover_ollama_models",
    "probe_ollama_backend",
    "pull_ollama_model",
    "stream_ollama_pull",
]
