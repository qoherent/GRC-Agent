"""Tests for the runtime startup and bootstrap behavior."""

from __future__ import annotations

import re
import unittest
from unittest import mock

import httpx
from grc_agent.config import (
    DEFAULT_GUARDRAILS_CONFIG,
    DEFAULT_HISTORY_CONFIG,
    DEFAULT_RETRIEVAL_CONFIG,
    AgentConfig,
    AppConfig,
    LlamaConfig,
)
from grc_agent.startup import bootstrap_runtime


class StartupTests(unittest.TestCase):
    def _app_config(self, backend: str, port: int) -> AppConfig:
        return AppConfig(
            llama=LlamaConfig(
                server_url=f"http://127.0.0.1:{port}",
                backend=backend,
                model="test-model",
                request_timeout_seconds=2.0,
            ),
            agent=AgentConfig(
                retrieval=DEFAULT_RETRIEVAL_CONFIG,
                history=DEFAULT_HISTORY_CONFIG,
                guardrails=DEFAULT_GUARDRAILS_CONFIG,
            ),
        )

    def test_bootstrap_runtime_ollama_backend_success(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"id": "test-model"}]})

        test_client = httpx.Client(transport=httpx.MockTransport(handler))

        config = self._app_config("ollama", 11434)
        with mock.patch("grc_agent.model_manager.httpx.Client", return_value=test_client):
            with mock.patch("grc_agent.startup.initialize_retrieval") as mock_init_retrieval:
                mock_init_retrieval.return_value = {
                    "ok": True,
                    "catalog_root": "/tmp",
                    "catalog_files": [],
                }
                result = bootstrap_runtime(config, init_retrieval=True)

        self.assertEqual(result.launch_status, "probe_ok")
        self.assertEqual(result.model_alias, "test-model")
        self.assertEqual(result.server_url, "http://127.0.0.1:11434")
        self.assertIsNotNone(result.provider_config)
        self.assertTrue(result.health_evidence["model_ready"])

    def test_bootstrap_runtime_openrouter_backend_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        test_client = httpx.Client(transport=httpx.MockTransport(handler))

        config = self._app_config("openrouter", 8080)
        with mock.patch("grc_agent.model_manager.httpx.Client", return_value=test_client):
            with mock.patch("grc_agent.startup.initialize_retrieval") as mock_init_retrieval:
                mock_init_retrieval.return_value = {
                    "ok": True,
                    "catalog_root": "/tmp",
                    "catalog_files": [],
                }
                result = bootstrap_runtime(config, init_retrieval=True)

        self.assertEqual(result.launch_status, "probe_failed")
        self.assertEqual(len(result.errors), 1)
        self.assertIn("Failed to reach openrouter server", result.errors[0])

    def test_bootstrap_runtime_connection_refused_maps_to_backend_unreachable(self) -> None:
        """When the probe cannot reach the backend, the result must carry
        a stable ``error_type=BACKEND_UNREACHABLE`` and a platform-agnostic
        hint the GUI can render directly in the chat view."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        test_client = httpx.Client(transport=httpx.MockTransport(handler))

        from grc_agent.domain_models import ErrorCode

        config = self._app_config("ollama", 11434)
        with mock.patch("grc_agent.model_manager.httpx.Client", return_value=test_client):
            with mock.patch("grc_agent.startup.initialize_retrieval") as mock_init_retrieval:
                mock_init_retrieval.return_value = {
                    "ok": True,
                    "catalog_root": "/tmp",
                    "catalog_files": [],
                }
                result = bootstrap_runtime(config, init_retrieval=True)

        self.assertEqual(result.launch_status, "probe_failed")
        self.assertEqual(result.error_type, ErrorCode.BACKEND_UNREACHABLE)
        # Fallback provider_config must still be populated so the GUI can
        # build a worker without crashing; the error surfaces on first turn.
        self.assertIsNotNone(result.provider_config)
        # The hint must be platform-agnostic — no "systemctl", no "service",
        # no Linux-specific text that would mislead macOS/Windows users.
        joined = " ".join(result.errors).lower()
        self.assertNotIn("systemctl", joined)
        self.assertNotIn("service", joined)
        self.assertIn("ollama", joined)
        self.assertIn("http://127.0.0.1:11434", joined)
        # Concrete, known-good context-size recommendation — matches this
        # app's own default model's baked-in num_ctx, not a vague "set it
        # higher".
        self.assertIn("ollama_context_length", joined)
        self.assertIn("120000", joined)
        # Must not tell the user to manually launch a second `ollama serve`
        # — Ollama is typically already running in the background (this is
        # exactly the anti-pattern that caused a port conflict in practice).
        # Word-boundary match: "...the Ollama server..." is fine, "ollama
        # serve" as a standalone command is not.
        self.assertIsNone(re.search(r"\bollama serve\b", joined))

    def test_bootstrap_runtime_openrouter_connection_refused_has_no_ollama_hint(self) -> None:
        """OpenRouter has no local server to start — the Ollama-specific
        hint (OLLAMA_CONTEXT_LENGTH) must not appear."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        test_client = httpx.Client(transport=httpx.MockTransport(handler))

        config = self._app_config("openrouter", 8080)
        with mock.patch("grc_agent.model_manager.httpx.Client", return_value=test_client):
            with mock.patch("grc_agent.startup.initialize_retrieval") as mock_init_retrieval:
                mock_init_retrieval.return_value = {
                    "ok": True,
                    "catalog_root": "/tmp",
                    "catalog_files": [],
                }
                result = bootstrap_runtime(config, init_retrieval=True)

        joined = " ".join(result.errors).lower()
        self.assertNotIn("ollama serve", joined)
        self.assertNotIn("ollama_context_length", joined)


if __name__ == "__main__":
    unittest.main()
