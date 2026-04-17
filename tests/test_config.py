"""Tests for runtime config resolution and packaged CLI defaults."""

from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest import mock

from grc_agent.cli import _build_parser
from grc_agent.config import (
    CONFIG_ENV_VAR,
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
        self.assertEqual(config.llama.server_url, "http://127.0.0.1:8080")
        self.assertEqual(config.llama.model, "unsloth/gemma-4-E2B-it-GGUF")
        self.assertEqual(config.llama.hf_model, "unsloth/gemma-4-E2B-it-GGUF:Q4_K_M")
        self.assertEqual(config.llama.startup_timeout_seconds, 300.0)
        self.assertEqual(config.llama.max_tokens, 12000)
        self.assertEqual(config.llama.temperature, 1.0)
        self.assertFalse(config.llama.enable_thinking)
        self.assertEqual(config.llama.request_timeout_seconds, 60.0)

    def test_cli_parser_defaults_come_from_repo_config(self) -> None:
        config = load_app_config()
        parser = _build_parser(config)

        args = parser.parse_args(
            ["chat", "fixture.grc", "--message", "Summarize the graph."]
        )

        self.assertEqual(args.llama_server_url, config.llama.server_url)
        self.assertEqual(args.model, config.llama.model)

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
                    'hf_model = "custom/model:Q4"\n'
                    "startup_timeout_seconds = 120.0\n"
                    "max_tokens = 2048\n"
                    "temperature = 0.2\n"
                    "enable_thinking = true\n"
                    "request_timeout_seconds = 30.0\n"
                ),
                encoding="utf-8",
            )

            config = load_app_config(config_path)

        self.assertEqual(config.llama.server_url, "http://127.0.0.1:9000")
        self.assertEqual(config.llama.model, "custom-model")
        self.assertTrue(config.llama.enable_thinking)

    def test_resolve_config_path_prefers_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_config = Path(tmpdir) / "env.toml"
            env_config.write_text(
                "[llama]\nserver_url='http://127.0.0.1:8080'\nmodel='m'\nhf_model='m:q'\nstartup_timeout_seconds=1.0\nmax_tokens=1\ntemperature=0.0\nenable_thinking=false\nrequest_timeout_seconds=1.0\n",
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

    def test_pyproject_declares_console_script_entrypoint(self) -> None:
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

        self.assertEqual(
            payload["project"]["scripts"]["grc-agent"],
            "grc_agent.cli:main",
        )
