"""Shared runtime bootstrap for CLI and GUI.

Single entry point for retrieval initialization and llama.cpp server
readiness so both products do the same startup dance without duplication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from grc_agent._payload import ErrorCode
from grc_agent.config import AppConfig
from grc_agent.llama_launcher import LlamaLauncherError, LlamaServerLauncher
from grc_agent.llama_probe import LlamaHealthProbe
from grc_agent.retrieval import initialize_retrieval
from grc_agent.toolagents_runtime import ToolAgentsLlamaProviderConfig


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
    start_llama: bool = True,
    init_retrieval: bool = True,
    api_key: str | None = None,
    server_url: str | None = None,
    model_alias: str | None = None,
) -> RuntimeBootstrapResult:
    """Initialize retrieval and/or ensure llama.cpp server readiness.

    Parameters
    ----------
    config:
        Loaded application configuration.
    start_llama:
        When True, ensures the llama.cpp server is running (starts it if
        necessary).  When False, only probes the server (no startup).
    init_retrieval:
        When True, initializes the GNU Radio catalog retrieval index.
    api_key:
        Optional API key for the llama.cpp server.
    server_url:
        Override for config.llama.server_url.
    model_alias:
        Override for config.llama.model.

    Returns
    -------
    RuntimeBootstrapResult with provider_config always populated (from
    actual launch or built from config defaults).
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

    # 2. Llama server startup or probe
    if start_llama:
        _bootstrap_llama(result, config, api_key, effective_server_url, effective_model)
    else:
        _probe_llama(result, config, api_key, effective_server_url, effective_model)

    # Always build a fallback provider config from the static config so
    # callers always have something to pass to ToolAgentsRunner (even if
    # the actual server is unreachable — error surfaces at runtime).
    if result.provider_config is None:
        result.provider_config = _build_fallback_provider(
            config, api_key, effective_server_url, effective_model
        )

    return result


def _bootstrap_llama(
    result: RuntimeBootstrapResult,
    config: AppConfig,
    api_key: str | None,
    effective_server_url: str,
    effective_model: str,
) -> None:
    """Start or reuse a llama.cpp server and populate the result."""
    try:
        launcher = LlamaServerLauncher(
            config.llama,
            server_url=effective_server_url,
            model_alias=effective_model,
            api_key=api_key,
        )
        launch_result = launcher.ensure_server_ready()
        result.provider_config = launch_result.provider_config
        result.health_evidence = launch_result.health_evidence
        result.server_url = launch_result.server_url
        result.model_alias = launch_result.model_alias
        result.launch_status = launch_result.status
        result.launch_pid = launch_result.pid
    except LlamaLauncherError as exc:
        result.launch_status = "failed"
        message = str(exc)
        result.errors.append(message)
        result.error_type = _classify_launcher_error(message)


def _probe_llama(
    result: RuntimeBootstrapResult,
    config: AppConfig,
    api_key: str | None,
    effective_server_url: str,
    effective_model: str,
) -> None:
    """Probe the server address for evidence without starting a process."""
    try:
        probe = LlamaHealthProbe(
            base_url=effective_server_url,
            api_key=api_key,
            timeout_seconds=min(config.llama.request_timeout_seconds, 5.0),
        )
        evidence = probe.health_evidence(expected_alias=effective_model)
        result.health_evidence = evidence
        result.server_url = effective_server_url
        result.model_alias = effective_model
        result.launch_status = "probe_ok"
    except Exception as exc:
        result.health_evidence = None
        result.launch_status = "probe_failed"
        message = str(exc)
        result.errors.append(message)
        result.error_type = _classify_launcher_error(message)


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
        temperature=config.llama.temperature,
        enable_thinking=config.llama.enable_thinking,
    )


__all__ = ["RuntimeBootstrapResult", "bootstrap_runtime"]


def _classify_launcher_error(message: str) -> str:
    """Map a launcher/probe error message to a stable ``ErrorCode`` value.

    Allows the CLI/GUI to surface actionable install/config hints instead of
    raw exception text. Falls back to ``internal_error`` for unknown shapes.
    """
    lowered = message.lower()
    if "llama-server" in lowered and "not found" in lowered:
        return ErrorCode.LLAMA_SERVER_MISSING
    if "llama-server" in lowered or "llama-server binary" in lowered:
        return ErrorCode.LLAMA_SERVER_MISSING
    if "alias" in lowered and "mismatch" in lowered:
        return ErrorCode.MODEL_NOT_FOUND
    if "model" in lowered and "not found" in lowered:
        return ErrorCode.MODEL_NOT_FOUND
    return ErrorCode.INTERNAL_ERROR
