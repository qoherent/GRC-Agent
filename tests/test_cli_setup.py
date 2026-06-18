"""Tests for the streamlined provider-picker helper.

The CLI is gone; ``run_cli_setup`` remains as a library function (currently
a stub that returns ``True`` without prompting). These tests pin that
contract.
"""

from __future__ import annotations

import unittest
from unittest import mock

from grc_agent.config import (
    AppConfig,
    LlamaConfig,
    run_cli_setup,
)


def _config() -> AppConfig:
    return AppConfig(
        llama=LlamaConfig(
            server_url="http://127.0.0.1:11434",
            backend="ollama",
            model="qwen3.5:9b-q4_K_M",
        ),
        agent=mock.MagicMock(),
    )


class RunCliSetupTests(unittest.TestCase):

    def test_run_cli_setup_always_returns_true_without_prompting(self) -> None:
        with mock.patch("builtins.input") as input_mock:
            self.assertTrue(run_cli_setup(config=_config(), is_tty=True))
            self.assertTrue(run_cli_setup(config=_config(), is_tty=False))
        input_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
