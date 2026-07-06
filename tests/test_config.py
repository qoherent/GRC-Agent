"""Tests for runtime config resolution and packaged defaults."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from grc_agent.config import (
    CONFIG_ENV_VAR,
    ConfigError,
    default_app_config,
    default_chat_model,
    default_config_path,
    default_embedding_model,
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
        # Model names are sourced from .env (keyed by backend), not from
        # grc_agent.toml. For the ollama backend they must equal the env
        # resolver output and never be empty (an empty model silently
        # degrades every LLM/embedding call to a backend 400).
        self.assertEqual(config.llama.model, default_chat_model("ollama"))
        self.assertEqual(config.llama.embedding_model, default_embedding_model("ollama"))
        self.assertTrue(config.llama.model)
        self.assertTrue(config.llama.embedding_model)
        self.assertEqual(config.llama.max_tokens, 4096)
        self.assertEqual(config.llama.max_tool_rounds, 8)
        self.assertEqual(config.llama.request_timeout_seconds, 120.0)
        self.assertEqual(config.agent.retrieval.search_blocks_default_k, 5)
        self.assertEqual(config.agent.history.checkpoint_retention, 100)
        self.assertEqual(config.agent.guardrails.max_inspect_targets, 8)

    def test_load_app_config_falls_back_to_builtin_defaults_when_no_file_exists(
        self,
    ) -> None:
        expected = default_app_config()

        with mock.patch("grc_agent.config.resolve_config_path", return_value=None):
            config = load_app_config()

        self.assertEqual(config, expected)

    def test_load_app_config_reads_explicit_override_file(self) -> None:
        # Model names are env-sourced (not toml-sourced). The toml still drives
        # backend / server_url / agent fields; the chat + embedding models for
        # the openrouter backend come from the env vars set below.
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "custom.toml"
            config_path.write_text(
                (
                    "[llama]\n"
                    'server_url = "http://127.0.0.1:9000"\n'
                    'backend = "openrouter"\n'
                    "max_tokens = 2048\n"
                    "max_tool_rounds = 42\n"
                    "request_timeout_seconds = 30.0\n"
                    "\n[agent.retrieval]\n"
                    "search_blocks_default_k = 7\n"
                    "\n[agent.history]\n"
                    "checkpoint_retention = 140\n"
                ),
                encoding="utf-8",
            )

            with mock.patch.dict(
                "os.environ",
                {"OPENROUTER_MODEL": "custom-model", "OPENROUTER_EMBEDDING_MODEL": "custom-embed"},
            ):
                config = load_app_config(config_path)

        self.assertEqual(config.llama.server_url, "http://127.0.0.1:9000")
        self.assertEqual(config.llama.model, "custom-model")
        self.assertEqual(config.llama.embedding_model, "custom-embed")
        self.assertEqual(config.llama.backend, "openrouter")
        self.assertEqual(config.llama.max_tool_rounds, 42)
        self.assertEqual(config.agent.retrieval.search_blocks_default_k, 7)
        self.assertEqual(config.agent.history.checkpoint_retention, 140)

    def test_model_always_resolved_even_when_toml_omits_model_keys(self) -> None:
        """A config file without model/embedding_model keys still resolves them
        from .env (keyed by backend). A parsed config always carries a usable,
        non-empty model — there is no longer a 'missing model' error because
        model names no longer live in grc_agent.toml.
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
                    "request_timeout_seconds = 1.0\n"
                ),
                encoding="utf-8",
            )
            config = load_app_config(config_path)

        self.assertEqual(config.llama.backend, "ollama")
        self.assertTrue(config.llama.model)
        self.assertTrue(config.llama.embedding_model)

    def test_resolve_config_path_prefers_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_config = Path(tmpdir) / "env.toml"
            env_config.write_text(
                "[llama]\nserver_url='http://localhost:11434'\nmodel='m'\nmax_tokens=1\nmax_tool_rounds=1\nrequest_timeout_seconds=1.0\n",
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
                        "request_timeout_seconds = 1.0\n"
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
                    "request_timeout_seconds = 1.0\n"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ConfigError, "backend must be"):
                load_app_config(invalid_path)
