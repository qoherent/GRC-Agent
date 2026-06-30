"""Tests for doctor dynamic backends checks."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from grc_agent.doctor import _check_llama_server


class DoctorBackendTests(unittest.TestCase):
    @mock.patch("grc_agent.startup.bootstrap_runtime")
    def test_doctor_dynamic_names_and_success(self, mock_bootstrap: mock.MagicMock) -> None:
        mock_evidence = {
            "actual_context_tokens": 120000,
        }
        mock_bootstrap.return_value.health_evidence = mock_evidence
        mock_bootstrap.return_value.model_alias = "test-model"
        mock_bootstrap.return_value.server_url = "http://127.0.0.1:8080"
        mock_bootstrap.return_value.launch_status = "probe_ok"

        backends = {
            "ollama": "Ollama server",
            "openrouter": "OpenRouter API",
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            for backend, expected_name in backends.items():
                config_path = Path(tmpdir) / f"{backend}.toml"
                config_path.write_text(
                    (
                        "[llama]\n"
                        'server_url = "http://127.0.0.1:8080"\n'
                        f'backend = "{backend}"\n'
                        'model = "m"\n'
                        "max_tokens = 1024\n"
                        "max_tool_rounds = 1\n"
                        "enable_thinking = false\n"
                        "request_timeout_seconds = 1.0\n"
                        "\n[agent]\n"
                        "history_compact_budget = 1000\n"
                    ),
                    encoding="utf-8",
                )

                check = _check_llama_server(str(config_path))
                self.assertEqual(check["name"], expected_name)
                self.assertTrue(check["ok"])
                self.assertIn("test-model at http://127.0.0.1:8080", check["detail"])

    @mock.patch("grc_agent.startup.bootstrap_runtime")
    def test_doctor_failure_messages(self, mock_bootstrap: mock.MagicMock) -> None:
        mock_bootstrap.side_effect = Exception("Connection refused")

        backends = {
            "ollama": ("Ollama server", "configured model pulled"),
            "openrouter": ("OpenRouter API", "valid OpenRouter API key"),
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            for backend, (expected_name, expected_help) in backends.items():
                config_path = Path(tmpdir) / f"{backend}_fail.toml"
                config_path.write_text(
                    (
                        "[llama]\n"
                        'server_url = "http://127.0.0.1:8080"\n'
                        f'backend = "{backend}"\n'
                        'model = "m"\n'
                        "max_tokens = 1024\n"
                        "max_tool_rounds = 1\n"
                        "enable_thinking = false\n"
                        "request_timeout_seconds = 1.0\n"
                        "\n[agent]\n"
                        "history_compact_budget = 1000\n"
                    ),
                    encoding="utf-8",
                )

                check = _check_llama_server(str(config_path))
                self.assertEqual(check["name"], expected_name)
                self.assertFalse(check["ok"])
                self.assertIn("Connection refused", check["detail"])
                self.assertIn(expected_help, check["detail"])


if __name__ == "__main__":
    unittest.main()
