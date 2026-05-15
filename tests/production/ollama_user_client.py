"""Secret-safe Ollama dummy-user client for production gameplay research."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import time
from typing import Any, Callable
from urllib import error, request

from tests.production.ollama_readiness import (
    OLLAMA_ENV_KEY,
    prepare_ollama_cloud_environment,
)

LOCAL_API_BASE_URL = "http://localhost:11434/api"
CLOUD_API_BASE_URL = "https://ollama.com/api"


class OllamaUserClientError(RuntimeError):
    """Raised when the dummy-user client cannot produce a turn."""

    def __init__(self, message: str, *, error_type: str) -> None:
        super().__init__(message)
        self.error_type = error_type


UrlOpen = Callable[..., Any]


@dataclass(frozen=True)
class OllamaUserClientConfig:
    """Redactable Ollama dummy-user client configuration."""

    base_url: str = CLOUD_API_BASE_URL
    model: str = "gpt-oss:120b"
    timeout: float = 20.0
    max_tokens: int = 96
    temperature: float = 0.2
    seed: int | None = None
    enabled: bool = False
    cloud_mode: bool = True


class OllamaUserClient:
    """Generate free-text dummy user turns with Ollama when explicitly enabled."""

    def __init__(
        self,
        config: OllamaUserClientConfig,
        *,
        api_key: str | None = None,
        urlopen: UrlOpen | None = None,
    ) -> None:
        if not config.base_url.strip():
            raise ValueError("base_url must be non-empty")
        if config.timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if config.max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self.config = config
        self._api_key = api_key
        self._urlopen = request.urlopen if urlopen is None else urlopen

    @classmethod
    def from_environment(
        cls,
        *,
        env_path: Path | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 20.0,
        max_tokens: int = 96,
        temperature: float = 0.2,
        seed: int | None = None,
        enabled: bool = False,
        cloud_mode: bool = True,
    ) -> "OllamaUserClient":
        status = prepare_ollama_cloud_environment(env_path=env_path)
        selected_base_url = base_url or (
            CLOUD_API_BASE_URL if cloud_mode else LOCAL_API_BASE_URL
        )
        selected_model = model or os.environ.get("OLLAMA_DUMMY_USER_MODEL") or "gpt-oss:120b"
        config = OllamaUserClientConfig(
            base_url=selected_base_url.rstrip("/"),
            model=selected_model,
            timeout=float(timeout),
            max_tokens=int(max_tokens),
            temperature=float(temperature),
            seed=seed,
            enabled=bool(enabled),
            cloud_mode=bool(cloud_mode),
        )
        api_key = os.environ.get(OLLAMA_ENV_KEY) if cloud_mode else None
        if cloud_mode and not status.get("cloud_key_present") and not api_key:
            api_key = None
        return cls(config, api_key=api_key)

    def redacted_config(self) -> dict[str, Any]:
        """Return artifact-safe configuration with no secret names or values."""
        return {
            "base_url": self.config.base_url,
            "model": self.config.model,
            "timeout": self.config.timeout,
            "max_tokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "seed": self.config.seed,
            "enabled": self.config.enabled,
            "cloud_mode": self.config.cloud_mode,
            "credential_present": bool(self._api_key),
        }

    def list_models(self) -> dict[str, Any]:
        """Return a redacted model-list summary from `/api/tags`."""
        payload, latency = self._request_json("GET", "/tags")
        models = payload.get("models")
        model_names: list[str] = []
        if isinstance(models, list):
            for item in models:
                if isinstance(item, dict) and isinstance(item.get("name"), str):
                    model_names.append(item["name"])
        return {
            "reachable": True,
            "latency_ms": latency,
            "model_count": len(model_names),
            "models": model_names[:20],
        }

    def generate_user_turn(
        self,
        *,
        scenario_goal: str,
        graph_summary: dict[str, Any],
        allowed_user_behavior: list[str],
        forbidden_user_behavior: list[str],
        prior_conversation: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Generate one concise natural-language user request."""
        prompt = build_dummy_user_prompt(
            scenario_goal=scenario_goal,
            graph_summary=graph_summary,
            allowed_user_behavior=allowed_user_behavior,
            forbidden_user_behavior=forbidden_user_behavior,
            prior_conversation=prior_conversation,
        )
        payload = {
            "model": self.config.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.config.temperature,
                "num_predict": self.config.max_tokens,
            },
        }
        if self.config.seed is not None:
            payload["options"]["seed"] = self.config.seed
        started = time.monotonic()
        response, latency = self._request_json("POST", "/generate", payload=payload)
        text = _extract_generate_text(response)
        return {
            "text": text,
            "latency_ms": latency,
            "usage": _extract_usage(response),
            "prompt_chars": len(prompt),
            "response_chars": len(text),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        }

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], int]:
        if not self.config.enabled:
            raise OllamaUserClientError(
                "Ollama network calls are disabled.",
                error_type="network_disabled",
            )
        if self.config.cloud_mode and not self._api_key:
            raise OllamaUserClientError(
                "Ollama Cloud credential is missing.",
                error_type="missing_key",
            )
        url = f"{self.config.base_url.rstrip('/')}{path}"
        headers = {"Accept": "application/json"}
        data: bytes | None = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        if self.config.cloud_mode and self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        req = request.Request(url, headers=headers, data=data, method=method)
        started = time.monotonic()
        try:
            with self._urlopen(req, timeout=self.config.timeout) as response:
                raw_body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            raise OllamaUserClientError(
                f"Ollama returned HTTP {exc.code}.",
                error_type="http_error",
            ) from exc
        except (error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise OllamaUserClientError(
                "Ollama network request failed.",
                error_type="network_error",
            ) from exc
        latency = int((time.monotonic() - started) * 1000)
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise OllamaUserClientError(
                "Ollama returned non-JSON response.",
                error_type="invalid_json",
            ) from exc
        if not isinstance(parsed, dict):
            raise OllamaUserClientError(
                "Ollama returned non-object JSON.",
                error_type="invalid_json",
            )
        return parsed, latency


def build_dummy_user_prompt(
    *,
    scenario_goal: str,
    graph_summary: dict[str, Any],
    allowed_user_behavior: list[str],
    forbidden_user_behavior: list[str],
    prior_conversation: list[dict[str, Any]],
) -> str:
    """Build the dummy-user prompt without exposing judge expectations."""
    compact_history = [
        {"role": item.get("role"), "content": str(item.get("content", ""))[:300]}
        for item in prior_conversation[-4:]
        if isinstance(item, dict) and item.get("role") in {"user", "assistant"}
    ]
    safe_summary = {
        "block_count": graph_summary.get("block_count"),
        "connection_count": graph_summary.get("connection_count"),
        "variables": graph_summary.get("variable_values", {}),
        "blocks": graph_summary.get("block_names", [])[:10],
        "connections": graph_summary.get("connection_ids", [])[:10],
    }
    return (
        "You are simulating a normal GNU Radio Companion user. "
        "Write exactly one concise natural-language user request for GRC Agent. "
        "Do not mention tool names, schemas, JSON, judge rules, hidden expectations, "
        "or implementation details.\n\n"
        f"Scenario goal:\n{scenario_goal}\n\n"
        f"Graph summary visible to user:\n{json.dumps(safe_summary, sort_keys=True)}\n\n"
        f"Allowed user behavior:\n{json.dumps(allowed_user_behavior, sort_keys=True)}\n\n"
        f"Forbidden user behavior:\n{json.dumps(forbidden_user_behavior, sort_keys=True)}\n\n"
        f"Prior conversation:\n{json.dumps(compact_history, sort_keys=True)}\n\n"
        "Return only the next user message."
    )


def _extract_generate_text(payload: dict[str, Any]) -> str:
    text = payload.get("response")
    if not isinstance(text, str) or not text.strip():
        raise OllamaUserClientError(
            "Ollama generate response did not contain text.",
            error_type="empty_response",
        )
    return text.strip()


def _extract_usage(payload: dict[str, Any]) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    for key in (
        "prompt_eval_count",
        "eval_count",
        "prompt_eval_duration",
        "eval_duration",
        "total_duration",
    ):
        if isinstance(payload.get(key), int):
            usage[key] = payload[key]
    return usage
