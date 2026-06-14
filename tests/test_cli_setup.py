"""Tests for the streamlined ``grc_agent.cli_setup`` provider picker."""

from __future__ import annotations

import io
import unittest
from unittest import mock

from grc_agent.config import (
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
            "grc_agent.config.load_user_preferences",
            return_value=mock.Mock(provider_chosen="ollama"),
        ):
            with mock.patch("builtins.input") as input_mock:
                self.assertTrue(run_cli_setup(config=_config(), is_tty=True))
        input_mock.assert_not_called()

    def test_non_tty_skips_prompt(self) -> None:
        with mock.patch(
            "grc_agent.config.load_user_preferences",
            return_value=mock.Mock(provider_chosen=""),
        ):
            with mock.patch("builtins.input") as input_mock:
                self.assertTrue(run_cli_setup(config=_config(), is_tty=False))
        input_mock.assert_not_called()

    def test_user_quits_returns_false(self) -> None:
        with mock.patch(
            "grc_agent.config.load_user_preferences",
            return_value=mock.Mock(provider_chosen=""),
        ):
            with mock.patch("builtins.input", return_value="q"):
                self.assertFalse(run_cli_setup(config=_config(), is_tty=True))

    def test_user_picks_ollama_persists_choice(self) -> None:
        with mock.patch(
            "grc_agent.config.load_user_preferences",
            return_value=mock.Mock(provider_chosen=""),
        ):
            with mock.patch(
                "grc_agent.config.update_provider_chosen"
            ) as update_mock:
                with mock.patch("builtins.input", return_value="1"):
                    self.assertTrue(
                        run_cli_setup(config=_config(), is_tty=True)
                    )
        update_mock.assert_called_once_with(provider=PROVIDER_OLLAMA)

    def test_user_picks_openrouter_persists_choice(self) -> None:
        with mock.patch(
            "grc_agent.config.load_user_preferences",
            return_value=mock.Mock(provider_chosen=""),
        ):
            with mock.patch(
                "grc_agent.config.update_provider_chosen"
            ) as update_mock:
                with mock.patch("builtins.input", return_value="2"):
                    self.assertTrue(
                        run_cli_setup(config=_config(), is_tty=True)
                    )
        update_mock.assert_called_once_with(provider=PROVIDER_OPENROUTER)

    def test_persist_failure_does_not_abort(self) -> None:
        with mock.patch(
            "grc_agent.config.load_user_preferences",
            return_value=mock.Mock(provider_chosen=""),
        ):
            with mock.patch(
                "grc_agent.config.update_provider_chosen",
                side_effect=OSError("disk full"),
            ):
                with mock.patch("builtins.input", return_value="1"):
                    # The CLI must continue even if the prefs write
                    # fails; the user is not locked out.
                    self.assertTrue(
                        run_cli_setup(config=_config(), is_tty=True)
                    )


class CliOllamaProbeTests(unittest.TestCase):
    """Verify the CLI chat path runs the shared detect-only Ollama probe.

    The probe result is rendered verbatim to stderr so the GUI and CLI
    show the same wording. No daemon management, no Popen, no
    auto-pull. These tests only assert the orchestration.
    """

    def _config(self) -> AppConfig:
        from grc_agent.config import (
            DEFAULT_DOCS_ANSWER_CONFIG,
            DEFAULT_GUARDRAILS_CONFIG,
            DEFAULT_HISTORY_CONFIG,
            DEFAULT_RETRIEVAL_CONFIG,
            AgentConfig,
        )
        return AppConfig(
            llama=LlamaConfig(
                server_url="http://127.0.0.1:11434",
                backend="ollama",
                model="qwen3.5:9b-q4_K_M",
            ),
            agent=AgentConfig(
                history_compact_budget=1000,
                docs_answer=DEFAULT_DOCS_ANSWER_CONFIG,
                retrieval=DEFAULT_RETRIEVAL_CONFIG,
                history=DEFAULT_HISTORY_CONFIG,
                guardrails=DEFAULT_GUARDRAILS_CONFIG,
            ),
        )

    def _capture(self, func, *args, **kwargs) -> tuple[int, str]:
        """Run ``func`` with stdout + stderr captured; return (exit, text)."""
        buf_out, buf_err = io.StringIO(), io.StringIO()
        real_out, real_err = __import__("sys").stdout, __import__("sys").stderr
        __import__("sys").stdout, __import__("sys").stderr = buf_out, buf_err
        try:
            exit_code = func(*args, **kwargs)
        finally:
            __import__("sys").stdout, __import__("sys").stderr = real_out, real_err
        return exit_code, buf_out.getvalue() + buf_err.getvalue()

    def test_chat_path_exits_one_when_server_unreachable(self) -> None:
        """When the Ollama probe says the server is down, the CLI
        prints the shared hint and exits 1 before loading the graph."""
        from grc_agent.model_manager import OllamaBackendStatus

        fake_status = OllamaBackendStatus(
            server_url="http://127.0.0.1:11434",
            server_reachable=False,
            model_alias="qwen3.5:9b-q4_K_M",
            model_available=False,
            available_models=[],
            start_command="ollama serve",
            pull_command="ollama pull qwen3.5:9b-q4_K_M",
            hint=(
                "Ollama server is not reachable at http://127.0.0.1:11434. "
                "Run the following in a new terminal, then retry:\n"
                "  ollama serve\n  ollama pull qwen3.5:9b-q4_K_M"
            ),
        )

        from grc_agent import cli, config as cfg_module, model_manager

        with mock.patch.object(cfg_module, "run_cli_setup", return_value=True):
            with mock.patch.object(model_manager, "probe_ollama_backend", return_value=fake_status):
                with mock.patch.object(__import__("sys").stdin, "isatty", return_value=False):
                    exit_code, captured = self._capture(
                        cli._run_llama_runtime,
                        file_path=None,
                        user_message=None,
                        config=self._config(),
                        server_url="http://127.0.0.1:11434",
                        model="qwen3.5:9b-q4_K_M",
                        api_key=None,
                    )
        self.assertEqual(exit_code, 1)
        self.assertIn("Ollama", captured)
        self.assertIn("not reachable", captured)
        self.assertIn("ollama serve", captured)
        self.assertIn("ollama pull qwen3.5:9b-q4_K_M", captured)
        # The shared hint must reach the user — no daemon management
        # language, no auto-pull language.
        self.assertNotIn("systemctl", captured)

    def test_chat_path_falls_back_to_first_installed_when_alias_missing(self) -> None:
        """The CLI no longer hard-fails when the requested model is
        missing. Instead it prints the generic pull reminder and
        silently falls back to the first installed tag, so the chat
        path can still run."""
        from grc_agent.model_manager import OllamaBackendStatus
        from grc_agent.startup import RuntimeBootstrapResult
        from grc_agent.toolagents_runtime import ToolAgentsLlamaProviderConfig

        fake_status = OllamaBackendStatus(
            server_url="http://127.0.0.1:11434",
            server_reachable=True,
            model_alias="qwen3.5:9b-q4_K_M",
            model_available=False,
            available_models=["qwen3.6:27b-mtp-q4_K_M"],
            start_command="ollama serve",
            pull_command="ollama pull qwen3.5:9b-q4_K_M",
            hint=(
                "Ollama is running at http://127.0.0.1:11434 · "
                "`qwen3.5:9b-q4_K_M` is not installed. "
                "Use `ollama pull <model_name>` to download any ollama "
                "model, e.g. `ollama pull qwen3.5:9b-q4_K_M`."
            ),
        )

        from grc_agent import cli, config as cfg_module, model_manager
        from grc_agent import startup as startup_module

        provider = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:11434",
            model="qwen3.6:27b-mtp-q4_K_M",
        )
        fake_result = RuntimeBootstrapResult()
        fake_result.provider_config = provider
        fake_result.launch_status = "probe_ok"
        fake_result.server_url = "http://127.0.0.1:11434"
        fake_result.model_alias = "qwen3.6:27b-mtp-q4_K_M"
        fake_result.retrieval_ok = True
        fake_result.catalog_root = "/tmp"

        bootstrap_mock = mock.MagicMock(return_value=fake_result)

        with mock.patch.object(cfg_module, "run_cli_setup", return_value=True):
            with mock.patch.object(model_manager, "probe_ollama_backend", return_value=fake_status):
                with mock.patch.object(startup_module, "bootstrap_runtime", bootstrap_mock):
                    with mock.patch.object(__import__("sys").stdin, "isatty", return_value=False):
                        with mock.patch("builtins.input", side_effect=["hi", "/quit"]):
                            with mock.patch.object(cli, "_run_single_turn", return_value=0):
                                self._capture(
                                    cli._run_llama_runtime,
                                    file_path=None,
                                    user_message=None,
                                    config=self._config(),
                                    server_url="http://127.0.0.1:11434",
                                    model="qwen3.5:9b-q4_K_M",
                                    api_key=None,
                                )
        # The CLI must have called bootstrap_runtime with the
        # first installed model — the configured-but-missing alias
        # must NOT have been forwarded to the runtime.
        bootstrap_mock.assert_called_once()
        kwargs = bootstrap_mock.call_args.kwargs
        self.assertEqual(
            kwargs["model_alias"], "qwen3.6:27b-mtp-q4_K_M",
            "CLI must fall back to the first installed model when "
            "the configured alias is missing on the server.",
        )

    def test_chat_path_picks_first_installed_when_no_alias(self) -> None:
        """The new default — no configured model — must fall back to
        the first installed tag the server reports."""
        from grc_agent.model_manager import OllamaBackendStatus
        from grc_agent.startup import RuntimeBootstrapResult
        from grc_agent.toolagents_runtime import ToolAgentsLlamaProviderConfig

        fake_status = OllamaBackendStatus(
            server_url="http://127.0.0.1:11434",
            server_reachable=True,
            model_alias="",
            model_available=False,
            available_models=["qwen3.6:27b-mtp-q4_K_M"],
            start_command="ollama serve",
            pull_command="ollama pull <model_name>",
            hint=(
                "Ollama is running at http://127.0.0.1:11434. "
                "Use `ollama pull <model_name>` to download any ollama "
                "model, e.g. `ollama pull qwen3.5:9b-q4_K_M`."
            ),
        )

        from grc_agent import cli, config as cfg_module, model_manager
        from grc_agent import startup as startup_module

        config = self._config()
        # Simulate the new default — the toml file no longer
        # overrides ``[llama].model``.
        object.__setattr__(config, "llama", config.llama.__class__(
            server_url=config.llama.server_url,
            model="",
            backend=config.llama.backend,
            max_tokens=config.llama.max_tokens,
            max_tool_rounds=config.llama.max_tool_rounds,
            temperature=config.llama.temperature,
            enable_thinking=config.llama.enable_thinking,
            request_timeout_seconds=config.llama.request_timeout_seconds,
        ))

        provider = ToolAgentsLlamaProviderConfig(
            base_url="http://127.0.0.1:11434",
            model="qwen3.6:27b-mtp-q4_K_M",
        )
        fake_result = RuntimeBootstrapResult()
        fake_result.provider_config = provider
        fake_result.launch_status = "probe_ok"
        fake_result.server_url = "http://127.0.0.1:11434"
        fake_result.model_alias = "qwen3.6:27b-mtp-q4_K_M"
        fake_result.retrieval_ok = True
        fake_result.catalog_root = "/tmp"

        bootstrap_mock = mock.MagicMock(return_value=fake_result)

        with mock.patch.object(cfg_module, "run_cli_setup", return_value=True):
            with mock.patch.object(model_manager, "probe_ollama_backend", return_value=fake_status):
                with mock.patch.object(startup_module, "bootstrap_runtime", bootstrap_mock):
                    with mock.patch.object(__import__("sys").stdin, "isatty", return_value=False):
                        with mock.patch("builtins.input", side_effect=["hi", "/quit"]):
                            with mock.patch.object(cli, "_run_single_turn", return_value=0):
                                self._capture(
                                    cli._run_llama_runtime,
                                    file_path=None,
                                    user_message=None,
                                    config=config,
                                    server_url="http://127.0.0.1:11434",
                                    model=None,
                                    api_key=None,
                                )
        bootstrap_mock.assert_called_once()
        self.assertEqual(
            bootstrap_mock.call_args.kwargs["model_alias"],
            "qwen3.6:27b-mtp-q4_K_M",
            "CLI must auto-pick the first installed model when no "
            "alias is configured anywhere.",
        )

    def test_chat_path_skips_probe_for_openrouter(self) -> None:
        """The probe is Ollama-only. For OpenRouter the CLI must not
        make any HTTP calls to ``localhost:11434``."""
        from grc_agent.config import (
            DEFAULT_DOCS_ANSWER_CONFIG,
            DEFAULT_GUARDRAILS_CONFIG,
            DEFAULT_HISTORY_CONFIG,
            DEFAULT_RETRIEVAL_CONFIG,
            AgentConfig,
        )

        config = AppConfig(
            llama=LlamaConfig(
                server_url="https://openrouter.ai/api",
                backend="openrouter",
                model="deepseek/deepseek-v4-flash",
            ),
            agent=AgentConfig(
                history_compact_budget=1000,
                docs_answer=DEFAULT_DOCS_ANSWER_CONFIG,
                retrieval=DEFAULT_RETRIEVAL_CONFIG,
                history=DEFAULT_HISTORY_CONFIG,
                guardrails=DEFAULT_GUARDRAILS_CONFIG,
            ),
        )

        from grc_agent import cli, config as cfg_module, model_manager

        probe_mock = mock.MagicMock()

        with mock.patch.object(cfg_module, "run_cli_setup", return_value=True):
            with mock.patch.object(model_manager, "probe_ollama_backend", probe_mock):
                with mock.patch.object(__import__("sys").stdin, "isatty", return_value=False):
                    # The probe is gated on ``backend == "ollama"``;
                    # the function will then continue into the
                    # session-load + bootstrap path. We don't need to
                    # stub those here — the contract is that the
                    # probe is NOT called. If it were called, the
                    # patched return value (a MagicMock without the
                    # fields the rest of the code expects) would
                    # raise later, but the test only cares about the
                    # call count.
                    try:
                        self._capture(
                            cli._run_llama_runtime,
                            file_path=None,
                            user_message=None,
                            config=config,
                            server_url="https://openrouter.ai/api",
                            model="deepseek/deepseek-v4-flash",
                            api_key=None,
                        )
                    except Exception:
                        # The downstream path will explode with our
                        # mock; the assertion below is what matters.
                        pass
        # The probe must not be called for non-ollama backends.
        probe_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
