"""Tests for Ollama model discovery, tool-support probing, and pulling."""

import unittest
from unittest import mock

import httpx
from grc_agent.model_manager import (
    OllamaBackendStatus,
    check_ollama_tool_support,
    discover_ollama_models,
    probe_ollama_backend,
    pull_ollama_model,
)


class DiscoverOllamaModelsTests(unittest.TestCase):

    def _client(self, handler) -> httpx.Client:
        """Real httpx.Client with a MockTransport — exercises the real
        connection-pool / timeout / context-manager lifecycle."""
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_empty_when_server_unreachable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("conn refused")
        with self._client(handler) as client:
            result = discover_ollama_models(
                "http://localhost:11434", client=client
            )
        self.assertEqual(result, [])

    def test_returns_model_names_from_valid_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "models": [
                    {"name": "qwen3.5:9b-q4_K_M"},
                    {"name": "llama3.2:latest"},
                ]
            })
        with self._client(handler) as client:
            result = discover_ollama_models(
                "http://localhost:11434", client=client
            )
        self.assertEqual(result, ["qwen3.5:9b-q4_K_M", "llama3.2:latest"])

    def test_skips_entries_without_name(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "models": [
                    {"name": "good-model"},
                    {"not_name": "bad"},
                ]
            })
        with self._client(handler) as client:
            result = discover_ollama_models(
                "http://localhost:11434", client=client
            )
        self.assertEqual(result, ["good-model"])

    def test_returns_empty_on_non_dict_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json="not a dict")
        with self._client(handler) as client:
            result = discover_ollama_models(
                "http://localhost:11434", client=client
            )
        self.assertEqual(result, [])


class CheckOllamaToolSupportTests(unittest.TestCase):

    def _client(self, handler) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_returns_true_when_has_requires_field(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "template": "{{ .Prompt }}",
                "requires": "0.17.1",
            })
        with self._client(handler) as client:
            result = check_ollama_tool_support(
                "http://localhost:11434", "test-model", client=client,
            )
        self.assertTrue(result)

    def test_returns_false_when_missing_requires_field(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "template": "{{ .Prompt }}",
            })
        with self._client(handler) as client:
            result = check_ollama_tool_support(
                "http://localhost:11434", "test-model", client=client,
            )
        self.assertFalse(result)

    def test_returns_none_when_probe_fails(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("conn refused")
        with self._client(handler) as client:
            result = check_ollama_tool_support(
                "http://localhost:11434", "test-model", client=client,
            )
        self.assertIsNone(result)

    def test_logs_warning_when_probe_fails(self) -> None:
        """A failed probe must be visible at WARNING, not silently DEBUG.

        The startup bootstrap path calls this function to surface tool-call
        compatibility to the user; if the Ollama daemon is down, the user
        must see a WARNING so the GUI's degraded mode is explainable.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("conn refused")
        with self._client(handler) as client:
            with self.assertLogs("grc_agent.model_manager", level="WARNING") as cm:
                check_ollama_tool_support(
                    "http://localhost:11434", "test-model", client=client,
                )
        joined = "\n".join(cm.output)
        self.assertIn("WARNING", joined)
        self.assertIn("test-model", joined)
        self.assertIn("http://localhost:11434", joined)


class PullOllamaModelTests(unittest.TestCase):

    def test_returns_ok_on_success(self) -> None:
        fake_proc = mock.Mock()
        fake_proc.returncode = 0
        fake_proc.stdout = ""
        fake_proc.stderr = ""
        with mock.patch("subprocess.run", return_value=fake_proc):
            result = pull_ollama_model("qwen3.5:9b-q4_K_M")
        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "qwen3.5:9b-q4_K_M")

    def test_returns_error_on_nonzero_exit(self) -> None:
        fake_proc = mock.Mock()
        fake_proc.returncode = 1
        fake_proc.stderr = "pull failed"
        fake_proc.stdout = ""
        with mock.patch("subprocess.run", return_value=fake_proc):
            result = pull_ollama_model("bad-model")
        self.assertFalse(result["ok"])
        self.assertIn("pull failed", result["error"])

    def test_returns_error_when_ollama_not_found(self) -> None:
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            result = pull_ollama_model("any-model")
        self.assertFalse(result["ok"])
        self.assertIn("not found on PATH", result["error"])


class ProbeOllamaBackendTests(unittest.TestCase):
    """Unit tests for the detect-only :func:`probe_ollama_backend` helper.

    The helper is shared by the GUI setup widget and the CLI startup
    path, so its outputs must be deterministic and platform-agnostic.
    """

    def _client(self, handler) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_server_reachable_and_model_available(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "models": [
                    {"name": "qwen3.5:9b-q4_K_M"},
                    {"name": "llama3.2:latest"},
                ]
            })
        with self._client(handler) as client:
            status = probe_ollama_backend(
                "http://127.0.0.1:11434",
                "qwen3.5:9b-q4_K_M",
                client=client,
            )
        self.assertIsInstance(status, OllamaBackendStatus)
        self.assertTrue(status.server_reachable)
        self.assertTrue(status.model_available)
        self.assertEqual(status.model_alias, "qwen3.5:9b-q4_K_M")
        self.assertEqual(
            status.available_models,
            ["qwen3.5:9b-q4_K_M", "llama3.2:latest"],
        )
        self.assertEqual(status.pull_command, "ollama pull qwen3.5:9b-q4_K_M")
        self.assertIn("ready", status.hint)

    def test_server_reachable_but_model_missing_uses_generic_hint(self) -> None:
        """The 404-in-the-log case: the alias is not on the server.
        The probe must surface the *generic* ``ollama pull`` reminder
        (the user owns the model choice) and mark the alias as not
        available. The CLI uses this flag to fall back to the first
        installed tag instead of 404-ing on the first chat turn."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "models": [
                    {"name": "qwen3.6:27b-mtp-q4_K_M"},
                ]
            })
        with self._client(handler) as client:
            status = probe_ollama_backend(
                "http://127.0.0.1:11434",
                "qwen3.5:9b-q4_K_M",
                client=client,
            )
        self.assertTrue(status.server_reachable)
        self.assertFalse(status.model_available)
        self.assertEqual(status.model_alias, "qwen3.5:9b-q4_K_M")
        # The generic pull reminder must be present, exactly as the
        # GUI's "Models on this machine" hint label renders.
        self.assertIn("Use `ollama pull <model_name>`", status.hint)
        self.assertIn("ollama pull qwen3.5:9b-q4_K_M", status.hint)
        # The configured model is reported as missing so callers
        # know to fall back.
        self.assertIn("qwen3.5:9b-q4_K_M", status.hint)

    def test_server_unreachable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")
        with self._client(handler) as client:
            status = probe_ollama_backend(
                "http://127.0.0.1:11434",
                "qwen3.5:9b-q4_K_M",
                client=client,
            )
        self.assertFalse(status.server_reachable)
        self.assertFalse(status.model_available)
        self.assertEqual(status.available_models, [])
        self.assertIn("not reachable", status.hint)
        self.assertIn("ollama serve", status.hint)
        self.assertIn("ollama pull qwen3.5:9b-q4_K_M", status.hint)

    def test_partial_tag_match_via_latest_suffix(self) -> None:
        """``model_name_matches`` tolerates ``:latest`` on either side.
        The probe must inherit that behavior so ``llama3.2`` matches
        ``llama3.2:latest`` and vice versa."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "models": [{"name": "llama3.2:latest"}]
            })
        with self._client(handler) as client:
            status = probe_ollama_backend(
                "http://127.0.0.1:11434",
                "llama3.2",
                client=client,
            )
        self.assertTrue(status.server_reachable)
        self.assertTrue(status.model_available)

    def test_empty_model_alias_does_not_crash(self) -> None:
        """Defensive: a blank ``model_alias`` (the new default — there
        is no 'configured model' assumption) must not crash and must
        still report the generic pull hint."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"models": [{"name": "llama3.2:latest"}]})
        with self._client(handler) as client:
            status = probe_ollama_backend(
                "http://127.0.0.1:11434",
                "",
                client=client,
            )
        self.assertTrue(status.server_reachable)
        self.assertFalse(status.model_available)
        self.assertIn("Use `ollama pull <model_name>`", status.hint)
        # The pull command falls back to a placeholder when no
        # alias is supplied, so the user can copy-paste it.
        self.assertEqual(status.pull_command, "ollama pull <model_name>")
