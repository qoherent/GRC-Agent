"""Tests for runtime config resolution and packaged CLI defaults."""

import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from grc_agent.cli import _build_parser, _maybe_translate_legacy_args
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
        # The repo config no longer carries a hardcoded ``model``
        # entry — the user is expected to pick from the installed
        # tags at runtime, so the default resolves to the empty
        # string. The CLI falls back to the first installed tag
        # when this is empty.
        self.assertEqual(config.llama.model, "")
        self.assertEqual(config.llama.max_tokens, 4096)
        # The default per-payload truncation cap is 4000 chars —
        # large enough to fit a full GNU Radio catalog JSON object
        # for ``query_knowledge``, small enough that even ten
        # such payloads still fit inside the 100K-char history
        # budget and the 256K-token context window.
        self.assertEqual(config.agent.max_tool_result_chars, 4000)
        self.assertEqual(config.agent.history_compact_budget, 100000)
        self.assertEqual(config.llama.max_tool_rounds, 8)
        self.assertEqual(config.llama.temperature, 0.0)
        self.assertFalse(config.llama.enable_thinking)
        self.assertEqual(config.llama.request_timeout_seconds, 120.0)
        self.assertEqual(config.agent.docs_answer.answer_cache_size, 64)
        self.assertEqual(config.agent.retrieval.search_blocks_default_k, 5)
        self.assertEqual(config.agent.history.checkpoint_retention, 100)
        self.assertEqual(config.agent.guardrails.max_validation_stderr_chars, 1200)
        self.assertEqual(config.agent.guardrails.max_compact_list_items, 3)

    def test_cli_parser_defaults_come_from_repo_config(self) -> None:
        config = load_app_config()
        parser = _build_parser(config)

        args = parser.parse_args(
            ["chat", "fixture.grc", "--message", "Summarize the graph."]
        )

        self.assertEqual(args.model, config.llama.model)
        self.assertFalse(args.agentic)
        self.assertIsNone(args.max_tool_rounds)

    def test_cli_parser_accepts_agentic_tool_budget_override(self) -> None:
        config = load_app_config()
        parser = _build_parser(config)

        args = parser.parse_args(
            [
                "chat",
                "fixture.grc",
                "--agentic",
                "--max-tool-rounds",
                "24",
            ]
        )

        self.assertTrue(args.agentic)
        self.assertEqual(args.max_tool_rounds, 24)

    def test_legacy_message_translation_preserves_global_options(self) -> None:
        translated = _maybe_translate_legacy_args(
            ["--verbose", "--message", "Summarize it.", "fixture.grc"]
        )

        self.assertEqual(
            translated,
            ["--verbose", "chat", "--message", "Summarize it.", "fixture.grc"],
        )

    def test_legacy_message_translation_does_not_rewrite_modern_chat(self) -> None:
        argv = [
            "--verbose",
            "chat",
            "--agentic",
            "--message",
            "Summarize it.",
            "fixture.grc",
        ]

        self.assertEqual(_maybe_translate_legacy_args(argv), argv)

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
                    "temperature = 0.2\n"
                    "enable_thinking = true\n"
                    "request_timeout_seconds = 30.0\n"
                    "\n[agent]\n"
                    "history_compact_budget = 5000\n"
                    "max_tool_result_chars = 8000\n"
                    "\n[agent.docs_answer]\n"
                    "max_sources = 5\n"
                    "answer_cache_size = 32\n"
                    "answer_target_chars = 250\n"
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
        self.assertEqual(config.agent.docs_answer.max_sources, 5)
        self.assertEqual(config.agent.docs_answer.answer_cache_size, 32)
        self.assertEqual(config.agent.docs_answer.answer_target_chars, 250)
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
                    "temperature=0.0\n"
                    "enable_thinking=false\n"
                    "request_timeout_seconds=1.0\n"
                    "\n[agent]\n"
                    "history_compact_budget=1000\n"
                    "\n[agent.docs_answer]\n"
                    "max_sources=10\n"
                    "\n[agent.retrieval]\n"
                    "ask_grc_docs_max_k=3\n"
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ConfigError, "max_sources must be <= .*ask_grc_docs_max_k"
            ):
                load_app_config(config_path)

    def test_resolve_config_path_prefers_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_config = Path(tmpdir) / "env.toml"
            env_config.write_text(
                "[llama]\nserver_url='http://localhost:11434'\nmodel='m'\nmax_tokens=1\nmax_tool_rounds=1\ntemperature=0.0\nenable_thinking=false\nrequest_timeout_seconds=1.0\n",
                encoding="utf-8",
            )

            with mock.patch.dict(
                "os.environ", {CONFIG_ENV_VAR: str(env_config)}, clear=False
            ):
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
                        "temperature = 0.0\n"
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
                    "temperature = 0.0\n"
                    "enable_thinking = false\n"
                    "request_timeout_seconds = 1.0\n"
                    "\n[agent]\n"
                    "history_compact_budget = 1000\n"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ConfigError, "backend must be"
            ):
                load_app_config(invalid_path)

    def test_pyproject_declares_console_script_entrypoint(self) -> None:
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

        self.assertEqual(
            payload["project"]["scripts"]["grc-agent"],
            "grc_agent.cli:main",
        )
