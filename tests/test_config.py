"""Tests for runtime config resolution and packaged defaults."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from grc_agent.config import (
    CONFIG_ENV_VAR,
    ConfigError,
    default_app_config,
    default_config_path,
    load_app_config,
    resolve_config_path,
    user_config_path,
)


class RuntimeConfigTests(unittest.TestCase):
    """Check config resolution and packaged CLI defaults."""

    def test_repo_llama_defaults_match_expected_values(self) -> None:
        config = load_app_config()

        self.assertTrue(default_config_path().is_file())
        self.assertEqual(config.llama.server_url, "http://localhost:11434")
        self.assertEqual(config.llama.backend, "ollama")
        # The repo config carries a non-empty ``model``: an empty model
        # silently degrades every LLM call (chat completion, RAG synthesis)
        # to a backend 400. The GUI/CLI provider-picker can override at
        # runtime, but the parsed config must always carry a usable model.
        self.assertEqual(config.llama.model, "gemma4:e4b-it-qat-120k")
        self.assertEqual(config.llama.max_tokens, 4096)
        # The default per-payload truncation cap is 4000 chars —
        # large enough to fit a full GNU Radio catalog JSON object
        # for ``query_knowledge``, small enough that even ten
        # such payloads still fit inside the 100K-char history
        # budget and the 256K-token context window.
        self.assertEqual(config.agent.max_tool_result_chars, 4000)
        self.assertEqual(config.agent.history_compact_budget, 100000)
        self.assertEqual(config.llama.max_tool_rounds, 8)
        self.assertFalse(config.llama.enable_thinking)
        self.assertEqual(config.llama.request_timeout_seconds, 120.0)
        self.assertEqual(config.agent.retrieval.search_blocks_default_k, 5)
        self.assertEqual(config.agent.history.checkpoint_retention, 100)
        self.assertEqual(config.agent.guardrails.max_validation_stderr_chars, 1200)
        self.assertEqual(config.agent.guardrails.max_compact_list_items, 3)

    def test_load_app_config_falls_back_to_builtin_defaults_when_no_file_exists(
        self,
    ) -> None:
        expected = default_app_config()

        with mock.patch("grc_agent.config.resolve_config_path", return_value=None):
            config = load_app_config()

        self.assertEqual(config, expected)

    def test_load_app_config_reads_explicit_override_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "custom.toml"
            config_path.write_text(
                (
                    "[llama]\n"
                    'server_url = "http://127.0.0.1:9000"\n'
                    'model = "custom-model"\n'
                    'backend = "openrouter"\n'
                    "max_tokens = 2048\n"
                    "max_tool_rounds = 42\n"
                    "enable_thinking = true\n"
                    "request_timeout_seconds = 30.0\n"
                    "\n[agent]\n"
                    "history_compact_budget = 5000\n"
                    "max_tool_result_chars = 8000\n"
                    "\n[agent.retrieval]\n"
                    "search_blocks_default_k = 7\n"
                    "\n[agent.history]\n"
                    "checkpoint_retention = 140\n"
                ),
                encoding="utf-8",
            )

            config = load_app_config(config_path)

        self.assertEqual(config.llama.server_url, "http://127.0.0.1:9000")
        self.assertEqual(config.llama.model, "custom-model")
        self.assertEqual(config.llama.backend, "openrouter")
        self.assertTrue(config.llama.enable_thinking)
        self.assertEqual(config.llama.max_tool_rounds, 42)
        self.assertEqual(config.agent.max_tool_result_chars, 8000)
        self.assertEqual(config.agent.retrieval.search_blocks_default_k, 7)
        self.assertEqual(config.agent.history.checkpoint_retention, 140)

    def test_load_app_config_rejects_cross_field_invalid_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "invalid.toml"
            config_path.write_text(
                (
                    "[llama]\n"
                    "server_url='http://localhost:11434'\n"
                    "model='m'\n"
                    "max_tokens=1024\n"
                    "max_tool_rounds=1\n"
                    "enable_thinking=false\n"
                    "request_timeout_seconds=1.0\n"
                    "\n[agent]\n"
                    "history_compact_budget=1000\n"
                    "\n[agent.retrieval]\n"
                    "ask_grc_docs_default_k=10\n"
                    "ask_grc_docs_max_k=3\n"
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ConfigError, "ask_grc_docs_default_k must be <= ask_grc_docs_max_k"
            ):
                load_app_config(config_path)

    def test_load_app_config_rejects_missing_model(self) -> None:
        """A config file without [llama].model must be a hard error.

        An empty model silently degrades every LLM call (chat completion,
        RAG synthesis) to a backend 400 — exactly the S2 audit finding.
        The GUI/CLI provider-picker can override at runtime, but a parsed
        config must always carry a non-empty model.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "no_model.toml"
            config_path.write_text(
                (
                    "[llama]\n"
                    'server_url = "http://localhost:11434"\n'
                    'backend = "ollama"\n'
                    "max_tokens = 1024\n"
                    "max_tool_rounds = 1\n"
                    "enable_thinking = false\n"
                    "request_timeout_seconds = 1.0\n"
                    "\n[agent]\n"
                    "history_compact_budget = 1000\n"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "model"):
                load_app_config(config_path)

    def test_resolve_config_path_prefers_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_config = Path(tmpdir) / "env.toml"
            env_config.write_text(
                "[llama]\nserver_url='http://localhost:11434'\nmodel='m'\nmax_tokens=1\nmax_tool_rounds=1\nenable_thinking=false\nrequest_timeout_seconds=1.0\n",
                encoding="utf-8",
            )

            with mock.patch.dict("os.environ", {CONFIG_ENV_VAR: str(env_config)}, clear=False):
                with mock.patch(
                    "grc_agent.config.default_config_path",
                    return_value=Path(tmpdir) / "missing-repo.toml",
                ):
                    with mock.patch(
                        "grc_agent.config.user_config_path",
                        return_value=Path(tmpdir) / "missing-user.toml",
                    ):
                        resolved = resolve_config_path()

        self.assertEqual(resolved, env_config)

    def test_user_config_path_uses_xdg_style_location(self) -> None:
        path = user_config_path()
        self.assertEqual(path.name, "config.toml")
        self.assertEqual(path.parent.name, "grc_agent")
        self.assertIn(".config", str(path.parent.parent))

    def test_load_app_config_accepts_and_validates_backends(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            for val in ("ollama", "openrouter"):
                config_path = Path(tmpdir) / f"{val}.toml"
                config_path.write_text(
                    (
                        "[llama]\n"
                        'server_url = "http://localhost:11434"\n'
                        f'backend = "{val}"\n'
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
                config = load_app_config(config_path)
                self.assertEqual(config.llama.backend, val)

            invalid_path = Path(tmpdir) / "invalid_backend.toml"
            invalid_path.write_text(
                (
                    "[llama]\n"
                    'server_url = "http://localhost:11434"\n'
                    'backend = "invalid_val"\n'
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
            with self.assertRaisesRegex(ConfigError, "backend must be"):
                load_app_config(invalid_path)
