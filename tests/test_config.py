"""Tests for the repo-backed runtime config."""

import unittest

from grc_agent.cli import _build_parser
from grc_agent.config import default_config_path, load_app_config


class RuntimeConfigTests(unittest.TestCase):
    """Check that the CLI defaults come from the repo config file."""

    def test_repo_llama_defaults_match_expected_values(self) -> None:
        config = load_app_config()

        self.assertTrue(default_config_path().is_file())
        self.assertEqual(config.llama.server_url, "http://127.0.0.1:8080")
        self.assertEqual(config.llama.model, "unsloth/gemma-4-E2B-it-GGUF")
        self.assertEqual(config.llama.max_steps, 2)
        self.assertEqual(config.llama.max_tokens, 12000)
        self.assertEqual(config.llama.temperature, 0.0)
        self.assertFalse(config.llama.enable_thinking)
        self.assertEqual(config.llama.request_timeout_seconds, 60.0)

    def test_cli_parser_defaults_come_from_repo_config(self) -> None:
        config = load_app_config()
        parser = _build_parser(config)

        args = parser.parse_args(["fixture.grc", "--message", "Summarize the graph."])

        self.assertEqual(args.llama_server_url, config.llama.server_url)
        self.assertEqual(args.model, config.llama.model)
        self.assertEqual(args.max_steps, config.llama.max_steps)
