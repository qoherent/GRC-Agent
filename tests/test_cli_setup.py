"""Tests for the streamlined ``grc_agent.cli_setup`` provider picker."""

from __future__ import annotations

import unittest
from unittest import mock

from grc_agent.cli_setup import (
    PROVIDER_OLLAMA,
    PROVIDER_OPENROUTER,
    run_cli_setup,
)
from grc_agent.config import AppConfig, LlamaConfig


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

    def test_already_chosen_returns_true_without_prompting(self) -> None:
        with mock.patch(
            "grc_agent.preferences.load_user_preferences",
            return_value=mock.Mock(provider_chosen="ollama"),
        ):
            with mock.patch("builtins.input") as input_mock:
                self.assertTrue(run_cli_setup(config=_config(), is_tty=True))
        input_mock.assert_not_called()

    def test_non_tty_skips_prompt(self) -> None:
        with mock.patch(
            "grc_agent.preferences.load_user_preferences",
            return_value=mock.Mock(provider_chosen=""),
        ):
            with mock.patch("builtins.input") as input_mock:
                self.assertTrue(run_cli_setup(config=_config(), is_tty=False))
        input_mock.assert_not_called()

    def test_user_quits_returns_false(self) -> None:
        with mock.patch(
            "grc_agent.preferences.load_user_preferences",
            return_value=mock.Mock(provider_chosen=""),
        ):
            with mock.patch("builtins.input", return_value="q"):
                self.assertFalse(run_cli_setup(config=_config(), is_tty=True))

    def test_user_picks_ollama_persists_choice(self) -> None:
        with mock.patch(
            "grc_agent.preferences.load_user_preferences",
            return_value=mock.Mock(provider_chosen=""),
        ):
            with mock.patch(
                "grc_agent.preferences.update_provider_chosen"
            ) as update_mock:
                with mock.patch("builtins.input", return_value="1"):
                    self.assertTrue(
                        run_cli_setup(config=_config(), is_tty=True)
                    )
        update_mock.assert_called_once_with(provider=PROVIDER_OLLAMA)

    def test_user_picks_openrouter_persists_choice(self) -> None:
        with mock.patch(
            "grc_agent.preferences.load_user_preferences",
            return_value=mock.Mock(provider_chosen=""),
        ):
            with mock.patch(
                "grc_agent.preferences.update_provider_chosen"
            ) as update_mock:
                with mock.patch("builtins.input", return_value="2"):
                    self.assertTrue(
                        run_cli_setup(config=_config(), is_tty=True)
                    )
        update_mock.assert_called_once_with(provider=PROVIDER_OPENROUTER)

    def test_persist_failure_does_not_abort(self) -> None:
        with mock.patch(
            "grc_agent.preferences.load_user_preferences",
            return_value=mock.Mock(provider_chosen=""),
        ):
            with mock.patch(
                "grc_agent.preferences.update_provider_chosen",
                side_effect=OSError("disk full"),
            ):
                with mock.patch("builtins.input", return_value="1"):
                    # The CLI must continue even if the prefs write
                    # fails; the user is not locked out.
                    self.assertTrue(
                        run_cli_setup(config=_config(), is_tty=True)
                    )


if __name__ == "__main__":
    unittest.main()
