"""Shared runtime bootstrap for CLI and GUI.

Single entry point for retrieval initialization and LLM backend readiness
so both products do the same startup dance without duplication.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from grc_agent.config import AppConfig
from grc_agent.domain_models import ErrorCode
from grc_agent.retrieval import initialize_retrieval
from grc_agent.toolagents_runtime import ToolAgentsLlamaProviderConfig, model_name_matches

logger = logging.getLogger(__name__)


@dataclass
class RuntimeBootstrapResult:
    """Outcome of a ``bootstrap_runtime()`` call."""

    provider_config: ToolAgentsLlamaProviderConfig | None = None
    catalog_root: str | None = None
    health_evidence: dict[str, Any] | None = None
    server_url: str = ""
    model_alias: str = ""
    launch_status: str = "skipped"
    launch_pid: int | None = None
    retrieval_ok: bool = False
    errors: list[str] = field(default_factory=list)
    error_type: str | None = None


def bootstrap_runtime(
    config: AppConfig,
    *,
    init_retrieval: bool = True,
    api_key: str | None = None,
    server_url: str | None = None,
    model_alias: str | None = None,
) -> RuntimeBootstrapResult:
    """Initialize retrieval and probe LLM backend readiness.

    Parameters
    ----------
    config:
        Loaded application configuration.
    init_retrieval:
        When True, initializes the GNU Radio catalog retrieval index.
    api_key:
        Optional API key for the backend server.
    server_url:
        Override for config.llama.server_url.
    model_alias:
        Override for config.llama.model.

    Returns
    -------
    RuntimeBootstrapResult with provider_config always populated.
    """
    effective_server_url = (server_url or config.llama.server_url).rstrip("/")
    effective_model = model_alias or config.llama.model

    result = RuntimeBootstrapResult()

    # 1. Retrieval initialization
    if init_retrieval:
        readiness = initialize_retrieval()
        if readiness.get("ok"):
            result.retrieval_ok = True
            result.catalog_root = readiness.get("catalog_root")
        else:
            msg = readiness.get("message", "Retrieval initialization failed.")
            result.errors.append(msg)

    # 2. Probe backend
    _probe_generic(result, config, api_key, effective_server_url, effective_model)

    # Always build a fallback provider config from the static config so
    # callers always have something to pass to ToolAgentsRunner (even if
    # the actual server is unreachable — error surfaces at runtime).
    if result.provider_config is None:
        result.provider_config = _build_fallback_provider(
            config, api_key, effective_server_url, effective_model
        )

    return result


def _probe_generic(
    result: RuntimeBootstrapResult,
    config: AppConfig,
    api_key: str | None,
    effective_server_url: str,
    effective_model: str,
    *,
    client: httpx.Client | None = None,
) -> None:
    """Probe a generic LLM server endpoint (Ollama, OpenRouter, etc.)."""
    backend = config.llama.backend
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=5.0)
    try:
        logger = logging.getLogger(__name__)

        # Query /v1/models to verify the server is alive and reachable
        openai_base_url = f"{effective_server_url.rstrip('/')}/v1"
        url = f"{openai_base_url}/models"
        headers = {"Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        response = client.get(url, headers=headers)
        try:
            parsed = response.json()
            if isinstance(parsed, dict) and "data" in parsed:
                model_ids = [
                    m.get("id") for m in parsed["data"] if isinstance(m, dict) and m.get("id")
                ]
                if not model_name_matches(effective_model, model_ids):
                    logger.warning(
                        f"Model '{effective_model}' not found in /v1/models of {backend}. Available models: {model_ids}"
                    )
        except Exception:
            pass

        result.server_url = effective_server_url
        result.model_alias = effective_model
        result.launch_status = "probe_ok"
        result.health_evidence = {
            "server_url": effective_server_url,
            "model": effective_model,
            "provider_type": backend,
            "model_ready": True,
            "context_verified": False,
            "actual_context_tokens": config.llama.max_tokens,
        }

        # For Ollama, check if the model's template supports tool calling
        if backend == "ollama":
            try:
                from grc_agent.model_manager import check_ollama_tool_support

                tool_ok = check_ollama_tool_support(effective_server_url, effective_model)
                if tool_ok is False:
                    logger.warning(
                        "Ollama model '%s' does not support tool calling. "
                        "Its chat template lacks {{ .Tools }}. "
                        "The agent requires tool calling to function. "
                        "Use a model fine-tuned for tool use, or switch backend to openrouter.",
                        effective_model,
                    )
            except Exception:
                pass
    except Exception as exc:
        result.launch_status = "probe_failed"
        result.health_evidence = None
        message = f"Failed to reach {backend} server at {effective_server_url}: {exc}"
        result.errors.append(message)
        result.error_type = _classify_launcher_error(exc, effective_server_url)
    finally:
        if own_client:
            client.close()


def _build_fallback_provider(
    config: AppConfig,
    api_key: str | None,
    effective_server_url: str,
    effective_model: str,
) -> ToolAgentsLlamaProviderConfig:
    return ToolAgentsLlamaProviderConfig(
        base_url=effective_server_url,
        model=effective_model,
        api_key=api_key,
        timeout_seconds=config.llama.request_timeout_seconds,
        max_tokens=config.llama.max_tokens,
        enable_thinking=config.llama.enable_thinking,
        backend=config.llama.backend,
        max_tool_rounds=config.llama.max_tool_rounds,
    )


def _classify_launcher_error(exc: BaseException, server_url: str) -> str:
    """Map a probe exception to a stable ``ErrorCode`` value.

    Connection-shaped errors (TCP refused, DNS failure, timeout) get a
    dedicated ``backend_unreachable`` code so the GUI can render a
    platform-agnostic hint and the user can reach the recovery path
    (Model > Select Model) without restarting the desktop app.
    """
    import httpx

    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout)):
        logger.warning("Backend unreachable at %s: %s", server_url, exc)
        return ErrorCode.BACKEND_UNREACHABLE
    lowered = str(exc).lower()
    if "alias" in lowered and "mismatch" in lowered:
        return ErrorCode.MODEL_NOT_FOUND
    if "model" in lowered and "not found" in lowered:
        return ErrorCode.MODEL_NOT_FOUND
    return ErrorCode.INTERNAL_ERROR


__all__ = ["RuntimeBootstrapResult", "bootstrap_runtime"]
